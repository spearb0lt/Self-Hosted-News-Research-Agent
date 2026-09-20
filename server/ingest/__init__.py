"""The source adapters, and the one place that guarantees they are registered.

`@register` only runs when the module holding the class is imported, so every
adapter module is imported here. Anything that asks `adapter_for("rss")` gets
an answer after a single `import app.ingest`, and a new adapter becomes
reachable by adding one line below rather than by being remembered at each call
site.
"""
from __future__ import annotations

# Imported for the side effect of registering their adapters. The order is
# alphabetical rather than meaningful: nothing here depends on another module
# having run first.
from . import aggregators as aggregators  # noqa: F401
from . import browser as browser  # noqa: F401
from . import html_crawl as html_crawl  # noqa: F401
from . import newsapis as newsapis  # noqa: F401
from . import rss as rss  # noqa: F401
from . import search as search  # noqa: F401
from . import sitemap as sitemap  # noqa: F401
from .base import (
    FetchOutcome,
    HttpClient,
    RawItem,
    Response,
    SourceAdapter,
    SourceError,
    adapter_for,
    all_adapters,
    dedupe_items,
    describe_adapters,
    register,
    within_window,
)
from .extract import extract_article
from .html_crawl import discover_feeds

__all__ = [
    "FetchOutcome",
    "HttpClient",
    "RawItem",
    "Response",
    "SourceAdapter",
    "SourceError",
    "adapter_for",
    "all_adapters",
    "dedupe_items",
    "describe_adapters",
    "discover_feeds",
    "extract_article",
    "register",
    "within_window",
]
