"""Grouping surviving articles into events.

An event is one real world happening that several outlets covered. The digest
is written from events rather than articles, which is what stops five reports
of one announcement from taking five of the five bullets.

Clustering is incremental. A new article is matched against the centroids of
events still open from previous runs before any new event is created, so a
story that develops over several days stays one event instead of becoming a
fresh one every morning.

The same contradiction guard the deduplication stage uses applies here, because
the failure it prevents is worse at this level: merging a rate hold with a rate
cut would produce a single summary asserting both.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..util import parse_datetime, utcnow
from .dedup import Fingerprint, build_fingerprint, contradicts

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np


@dataclass
class Cluster:
    """A proposed event: the articles in it and where it should be stored."""

    article_ids: list[int] = field(default_factory=list)
    similarities: dict[int, float] = field(default_factory=dict)
    existing_event_id: int | None = None
    title: str = ""
    primary_id: int | None = None

    @property
    def size(self) -> int:
        return len(self.article_ids)


class _UnionFind:
    def __init__(self, count: int) -> None:
        self._parent = list(range(count))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def _normalise(vector: np.ndarray) -> np.ndarray:
    import numpy as np

    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


def centroid_of(vectors: np.ndarray) -> np.ndarray:
    """The unit mean of a set of unit vectors.

    Renormalising matters: an unnormalised mean shrinks as a cluster grows, so
    similarity against it would drift downward and a large running event would
    slowly stop attracting new coverage.
    """
    import numpy as np

    if vectors.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return _normalise(np.asarray(vectors, dtype=np.float32).mean(axis=0)).astype(np.float32)


def build_clusters(
    articles: Sequence[dict[str, Any]],
    vectors: np.ndarray,
    *,
    cluster_threshold: float,
) -> list[Cluster]:
    """Group a batch of articles among themselves.

    Single link connectivity is used deliberately. Coverage of one event forms
    a chain, a wire report, a rewrite of it, and an analysis piece referencing
    the rewrite, where the two ends may not be similar to each other but each
    link is strong. Requiring every pair to be similar would split that chain.
    The contradiction guard is what keeps single link from over merging.
    """
    import numpy as np

    from ..embeddings.registry import cosine_matrix

    count = len(articles)
    if count == 0:
        return []

    prints = [build_fingerprint(a) for a in articles]
    if count == 1 or vectors.size == 0:
        return [
            Cluster(article_ids=[p.article_id], similarities={p.article_id: 1.0})
            for p in prints
        ]

    scores = cosine_matrix(vectors, vectors)
    union = _UnionFind(count)

    for i in range(count):
        for j in range(i + 1, count):
            similarity = float(scores[i, j])
            if similarity < cluster_threshold:
                continue
            if contradicts(prints[i], prints[j]):
                continue
            union.union(i, j)

    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(union.find(index), []).append(index)

    clusters: list[Cluster] = []
    for members in groups.values():
        member_vectors = vectors[members]
        centre = centroid_of(member_vectors)
        # The representative is the member closest to the cluster's centre,
        # which reads better as a title than an arbitrary first arrival.
        affinities = np.asarray(member_vectors, dtype=np.float32) @ centre
        best_position = int(np.argmax(affinities)) if len(members) > 1 else 0
        cluster = Cluster(
            article_ids=[prints[m].article_id for m in members],
            similarities={
                prints[m].article_id: float(affinities[position])
                for position, m in enumerate(members)
            },
            title=prints[members[best_position]].title,
            primary_id=prints[members[best_position]].article_id,
        )
        clusters.append(cluster)

    return clusters


def attach_to_existing(
    clusters: Sequence[Cluster],
    cluster_vectors: Sequence[np.ndarray],
    existing: Sequence[tuple[int, list[float]]],
    fingerprints: dict[int, Fingerprint],
    existing_fingerprints: dict[int, Fingerprint],
    *,
    cluster_threshold: float,
) -> None:
    """Point each cluster at an open event it continues, when there is one.

    Mutates `clusters` in place by setting `existing_event_id`, so the caller
    can tell a continuation from a genuinely new event.
    """
    import numpy as np

    if not existing or not clusters:
        return

    widths = {len(vector) for _, vector in existing}
    target_width = len(cluster_vectors[0]) if len(cluster_vectors) else 0
    # A stored centroid from a different embedding backend has a different
    # width and lives in a different space, so it is skipped rather than
    # compared to nonsense.
    usable = [(event_id, vector) for event_id, vector in existing if len(vector) == target_width]
    if not usable or target_width == 0:
        return
    if len(widths) > 1:
        # Mixed widths mean the backend changed at some point; only the current
        # ones are comparable.
        pass

    event_ids = [event_id for event_id, _ in usable]
    matrix = np.asarray([vector for _, vector in usable], dtype=np.float32)
    proposals = np.asarray(cluster_vectors, dtype=np.float32)
    scores = proposals @ matrix.T

    taken: set[int] = set()
    for index, cluster in enumerate(clusters):
        order = np.argsort(-scores[index])
        for position in order:
            score = float(scores[index, int(position)])
            if score < cluster_threshold:
                break
            event_id = event_ids[int(position)]
            if event_id in taken:
                continue
            representative = fingerprints.get(cluster.primary_id or 0)
            existing_print = existing_fingerprints.get(event_id)
            if representative and existing_print and contradicts(representative, existing_print):
                continue
            cluster.existing_event_id = event_id
            taken.add(event_id)
            break


def importance_of(
    *,
    size: int,
    distinct_domains: int,
    newest_published: str,
    source_weight: float = 1.0,
) -> float:
    """How prominently an event should feature in the digest.

    Independent corroboration is weighted above raw article count: five outlets
    covering something is a stronger signal than one outlet publishing five
    follow ups, and without this distinction a single prolific source would
    dominate every digest.
    """
    corroboration = math.log1p(max(distinct_domains, 1)) * 2.4

    # Article count is credited only up to a few per outlet. A regulator's
    # filing feed will happily produce nine near identical notices in an hour,
    # and counting all nine let a single source's routine paperwork outrank a
    # story four independent outlets thought worth covering. Capping the count
    # by the number of contributing outlets makes volume a supporting signal
    # for corroboration rather than a substitute for it.
    effective_size = min(max(size, 1), max(distinct_domains, 1) * 3)
    volume = math.log1p(effective_size)

    recency = 0.0
    published = parse_datetime(newest_published) if newest_published else None
    if published is not None:
        hours = max(0.0, (utcnow() - published).total_seconds() / 3600.0)
        # Halves roughly every 18 hours, so this morning's news outranks
        # yesterday's without erasing a story that is still developing.
        recency = 2.0 * math.exp(-hours / 26.0)

    return round((corroboration + volume + recency) * max(source_weight, 0.1), 4)


ADJUDICATION_BAND = 0.08
"""How far below the clustering cut a pair is still worth asking a model about.

Pairs in this band are the ones where the encoder is genuinely uncertain, which
is exactly where the hold against cut failure lives. Everything outside it is
decided without an API call, so the adjudication cost stays proportional to the
ambiguity rather than to the article count."""


def borderline_pairs(
    articles: Sequence[dict[str, Any]],
    vectors: np.ndarray,
    *,
    cluster_threshold: float,
    limit: int = 40,
) -> list[tuple[int, int, float]]:
    """Article pairs close enough to the cut that a model should decide."""

    from ..embeddings.registry import cosine_matrix

    if len(articles) < 2 or vectors.size == 0:
        return []
    scores = cosine_matrix(vectors, vectors)
    low = cluster_threshold - ADJUDICATION_BAND
    high = cluster_threshold + ADJUDICATION_BAND

    out: list[tuple[int, int, float]] = []
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            score = float(scores[i, j])
            if low <= score <= high:
                out.append((int(articles[i]["id"]), int(articles[j]["id"]), score))
    out.sort(key=lambda row: -row[2])
    return out[:limit]
