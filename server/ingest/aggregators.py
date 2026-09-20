"""Sources that aggregate other people's publishing: Google News, Hacker News,
Reddit and YouTube.

They are grouped in one module because they share a shape rather than a
protocol: none of them is the publisher of the story, each has its own idea of
what a link is, and each needs a small amount of knowledge about the service
that would be noise anywhere else. Google News hands back a redirector rather
than the article, Hacker News charges one request per story, Reddit now
requires OAuth, and YouTube's per channel feed is plain Atom once the channel
id has been found.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote_plus, urlsplit

from .. import settings
from ..llm import keyring
from ..util import parse_datetime_iso, strip_html, truncate
from .base import (
    FetchOutcome,
    RawItem,
    SourceAdapter,
    SourceError,
    dedupe_items,
    register,
    within_window,
)
from .rss import FEED_ACCEPT, feed_title, items_from_feed, parse_feed

# --------------------------------------------------------------- Google News

GOOGLE_NEWS_HOST = "https://news.google.com"

# The section names Google exposes as ready made topic feeds.
GOOGLE_TOPICS = frozenset(
    {
        "WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SPORTS",
        "SCIENCE", "HEALTH",
    }
)


@register
class GoogleNewsAdapter(SourceAdapter):
    kind = "googlenews"
    label = "Google News"
    urlless = True
    url_hint = "Leave blank and set `query` in the config, or paste a Google News RSS URL"

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
        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        feed_url = self._feed_url(url, config)

        response = self.client.get(
            feed_url,
            etag=etag,
            last_modified=last_modified,
            accept=FEED_ACCEPT,
            # Google publishes this endpoint as a feed interface, and its
            # robots.txt is written for the crawlable web UI rather than for
            # /rss, so the feed is fetched the way the service intends.
            allow_robots_override=True,
        )
        if response.unchanged:
            return FetchOutcome(
                items=[], status="unchanged", etag=etag, last_modified=last_modified
            )
        if not response.ok:
            raise SourceError(
                f"Google News returned HTTP {response.status}.",
                retryable=response.status in (429, 500, 502, 503),
                hint="Check the query syntax, or that the language and country pair exists.",
            )

        parsed = parse_feed(response.content or response.text)
        items: list[RawItem] = []
        for entry in getattr(parsed, "entries", []) or []:
            item = self._entry_item(entry, feed_url)
            if item is not None:
                items.append(item)

        items = dedupe_items(within_window(items, since))[:cap]
        return FetchOutcome(
            items=items,
            status="ok",
            etag=str(response.headers.get("etag", "") or ""),
            last_modified=str(response.headers.get("last-modified", "") or ""),
            notes={"feed_url": feed_url, "feed_title": feed_title(parsed)},
        )

    def _feed_url(self, url: str, config: dict[str, Any]) -> str:
        if url and "news.google.com" in url:
            return url

        language = str(config.get("language") or config.get("hl") or "en-IN").strip()
        country = str(config.get("country") or config.get("gl") or "IN").strip().upper()
        # ceid pairs the country with the bare language code, which is the only
        # combination Google accepts.
        ceid = f"{country}:{language.split('-')[0].lower()}"
        suffix = f"hl={quote_plus(language)}&gl={quote_plus(country)}&ceid={quote_plus(ceid)}"

        query = str(config.get("query") or config.get("q") or "").strip()
        if query:
            return f"{GOOGLE_NEWS_HOST}/rss/search?q={quote_plus(query)}&{suffix}"

        topic = str(config.get("topic") or "").strip()
        if topic:
            if topic.upper() in GOOGLE_TOPICS:
                return (
                    f"{GOOGLE_NEWS_HOST}/rss/headlines/section/topic/"
                    f"{topic.upper()}?{suffix}"
                )
            # Anything else is one of Google's opaque topic tokens, which live
            # under a different path.
            return f"{GOOGLE_NEWS_HOST}/rss/topics/{quote_plus(topic)}?{suffix}"

        return f"{GOOGLE_NEWS_HOST}/rss?{suffix}"

    def _entry_item(self, entry: Any, feed_url: str) -> RawItem | None:
        link = str(entry.get("link") or "").strip()
        if not link:
            return None

        publisher = ""
        source = entry.get("source")
        if source is not None and hasattr(source, "get"):
            publisher = strip_html(str(source.get("title") or "")).strip()
        if not publisher:
            publisher = strip_html(str(entry.get("source_title") or "")).strip()

        title = strip_html(str(entry.get("title") or "")).strip()
        # Google appends " - Publisher" to every headline, which would
        # otherwise weaken the similarity comparison the pipeline runs on it.
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -(len(publisher) + 3)].strip()

        published = ""
        for field in ("published_parsed", "updated_parsed"):
            value = entry.get(field)
            if value:
                published = parse_datetime_iso(tuple(value))
                if published:
                    break
        if not published:
            published = parse_datetime_iso(entry.get("published") or "")

        return RawItem(
            url=link,
            title=title,
            summary=str(entry.get("summary") or ""),
            published_at=published,
            meta={
                # The link is a Google redirector, so the real publisher is
                # recorded here: the URL's domain says nothing but "google".
                "publisher": publisher,
                "via": "google-news",
                "feed_url": feed_url,
            },
        )


# --------------------------------------------------------------- Hacker News

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
HN_LISTS = {"top": "topstories", "new": "newstories", "best": "beststories"}
# One HTTP request per story, so the default is a number that finishes inside a
# serverless invocation rather than the usual per source cap.
HN_DEFAULT_LIMIT = 40


@register
class HackerNewsAdapter(SourceAdapter):
    kind = "hackernews"
    label = "Hacker News"
    urlless = True
    url_hint = "Leave blank. Set `feed` to top, new or best in the config"

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
        config = config or {}
        feed = str(config.get("feed") or config.get("list") or "top").strip().lower()
        list_name = HN_LISTS.get(feed, HN_LISTS["top"])
        cap = limit or int(config.get("limit") or 0) or HN_DEFAULT_LIMIT
        cap = max(1, min(cap, 100))
        min_score = int(config.get("min_score") or 0)

        ids = self.client.get_json(f"{HN_API}/{list_name}.json")
        if not isinstance(ids, list):
            raise SourceError("Hacker News returned an unexpected story list.")

        items: list[RawItem] = []
        for story_id in ids[:cap]:
            try:
                story = self.client.get_json(f"{HN_API}/item/{int(story_id)}.json")
            except (SourceError, TypeError, ValueError):
                # A dead or deleted id should cost one story, not the run.
                continue
            item = self._story_item(story, min_score)
            if item is not None:
                items.append(item)

        items = dedupe_items(within_window(items, since))
        return FetchOutcome(
            items=items,
            status="ok",
            notes={"list": list_name, "ids_seen": len(ids)},
        )

    def _story_item(self, story: Any, min_score: int) -> RawItem | None:
        if not isinstance(story, dict) or story.get("type") != "story":
            return None
        if story.get("dead") or story.get("deleted"):
            return None
        score = int(story.get("score") or 0)
        if min_score and score < min_score:
            return None

        story_id = story.get("id")
        permalink = HN_ITEM_URL.format(id=story_id)
        # An Ask HN or a text post has no external link, and the discussion is
        # the story, so the permalink stands in as the URL.
        link = str(story.get("url") or "").strip() or permalink

        return RawItem(
            url=link,
            title=str(story.get("title") or ""),
            summary=strip_html(str(story.get("text") or "")),
            author=str(story.get("by") or ""),
            published_at=parse_datetime_iso(story.get("time")),
            meta={
                "score": score,
                "descendants": int(story.get("descendants") or 0),
                "hn_id": story_id,
                "comments_url": permalink,
                "via": "hacker-news",
            },
        )


# -------------------------------------------------------------------- Reddit

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
REDDIT_SORTS = ("hot", "new", "top", "rising", "controversial")

# Tokens live an hour. Kept in the kv table when there is a database, because a
# serverless deployment starts a fresh process per invocation and would
# otherwise spend one token request per run against a rate limited endpoint.
_TOKEN_TTL_SLACK = 60
_memory_tokens: dict[str, tuple[str, float]] = {}


def _reddit_credentials() -> tuple[str, str]:
    client_id = keyring.key_for("reddit") or settings.REDDIT_CLIENT_ID or ""
    client_secret = keyring.key_for("reddit_secret") or settings.REDDIT_CLIENT_SECRET or ""
    return client_id.strip(), client_secret.strip()


def _kv_get(key: str) -> str:
    """Read the token cache from the database when the repo layer exists."""
    try:
        from ..db import repo  # noqa: PLC0415 - optional at this layer
    except Exception:  # noqa: BLE001 - no database configured is normal
        return ""
    for name in ("kv_get", "get_kv", "kv_read"):
        fn = getattr(repo, name, None)
        if callable(fn):
            try:
                value = fn(key)
            except Exception:  # noqa: BLE001
                return ""
            return str(value or "")
    return ""


def _kv_set(key: str, value: str, ttl_seconds: int) -> bool:
    try:
        from ..db import repo  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    for name in ("kv_set", "set_kv", "kv_put"):
        fn = getattr(repo, name, None)
        if not callable(fn):
            continue
        try:
            fn(key, value, ttl_seconds)
            return True
        except TypeError:
            # The repo may spell the expiry differently, so the plain two
            # argument form is tried before giving up on the database.
            try:
                fn(key, value)
                return True
            except Exception:  # noqa: BLE001
                return False
        except Exception:  # noqa: BLE001
            return False
    return False


@register
class RedditAdapter(SourceAdapter):
    kind = "reddit"
    label = "Reddit"
    urlless = True
    url_hint = "Leave blank and set `subreddit`, or paste https://www.reddit.com/r/worldnews"
    key_names = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")

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
        config = config or {}
        client_id, client_secret = _reddit_credentials()
        if not (client_id and client_secret):
            return FetchOutcome(
                items=[],
                status="skipped",
                error=(
                    "Reddit now requires OAuth: the unauthenticated .json "
                    "endpoints return 403. Create a script app at "
                    "https://www.reddit.com/prefs/apps and set REDDIT_CLIENT_ID "
                    "and REDDIT_CLIENT_SECRET, or paste them in Settings."
                ),
            )

        subreddit = self._subreddit(url, config)
        sort = str(config.get("sort") or "hot").strip().lower()
        if sort not in REDDIT_SORTS:
            sort = "hot"
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        cap = max(1, min(cap, 100))

        params: dict[str, Any] = {"limit": cap, "raw_json": 1}
        if sort in ("top", "controversial"):
            params["t"] = str(config.get("time") or "day")

        token = self._token(client_id, client_secret)
        payload = self.client.get_json(
            f"{REDDIT_API}/r/{subreddit}/{sort}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                # Reddit rejects a generic agent outright, so its own format is
                # used rather than the app wide one.
                "User-Agent": settings.REDDIT_USER_AGENT,
            },
        )

        children = ((payload or {}).get("data") or {}).get("children") or []
        items: list[RawItem] = []
        for child in children:
            item = self._post_item(child.get("data") or {}, config)
            if item is not None:
                items.append(item)

        items = dedupe_items(within_window(items, since))[:cap]
        return FetchOutcome(
            items=items,
            status="ok",
            notes={"subreddit": subreddit, "sort": sort},
        )

    def _subreddit(self, url: str, config: dict[str, Any]) -> str:
        name = str(config.get("subreddit") or config.get("sub") or "").strip()
        if not name and url:
            path = urlsplit(url).path if "://" in url else url
            parts = [part for part in path.split("/") if part]
            if "r" in parts:
                index = parts.index("r")
                if index + 1 < len(parts):
                    name = parts[index + 1]
            elif parts:
                name = parts[0]
        name = name.strip().strip("/").removeprefix("r/")
        if not name:
            raise SourceError(
                "A Reddit source needs a subreddit.",
                retryable=False,
                hint="Set `subreddit` in the config, for example worldnews.",
            )
        return name

    def _token(self, client_id: str, client_secret: str) -> str:
        cache_key = f"reddit_token:{keyring.fingerprint(client_id)}"

        cached = _kv_get(cache_key)
        if cached:
            return cached
        held = _memory_tokens.get(cache_key)
        if held and held[1] > time.time():
            return held[0]

        # The shared client is GET only and this is a form POST, so requests is
        # used directly here. Nothing is lost: an OAuth token endpoint is not
        # subject to robots.txt, and one call per hour needs no rate limiting.
        import requests  # noqa: PLC0415

        try:
            response = requests.post(
                REDDIT_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
                headers={"User-Agent": settings.REDDIT_USER_AGENT},
                timeout=float(settings.FETCH_TIMEOUT),
            )
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"Could not reach Reddit's token endpoint: {exc}") from exc

        if response.status_code in (401, 403):
            raise SourceError(
                "Reddit rejected the client credentials.",
                retryable=False,
                hint=(
                    "The app must be of type 'script' and the secret must be "
                    "the one shown under the app name, not the app id."
                ),
            )
        if response.status_code == 429:
            raise SourceError(
                "Reddit rate limited the token request.",
                hint="Free OAuth apps get 100 queries per minute per client id.",
            )
        if response.status_code >= 400:
            raise SourceError(
                f"Reddit's token endpoint returned HTTP {response.status_code}.",
                hint=response.text[:200],
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("Reddit's token response was not JSON.") from exc

        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise SourceError(
                "Reddit returned no access token.",
                retryable=False,
                hint=str(payload.get("error") or "")[:200],
            )

        ttl = max(60, int(payload.get("expires_in") or 3600) - _TOKEN_TTL_SLACK)
        if not _kv_set(cache_key, token, ttl):
            _memory_tokens[cache_key] = (token, time.time() + ttl)
        return token

    def _post_item(self, post: dict[str, Any], config: dict[str, Any]) -> RawItem | None:
        if not post:
            return None
        # A pinned moderator post is rules and announcements, not a story.
        if post.get("stickied") and not config.get("include_stickied"):
            return None
        if post.get("over_18") and not config.get("include_nsfw"):
            return None

        permalink = str(post.get("permalink") or "")
        discussion = f"https://www.reddit.com{permalink}" if permalink else ""
        link = str(post.get("url_overridden_by_dest") or post.get("url") or "").strip()
        if not link or link.startswith("/r/"):
            link = discussion
        if not link:
            return None

        image = ""
        thumbnail = str(post.get("thumbnail") or "")
        if thumbnail.startswith("http"):
            image = thumbnail
        preview = post.get("preview") or {}
        images = preview.get("images") or [] if isinstance(preview, dict) else []
        if images and isinstance(images[0], dict):
            source = images[0].get("source") or {}
            if source.get("url"):
                image = str(source["url"])

        return RawItem(
            url=link,
            title=str(post.get("title") or ""),
            summary=truncate(strip_html(str(post.get("selftext") or "")), 2000),
            author=str(post.get("author") or ""),
            published_at=parse_datetime_iso(post.get("created_utc")),
            image_url=image,
            meta={
                "score": int(post.get("score") or 0),
                "num_comments": int(post.get("num_comments") or 0),
                "subreddit": str(post.get("subreddit") or ""),
                "flair": str(post.get("link_flair_text") or ""),
                "comments_url": discussion,
                "upvote_ratio": post.get("upvote_ratio"),
                "via": "reddit",
            },
        )


# ------------------------------------------------------------------- YouTube

YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml"
_CHANNEL_ID_MARKERS = (
    r'"channelId"\s*:\s*"(UC[0-9A-Za-z_-]{20,})"',
    r'"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{20,})"',
    r'channel/(UC[0-9A-Za-z_-]{20,})',
)


@register
class YouTubeAdapter(SourceAdapter):
    kind = "youtube"
    label = "YouTube channel"
    url_hint = "A channel URL, an @handle, or a playlist URL. No API key needed"

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
        config = config or {}
        cap = limit or int(config.get("limit") or 0) or settings.MAX_ARTICLES_PER_SOURCE
        feed_url = self._feed_url(url, config)

        response = self.client.get(
            feed_url,
            etag=etag,
            last_modified=last_modified,
            accept=FEED_ACCEPT,
            # YouTube's robots.txt governs the watch pages, and this endpoint
            # is the feed the service publishes for exactly this purpose.
            allow_robots_override=True,
        )
        if response.unchanged:
            return FetchOutcome(
                items=[], status="unchanged", etag=etag, last_modified=last_modified
            )
        if not response.ok:
            raise SourceError(
                f"YouTube returned HTTP {response.status} for that feed.",
                retryable=response.status in (429, 500, 502, 503),
                hint="A channel id looks like UC followed by 22 characters.",
            )

        parsed = parse_feed(response.content or response.text)
        channel = feed_title(parsed)
        items = items_from_feed(
            parsed,
            feed_url,
            extra_meta={"channel": channel, "via": "youtube"},
        )
        for item in items:
            video_id = str(item.meta.get("guid") or "").rsplit(":", 1)[-1]
            if video_id:
                item.meta["video_id"] = video_id
        items = dedupe_items(within_window(items, since))[:cap]

        return FetchOutcome(
            items=items,
            status="ok",
            etag=str(response.headers.get("etag", "") or ""),
            last_modified=str(response.headers.get("last-modified", "") or ""),
            notes={"channel": channel, "feed_url": feed_url},
        )

    def _feed_url(self, url: str, config: dict[str, Any]) -> str:
        channel_id = str(config.get("channel_id") or config.get("channel") or "").strip()
        playlist_id = str(config.get("playlist_id") or config.get("playlist") or "").strip()
        user = str(config.get("user") or config.get("handle") or "").strip()

        if playlist_id:
            return f"{YOUTUBE_FEED}?playlist_id={quote_plus(playlist_id)}"
        if channel_id.startswith("UC"):
            return f"{YOUTUBE_FEED}?channel_id={quote_plus(channel_id)}"
        if user:
            return f"{YOUTUBE_FEED}?user={quote_plus(user.lstrip('@'))}"

        target = url.strip()
        if not target:
            raise SourceError(
                "A YouTube source needs a channel, user or playlist.",
                retryable=False,
                hint="Paste the channel URL, or set channel_id in the config.",
            )
        if "feeds/videos.xml" in target:
            return target

        parts = urlsplit(target)
        segments = [segment for segment in (parts.path or "").split("/") if segment]
        query = dict(
            pair.split("=", 1) for pair in (parts.query or "").split("&") if "=" in pair
        )
        if query.get("list"):
            return f"{YOUTUBE_FEED}?playlist_id={quote_plus(query['list'])}"
        if segments and segments[0] == "channel" and len(segments) > 1:
            return f"{YOUTUBE_FEED}?channel_id={quote_plus(segments[1])}"
        if segments and segments[0] == "user" and len(segments) > 1:
            return f"{YOUTUBE_FEED}?user={quote_plus(segments[1])}"

        # An @handle or a vanity /c/ URL carries no id, so the channel page is
        # read once to find the one YouTube's feed endpoint accepts.
        return f"{YOUTUBE_FEED}?channel_id={quote_plus(self._channel_id_from_page(target))}"

    def _channel_id_from_page(self, url: str) -> str:
        import re  # noqa: PLC0415 - only this path needs it

        response = self.client.get(url, allow_robots_override=True)
        if not response.ok:
            raise SourceError(
                f"Could not read that YouTube page (HTTP {response.status}).",
                hint="Set channel_id in the config instead.",
            )
        for pattern in _CHANNEL_ID_MARKERS:
            match = re.search(pattern, response.text)
            if match:
                return match.group(1)
        raise SourceError(
            "No channel id was found on that page.",
            retryable=False,
            hint=(
                "Open the channel, click any video, and use the /channel/UC... "
                "URL, or set channel_id in the config."
            ),
        )
