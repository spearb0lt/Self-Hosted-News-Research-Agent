"""The keyed news APIs, one adapter each over a shared base.

Every one of these is the same three steps with different spellings: put a key
somewhere in the request, ask for recent articles matching a query, and read a
list of objects whose field names nobody agreed on. The base class owns the
steps, and each adapter owns only the spelling.

Two things justify the shared base beyond tidiness. First, a missing key must
be a `skipped` outcome with a sentence a user can act on, never an exception:
these are optional sources and a deployment is expected to have keys for a
couple of them at most. Second, every one of these providers has a small free
tier, so a 429 is the normal failure mode rather than an exotic one, and the
error carries the specific limit that was hit so nobody has to go and look it
up.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import settings
from ..llm import keyring
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


class KeyedNewsAdapter(SourceAdapter):
    """Shared plumbing for a commercial news API behind a key."""

    # Slug under which a visitor's own key arrives, from keyring.DATA_SLUGS.
    slug: str = ""
    # Attribute on app.settings holding the server's key.
    settings_attr: str = ""
    # The free tier's shape, quoted back when the provider says no.
    free_tier: str = ""
    docs_url: str = ""

    @property
    def api_key(self) -> str:
        """The visitor's key wins, exactly as it does for the LLM providers."""
        if self.slug:
            supplied = keyring.key_for(self.slug)
            if supplied:
                return supplied
        return str(getattr(settings, self.settings_attr, "") or "")

    def missing_key_message(self) -> str:
        names = " or ".join(self.key_names) if self.key_names else self.settings_attr
        where = f" Get one at {self.docs_url}." if self.docs_url else ""
        return (
            f"No {self.label or self.kind} key. Paste one in Settings or set "
            f"{names} on the server.{where}"
        )

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
        if not self.api_key:
            return FetchOutcome(items=[], status="skipped", error=self.missing_key_message())

        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        payload = self.call(url=url, config=config, limit=cap, since=since)
        items = dedupe_items(within_window(self.to_items(payload, config), since))[:cap]
        return FetchOutcome(items=items, status="ok", notes=self.notes(payload))

    # ------------------------------------------------------------ per provider

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        raise NotImplementedError

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        raise NotImplementedError

    def notes(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            for key in ("totalResults", "totalArticles", "totalHits"):
                if key in payload:
                    return {"total_results": payload.get(key)}
        return {}

    # ---------------------------------------------------------------- requests

    def request_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """A JSON call whose failures name the provider's own free tier limit.

        `HttpClient.get_json` would do most of this, but it cannot know which
        provider's quota was exhausted, and "HTTP 429" on its own sends the
        user hunting through documentation.
        """
        response = self.client.get(
            endpoint,
            params={k: v for k, v in (params or {}).items() if v not in (None, "")},
            headers=headers,
            accept="application/json",
            allow_robots_override=True,
        )

        if response.status == 429:
            raise SourceError(
                f"{self.label or self.kind} rate limited this request.",
                retryable=True,
                hint=self.free_tier or "The free tier's quota is used up for now.",
            )
        if response.status in (401, 403):
            raise SourceError(
                f"{self.label or self.kind} rejected the API key.",
                retryable=False,
                hint=self.free_tier or _provider_message(response.text),
            )
        if not response.ok:
            raise SourceError(
                f"{self.label or self.kind} returned HTTP {response.status}.",
                retryable=response.status >= 500,
                hint=_provider_message(response.text) or self.free_tier,
            )

        try:
            payload = json.loads(response.text)
        except ValueError as exc:
            raise SourceError(
                f"{self.label or self.kind} did not return JSON.",
                hint=response.text[:200],
            ) from exc

        # Several of these answer 200 with an error document inside.
        if isinstance(payload, dict):
            status = str(payload.get("status") or "").lower()
            if status in ("error", "fail", "failed"):
                raise SourceError(
                    f"{self.label or self.kind}: {_provider_message(response.text)}",
                    retryable=False,
                    hint=self.free_tier,
                )
            if isinstance(payload.get("error"), (str, dict)):
                raise SourceError(
                    f"{self.label or self.kind}: {_provider_message(response.text)}",
                    retryable=False,
                    hint=self.free_tier,
                )
        return payload


def _provider_message(body: str) -> str:
    """The human readable part of an error document, whatever shape it took."""
    try:
        payload = json.loads(body or "")
    except ValueError:
        return strip_html(body or "")[:200]
    if isinstance(payload, dict):
        for key in ("message", "error", "errors", "detail", "results", "info"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
            if isinstance(value, dict):
                for inner in ("message", "info", "code", "details"):
                    if isinstance(value.get(inner), str):
                        return str(value[inner])[:300]
            if isinstance(value, list) and value:
                return str(value[0])[:300]
    return strip_html(body or "")[:200]


def _yyyy_mm_dd(iso: str) -> str:
    stamp = parse_datetime_iso(iso)
    return stamp[:10] if stamp else ""


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# ------------------------------------------------------------------- NewsAPI


@register
class NewsApiAdapter(KeyedNewsAdapter):
    kind = "newsapi"
    label = "NewsAPI.org"
    urlless = True
    url_hint = "Leave blank. Set `query`, or `country`/`category` for top headlines"
    key_names = ("NEWSAPI_KEY",)
    slug = "newsapi"
    settings_attr = "NEWSAPI_KEY"
    docs_url = "https://newsapi.org/register"
    free_tier = (
        "NewsAPI's free plan allows 100 requests a day, delays articles by up "
        "to 24 hours, and only accepts requests from localhost."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        query = str(config.get("query") or config.get("q") or "").strip()
        country = str(config.get("country") or "").strip()
        category = str(config.get("category") or "").strip()
        endpoint_choice = str(config.get("endpoint") or "").strip().lower()

        # Top headlines is the only endpoint that understands a country or a
        # category, and /everything is the only one that takes a bare query.
        top = endpoint_choice == "top-headlines" or (not query and (country or category))
        headers = {"X-Api-Key": self.api_key}

        if top:
            return self.request_json(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "q": query,
                    "country": country.lower(),
                    "category": category.lower(),
                    "sources": str(config.get("sources") or ""),
                    "pageSize": min(100, limit),
                },
                headers=headers,
            )
        return self.request_json(
            "https://newsapi.org/v2/everything",
            params={
                "q": query or "news",
                "language": str(config.get("language") or "en"),
                "sortBy": str(config.get("sort") or "publishedAt"),
                "domains": str(config.get("domains") or ""),
                "from": parse_datetime_iso(since) or "",
                "pageSize": min(100, limit),
            },
            headers=headers,
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        items: list[RawItem] = []
        for article in (payload or {}).get("articles") or []:
            link = str(article.get("url") or "")
            if not link:
                continue
            source = article.get("source") or {}
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("title") or ""),
                    summary=str(article.get("description") or ""),
                    # The free plan truncates content at 200 characters with a
                    # "[+1234 chars]" marker, so it is a summary at best.
                    content=strip_html(str(article.get("content") or "")),
                    author=str(article.get("author") or ""),
                    published_at=parse_datetime_iso(article.get("publishedAt")),
                    image_url=str(article.get("urlToImage") or ""),
                    meta={"publisher": str(source.get("name") or ""), "via": "newsapi"},
                )
            )
        return items


