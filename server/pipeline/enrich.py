"""Per article enrichment: named entities, sentiment, and stance.

This stage is optional. It runs last so that a run which exhausts its time
budget loses the enrichment rather than the digest, and every function returns
empty rather than raising, because none of this is worth failing a run over.

Entities are extracted with a model when one is available and by a capitalised
phrase heuristic otherwise. The heuristic is deliberately conservative: a wrong
entity pollutes the entity index permanently, while a missed one costs nothing.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ..llm import LLMError
from ..llm import registry as llm
from ..util import truncate

SYSTEM = (
    "You extract structured metadata from news text. Report only what the text "
    "states. Never infer a sentiment the text does not carry, and never add an "
    "entity the text does not name."
)

SCHEMA = (
    '{"items": [{"n": 1,'
    ' "entities": [{"name": "as written", "kind": "person|org|place|ticker|other"}],'
    ' "sentiment": -1.0 to 1.0,'
    ' "stance": "factual|critical|supportive|mixed"}]}'
)

BATCH_SIZE = 12

_ENTITY_KINDS = {"person", "org", "place", "ticker", "other"}
_STANCES = {"factual", "critical", "supportive", "mixed"}

# A run of capitalised words, which is what a proper noun looks like in an
# English headline. Bounded to four words so a whole capitalised headline does
# not become one giant entity.
_PROPER_NOUN_RE = re.compile(r"\b([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})\b")

# Words that start a sentence and are capitalised for that reason alone.
_SENTENCE_STARTERS = frozenset(
    {
        "The", "A", "An", "This", "That", "These", "Those", "It", "He", "She",
        "They", "We", "You", "I", "But", "And", "Or", "If", "When", "While",
        "After", "Before", "However", "Meanwhile", "In", "On", "At", "For",
        "New", "Top", "Why", "How", "What", "Who", "Here", "There",
    }
)


def heuristic_entities(text: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Capitalised phrases that are probably proper nouns.

    Everything is reported as kind "other" because guessing between a person,
    an organisation and a place from surface form alone is unreliable, and a
    wrong kind is worse than an unspecified one.
    """
    found: dict[str, int] = {}
    for match in _PROPER_NOUN_RE.finditer(text or ""):
        phrase = match.group(1).strip()
        first_word = phrase.split()[0] if phrase else ""
        if first_word in _SENTENCE_STARTERS and " " not in phrase:
            continue
        if len(phrase) < 3 or phrase.isupper() and len(phrase) < 3:
            continue
        found[phrase] = found.get(phrase, 0) + 1

    ranked = sorted(found.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [
        {"name": name, "kind": "other", "salience": min(1.0, 0.3 + 0.2 * count)}
        for name, count in ranked[:limit]
    ]


def enrich(
    items: Sequence[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    use_model: bool = True,
) -> dict[int, dict[str, Any]]:
    """Return per article id: entities, sentiment and stance."""
    if not items:
        return {}

    results: dict[int, dict[str, Any]] = {}

    for start in range(0, len(items), BATCH_SIZE):
        batch = list(items[start : start + BATCH_SIZE])
        parsed: dict[int, dict[str, Any]] = {}

        if use_model:
            lines = []
            for index, item in enumerate(batch, start=1):
                body = item.get("summary") or item.get("content") or ""
                lines.append(f"{index}. {truncate(item.get('title') or '', 200)}")
                lines.append(f"   {truncate(body, 500)}")
            try:
                rows = llm.generate_json(
                    "\n".join(lines),
                    provider=provider,
                    model=model,
                    system=SYSTEM,
                    schema_hint=SCHEMA,
                    expect_list=True,
                    cheap=True,
                    temperature=0.0,
                    max_tokens=2500,
                    operation="enrich",
                )
                for row in rows or []:
                    if not isinstance(row, dict):
                        continue
                    try:
                        position = int(row.get("n") or 0) - 1
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= position < len(batch):
                        continue
                    parsed[position] = row
            except (LLMError, ValueError):
                parsed = {}

        for position, item in enumerate(batch):
            row = parsed.get(position) or {}
            entities: list[dict[str, Any]] = []
            for entry in row.get("entities") or []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if not name or len(name) > 120:
                    continue
                kind = str(entry.get("kind") or "other").strip().lower()
                entities.append(
                    {
                        "name": name,
                        "kind": kind if kind in _ENTITY_KINDS else "other",
                        "salience": 0.6,
                    }
                )
            if not entities:
                entities = heuristic_entities(
                    f"{item.get('title') or ''}. {item.get('summary') or ''}"
                )

            sentiment: float | None = None
            raw_sentiment = row.get("sentiment")
            if isinstance(raw_sentiment, (int, float)):
                sentiment = max(-1.0, min(1.0, float(raw_sentiment)))

            stance = str(row.get("stance") or "").strip().lower()

            results[int(item["id"])] = {
                "entities": entities[:10],
                "sentiment": sentiment,
                "stance": stance if stance in _STANCES else "",
            }

    return results


def source_spread(articles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How an event's coverage is distributed across outlets and tone.

    A story carried by one outlet with a strong tone reads very differently
    from the same story carried by eight outlets neutrally, and that difference
    is worth surfacing rather than leaving the reader to infer it.
    """
    domains: dict[str, int] = {}
    sentiments: list[float] = []
    for article in articles:
        domain = article.get("domain") or "unknown"
        domains[domain] = domains.get(domain, 0) + 1
        value = article.get("sentiment")
        if isinstance(value, (int, float)):
            sentiments.append(float(value))

    spread = 0.0
    if len(sentiments) > 1:
        spread = max(sentiments) - min(sentiments)

    return {
        "domains": sorted(domains.items(), key=lambda kv: -kv[1]),
        "distinct_domains": len(domains),
        "mean_sentiment": (
            round(sum(sentiments) / len(sentiments), 3) if sentiments else None
        ),
        # A wide spread means outlets framed the same facts very differently,
        # which is the signal a reader most wants flagged.
        "sentiment_spread": round(spread, 3),
        "contested": spread >= 0.8,
    }
