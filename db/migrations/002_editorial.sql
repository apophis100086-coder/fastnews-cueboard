-- M3 is additive. Existing metadata, discoveries and user records are retained.
CREATE TABLE fn_editions (
    edition_id TEXT PRIMARY KEY,
    product_code TEXT NOT NULL REFERENCES fn_products(code),
    edition_date TEXT NOT NULL,
    news_cutoff TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_fixture INTEGER NOT NULL CHECK(is_fixture IN (0,1))
);
CREATE TABLE fn_documents (
    document_id TEXT PRIMARY KEY,
    local_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    privacy_class TEXT NOT NULL CHECK(privacy_class IN ('public','private_local')),
    is_fixture INTEGER NOT NULL CHECK(is_fixture IN (0,1))
);
CREATE TABLE fn_evidence (
    evidence_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES fn_documents(document_id),
    source_id TEXT REFERENCES fn_sources(source_id),
    data_json TEXT NOT NULL
);
CREATE TABLE fn_edition_evidence (
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    event_id TEXT NOT NULL REFERENCES fn_events(event_id),
    evidence_id TEXT NOT NULL REFERENCES fn_evidence(evidence_id),
    PRIMARY KEY(edition_id,event_id,evidence_id)
);
CREATE TABLE fn_facts (
    fact_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES fn_events(event_id),
    claim_fingerprint TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE fn_fact_evidence (
    fact_id TEXT NOT NULL REFERENCES fn_facts(fact_id),
    evidence_id TEXT NOT NULL REFERENCES fn_evidence(evidence_id),
    PRIMARY KEY(fact_id,evidence_id)
);
CREATE TABLE fn_edition_facts (
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    fact_id TEXT NOT NULL REFERENCES fn_facts(fact_id),
    PRIMARY KEY(edition_id,fact_id)
);
CREATE TABLE fn_product_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    product_code TEXT NOT NULL REFERENCES fn_products(code),
    document_id TEXT NOT NULL REFERENCES fn_documents(document_id),
    data_json TEXT NOT NULL
);
CREATE TABLE fn_history (
    history_doc_id TEXT PRIMARY KEY,
    product_code TEXT NOT NULL REFERENCES fn_products(code),
    document_id TEXT NOT NULL REFERENCES fn_documents(document_id),
    publication_time TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE fn_assessments (
    candidate_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    event_id TEXT NOT NULL REFERENCES fn_events(event_id),
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE fn_decisions (
    decision_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    selected_event_id TEXT REFERENCES fn_events(event_id),
    actor_type TEXT NOT NULL CHECK(actor_type='human'),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(edition_id,revision)
);
CREATE TABLE fn_exports (
    export_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES fn_editions(edition_id),
    decision_id TEXT NOT NULL REFERENCES fn_decisions(decision_id),
    data_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX fn_idx_edfacts_fact ON fn_edition_facts(fact_id);
CREATE INDEX fn_idx_assessment_edition ON fn_assessments(edition_id,event_id);
CREATE TRIGGER fn_check_edition_mode BEFORE INSERT ON fn_editions
WHEN NEW.is_fixture != ((SELECT value FROM fn_meta WHERE key='mode')='fixture')
BEGIN
    SELECT RAISE(ABORT,'edition mode differs from database mode');
END;
CREATE TRIGGER fn_check_document_mode BEFORE INSERT ON fn_documents
WHEN NEW.is_fixture != ((SELECT value FROM fn_meta WHERE key='mode')='fixture')
BEGIN
    SELECT RAISE(ABORT,'document mode differs from database mode');
END;
CREATE TRIGGER fn_frozen_edition BEFORE UPDATE ON fn_editions
BEGIN
    SELECT RAISE(ABORT,'Edition is immutable; create a new Edition');
END;
CREATE TRIGGER fn_frozen_decision BEFORE UPDATE ON fn_decisions
BEGIN
    SELECT RAISE(ABORT,'Decision history is immutable; append a revision');
END;
CREATE TRIGGER fn_frozen_export BEFORE UPDATE ON fn_exports
BEGIN
    SELECT RAISE(ABORT,'Export is immutable; create a new export');
END;
