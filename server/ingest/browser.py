"""Rendering a page with a real browser, for sites that ship no HTML.

A growing number of publishers send an empty shell and build the page in
JavaScript. A plain fetch of one of those returns navigation and nothing else,
so the crawler finds no links and the extractor finds no article. Playwright
renders the page the way a reader would see it and hands the resulting HTML to
exactly the same extraction and link scoring the other adapters use.

This costs a browser process and a few hundred megabytes, which is why it is
gated behind the `headless_browser` capability. On Vercel that capability is
false and the adapter reports the runtime's own reason rather than failing with
an ImportError at call time. The playwright import is therefore inside the
method: at module import time playwright may genuinely not exist, and this
module still has to import so the registry is complete and the UI can explain
why the option is greyed out.
"""
from __future__ import annotations

from typing import Any

from .. import settings
from ..util import truncate
from .base import (
    FetchOutcome,
    RawItem,
    SourceAdapter,
    SourceError,
    dedupe_items,
    register,
    within_window,
)
from .extract import extract_article
from .html_crawl import article_links

# How long to wait for the network to go quiet after the document loads. A page
# with an open websocket or a polling advert never reaches network idle at all,
# so this expiring is normal and not an error.
DEFAULT_IDLE_MS = 4000
MIN_ARTICLE_CHARS = 600


@register
class BrowserAdapter(SourceAdapter):
    kind = "browser"
    label = "Rendered page (headless browser)"
    url_hint = "A page that needs JavaScript to show its content"
    requires_capability = "headless_browser"

    def fetch(
        self,
        url: str,
        *,
        config: dict[str, Any] | None = None,
        etag: str = "",
        last_modified: str = "",
        limit: int = 0,
        since: str = "",
    ) -> FetchOutcome:
        available, reason = self.is_available()
        if not available:
            return FetchOutcome(items=[], status="skipped", error=reason)
        if not url:
            raise SourceError("A browser source needs a page URL.", retryable=False)

        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        mode = str(config.get("mode") or "auto").strip().lower()

        self._check_robots(url)
        html, final_url = self._render(url, config)
        if not html.strip():
            raise SourceError(
                "The browser rendered an empty page.",
                hint="The site may be blocking automated browsers.",
            )

        article = extract_article(html, final_url)
        items: list[RawItem] = []

        wants_article = mode in ("auto", "article", "both")
        if wants_article and len(article.get("content") or "") >= MIN_ARTICLE_CHARS:
            items.append(
                RawItem(
                    url=article.get("canonical_url") or final_url,
                    title=article.get("title") or "",
                    summary=truncate(article.get("content") or "", 600),
                    content=article.get("content") or "",
                    author=article.get("author") or "",
                    published_at=article.get("published_at") or "",
                    image_url=article.get("image_url") or "",
                    language=article.get("language") or "",
                    meta={"rendered": True, "extraction": article.get("method")},
                )
            )

        # A rendered page that is not itself an article is an index, and an
        # index is only useful for the links on it.
        wants_links = mode in ("links", "both") or (mode == "auto" and not items)
        if wants_links:
            found = article_links(
                html,
                final_url,
                limit=cap * 2,
                min_score=float(config.get("min_score") or 2.0),
            )
            for item in found:
                item.meta["rendered"] = True
            items.extend(found)

        items = dedupe_items(within_window(items, since))[:cap]
        if not items:
            raise SourceError(
                "The rendered page yielded neither an article nor article links.",
                hint=(
                    "Set `wait_for` in the config to a CSS selector that only "
                    "appears once the content has loaded."
                ),
            )

        return FetchOutcome(
            items=items,
            status="ok",
            notes={"rendered_url": final_url, "extraction": article.get("method")},
        )

    def _check_robots(self, url: str) -> None:
        """Politeness cannot depend on which engine does the fetching.

        The shared client enforces robots.txt inside `get`, and this adapter
        does not call `get`, so the same check is made explicitly here.
        """
        if not self.client.respect_robots:
            return
        from .base import _robots  # noqa: PLC0415 - one shared cache per process

        if not _robots.allows(url, self.client.user_agent):
            raise SourceError(
                "robots.txt disallows rendering that page.",
                retryable=False,
                hint="Set RESPECT_ROBOTS=0 only with the site owner's permission.",
            )

    def _render(self, url: str, config: dict[str, Any]) -> tuple[str, str]:
        """Load the page in Chromium and return (html, final url).

        Imported here rather than at module scope so this module loads on a
        deployment where playwright is not installed at all.
        """
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as exc:
            raise SourceError(
                "Playwright is not installed in this environment.",
                retryable=False,
                hint="pip install -r requirements-server.txt && playwright install chromium",
            ) from exc

        timeout_ms = int(float(config.get("timeout") or settings.FETCH_TIMEOUT) * 1000)
        idle_ms = int(config.get("idle_ms") or DEFAULT_IDLE_MS)
        wait_for = str(config.get("wait_for") or "").strip()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                )
                try:
                    context = browser.new_context(
                        user_agent=self.client.user_agent,
                        viewport={"width": 1366, "height": 900},
                        locale=str(config.get("locale") or "en-IN"),
                    )
                    page = context.new_page()
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                    if response is not None and response.status >= 400:
                        raise SourceError(
                            f"The page returned HTTP {response.status}.",
                            retryable=response.status in (429, 500, 502, 503, 504),
                        )
                    try:
                        page.wait_for_load_state("networkidle", timeout=idle_ms)
                    except Exception:  # noqa: BLE001
                        # Never reaching network idle is the normal case on an
                        # ad funded page, and the DOM is usually ready anyway.
                        pass
                    if wait_for:
                        try:
                            page.wait_for_selector(wait_for, timeout=idle_ms)
                        except Exception:  # noqa: BLE001
                            pass
                    html = page.content()
                    final_url = page.url or url
                    return html, final_url
                finally:
                    browser.close()
        except SourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalised for the caller
            raise SourceError(f"Rendering {url} failed: {exc}") from exc
