from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from fastnews.config import load_config
from fastnews.daily import daily_news
from fastnews.db import connect, sync_registry
from fastnews.metadata import normalize_record
from fastnews.pipeline import scan_morning, store_record


class DailyNewsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = load_config()
        self.conn = connect(self.root / "daily.fixture.db", mode="fixture")
        self.addCleanup(self.conn.close)
        sync_registry(self.conn, self.config)
        self.now = "2026-09-15T14:00:00+08:00"

    def save(self, key, *, source="miit", report="2026-09-14", first=None,
             revision=None, retrieved="2026-09-15T03:00:00+00:00", **changes):
        raw = {
            "source_id": source, "url": f"https://fixture.example.invalid/{key}",
            "title": f"合成 AI 新闻 {key}", "lead": "合成列表摘要，尚未核实正文。",
            "report_published_at": report, "first_published_at": first,
            "source_revision_at": revision, "is_fixture": True,
        }
        raw.update(changes)
        with self.conn:
            store_record(self.conn, normalize_record(raw), self.config, retrieved)

    def view(self, day="2026-09-14", **options):
        return daily_news(self.conn, self.config, day, now=options.pop("now", self.now), offline=True, **options)

    def test_publication_date_not_first_seen_statistics_or_effective_day(self):
        self.save("published", first="2026-09-10", effective_at="2026-10-01", data_period_end="2026-08-31")
        self.save("old", report="2026-09-12", effective_at="2026-09-14", data_period_end="2026-09-14")
        result = self.view()
        self.assertEqual(result["counts"]["items"], 1)
        item = result["items"][0]
        self.assertEqual(item["date_basis"], "report_published_at")
        self.assertEqual(item["first_published_at"], "2026-09-10")
        self.assertEqual(item["display_date"], "2026-09-14")
        self.assertEqual(item["date_precision"], "date")
        self.assertEqual(self.view("2026-09-15")["counts"]["items"], 0)

    def test_first_publication_fallback_is_explicit(self):
        self.save("first-only", report=None, first="2026-09-14T23:00:00+08:00")
        item = self.view()["items"][0]
        self.assertEqual(item["date_basis"], "first_published_at")
        self.assertIsNone(item["report_published_at"])
        self.assertEqual(item["date_precision"], "second")
        self.assertTrue(any("首次公开" in value for value in item["warnings"]))

    def test_timestamp_converts_to_shanghai_day_without_changing_original(self):
        self.save("utc-next-day", report="2026-09-14T16:05:00Z")
        self.save("offset-next-day", report="2026-09-14T12:30:00-04:00")
        self.save("utc-same-day", report="2026-09-14T15:59:59Z")
        self.assertEqual(self.view()["counts"]["items"], 1)
        result = self.view("2026-09-15")
        self.assertEqual(result["counts"]["items"], 2)
        self.assertTrue(all(item["display_date"] == "2026-09-15" for item in result["items"]))
        self.assertEqual({item["report_published_at"] for item in result["items"]}, {"2026-09-14T16:05:00Z", "2026-09-14T12:30:00-04:00"})

    def test_unknown_dates_are_separate_and_not_filled_from_other_dates(self):
        self.save("unknown", report=None, first=None, occurred_at="2026-09-14", effective_at="2026-09-14", data_period_end="2026-09-14")
        self.save("unknown", report=None, first=None, title="合成更新版无日期")
        result = self.view()
        self.assertEqual(result["counts"]["items"], 0)
        self.assertEqual(result["counts"]["undated"], 1)
        item = result["undated_items"][0]
        self.assertIsNone(item["display_date"])
        self.assertIsNone(item["date_basis"])
        self.assertEqual(item["title"], "合成更新版无日期")
        self.assertIn("这不表示该日没有新闻", result["notice"])

    def test_unknown_old_version_not_listed_when_latest_publication_is_dated(self):
        self.save("learned-date", report=None)
        self.save("learned-date", report="2026-09-13")
        result = self.view()
        self.assertEqual(result["counts"]["items"], 0)
        self.assertEqual(result["counts"]["undated"], 0)

    def test_later_revision_uses_saved_older_version_and_does_not_leak_new_title(self):
        self.save("revised", revision="2026-09-14T09:00:00+08:00", title="合成原版", retrieved="2026-09-14T03:00:00Z")
        self.save("revised", revision="2026-09-14T12:00:00+08:00", title="合成当日下午版", retrieved="2026-09-14T05:00:00Z")
        self.save("revised", revision="2026-09-15T09:00:00+08:00", title="合成次日更正版")
        result = self.view()
        self.assertEqual(result["counts"]["items"], 1)
        self.assertEqual(result["items"][0]["title"], "合成当日下午版")
        self.assertEqual(result["items"][0]["replay_state"], "saved_before_day_end")
        self.assertEqual(result["counts"]["historical_gaps"], 0)

    def test_missing_old_revision_is_a_gap_not_an_item(self):
        self.save("no-old-copy", revision="2026-09-15")
        result = self.view()
        self.assertEqual(result["counts"]["items"], 0)
        self.assertEqual(result["counts"]["historical_gaps"], 1)
        self.assertNotIn("title", result["historical_gaps"][0])
        self.assertIn("后来修订", result["notice"])

    def test_unknown_revision_and_late_acquisition_do_not_claim_historical_snapshot(self):
        self.save("unknown-revision")
        self.save("known-revision", revision="2026-09-14T09:00:00+08:00")
        items = self.view()["items"]
        self.assertEqual({item["replay_state"] for item in items}, {"revision_time_unknown", "source_revision_only"})
        self.assertTrue(all(any("快照" in warning for warning in item["warnings"]) for item in items))

    def test_dedup_retains_all_sources_topics_and_events(self):
        self.save("shared", source="miit", attribution_chain=[{"source": "某发布机构", "url": "https://fixture.example.invalid/origin"}])
        self.save("shared", source="nda", attribution_chain=[{"source": "转引工信部"}])
        result = self.view()
        self.assertEqual(result["counts"]["items"], 1)
        self.assertEqual(result["counts"]["sources_with_items"], 2)
        item = result["items"][0]
        self.assertEqual({source["source_id"] for source in item["source_chain"]}, {"miit", "nda"})
        self.assertIn("AI", item["topics"])
        self.assertTrue(item["event_id"])
        self.assertTrue(item["pending"])
        self.assertTrue(all(not source["attribution_chain_verified"] for source in item["source_chain"]))
        self.assertTrue(item["source_chain"][0]["attribution_chain"])

    def test_mode_date_and_clock_validation(self):
        for day in ("2026-09-16", "not-a-date", "20260914", "2026-W38-1", None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.view(day)
        with self.assertRaises(ValueError):
            daily_news(self.conn, self.config, "2026-09-14", now=self.now)
        with self.assertRaises(ValueError):
            self.view(now="2026-09-15T09:00:00")

    def test_fixture_default_clock_is_last_working_clock(self):
        with self.conn:
            self.conn.execute("INSERT INTO fn_scan_batches(run_id, mode, started_at, status, parameters_json) VALUES(?,?,?,?,?)",
                              ("clock", "fixture", "2026-09-15T00:00:00Z", "success", json.dumps({"scan_now": self.now})))
        result = daily_news(self.conn, self.config, "2026-09-15", offline=True)
        self.assertEqual(result["date"], "2026-09-15")
        with self.assertRaises(ValueError):
            daily_news(self.conn, self.config, "2026-09-16", offline=True)

    def test_failure_and_no_news_are_visible_separately_without_network(self):
        with closing(connect(self.root / "live.db", mode="live")) as live:
            sync_registry(live, self.config)
            with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")), patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
                scan_morning(live, self.config, no_network=True)
                result = daily_news(live, self.config, "2026-09-14", now=self.now)
        self.assertEqual(result["counts"]["items"], 0)
        self.assertEqual(result["coverage"]["latest_scan"]["status"], "unavailable")
        self.assertEqual(result["coverage"]["connected_sources"], 0)
        self.assertFalse(result["coverage"]["complete"])
        failed = next(source for source in result["coverage"]["source_statuses"] if source["source_id"] == "miit")
        self.assertEqual(failed["last_run_status"], "unavailable")
        self.assertEqual(failed["error_code"], "adapter_not_connected")
        self.assertEqual(failed["day_items"], 0)
        self.assertIn("来源访问失败", result["notice"])

    def test_view_is_read_only_and_requires_no_edition(self):
        self.save("standalone")
        before = self.conn.total_changes
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")), patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            result = self.view()
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_editions").fetchone()[0], 0)
        self.assertEqual(result["counts"]["items"], 1)
        self.assertEqual(result["coverage"]["registry_total"], 26)
        self.assertEqual(result["coverage"]["morning_enabled"], 21)
        self.assertTrue(result["is_fixture"])

    def test_tracking_urls_do_not_inflate_per_source_daily_counts(self):
        self.save("tracked", url="https://fixture.example.invalid/tracked?utm_source=a")
        self.save("tracked", url="https://fixture.example.invalid/tracked?utm_source=b")
        result = self.view()
        self.assertEqual(result["counts"]["items"], 1)
        self.assertEqual(len(result["items"][0]["source_chain"]), 2)
        miit = next(source for source in result["coverage"]["source_statuses"] if source["source_id"] == "miit")
        self.assertEqual(miit["day_items"], 1)


if __name__ == "__main__":
    unittest.main()