# --------------------------------------------------------------------- GNews


@register
class GNewsAdapter(KeyedNewsAdapter):
    kind = "gnews"
    label = "GNews"
    urlless = True
    url_hint = "Leave blank and set `query` in the config"
    key_names = ("GNEWS_API_KEY",)
    slug = "gnews"
    settings_attr = "GNEWS_API_KEY"
    docs_url = "https://gnews.io/register"
    free_tier = (
        "GNews' free plan allows 100 requests a day, returns at most 10 "
        "articles per request, and delays articles by about 12 hours."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        query = str(config.get("query") or config.get("q") or "").strip()
        endpoint = (
            "https://gnews.io/api/v4/top-headlines"
            if not query
            else "https://gnews.io/api/v4/search"
        )
        return self.request_json(
            endpoint,
            params={
                "q": query or None,
                "lang": str(config.get("language") or "en"),
                "country": str(config.get("country") or ""),
                "topic": str(config.get("topic") or ""),
                "from": parse_datetime_iso(since) or "",
                # The free plan caps this at 10 whatever is asked for.
                "max": min(10, limit),
                "apikey": self.api_key,
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        items: list[RawItem] = []
        for article in (payload or {}).get("articles") or []:
            link = str(article.get("url") or "")
            if not link:
                continue
            source = article.get("source") or {}
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("title") or ""),
                    summary=str(article.get("description") or ""),
                    content=strip_html(str(article.get("content") or "")),
                    published_at=parse_datetime_iso(article.get("publishedAt")),
                    image_url=str(article.get("image") or ""),
                    meta={"publisher": str(source.get("name") or ""), "via": "gnews"},
                )
            )
        return items


