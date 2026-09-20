"""RSS, Atom and RDF feeds, which is how most of a digest arrives.

feedparser already normalises the three formats and the dozen namespaces
publishers sprinkle over them, so the work here is defensive field mapping:
every field a feed can omit, it does omit somewhere, and an adapter that
assumes otherwise loses a whole source to an AttributeError.

Conditional requests matter more here than anywhere else. A feed is polled
every run and changes a few times a day, so sending back the etag and
Last-Modified the previous run stored turns most polls into a 304 with no body.
That is the single largest bandwidth and rate limit saving in the app, which is
why `unchanged` is a first class outcome rather than an empty `ok`.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

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

FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
    "application/rdf+xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
)


def parse_feed(payload: bytes | str) -> Any:
    """feedparser over a body already fetched by the shared client.

    The bytes are preferred over the decoded text because feedparser reads the
    XML declaration to pick an encoding, and a feed that lies about its charset
    is common enough to matter.
    """
    try:
        import feedparser  # noqa: PLC0415 - kept local so the module imports without it
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise SourceError(
            "feedparser is not installed, so feed sources cannot be read.",
            retryable=False,
            hint="pip install -r requirements.txt",
        ) from exc
    return feedparser.parse(payload)


def feed_title(parsed: Any) -> str:
    feed = getattr(parsed, "feed", None) or {}
    return strip_html(str(feed.get("title") or feed.get("subtitle") or "")).strip()


def entry_link(entry: Any, base_url: str = "") -> str:
    """The entry's canonical link, however this feed chose to express it.

    RSS uses <link>, Atom uses a list of <link rel="alternate">, and some feeds
    only carry a usable URL in <guid>. Relative links are resolved against the
    feed's own URL because a self hosted blog emitting '/posts/x' is common.
    """
    candidates: list[str] = []
    direct = entry.get("link") if hasattr(entry, "get") else ""
    if direct:
        candidates.append(str(direct))

    for link in entry.get("links", []) or []:
        href = (link.get("href") or "").strip() if hasattr(link, "get") else ""
        if not href:
            continue
        rel = (link.get("rel") or "").lower() if hasattr(link, "get") else ""
        if rel in ("", "alternate"):
            candidates.append(href)

    guid = entry.get("id") or entry.get("guid") or ""
    if guid and str(guid).startswith(("http://", "https://")):
        candidates.append(str(guid))

    for candidate in candidates:
        value = candidate.strip()
        if not value:
            continue
        if value.startswith(("http://", "https://")):
            return value
        if base_url:
            try:
                return urljoin(base_url, value)
            except ValueError:
                continue
    return ""


def entry_published(entry: Any) -> str:
    """The best date this entry offers, as ISO UTC, or empty.

    The parsed struct_time fields are tried before the raw strings because
    feedparser has already resolved the timezone there.
    """
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(field) if hasattr(entry, "get") else None
        if value:
            stamp = parse_datetime_iso(tuple(value))
            if stamp:
                return stamp
    for field in ("published", "updated", "created", "pubDate", "dc_date", "date"):
        value = entry.get(field) if hasattr(entry, "get") else None
        if value:
            stamp = parse_datetime_iso(value)
            if stamp:
                return stamp
    return ""


def entry_author(entry: Any) -> str:
    detail = entry.get("author_detail") or {}
    if hasattr(detail, "get") and detail.get("name"):
        return str(detail.get("name"))
    for field in ("author", "dc_creator", "creator", "publisher"):
        value = entry.get(field)
        if value:
            return str(value)
    authors = entry.get("authors") or []
    names = [
        str(a.get("name")) for a in authors if hasattr(a, "get") and a.get("name")
    ]
    return ", ".join(names)


def entry_content(entry: Any) -> str:
    """Full text when the feed carries it, which some publishers still do."""
    blocks = entry.get("content") or []
    for block in blocks:
        value = block.get("value") if hasattr(block, "get") else ""
        if value and value.strip():
            return strip_html(value).strip()
    for field in ("content_encoded", "fulltext"):
        value = entry.get(field)
        if value:
            return strip_html(str(value)).strip()
    return ""


def entry_summary(entry: Any) -> str:
    for field in ("summary", "description", "subtitle"):
        value = entry.get(field)
        if value and str(value).strip():
            return str(value)
    detail = entry.get("summary_detail") or {}
    if hasattr(detail, "get") and detail.get("value"):
        return str(detail.get("value"))
    return ""


def entry_image(entry: Any, base_url: str = "") -> str:
    """The lead image, from whichever of the four common namespaces carries it."""
    candidates: list[str] = []

    for thumb in entry.get("media_thumbnail") or []:
        url = thumb.get("url") if hasattr(thumb, "get") else ""
        if url:
            candidates.append(str(url))

    for media in entry.get("media_content") or []:
        if not hasattr(media, "get"):
            continue
        medium = str(media.get("medium") or "").lower()
        mime = str(media.get("type") or "").lower()
        url = str(media.get("url") or "")
        if url and (medium == "image" or mime.startswith("image/") or not mime):
            candidates.append(url)

    for enclosure in entry.get("enclosures") or []:
        if not hasattr(enclosure, "get"):
            continue
        mime = str(enclosure.get("type") or "").lower()
        href = str(enclosure.get("href") or enclosure.get("url") or "")
        if href and (mime.startswith("image/") or _looks_like_image(href)):
            candidates.append(href)

    for link in entry.get("links") or []:
        if not hasattr(link, "get"):
            continue
        if (link.get("rel") or "") == "enclosure":
            mime = str(link.get("type") or "").lower()
            href = str(link.get("href") or "")
            if href and (mime.startswith("image/") or _looks_like_image(href)):
                candidates.append(href)

    thumbnail = entry.get("media_group") or {}
    if hasattr(thumbnail, "get"):
        for thumb in thumbnail.get("media_thumbnail") or []:
            if hasattr(thumb, "get") and thumb.get("url"):
                candidates.append(str(thumb.get("url")))

    for candidate in candidates:
        value = candidate.strip()
        if not value:
            continue
        if value.startswith("//"):
            return "https:" + value
        if value.startswith(("http://", "https://")):
            return value
        if base_url:
            try:
                return urljoin(base_url, value)
            except ValueError:
                continue
    return ""


def entry_tags(entry: Any) -> list[str]:
    out: list[str] = []
    for tag in entry.get("tags") or []:
        term = tag.get("term") if hasattr(tag, "get") else ""
        if term:
            out.append(str(term))
    return out[:12]


def entry_to_item(entry: Any, base_url: str = "", *, extra_meta: dict[str, Any] | None = None) -> RawItem | None:
    """One feed entry as a RawItem, or None when it carries no usable link."""
    link = entry_link(entry, base_url)
    if not link:
        return None

    meta: dict[str, Any] = {}
    tags = entry_tags(entry)
    if tags:
        meta["tags"] = tags
    entry_id = entry.get("id") or entry.get("guid") or ""
    if entry_id:
        meta["guid"] = str(entry_id)
    if extra_meta:
        meta.update(extra_meta)

    return RawItem(
        url=link,
        title=str(entry.get("title") or ""),
        summary=entry_summary(entry),
        content=entry_content(entry),
        author=entry_author(entry),
        published_at=entry_published(entry),
        image_url=entry_image(entry, base_url),
        language=str(entry.get("language") or ""),
        meta=meta,
    )


def items_from_feed(
    parsed: Any, base_url: str = "", *, extra_meta: dict[str, Any] | None = None
) -> list[RawItem]:
    items: list[RawItem] = []
    for entry in getattr(parsed, "entries", []) or []:
        item = entry_to_item(entry, base_url, extra_meta=extra_meta)
        if item is not None:
            items.append(item)
    return items


def _looks_like_image(url: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"))


@register
class RssAdapter(SourceAdapter):
    kind = "rss"
    label = "RSS / Atom feed"
    url_hint = "The feed URL, for example https://feeds.bbci.co.uk/news/world/rss.xml"

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
            raise SourceError("An RSS source needs a feed URL.", retryable=False)

        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE

        # A feed is not a crawl. robots.txt governs a crawler discovering
        # content it was not pointed at; a feed is published specifically to be
        # read by machines, and this one was configured by the person running
        # the app. No feed reader consults robots.txt before fetching a
        # subscription, and enforcing it here breaks official, documented APIs:
        # export.arxiv.org disallows the very path its own API lives on.
        #
        # Robots is still enforced for the crawl-shaped adapters and for
        # full-text extraction, which is where it actually applies.
        response = self.client.get(
            url,
            etag=etag,
            last_modified=last_modified,
            accept=FEED_ACCEPT,
            allow_robots_override=True,
        )
        if response.unchanged:
            # Nothing changed since the last poll, so the previous validators
            # are handed straight back for the next one.
            return FetchOutcome(
                items=[],
                status="unchanged",
                etag=etag,
                last_modified=last_modified,
            )
        if not response.ok:
            raise SourceError(
                f"The feed returned HTTP {response.status}.",
                retryable=response.status in (429, 500, 502, 503, 504),
                hint=(
                    "Check the URL in a browser. Many sites moved their feeds "
                    "when they changed CMS."
                ),
            )

        parsed = parse_feed(response.content or response.text)
        entries = getattr(parsed, "entries", []) or []
        if not entries:
            bozo = getattr(parsed, "bozo_exception", None)
            raise SourceError(
                "The feed parsed to zero entries.",
                hint=(
                    f"Parser said: {bozo}"
                    if bozo
                    else "The URL may be an HTML page rather than a feed."
                ),
            )

        base = response.url or url
        source_title = feed_title(parsed)
        items = items_from_feed(
            parsed,
            base,
            extra_meta={"feed_title": source_title} if source_title else None,
        )
        items = dedupe_items(within_window(items, since))[:cap]

        return FetchOutcome(
            items=items,
            status="ok",
            etag=str(response.headers.get("etag", "") or ""),
            last_modified=str(response.headers.get("last-modified", "") or ""),
            notes={"feed_title": source_title, "entries_seen": len(entries)},
        )
