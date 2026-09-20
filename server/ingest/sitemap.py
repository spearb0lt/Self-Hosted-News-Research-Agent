"""Sitemaps, including sitemap index files and Google News sitemaps.

A sitemap is the best source for a site that publishes no feed: it is a
machine readable list the publisher maintains for search engines, and the
Google News variant even carries the headline and the publication date.

Tags are matched by local name with the namespace prefix stripped. The
namespace URIs in the wild vary by protocol version, by CMS and by typo, and
hardcoding them silently returns zero URLs from a sitemap that is otherwise
perfectly readable.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from .. import settings
from ..util import parse_datetime_iso, strip_html
from .base import (
    FetchOutcome,
    RawItem,
    SourceAdapter,
    SourceError,
    dedupe_items,
    register,
    within_window,
)

# A publisher's index can list hundreds of monthly sitemaps. Fetching them all
# would blow any serverless time budget, so only the newest few are followed
# and the rest are left for a later run with a different `since`.
DEFAULT_MAX_CHILD_SITEMAPS = 5
MAX_CHILD_SITEMAPS = 20

SITEMAP_ACCEPT = "application/xml, text/xml;q=0.9, application/gzip;q=0.5, */*;q=0.5"


def local_name(tag: str) -> str:
    """'{http://www.google.com/schemas/sitemap-news/0.9}news' becomes 'news'."""
    if not tag:
        return ""
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    if ":" in tag:
        tag = tag.rsplit(":", 1)[1]
    return tag.strip().lower()


def child_text(element: Any, name: str) -> str:
    for child in element:
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def child_element(element: Any, name: str) -> Any | None:
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def parse_xml(text: str) -> Any:
    try:
        return ElementTree.fromstring(text.strip())
    except ElementTree.ParseError as exc:
        # A byte order mark or a stray prolog is the usual cause, and trimming
        # to the first angle bracket recovers the document more often than not.
        start = text.find("<?xml")
        if start < 0:
            start = text.find("<")
        if start > 0:
            try:
                return ElementTree.fromstring(text[start:].strip())
            except ElementTree.ParseError:
                pass
        raise SourceError(
            "That URL did not parse as XML.",
            retryable=False,
            hint=f"Parser said: {exc}",
        ) from exc


def title_from_url(url: str) -> str:
    """A readable headline guessed from the slug.

    A plain sitemap entry is a bare URL, and an item with no title at all is
    discarded downstream as unusable, so the slug stands in until the full text
    extractor replaces it with the real headline.
    """
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    slug = [part for part in path.split("/") if part]
    if not slug:
        return ""
    last = slug[-1]
    for suffix in (".html", ".htm", ".php", ".amp", ".shtml"):
        if last.endswith(suffix):
            last = last[: -len(suffix)]
    if last.isdigit() and len(slug) > 1:
        last = slug[-2]
    words = [word for word in last.replace("_", "-").split("-") if word]
    # A trailing numeric article id is noise, not part of the headline.
    while words and words[-1].isdigit():
        words.pop()
    if not words:
        return ""
    return " ".join(words).strip().capitalize()


def items_from_urlset(root: Any, base_url: str) -> list[RawItem]:
    """Every <url> in a urlset, with the Google News fields when present."""
    items: list[RawItem] = []
    for url_element in root:
        if local_name(url_element.tag) != "url":
            continue
        loc = child_text(url_element, "loc")
        if not loc:
            continue
        link = urljoin(base_url, loc) if not loc.startswith("http") else loc

        published = parse_datetime_iso(child_text(url_element, "lastmod"))
        title = ""
        meta: dict[str, Any] = {}

        news = child_element(url_element, "news")
        if news is not None:
            news_title = child_text(news, "title")
            if news_title:
                title = strip_html(news_title)
            news_date = parse_datetime_iso(child_text(news, "publication_date"))
            if news_date:
                published = news_date
            keywords = child_text(news, "keywords")
            if keywords:
                meta["keywords"] = [k.strip() for k in keywords.split(",") if k.strip()]
            publication = child_element(news, "publication")
            if publication is not None:
                name = child_text(publication, "name")
                if name:
                    meta["publisher"] = name
                language = child_text(publication, "language")
                if language:
                    meta["language"] = language

        image = child_element(url_element, "image")
        image_url = child_text(image, "loc") if image is not None else ""
        changefreq = child_text(url_element, "changefreq")
        if changefreq:
            meta["changefreq"] = changefreq

        items.append(
            RawItem(
                url=link,
                title=title or title_from_url(link),
                published_at=published,
                image_url=image_url,
                language=str(meta.get("language") or ""),
                meta=meta,
            )
        )
    return items


def child_sitemaps(root: Any, base_url: str) -> list[tuple[str, str]]:
    """(url, lastmod) for each sitemap listed in an index, newest first."""
    out: list[tuple[str, str]] = []
    for element in root:
        if local_name(element.tag) != "sitemap":
            continue
        loc = child_text(element, "loc")
        if not loc:
            continue
        link = urljoin(base_url, loc) if not loc.startswith("http") else loc
        out.append((link, parse_datetime_iso(child_text(element, "lastmod"))))
    # An index is usually chronological, but not reliably, so it is sorted by
    # the declared lastmod with undated entries last.
    out.sort(key=lambda pair: pair[1] or "", reverse=True)
    return out


@register
class SitemapAdapter(SourceAdapter):
    kind = "sitemap"
    label = "XML sitemap"
    url_hint = "sitemap.xml, a sitemap index, or a Google News sitemap"

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
        if not url:
            raise SourceError("A sitemap source needs a sitemap URL.", retryable=False)

        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        max_children = max(
            1,
            min(
                MAX_CHILD_SITEMAPS,
                int(config.get("max_sitemaps") or DEFAULT_MAX_CHILD_SITEMAPS),
            ),
        )

        # A sitemap exists solely so that machines can read it, so robots.txt
        # is not the gate here. See the note in rss.py.
        response = self.client.get(
            url,
            etag=etag,
            last_modified=last_modified,
            accept=SITEMAP_ACCEPT,
            allow_robots_override=True,
        )
        if response.unchanged:
            return FetchOutcome(
                items=[], status="unchanged", etag=etag, last_modified=last_modified
            )
        if not response.ok:
            raise SourceError(
                f"The sitemap returned HTTP {response.status}.",
                retryable=response.status in (429, 500, 502, 503, 504),
                hint="Check /robots.txt for the sitemap the site actually publishes.",
            )

        base = response.url or url
        root = parse_xml(response.text)
        items: list[RawItem] = []
        followed: list[str] = []

        if local_name(root.tag) == "sitemapindex":
            for child_url, _lastmod in child_sitemaps(root, base)[:max_children]:
                try:
                    child = self.client.get(child_url, accept=SITEMAP_ACCEPT, allow_robots_override=True)
                    if not child.ok:
                        continue
                    child_root = parse_xml(child.text)
                except SourceError:
                    # One broken child must not lose the children that parse.
                    continue
                followed.append(child_url)
                # Only one level is followed: an index of indexes is rare, and
                # recursing further turns one source into an unbounded crawl.
                if local_name(child_root.tag) == "sitemapindex":
                    continue
                items.extend(items_from_urlset(child_root, child.url or child_url))
                if len(items) >= cap * 4:
                    break
        else:
            items = items_from_urlset(root, base)

        if not items:
            raise SourceError(
                "The sitemap listed no usable URLs.",
                hint="Google News sitemaps only list the last 48 hours of stories.",
            )

        # Newest first, so the cap keeps the stories that matter rather than
        # whichever ones the publisher happened to list at the top.
        items.sort(key=lambda item: item.published_at or "", reverse=True)
        items = dedupe_items(within_window(items, since))[:cap]

        return FetchOutcome(
            items=items,
            status="ok",
            etag=str(response.headers.get("etag", "") or ""),
            last_modified=str(response.headers.get("last-modified", "") or ""),
            notes={"child_sitemaps": followed} if followed else {},
        )
