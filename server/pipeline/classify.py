"""Assigning each event a theme from the workspace's own taxonomy.

Themes are per workspace, not global, because a theme set that suits a city
beat (civic, crime, transport) is useless for a markets beat (regulation,
earnings, macro). The taxonomy therefore travels in the prompt rather than
living in the code.

Classification runs once per event and is the highest volume model call in the
pipeline, so it asks for the cheap model and batches many events into one
request. A keyword fallback covers the case where no provider is configured at
all, which keeps the app usable with zero keys.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..llm import LLMError
from ..llm import registry as llm
from ..util import slugify, tokens, truncate

SYSTEM = (
    "You are a news desk editor sorting stories into a fixed set of sections. "
    "You are given the section list for this publication and a numbered list of "
    "stories. Assign every story to exactly one section id from the list. "
    "Use the section whose description best fits what the story is about, not "
    "merely which words it contains. When nothing fits, use the last section in "
    "the list."
)

SCHEMA_HINT = (
    '{"items": [{"n": 1, "theme": "<section id>", "confidence": 0.0 to 1.0}]}'
)

# How many stories go in one request. Large enough that a typical run is one or
# two calls, small enough that a model does not lose track of the numbering.
BATCH_SIZE = 25


def _fallback_theme(text: str, themes: Sequence[dict[str, Any]]) -> str:
    """Keyword scoring, used when no model is available.

    Each theme's label and hint supply its vocabulary, so this improves
    automatically as the operator writes better hints, with no code change.
    """
    words = set(tokens(text))
    if not words or not themes:
        return themes[-1]["id"] if themes else "other"

    best_id = themes[-1]["id"]
    best_score = 0
    for theme in themes:
        vocabulary = set(tokens(f"{theme.get('label', '')} {theme.get('hint', '')}"))
        score = len(words & vocabulary)
        if score > best_score:
            best_score = score
            best_id = theme["id"]
    return best_id


def _prompt(items: Sequence[dict[str, Any]], themes: Sequence[dict[str, Any]]) -> str:
    lines = ["Sections:"]
    for theme in themes:
        label = theme.get("label") or theme["id"]
        hint = theme.get("hint") or ""
        lines.append(f"- {theme['id']}: {label}. {hint}".rstrip())
    lines.append("")
    lines.append("Stories:")
    for index, item in enumerate(items, start=1):
        title = truncate(item.get("title") or "", 200)
        summary = truncate(item.get("summary") or "", 260)
        lines.append(f"{index}. {title}")
        if summary:
            lines.append(f"   {summary}")
    return "\n".join(lines)


def classify(
    items: Sequence[dict[str, Any]],
    themes: Sequence[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[int, str]:
    """Map each item's id to a theme id.

    Never raises on a provider failure: a digest with imperfect sections is far
    better than no digest, so a failed call falls back to keyword scoring for
    that batch and the run continues.
    """
    if not items:
        return {}
    # Weaker models answer with the section's label rather than its id often
    # enough that rejecting those would send whole batches to the keyword
    # fallback. Groq's gpt-oss-120b does it consistently. Both spellings, and a
    # slugified label, therefore resolve to the same theme.
    resolve: dict[str, str] = {}
    for theme in themes:
        theme_id = theme["id"]
        resolve[theme_id.lower()] = theme_id
        label = (theme.get("label") or "").strip().lower()
        if label:
            resolve[label] = theme_id
            resolve[slugify(label)] = theme_id
    default = themes[-1]["id"] if themes else "other"
    results: dict[int, str] = {}

    for start in range(0, len(items), BATCH_SIZE):
        batch = list(items[start : start + BATCH_SIZE])
        assigned: dict[int, str] = {}
        try:
            rows = llm.generate_json(
                _prompt(batch, themes),
                provider=provider,
                model=model,
                system=SYSTEM,
                schema_hint=SCHEMA_HINT,
                expect_list=True,
                cheap=True,
                temperature=0.0,
                max_tokens=2048,
                operation="classify",
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
                answer = str(row.get("theme") or "").strip().lower()
                theme = resolve.get(answer) or resolve.get(slugify(answer))
                if theme:
                    assigned[position] = theme
        except (LLMError, ValueError):
            assigned = {}

        for position, item in enumerate(batch):
            results[int(item["id"])] = assigned.get(
                position,
                _fallback_theme(
                    f"{item.get('title', '')} {item.get('summary', '')}", themes
                ),
            ) or default

    return results
