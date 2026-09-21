"""Every query the app makes, in one place.

Two conventions, both there to keep SQLite and Postgres behaving identically:

* Booleans are always passed as Python `bool`. Postgres will not implicitly
  cast an integer to boolean, and SQLite stores a bool as 0 or 1 happily, so
  `bool` is the one form both accept. On the way out they are normalised again,
  because SQLite returns integers where Postgres returns real booleans.
* JSON columns are dumped on write and read back through `util.loads`, which
  tolerates Postgres handing back an already decoded object.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .. import settings
from ..util import (
    content_hash,
    dumps,
    host_of,
    loads,
    normalise_url,
    now_iso,
    simhash,
    slugify,
    truncate,
    url_key,
    word_count,
)
from .engine import Row, get_db

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np


def _b(value: Any) -> bool:
    return bool(value)


# ------------------------------------------------------------- workspaces

_DEFAULT_THEMES = [
    {"id": "policy", "label": "Policy & Regulation", "hint": "Rules, laws, official decisions"},
    {"id": "markets", "label": "Markets & Business", "hint": "Companies, deals, prices, jobs"},
    {"id": "events", "label": "Events & Community", "hint": "Happenings, culture, people"},
    {"id": "safety", "label": "Safety & Incidents", "hint": "Crime, accidents, emergencies"},
    {"id": "other", "label": "Other", "hint": "Anything that fits no other theme"},
]


def normalise_themes(value: Any) -> list[dict[str, Any]]:
    """Accept themes as plain labels or as full objects, always store objects.

    The classifier needs the `hint` to tell one theme from another, so the
    stored form is always {id, label, hint}. But a person editing a taxonomy in
    the UI, or writing one into an API call, reasonably sends a list of names.
    Both are taken here and an existing hint is never discarded by an edit that
    only renamed something.
    """
    if not isinstance(value, list):
        return list(_DEFAULT_THEMES)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, str):
            label = entry.strip()
            if not label:
                continue
            theme = {"id": slugify(label), "label": label, "hint": ""}
        elif isinstance(entry, dict):
            label = str(entry.get("label") or entry.get("name") or "").strip()
            theme_id = str(entry.get("id") or "").strip() or slugify(label)
            if not theme_id:
                continue
            theme = {
                "id": theme_id,
                "label": label or theme_id,
                "hint": str(entry.get("hint") or "").strip(),
            }
        else:
            continue
        if theme["id"] in seen:
            continue
        seen.add(theme["id"])
        out.append(theme)

    return out or list(_DEFAULT_THEMES)


def shape_workspace(row: Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"] or "",
        "timezone": row["timezone"] or "UTC",
        "language": row["language"] or "en",
        "preset": row["preset"] or "",
        "themes": normalise_themes(loads(row["themes"], [])),
        "keywords": loads(row["keywords"], {}) or {"include": [], "exclude": [], "boost": []},
        "config": loads(row["config"], {}),
        "enabled": _b(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_workspaces() -> list[dict[str, Any]]:
    db = get_db()
    rows = db.query(
        """
        SELECT w.*,
               (SELECT COUNT(*) FROM sources s WHERE s.workspace_id = w.id) AS source_count,
               (SELECT COUNT(*) FROM articles a WHERE a.workspace_id = w.id) AS article_count,
               (SELECT MAX(r.started_at) FROM runs r WHERE r.workspace_id = w.id) AS last_run_at
        FROM workspaces w
        ORDER BY w.name
        """
    )
    out = []
    for row in rows:
        shaped = shape_workspace(row)
        if shaped is None:
            continue
        shaped["source_count"] = int(row["source_count"] or 0)
        shaped["article_count"] = int(row["article_count"] or 0)
        shaped["last_run_at"] = row["last_run_at"] or ""
        out.append(shaped)
    return out


def get_workspace(slug: str) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM workspaces WHERE slug = ?", (slug,))
    return shape_workspace(row)


def get_workspace_by_id(workspace_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
    return shape_workspace(row)


def _unique_slug(base: str) -> str:
    db = get_db()
    slug = slugify(base) or "workspace"
    candidate = slug
    suffix = 2
    while db.query_one("SELECT id FROM workspaces WHERE slug = ?", (candidate,)):
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def create_workspace(
    *,
    name: str,
    slug: str = "",
    description: str = "",
    timezone: str = "",
    language: str = "en",
    preset: str = "",
    themes: list[dict[str, Any]] | None = None,
    keywords: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = get_db()
    stamp = now_iso()
    final_slug = _unique_slug(slug or name)
    db.insert(
        """
        INSERT INTO workspaces
            (slug, name, description, timezone, language, preset, themes, keywords,
             config, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            final_slug,
            name.strip() or final_slug,
            description,
            timezone or settings.DEFAULT_TIMEZONE,
            language or "en",
            preset,
            dumps(normalise_themes(themes) if themes is not None else _DEFAULT_THEMES),
            dumps(keywords or {"include": [], "exclude": [], "boost": []}),
            dumps(config or {}),
            True,
            stamp,
            stamp,
        ),
    )
    created = get_workspace(final_slug)
    assert created is not None
    return created


