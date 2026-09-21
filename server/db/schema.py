"""The full schema, written once in the portable dialect from `engine`.

`create_all` is idempotent, so it runs on every cold start and costs nothing
after the first. That matters on a serverless deployment, where there is no
deploy hook to run a migration and the first request has to find a usable
database whether or not it is the first request ever.

Timestamps are ISO 8601 UTC text throughout. See `engine` for why.

Boolean defaults are written as TRUE and FALSE rather than 1 and 0. SQLite maps
{BOOL} to INTEGER and would take either, but Postgres maps it to BOOLEAN and
rejects an integer default outright with a DatatypeMismatch. Both engines
accept the keywords.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .engine import render_ddl

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Database


TABLES: tuple[str, ...] = (
    # A workspace is one beat: its sources, its theme taxonomy, its schedule.
    # Everything else hangs off it, so the app is multi-beat from the start
    # rather than growing a second beat later as a special case.
    """
    CREATE TABLE IF NOT EXISTS workspaces (
        id            {PK},
        slug          {TEXT} NOT NULL UNIQUE,
        name          {TEXT} NOT NULL,
        description   {TEXT} DEFAULT '',
        timezone      {TEXT} DEFAULT 'UTC',
        language      {TEXT} DEFAULT 'en',
        preset        {TEXT} DEFAULT '',
        themes        {JSON},
        keywords      {JSON},
        config        {JSON},
        enabled       {BOOL} DEFAULT TRUE,
        created_at    {TS} NOT NULL,
        updated_at    {TS} NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id                   {PK},
        workspace_id         {BIGINT} NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        kind                 {TEXT} NOT NULL,
        name                 {TEXT} DEFAULT '',
        url                  {TEXT} NOT NULL,
        config               {JSON},
        weight               {REAL} DEFAULT 1.0,
        enabled              {BOOL} DEFAULT TRUE,
        etag                 {TEXT} DEFAULT '',
        last_modified        {TEXT} DEFAULT '',
        last_run_at          {TS},
        last_success_at      {TS},
        last_status          {TEXT} DEFAULT '',
        last_error           {TEXT} DEFAULT '',
        consecutive_failures {INT} DEFAULT 0,
        items_seen           {INT} DEFAULT 0,
        created_at           {TS} NOT NULL,
        UNIQUE (workspace_id, kind, url)
    )
    """,
    # url_key is a normalised hash of the canonical URL and is what makes
    # re-ingesting the same feed cheap: the unique constraint rejects a story
    # already held without a read first.
    """
    CREATE TABLE IF NOT EXISTS articles (
        id             {PK},
        workspace_id   {BIGINT} NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        source_id      {BIGINT} REFERENCES sources(id) ON DELETE SET NULL,
        url            {TEXT} NOT NULL,
        canonical_url  {TEXT} DEFAULT '',
        url_key        {TEXT} NOT NULL,
        domain         {TEXT} DEFAULT '',
        title          {TEXT} DEFAULT '',
        author         {TEXT} DEFAULT '',
        summary        {TEXT} DEFAULT '',
        content        {TEXT} DEFAULT '',
        language       {TEXT} DEFAULT '',
        image_url      {TEXT} DEFAULT '',
        published_at   {TS},
        fetched_at     {TS} NOT NULL,
        simhash        {BIGINT} DEFAULT 0,
        content_hash   {TEXT} DEFAULT '',
        word_count     {INT} DEFAULT 0,
        theme          {TEXT} DEFAULT '',
        theme_source   {TEXT} DEFAULT '',
        sentiment      {REAL},
        stance         {TEXT} DEFAULT '',
        importance     {REAL} DEFAULT 0,
        extraction     {TEXT} DEFAULT 'feed',
        duplicate_of   {BIGINT},
        meta           {JSON},
        UNIQUE (workspace_id, url_key)
    )
    """,
    # One current embedding per article. Re-embedding with a different provider
    # replaces the row, and `provider`/`model`/`dim` are stored so a mixed set
    # is detectable rather than silently compared across incompatible spaces.
    """
    CREATE TABLE IF NOT EXISTS embeddings (
        article_id  {BIGINT} PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
        provider    {TEXT} NOT NULL,
        model       {TEXT} NOT NULL,
        dim         {INT} NOT NULL,
        vector      {BLOB} NOT NULL,
        created_at  {TS} NOT NULL
    )
    """,
    # An event is a real world happening that several articles describe. This
    # is the unit the digest is written from, not the article.
    """
    CREATE TABLE IF NOT EXISTS events (
        id            {PK},
        workspace_id  {BIGINT} NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        slug          {TEXT} DEFAULT '',
        title         {TEXT} DEFAULT '',
        theme         {TEXT} DEFAULT '',
        summary       {TEXT} DEFAULT '',
        narrative     {TEXT} DEFAULT '',
        importance    {REAL} DEFAULT 0,
        size          {INT} DEFAULT 0,
        source_count  {INT} DEFAULT 0,
        centroid      {BLOB},
        first_seen    {TS},
        last_seen     {TS},
        status        {TEXT} DEFAULT 'open',
        meta          {JSON}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_articles (
        event_id    {BIGINT} NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        article_id  {BIGINT} NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        similarity  {REAL} DEFAULT 0,
        is_primary  {BOOL} DEFAULT FALSE,
        PRIMARY KEY (event_id, article_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
        id            {PK},
        workspace_id  {BIGINT} NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name          {TEXT} NOT NULL,
        norm          {TEXT} NOT NULL,
        kind          {TEXT} DEFAULT 'other',
        mentions      {INT} DEFAULT 0,
        first_seen    {TS},
        last_seen     {TS},
        meta          {JSON},
        UNIQUE (workspace_id, norm, kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS article_entities (
        article_id  {BIGINT} NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        entity_id   {BIGINT} NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
        salience    {REAL} DEFAULT 0,
        PRIMARY KEY (article_id, entity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS digests (
        id            {PK},
        workspace_id  {BIGINT} NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        slug          {TEXT} NOT NULL,
        title         {TEXT} DEFAULT '',
        period_start  {TS},
        period_end    {TS},
        markdown      {TEXT} DEFAULT '',
        html          {TEXT} DEFAULT '',
        payload       {JSON},
        provider      {TEXT} DEFAULT '',
        model         {TEXT} DEFAULT '',
        created_at    {TS} NOT NULL,
        UNIQUE (workspace_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deliveries (
        id          {PK},
        digest_id   {BIGINT} NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
        channel     {TEXT} NOT NULL,
        target      {TEXT} DEFAULT '',
        status      {TEXT} DEFAULT 'pending',
        error       {TEXT} DEFAULT '',
        sent_at     {TS}
    )
    """,
    # A run is resumable. `stage` and `cursor` are what let a serverless
    # invocation stop at its deadline and the next one pick up mid pipeline
    # instead of starting the whole thing again.
    """
    CREATE TABLE IF NOT EXISTS runs (
        id            {PK},
        workspace_id  {BIGINT} NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        trigger       {TEXT} DEFAULT 'manual',
        status        {TEXT} DEFAULT 'pending',
        stage         {TEXT} DEFAULT '',
        cursor        {JSON},
        stats         {JSON},
        log           {JSON},
        error         {TEXT} DEFAULT '',
        started_at    {TS} NOT NULL,
        updated_at    {TS} NOT NULL,
        finished_at   {TS}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS usage (
        id             {PK},
        run_id         {BIGINT},
        workspace_id   {BIGINT},
        provider       {TEXT} NOT NULL,
        model          {TEXT} DEFAULT '',
        kind           {TEXT} DEFAULT 'llm',
        operation      {TEXT} DEFAULT '',
        input_tokens   {INT} DEFAULT 0,
        output_tokens  {INT} DEFAULT 0,
        units          {INT} DEFAULT 0,
        cost_usd       {REAL} DEFAULT 0,
        created_at     {TS} NOT NULL
    )
    """,
    # Small durable key value store for things that are state rather than data:
    # a cached Reddit OAuth token, the last successful run marker, a lock held
    # across overlapping cron invocations.
    """
    CREATE TABLE IF NOT EXISTS kv (
        key         {TEXT} PRIMARY KEY,
        value       {TEXT},
        expires_at  {TS},
        updated_at  {TS} NOT NULL
    )
    """,
)


INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_sources_workspace ON sources (workspace_id, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_articles_workspace_pub ON articles (workspace_id, published_at)",
    "CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles (workspace_id, fetched_at)",
    "CREATE INDEX IF NOT EXISTS idx_articles_simhash ON articles (workspace_id, simhash)",
    "CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles (workspace_id, content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_articles_theme ON articles (workspace_id, theme)",
    "CREATE INDEX IF NOT EXISTS idx_articles_source ON articles (source_id)",
    "CREATE INDEX IF NOT EXISTS idx_articles_duplicate ON articles (duplicate_of)",
    "CREATE INDEX IF NOT EXISTS idx_events_workspace ON events (workspace_id, last_seen)",
    "CREATE INDEX IF NOT EXISTS idx_events_theme ON events (workspace_id, theme)",
    "CREATE INDEX IF NOT EXISTS idx_event_articles_article ON event_articles (article_id)",
    "CREATE INDEX IF NOT EXISTS idx_entities_workspace ON entities (workspace_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_article_entities_entity ON article_entities (entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_digests_workspace ON digests (workspace_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs (workspace_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status)",
    "CREATE INDEX IF NOT EXISTS idx_usage_run ON usage (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage (created_at)",
)


def create_all(db: Database) -> None:
    with db.connect() as conn:
        for statement in TABLES:
            conn.execute(render_ddl(statement.strip(), db.dialect))
        for statement in INDEXES:
            conn.execute(statement)
