"""Central configuration, read entirely from the environment.

Nothing here raises at import time. A missing credential means the matching
provider or source reports itself unavailable, so the app boots and does useful
work with whatever subset of keys happens to be present, including none at all.
"""
from __future__ import annotations

import os
from pathlib import Path

from .runtime import current as runtime

BASE_DIR = Path(__file__).resolve().parent.parent
PRESETS_DIR = BASE_DIR / "app" / "presets"
MODELS_DIR = Path(os.environ.get("MODELS_DIR", "").strip() or (BASE_DIR / "Semantic Models"))

# The project's own .env fills gaps the real environment has not already
# covered. A hosted deployment ships no .env, so the platform's environment is
# the single source of truth there.
if os.environ.get("IGNORE_DOTENV", "").strip().lower() not in {"1", "true", "yes"}:
    try:
        from dotenv import load_dotenv

        _env_file = BASE_DIR / ".env"
        if _env_file.exists():
            load_dotenv(_env_file, override=False)
    except ImportError:  # python-dotenv is optional in a slim deployment
        pass


def env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def env_list(name: str, default: str = "") -> list[str]:
    raw = env(name, default=default) or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------- application

APP_NAME = env("APP_NAME", default="Newsroom")
APP_TAGLINE = env(
    "APP_TAGLINE",
    default="Your beat, deduplicated, clustered and summarised every morning",
)
ENVIRONMENT = env("ENVIRONMENT", default="production")
DEBUG = env_bool("DEBUG", False)
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", "VERCEL_URL", default="") or "").rstrip("/")
if PUBLIC_BASE_URL and not PUBLIC_BASE_URL.startswith("http"):
    PUBLIC_BASE_URL = f"https://{PUBLIC_BASE_URL}"

CORS_ORIGINS = env_list("CORS_ORIGINS")

# Shared secret for /api/cron/*. Vercel Cron sends it as a bearer token; an
# external scheduler must do the same. Without it those routes are open, which
# is fine locally and a problem in public, so the API says so loudly.
CRON_SECRET = env("CRON_SECRET")

# Optional gate on the whole API for a public deployment the operator does not
# want strangers spending their keys on.
APP_PASSWORD = env("APP_PASSWORD")

# Bring your own key. When on, a visitor pastes their own provider key in the
# UI and it rides their requests only, so a public deployment costs the
# operator nothing. Turn off for a private instance that must use server keys.
ALLOW_CLIENT_KEYS = env_bool("ALLOW_CLIENT_KEYS", True)

# ------------------------------------------------------------------- storage