_WORKSPACE_JSON_FIELDS = {"themes", "keywords", "config"}
_WORKSPACE_FIELDS = {
    "name", "description", "timezone", "language", "enabled",
    "themes", "keywords", "config",
}


def update_workspace(slug: str, **fields: Any) -> dict[str, Any] | None:
    updates = {k: v for k, v in fields.items() if k in _WORKSPACE_FIELDS}
    if not updates:
        return get_workspace(slug)
    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        assignments.append(f"{key} = ?")
        if key == "themes":
            values.append(dumps(normalise_themes(value)))
        elif key in _WORKSPACE_JSON_FIELDS:
            values.append(dumps(value))
        elif key == "enabled":
            values.append(_b(value))
        else:
            values.append(value)
    assignments.append("updated_at = ?")
    values.extend([now_iso(), slug])
    get_db().execute(
        f"UPDATE workspaces SET {', '.join(assignments)} WHERE slug = ?", values
    )
    return get_workspace(slug)


def delete_workspace(slug: str) -> bool:
    db = get_db()
    row = db.query_one("SELECT id FROM workspaces WHERE slug = ?", (slug,))
    if row is None:
        return False
    # SQLite only enforces ON DELETE CASCADE when foreign keys are on, which
    # the engine does per connection, but deleting children explicitly keeps
    # the behaviour identical on a database that was created elsewhere.
    workspace_id = row["id"]
    for table in ("digests", "events", "entities", "runs", "articles", "sources"):
        db.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (workspace_id,))
    db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    return True


# ----------------------------------------------------------------- sources


def shape_source(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "kind": row["kind"],
        "name": row["name"] or "",
        "url": row["url"] or "",
        "config": loads(row["config"], {}),
        "weight": float(row["weight"] or 1.0),
        "enabled": _b(row["enabled"]),
        "etag": row["etag"] or "",
        "last_modified": row["last_modified"] or "",
        "last_run_at": row["last_run_at"] or "",
        "last_success_at": row["last_success_at"] or "",
        "last_status": row["last_status"] or "",
        "last_error": row["last_error"] or "",
        "consecutive_failures": int(row["consecutive_failures"] or 0),
        "items_seen": int(row["items_seen"] or 0),
    }


