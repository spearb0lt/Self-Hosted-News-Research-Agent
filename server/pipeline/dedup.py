"""Deciding when two articles are the same story.

Four layers run in increasing order of cost, and each one that fires saves the
next from running:

1. The canonical URL, handled upstream by the unique index on `url_key`, so a
   story already held never reaches this stage at all.
2. A hash of the normalised text, which catches a byte identical reprint.
3. SimHash Hamming distance, which catches syndicated wire copy that each
   outlet has lightly reworded. No model involved.
4. Cosine similarity between embeddings, which catches independent reporting of
   one event.

Layer 4 is the one that can be wrong in an expensive way, so it is gated by a
contradiction guard. Sentence encoders place "RBI holds the repo rate" and "RBI
cuts the repo rate" about 0.69 apart while placing two reports of the same hold
about 0.76 apart. Seven hundredths is not a margin worth trusting, so a pair
whose texts disagree about a number or use mutually exclusive verbs is refused
regardless of how similar the vectors are.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..util import hamming, tokens

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np


# Verb and outcome groups whose members are mutually exclusive. Two articles
# that each pick a different member of the same group are reporting different
# outcomes, however similar the surrounding language is.
_ANTONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"holds", "hold", "unchanged", "steady", "maintains", "retains", "pause"}),
    frozenset({"cuts", "cut", "lowers", "lowered", "reduces", "reduced", "slashes", "eases"}),
    frozenset({"hikes", "hike", "raises", "raised", "increases", "increased", "tightens"}),
    frozenset({"approves", "approved", "clears", "cleared", "grants", "granted", "upholds",
               "upheld", "allows", "allowed", "sanctions"}),
    frozenset({"rejects", "rejected", "denies", "denied", "dismisses", "dismissed",
               "quashes", "quashed", "blocks", "blocked", "refuses", "refused",
               "strikes", "struck"}),
    frozenset({"wins", "won", "beats", "beat", "defeats", "defeated"}),
    frozenset({"loses", "lost", "concedes", "conceded"}),
    # "higher" and "lower" belong here as well as the verbs. A market report
    # very often states direction only as an adjective, "closes higher" against
    # "ends lower", and without them a pair like that scores 0.84 and merges
    # into one event asserting both directions at once.
    # Bare "up" and "down" are deliberately left out: they are far too common
    # in unrelated senses to use as evidence of a contradiction.
    frozenset({"rises", "rose", "gains", "gained", "surges", "surged", "jumps", "jumped",
               "climbs", "climbed", "rallies", "rallied", "higher", "advances",
               "advanced", "gainers"}),
    frozenset({"falls", "fell", "drops", "dropped", "declines", "declined", "slumps",
               "slumped", "sinks", "sank", "tumbles", "tumbled", "plunges", "plunged",
               "lower", "slips", "slipped", "sheds", "retreats", "retreated", "losers"}),
    frozenset({"acquitted", "acquits", "cleared"}),
    frozenset({"convicted", "convicts", "guilty", "sentenced"}),
    frozenset({"resigns", "resigned", "quits", "quit", "steps", "ousted", "sacked"}),
    frozenset({"appointed", "appoints", "named", "elevated", "promoted"}),
    frozenset({"launches", "launched", "opens", "opened", "begins", "began", "starts"}),
    frozenset({"halts", "halted", "suspends", "suspended", "shuts", "closed", "stops"}),
    frozenset({"profit", "profits", "surplus"}),
    frozenset({"loss", "losses", "deficit"}),
)

# Numbers that identify an outcome: a percentage, a basis point move, or an
# amount with a magnitude word. A bare year or a list position is not one of
# these and would only create false contradictions.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent|percent|pc\b)", re.IGNORECASE)
_BPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bps|basis\s+points?)", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(crore|lakh|billion|million|trillion|bn|mn)\b", re.IGNORECASE
)


@dataclass
class DuplicateDecision:
    is_duplicate: bool
    reason: str = ""
    score: float = 0.0
    matched_id: int | None = None


@dataclass
class Fingerprint:
    """The cheap signals used before any vector comparison."""

    article_id: int
    title: str
    text: str
    simhash: int
    content_hash: str
    numbers: set[str] = field(default_factory=set)
    antonym_groups: dict[int, frozenset[str]] = field(default_factory=dict)


def salient_numbers(text: str) -> set[str]:
    """Numbers that would change the meaning of a headline if they changed."""
    found: set[str] = set()
    for match in _PERCENT_RE.finditer(text):
        found.add(f"pct:{float(match.group(1)):g}")
    for match in _BPS_RE.finditer(text):
        found.add(f"bps:{float(match.group(1)):g}")
    for match in _AMOUNT_RE.finditer(text):
        value = match.group(1).replace(",", "")
        unit = match.group(2).lower()
        try:
            found.add(f"amt:{float(value):g}{unit}")
        except ValueError:
            continue
    return found


def antonym_signature(text: str) -> dict[int, frozenset[str]]:
    """Which mutually exclusive groups this text picks a side in."""
    words = set(tokens(text, drop_stopwords=False))
    signature: dict[int, frozenset[str]] = {}
    for index, group in enumerate(_ANTONYM_GROUPS):
        hits = words & group
        if hits:
            signature[index] = frozenset(hits)
    return signature


def build_fingerprint(article: dict[str, Any]) -> Fingerprint:
    title = article.get("title") or ""
    body = article.get("summary") or article.get("content") or ""
    # Only the headline and opening are scanned. A contradiction buried in
    # paragraph twelve is usually background rather than the article's point,
    # and scanning the whole body produces false positives from quoted history.
    text = f"{title}. {body[:600]}"
    return Fingerprint(
        article_id=int(article["id"]),
        title=title,
        text=text,
        simhash=int(article.get("simhash") or 0),
        content_hash=article.get("content_hash") or "",
        numbers=salient_numbers(text),
        antonym_groups=antonym_signature(text),
    )


# Families of groups that are mutually exclusive with one another. The indices
# refer to positions in _ANTONYM_GROUPS. Members within a single group are
# synonyms, so "raised" against "increased" agrees; members across two groups
# of one family disagree, so "holds" against "cuts" is a contradiction.
_EXCLUSIVE_FAMILIES: tuple[tuple[int, ...], ...] = (
    (0, 1, 2),      # hold / cut / hike
    (3, 4),         # approve / reject
    (5, 6),         # win / lose
    (7, 8),         # rise / fall
    (9, 10),        # acquit / convict
    (11, 12),       # resign / appoint
    (13, 14),       # launch / halt
    (15, 16),       # profit / loss
)


def contradicts(left: Fingerprint, right: Fingerprint) -> str:
    """Why these two cannot be the same story, or an empty string.

    Returning the reason rather than a boolean means the decision can be shown
    in the UI, which matters because a wrongly blocked merge is otherwise
    invisible and very hard to debug.
    """
    for family in _EXCLUSIVE_FAMILIES:
        left_side = [i for i in family if i in left.antonym_groups]
        right_side = [i for i in family if i in right.antonym_groups]
        if not left_side or not right_side:
            continue
        # A text that hedges across both sides of a family is a round up, not a
        # claim, so it cannot contradict anything.
        if len(left_side) > 1 or len(right_side) > 1:
            continue
        if left_side[0] != right_side[0]:
            left_word = next(iter(left.antonym_groups[left_side[0]]))
            right_word = next(iter(right.antonym_groups[right_side[0]]))
            return f"opposite outcome: '{left_word}' against '{right_word}'"

    # Disagreeing headline numbers of the same kind, for example one says a
    # 25 bps cut and the other 50 bps.
    for prefix in ("pct:", "bps:", "amt:"):
        left_values = {n for n in left.numbers if n.startswith(prefix)}
        right_values = {n for n in right.numbers if n.startswith(prefix)}
        if left_values and right_values and not (left_values & right_values):
            return (
                f"different figures: {sorted(left_values)[0]} against "
                f"{sorted(right_values)[0]}"
            )
    return ""


def near_identical(left: Fingerprint, right: Fingerprint, *, max_distance: int) -> bool:
    """Whether the two texts are the same words, allowing light rewriting."""
    if left.content_hash and left.content_hash == right.content_hash:
        return True
    if left.simhash and right.simhash:
        return hamming(left.simhash, right.simhash) <= max_distance
    return False


def title_overlap(left: Fingerprint, right: Fingerprint) -> float:
    """Jaccard overlap of headline words, used as a secondary check.

    Two articles about one event almost always share several headline nouns.
    Requiring some overlap catches the case where an encoder scores two
    unrelated stories highly because they share a topic and a tone.
    """
    left_words = set(tokens(left.title))
    right_words = set(tokens(right.title))
    if not left_words or not right_words:
        return 0.0
    union = len(left_words | right_words)
    return len(left_words & right_words) / union if union else 0.0


# Below this headline overlap, a high cosine is treated as the encoder being
# generous about topic rather than evidence of the same event.
MIN_TITLE_OVERLAP = 0.12


def decide(
    candidate: Fingerprint,
    existing: Fingerprint,
    *,
    similarity: float,
    duplicate_threshold: float,
    simhash_distance: int,
) -> DuplicateDecision:
    """Whether `candidate` duplicates `existing`."""
    if near_identical(candidate, existing, max_distance=simhash_distance):
        # Identical text is identical regardless of what the vectors say, and
        # the contradiction guard cannot apply to a story that is literally the
        # same words.
        return DuplicateDecision(
            True, "near identical text", 1.0, existing.article_id
        )

    if similarity < duplicate_threshold:
        return DuplicateDecision(False, "", similarity, existing.article_id)

    if reason := contradicts(candidate, existing):
        return DuplicateDecision(False, reason, similarity, existing.article_id)

    if title_overlap(candidate, existing) < MIN_TITLE_OVERLAP:
        return DuplicateDecision(
            False, "headlines share too few words", similarity, existing.article_id
        )

    return DuplicateDecision(True, "high similarity", similarity, existing.article_id)


def find_duplicates(
    new_articles: Sequence[dict[str, Any]],
    pool_articles: Sequence[dict[str, Any]],
    new_vectors: np.ndarray,
    pool_vectors: np.ndarray,
    *,
    duplicate_threshold: float,
    simhash_distance: int,
) -> dict[int, DuplicateDecision]:
    """Map each new article to the earlier article it duplicates, if any.

    `pool_articles` are the ones already kept in the window, in the same order
    as `pool_vectors`. New articles are compared against the pool and against
    each other in order, so a batch containing five copies of one wire story
    collapses to the first of them rather than to five separate survivors.
    """
    import numpy as np

    from ..embeddings.registry import cosine_matrix

    decisions: dict[int, DuplicateDecision] = {}
    if not new_articles:
        return decisions

    pool_prints = [build_fingerprint(a) for a in pool_articles]
    new_prints = [build_fingerprint(a) for a in new_articles]

    scores = (
        cosine_matrix(new_vectors, pool_vectors)
        if len(pool_prints) and pool_vectors.size
        else np.zeros((len(new_prints), 0), dtype=np.float32)
    )
    within = (
        cosine_matrix(new_vectors, new_vectors)
        if new_vectors.size
        else np.zeros((len(new_prints), len(new_prints)), dtype=np.float32)
    )

    # Survivors accumulate as the batch is walked, so an item can be matched
    # against an earlier item of the same batch that was itself kept.
    kept_indices: list[int] = []

    for index, candidate in enumerate(new_prints):
        best: DuplicateDecision | None = None

        if scores.shape[1]:
            order = np.argsort(-scores[index])[:12]
            for pool_index in order:
                decision = decide(
                    candidate,
                    pool_prints[int(pool_index)],
                    similarity=float(scores[index, int(pool_index)]),
                    duplicate_threshold=duplicate_threshold,
                    simhash_distance=simhash_distance,
                )
                if decision.is_duplicate:
                    best = decision
                    break

        if best is None:
            for other in kept_indices:
                decision = decide(
                    candidate,
                    new_prints[other],
                    similarity=float(within[index, other]) if within.size else 0.0,
                    duplicate_threshold=duplicate_threshold,
                    simhash_distance=simhash_distance,
                )
                if decision.is_duplicate:
                    best = decision
                    break

        if best is not None and best.is_duplicate:
            decisions[candidate.article_id] = best
        else:
            kept_indices.append(index)

    return decisions
