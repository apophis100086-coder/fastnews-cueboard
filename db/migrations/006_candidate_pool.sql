-- Reversible membership, separate from source evidence and decision history.
CREATE TABLE fn_candidate_pool (
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    event_id TEXT NOT NULL REFERENCES fn_events(event_id),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    snapshot_json TEXT,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(edition_id,event_id)
);
-- Preserve explicitly imported/adopted materials from earlier versions.
-- Automatically read public full text is not an adopted candidate.
INSERT OR IGNORE INTO fn_candidate_pool
SELECT ee.edition_id,ee.event_id,1,NULL,MIN(d.imported_at),MAX(d.imported_at)
FROM fn_edition_evidence ee JOIN fn_evidence e USING(evidence_id)
JOIN fn_documents d USING(document_id)
WHERE d.local_path LIKE 'reading:%' OR d.local_path LIKE 'user:%' OR d.privacy_class='private_local'
GROUP BY ee.edition_id,ee.event_id;
INSERT OR IGNORE INTO fn_candidate_pool
SELECT edition_id,selected_event_id,1,NULL,MIN(created_at),MAX(created_at)
FROM fn_decisions WHERE selected_event_id IS NOT NULL
GROUP BY edition_id,selected_event_id;
