"""One shot research on a topic, without creating a standing beat.

The rest of this project is built around a workspace: a set of sources you
configure once and read every morning. That is the right shape for a beat you
follow, and the wrong shape for "what happened with X today", which is a
question you ask once and do not want to maintain sources for.

So this module takes a query, fans out across every usable search backend,
deduplicates the results with the same machinery the pipeline uses, and returns
either the ranked article list or a cited brief written from it.

Two properties are deliberate:

* It works with no API key at all. Google News RSS needs none and raw mode
  needs no model, so the feature is not gated behind a paid account. Keys for
  Brave, Tavily, Exa, SerpAPI, NewsAPI and the rest simply widen the net.
* Ranking is by relevance to the query and recency, not by the order a search
  engine returned. Several backends are merged here and their orderings are not
  comparable with one another.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import settings
from .embeddings import build_text
from .embeddings import registry as embeddings
from .ingest import HttpClient, SourceError, adapter_for
from .ingest.base import RawItem, dedupe_items
from .llm import LLMError, keyring
from .llm import registry as llm
from .runtime import Deadline
from .util import host_of, parse_datetime, truncate, utcnow

REPORT_SYSTEM = (
    "You write a research brief from a set of news articles supplied to you.\n"
    "\n"
    "Absolute rules:\n"
    "1. Use only the supplied articles. Never add a fact, figure, name, date or "
    "quote that is not in them.\n"
    "2. Cite every factual claim with its article number in square brackets, "
    "like [3]. A sentence containing a fact and no citation is not acceptable.\n"
    "3. When sources disagree, say so and cite both.\n"
    "4. When the articles do not settle something important, say plainly that "
    "they do not. Do not fill the gap.\n"
    "5. No opinion, no speculation, no invented author persona, no sign off.\n"
    "\n"
    "Structure the brief as markdown:\n"
    "## What happened\n"
    "A short paragraph of the core facts.\n"
    "## Detail\n"
    "Three to six bullets, each cited.\n"
    "## What is unclear\n"
    "What the sources do not settle. Write 'Nothing significant' if they agree."
)

# How many articles reach the model. Enough for a real brief, few enough that a
# small model keeps the numbering straight.
REPORT_CONTEXT = 10
REPORT_CHARS = 900

# Every source kind that can answer a plain text query. Google News leads
# because it needs no key and aggregates thousands of publishers by itself.
QUERY_BACKENDS: tuple[str, ...] = (
    "googlenews",
    "search",
    "newsapi",
    "gnews",
    "newsdata",
    "currents",
    "guardian",
    "nyt",
    "mediastack",
)


@dataclass
class ResearchResult:
    query: str
    mode: str
    sort: str = "relevance"
    articles: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    backends_used: list[str] = field(default_factory=list)
    backends_failed: list[dict[str, str]] = field(default_factory=list)
    total_found: int = 0
    duplicates_removed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "mode": self.mode,
            "sort": self.sort,
            "articles": self.articles,
            "report": self.report,
            "citations": self.citations,
            "backends_used": self.backends_used,
            "backends_failed": self.backends_failed,
            "total_found": self.total_found,
            "duplicates_removed": self.duplicates_removed,
        }


def _has_credentials(key_names: tuple[str, ...]) -> bool:
    """Whether any of an adapter's credentials is actually set.

    An adapter's own is_available reports whether the code can run here, not
    whether it has a key, because the key is checked at fetch time where the
    error message can be specific. For choosing which backends to fan out
    across, the credential is what decides whether the call is worth making.
    """
    if not key_names:
        return True
    for name in key_names:
        if getattr(settings, name, None):
            return True
        # A visitor's own key arrives per request rather than in the settings.
        if keyring.key_for(name.lower().replace("_api_key", "").replace("_key", "")):
            return True
    return False


def available_backends() -> list[dict[str, Any]]:
    """Which fan out sources this deployment can use for a topic search."""
    out: list[dict[str, Any]] = []
    for kind in QUERY_BACKENDS:
        adapter_cls = adapter_for(kind)
        if adapter_cls is None:
            continue
        adapter = adapter_cls()
        usable, reason = adapter.is_available()
        keyed = _has_credentials(tuple(adapter_cls.key_names))
        if usable and not keyed:
            usable = False
            reason = (
                f"Needs {' or '.join(adapter_cls.key_names)}. Google News works "
                "without any key and is always searched."
            )
        out.append(
            {
                "kind": kind,
                "label": adapter_cls.label or kind,
                "available": usable,
                "reason": reason,
                "needs_key": bool(adapter_cls.key_names),
            }
        )
    return out


def _gather(
    query: str, backends: list[str], per_backend: int, deadline: Deadline
) -> tuple[list[RawItem], list[str], list[dict[str, str]]]:
    items: list[RawItem] = []
    used: list[str] = []
    failed: list[dict[str, str]] = []

    # The search adapter deliberately queries ONE provider per source, which is
    # right for a scheduled workspace source: fanning out on every run would
    # burn four quotas for one feed. Research is the opposite case, so each
    # provider holding a key is asked separately here.
    expanded: list[tuple[str, dict[str, Any]]] = []
    for kind in backends:
        if kind != "search":
            expanded.append((kind, {}))
            continue
        adapter_cls = adapter_for("search")
        if adapter_cls is None:
            continue
        for name in adapter_cls().available_backends():
            expanded.append((kind, {"backend": name}))

    for kind, extra in expanded:
        if deadline.expired():
            break
        adapter_cls = adapter_for(kind)
        if adapter_cls is None:
            failed.append({"backend": kind, "error": "No such source kind."})
            continue
        adapter = adapter_cls()
        usable, reason = adapter.is_available()
        if not usable:
            failed.append({"backend": kind, "error": reason})
            continue
        label = f"{kind}:{extra['backend']}" if extra.get("backend") else kind
        try:
            outcome = adapter.fetch(
                "", config={"query": query, **extra}, limit=per_backend
            )
        except SourceError as exc:
            failed.append({"backend": label, "error": exc.message})
            continue
        except Exception as exc:  # noqa: BLE001 - one backend must not stop the rest
            failed.append({"backend": label, "error": str(exc)[:200]})
            continue

        if outcome.status == "skipped":
            failed.append({"backend": label, "error": outcome.error or "Skipped."})
            continue
        if outcome.items:
            used.append(label)
            items.extend(outcome.items)
        else:
            failed.append({"backend": label, "error": "Returned nothing for this query."})

    return items, used, failed


def _extract_bodies(items: list[RawItem], deadline: Deadline, budget: int) -> None:
    """Fill in article text for the leading results, in place.

    Full text materially improves both the deduplication and the brief, but it
    is one request per article and the tail of a search rarely earns that cost.
    """
    from .ingest import extract_article

    client = HttpClient()
    for item in items[:budget]:
        if item.content or not deadline.allows(6.0):
            continue
        try:
            response = client.get(item.url)
            if not response.ok:
                continue
            parsed = extract_article(response.text, response.url)
        except Exception:  # noqa: BLE001 - extraction is best effort
            continue
        if parsed.get("content"):
            item.content = parsed["content"]
            if not item.published_at and parsed.get("published_at"):
                item.published_at = parsed["published_at"]


def _rank(items: list[RawItem], query: str) -> tuple[list[dict[str, Any]], int]:
    """Order by relevance to the query and recency, dropping near duplicates."""
    import numpy as np

    embedder = embeddings.resolve()
    _, duplicate_cut = embeddings.thresholds(embedder)

    texts = [build_text(i.title, i.summary, i.content) for i in items]
    vectors = embedder.embed([query, *texts], operation="research").vectors
    query_vector, doc_vectors = vectors[0], vectors[1:]

    relevance = doc_vectors @ query_vector
    now = utcnow()

    scored: list[tuple[float, int]] = []
    for index, item in enumerate(items):
        published = parse_datetime(item.published_at) if item.published_at else None
        hours = (now - published).total_seconds() / 3600.0 if published else 48.0
        # Recency is a nudge, not the main signal. A three day old piece that is
        # squarely on topic should outrank an hour old piece that merely
        # mentions the subject in passing.
        freshness = max(0.0, 1.0 - min(max(hours, 0.0), 336.0) / 336.0)
        scored.append((float(relevance[index]) + 0.15 * freshness, index))
    scored.sort(reverse=True)

    kept: list[dict[str, Any]] = []
    kept_vectors: list[Any] = []
    removed = 0
    for score, index in scored:
        vector = doc_vectors[index]
        if kept_vectors and float(np.max(np.asarray(kept_vectors) @ vector)) >= duplicate_cut:
            removed += 1
            continue
        item = items[index]
        kept_vectors.append(vector)
        kept.append(
            {
                "n": len(kept) + 1,
                "title": item.title,
                "url": item.url,
                # Google News hands back a redirector URL, so every result
                # would otherwise read as news.google.com. The adapter records
                # the real outlet, and that is what a reader needs to see.
                "domain": (item.meta or {}).get("publisher") or item.domain or host_of(item.url),
                "summary": truncate(item.summary or item.content or "", 400),
                "published_at": item.published_at,
                "relevance": round(float(relevance[index]), 4),
                "score": round(score, 4),
                "meta": item.meta,
            }
        )
    return kept, removed


def _write_report(
    query: str,
    articles: list[dict[str, Any]],
    *,
    provider: str | None,
    model: str | None,
) -> str:
    context: list[str] = []
    for article in articles[:REPORT_CONTEXT]:
        context.append(f"[{article['n']}] {article['title']}")
        context.append(f"    Source: {article['domain']}")
        if article.get("published_at"):
            context.append(f"    Published: {article['published_at'][:10]}")
        context.append(f"    {truncate(article.get('summary') or '', REPORT_CHARS)}")
        context.append("")

    return llm.generate(
        f"Topic: {query}\n\nArticles:\n\n" + "\n".join(context),
        provider=provider,
        model=model,
        system=REPORT_SYSTEM,
        temperature=0.15,
        max_tokens=1800,
        operation="research_report",
    )


def research(
    query: str,
    *,
    mode: str = "raw",
    backends: list[str] | None = None,
    limit: int = 25,
    per_backend: int = 20,
    extract: bool = True,
    sort: str = "relevance",
    provider: str | None = None,
    model: str | None = None,
    seconds: float | None = None,
) -> ResearchResult:
    """Search a topic across every usable backend and rank what comes back."""
    query = (query or "").strip()
    if not query:
        raise ValueError("A research query cannot be empty.")

    deadline = Deadline(seconds)
    # With nothing specified, everything usable is tried. Each adapter reports
    # its own unavailability, so listing them all costs nothing when most have
    # no key.
    chosen = backends or [
        entry["kind"] for entry in available_backends() if entry["available"]
    ] or ["googlenews"]

    items, used, failed = _gather(query, chosen, per_backend, deadline)
    items = dedupe_items(items)
    total = len(items)
    if not items:
        return ResearchResult(
            query=query, mode=mode, sort=sort,
            backends_used=used, backends_failed=failed,
        )

    if extract and settings.EXTRACT_FULLTEXT:
        _extract_bodies(items, deadline, budget=min(limit, 12))

    ranked, removed = _rank(items, query)
    ranked = ranked[:limit]

    # Relevance decides which results survive the cut, always. Date order is
    # applied afterwards, to the survivors, so asking for newest first never
    # turns the list into whatever happened to be published most recently
    # regardless of whether it is about the topic.
    if sort in ("newest", "oldest"):
        ranked.sort(
            key=lambda a: a.get("published_at") or "",
            reverse=(sort == "newest"),
        )

    for position, article in enumerate(ranked, start=1):
        article["n"] = position

    result = ResearchResult(
        query=query,
        mode=mode,
        sort=sort,
        articles=ranked,
        backends_used=used,
        backends_failed=failed,
        total_found=total,
        duplicates_removed=removed,
    )

    if mode == "report":
        try:
            result.report = _write_report(
                query, ranked, provider=provider, model=model
            )
            result.citations = ranked[:REPORT_CONTEXT]
        except LLMError as exc:
            # The ranked list already worked and is the useful part, so a
            # provider failure degrades the answer rather than losing it.
            result.backends_failed.append({"backend": "report", "error": exc.message})

    return result


def to_workspace(result: ResearchResult, *, name: str = "") -> dict[str, Any]:
    """Turn a one off search into a standing beat.

    A topic worth researching once is often worth following, and the outlets
    that actually answered it are the right ones to follow it with.
    """
    from .db import repo

    workspace = repo.create_workspace(
        name=name or f"Research: {truncate(result.query, 40)}",
        description=f"Created from a topic search for {result.query!r}.",
        config={"lookback_hours": 48},
    )
    repo.add_source(
        int(workspace["id"]),
        kind="googlenews",
        url="googlenews",
        name=f"Google News: {truncate(result.query, 40)}",
        config={"query": result.query},
    )
    # Each outlet that answered becomes a source in its own right, so the beat
    # keeps working even if a search backend stops being available.
    for domain in sorted({a["domain"] for a in result.articles if a.get("domain")})[:12]:
        repo.add_source(
            int(workspace["id"]),
            kind="html",
            url=f"https://{domain}",
            name=domain,
        )
    return workspace
