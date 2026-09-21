"""Reading a section page or a homepage as if it were a feed.

Plenty of sites publish no feed and no sitemap worth having. For those, the
front page is the index: it links to today's stories among a hundred links to
tag pages, author pages, the newsletter and the app store. Separating the two
is guesswork, so it is done with an explicit score rather than a single rule,
and the scoring reflects what an article URL actually looks like: a date in the
path, a long hyphenated slug, a couple of path segments of depth.

`discover_feeds` lives here rather than in rss.py because it is the same job in
reverse: given a page, work out what the site offers. It is what the UI's
"paste any site" button calls, so it is deliberately tolerant. Every probe that
fails is skipped, never raised.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from .. import settings
from ..util import host_of, normalise_url, strip_html, truncate
from .base import (
    FetchOutcome,
    HttpClient,
    RawItem,
    SourceAdapter,
    SourceError,
    dedupe_items,
    register,
)
from .extract import iter_anchors

# Whole path segments that mark a listing page or site furniture rather than a
# story. Matching whole segments rather than substrings matters: a headline
# slug like /feed-the-birds-2026 is an article, and a substring test would
# throw it away.
_NON_ARTICLE_SEGMENTS = frozenset(
    {
        "tag", "tags", "category", "categories", "topic", "topics", "section",
        "sections", "author", "authors", "byline", "page", "pages", "search",
        "subscribe", "subscription", "newsletter", "newsletters", "login",
        "signin", "sign-in", "signup", "sign-up", "register", "account",
        "profile", "privacy", "terms", "contact", "about", "about-us",
        "advertise", "advertising", "careers", "jobs", "sitemap", "sitemaps",
        "rss", "feed", "feeds", "amp", "podcast", "podcasts", "gallery",
        "galleries", "games", "crossword", "crosswords", "weather",
        "horoscope", "horoscopes", "shop", "store", "cart", "donate", "help",
        "faq", "faqs", "archive", "archives", "comments", "print", "share",
        "cdn-cgi", "wp-admin", "wp-login", "wp-content", "static", "assets",
    }
)

_MEDIA_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp",
    ".mp4", ".webm", ".mov", ".mp3", ".wav", ".m4a", ".ogg", ".pdf", ".doc",
    ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".gz", ".rar", ".dmg",
    ".exe", ".apk", ".css", ".js", ".json", ".xml", ".rss", ".woff", ".woff2",
    ".ttf",
)

_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "sms:", "whatsapp:", "data:")

# Two date shapes cover almost every news CMS: /2026/09/20/ and /2026-09-20-.
_DATE_IN_PATH = re.compile(r"/(19|20)\d{2}[/\-](0?[1-9]|1[0-2])([/\-]\d{1,2})?(/|-|$)")
# A numeric article id, the other strong signal a URL is a story.
_ARTICLE_ID = re.compile(r"(/|-)(\d{6,12})(/|\.|$)|/articleshow/|/story/|/article/")

# Suffixes that are part of a registrable domain rather than the site's name.
# Only the multi part ones need listing: everything else is the last two labels.
_MULTI_PART_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk", "sch.uk",
        "co.in", "net.in", "org.in", "gov.in", "ac.in", "edu.in", "firm.in",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "com.br", "net.br",
        "org.br", "gov.br", "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
        "co.kr", "or.kr", "co.za", "org.za", "net.za", "co.nz", "net.nz",
        "org.nz", "govt.nz", "com.sg", "com.my", "com.hk", "com.tw", "com.cn",
        "net.cn", "org.cn", "gov.cn", "com.mx", "com.ar", "com.tr", "com.ph",
        "com.pk", "com.ng", "com.eg", "com.sa", "com.ua", "com.pl", "com.vn",
        "co.id", "or.id", "ac.id", "co.il", "org.il", "com.co", "com.pe",
    }
)

# Probed in order of how often they are the answer. Each one costs a request
# against a host that is already being politely paced, so the list stops where
# the hit rate does.
COMMON_FEED_PATHS = (
    "/feed",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/index.xml",
    "/feeds/posts/default",
    "/?feed=rss2",
    "/blog/feed",
)

# link rel="alternate" types that mean "this is a feed".
_FEED_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/feed+json",
    "application/json",
    "text/xml",
    "application/xml",
)

_LINK_TAG_RE = re.compile(r"<link\s[^>]*?/?>", re.I | re.S)
_ATTR_RE = re.compile(r"""([\w:.\-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.I)


def registrable_domain(url_or_host: str) -> str:
    """The domain a reader would call the site, ignoring subdomains.

    Used so that a link from www.theguardian.com to amp.theguardian.com counts
    as the same publisher while a link to an ad network does not.
    """
    host = url_or_host
    if "://" in host or host.startswith("//"):
        host = host_of(host)
    host = (host or "").strip().lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return host
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_article_like(url: str) -> bool:
    """Whether a link is plausibly a story rather than furniture."""
    if not url or url.startswith(_SKIP_SCHEMES):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    path = (parts.path or "/").lower()
    if path in ("", "/"):
        return False
    if path.endswith(_MEDIA_SUFFIXES):
        return False
    segments = [segment for segment in path.split("/") if segment]
    if any(segment in _NON_ARTICLE_SEGMENTS for segment in segments):
        return False
    # A bare section like /sport or /business is an index page, unless it ends
    # in something long enough to be a slug.
    return not (len(segments) == 1 and len(segments[0]) < 24 and "-" not in segments[0])


def score_link(url: str, text: str) -> float:
    """How strongly this link looks like an article. Higher is better."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return 0.0
    path = (parts.path or "").lower()
    segments = [segment for segment in path.split("/") if segment]
    slug = segments[-1] if segments else ""

    score = 0.0
    if _DATE_IN_PATH.search(path):
        score += 5.0
    if _ARTICLE_ID.search(path):
        score += 2.5
    hyphens = slug.count("-")
    if hyphens >= 3:
        score += 3.0
    elif hyphens == 2:
        score += 1.5
    if len(slug) >= 30:
        score += 1.5
    elif len(slug) >= 18:
        score += 0.75
    if 2 <= len(segments) <= 6:
        score += 1.0
    if path.endswith((".html", ".htm")):
        score += 0.5
    # A headline's worth of anchor text is the clearest human signal there is.
    words = len(strip_html(text).split())
    if words >= 6:
        score += 2.0
    elif words >= 4:
        score += 1.0
    if parts.query:
        score -= 0.5
    if len(segments) > 7:
        score -= 1.0
    return score


def article_links(
    html: str, base_url: str, *, limit: int = 60, min_score: float = 2.0
) -> list[RawItem]:
    """Candidate story links from one page, best first.

    The anchor text becomes the provisional title. It is often the real
    headline, and when it is not the full text extractor overwrites it later.
    """
    home = registrable_domain(base_url)
    # Keyed by the normalised URL so one story counts once, but the link that
    # is kept is the one the page actually published: the normalised form is a
    # comparison key and is not always fetchable.
    scored: dict[str, tuple[float, str, str]] = {}

    for href, text in iter_anchors(html, base_url):
        href = href.split("#", 1)[0].strip()
        if not href or not is_article_like(href):
            continue
        if home and registrable_domain(href) != home:
            continue
        key = normalise_url(href)
        if not key:
            continue
        score = score_link(href, text)
        if score < min_score:
            continue
        existing = scored.get(key)
        if existing is None or score > existing[0]:
            scored[key] = (score, text, href)
            continue
        # The same story is linked several times per page, usually once from
        # an image with no text, so the richest anchor text wins.
        if len(text) > len(existing[1]):
            scored[key] = (existing[0], text, existing[2])

    ordered = sorted(scored.values(), key=lambda row: row[0], reverse=True)
    items: list[RawItem] = []
    for score, text, href in ordered[: max(1, limit)]:
        title = truncate(strip_html(text), 300)
        if not title:
            title = _title_from_slug(href)
        if not title:
            continue
        items.append(
            RawItem(
                url=href,
                title=title,
                meta={"discovered_on": base_url, "link_score": round(score, 2)},
            )
        )
    return items


def _title_from_slug(url: str) -> str:
    try:
        segments = [s for s in urlsplit(url).path.split("/") if s]
    except ValueError:
        return ""
    if not segments:
        return ""
    slug = segments[-1]
    for suffix in (".html", ".htm", ".php"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
    words = [word for word in slug.replace("_", "-").split("-") if not word.isdigit()]
    return " ".join(words).strip().capitalize()


# ------------------------------------------------------------ feed discovery


def discover_feeds(url: str, *, client: HttpClient | None = None) -> list[dict]:
    """Every feed a site appears to publish, as [{url, title, kind}].

    Declared feeds come first because a site that advertises a feed means it.
    The common paths are probed afterwards for the many sites that serve a
    perfectly good feed without ever linking to it. Each probe is independent
    and a failure only removes that one candidate.
    """
    target = (url or "").strip()
    if not target:
        return []
    if not target.startswith(("http://", "https://")):
        target = "https://" + target.lstrip("/")

    http = client or HttpClient()
    found: dict[str, dict[str, str]] = {}

    html = ""
    base = target
    try:
        response = http.get(target)
        if response.ok:
            html = response.text
            base = response.url or target
            # The URL pasted may itself be the feed.
            if _is_feed_body(html):
                _remember(found, base, _feed_title(html), "rss")
                return list(found.values())
    except SourceError:
        html = ""

    if html:
        for href, title, kind in _declared_feeds(html, base):
            _remember(found, href, title, kind)

    root = _site_root(base)
    for path in COMMON_FEED_PATHS:
        candidate = urljoin(root, path)
        if normalise_url(candidate) in found:
            continue
        # Enough declared feeds already: probing further costs a request per
        # path against a host that has told us what it has.
        if len(found) >= 6:
            break
        try:
            probe = http.get(candidate, accept="application/rss+xml, application/xml, */*")
        except SourceError:
            continue
        if not probe.ok or not _is_feed_body(probe.text):
            continue
        _remember(found, probe.url or candidate, _feed_title(probe.text), "rss")

    return list(found.values())


def _declared_feeds(html: str, base: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for match in _LINK_TAG_RE.finditer(html):
        attrs = _parse_attrs(match.group(0))
        rel = attrs.get("rel", "").lower()
        mime = attrs.get("type", "").lower()
        href = attrs.get("href", "").strip()
        if not href or "alternate" not in rel:
            continue
        if mime not in _FEED_TYPES:
            continue
        # text/xml and application/json are also used for things that are not
        # feeds, so those only count when the URL says feed as well.
        if mime in ("text/xml", "application/xml", "application/json") and not re.search(
            r"(feed|rss|atom|xml)", href, re.I
        ):
            continue
        out.append(
            (
                urljoin(base, href),
                strip_html(attrs.get("title", "")) or "",
                "atom" if "atom" in mime else "rss",
            )
        )

    # Sites that link their feed from the footer rather than the head.
    for href, text in iter_anchors(html, base):
        lowered = href.lower()
        if re.search(r"(/feed/?$|/rss/?$|\.rss$|/atom\.xml$|/feed\.xml$|/rss\.xml$|/index\.xml$)", lowered):
            out.append((href.split("#", 1)[0], strip_html(text), "rss"))
    return out


def _remember(found: dict[str, dict[str, str]], url: str, title: str, kind: str) -> None:
    key = normalise_url(url)
    if not key or key in found:
        return
    found[key] = {"url": url, "title": truncate(title or host_of(url), 200), "kind": kind}


def _is_feed_body(text: str) -> bool:
    head = (text or "")[:1500].lower()
    return any(
        marker in head
        for marker in ("<rss", "<feed", "<rdf:rdf", "<channel", "application/rss+xml")
    ) and "<html" not in head[:200]


def _feed_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.I | re.S)
    return strip_html(match.group(1)) if match else ""


def _site_root(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return f"{parts.scheme or 'https'}://{parts.netloc}/"


def _parse_attrs(tag_html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _ATTR_RE.finditer(tag_html):
        name = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        out.setdefault(name, value)
    return out


@register
class HtmlCrawlAdapter(SourceAdapter):
    kind = "html"
    label = "Web page (link scrape)"
    url_hint = "A homepage or section page, for example https://example.com/business"

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
            raise SourceError("An HTML source needs a page URL.", retryable=False)

        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        min_score = float(config.get("min_score") or 2.0)

        response = self.client.get(url, etag=etag, last_modified=last_modified)
        if response.unchanged:
            return FetchOutcome(
                items=[], status="unchanged", etag=etag, last_modified=last_modified
            )
        if not response.ok:
            raise SourceError(
                f"The page returned HTTP {response.status}.",
                retryable=response.status in (429, 500, 502, 503, 504),
                hint=(
                    "Some sites serve a challenge page to non browser clients. "
                    "A browser source can render those if this deployment "
                    "supports one."
                ),
            )

        base = response.url or url
        items = article_links(response.text, base, limit=cap * 2, min_score=min_score)
        items = dedupe_items(items)[:cap]
        if not items:
            raise SourceError(
                "No links on that page looked like articles.",
                hint=(
                    "Point the source at a section page rather than the "
                    "homepage, or lower min_score in the source config."
                ),
            )

        return FetchOutcome(
            items=items,
            status="ok",
            etag=str(response.headers.get("etag", "") or ""),
            last_modified=str(response.headers.get("last-modified", "") or ""),
            # The pipeline has no date for these until it extracts each page,
            # so `since` is deliberately not applied at this stage.
            notes={"scraped": base},
        )
