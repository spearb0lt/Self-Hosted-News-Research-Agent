"""Applying a workspace's own vocabulary to what it ingests and ranks.

A workspace stores three lists: include, exclude and boost. They existed in the
schema and in the settings form from the start and nothing ever read them, so a
beat that said "only stories about SEBI, never about cricket" silently ingested
everything. This module is what makes those lists real.

The three do different jobs on purpose:

* exclude drops a story outright, at ingest, before it is ever stored. This is
  the one that saves work rather than adding it.
* include, when non empty, keeps only stories that match something. It is a
  whitelist and therefore dangerous: an over specific include list produces an
  empty digest, so the runner reports how many items each rule removed.
* boost never removes anything. It raises an event's importance so a subject
  you care about surfaces above a busier one you do not.

Matching is whole word and case insensitive, and a term containing a space is
matched as a phrase. Substring matching was tried first and was wrong: "modi"
matched "modification" and "AI" matched "said".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..util import normalise_text


def _compile(terms: list[str]) -> re.Pattern[str] | None:
    """One alternation for the whole list, anchored to word boundaries."""
    cleaned = [normalise_text(t) for t in terms if isinstance(t, str) and t.strip()]
    cleaned = [t for t in cleaned if t]
    if not cleaned:
        return None
    # Longest first so that a phrase wins over one of its own words.
    cleaned.sort(key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in cleaned)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


@dataclass
class Matcher:
    include: re.Pattern[str] | None = None
    exclude: re.Pattern[str] | None = None
    boost: re.Pattern[str] | None = None
    enabled: bool = True
    # Counted so a run can report that a rule emptied the digest, rather than
    # the user seeing nothing and assuming the sources broke.
    stats: dict[str, int] = field(default_factory=lambda: {"excluded": 0, "not_included": 0})

    @property
    def active(self) -> bool:
        return self.enabled and any((self.include, self.exclude, self.boost))

    def allows(self, *parts: str) -> bool:
        """Whether this text survives the exclude and include rules."""
        if not self.enabled:
            return True
        text = normalise_text(" ".join(p for p in parts if p))
        if not text:
            return True
        if self.exclude is not None and self.exclude.search(text):
            self.stats["excluded"] += 1
            return False
        if self.include is not None and not self.include.search(text):
            self.stats["not_included"] += 1
            return False
        return True

    def boost_factor(self, *parts: str) -> float:
        """A multiplier for event importance, 1.0 when nothing matches.

        Deliberately modest. A boosted story should win a tie against an
        equally covered one, not outrank a story five outlets independently
        thought was the day's biggest.
        """
        if not self.enabled or self.boost is None:
            return 1.0
        text = normalise_text(" ".join(p for p in parts if p))
        return 1.25 if text and self.boost.search(text) else 1.0


def build(workspace: dict[str, Any]) -> Matcher:
    keywords = workspace.get("keywords") or {}
    config = workspace.get("config") or {}
    if not isinstance(keywords, dict):
        keywords = {}
    return Matcher(
        include=_compile(keywords.get("include") or []),
        exclude=_compile(keywords.get("exclude") or []),
        boost=_compile(keywords.get("boost") or []),
        enabled=bool(config.get("apply_keywords", True)),
    )