DATABASE_URL = env("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "NEON_DATABASE_URL")
DATA_DIR = Path(env("DATA_DIR", default=str(runtime().writable_dir)))
SQLITE_PATH = Path(env("SQLITE_PATH", default=str(DATA_DIR / "newsroom.db")))

# ---------------------------------------------------------- LLM provider keys

GOOGLE_API_KEY = env("GOOGLE_API_KEY", "GEMINI_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", default="https://api.openai.com/v1")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")
MISTRAL_API_KEY = env("MISTRAL_API_KEY")
DEEPSEEK_API_KEY = env("DEEPSEEK_API_KEY")
TOGETHER_API_KEY = env("TOGETHER_API_KEY", "TOGETHERAI_API_KEY")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY")
CEREBRAS_API_KEY = env("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY = env("SAMBANOVA_API_KEY")
XAI_API_KEY = env("XAI_API_KEY", "GROK_API_KEY")
FIREWORKS_API_KEY = env("FIREWORKS_API_KEY")
PERPLEXITY_API_KEY = env("PERPLEXITY_API_KEY")
HUGGINGFACE_API_KEY = env(
    "HUGGINGFACE_API_KEY", "HUGGINGFACE_API_TOKEN", "HF_TOKEN", "HF_API_KEY"
)
HUGGINGFACE_BASE_URL = env(
    "HUGGINGFACE_BASE_URL", "HF_BASE_URL", default="https://router.huggingface.co/v1"
)

# Cloudflare Workers AI needs two values, because the account id sits in the
# URL path rather than a header and a token alone addresses nothing.
CLOUDFLARE_API_TOKEN = env("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY", "CF_API_TOKEN")
CLOUDFLARE_ACCOUNT_ID = env("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")

# OpenAI protocol servers running on the operator's own machine. These are only
# reachable from a server tier deployment.
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", "OLLAMA_HOST", default="http://localhost:11434")
LMSTUDIO_BASE_URL = env("LMSTUDIO_BASE_URL", default="http://localhost:1234/v1")
LLAMACPP_BASE_URL = env("LLAMACPP_BASE_URL", default="http://localhost:8080/v1")
VLLM_BASE_URL = env("VLLM_BASE_URL", default="http://localhost:8000/v1")
VLLM_API_KEY = env("VLLM_API_KEY", default="EMPTY")

# Default model overrides, one per provider.
MODEL_OVERRIDES = {
    "gemini": env("GEMINI_MODEL"),
    "groq": env("GROQ_MODEL"),
    "openai": env("OPENAI_MODEL"),
    "anthropic": env("ANTHROPIC_MODEL"),
    "mistral": env("MISTRAL_MODEL"),
    "deepseek": env("DEEPSEEK_MODEL"),
    "together": env("TOGETHER_MODEL"),
    "openrouter": env("OPENROUTER_MODEL"),
    "cerebras": env("CEREBRAS_MODEL"),
    "sambanova": env("SAMBANOVA_MODEL"),
    "xai": env("XAI_MODEL"),
    "fireworks": env("FIREWORKS_MODEL"),
    "perplexity": env("PERPLEXITY_MODEL"),
    "huggingface": env("HUGGINGFACE_MODEL", "HF_MODEL"),
    "cloudflare": env("CLOUDFLARE_MODEL", "CF_MODEL"),
    "ollama": env("OLLAMA_MODEL"),
    "lmstudio": env("LMSTUDIO_MODEL"),
    "llamacpp": env("LLAMACPP_MODEL"),
    "vllm": env("VLLM_MODEL"),
}

# Extra model ids, comma separated, appended to a provider's picker.
EXTRA_MODELS = {
    key: env(f"{key.upper()}_EXTRA_MODELS", default="") for key in MODEL_OVERRIDES
}

# --------------------------------------------------------------- embeddings

# Which embedding backend to prefer. "auto" walks the registry in order and
# takes the first that is usable, which on a bare deployment is the bundled
# ONNX model and therefore needs no key at all.
EMBEDDING_PROVIDER = env("EMBEDDING_PROVIDER", default="auto")
EMBEDDING_MODEL = env("EMBEDDING_MODEL")
EMBEDDING_DIMENSIONS = env_int("EMBEDDING_DIMENSIONS", 0)  # 0 means provider default
EMBEDDING_BATCH_SIZE = env_int("EMBEDDING_BATCH_SIZE", 64)

# The bundled ONNX sentence encoder. scripts/fetch_model.py puts it here.
LOCAL_EMBED_MODEL_DIR = Path(
    env("LOCAL_EMBED_MODEL_DIR", default=str(MODELS_DIR / "all-MiniLM-L6-v2-onnx"))
)
LOCAL_EMBED_ENABLED = env_bool("LOCAL_EMBED_ENABLED", True)

JINA_API_KEY = env("JINA_API_KEY")
VOYAGE_API_KEY = env("VOYAGE_API_KEY")
COHERE_API_KEY = env("COHERE_API_KEY")

# --------------------------------------------------------- news source keys

NEWSAPI_KEY = env("NEWSAPI_KEY", "NEWSAPI_ORG_KEY")
GNEWS_API_KEY = env("GNEWS_API_KEY")
NEWSDATA_API_KEY = env("NEWSDATA_API_KEY", "NEWSDATA_IO_KEY")
CURRENTS_API_KEY = env("CURRENTS_API_KEY")
MEDIASTACK_API_KEY = env("MEDIASTACK_API_KEY")
WORLDNEWS_API_KEY = env("WORLDNEWS_API_KEY")
THENEWSAPI_KEY = env("THENEWSAPI_KEY")
MARKETAUX_API_KEY = env("MARKETAUX_API_KEY")
FINNHUB_API_KEY = env("FINNHUB_API_KEY")
ALPHAVANTAGE_API_KEY = env("ALPHAVANTAGE_API_KEY")
GUARDIAN_API_KEY = env("GUARDIAN_API_KEY")
NYT_API_KEY = env("NYT_API_KEY")
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY")

# Reddit stopped serving its .json endpoints to unauthenticated clients in
# 2026, so a script-type OAuth app is now the only way in.
REDDIT_CLIENT_ID = env("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = env("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = env(
    "REDDIT_USER_AGENT", default="python:newsroom-aggregator:v1.0 (by /u/unknown)"
)

# Web search backends, used to find stories that no configured feed carried.
SERPAPI_KEY = env("SERPAPI_KEY", "SERP_API_KEY")
BRAVE_API_KEY = env("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY")
TAVILY_API_KEY = env("TAVILY_API_KEY")
EXA_API_KEY = env("EXA_API_KEY", "METAPHOR_API_KEY")
SEARXNG_BASE_URL = env("SEARXNG_BASE_URL")

# ----------------------------------------------------------------- ingestion

USER_AGENT = env(
    "USER_AGENT",
    default=(
        "NewsroomBot/1.0 (+https://github.com/; personal news digest; "
        "contact via repository issues)"
    ),
)
RESPECT_ROBOTS = env_bool("RESPECT_ROBOTS", True)
FETCH_TIMEOUT = env_int("FETCH_TIMEOUT", 20)
FETCH_CONCURRENCY = env_int("FETCH_CONCURRENCY", 8)
PER_HOST_DELAY = env_float("PER_HOST_DELAY", 1.0)
MAX_ARTICLES_PER_SOURCE = env_int("MAX_ARTICLES_PER_SOURCE", 60)
MAX_ARTICLE_BYTES = env_int("MAX_ARTICLE_BYTES", 2_000_000)
LOOKBACK_HOURS = env_int("LOOKBACK_HOURS", 24)
EXTRACT_FULLTEXT = env_bool("EXTRACT_FULLTEXT", True)

# ------------------------------------------------------------------- pipeline

# Cosine above this and two articles are the same story rather than two stories
# about the same topic. Tuned against the eval set in app/ops/eval.py.
DUPLICATE_THRESHOLD = env_float("DUPLICATE_THRESHOLD", 0.86)
# The looser threshold that groups distinct articles into one running event.
CLUSTER_THRESHOLD = env_float("CLUSTER_THRESHOLD", 0.72)
# Hamming distance between 64 bit simhashes below which text is near identical,
# which catches syndicated wire copy without spending an embedding call.
SIMHASH_DISTANCE = env_int("SIMHASH_DISTANCE", 3)
DEDUP_WINDOW_HOURS = env_int("DEDUP_WINDOW_HOURS", 72)

DIGEST_BULLETS = env_int("DIGEST_BULLETS", 5)
DIGEST_STORIES_PER_THEME = env_int("DIGEST_STORIES_PER_THEME", 4)
SUMMARY_TEMPERATURE = env_float("SUMMARY_TEMPERATURE", 0.2)

# -------------------------------------------------------------------- delivery

# Automatic delivery after a scheduled run. Off by default and deliberately so:
# a digest that mails itself the moment someone clones the repo is a surprise,
# and the useful case is a real deployment on a real schedule. A workspace can
# override the channels and the recipient in its own config.
AUTO_DELIVER = env_bool("AUTO_DELIVER", False)
AUTO_DELIVER_CHANNELS = env_list("AUTO_DELIVER_CHANNELS", "email")
AUTO_DELIVER_TO = env("AUTO_DELIVER_TO", "SMTP_TO", default="")

SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_USER = env("SMTP_USER", "SMTP_USERNAME")
SMTP_PASSWORD = env("SMTP_PASSWORD", "SMTP_PASS")
SMTP_FROM = env("SMTP_FROM", "MAIL_FROM")
SMTP_STARTTLS = env_bool("SMTP_STARTTLS", True)
RESEND_API_KEY = env("RESEND_API_KEY")
SLACK_WEBHOOK_URL = env("SLACK_WEBHOOK_URL")
DISCORD_WEBHOOK_URL = env("DISCORD_WEBHOOK_URL")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

# ------------------------------------------------------------------ behaviour

# Insert a couple of preset workspaces on an empty database. Without this a
# fresh deployment looks broken rather than new.
DEMO_SEED = env_bool("DEMO_SEED", True)
DEFAULT_PRESET = env("DEFAULT_PRESET", default="india-markets")
DEFAULT_TIMEZONE = env("DEFAULT_TIMEZONE", default="Asia/Kolkata")
