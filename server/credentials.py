"""Every non-LLM credential the app can use, described for the UI.

The bring your own key mechanism was built for all of these from the start:
`llm.keyring` carries a slug per credential, and every source adapter already
prefers `keyring.key_for(slug)` over the server's own environment. What was
missing is this list. Nothing told the browser these slugs existed, so the
settings page could only offer LLM and embedding providers and a visitor had no
way to supply a SerpAPI or NewsAPI key even though the backend would have used
it.

The slug is the contract. A browser sends `X-LLM-Key-<slug>` and the adapter
that owns that slug picks it up for that one request, exactly as an LLM
provider key does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import settings
from .llm import keyring


@dataclass(frozen=True)
class Credential:
    slug: str
    label: str
    env_names: tuple[str, ...]
    docs_url: str
    # What switches on when this is present, in the user's terms.
    enables: str
    group: str
    # A second value some providers need alongside the key.
    partner_slug: str = ""
    partner_label: str = ""
    free_tier: str = ""

    def status(self) -> dict[str, Any]:
        from_client = bool(keyring.key_for(self.slug))
        from_server = any(getattr(settings, name, None) for name in self.env_names)
        return {
            "slug": self.slug,
            "label": self.label,
            "env_names": list(self.env_names),
            "docs_url": self.docs_url,
            "enables": self.enables,
            "group": self.group,
            "partner_slug": self.partner_slug,
            "partner_label": self.partner_label,
            "free_tier": self.free_tier,
            "available": from_client or from_server,
            "key_source": "client" if from_client else ("server" if from_server else ""),
        }


CREDENTIALS: tuple[Credential, ...] = (
    # Web search. These are what turn a topic search into full text, because
    # they return direct publisher links rather than a redirector.
    Credential(
        "serpapi", "SerpAPI", ("SERPAPI_KEY",),
        "https://serpapi.com/manage-api-key",
        "Web search in Research and in a search source.",
        "search", free_tier="100 searches a month",
    ),
    Credential(
        "brave", "Brave Search", ("BRAVE_API_KEY",),
        "https://api-dashboard.search.brave.com/app/keys",
        "Web search in Research and in a search source.",
        "search", free_tier="2,000 queries a month",
    ),
    Credential(
        "tavily", "Tavily", ("TAVILY_API_KEY",),
        "https://app.tavily.com/home",
        "Web search in Research and in a search source.",
        "search", free_tier="1,000 credits a month",
    ),
    Credential(
        "exa", "Exa", ("EXA_API_KEY",),
        "https://dashboard.exa.ai/api-keys",
        "Web search in Research and in a search source.",
        "search",
    ),
    # News APIs.
    Credential(
        "newsapi", "NewsAPI.org", ("NEWSAPI_KEY",),
        "https://newsapi.org/register",
        "The newsapi source kind.",
        "news", free_tier="100 requests a day, 24 hour delay, development use only",
    ),
    Credential(
        "gnews", "GNews", ("GNEWS_API_KEY",),
        "https://gnews.io/dashboard",
        "The gnews source kind.",
        "news", free_tier="100 requests a day, 10 articles each",
    ),
    Credential(
        "newsdata", "NewsData.io", ("NEWSDATA_API_KEY",),
        "https://newsdata.io/api-key",
        "The newsdata source kind.",
        "news", free_tier="200 credits a day, 10 articles each",
    ),
    Credential(
        "currents", "Currents API", ("CURRENTS_API_KEY",),
        "https://currentsapi.services/en/register",
        "The currents source kind.",
        "news", free_tier="250 requests a day",
    ),
    Credential(
        "mediastack", "Mediastack", ("MEDIASTACK_API_KEY",),
        "https://mediastack.com/signup/free",
        "The mediastack source kind.",
        "news", free_tier="100 requests a month, non commercial",
    ),
    Credential(
        "guardian", "The Guardian", ("GUARDIAN_API_KEY",),
        "https://open-platform.theguardian.com/access/",
        "The guardian source kind.",
        "news", free_tier="Free for non commercial use",
    ),
    Credential(
        "nyt", "New York Times", ("NYT_API_KEY",),
        "https://developer.nytimes.com/get-started",
        "The nyt source kind.",
        "news",
    ),
    Credential(
        "marketaux", "Marketaux", ("MARKETAUX_API_KEY",),
        "https://www.marketaux.com/account/dashboard",
        "Company and ticker news.",
        "news",
    ),
    Credential(
        "finnhub", "Finnhub", ("FINNHUB_API_KEY",),
        "https://finnhub.io/dashboard",
        "Company news by ticker.",
        "news",
    ),
    # Social and video.
    Credential(
        "reddit", "Reddit", ("REDDIT_CLIENT_ID",),
        "https://www.reddit.com/prefs/apps",
        "The reddit source kind. Reddit removed anonymous access in 2026, so "
        "this is now required rather than optional.",
        "social",
        partner_slug="reddit_secret", partner_label="Client secret",
    ),
    Credential(
        "youtube", "YouTube Data API", ("YOUTUBE_API_KEY",),
        "https://console.cloud.google.com/apis/credentials",
        "Resolving a channel name to its feed. Channel ids and handles work "
        "without it.",
        "social",
    ),
    # Embedding providers that are not also LLM providers, so they do not
    # appear in the model picker.
    Credential(
        "jina", "Jina AI", ("JINA_API_KEY",),
        "https://jina.ai/api-dashboard/key-manager",
        "The jina_embed backend.",
        "embedding", free_tier="10 million tokens",
    ),
    Credential(
        "voyage", "Voyage AI", ("VOYAGE_API_KEY",),
        "https://dashboard.voyageai.com/api-keys",
        "The voyage_embed backend.",
        "embedding", free_tier="200 million tokens",
    ),
    Credential(
        "cohere", "Cohere", ("COHERE_API_KEY",),
        "https://dashboard.cohere.com/api-keys",
        "The cohere_embed backend.",
        "embedding", free_tier="1,000 calls a month on a trial key",
    ),
)

GROUP_LABELS = {
    "search": "Web search",
    "news": "News APIs",
    "social": "Social and video",
    "embedding": "Embedding only providers",
}


def all_status() -> list[dict[str, Any]]:
    return [credential.status() for credential in CREDENTIALS]


def by_slug(slug: str) -> Credential | None:
    for credential in CREDENTIALS:
        if credential.slug == slug:
            return credential
    return None
