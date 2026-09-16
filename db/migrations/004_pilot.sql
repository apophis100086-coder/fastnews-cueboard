-- Explicit human trial feedback, counted by unique product/Edition day.
CREATE TABLE fn_pilot_feedback (
    feedback_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('live','fixture')),
    product_code TEXT NOT NULL REFERENCES fn_products(code),
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    research_run_id TEXT NOT NULL REFERENCES fn_research_runs(run_id),
    trial_date TEXT NOT NULL,
    feedback_date TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type='human'),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    previous_feedback_id TEXT REFERENCES fn_pilot_feedback(feedback_id),
    version_signature TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(product_code,trial_date,revision)
);
CREATE INDEX fn_pilot_product_idx ON fn_pilot_feedback(product_code,trial_date);
CREATE TRIGGER fn_pilot_feedback_immutable BEFORE UPDATE ON fn_pilot_feedback
BEGIN
    SELECT RAISE(ABORT,'Pilot feedback is immutable; do not rewrite trial history');
END;
CREATE TRIGGER fn_pilot_feedback_mode BEFORE INSERT ON fn_pilot_feedback
WHEN NEW.mode != (SELECT value FROM fn_meta WHERE key='mode')
BEGIN
    SELECT RAISE(ABORT,'Pilot feedback mode differs from database mode');
END;