def list_sources(workspace_id: int, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM sources WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    if enabled_only:
        sql += " AND enabled = ?"
        params.append(True)
    sql += " ORDER BY kind, name, id"
    return [shape_source(row) for row in get_db().query(sql, params)]


def get_source(source_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    return shape_source(row) if row else None


def add_source(
    workspace_id: int,
    *,
    kind: str,
    url: str,
    name: str = "",
    config: dict[str, Any] | None = None,
    weight: float = 1.0,
) -> dict[str, Any] | None:
    db = get_db()
    kind = kind.strip().lower()
    url = url.strip()
    existing = db.query_one(
        "SELECT * FROM sources WHERE workspace_id = ? AND kind = ? AND url = ?",
        (workspace_id, kind, url),
    )
    if existing:
        return shape_source(existing)
    new_id = db.insert(
        """
        INSERT INTO sources
            (workspace_id, kind, name, url, config, weight, enabled, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            kind,
            name.strip() or (host_of(url) or kind),
            url,
            dumps(config or {}),
            float(weight),
            True,
            now_iso(),
        ),
    )
    return get_source(new_id)


_SOURCE_FIELDS = {"name", "url", "config", "weight", "enabled"}


def update_source(source_id: int, **fields: Any) -> dict[str, Any] | None:
    updates = {k: v for k, v in fields.items() if k in _SOURCE_FIELDS}
    if not updates:
        return get_source(source_id)
    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        assignments.append(f"{key} = ?")
        if key == "config":
            values.append(dumps(value))
        elif key == "enabled":
            values.append(_b(value))
        elif key == "weight":
            values.append(float(value))
        else:
            values.append(value)
    values.append(source_id)
    get_db().execute(f"UPDATE sources SET {', '.join(assignments)} WHERE id = ?", values)
    return get_source(source_id)


def delete_source(source_id: int) -> bool:
    db = get_db()
    if db.query_one("SELECT id FROM sources WHERE id = ?", (source_id,)) is None:
        return False
    db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return True


# A source that has failed this many times running is disabled automatically.
# Left alone, a dead feed burns a slice of every run's time budget forever.
AUTO_DISABLE_AFTER = 8


def record_source_result(
    source_id: int,
    *,
    status: str,
    error: str = "",
    etag: str = "",
    last_modified: str = "",
    items: int = 0,
) -> None:
    db = get_db()
    stamp = now_iso()
    if status in ("ok", "unchanged"):
        db.execute(
            """
            UPDATE sources
               SET last_run_at = ?, last_success_at = ?, last_status = ?, last_error = '',
                   consecutive_failures = 0, items_seen = items_seen + ?,
                   etag = ?, last_modified = ?
             WHERE id = ?
            """,
            (stamp, stamp, status, int(items), etag, last_modified, source_id),
        )
        return

    db.execute(
        """
        UPDATE sources
           SET last_run_at = ?, last_status = ?, last_error = ?,
               consecutive_failures = consecutive_failures + 1
         WHERE id = ?
        """,
        (stamp, status, truncate(error, 500), source_id),
    )
    row = db.query_one(
        "SELECT consecutive_failures FROM sources WHERE id = ?", (source_id,)
    )
    if row and int(row["consecutive_failures"] or 0) >= AUTO_DISABLE_AFTER:
        db.execute(
            "UPDATE sources SET enabled = ?, last_error = ? WHERE id = ?",
            (
                False,
                f"Disabled automatically after {AUTO_DISABLE_AFTER} consecutive "
                "failures. Fix the URL and enable it again.",
                source_id,
            ),
        )


# ---------------------------------------------------------------- articles


def shape_article(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "source_id": row["source_id"],
        "url": row["url"],
        "canonical_url": row["canonical_url"] or "",
        "domain": row["domain"] or "",
        "title": row["title"] or "",
        "author": row["author"] or "",
        "summary": row["summary"] or "",
        "language": row["language"] or "",
        "image_url": row["image_url"] or "",
        "published_at": row["published_at"] or "",
        "fetched_at": row["fetched_at"] or "",
        "theme": row["theme"] or "",
        "sentiment": row["sentiment"],
        "stance": row["stance"] or "",
        "importance": float(row["importance"] or 0),
        "word_count": int(row["word_count"] or 0),
        "extraction": row["extraction"] or "",
        "duplicate_of": row["duplicate_of"],
        "meta": loads(row["meta"], {}),
    }


def upsert_article(
    workspace_id: int,
    *,
    source_id: int | None,
    url: str,
    title: str,
    summary: str = "",
    content: str = "",
    author: str = "",
    published_at: str = "",
    image_url: str = "",
    language: str = "",
    canonical_url: str = "",
    extraction: str = "feed",
    meta: dict[str, Any] | None = None,
) -> tuple[int, bool]:
    """Store an article, returning its id and whether it is new.

    The unique index on (workspace_id, url_key) is what makes re-reading a feed
    cheap, so the insert is attempted first and a conflict is treated as the
    normal case rather than an error. An existing row is enriched, never
    overwritten: a later run that fetched the full text should fill in the body
    without discarding a better title the feed gave first.
    """
    db = get_db()
    key = url_key(canonical_url or url)
    fingerprint_text = f"{title}\n{content or summary}"

    existing = db.query_one(
        "SELECT id, content, extraction FROM articles WHERE workspace_id = ? AND url_key = ?",
        (workspace_id, key),
    )
    if existing:
        article_id = int(existing["id"])
        if content and len(content) > len(existing["content"] or ""):
            db.execute(
                """
                UPDATE articles
                   SET content = ?, word_count = ?, simhash = ?, content_hash = ?,
                       extraction = ?
                 WHERE id = ?
                """,
                (
                    content,
                    word_count(content),
                    simhash(fingerprint_text),
                    content_hash(fingerprint_text),
                    extraction,
                    article_id,
                ),
            )
        return article_id, False

    article_id = db.insert(
        """
        INSERT INTO articles
            (workspace_id, source_id, url, canonical_url, url_key, domain, title,
             author, summary, content, language, image_url, published_at, fetched_at,
             simhash, content_hash, word_count, extraction, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            source_id,
            url,
            canonical_url or normalise_url(url),
            key,
            host_of(url),
            truncate(title, 500),
            truncate(author, 200),
            truncate(summary, 4000),
            content,
            language,
            image_url,
            published_at,
            now_iso(),
            simhash(fingerprint_text),
            content_hash(fingerprint_text),
            word_count(content or summary),
            extraction,
            dumps(meta or {}),
        ),
    )
    return article_id, True


def set_article_fields(article_id: int, **fields: Any) -> None:
    allowed = {
        "theme", "theme_source", "sentiment", "stance", "importance",
        "duplicate_of", "summary", "content", "meta", "extraction",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        assignments.append(f"{key} = ?")
        values.append(dumps(value) if key == "meta" else value)
    values.append(article_id)
    get_db().execute(f"UPDATE articles SET {', '.join(assignments)} WHERE id = ?", values)


def articles_in_window(
    workspace_id: int, since_iso: str, *, include_duplicates: bool = False
) -> list[dict[str, Any]]:
    """Articles fetched or published since a point in time.

    Both timestamps are checked because a feed may surface a story days after
    it was published, and a run that only looked at `published_at` would never
    process it.
    """
    sql = """
        SELECT * FROM articles
         WHERE workspace_id = ?
           AND (fetched_at >= ? OR (published_at != '' AND published_at >= ?))
    """
    params: list[Any] = [workspace_id, since_iso, since_iso]
    if not include_duplicates:
        sql += " AND duplicate_of IS NULL"
    sql += " ORDER BY published_at DESC, id DESC"
    return [shape_article(row) for row in get_db().query(sql, params)]


def get_articles(
    workspace_id: int,
    *,
    theme: str = "",
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    include_duplicates: bool = False,
    sort: str = "newest",
) -> tuple[list[dict[str, Any]], int]:
    where = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if theme:
        where.append("theme = ?")
        params.append(theme)
    if query:
        # Wildcards live in the parameter, never in the SQL text, so the same
        # statement runs unchanged on both dialects.
        where.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
        pattern = f"%{query.lower()}%"
        params.extend([pattern, pattern])
    if not include_duplicates:
        where.append("duplicate_of IS NULL")
    clause = " AND ".join(where)

    db = get_db()
    total = int(db.scalar(f"SELECT COUNT(*) FROM articles WHERE {clause}", params) or 0)
    # An article with no published date falls back to when it was fetched, so
    # a feed that omits dates does not sink to the bottom of every listing.
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = db.query(
        f"""
        SELECT * FROM articles WHERE {clause}
         ORDER BY COALESCE(NULLIF(published_at, ''), fetched_at) {direction}, id {direction}
         LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    )
    return [shape_article(row) for row in rows], total


def count_articles(workspace_id: int, *, include_duplicates: bool = False) -> int:
    sql = "SELECT COUNT(*) FROM articles WHERE workspace_id = ?"
    if not include_duplicates:
        sql += " AND duplicate_of IS NULL"
    return int(get_db().scalar(sql, (workspace_id,)) or 0)


def get_article(article_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM articles WHERE id = ?", (article_id,))
    return shape_article(row) if row else None


def article_texts(article_ids: Sequence[int]) -> dict[int, dict[str, str]]:
    """Title, summary and body for a set of articles, for embedding or prompts."""
    if not article_ids:
        return {}
    placeholders = ",".join("?" for _ in article_ids)
    rows = get_db().query(
        f"SELECT id, title, summary, content FROM articles WHERE id IN ({placeholders})",
        list(article_ids),
    )
    return {
        int(r["id"]): {
            "title": r["title"] or "",
            "summary": r["summary"] or "",
            "content": r["content"] or "",
        }
        for r in rows
    }


def find_by_content_hash(workspace_id: int, digest: str, since_iso: str) -> int | None:
    row = get_db().query_one(
        """
        SELECT id FROM articles
         WHERE workspace_id = ? AND content_hash = ? AND fetched_at >= ?
               AND duplicate_of IS NULL
         ORDER BY id LIMIT 1
        """,
        (workspace_id, digest, since_iso),
    )
    return int(row["id"]) if row else None


# -------------------------------------------------------------- embeddings


def save_embeddings(
    rows: Sequence[tuple[int, str, str, int, bytes]],
) -> None:
    """Store vectors for a batch: (article_id, provider, model, dim, blob)."""
    if not rows:
        return
    db = get_db()
    stamp = now_iso()
    # An article keeps exactly one current vector, so re-embedding replaces
    # rather than accumulates. Delete then insert is the only form both
    # dialects spell identically.
    ids = [r[0] for r in rows]
    placeholders = ",".join("?" for _ in ids)
    db.execute(f"DELETE FROM embeddings WHERE article_id IN ({placeholders})", ids)
    db.execute_many(
        """
        INSERT INTO embeddings (article_id, provider, model, dim, vector, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(r[0], r[1], r[2], r[3], r[4], stamp) for r in rows],
    )


def articles_missing_embeddings(
    workspace_id: int, since_iso: str, provider: str, model: str, *, limit: int = 500
) -> list[int]:
    """Articles with no vector, or one from a different backend.

    A vector produced by another provider lives in a different space and cannot
    be compared with these, so it counts as missing rather than reusable.
    """
    rows = get_db().query(
        """
        SELECT a.id FROM articles a
        LEFT JOIN embeddings e ON e.article_id = a.id
         WHERE a.workspace_id = ?
           AND a.fetched_at >= ?
           AND (e.article_id IS NULL OR e.provider != ? OR e.model != ?)
         ORDER BY a.id
         LIMIT ?
        """,
        (workspace_id, since_iso, provider, model, int(limit)),
    )
    return [int(r["id"]) for r in rows]


def load_embedding_matrix(
    workspace_id: int, since_iso: str, provider: str, model: str
) -> tuple[list[int], np.ndarray]:
    """Every comparable vector in the window, as one matrix.

    Returned as a single array rather than per row so the similarity stage is
    one matrix product instead of a Python loop over thousands of pairs.
    """
    import numpy as np

    from ..embeddings.base import from_blob

    rows = get_db().query(
        """
        SELECT e.article_id, e.vector, e.dim FROM embeddings e
        JOIN articles a ON a.id = e.article_id
         WHERE a.workspace_id = ? AND a.fetched_at >= ?
           AND e.provider = ? AND e.model = ?
         ORDER BY e.article_id
        """,
        (workspace_id, since_iso, provider, model),
    )
    ids: list[int] = []
    vectors: list[list[float]] = []
    expected = 0
    for row in rows:
        values = from_blob(row["vector"])
        if not values:
            continue
        expected = expected or len(values)
        # A row of the wrong width would make the stack ragged and crash numpy,
        # which happens if a model's dimension changed under the same name.
        if len(values) != expected:
            continue
        ids.append(int(row["article_id"]))
        vectors.append(values)

    if not vectors:
        return [], np.zeros((0, 1), dtype=np.float32)
    return ids, np.asarray(vectors, dtype=np.float32)


# ------------------------------------------------------------------ events


def shape_event(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "slug": row["slug"] or "",
        "title": row["title"] or "",
        "theme": row["theme"] or "",
        "summary": row["summary"] or "",
        "narrative": row["narrative"] or "",
        "importance": float(row["importance"] or 0),
        "size": int(row["size"] or 0),
        "source_count": int(row["source_count"] or 0),
        "first_seen": row["first_seen"] or "",
        "last_seen": row["last_seen"] or "",
        "status": row["status"] or "open",
        "meta": loads(row["meta"], {}),
    }


def create_event(
    workspace_id: int,
    *,
    title: str,
    theme: str = "",
    centroid: bytes | None = None,
    first_seen: str = "",
    last_seen: str = "",
    importance: float = 0.0,
) -> int:
    return get_db().insert(
        """
        INSERT INTO events
            (workspace_id, slug, title, theme, importance, size, source_count,
             centroid, first_seen, last_seen, status, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            slugify(title),
            truncate(title, 400),
            theme,
            float(importance),
            0,
            0,
            centroid,
            first_seen or now_iso(),
            last_seen or now_iso(),
            "open",
            dumps({}),
        ),
    )


def update_event(event_id: int, **fields: Any) -> None:
    allowed = {
        "title", "theme", "summary", "narrative", "importance", "size",
        "source_count", "centroid", "last_seen", "status", "meta",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        assignments.append(f"{key} = ?")
        values.append(dumps(value) if key == "meta" else value)
    values.append(event_id)
    get_db().execute(f"UPDATE events SET {', '.join(assignments)} WHERE id = ?", values)


def add_event_articles(
    event_id: int, members: Sequence[tuple[int, float, bool]]
) -> None:
    """Attach articles to an event: (article_id, similarity, is_primary)."""
    if not members:
        return
    db = get_db()
    ids = [m[0] for m in members]
    placeholders = ",".join("?" for _ in ids)
    db.execute(
        f"DELETE FROM event_articles WHERE event_id = ? AND article_id IN ({placeholders})",
        [event_id, *ids],
    )
    db.execute_many(
        """
        INSERT INTO event_articles (event_id, article_id, similarity, is_primary)
        VALUES (?, ?, ?, ?)
        """,
        [(event_id, m[0], float(m[1]), _b(m[2])) for m in members],
    )
    db.execute(
        """
        UPDATE events SET
            size = (SELECT COUNT(*) FROM event_articles WHERE event_id = ?),
            source_count = (
                SELECT COUNT(DISTINCT a.domain) FROM event_articles ea
                JOIN articles a ON a.id = ea.article_id
                WHERE ea.event_id = ?
            )
        WHERE id = ?
        """,
        (event_id, event_id, event_id),
    )


_EVENT_ORDERS = {
    "importance": "importance DESC, size DESC, last_seen DESC",
    "newest": "last_seen DESC, importance DESC",
    "oldest": "first_seen ASC, importance DESC",
}


def events_in_window(
    workspace_id: int,
    since_iso: str,
    *,
    theme: str = "",
    limit: int = 100,
    offset: int = 0,
    sort: str = "importance",
) -> tuple[list[dict[str, Any]], int]:
    where = ["workspace_id = ?", "last_seen >= ?"]
    params: list[Any] = [workspace_id, since_iso]
    if theme:
        where.append("theme = ?")
        params.append(theme)
    clause = " AND ".join(where)
    db = get_db()
    total = int(db.scalar(f"SELECT COUNT(*) FROM events WHERE {clause}", params) or 0)
    order = _EVENT_ORDERS.get(sort, _EVENT_ORDERS["importance"])
    rows = db.query(
        f"""
        SELECT * FROM events WHERE {clause}
         ORDER BY {order}
         LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    )
    return [shape_event(r) for r in rows], total


def event_articles(event_id: int) -> list[dict[str, Any]]:
    rows = get_db().query(
        """
        SELECT a.*, ea.similarity, ea.is_primary FROM event_articles ea
        JOIN articles a ON a.id = ea.article_id
         WHERE ea.event_id = ?
         ORDER BY ea.is_primary DESC, ea.similarity DESC
        """,
        (event_id,),
    )
    out = []
    for row in rows:
        shaped = shape_article(row)
        shaped["similarity"] = float(row["similarity"] or 0)
        shaped["is_primary"] = _b(row["is_primary"])
        out.append(shaped)
    return out


def event_id_for_article(article_id: int) -> int | None:
    row = get_db().query_one(
        "SELECT event_id FROM event_articles WHERE article_id = ? LIMIT 1", (article_id,)
    )
    return int(row["event_id"]) if row else None


def open_events_with_centroids(
    workspace_id: int, since_iso: str
) -> list[tuple[int, list[float]]]:
    """Existing events a new article might belong to.

    Matching against a running event's centroid first is what makes a story
    that develops over several days stay one event rather than becoming a new
    one every morning.
    """
    from ..embeddings.base import from_blob

    rows = get_db().query(
        """
        SELECT id, centroid FROM events
         WHERE workspace_id = ? AND last_seen >= ? AND centroid IS NOT NULL
        """,
        (workspace_id, since_iso),
    )
    out: list[tuple[int, list[float]]] = []
    for row in rows:
        values = from_blob(row["centroid"])
        if values:
            out.append((int(row["id"]), values))
    return out


# ---------------------------------------------------------------- entities


def record_entities(
    workspace_id: int, article_id: int, entities: Iterable[dict[str, Any]]
) -> None:
    db = get_db()
    stamp = now_iso()
    for entity in entities:
        name = (entity.get("name") or "").strip()
        if not name:
            continue
        kind = (entity.get("kind") or entity.get("type") or "other").strip().lower()
        norm = slugify(name)
        if not norm:
            continue
        existing = db.query_one(
            "SELECT id FROM entities WHERE workspace_id = ? AND norm = ? AND kind = ?",
            (workspace_id, norm, kind),
        )
        if existing:
            entity_id = int(existing["id"])
            db.execute(
                "UPDATE entities SET mentions = mentions + 1, last_seen = ? WHERE id = ?",
                (stamp, entity_id),
            )
        else:
            entity_id = db.insert(
                """
                INSERT INTO entities
                    (workspace_id, name, norm, kind, mentions, first_seen, last_seen, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (workspace_id, truncate(name, 200), norm, kind, 1, stamp, stamp, dumps({})),
            )
        db.execute("DELETE FROM article_entities WHERE article_id = ? AND entity_id = ?",
                   (article_id, entity_id))
        db.execute(
            "INSERT INTO article_entities (article_id, entity_id, salience) VALUES (?, ?, ?)",
            (article_id, entity_id, float(entity.get("salience") or 0.5)),
        )


def top_entities(
    workspace_id: int, since_iso: str, *, kind: str = "", limit: int = 25
) -> list[dict[str, Any]]:
    where = ["e.workspace_id = ?", "e.last_seen >= ?"]
    params: list[Any] = [workspace_id, since_iso]
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    rows = get_db().query(
        f"""
        SELECT e.id, e.name, e.kind, COUNT(ae.article_id) AS recent
          FROM entities e
          JOIN article_entities ae ON ae.entity_id = e.id
          JOIN articles a ON a.id = ae.article_id
         WHERE {' AND '.join(where)} AND a.fetched_at >= ?
         GROUP BY e.id, e.name, e.kind
         ORDER BY recent DESC, e.mentions DESC
         LIMIT ?
        """,
        [*params, since_iso, int(limit)],
    )
    return [
        {"id": int(r["id"]), "name": r["name"], "kind": r["kind"], "count": int(r["recent"])}
        for r in rows
    ]


# ----------------------------------------------------------------- digests


def create_digest(
    workspace_id: int,
    *,
    slug: str,
    title: str,
    period_start: str,
    period_end: str,
    markdown: str,
    html: str,
    payload: dict[str, Any],
    provider: str = "",
    model: str = "",
) -> int:
    db = get_db()
    existing = db.query_one(
        "SELECT id FROM digests WHERE workspace_id = ? AND slug = ?", (workspace_id, slug)
    )
    if existing:
        digest_id = int(existing["id"])
        db.execute(
            """
            UPDATE digests SET title = ?, period_start = ?, period_end = ?, markdown = ?,
                   html = ?, payload = ?, provider = ?, model = ?, created_at = ?
             WHERE id = ?
            """,
            (title, period_start, period_end, markdown, html, dumps(payload),
             provider, model, now_iso(), digest_id),
        )
        return digest_id
    return db.insert(
        """
        INSERT INTO digests
            (workspace_id, slug, title, period_start, period_end, markdown, html,
             payload, provider, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, slug, title, period_start, period_end, markdown, html,
         dumps(payload), provider, model, now_iso()),
    )


def shape_digest(row: Row, *, full: bool = False) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "slug": row["slug"],
        "title": row["title"] or "",
        "period_start": row["period_start"] or "",
        "period_end": row["period_end"] or "",
        "created_at": row["created_at"],
        "provider": row["provider"] or "",
        "model": row["model"] or "",
    }
    if full:
        out["markdown"] = row["markdown"] or ""
        out["html"] = row["html"] or ""
        out["payload"] = loads(row["payload"], {})
    return out


def list_digests(workspace_id: int, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT * FROM digests WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
        (workspace_id, int(limit)),
    )
    return [shape_digest(r) for r in rows]


def get_digest(digest_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM digests WHERE id = ?", (digest_id,))
    return shape_digest(row, full=True) if row else None


def latest_digest(workspace_id: int) -> dict[str, Any] | None:
    row = get_db().query_one(
        "SELECT * FROM digests WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 1",
        (workspace_id,),
    )
    return shape_digest(row, full=True) if row else None


def record_delivery(
    digest_id: int, *, channel: str, target: str, status: str, error: str = ""
) -> None:
    get_db().insert(
        """
        INSERT INTO deliveries (digest_id, channel, target, status, error, sent_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (digest_id, channel, target, status, truncate(error, 500), now_iso()),
    )


# -------------------------------------------------------------------- runs


def shape_run(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "trigger": row["trigger"] or "",
        "status": row["status"] or "",
        "stage": row["stage"] or "",
        "cursor": loads(row["cursor"], {}),
        "stats": loads(row["stats"], {}),
        "log": loads(row["log"], []),
        "error": row["error"] or "",
        "started_at": row["started_at"] or "",
        "updated_at": row["updated_at"] or "",
        "finished_at": row["finished_at"] or "",
    }


def create_run(workspace_id: int, *, trigger: str = "manual") -> dict[str, Any]:
    stamp = now_iso()
    run_id = get_db().insert(
        """
        INSERT INTO runs
            (workspace_id, trigger, status, stage, cursor, stats, log, error,
             started_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, trigger, "running", "ingest", dumps({}), dumps({}), dumps([]),
         "", stamp, stamp),
    )
    run = get_run(run_id)
    assert run is not None
    return run


def get_run(run_id: int) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    return shape_run(row) if row else None


def save_run(
    run_id: int,
    *,
    status: str | None = None,
    stage: str | None = None,
    cursor: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    log: list[Any] | None = None,
    error: str | None = None,
    finished: bool = False,
) -> dict[str, Any] | None:
    assignments = ["updated_at = ?"]
    values: list[Any] = [now_iso()]
    for key, value in (
        ("status", status), ("stage", stage), ("error", error),
    ):
        if value is not None:
            assignments.append(f"{key} = ?")
            values.append(value)
    for key, value in (("cursor", cursor), ("stats", stats), ("log", log)):
        if value is not None:
            assignments.append(f"{key} = ?")
            values.append(dumps(value))
    if finished:
        assignments.append("finished_at = ?")
        values.append(now_iso())
    values.append(run_id)
    get_db().execute(f"UPDATE runs SET {', '.join(assignments)} WHERE id = ?", values)
    return get_run(run_id)


def latest_run(workspace_id: int) -> dict[str, Any] | None:
    row = get_db().query_one(
        "SELECT * FROM runs WHERE workspace_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
        (workspace_id,),
    )
    return shape_run(row) if row else None


def resumable_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Runs that stopped at a time budget and are waiting to be continued.

    This is what the cron endpoint drives: a serverless invocation that ran out
    of time leaves the run paused, and the next tick picks it up here.
    """
    rows = get_db().query(
        "SELECT * FROM runs WHERE status = ? ORDER BY updated_at LIMIT ?",
        ("paused", int(limit)),
    )
    return [shape_run(r) for r in rows]


def list_runs(workspace_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = get_db().query(
        "SELECT * FROM runs WHERE workspace_id = ? ORDER BY started_at DESC LIMIT ?",
        (workspace_id, int(limit)),
    )
    return [shape_run(r) for r in rows]


# ------------------------------------------------------------------- usage


def record_usage(run_id: int | None, workspace_id: int | None, entries: Sequence[Any]) -> None:
    if not entries:
        return
    stamp = now_iso()
    get_db().execute_many(
        """
        INSERT INTO usage
            (run_id, workspace_id, provider, model, kind, operation, input_tokens,
             output_tokens, units, cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id, workspace_id, e.provider, e.model, e.kind, e.operation,
                e.input_tokens, e.output_tokens, e.units, e.cost_usd, stamp,
            )
            for e in entries
        ],
    )


def usage_summary(workspace_id: int, *, since_iso: str = "") -> dict[str, Any]:
    where = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if since_iso:
        where.append("created_at >= ?")
        params.append(since_iso)
    clause = " AND ".join(where)
    rows = get_db().query(
        f"""
        SELECT provider, model, kind, COUNT(*) AS calls,
               SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
               SUM(units) AS units, SUM(cost_usd) AS cost_usd,
               SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced
          FROM usage WHERE {clause}
         GROUP BY provider, model, kind
         ORDER BY calls DESC
        """,
        params,
    )
    by_provider = []
    for r in rows:
        calls = int(r["calls"] or 0)
        unpriced = int(r["unpriced"] or 0)
        by_provider.append(
            {
                "provider": r["provider"],
                "model": r["model"],
                "kind": r["kind"],
                "calls": calls,
                "input_tokens": int(r["input_tokens"] or 0),
                "output_tokens": int(r["output_tokens"] or 0),
                "units": int(r["units"] or 0),
                # None, not zero, when nothing in this group had a published
                # price. The UI must be able to say "not priced" rather than
                # showing a total that reads as "this was free".
                "cost_usd": (
                    None if unpriced == calls else round(float(r["cost_usd"] or 0), 6)
                ),
                "priced": unpriced < calls,
                "unpriced_calls": unpriced,
            }
        )
    return {
        "total_cost_usd": round(
            sum(r["cost_usd"] or 0.0 for r in by_provider if r["priced"]), 6
        ),
        "unpriced_calls": sum(r["unpriced_calls"] for r in by_provider),
        "by_provider": by_provider,
    }


# ---------------------------------------------------------------------- kv


def kv_get(key: str) -> str | None:
    row = get_db().query_one(
        "SELECT value, expires_at FROM kv WHERE key = ?", (key,)
    )
    if row is None:
        return None
    expires = row["expires_at"] or ""
    if expires and expires < now_iso():
        return None
    return row["value"]


def kv_set(key: str, value: str, *, expires_at: str = "") -> None:
    db = get_db()
    db.execute("DELETE FROM kv WHERE key = ?", (key,))
    db.execute(
        "INSERT INTO kv (key, value, expires_at, updated_at) VALUES (?, ?, ?, ?)",
        (key, value, expires_at, now_iso()),
    )


def kv_delete(key: str) -> None:
    get_db().execute("DELETE FROM kv WHERE key = ?", (key,))
