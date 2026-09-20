"""The pipeline orchestrator, built to be interrupted.

A full run does more work than a serverless invocation is allowed to finish, so
it is not written as one function that either completes or fails. It is a list
of stages, each of which processes a bounded slice, writes its position to the
run's cursor, and checks the clock. When the budget runs out the run is left
`paused` with everything done so far already committed, and the next
invocation, from the cron tick or the browser, picks up exactly where it
stopped.

That design is what makes the same code run unchanged on a 300 second Vercel
function and on a machine with no limit at all. On the latter the deadline
simply never fires and the run completes in one pass.

Every stage is also safe to re-enter. A stage that is interrupted and restarted
must not double count or duplicate rows, which is why each one derives its
remaining work from the database rather than from anything held in memory.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import settings
from ..db import repo
from ..embeddings import build_text, to_blob
from ..embeddings import registry as embeddings
from ..llm import LLMError
from ..llm import registry as llm
from ..ops import meter
from ..runtime import Deadline
from ..util import hours_ago_iso, now_iso, truncate
from . import classify, cluster, digest, enrich, summarize
from .dedup import find_duplicates

# The order stages run in. Enrichment sits after the digest deliberately: it is
# the only optional stage, so a run that runs out of budget loses the metadata
# rather than the thing the user actually asked for.
STAGES: tuple[str, ...] = (
    "ingest",
    "extract",
    "embed",
    "dedup",
    "cluster",
    "classify",
    "summarise",
    "digest",
    "enrich",
)


class Context:
    """Everything a stage needs, assembled once per invocation."""

    def __init__(
        self,
        *,
        workspace: dict[str, Any],
        run: dict[str, Any],
        deadline: Deadline,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.workspace_id = int(workspace["id"])
        self.run = run
        self.run_id = int(run["id"])
        self.deadline = deadline
        self.provider = provider
        self.model = model
        self.stats: dict[str, Any] = dict(run.get("stats") or {})
        self.cursor: dict[str, Any] = dict(run.get("cursor") or {})
        self.log: list[Any] = list(run.get("log") or [])

        config = workspace.get("config") or {}
        self.lookback_hours = int(
            config.get("lookback_hours") or settings.LOOKBACK_HOURS
        )
        self.since = hours_ago_iso(self.lookback_hours)
        self.dedup_since = hours_ago_iso(
            max(self.lookback_hours, settings.DEDUP_WINDOW_HOURS)
        )
        self._embedder: Any = None

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = embeddings.resolve()
        return self._embedder

    def bump(self, key: str, amount: int = 1) -> None:
        self.stats[key] = int(self.stats.get(key, 0)) + amount

    def note(self, message: str) -> None:
        # The log is what the UI shows while a run is in flight, so it is kept
        # short and human readable rather than exhaustive.
        self.log.append({"at": now_iso(), "message": truncate(message, 300)})
        self.log = self.log[-60:]


StageResult = str  # "done" when the stage finished, "paused" when out of time


def _stage_ingest(ctx: Context) -> StageResult:
    from ..ingest import SourceError, adapter_for

    sources = repo.list_sources(ctx.workspace_id, enabled_only=True)
    start_index = int(ctx.cursor.get("source_index") or 0)
    if start_index == 0:
        ctx.stats.setdefault("sources_total", len(sources))

    for index in range(start_index, len(sources)):
        if ctx.deadline.expired():
            ctx.cursor["source_index"] = index
            return "paused"

        source = sources[index]
        adapter_cls = adapter_for(source["kind"])
        if adapter_cls is None:
            repo.record_source_result(
                source["id"], status="error", error=f"Unknown source kind '{source['kind']}'."
            )
            continue

        adapter = adapter_cls()
        usable, reason = adapter.is_available()
        if not usable:
            repo.record_source_result(source["id"], status="skipped", error=reason)
            ctx.bump("sources_skipped")
            continue

        try:
            outcome = adapter.fetch(
                source["url"],
                config=source.get("config") or {},
                etag=source.get("etag") or "",
                last_modified=source.get("last_modified") or "",
                limit=settings.MAX_ARTICLES_PER_SOURCE,
                since=ctx.since,
            )
        except SourceError as exc:
            repo.record_source_result(source["id"], status="error", error=exc.message)
            ctx.bump("sources_failed")
            ctx.note(f"{source['name']}: {exc.message}")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop a run
            repo.record_source_result(source["id"], status="error", error=str(exc))
            ctx.bump("sources_failed")
            continue

        if outcome.status == "skipped":
            repo.record_source_result(source["id"], status="skipped", error=outcome.error)
            ctx.bump("sources_skipped")
            continue

        new_count = 0
        for item in outcome.items:
            _, created = repo.upsert_article(
                ctx.workspace_id,
                source_id=source["id"],
                url=item.url,
                title=item.title,
                summary=item.summary,
                content=item.content,
                author=item.author,
                published_at=item.published_at,
                image_url=item.image_url,
                language=item.language,
                extraction="feed",
                meta=item.meta,
            )
            if created:
                new_count += 1

        repo.record_source_result(
            source["id"],
            status=outcome.status,
            etag=outcome.etag,
            last_modified=outcome.last_modified,
            items=len(outcome.items),
        )
        ctx.bump("sources_ok")
        ctx.bump("articles_seen", len(outcome.items))
        ctx.bump("articles_new", new_count)

    ctx.cursor.pop("source_index", None)
    return "done"


def _stage_extract(ctx: Context) -> StageResult:
    """Fetch the full text of articles the feed only summarised.

    Body text materially improves both deduplication and summary quality, but
    it is one HTTP request per article and therefore the easiest stage to run
    out of budget in. It is bounded per invocation and resumes by article id.
    """
    if not settings.EXTRACT_FULLTEXT:
        return "done"

    from ..ingest import HttpClient, SourceError, extract_article

    last_id = int(ctx.cursor.get("extract_after_id") or 0)
    articles = [
        a
        for a in repo.articles_in_window(ctx.workspace_id, ctx.since)
        if a["extraction"] == "feed" and int(a["id"]) > last_id
    ]
    if not articles:
        ctx.cursor.pop("extract_after_id", None)
        return "done"

    client = HttpClient()
    for article in articles:
        # Roughly the cost of one polite fetch plus parsing. Stopping while
        # there is still room for a write is what keeps progress durable.
        if not ctx.deadline.allows(6.0):
            ctx.cursor["extract_after_id"] = last_id
            return "paused"

        last_id = int(article["id"])
        try:
            response = client.get(article["url"])
            if not response.ok:
                repo.set_article_fields(article["id"], extraction="failed")
                continue
            parsed = extract_article(response.text, response.url)
        except SourceError:
            repo.set_article_fields(article["id"], extraction="blocked")
            continue
        except Exception:  # noqa: BLE001 - extraction is best effort
            repo.set_article_fields(article["id"], extraction="failed")
            continue

        content = parsed.get("content") or ""
        if not content:
            repo.set_article_fields(article["id"], extraction="failed")
            continue

        repo.upsert_article(
            ctx.workspace_id,
            source_id=article["source_id"],
            url=article["url"],
            title=article["title"] or parsed.get("title") or "",
            summary=article["summary"],
            content=content,
            author=article["author"] or parsed.get("author") or "",
            published_at=article["published_at"] or parsed.get("published_at") or "",
            image_url=article["image_url"] or parsed.get("image_url") or "",
            language=article["language"] or parsed.get("language") or "",
            canonical_url=parsed.get("canonical_url") or "",
            extraction="fulltext",
        )
        ctx.bump("articles_extracted")

    ctx.cursor.pop("extract_after_id", None)
    return "done"


def _stage_embed(ctx: Context) -> StageResult:
    embedder = ctx.embedder
    provider_id, model_id = embeddings.signature(embedder)

    while True:
        pending = repo.articles_missing_embeddings(
            ctx.workspace_id, ctx.dedup_since, provider_id, model_id, limit=embedder.max_batch
        )
        if not pending:
            break
        if not ctx.deadline.allows(20.0):
            return "paused"

        texts_by_id = repo.article_texts(pending)
        payloads = [
            build_text(
                texts_by_id.get(article_id, {}).get("title", ""),
                texts_by_id.get(article_id, {}).get("summary", ""),
                texts_by_id.get(article_id, {}).get("content", ""),
            )
            for article_id in pending
        ]
        result = embedder.embed(payloads, operation="embed_articles")
        repo.save_embeddings(
            [
                (article_id, provider_id, model_id, result.dim, to_blob(vector.tolist()))
                # strict: a backend returning the wrong number of vectors must
                # raise here rather than silently store a vector against the
                # wrong article, which would corrupt every later comparison.
                for article_id, vector in zip(pending, result.vectors, strict=True)
            ]
        )
        ctx.bump("articles_embedded", len(pending))

    return "done"


def _stage_dedup(ctx: Context) -> StageResult:
    import numpy as np

    embedder = ctx.embedder
    provider_id, model_id = embeddings.signature(embedder)
    duplicate_threshold, _ = embeddings.thresholds(embedder)

    ids, matrix = repo.load_embedding_matrix(
        ctx.workspace_id, ctx.dedup_since, provider_id, model_id
    )
    if not ids:
        return "done"

    position = {article_id: index for index, article_id in enumerate(ids)}
    window = repo.articles_in_window(ctx.workspace_id, ctx.dedup_since, include_duplicates=True)
    by_id = {int(a["id"]): a for a in window}

    # Anything already judged in a previous pass is the pool; only articles
    # first seen in this run are candidates. That ordering is what makes the
    # stage idempotent when it is re-entered.
    already = {int(a["id"]) for a in window if a["duplicate_of"] is not None}
    judged = set(ctx.cursor.get("dedup_judged") or [])
    candidates = [
        by_id[a]
        for a in ids
        if a in by_id and a not in already and a not in judged
    ]
    pool = [by_id[a] for a in ids if a in by_id and a not in already and a in judged]

    if not candidates:
        ctx.cursor.pop("dedup_judged", None)
        return "done"

    # Fingerprints need simhash and content_hash, which `articles_in_window`
    # does not select, so they are filled from the same rows here.
    raw = repo.get_db().query(
        "SELECT id, simhash, content_hash FROM articles WHERE workspace_id = ?",
        (ctx.workspace_id,),
    )
    prints = {int(r["id"]): (int(r["simhash"] or 0), r["content_hash"] or "") for r in raw}
    for article in list(candidates) + list(pool):
        fingerprint = prints.get(int(article["id"]), (0, ""))
        article["simhash"] = fingerprint[0]
        article["content_hash"] = fingerprint[1]

    candidate_vectors = np.asarray(
        [matrix[position[int(a["id"])]] for a in candidates], dtype=np.float32
    )
    pool_vectors = (
        np.asarray([matrix[position[int(a["id"])]] for a in pool], dtype=np.float32)
        if pool
        else np.zeros((0, matrix.shape[1]), dtype=np.float32)
    )

    decisions = find_duplicates(
        candidates,
        pool,
        candidate_vectors,
        pool_vectors,
        duplicate_threshold=duplicate_threshold,
        simhash_distance=settings.SIMHASH_DISTANCE,
    )

    for article_id, decision in decisions.items():
        repo.set_article_fields(
            article_id,
            duplicate_of=decision.matched_id,
            meta={"duplicate_reason": decision.reason, "duplicate_score": decision.score},
        )
        ctx.bump("duplicates_removed")

    ctx.stats["articles_kept"] = len(candidates) + len(pool) - len(decisions)
    ctx.cursor.pop("dedup_judged", None)
    return "done"


def _stage_cluster(ctx: Context) -> StageResult:
    import numpy as np

    embedder = ctx.embedder
    provider_id, model_id = embeddings.signature(embedder)
    _, cluster_threshold = embeddings.thresholds(embedder)

    ids, matrix = repo.load_embedding_matrix(
        ctx.workspace_id, ctx.since, provider_id, model_id
    )
    if not ids:
        return "done"

    survivors = repo.articles_in_window(ctx.workspace_id, ctx.since)
    keep = {int(a["id"]) for a in survivors}
    rows = [(a, i) for i, a in enumerate(ids) if a in keep]
    if not rows:
        return "done"

    by_id = {int(a["id"]): a for a in survivors}
    ordered = [by_id[article_id] for article_id, _ in rows]
    vectors = np.asarray([matrix[index] for _, index in rows], dtype=np.float32)

    proposals = cluster.build_clusters(
        ordered, vectors, cluster_threshold=cluster_threshold
    )
    if not proposals:
        return "done"

    index_of = {int(a["id"]): i for i, a in enumerate(ordered)}
    centroids = [
        cluster.centroid_of(
            np.asarray([vectors[index_of[a]] for a in proposal.article_ids], dtype=np.float32)
        )
        for proposal in proposals
    ]

    from .dedup import build_fingerprint

    fingerprints = {int(a["id"]): build_fingerprint(a) for a in ordered}
    existing = repo.open_events_with_centroids(ctx.workspace_id, ctx.dedup_since)
    existing_prints: dict[int, Any] = {}
    for event_id, _ in existing:
        members = repo.event_articles(event_id)
        if members:
            existing_prints[event_id] = build_fingerprint(members[0])

    cluster.attach_to_existing(
        proposals,
        centroids,
        existing,
        fingerprints,
        existing_prints,
        cluster_threshold=cluster_threshold,
    )

    for proposal, centre in zip(proposals, centroids, strict=True):
        members = [by_id[a] for a in proposal.article_ids if a in by_id]
        if not members:
            continue
        newest = max((m.get("published_at") or m.get("fetched_at") or "") for m in members)
        oldest = min((m.get("published_at") or m.get("fetched_at") or "") for m in members)
        domains = {m.get("domain") or "" for m in members}

        if proposal.existing_event_id:
            event_id = proposal.existing_event_id
            existing_members = repo.event_articles(event_id)
            domains |= {m.get("domain") or "" for m in existing_members}
            total = len({int(m["id"]) for m in existing_members} | set(proposal.article_ids))
        else:
            event_id = repo.create_event(
                ctx.workspace_id,
                title=proposal.title or members[0].get("title") or "Untitled",
                first_seen=oldest,
                last_seen=newest,
            )
            total = len(members)
            ctx.bump("events_created")

        repo.add_event_articles(
            event_id,
            [
                (article_id, proposal.similarities.get(article_id, 0.0),
                 article_id == proposal.primary_id)
                for article_id in proposal.article_ids
            ],
        )
        repo.update_event(
            event_id,
            centroid=to_blob(centre.tolist()),
            last_seen=newest,
            importance=cluster.importance_of(
                size=total,
                distinct_domains=len([d for d in domains if d]),
                newest_published=newest,
            ),
        )

    events, _ = repo.events_in_window(ctx.workspace_id, ctx.since, limit=1000)
    ctx.stats["events"] = len(events)
    return "done"


def _stage_classify(ctx: Context) -> StageResult:
    events, _ = repo.events_in_window(ctx.workspace_id, ctx.since, limit=500)
    pending = [e for e in events if not e.get("theme")]
    if not pending:
        return "done"
    if not ctx.deadline.allows(25.0):
        return "paused"

    themes = ctx.workspace.get("themes") or []
    assignments = classify.classify(
        pending, themes, provider=ctx.provider, model=ctx.model
    )
    for event_id, theme in assignments.items():
        repo.update_event(event_id, theme=theme)
    ctx.bump("events_classified", len(assignments))
    return "done"


def _stage_summarise(ctx: Context) -> StageResult:
    events, _ = repo.events_in_window(ctx.workspace_id, ctx.since, limit=200)
    pending = [e for e in events if not e.get("summary")]
    pending.sort(key=lambda e: -float(e.get("importance") or 0))
    if not pending:
        return "done"

    # A provider that has run out of quota will refuse every remaining event,
    # so the stage stops and waits rather than burning through the backlog with
    # fallbacks. After a few attempts it gives up waiting and takes the
    # fallbacks, because a digest that never arrives is worse than one written
    # from publisher summaries.
    stalls = int(ctx.cursor.get("summarise_stalls") or 0)
    accept_fallback = stalls >= 4

    for event in pending:
        # One summary is a single call over a few thousand characters.
        if not ctx.deadline.allows(30.0):
            return "paused"
        articles = repo.event_articles(event["id"])
        if not articles:
            continue
        try:
            written = summarize.summarise_event(
                articles, provider=ctx.provider, model=ctx.model,
                temperature=settings.SUMMARY_TEMPERATURE,
            )
        except LLMError as exc:
            if accept_fallback:
                written = {
                    "title": event.get("title") or "",
                    "summary": truncate(articles[0].get("summary") or "", 320),
                    "source": "fallback",
                }
            else:
                ctx.cursor["summarise_stalls"] = stalls + 1
                ctx.note(
                    f"Paused summarising: {exc.message[:120]} "
                    f"({ctx.stats.get('events_summarised', 0)} of {len(events)} done)"
                )
                return "paused"
        if written.get("source") == "fallback":
            ctx.bump("summaries_fallback")
        else:
            ctx.bump("summaries_written")
        spread = enrich.source_spread(articles)
        repo.update_event(
            event["id"],
            title=written.get("title") or event.get("title") or "",
            summary=written.get("summary") or "",
            meta={
                "why_it_matters": written.get("why_it_matters") or "",
                "disagreement": written.get("disagreement") or "",
                "spread": spread,
                "summary_source": written.get("source") or "",
            },
        )
        ctx.bump("events_summarised")

    ctx.cursor.pop("summarise_stalls", None)
    if ctx.stats.get("summaries_fallback"):
        ctx.note(
            f"{ctx.stats['summaries_fallback']} summaries came from the source text "
            "rather than a model, because the provider refused the calls."
        )
    return "done"


def _stage_digest(ctx: Context) -> StageResult:
    events, _ = repo.events_in_window(ctx.workspace_id, ctx.since, limit=200)
    if not events:
        ctx.note("No events in the window, so no digest was written.")
        return "done"

    for event in events:
        event["articles"] = repo.event_articles(event["id"])

    themes = ctx.workspace.get("themes") or []
    grouped = digest.group_by_theme(events, themes)

    config = ctx.workspace.get("config") or {}
    count = int(config.get("digest_bullets") or settings.DIGEST_BULLETS)
    try:
        bullets = summarize.write_bullets(
            events, count=count, provider=ctx.provider, model=ctx.model
        )
    except LLMError:
        bullets = []

    period_start, period_end = digest.default_period(ctx.lookback_hours)
    payload = digest.build_payload(
        ctx.workspace, grouped, bullets,
        period_start=period_start, period_end=period_end, stats=ctx.stats,
    )
    markdown = digest.render_markdown(payload)
    html = digest.render_html(payload)

    provider_id, model_id = "", ""
    try:
        selection = llm.resolve(ctx.provider, ctx.model)
        provider_id, model_id = selection.provider.id, selection.model
    except LLMError:
        pass

    digest_id = repo.create_digest(
        ctx.workspace_id,
        slug=digest.digest_slug(ctx.workspace["slug"], period_end),
        title=f"{ctx.workspace['name']}: {period_end[:10]}",
        period_start=period_start,
        period_end=period_end,
        markdown=markdown,
        html=html,
        payload=payload,
        provider=provider_id,
        model=model_id,
    )
    ctx.stats["digest_id"] = digest_id
    ctx.note(f"Digest written with {len(bullets)} bullets across {len(grouped)} sections.")
    return "done"


def _stage_enrich(ctx: Context) -> StageResult:
    articles = repo.articles_in_window(ctx.workspace_id, ctx.since)
    last_id = int(ctx.cursor.get("enrich_after_id") or 0)
    pending = [a for a in articles if int(a["id"]) > last_id and not a.get("stance")]
    if not pending:
        ctx.cursor.pop("enrich_after_id", None)
        return "done"

    for start in range(0, len(pending), enrich.BATCH_SIZE):
        if not ctx.deadline.allows(25.0):
            ctx.cursor["enrich_after_id"] = last_id
            return "paused"
        batch = pending[start : start + enrich.BATCH_SIZE]
        results = enrich.enrich(batch, provider=ctx.provider, model=ctx.model)
        for article_id, data in results.items():
            repo.set_article_fields(
                article_id,
                sentiment=data.get("sentiment"),
                stance=data.get("stance") or "factual",
            )
            repo.record_entities(ctx.workspace_id, article_id, data.get("entities") or [])
            ctx.bump("articles_enriched")
        last_id = max(int(a["id"]) for a in batch)

    ctx.cursor.pop("enrich_after_id", None)
    return "done"


_HANDLERS: dict[str, Callable[[Context], StageResult]] = {
    "ingest": _stage_ingest,
    "extract": _stage_extract,
    "embed": _stage_embed,
    "dedup": _stage_dedup,
    "cluster": _stage_cluster,
    "classify": _stage_classify,
    "summarise": _stage_summarise,
    "digest": _stage_digest,
    "enrich": _stage_enrich,
}


def start(
    workspace_slug: str, *, trigger: str = "manual"
) -> dict[str, Any]:
    workspace = repo.get_workspace(workspace_slug)
    if workspace is None:
        raise ValueError(f"No workspace '{workspace_slug}'.")
    return repo.create_run(int(workspace["id"]), trigger=trigger)


def advance(
    run_id: int,
    *,
    provider: str | None = None,
    model: str | None = None,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Run as many stages as the time budget allows, then save and return.

    The returned run's status is `paused` when there is more to do, which is
    the signal for the caller, a browser or the cron tick, to call again.
    """
    run = repo.get_run(run_id)
    if run is None:
        raise ValueError(f"No run {run_id}.")
    if run["status"] in ("done", "failed"):
        return run

    workspace = repo.get_workspace_by_id(int(run["workspace_id"]))
    if workspace is None:
        return repo.save_run(
            run_id, status="failed", error="The workspace was deleted.", finished=True
        ) or run

    deadline = Deadline(seconds)
    collector = meter.start()
    ctx = Context(
        workspace=workspace, run=run, deadline=deadline, provider=provider, model=model
    )

    stage = run["stage"] or STAGES[0]
    if stage not in _HANDLERS:
        stage = STAGES[0]

    try:
        while True:
            result = _HANDLERS[stage](ctx)

            if result == "paused":
                repo.record_usage(run_id, ctx.workspace_id, collector.drain())
                return repo.save_run(
                    run_id, status="paused", stage=stage,
                    cursor=ctx.cursor, stats=ctx.stats, log=ctx.log,
                ) or run

            position = STAGES.index(stage)
            if position + 1 >= len(STAGES):
                repo.record_usage(run_id, ctx.workspace_id, collector.drain())
                return repo.save_run(
                    run_id, status="done", stage="complete",
                    cursor={}, stats=ctx.stats, log=ctx.log, finished=True,
                ) or run

            stage = STAGES[position + 1]
            repo.save_run(
                run_id, status="running", stage=stage,
                cursor=ctx.cursor, stats=ctx.stats, log=ctx.log,
            )

            if deadline.expired():
                repo.record_usage(run_id, ctx.workspace_id, collector.drain())
                return repo.save_run(run_id, status="paused", stage=stage) or run

    except Exception as exc:  # noqa: BLE001 - recorded on the run, not raised
        repo.record_usage(run_id, ctx.workspace_id, collector.drain())
        ctx.note(f"Failed during {stage}: {exc}")
        return repo.save_run(
            run_id, status="failed", stage=stage, error=truncate(str(exc), 500),
            stats=ctx.stats, log=ctx.log, finished=True,
        ) or run
    finally:
        meter.stop()


def run_to_completion(
    workspace_slug: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    trigger: str = "manual",
    max_passes: int = 40,
) -> dict[str, Any]:
    """Drive a run all the way through, for a server tier or a CLI.

    Each pass gets its own deadline, so this behaves identically to a sequence
    of cron ticks and exercises the same resume path rather than a second one.
    """
    run = start(workspace_slug, trigger=trigger)
    for _ in range(max_passes):
        run = advance(int(run["id"]), provider=provider, model=model)
        if run["status"] in ("done", "failed"):
            break
    return run


def tick(limit: int = 3, *, seconds: float | None = None) -> list[dict[str, Any]]:
    """Continue whatever runs are waiting. This is what the cron route calls."""
    out: list[dict[str, Any]] = []
    for run in repo.resumable_runs(limit=limit):
        out.append(advance(int(run["id"]), seconds=seconds))
    return out
