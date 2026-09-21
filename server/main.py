"""The FastAPI application.

Two pieces of middleware carry most of the app's character.

The first binds any provider keys the request brought to a context variable for
exactly the lifetime of that request. That is what lets this be deployed
publicly with no keys of its own: a visitor's key rides their own requests and
is never stored, logged or shared.

The second turns the project's own exception types into a uniform error body
with a `hint` field. Almost every failure here is something the user can fix,
a missing key, a dead feed, a capability the platform does not offer, so the
fix travels with the error rather than being buried in a log.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import settings
from .api.routes import router
from .db import repo
from .embeddings import EmbeddingError
from .ingest import SourceError
from .llm import LLMError, keyring
from .presets import load_preset
from .runtime import CapabilityError
from .runtime import current as runtime

logger = logging.getLogger("newsroom")


def _seed_if_empty() -> None:
    """Give a brand new deployment something to look at.

    Without this the first screen is an empty list with no indication that
    anything works, which reads as broken rather than new.
    """
    if not settings.DEMO_SEED:
        return
    try:
        if repo.list_workspaces():
            return
        preset = load_preset(settings.DEFAULT_PRESET)
        if preset is None:
            return
        workspace = repo.create_workspace(
            name=preset.get("name") or settings.DEFAULT_PRESET,
            slug=preset.get("slug") or settings.DEFAULT_PRESET,
            description=preset.get("description") or "",
            timezone=preset.get("timezone") or settings.DEFAULT_TIMEZONE,
            language=preset.get("language") or "en",
            preset=settings.DEFAULT_PRESET,
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
    except Exception:  # noqa: BLE001 - seeding must never block start up
        logger.warning("Could not seed the default workspace.", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Serverless shutdown is capped at 500 milliseconds, so nothing slow may
    # run after the yield.
    try:
        repo.get_db().ensure_schema()
        _seed_if_empty()
    except Exception:  # noqa: BLE001 - a broken database must still return a
        # readable error from /api/health rather than failing to boot at all.
        logger.error("Start up initialisation failed.", exc_info=True)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_TAGLINE,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def bind_request_credentials(request: Request, call_next):
    """Attach this request's own API keys, and nothing else's.

    Headers are read as `X-LLM-Key-<slug>`, `X-LLM-Base-<slug>` and
    `X-LLM-Account-<slug>`. Each value is validated before it is bound, so a
    malformed or oversized value never reaches an upstream Authorization header
    or a URL path.
    """
    keys: dict[str, str] = {}
    base_urls: dict[str, str] = {}
    accounts: dict[str, str] = {}

    if settings.ALLOW_CLIENT_KEYS:
        for raw_name, raw_value in request.headers.items():
            name = raw_name.lower()
            for prefix, clean, target in (
                (keyring.KEY_PREFIX, keyring.clean_key, keys),
                (keyring.BASE_URL_PREFIX, keyring.clean_base_url, base_urls),
                (keyring.ACCOUNT_PREFIX, keyring.clean_account, accounts),
            ):
                if name.startswith(prefix) and (value := clean(raw_value)):
                    target[name[len(prefix) :]] = value
                    break

    keyring.bind(keys, base_urls, accounts)
    try:
        return await call_next(request)
    finally:
        keyring.reset()


@app.middleware("http")
async def optional_password_gate(request: Request, call_next):
    """Close a public deployment the operator does not want strangers using."""
    if not settings.APP_PASSWORD:
        return await call_next(request)
    path = request.url.path
    if path.startswith(("/api/health", "/api/docs", "/api/openapi")):
        return await call_next(request)
    supplied = request.headers.get("x-app-password") or ""
    if supplied != settings.APP_PASSWORD:
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "message": "This instance is password protected.",
                    "hint": "Send the password in the X-App-Password header.",
                    "provider": "",
                }
            },
        )
    return await call_next(request)


def _error(status: int, message: str, hint: str = "", provider: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "hint": hint, "provider": provider}},
    )


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    # Routes raise with an already shaped detail; anything else, including a
    # 404 from the framework itself, is wrapped into the same shape so the
    # client only ever parses one thing.
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return _error(exc.status_code, str(exc.detail))


@app.exception_handler(LLMError)
async def llm_error(_: Request, exc: LLMError) -> JSONResponse:
    return _error(429 if exc.retryable else 400, exc.message, exc.hint, exc.provider)


@app.exception_handler(EmbeddingError)
async def embedding_error(_: Request, exc: EmbeddingError) -> JSONResponse:
    return _error(400, exc.message, exc.hint, exc.provider)


@app.exception_handler(SourceError)
async def source_error(_: Request, exc: SourceError) -> JSONResponse:
    return _error(502 if exc.retryable else 400, exc.message, exc.hint)


@app.exception_handler(CapabilityError)
async def capability_error(_: Request, exc: CapabilityError) -> JSONResponse:
    return _error(
        501,
        f"This deployment cannot do that: {exc.capability_id}.",
        exc.reason,
    )


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error")
    return _error(
        500,
        "Something went wrong on the server.",
        str(exc)[:300] if settings.DEBUG else "Check the server logs for details.",
    )


app.include_router(router, prefix="/api")


@app.get("/api")
def api_root() -> dict[str, Any]:
    rt = runtime()
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "tier": rt.tier.value,
        "docs": "/api/docs",
    }
