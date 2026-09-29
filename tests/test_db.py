"""Offline storage contracts, including real SQLite backup/rollback behavior."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastnews import db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "metadata.sqlite3"

    def open(self, mode="live"):
        conn = db.connect(self.path, mode)
        self.addCleanup(conn.close)
        return conn

    def config(self):
        return {"sources": {"source_a": {"id": "source_a", "name": "Fixture Source"}},
                "topics": {"topic_a": {"id": "topic_a", "keywords": ["test"]}},
                "products": {"513130": {"code": "513130", "name": "Fixture Product"}},
                "source_runtime": {"source_a": {"capability_status": "needs_probe", "adapter_name": None,
                                                "access_review": "not_checked"}}}

    def make_legacy(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript((Path(__file__).resolve().parents[1] / "db/schema.v1.reference.sql").read_text(encoding="utf-8"))
            conn.execute("INSERT INTO sources(id,name,home_url,discovery_mode,tier) VALUES('legacy','Old source','https://example.org','manual','T1')")
            conn.execute("INSERT INTO products(code,name,config_json) VALUES('513130','Old product','{}')")
            conn.execute("INSERT INTO scan_runs(source_id,started_at,status) VALUES('legacy','2026-01-01T00:00:00Z','empty')")
            conn.execute("INSERT INTO articles(source_id,canonical_url,raw_url,title) VALUES('legacy','https://example.org/a','https://example.org/a','Old article')")
            conn.execute("INSERT INTO article_tags(article_id,tag) VALUES(1,'old-topic')")
            conn.execute("INSERT INTO events(id,title,event_hash) VALUES('old-event','Old event','old-hash')")
            conn.execute("INSERT INTO event_articles(event_id,article_id) VALUES('old-event',1)")
            conn.execute("INSERT INTO event_tags(event_id,tag) VALUES('old-event','old-topic')")
            conn.execute("INSERT INTO daily_candidates(date,product_code,event_id,relevance_score) VALUES('2026-01-01','513130','old-event',2.5)")
            conn.execute("INSERT INTO usage_log(run_type,estimated_cost) VALUES('old',NULL)")
            conn.commit()
            return {table: conn.execute('SELECT * FROM "' + table + '"').fetchall() for table in db.LEGACY_TABLES}

    def test_empty_init_and_repeat_are_idempotent(self):
        conn = self.open()
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertIsInstance(conn.execute("SELECT * FROM fn_meta").fetchone(), sqlite3.Row)
        db.initialize(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_schema_migrations").fetchone()[0], len(db.migration_files()))
        self.assertEqual(conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0], "live")
        self.assertIsNone(conn.execute("SELECT backup_path FROM fn_schema_migrations").fetchone()[0])
        self.assertEqual(list(self.path.parent.glob("*.backup-*")), [])

    def test_legacy_rows_preserved_backup_is_pre_migration(self):
        before = self.make_legacy()
        conn = self.open()
        for table, rows in before.items():
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM "' + table + '"')], rows)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_articles").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT value FROM fn_meta WHERE key='legacy_import_status'").fetchone()[0], "preserved_not_converted")
        counts = json.loads(conn.execute("SELECT value FROM fn_meta WHERE key='legacy_tables_json'").fetchone()[0])
        self.assertEqual(counts, {table: 1 for table in db.LEGACY_TABLES})
        backup_path = Path(conn.execute("SELECT backup_path FROM fn_schema_migrations").fetchone()[0])
        self.assertEqual(backup_path.parent, self.path.parent)
        self.assertTrue(backup_path.is_file())
        with closing(sqlite3.connect(backup_path)) as backup:
            self.assertNotIn("fn_meta", db._tables(backup))
            for table, rows in before.items():
                self.assertEqual(backup.execute('SELECT * FROM "' + table + '"').fetchall(), rows)
        db.initialize(conn)
        self.assertEqual(len(list(self.path.parent.glob("*.backup-*"))), 1)

    def test_live_fixture_modes_cannot_be_reopened_as_each_other(self):
        conn = self.open("fixture")
        conn.close()
        with self.assertRaisesRegex(ValueError, "mode mismatch"):
            db.connect(self.path, "live")
        with closing(db.connect(self.path, "fixture")) as check:
            self.assertEqual(check.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0], "fixture")

    def test_mode_constraints_prevent_cross_mode_records(self):
        conn = self.open("fixture")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO fn_scan_batches(run_id,mode,started_at,status) VALUES('run','live','2026-01-01T00:00:00Z','running')")
        conn.execute("INSERT INTO fn_articles(article_id,canonical_url,first_seen_at) VALUES('a','https://example.org','2026-01-01T00:00:00Z')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO fn_article_versions(version_id,article_id,content_hash,title,retrieved_at,is_fixture) VALUES('v','a','hash','title','2026-01-01T00:00:00Z',0)")

    def test_fixture_refuses_legacy_records_with_unknown_provenance(self):
        before = self.make_legacy()
        with self.assertRaisesRegex(ValueError, "unknown provenance"):
            db.connect(self.path, "fixture")
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM articles").fetchall(), before["articles"])
            self.assertNotIn("fn_meta", db._tables(conn))

    def test_invalid_mode_creates_no_database(self):
        with self.assertRaises(ValueError):
            db.connect(self.path, "simulation")
        self.assertFalse(self.path.exists())

    def test_unknown_owned_schema_rejected_without_removal(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TABLE fn_sources(secret TEXT)")
            conn.execute("INSERT INTO fn_sources VALUES('preserve me')")
            conn.commit()
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            db.connect(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT secret FROM fn_sources").fetchone()[0], "preserve me")
            self.assertEqual(db._tables(conn), {"fn_sources"})

    def test_changed_migration_checksum_rejected(self):
        conn = self.open()
        conn.execute("UPDATE fn_schema_migrations SET checksum='tampered'")
        conn.commit()
        with self.assertRaisesRegex(ValueError, "checksum"):
            db.initialize(conn)
        self.assertEqual(conn.execute("SELECT checksum FROM fn_schema_migrations").fetchone()[0], "tampered")

    def test_active_transaction_never_committed_implicitly(self):
        conn = self.open()
        conn.execute("INSERT INTO fn_topics(topic_id,config_json) VALUES('uncommitted','{}')")
        with self.assertRaisesRegex(ValueError, "caller transaction"):
            db.initialize(conn)
        self.assertTrue(conn.in_transaction)
        conn.rollback()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_topics").fetchone()[0], 0)

    def test_migration_failure_rolls_back_ddl_and_preserves_backup(self):
        before = self.make_legacy()
        original = db._apply_migration

        def failing_migration(conn, script):
            original(conn, script)
            conn.execute("INSERT INTO intentionally_missing_table VALUES (1)")

        with patch.object(db, "_apply_migration", failing_migration):
            with self.assertRaises(sqlite3.OperationalError):
                db.connect(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(db._tables(conn), set(db.LEGACY_TABLES))
            self.assertEqual(conn.execute("SELECT * FROM articles").fetchall(), before["articles"])
        self.assertEqual(len(list(self.path.parent.glob("*.backup-*"))), 1)

    def test_registry_sync_preserves_probe_cursor_resume_and_legacy_data(self):
        conn = self.open()
        config = self.config()
        db.sync_registry(conn, config)
        conn.execute("UPDATE fn_sources SET cursor='cursor',resume_json='{}',capability_status='blocked',last_probe_at='probe',last_success_at='success' WHERE source_id='source_a'")
        conn.commit()
        config["sources"]["source_a"]["name"] = "Updated source label"
        for _ in range(2):
            summary = db.sync_registry(conn, config)
        source = conn.execute("SELECT * FROM fn_sources").fetchone()
        self.assertEqual(source["cursor"], "cursor")
        self.assertEqual(source["resume_json"], "{}")
        self.assertEqual(source["capability_status"], "blocked")
        self.assertEqual(source["last_probe_at"], "probe")
        self.assertEqual(source["last_success_at"], "success")
        self.assertEqual(json.loads(source["config_json"])["name"], "Updated source label")
        self.assertEqual(summary["sources"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_sources").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_products").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_topics").fetchone()[0], 1)

    def test_foreign_keys_reject_dangling_relationships(self):
        conn = self.open()
        statements = [
            "INSERT INTO fn_article_versions(version_id,article_id,content_hash,title,retrieved_at,is_fixture) VALUES('v','missing','hash','title','2026-01-01T00:00:00Z',0)",
            "INSERT INTO fn_article_sources(version_id,source_id,source_url) VALUES('missing','missing','https://example.org')",
            "INSERT INTO fn_article_tags(version_id,topic_id) VALUES('missing','missing')",
            "INSERT INTO fn_event_articles(event_id,version_id) VALUES('missing','missing')",
            "INSERT INTO fn_source_runs(run_id,source_id,status) VALUES('missing','missing','failed')",
            "INSERT INTO fn_usage(run_id) VALUES('missing')",
        ]
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(statement)
        conn.rollback()

    def test_fixture_overlay_cannot_claim_real_source_connectivity(self):
        conn = self.open("fixture")
        config = self.config()
        config["source_runtime"]["source_a"].update(
            capability_status="ready_metadata", adapter_name="real_adapter",
            access_review="allowed", validated_discovery_url="https://example.org",
            last_probe_at="2026-01-01T00:00:00Z", last_success_at="2026-01-01T00:00:00Z")
        db.sync_registry(conn, config)
        row = conn.execute("SELECT * FROM fn_sources").fetchone()
        self.assertEqual(row["capability_status"], "needs_probe")
        self.assertEqual(row["access_review"], "not_checked")
        for field in ("adapter_name", "validated_discovery_url", "last_probe_at", "last_success_at"):
            self.assertIsNone(row[field])
        self.assertEqual(config["source_runtime"]["source_a"]["capability_status"], "ready_metadata")

    def test_same_content_and_relationships_are_unique(self):
        conn = self.open()
        db.sync_registry(conn, self.config())
        conn.execute("INSERT INTO fn_articles VALUES('a','https://example.org/a','2026-01-01T00:00:00Z')")
        version = ("v", "a", "hash", "title", "2026-01-01T00:00:00Z", 0)
        conn.execute("INSERT INTO fn_article_versions(version_id,article_id,content_hash,title,retrieved_at,is_fixture) VALUES(?,?,?,?,?,?)", version)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO fn_article_versions(version_id,article_id,content_hash,title,retrieved_at,is_fixture) VALUES(?,?,?,?,?,?)", ("v2",) + version[1:])
        for _ in range(2):
            conn.execute("INSERT OR IGNORE INTO fn_article_sources(version_id,source_id,source_url) VALUES('v','source_a','https://example.org/a')")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_article_versions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_article_sources").fetchone()[0], 1)

    def test_source_discovery_time_is_independent_from_content_version_time(self):
        from fastnews.pipeline import store_record
        from fastnews.routing import list_events

        conn = self.open()
        config = self.config()
        config["sources"]["source_b"] = {"id": "source_b", "name": "Second discovery"}
        config["source_runtime"]["source_b"] = {"capability_status": "needs_probe"}
        db.sync_registry(conn, config)
        record = {"url": "https://example.org/a", "title": "Shared metadata", "lead": "Same content",
                  "source_id": "source_a", "is_fixture": False, "event_key": "shared-event",
                  "first_published_at": "2026-01-01T00:00:00Z"}
        first = "2026-01-02T00:00:00+00:00"
        second = "2026-01-03T00:00:00+00:00"
        with conn:
            store_record(conn, record, config, first)
            store_record(conn, dict(record, source_id="source_b"), config, second)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fn_article_versions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT retrieved_at FROM fn_article_versions").fetchone()[0], first)
        links = {row["source_id"]: row["retrieved_at"] for row in conn.execute("SELECT * FROM fn_article_sources")}
        self.assertEqual(links, {"source_a": first, "source_b": second})
        displayed = {entry["source_id"]: entry["retrieved_at"] for entry in list_events(conn)[0]["source_chain"]}
        self.assertEqual(displayed, links)


if __name__ == "__main__":
    unittest.main()
