-- User preferences and AI reading reports; original source registry is preserved.
CREATE TABLE fn_reader_profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO fn_reader_profiles VALUES('default','默认方案','{"host":"codex","web_search":true}',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
CREATE TABLE fn_source_preferences (
    profile_id TEXT NOT NULL REFERENCES fn_reader_profiles(profile_id),
    source_id TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id,source_id)
);
CREATE TABLE fn_reading_reports (
    report_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES fn_reader_profiles(profile_id),
    kind TEXT NOT NULL CHECK(kind IN ('morning','product')),
    day TEXT NOT NULL,
    edition_id TEXT,
    product_code TEXT,
    host TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed')),
    input_hash TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    usage_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX fn_reading_report_scope ON fn_reading_reports(profile_id,kind,day,edition_id,created_at);
