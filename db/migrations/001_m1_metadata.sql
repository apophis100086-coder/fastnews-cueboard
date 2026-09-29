-- M1 owns only fn_* tables. Original V1 tables are retained without conversion.
CREATE TABLE fn_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    backup_path TEXT
);
CREATE TABLE fn_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE fn_sources (
    source_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    capability_status TEXT NOT NULL DEFAULT 'needs_probe',
    adapter_name TEXT,
    access_review TEXT NOT NULL DEFAULT 'not_checked',
    validated_discovery_url TEXT,
    last_probe_at TEXT,
    last_success_at TEXT,
    cursor TEXT,
    coverage_start TEXT,
    coverage_end TEXT,
    truncation_reason TEXT,
    resume_json TEXT
);
CREATE TABLE fn_products (
    code TEXT PRIMARY KEY,
    config_json TEXT NOT NULL
);
CREATE TABLE fn_topics (
    topic_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL
);
CREATE TABLE fn_scan_batches (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('live', 'fixture')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT
);
CREATE TABLE fn_source_runs (
    run_id TEXT NOT NULL REFERENCES fn_scan_batches(run_id),
    source_id TEXT NOT NULL REFERENCES fn_sources(source_id),
    status TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0 CHECK(items_seen >= 0),
    articles_new INTEGER NOT NULL DEFAULT 0 CHECK(articles_new >= 0),
    coverage_incomplete INTEGER NOT NULL DEFAULT 0 CHECK(coverage_incomplete IN (0, 1)),
    error_code TEXT,
    cursor_before TEXT,
    cursor_after TEXT,
    resume_json TEXT,
    PRIMARY KEY(run_id, source_id)
);
CREATE TABLE fn_articles (
    article_id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE fn_article_versions (
    version_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES fn_articles(article_id),
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    lead TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    retrieved_at TEXT NOT NULL,
    is_fixture INTEGER NOT NULL CHECK(is_fixture IN (0, 1)),
    UNIQUE(article_id, content_hash)
);
CREATE TABLE fn_article_sources (
    version_id TEXT NOT NULL REFERENCES fn_article_versions(version_id),
    source_id TEXT NOT NULL REFERENCES fn_sources(source_id),
    source_url TEXT NOT NULL,
    origin_role TEXT NOT NULL DEFAULT 'unknown',
    attribution_json TEXT NOT NULL DEFAULT '[]',
    retrieved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(version_id, source_id, source_url)
);
CREATE TABLE fn_article_tags (
    version_id TEXT NOT NULL REFERENCES fn_article_versions(version_id),
    topic_id TEXT NOT NULL REFERENCES fn_topics(topic_id),
    matches_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(version_id, topic_id)
);
CREATE TABLE fn_events (
    event_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    phase TEXT NOT NULL,
    related_key TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE fn_event_articles (
    event_id TEXT NOT NULL REFERENCES fn_events(event_id),
    version_id TEXT NOT NULL REFERENCES fn_article_versions(version_id),
    PRIMARY KEY(event_id, version_id)
);
CREATE TABLE fn_usage (
    run_id TEXT PRIMARY KEY REFERENCES fn_scan_batches(run_id),
    llm_requests INTEGER NOT NULL DEFAULT 0 CHECK(llm_requests >= 0),
    search_requests INTEGER NOT NULL DEFAULT 0 CHECK(search_requests >= 0),
    network_requests INTEGER NOT NULL DEFAULT 0 CHECK(network_requests >= 0),
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
    cost REAL CHECK(cost IS NULL OR cost >= 0),
    cost_status TEXT NOT NULL DEFAULT 'unknown',
    elapsed_seconds REAL NOT NULL DEFAULT 0 CHECK(elapsed_seconds >= 0)
);
CREATE INDEX fn_idx_source_runs_source ON fn_source_runs(source_id);
CREATE INDEX fn_idx_versions_article ON fn_article_versions(article_id);
CREATE INDEX fn_idx_article_sources_source ON fn_article_sources(source_id);
CREATE INDEX fn_idx_event_articles_version ON fn_event_articles(version_id);
CREATE TRIGGER fn_check_batch_mode_insert BEFORE INSERT ON fn_scan_batches
WHEN NEW.mode != (SELECT value FROM fn_meta WHERE key = 'mode')
BEGIN
    SELECT RAISE(ABORT, 'scan batch mode differs from database mode');
END;
CREATE TRIGGER fn_check_batch_mode_update BEFORE UPDATE OF mode ON fn_scan_batches
WHEN NEW.mode != (SELECT value FROM fn_meta WHERE key = 'mode')
BEGIN
    SELECT RAISE(ABORT, 'scan batch mode differs from database mode');
END;
CREATE TRIGGER fn_check_version_mode_insert BEFORE INSERT ON fn_article_versions
WHEN NEW.is_fixture != ((SELECT value FROM fn_meta WHERE key = 'mode') = 'fixture')
BEGIN
    SELECT RAISE(ABORT, 'article version mode differs from database mode');
END;
CREATE TRIGGER fn_check_version_mode_update BEFORE UPDATE OF is_fixture ON fn_article_versions
WHEN NEW.is_fixture != ((SELECT value FROM fn_meta WHERE key = 'mode') = 'fixture')
BEGIN
    SELECT RAISE(ABORT, 'article version mode differs from database mode');
END;
