"""Calendar-day acquisition is bounded and never resets incremental state."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastnews.adapters import DiscoveryPage
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.metadata import parse_instant
from fastnews.pipeline import scan_morning


class ScanDateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.db = self.root / "dated.fixture.db"
        self.conn = connect(self.db, mode="fixture")
        self.addCleanup(self.conn.close)
        sync_registry(self.conn, self.config)
        self.fixture = self.root / "date.synthetic.json"

    def record(self, key, published, **extra):
        return {"source_id": "trendforce", "url": f"https://fixture.example.invalid/{key}",
                "title": f"[合成测试] 半导体线索 {key}", "lead": "仅用于离线验收",
                "first_published_at": published, "report_published_at": published,
                "origin_role": "unknown", **extra}

    def write_fixture(self, records, *, sources=None):
        self.fixture.write_text(json.dumps({"is_fixture": True,
            "reference_now": "2026-09-15T08:15:00+08:00",
            "sources": sources or {"trendforce": {"records": records}}}, ensure_ascii=False), encoding="utf-8")

    def scan(self, **kwargs):
        return scan_morning(self.conn, self.config, offline=True, fixture_path=self.fixture, **kwargs)

    def stored(self):
        return {row["title"]: json.loads(row["metadata_json"])
                for row in self.conn.execute("SELECT title,metadata_json FROM fn_article_versions")}

    def source_state(self, conn=None):
        return [tuple(row) for row in (conn or self.conn).execute(
            "SELECT source_id,cursor,resume_json,coverage_start,coverage_end,truncation_reason FROM fn_sources ORDER BY source_id")]

    def test_shanghai_day_excludes_adjacent_midnights_and_keeps_date_precision(self):
        self.write_fixture([
            self.record("before", "2026-09-13T15:59:59.999999+00:00"),
            self.record("start", "2026-09-13T16:00:00+00:00"),
            self.record("last", "2026-09-14T15:59:59.999999+00:00"),
            self.record("after", "2026-09-14T16:00:00+00:00"),
            self.record("date_only", "2026-09-14"),
        ])
        with patch.object(socket, "create_connection", side_effect=AssertionError("offline network forbidden")):
            result = self.scan(collection_date="2026-09-14")
        self.assertEqual({metadata["url"].rsplit("/", 1)[1] for metadata in self.stored().values()},
                         {"start", "last", "date_only"})
        self.assertEqual(next(m for m in self.stored().values() if "date_only" in m["url"])["report_published_at"], "2026-09-14")
        self.assertEqual(result["parameters"]["window_start"], "2026-09-13T16:00:00+00:00")
        self.assertEqual(result["parameters"]["window_end"], "2026-09-14T15:59:59.999999+00:00")
        self.assertEqual(result["parameters"]["date_timezone"], "Asia/Shanghai")
        self.assertEqual(result["usage"]["llm_requests"], 0)
        self.assertEqual(result["usage"]["network_requests"], 0)

    def test_today_ends_at_scan_clock_without_fetching_later_reports(self):
        self.write_fixture([self.record("available", "2026-09-15T08:00:00+08:00"),
                            self.record("future", "2026-09-15T09:00:00+08:00")])
        result = self.scan(collection_date="2026-09-15")
        self.assertEqual(len(self.stored()), 1)
        self.assertIn("available", next(iter(self.stored())))
        self.assertEqual(result["parameters"]["window_end"], "2026-09-15T00:15:00+00:00")
        self.assertFalse(result["collection"]["historical_date"])

    def test_invalid_and_future_dates_leave_no_scan_batch(self):
        self.write_fixture([])
        for selected in ("2026-9-14", "20260914", "2026-02-30", "2026-09-16", "", 1, True,
                         "2026-09-14T00:00:00+08:00", "２０２６-０９-１４"):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.scan(collection_date=selected)
        for options in ({"since_hours": 24}, {"bootstrap_hours": 72}):
            with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                self.scan(collection_date="2026-09-14", **options)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_scan_batches").fetchone()[0], 0)

    def test_independent_day_does_not_read_or_overwrite_saved_resume_and_coverage(self):
        self.write_fixture([self.record("older", "2026-09-14T10:00:00+08:00")])
        # Even an unfinished newer incremental window must not redirect the selected date.
        saved_resume = json.dumps({"offset": 2, "dataset_hash": "other-window",
                                   "window_start": "2026-09-15T00:00:00+08:00",
                                   "window_end": "2026-09-15T08:15:00+08:00"})
        with self.conn:
            self.conn.execute("UPDATE fn_sources SET cursor=?,resume_json=?,coverage_start=?,coverage_end=?,truncation_reason=?",
                ("2026-09-15T08:15:00+08:00", saved_resume, "2026-09-12T00:00:00+08:00",
                 "2026-09-13T00:00:00+08:00", "item_limit"))
        before = self.source_state()
        result = self.scan(collection_date="2026-09-14")
        self.assertEqual(before, self.source_state())
        self.assertEqual(result["articles_new"], 1)
        self.assertTrue(all(row["cursor_before"] == row["cursor_after"] for row in result["sources"]))
        self.assertTrue(result["collection"]["default_cursor_preserved"])

    def test_truncation_and_repeat_are_bounded_without_creating_global_resume(self):
        self.write_fixture([self.record(str(i), "2026-09-14T10:00:00+08:00") for i in range(4)])
        before = self.source_state()
        first = self.scan(collection_date="2026-09-14", max_items=2)
        second = self.scan(collection_date="2026-09-14", max_items=2)
        self.assertEqual(first["articles_new"], 2)
        self.assertEqual(second["articles_new"], 0)
        self.assertEqual(second["article_versions_new"], 0)
        source = next(row for row in second["sources"] if row["source_id"] == "trendforce")
        self.assertEqual(source["status"], "truncated")
        self.assertTrue(source["coverage_incomplete"])
        self.assertFalse(source["resume_persisted"])
        self.assertEqual(before, self.source_state())

    def test_failure_is_distinct_from_empty_and_does_not_stop_other_sources(self):
        self.write_fixture([], sources={
            "trendforce": {"records": [self.record("available", "2026-09-14")]},
            "nda": {"error": "timeout"}, "miit": {"records": []}})
        result = self.scan(collection_date="2026-09-14")
        statuses = {row["source_id"]: row for row in result["sources"]}
        self.assertEqual(statuses["trendforce"]["status"], "success")
        self.assertEqual(statuses["nda"]["status"], "failed")
        self.assertEqual(statuses["nda"]["error_code"], "timeout")
        self.assertEqual(statuses["miit"]["status"], "empty")
        self.assertEqual(statuses["fda"]["status"], "skipped_disabled")
        self.assertEqual(result["coverage"]["configured"], 26)
        self.assertEqual(result["coverage"]["expected_morning"], 21)
        self.assertFalse(result["collection"]["complete_historical_snapshot"])
        self.assertEqual(result["status"], "partial")

    def test_unknown_publication_remains_unknown_and_retrieval_uses_actual_clock(self):
        self.write_fixture([self.record("unknown", None, effective_at="2026-09-14",
                                        data_period_start="2026-09-14", data_period_end="2026-09-14")])
        actual_start = datetime.now(timezone.utc)
        self.scan(collection_date="2026-09-14")
        metadata = next(iter(self.stored().values()))
        self.assertIsNone(metadata["first_published_at"])
        self.assertIsNone(metadata["report_published_at"])
        self.assertGreaterEqual(parse_instant(metadata["retrieved_at"]), actual_start)
        self.assertEqual(metadata["effective_at"], "2026-09-14")

    def test_date_collection_does_not_prevent_later_normal_incremental_scan(self):
        self.write_fixture([self.record("yesterday", "2026-09-14T10:00:00+08:00"),
                            self.record("today", "2026-09-15T07:00:00+08:00")])
        self.scan(collection_date="2026-09-14")
        self.assertIsNone(self.conn.execute("SELECT cursor FROM fn_sources WHERE source_id='trendforce'").fetchone()[0])
        incremental = self.scan()
        self.assertEqual(incremental["articles_new"], 1)
        source = next(row for row in incremental["sources"] if row["source_id"] == "trendforce")
        self.assertEqual(source["window_basis"], "bootstrap")
        self.assertEqual(source["cursor_after"], incremental["parameters"]["scan_now"])

    def test_simulated_live_reader_updates_health_but_preserves_incremental_fields(self):
        class EmptyReader:
            def discover(self, source_id, start, end, limit, resume=None):
                return DiscoveryPage([], False, None, 0, coverage_reason="listing_scope_incomplete")
        with closing(connect(self.root / "health.db", mode="live")) as conn:
            sync_registry(conn, self.config)
            before = self.source_state(conn)
            result = scan_morning(conn, self.config, collection_date="2026-09-14",
                                  now="2026-09-15T08:15:00+08:00", adapter=EmptyReader(), source_ids=["trendforce"])
            self.assertEqual(before, self.source_state(conn))
            health = conn.execute("SELECT capability_status,last_success_at FROM fn_sources WHERE source_id='trendforce'").fetchone()
            self.assertEqual(health["capability_status"], "ready_metadata")
            self.assertIsNotNone(health["last_success_at"])
            self.assertTrue(result["coverage"]["coverage_incomplete"])
            self.assertIn("不能复原完整历史", result["collection"]["notice"])

    def test_cli_date_reaches_collection_with_fixture_banner(self):
        process = subprocess.run([sys.executable, "-m", "fastnews", "scan-morning", "--offline",
            "--db", str(self.root / "cli.fixture.db"), "--date", "2026-09-14", "--summary"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertTrue(payload["is_fixture"])
        self.assertEqual(payload["result"]["parameters"]["collection_date"], "2026-09-14")
        self.assertGreater(payload["result"]["articles_new"], 0)


if __name__ == "__main__":
    unittest.main()
