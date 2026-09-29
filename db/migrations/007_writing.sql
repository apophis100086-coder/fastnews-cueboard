-- Human-approved writing is separate from research and human selection history.
CREATE TABLE fn_writing_runs (
 run_id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 request_hash TEXT NOT NULL,
 edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
 decision_revision INTEGER NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('outline','draft')),
 profile_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','timed_out','interrupted')),
 input_json TEXT NOT NULL,
 version_id TEXT,
 usage_json TEXT,
 error_code TEXT,
 created_at TEXT NOT NULL,
 deadline_at TEXT NOT NULL,
 finished_at TEXT
);
CREATE TABLE fn_writing_versions (
 version_id TEXT PRIMARY KEY,
 edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
 decision_revision INTEGER NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('outline','confirmed_outline','draft')),
 parent_id TEXT REFERENCES fn_writing_versions(version_id),
 run_id TEXT REFERENCES fn_writing_runs(run_id),
 request_id TEXT UNIQUE,
 input_hash TEXT NOT NULL,
 content_json TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,
 checks_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX fn_writing_edition ON fn_writing_versions(edition_id,created_at);