# ------------------------------------------------------------------ NewsData


@register
class NewsDataAdapter(KeyedNewsAdapter):
    kind = "newsdata"
    label = "NewsData.io"
    urlless = True
    url_hint = "Leave blank and set `query`, `country` or `category`"
    key_names = ("NEWSDATA_API_KEY",)
    slug = "newsdata"
    settings_attr = "NEWSDATA_API_KEY"
    docs_url = "https://newsdata.io/register"
    free_tier = (
        "NewsData's free plan allows 200 credits a day and returns 10 articles "
        "per credit, so one request per run is the budget."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        return self.request_json(
            "https://newsdata.io/api/1/latest",
            params={
                "apikey": self.api_key,
                "q": str(config.get("query") or config.get("q") or ""),
                "language": str(config.get("language") or "en"),
                "country": str(config.get("country") or ""),
                "category": str(config.get("category") or ""),
                "domain": str(config.get("domain") or ""),
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        items: list[RawItem] = []
        for article in (payload or {}).get("results") or []:
            link = str(article.get("link") or "")
            if not link:
                continue
            creators = article.get("creator") or []
            author = ", ".join(str(c) for c in creators if c) if isinstance(creators, list) else str(creators or "")
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("title") or ""),
                    summary=str(article.get("description") or ""),
                    content=strip_html(str(article.get("content") or "")),
                    author=author,
                    published_at=parse_datetime_iso(article.get("pubDate")),
                    image_url=str(article.get("image_url") or ""),
                    language=str(article.get("language") or ""),
                    meta={
                        "publisher": str(article.get("source_id") or ""),
                        "categories": article.get("category") or [],
                        "country": article.get("country") or [],
                        "via": "newsdata",
                    },
                )
            )
        return items

    def notes(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return {
                "total_results": payload.get("totalResults"),
                "next_page": payload.get("nextPage"),
            }
        return {}


# ------------------------------------------------------------------- Currents


@register
class CurrentsAdapter(KeyedNewsAdapter):
    kind = "currents"
    label = "Currents API"
    urlless = True
    url_hint = "Leave blank and set `query` in the config"
    key_names = ("CURRENTS_API_KEY",)
    slug = "currents"
    settings_attr = "CURRENTS_API_KEY"
    docs_url = "https://currentsapi.services/en/register"
    free_tier = (
        "Currents' free plan allows 250 requests a day and returns at most 20 "
        "results per request."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        query = str(config.get("query") or config.get("q") or "").strip()
        endpoint = (
            "https://api.currentsapi.services/v1/search"
            if query or config.get("category") or config.get("country")
            else "https://api.currentsapi.services/v1/latest-news"
        )
        return self.request_json(
            endpoint,
            params={
                "keywords": query or None,
                "language": str(config.get("language") or "en"),
                "country": str(config.get("country") or ""),
                "category": str(config.get("category") or ""),
                "start_date": parse_datetime_iso(since) or "",
                "page_size": min(20, limit),
            },
            headers={"Authorization": self.api_key},
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        items: list[RawItem] = []
        for article in (payload or {}).get("news") or []:
            link = str(article.get("url") or "")
            if not link:
                continue
            image = str(article.get("image") or "")
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("title") or ""),
                    summary=str(article.get("description") or ""),
                    author=str(article.get("author") or ""),
                    published_at=parse_datetime_iso(article.get("published")),
                    # Currents writes the literal string "None" when a story
                    # has no image.
                    image_url="" if image.lower() in ("none", "null") else image,
                    language=str(article.get("language") or ""),
                    meta={
                        "categories": article.get("category") or [],
                        "currents_id": article.get("id"),
                        "via": "currents",
                    },
                )
            )
        return items


