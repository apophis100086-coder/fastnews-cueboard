-- Bounded research and provider audit. No human decisions are written here.
CREATE TABLE fn_research_runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('live','fixture')),
    product_code TEXT NOT NULL REFERENCES fn_products(code),
    edition_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed')),
    limits_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    report_json TEXT
);
CREATE TABLE fn_provider_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES fn_research_runs(run_id),
    kind TEXT NOT NULL CHECK(kind IN ('search','llm','fulltext')),
    provider TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    event_id TEXT,
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('reserved','success','failed','invalid_output')),
    reserved_input INTEGER NOT NULL CHECK(reserved_input >= 0),
    reserved_output INTEGER NOT NULL CHECK(reserved_output >= 0),
    reserved_cost REAL CHECK(reserved_cost >= 0),
    actual_input INTEGER CHECK(actual_input >= 0),
    actual_output INTEGER CHECK(actual_output >= 0),
    actual_cost REAL CHECK(actual_cost >= 0),
    usage_status TEXT NOT NULL DEFAULT 'reserved',
    error_code TEXT
);
CREATE INDEX fn_provider_calls_run_idx ON fn_provider_calls(run_id,kind);
CREATE TABLE fn_call_cache (
    cache_key TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('live','fixture')),
    kind TEXT NOT NULL CHECK(kind IN ('search','llm','fulltext')),
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(cache_key,mode)
);
