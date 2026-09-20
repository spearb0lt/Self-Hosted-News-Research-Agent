"""Every HTTP endpoint.

Two conventions run through the whole surface:

* Errors are returned as `{"error": {"message", "hint", "provider"}}`. The hint
  is written to be shown to a person and to tell them what to do next, which is
  why provider and source failures carry one all the way from where they
  happened.
* Nothing here does heavy work inline except by explicit request. A pipeline
  run is started, advanced and reported on; it is never awaited, because on a
  serverless deployment the request would be killed before it finished.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from .. import settings
from ..db import repo
from ..delivery import channels as delivery
from ..delivery import feed as feed_renderer
from ..embeddings import EmbeddingError
from ..embeddings import registry as embeddings
from ..ingest import SourceError, describe_adapters, discover_feeds
from ..llm import LLMError
from ..llm import registry as llm
from ..pipeline import runner
from ..presets import list_presets, load_preset
from ..rag import chat as rag
from ..runtime import current as runtime
from ..util import hours_ago_iso

router = APIRouter()


def fail(status: int, message: str, *, hint: str = "", provider: str = "") -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"message": message, "hint": hint, "provider": provider}},
    )


def _workspace_or_404(slug: str) -> dict[str, Any]:
    workspace = repo.get_workspace(slug)
    if workspace is None:
        raise fail(404, f"No workspace named '{slug}'.", hint="Check the URL, or create it.")
    return workspace


# ------------------------------------------------------------------ schemas


class WorkspaceCreate(BaseModel):
    preset: str = ""
    slug: str = ""
    name: str = ""
    description: str = ""
    timezone: str = ""
    language: str = "en"
    # Either a list of labels or a list of {id,label,hint}. The repository
    # normalises both to the stored form, so the UI may send whichever is
    # natural for the control the user was editing.
    themes: list[dict[str, Any] | str] | None = None
    keywords: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    timezone: str | None = None
    language: str | None = None
    enabled: bool | None = None
    # Either a list of labels or a list of {id,label,hint}. The repository
    # normalises both to the stored form, so the UI may send whichever is
    # natural for the control the user was editing.
    themes: list[dict[str, Any] | str] | None = None
    keywords: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


class SourceCreate(BaseModel):
    kind: str
    url: str = ""
    name: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    weight: float | None = None
    config: dict[str, Any] | None = None


class RunRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


class DiscoverRequest(BaseModel):
    url: str


class DeliverRequest(BaseModel):
    channels: list[str] = Field(default_factory=list)
    to: str = ""


class ChatRequest(BaseModel):
    question: str
    provider: str | None = None
    model: str | None = None


# ------------------------------------------------------------------- health


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "runtime": runtime().as_dict(),
        "providers_available": llm.any_available(),
    }


def _preset_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """A preset as the picker needs it.

    The theme labels are included rather than only a count, because the whole
    point of choosing a preset is seeing what it will actually sort your news
    into. The full theme objects stay out: their hints are written for the
    classifier prompt and are far too long to render in a card.
    """
    full = load_preset(summary["slug"]) or {}
    return {
        "slug": summary["slug"],
        "name": summary.get("name") or summary["slug"],
        "description": summary.get("description") or "",
        "themes": [
            theme.get("label") or theme.get("id") or ""
            for theme in (full.get("themes") or [])
        ],
        "sources": len(full.get("sources") or []),
    }


@router.get("/config")
def config() -> dict[str, Any]:
    return {
        "app": {"name": settings.APP_NAME, "tagline": settings.APP_TAGLINE},
        "runtime": runtime().as_dict(),
        "allow_client_keys": settings.ALLOW_CLIENT_KEYS,
        "providers": llm.all_status(),
        "embedders": embeddings.all_status(),
        "source_kinds": describe_adapters(),
        "presets": [_preset_summary(p) for p in list_presets()],
        "delivery_channels": delivery.available_channels(),
    }


@router.post("/providers/{provider_id}/verify")
def verify_provider(provider_id: str) -> dict[str, Any]:
    provider = llm.provider_map().get(provider_id.strip().lower())
    if provider is None:
        raise fail(404, f"Unknown provider '{provider_id}'.")
    try:
        models = provider.verify()
    except LLMError as exc:
        raise fail(400, exc.message, hint=exc.hint, provider=exc.provider) from exc
    return {"ok": True, "models": models}


# --------------------------------------------------------------- workspaces


@router.get("/workspaces")
def get_workspaces() -> list[dict[str, Any]]:
    return repo.list_workspaces()


@router.post("/workspaces", status_code=201)
def post_workspace(body: WorkspaceCreate) -> dict[str, Any]:
    if body.preset:
        preset = load_preset(body.preset)
        if preset is None:
            raise fail(404, f"No preset named '{body.preset}'.")
        workspace = repo.create_workspace(
            name=body.name or preset.get("name") or body.preset,
            slug=body.slug or preset.get("slug") or body.preset,
            description=preset.get("description") or "",
            timezone=body.timezone or preset.get("timezone") or settings.DEFAULT_TIMEZONE,
            language=preset.get("language") or "en",
            preset=body.preset,
            themes=preset.get("themes"),
            keywords=preset.get("keywords"),
            config=preset.get("config"),
        )
        for source in preset.get("sources") or []:
            repo.add_source(
                int(workspace["id"]),
                kind=source.get("kind") or "rss",
                url=source.get("url") or "",
                name=source.get("name") or "",
                config=source.get("config") or {},
                weight=float(source.get("weight") or 1.0),
            )
        return workspace

    if not body.name:
        raise fail(400, "A workspace needs a name, or a preset to copy.")
    return repo.create_workspace(
        name=body.name,
        slug=body.slug,
        description=body.description,
        timezone=body.timezone or settings.DEFAULT_TIMEZONE,
        language=body.language,
        themes=body.themes,
        keywords=body.keywords,
        config=body.config,
    )


@router.get("/workspaces/{slug}")
def get_workspace(slug: str) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    workspace_id = int(workspace["id"])
    sources = repo.list_sources(workspace_id)
    latest = repo.latest_run(workspace_id)

    workspace["sources"] = sources
    workspace["latest_run"] = latest
    # The list and detail views render the same header, so the detail response
    # carries the same counts the list one does. Without them the header reads
    # "undefined sources".
    workspace["source_count"] = len(sources)
    workspace["article_count"] = repo.count_articles(workspace_id)
    workspace["last_run_at"] = (latest or {}).get("started_at") or ""
    return workspace


@router.patch("/workspaces/{slug}")
def patch_workspace(slug: str, body: WorkspaceUpdate) -> dict[str, Any]:
    _workspace_or_404(slug)
    updated = repo.update_workspace(
        slug, **{k: v for k, v in body.model_dump().items() if v is not None}
    )
    if updated is None:
        raise fail(404, f"No workspace named '{slug}'.")
    return updated


@router.delete("/workspaces/{slug}")
def remove_workspace(slug: str) -> dict[str, Any]:
    if not repo.delete_workspace(slug):
        raise fail(404, f"No workspace named '{slug}'.")
    return {"deleted": True}


# ------------------------------------------------------------------ sources


@router.post("/workspaces/{slug}/sources", status_code=201)
def post_source(slug: str, body: SourceCreate) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    from ..ingest import adapter_for

    adapter_cls = adapter_for(body.kind)
    if adapter_cls is None:
        raise fail(400, f"Unknown source kind '{body.kind}'.")
    if not body.url and not adapter_cls.urlless:
        raise fail(400, f"A {body.kind} source needs a URL.", hint=adapter_cls.url_hint)

    source = repo.add_source(
        int(workspace["id"]),
        kind=body.kind,
        url=body.url or body.kind,
        name=body.name,
        config=body.config,
        weight=body.weight,
    )
    if source is None:
        raise fail(500, "The source could not be stored.")
    return source


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourceUpdate) -> dict[str, Any]:
    if repo.get_source(source_id) is None:
        raise fail(404, f"No source {source_id}.")
    updated = repo.update_source(
        source_id, **{k: v for k, v in body.model_dump().items() if v is not None}
    )
    if updated is None:
        raise fail(404, f"No source {source_id}.")
    return updated


@router.delete("/sources/{source_id}")
def remove_source(source_id: int) -> dict[str, Any]:
    if not repo.delete_source(source_id):
        raise fail(404, f"No source {source_id}.")
    return {"deleted": True}


@router.post("/workspaces/{slug}/discover")
def discover(slug: str, body: DiscoverRequest) -> dict[str, Any]:
    _workspace_or_404(slug)
    try:
        feeds = discover_feeds(body.url)
    except SourceError as exc:
        raise fail(400, exc.message, hint=exc.hint) from exc
    except Exception as exc:  # noqa: BLE001
        raise fail(400, f"Could not read {body.url}: {exc}") from exc
    return {"feeds": feeds}


# --------------------------------------------------------------------- runs


@router.post("/workspaces/{slug}/run")
def post_run(slug: str, body: RunRequest = Body(default=RunRequest())) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)

    # A run already in flight is continued rather than duplicated. Vercel Cron
    # explicitly does not guarantee against duplicate invocations, so this has
    # to be safe against two ticks arriving together.
    existing = repo.latest_run(int(workspace["id"]))
    if existing and existing["status"] in ("running", "paused"):
        return _with_progress(
            runner.advance(int(existing["id"]), provider=body.provider, model=body.model)
        )

    run = runner.start(slug, trigger="manual")
    return _with_progress(
        runner.advance(int(run["id"]), provider=body.provider, model=body.model)
    )


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    run = repo.get_run(run_id)
    if run is None:
        raise fail(404, f"No run {run_id}.")
    return _with_progress(run)


@router.post("/runs/{run_id}/continue")
def continue_run(run_id: int, body: RunRequest = Body(default=RunRequest())) -> dict[str, Any]:
    run = repo.get_run(run_id)
    if run is None:
        raise fail(404, f"No run {run_id}.")
    return _with_progress(
        runner.advance(run_id, provider=body.provider, model=body.model)
    )


def _with_progress(run: dict[str, Any]) -> dict[str, Any]:
    stage = run.get("stage") or ""
    try:
        done = runner.STAGES.index(stage)
    except ValueError:
        done = len(runner.STAGES) if run.get("status") == "done" else 0
    run["progress"] = {"done": done, "total": len(runner.STAGES), "stages": list(runner.STAGES)}
    return run


# ----------------------------------------------------------- reading output


@router.get("/workspaces/{slug}/events")
def get_events(
    slug: str,
    theme: str = Query(default=""),
    hours: int = Query(default=0),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    window = hours or int(
        (workspace.get("config") or {}).get("lookback_hours") or settings.LOOKBACK_HOURS
    )
    events, total = repo.events_in_window(
        int(workspace["id"]), hours_ago_iso(window), theme=theme, limit=limit, offset=offset
    )
    for event in events:
        event["articles"] = repo.event_articles(event["id"])
    return {"events": events, "total": total, "themes": workspace.get("themes") or []}


@router.get("/workspaces/{slug}/articles")
def get_articles(
    slug: str,
    theme: str = Query(default=""),
    q: str = Query(default=""),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    include_duplicates: bool = Query(default=False),
) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    articles, total = repo.get_articles(
        int(workspace["id"]), theme=theme, query=q, limit=limit, offset=offset,
        include_duplicates=include_duplicates,
    )
    return {"articles": articles, "total": total}


@router.get("/workspaces/{slug}/search")
def search(
    slug: str, q: str = Query(default=""), limit: int = Query(default=20, le=100)
) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    try:
        results = rag.search(int(workspace["id"]), q, limit=limit)
    except EmbeddingError as exc:
        raise fail(400, exc.message, hint=exc.hint, provider=exc.provider) from exc
    return {"results": results}


@router.post("/workspaces/{slug}/chat")
def chat(slug: str, body: ChatRequest) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    try:
        return rag.answer(
            int(workspace["id"]), body.question, provider=body.provider, model=body.model
        )
    except EmbeddingError as exc:
        raise fail(400, exc.message, hint=exc.hint, provider=exc.provider) from exc
    except LLMError as exc:
        raise fail(400, exc.message, hint=exc.hint, provider=exc.provider) from exc


@router.get("/workspaces/{slug}/entities")
def get_entities(
    slug: str, kind: str = Query(default=""), hours: int = Query(default=168)
) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    return {
        "entities": repo.top_entities(
            int(workspace["id"]), hours_ago_iso(hours), kind=kind, limit=40
        )
    }


# ------------------------------------------------------------------ digests


@router.get("/workspaces/{slug}/digests")
def get_digests(slug: str) -> list[dict[str, Any]]:
    workspace = _workspace_or_404(slug)
    return repo.list_digests(int(workspace["id"]))


@router.get("/digests/{digest_id}")
def get_digest(digest_id: int) -> dict[str, Any]:
    digest = repo.get_digest(digest_id)
    if digest is None:
        raise fail(404, f"No digest {digest_id}.")
    return digest


@router.post("/workspaces/{slug}/digest")
def post_digest(slug: str, body: RunRequest = Body(default=RunRequest())) -> dict[str, Any]:
    """Rebuild the digest from what is already stored, without re-ingesting."""
    workspace = _workspace_or_404(slug)
    run = repo.create_run(int(workspace["id"]), trigger="digest-only")
    repo.save_run(int(run["id"]), stage="digest")
    finished = runner.advance(int(run["id"]), provider=body.provider, model=body.model)
    digest_id = (finished.get("stats") or {}).get("digest_id")
    if not digest_id:
        raise fail(
            400,
            "No digest could be written.",
            hint="There were no events in the window. Run the full pipeline first.",
        )
    return repo.get_digest(int(digest_id)) or {}


@router.post("/digests/{digest_id}/deliver")
def deliver_digest(digest_id: int, body: DeliverRequest) -> dict[str, Any]:
    digest = repo.get_digest(digest_id)
    if digest is None:
        raise fail(404, f"No digest {digest_id}.")
    results = delivery.deliver(digest, body.channels or ["email"], target=body.to)
    for result in results:
        repo.record_delivery(
            digest_id, channel=result.channel, target=result.target,
            status=result.status, error=result.error,
        )
    return {"results": [r.as_dict() for r in results]}


@router.get("/workspaces/{slug}/feed.xml")
def get_feed(slug: str, request: Request) -> Response:
    workspace = _workspace_or_404(slug)
    window = int(
        (workspace.get("config") or {}).get("lookback_hours") or settings.LOOKBACK_HOURS
    )
    events, _ = repo.events_in_window(
        int(workspace["id"]), hours_ago_iso(max(window, 72)), limit=60
    )
    for event in events:
        event["articles"] = repo.event_articles(event["id"])
    base = settings.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    xml = feed_renderer.render_rss(workspace, events, base_url=base)
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


# -------------------------------------------------------------------- usage


@router.get("/workspaces/{slug}/usage")
def get_usage(slug: str, hours: int = Query(default=720)) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    summary = repo.usage_summary(int(workspace["id"]), since_iso=hours_ago_iso(hours))
    summary["recent_runs"] = repo.list_runs(int(workspace["id"]), limit=15)
    return summary


@router.get("/workspaces/{slug}/health")
def source_health(slug: str) -> dict[str, Any]:
    workspace = _workspace_or_404(slug)
    sources = repo.list_sources(int(workspace["id"]))
    failing = [s for s in sources if s["consecutive_failures"] > 0]
    return {
        "sources": sources,
        "total": len(sources),
        "enabled": sum(1 for s in sources if s["enabled"]),
        "failing": len(failing),
        "disabled_by_failure": [
            s for s in sources if not s["enabled"] and s["consecutive_failures"] >= 8
        ],
    }


# --------------------------------------------------------------------- cron


def _check_cron_secret(authorization: str | None) -> None:
    """Vercel Cron sends CRON_SECRET as a bearer token, and so must anything else.

    When no secret is configured the route stays open, which is right for a
    local run and wrong in public, so /config reports the state and the UI warns.
    """
    if not settings.CRON_SECRET:
        return
    expected = f"Bearer {settings.CRON_SECRET}"
    if (authorization or "") != expected:
        raise fail(
            401,
            "This endpoint needs the scheduler secret.",
            hint="Send 'Authorization: Bearer <CRON_SECRET>'.",
        )


@router.post("/cron/tick")
@router.get("/cron/tick")
def cron_tick(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=3, le=10),
) -> dict[str, Any]:
    """Continue paused runs. This is what an external scheduler calls."""
    _check_cron_secret(authorization)
    advanced = runner.tick(limit=limit)
    return {
        "advanced": [
            {"id": r["id"], "status": r["status"], "stage": r["stage"]} for r in advanced
        ],
        "count": len(advanced),
    }


@router.post("/cron/run-all")
@router.get("/cron/run-all")
def cron_run_all(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Start a run for every enabled workspace, then continue anything paused.

    This is the daily entry point. It starts each workspace and returns
    immediately with whatever progress fitted in the invocation; the following
    ticks finish them.
    """
    _check_cron_secret(authorization)
    started: list[dict[str, Any]] = []

    for workspace in repo.list_workspaces():
        if not workspace.get("enabled"):
            continue
        existing = repo.latest_run(int(workspace["id"]))
        if existing and existing["status"] in ("running", "paused"):
            continue
        try:
            run = runner.start(workspace["slug"], trigger="cron")
        except ValueError:
            continue
        started.append({"workspace": workspace["slug"], "run_id": run["id"]})

    advanced = runner.tick(limit=len(started) + 3)
    return {
        "started": started,
        "advanced": [
            {"id": r["id"], "status": r["status"], "stage": r["stage"]} for r in advanced
        ],
    }


# ------------------------------------------------------------------ presets


@router.get("/presets")
def get_presets() -> list[dict[str, Any]]:
    return [_preset_summary(p) for p in list_presets()]


@router.get("/presets/{slug}")
def get_preset(slug: str) -> dict[str, Any]:
    preset = load_preset(slug)
    if preset is None:
        raise fail(404, f"No preset named '{slug}'.")
    return preset