# ------------------------------------------------------------------ Mediastack


@register
class MediastackAdapter(KeyedNewsAdapter):
    kind = "mediastack"
    label = "Mediastack"
    urlless = True
    url_hint = "Leave blank and set `query`, `countries` or `categories`"
    key_names = ("MEDIASTACK_API_KEY",)
    slug = "mediastack"
    settings_attr = "MEDIASTACK_API_KEY"
    docs_url = "https://mediastack.com/signup/free"
    free_tier = (
        "Mediastack's free plan allows 100 requests a MONTH and serves plain "
        "HTTP only, so schedule this source sparingly."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        # The free plan rejects https outright, so http is the default and an
        # operator on a paid plan can turn it back on.
        scheme = "https" if config.get("https") else "http"
        return self.request_json(
            f"{scheme}://api.mediastack.com/v1/news",
            params={
                "access_key": self.api_key,
                "keywords": str(config.get("query") or config.get("q") or ""),
                "languages": str(config.get("language") or "en"),
                "countries": str(config.get("countries") or config.get("country") or ""),
                "categories": str(config.get("categories") or config.get("category") or ""),
                "sources": str(config.get("sources") or ""),
                "sort": str(config.get("sort") or "published_desc"),
                "limit": min(100, limit),
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        items: list[RawItem] = []
        for article in (payload or {}).get("data") or []:
            link = str(article.get("url") or "")
            if not link:
                continue
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("title") or ""),
                    summary=str(article.get("description") or ""),
                    author=str(article.get("author") or ""),
                    published_at=parse_datetime_iso(article.get("published_at")),
                    image_url=str(article.get("image") or ""),
                    language=str(article.get("language") or ""),
                    meta={
                        "publisher": str(article.get("source") or ""),
                        "category": str(article.get("category") or ""),
                        "country": str(article.get("country") or ""),
                        "via": "mediastack",
                    },
                )
            )
        return items

    def notes(self, payload: Any) -> dict[str, Any]:
        pagination = (payload or {}).get("pagination") or {}
        return {"total_results": pagination.get("total")} if pagination else {}


# ------------------------------------------------------------------ Guardian


