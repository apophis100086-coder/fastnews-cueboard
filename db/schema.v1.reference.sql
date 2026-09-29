PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    home_url TEXT NOT NULL,
    discovery_url TEXT,
    discovery_mode TEXT NOT NULL,
    tier TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    morning_scan INTEGER NOT NULL DEFAULT 1,
    config_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    items_discovered INTEGER NOT NULL DEFAULT 0,
    items_inserted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    cursor_value TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    raw_url TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    section TEXT,
    lead TEXT,
    full_text TEXT,
    content_hash TEXT,
    language TEXT DEFAULT 'zh',
    tier TEXT,
    metadata_json TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at
ON articles(published_at);

CREATE INDEX IF NOT EXISTS idx_articles_source_id
ON articles(source_id);

CREATE TABLE IF NOT EXISTS article_tags (
    article_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY(article_id, tag),
    FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    event_time TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    summary TEXT,
    primary_topic TEXT,
    event_hash TEXT UNIQUE,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS event_articles (
    event_id TEXT NOT NULL,
    article_id INTEGER NOT NULL,
    relation_type TEXT DEFAULT 'supporting',
    PRIMARY KEY(event_id, article_id),
    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
    FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_tags (
    event_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY(event_id, tag),
    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS products (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    product_code TEXT NOT NULL,
    event_id TEXT NOT NULL,
    relevance_score REAL,
    freshness_score REAL,
    evidence_score REAL,
    source_quality_score REAL,
    novelty_score REAL,
    recent_overlap_score REAL,
    rank_label TEXT,
    why_relevant TEXT,
    hard_facts_json TEXT,
    source_chain_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_code) REFERENCES products(code),
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE INDEX IF NOT EXISTS idx_daily_candidates_date_product
ON daily_candidates(date, product_code);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
