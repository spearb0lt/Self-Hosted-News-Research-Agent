"""Turning a cluster of articles into something worth reading.

Two prompts do the work. One writes a short factual account of a single event
from the coverage of it. The other writes the digest's theme level bullets from
those accounts.

Both are told, repeatedly and specifically, that they may only use what is in
the supplied text. Summarisation is where a news tool most easily becomes a
liability: a model asked to summarise five partial reports will happily invent
the connective detail that makes them read as one coherent story. The prompts,
the low temperature, and the citation requirement all exist to make that
harder, and the digest always carries the source links so a reader can check.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..llm import LLMError
from ..llm import registry as llm
from ..util import truncate

EVENT_SYSTEM = (
    "You write neutral, factual news summaries for a professional daily digest.\n"
    "\n"
    "Absolute rules:\n"
    "1. Use only what the supplied articles say. Never add a fact, figure, name, "
    "date or quote that is not present in them.\n"
    "2. When the articles disagree, say so plainly rather than choosing a side.\n"
    "3. When something important is not stated, write that it is not stated. Do "
    "not fill the gap.\n"
    "4. No opinion, no speculation about consequences, no editorialising.\n"
    "5. Write plainly. No marketing language, no rhetorical questions."
)

EVENT_SCHEMA = (
    '{"title": "a factual headline under 100 characters",'
    ' "summary": "exactly two sentences",'
    ' "why_it_matters": "one sentence, or an empty string when the articles do '
    'not support one",'
    ' "disagreement": "one sentence describing any conflict between sources, or '
    'an empty string"}'
)

DIGEST_SYSTEM = (
    "You write the daily bullet digest for a professional news brief. You are "
    "given the day's events, already grouped and summarised. Select and "
    "compress. Use only the supplied material, never outside knowledge.\n"
    "Each bullet is one sentence, states what happened, and names the actor. "
    "Lead with the most consequential. No preamble, no sign off."
)

DIGEST_SCHEMA = (
    '{"items": [{"event_id": 1, "bullet": "one sentence"}]}'
)

# How much of each article goes into the prompt. Enough for the lead and a
# couple of supporting paragraphs, which is where the reportable facts live.
PER_ARTICLE_CHARS = 900
MAX_ARTICLES_PER_EVENT = 6


def _event_prompt(articles: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, article in enumerate(articles[:MAX_ARTICLES_PER_EVENT], start=1):
        body = article.get("content") or article.get("summary") or ""
        lines.append(f"--- Source {index}: {article.get('domain') or 'unknown'} ---")
        lines.append(f"Headline: {article.get('title') or ''}")
        if published := article.get("published_at"):
            lines.append(f"Published: {published}")
        lines.append(truncate(body, PER_ARTICLE_CHARS))
        lines.append("")
    return "\n".join(lines)


def summarise_event(
    articles: Sequence[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> dict[str, str]:
    """Write one event's summary from its coverage.

    Falls back to the best available article's own text rather than failing the
    run. A digest whose entries are publisher summaries is still useful; a run
    that dies because one provider rate limited is not.
    """
    if not articles:
        return {"title": "", "summary": "", "why_it_matters": "", "disagreement": ""}

    fallback_title = articles[0].get("title") or ""
    fallback_summary = truncate(
        articles[0].get("summary") or articles[0].get("content") or "", 320
    )

    try:
        result = llm.generate_json(
            _event_prompt(articles),
            provider=provider,
            model=model,
            system=EVENT_SYSTEM,
            schema_hint=EVENT_SCHEMA,
            temperature=temperature,
            max_tokens=700,
            operation="summarise_event",
        )
    except LLMError as exc:
        # A rate limit is temporary, so it is raised rather than absorbed. The
        # caller pauses the run and the next tick finishes the remaining events
        # with real summaries. Quietly falling back here was worse than it
        # looked: a free tier that allows twenty calls produced a digest where
        # two thirds of the entries were publisher marketing copy, and the run
        # still reported success.
        if exc.retryable:
            raise
        return {
            "title": fallback_title,
            "summary": fallback_summary,
            "why_it_matters": "",
            "disagreement": "",
            "source": "fallback",
        }
    except ValueError:
        return {
            "title": fallback_title,
            "summary": fallback_summary,
            "why_it_matters": "",
            "disagreement": "",
            "source": "fallback",
        }

    if not isinstance(result, dict):
        return {
            "title": fallback_title,
            "summary": fallback_summary,
            "why_it_matters": "",
            "disagreement": "",
            "source": "fallback",
        }

    return {
        "title": truncate(str(result.get("title") or fallback_title), 200),
        "summary": truncate(str(result.get("summary") or fallback_summary), 700),
        "why_it_matters": truncate(str(result.get("why_it_matters") or ""), 300),
        "disagreement": truncate(str(result.get("disagreement") or ""), 300),
        "source": "model",
    }


# At most this many bullets may come from any one outlet. A prolific publisher
# otherwise takes the whole digest: ranking by importance alone, a site posting
# thirty stories a day wins on recency every time, and the reader gets that
# site's front page rather than their beat.
MAX_BULLETS_PER_DOMAIN = 2


def _domain_of(event: dict[str, Any]) -> str:
    articles = event.get("articles") or []
    return (articles[0].get("domain") or "") if articles else ""


def write_bullets(
    events: Sequence[dict[str, Any]],
    *,
    count: int = 5,
    provider: str | None = None,
    model: str | None = None,
    theme_label: str = "",
) -> list[dict[str, Any]]:
    """Pick and phrase the headline bullets for a theme or a whole digest."""
    if not events:
        return []

    ranked = sorted(events, key=lambda e: -float(e.get("importance") or 0))
    # The shortlist the model chooses from is itself diversified, so a single
    # outlet cannot crowd out the alternatives before the model ever sees them.
    shortlist: list[dict[str, Any]] = []
    per_domain: dict[str, int] = {}
    overflow: list[dict[str, Any]] = []
    for event in ranked:
        domain = _domain_of(event)
        if domain and per_domain.get(domain, 0) >= MAX_BULLETS_PER_DOMAIN:
            overflow.append(event)
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        shortlist.append(event)
    target = max(count * 2, count)
    shortlist = (shortlist + overflow)[:target]

    lines = []
    if theme_label:
        lines.append(f"Section: {theme_label}")
    lines.append(f"Write the {count} most important bullets. Events:")
    for event in shortlist:
        lines.append(
            f"[{event['id']}] {event.get('title') or ''}: "
            f"{truncate(event.get('summary') or '', 320)}"
        )
    prompt = "\n".join(lines)

    try:
        rows = llm.generate_json(
            prompt,
            provider=provider,
            model=model,
            system=DIGEST_SYSTEM,
            schema_hint=DIGEST_SCHEMA,
            expect_list=True,
            temperature=0.2,
            max_tokens=1200,
            operation="digest_bullets",
        )
    except (LLMError, ValueError):
        rows = []

    by_id = {int(e["id"]): e for e in shortlist}
    bullets: list[dict[str, Any]] = []
    seen: set[int] = set()
    used_domains: dict[str, int] = {}

    def accept(event: dict[str, Any], text: str) -> bool:
        domain = _domain_of(event)
        if domain and used_domains.get(domain, 0) >= MAX_BULLETS_PER_DOMAIN:
            return False
        used_domains[domain] = used_domains.get(domain, 0) + 1
        seen.add(int(event["id"]))
        bullets.append({"event_id": int(event["id"]), "text": text, "event": event})
        return True

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            event_id = int(row.get("event_id") or 0)
        except (TypeError, ValueError):
            continue
        event = by_id.get(event_id)
        if event is None or event_id in seen:
            continue
        text = str(row.get("bullet") or "").strip()
        if text:
            accept(event, text)
        if len(bullets) >= count:
            break

    # Top up from the ranking when the model returned too few, so the digest
    # always has the requested number of bullets if there is material for them.
    # The title is used rather than the summary here because an untouched
    # publisher summary is usually a truncated opening paragraph, which reads
    # badly as a bullet.
    for event in ranked:
        if len(bullets) >= count:
            break
        if int(event["id"]) in seen:
            continue
        accept(event, truncate(event.get("title") or event.get("summary") or "", 200))

    # A workspace genuinely dominated by one outlet would otherwise return
    # fewer bullets than asked for, so the cap is relaxed as a last resort.
    for event in ranked:
        if len(bullets) >= count:
            break
        event_id = int(event["id"])
        if event_id in seen:
            continue
        seen.add(event_id)
        bullets.append(
            {
                "event_id": event_id,
                "text": truncate(event.get("title") or event.get("summary") or "", 200),
                "event": event,
            }
        )

    return bullets