@register
class GuardianAdapter(KeyedNewsAdapter):
    kind = "guardian"
    label = "The Guardian"
    urlless = True
    url_hint = "Leave blank and set `query` or `section` in the config"
    key_names = ("GUARDIAN_API_KEY",)
    slug = "guardian"
    settings_attr = "GUARDIAN_API_KEY"
    docs_url = "https://open-platform.theguardian.com/access/"
    free_tier = (
        "The Guardian's developer key allows 500 calls a day and 1 call per "
        "second, and excludes images from some content."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        return self.request_json(
            "https://content.guardianapis.com/search",
            params={
                "api-key": self.api_key,
                "q": str(config.get("query") or config.get("q") or ""),
                "section": str(config.get("section") or ""),
                "tag": str(config.get("tag") or ""),
                "from-date": _yyyy_mm_dd(since),
                "order-by": str(config.get("sort") or "newest"),
                "page-size": min(50, limit),
                "show-fields": "trailText,byline,thumbnail,bodyText,headline,firstPublicationDate",
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        results = ((payload or {}).get("response") or {}).get("results") or []
        items: list[RawItem] = []
        for article in results:
            link = str(article.get("webUrl") or "")
            if not link:
                continue
            fields = article.get("fields") or {}
            items.append(
                RawItem(
                    url=link,
                    title=str(fields.get("headline") or article.get("webTitle") or ""),
                    summary=str(fields.get("trailText") or ""),
                    content=strip_html(str(fields.get("bodyText") or "")),
                    author=str(fields.get("byline") or ""),
                    published_at=parse_datetime_iso(
                        fields.get("firstPublicationDate") or article.get("webPublicationDate")
                    ),
                    image_url=str(fields.get("thumbnail") or ""),
                    meta={
                        "publisher": "The Guardian",
                        "section": str(article.get("sectionName") or ""),
                        "pillar": str(article.get("pillarName") or ""),
                        "via": "guardian",
                    },
                )
            )
        return items

    def notes(self, payload: Any) -> dict[str, Any]:
        response = (payload or {}).get("response") or {}
        return {"total_results": response.get("total")} if response else {}


# ----------------------------------------------------------------------- NYT


@register
class NytAdapter(KeyedNewsAdapter):
    kind = "nyt"
    label = "New York Times"
    urlless = True
    url_hint = "Leave blank and set `query` or `fq` in the config"
    key_names = ("NYT_API_KEY",)
    slug = "nyt"
    settings_attr = "NYT_API_KEY"
    docs_url = "https://developer.nytimes.com/get-started"
    free_tier = (
        "The NYT developer key allows 5 requests a minute and 500 a day, and "
        "Article Search returns 10 results per page."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        return self.request_json(
            "https://api.nytimes.com/svc/search/v2/articlesearch.json",
            params={
                "api-key": self.api_key,
                "q": str(config.get("query") or config.get("q") or ""),
                "fq": str(config.get("fq") or ""),
                "sort": str(config.get("sort") or "newest"),
                "begin_date": _yyyy_mm_dd(since).replace("-", ""),
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        docs = ((payload or {}).get("response") or {}).get("docs") or []
        items: list[RawItem] = []
        for doc in docs:
            link = str(doc.get("web_url") or "")
            if not link:
                continue
            headline = doc.get("headline") or {}
            byline = doc.get("byline") or {}
            items.append(
                RawItem(
                    url=link,
                    title=str(headline.get("main") or doc.get("abstract") or ""),
                    summary=str(doc.get("abstract") or doc.get("snippet") or ""),
                    content=strip_html(str(doc.get("lead_paragraph") or "")),
                    author=str(byline.get("original") or "").removeprefix("By ").strip(),
                    published_at=parse_datetime_iso(doc.get("pub_date")),
                    image_url=_nyt_image(doc),
                    meta={
                        "publisher": "The New York Times",
                        "section": str(doc.get("section_name") or ""),
                        "document_type": str(doc.get("document_type") or ""),
                        "via": "nyt",
                    },
                )
            )
        return items

    def notes(self, payload: Any) -> dict[str, Any]:
        meta = (((payload or {}).get("response") or {}).get("meta")) or {}
        return {"total_results": meta.get("hits")} if meta else {}


def _nyt_image(doc: dict[str, Any]) -> str:
    """The lead image, from either shape of the multimedia field.

    Article Search returned a list of assets for years and now returns an
    object with a default rendition, and both are still served.
    """
    multimedia = doc.get("multimedia")
    candidate = ""
    if isinstance(multimedia, dict):
        default = multimedia.get("default") or {}
        candidate = str(default.get("url") or multimedia.get("url") or "")
    elif isinstance(multimedia, list) and multimedia:
        first = multimedia[0]
        if isinstance(first, dict):
            candidate = str(first.get("url") or "")
    if not candidate:
        return ""
    if candidate.startswith("http"):
        return candidate
    return f"https://www.nytimes.com/{candidate.lstrip('/')}"


# ----------------------------------------------------------------- Marketaux


@register
class MarketauxAdapter(KeyedNewsAdapter):
    kind = "marketaux"
    label = "Marketaux"
    urlless = True
    url_hint = "Leave blank and set `symbols` (for example RELIANCE.NS,AAPL) or `query`"
    key_names = ("MARKETAUX_API_KEY",)
    slug = "marketaux"
    settings_attr = "MARKETAUX_API_KEY"
    docs_url = "https://www.marketaux.com/pricing"
    free_tier = (
        "Marketaux's free plan allows 100 requests a day and returns 3 "
        "articles per request."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        return self.request_json(
            "https://api.marketaux.com/v1/news/all",
            params={
                "api_token": self.api_key,
                "symbols": str(config.get("symbols") or ""),
                "search": str(config.get("query") or config.get("q") or ""),
                "industries": str(config.get("industries") or ""),
                "countries": str(config.get("countries") or config.get("country") or ""),
                "language": str(config.get("language") or "en"),
                "published_after": (parse_datetime_iso(since) or "")[:16],
                "filter_entities": "true" if config.get("entities", True) else "false",
                "limit": min(50, limit),
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        items: list[RawItem] = []
        for article in (payload or {}).get("data") or []:
            link = str(article.get("url") or "")
            if not link:
                continue
            entities = article.get("entities") or []
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("title") or ""),
                    summary=str(article.get("description") or article.get("snippet") or ""),
                    published_at=parse_datetime_iso(article.get("published_at")),
                    image_url=str(article.get("image_url") or ""),
                    language=str(article.get("language") or ""),
                    meta={
                        "publisher": str(article.get("source") or ""),
                        "symbols": [
                            str(entity.get("symbol"))
                            for entity in entities
                            if isinstance(entity, dict) and entity.get("symbol")
                        ],
                        "via": "marketaux",
                    },
                )
            )
        return items

    def notes(self, payload: Any) -> dict[str, Any]:
        meta = (payload or {}).get("meta") or {}
        return {"total_results": meta.get("found")} if meta else {}


# ------------------------------------------------------------------- Finnhub


@register
class FinnhubAdapter(KeyedNewsAdapter):
    kind = "finnhub"
    label = "Finnhub"
    urlless = True
    url_hint = "Leave blank and set `symbol` (for example TCS.NS) for company news"
    key_names = ("FINNHUB_API_KEY",)
    slug = "finnhub"
    settings_attr = "FINNHUB_API_KEY"
    docs_url = "https://finnhub.io/register"
    free_tier = (
        "Finnhub's free plan allows 60 API calls a minute, and company news is "
        "limited to the last year for free accounts."
    )

    def call(self, *, url: str, config: dict[str, Any], limit: int, since: str) -> Any:
        symbol = str(config.get("symbol") or config.get("ticker") or "").strip().upper()
        if not symbol:
            return self.request_json(
                "https://finnhub.io/api/v1/news",
                params={
                    "category": str(config.get("category") or "general"),
                    "token": self.api_key,
                },
            )
        # Company news needs an explicit window, and an empty one returns an
        # empty list rather than an error, so a week is the floor.
        start = _yyyy_mm_dd(since) or str(config.get("from") or "")
        if not start:
            start = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
        return self.request_json(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": symbol,
                "from": start,
                "to": str(config.get("to") or _today()),
                "token": self.api_key,
            },
        )

    def to_items(self, payload: Any, config: dict[str, Any]) -> list[RawItem]:
        rows = payload if isinstance(payload, list) else (payload or {}).get("data") or []
        items: list[RawItem] = []
        for article in rows:
            if not isinstance(article, dict):
                continue
            link = str(article.get("url") or "")
            if not link:
                continue
            items.append(
                RawItem(
                    url=link,
                    title=str(article.get("headline") or ""),
                    summary=str(article.get("summary") or ""),
                    published_at=parse_datetime_iso(article.get("datetime")),
                    image_url=str(article.get("image") or ""),
                    meta={
                        "publisher": str(article.get("source") or ""),
                        "symbol": str(article.get("related") or ""),
                        "category": str(article.get("category") or ""),
                        "via": "finnhub",
                    },
                )
            )
        return items

    def notes(self, payload: Any) -> dict[str, Any]:
        return {"total_results": len(payload)} if isinstance(payload, list) else {}
