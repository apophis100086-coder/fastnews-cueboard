from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from fastnews.adapters import DiscoveryError, FixtureAdapter
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.metadata import parse_instant
from fastnews.pipeline import DEFAULT_FIXTURE, scan_morning
from fastnews.routing import list_events, route_product


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = load_config()
        self.conn = connect(self.root / "test.db", mode="fixture")
        self.addCleanup(self.conn.close)
        sync_registry(self.conn, self.config)
        self.fixture = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))

    def scan(self, **options):
        return scan_morning(self.conn, self.config, offline=True, **options)

    def write_fixture(self, value):
        target = self.root / "custom.synthetic.json"
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return target

    def count(self, table):
        return self.conn.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def test_offline_calls_no_network_and_never_upgrades_sources(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")), patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            result = self.scan()
        self.assertEqual(result["usage"]["llm_requests"], 0)
        self.assertEqual(result["usage"]["network_requests"], 0)
        self.assertEqual(result["usage"]["input_tokens"], 0)
        self.assertEqual(result["usage"]["cost_status"], "not_called")
        self.assertEqual(result["coverage"]["live_connected"], 0)
        states = self.conn.execute("SELECT DISTINCT capability_status,last_success_at FROM fn_sources").fetchall()
        self.assertEqual([tuple(row) for row in states], [("needs_probe", None)])

    def test_repeat_is_idempotent_but_runs_are_audited(self):
        first = self.scan()
        tables = ("fn_articles", "fn_article_versions", "fn_events", "fn_article_sources", "fn_event_articles", "fn_article_tags")
        counts = {table: self.count(table) for table in tables}
        self.assertEqual(first["articles_new"], counts["fn_articles"])
        self.assertEqual(first["article_versions_new"], counts["fn_article_versions"])
        second = self.scan()
        self.assertGreater(first["articles_new"], 0)
        self.assertEqual(second["articles_new"], 0)
        self.assertEqual(second["article_versions_new"], 0)
        self.assertEqual(counts, {table: self.count(table) for table in tables})
        self.assertEqual(self.count("fn_scan_batches"), 2)
        self.assertEqual(self.count("fn_usage"), 2)

    def test_source_failure_and_empty_and_unconnected_are_distinct(self):
        result = self.scan()
        sources = {row["source_id"]: row for row in result["sources"]}
        self.assertEqual(result["status"], "partial")
        self.assertEqual(sources["nda"]["status"], "failed")
        self.assertEqual(sources["nda"]["error_code"], "parse_error")
        self.assertEqual(sources["yicai_overnight"]["status"], "empty")
        self.assertEqual(sources["miit"]["status"], "unavailable")
        self.assertEqual(sources["hkex_news"]["status"], "success")
        self.assertEqual(len(sources), 26)

    def test_cold_bootstrap_includes_yesterday_daytime_and_explicit_18h_warns(self):
        result = self.scan()
        self.assertTrue(all(row["window_basis"] == "bootstrap" for row in result["sources"]))
        self.assertTrue(any("客户验证" in row[0] for row in self.conn.execute("SELECT title FROM fn_article_versions")))
        later = self.scan(since_hours=18)
        self.assertEqual(later["parameters"]["since_hours"], 18)
        self.assertEqual(self.count("fn_articles") > 0, True)
        # Separate cold explicit-window run makes the earlier gap visible.
        with closing(connect(self.root / "short.db", mode="fixture")) as other:
            sync_registry(other, self.config)
            short = scan_morning(other, self.config, offline=True, since_hours=18)
            self.assertTrue(any(row.get("earlier_coverage_unknown") for row in short["sources"]))

    def test_cutoff_is_independent_of_discovery_window(self):
        self.scan()
        result = route_product(self.conn, self.config, "513130", edition_date="2026-09-15",
                               news_cutoff="2026-09-14T23:59:59+08:00", now=parse_instant(self.fixture["reference_now"]))
        outside = result["groups"]["out_of_cutoff"]
        self.assertTrue(any("新增使用数据" in event["title"] for event in outside))
        self.assertTrue(result["groups"]["within_cutoff"])
        self.assertTrue(result["groups"]["pending_time"])
        self.assertIsNone(result["default_selection"])
        self.assertTrue(all(not event["selected"] and event["evidence_state"] == "pending" and not event["facts"]
                            for group in result["groups"].values() for event in group))
        # Routing is read-only and deterministic for the same view.
        before = self.conn.total_changes
        second = route_product(self.conn, self.config, "513130", edition_date="2026-09-15",
                               news_cutoff="2026-09-14T23:59:59+08:00", now=parse_instant(self.fixture["reference_now"]))
        self.assertEqual(result, second)
        self.assertEqual(self.conn.total_changes, before)

    def test_plan_and_cancellation_do_not_merge_or_invent_completion(self):
        self.scan()
        view = route_product(self.conn, self.config, "513530", now=parse_instant(self.fixture["reference_now"]))
        plan = next(event for event in view["groups"]["within_cutoff"] if "公告分红" in event["title"])
        cancel = next(event for event in view["groups"]["out_of_cutoff"] if "取消分红" in event["title"])
        self.assertEqual(plan["metadata_versions"][0]["metadata"]["kind"], "plan")
        self.assertEqual(plan["phase"], "announcement")
        self.assertEqual(cancel["phase"], "cancellation")
        self.assertNotEqual(plan["event_id"], cancel["event_id"])
        self.assertEqual(plan["related_key"], cancel["related_key"])

    def test_reprints_preserve_source_chains_and_do_not_invent_independence(self):
        self.scan()
        events = list_events(self.conn)
        flow = next(event for event in events if "synthetic-market-flow" == event["related_key"])
        self.assertEqual({source["source_id"] for source in flow["source_chain"]}, {"hkex_stock_connect_daily", "cls_hk_morning"})
        self.assertTrue(all(not source["attribution_chain_verified"] for source in flow["source_chain"]))
        self.assertEqual(flow["facts"], [])

    def test_immutable_versions_and_later_revision_are_not_replayed_as_old(self):
        self.scan()
        row = self.conn.execute("SELECT article_id FROM fn_articles WHERE canonical_url LIKE '%chip-revision'").fetchone()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_article_versions WHERE article_id=?", (row[0],)).fetchone()[0], 2)
        result = route_product(self.conn, self.config, "588710", now=parse_instant(self.fixture["reference_now"]))
        self.assertTrue(any("原始版本" in event["title"] for event in result["groups"]["within_cutoff"]))
        self.assertTrue(any("更正版本" in event["title"] for event in result["groups"]["historical_evidence_incomplete"]))

    def test_cross_day_working_pool_does_not_use_first_seen_today(self):
        self.scan()
        with self.conn:
            self.conn.execute("UPDATE fn_articles SET first_seen_at='2026-09-13T00:00:00+00:00'")
        events = list_events(self.conn, today=True, now=parse_instant(self.fixture["reference_now"]))
        self.assertTrue(any("客户验证" in event["title"] for event in events))
        old = next(event for event in events if "上月AI" in event["title"])
        self.assertEqual(old["metadata_versions"][0]["metadata"]["first_published_at"], "2026-08-10T12:00:00+08:00")

    def test_four_products_route_with_distinct_topics_and_unknown_structure(self):
        self.scan()
        for code, topic in (("513130", "AI"), ("513530", "SOUTHBOUND"), ("588710", "SEMICONDUCTOR"), ("517120", "INNOVATIVE_DRUG")):
            with self.subTest(code=code):
                result = route_product(self.conn, self.config, code, now=parse_instant(self.fixture["reference_now"]), show_scores=True)
                events = [event for group in result["groups"].values() for event in group]
                self.assertTrue(any(topic in event["routing"]["matched_topic_weights"] for event in events))
                self.assertTrue(all(event["editorial_score"] is None and event["product_structure"]["weights"] is None for event in events))
                self.assertTrue(any(event["routing"]["unmatched_or_unknown"] for event in events))
        flow = next(event for group in result["groups"].values() for event in group if event["related_key"] == "synthetic-market-flow")
        self.assertFalse(flow["selected"])

    def test_truncation_resumes_without_cursor_jumping_and_handles_failure(self):
        template = self.fixture["sources"]["hkex_stock_connect_daily"]["records"][0]
        many = []
        for number in range(7):
            record = deepcopy(template)
            record.update(url=f"https://fixture.example.invalid/item-{number}", title=f"合成南向资金事件{number}", event_key=f"test-{number}")
            many.append(record)
        document = {"is_fixture": True, "reference_now": self.fixture["reference_now"], "sources": {"hkex_stock_connect_daily": {"records": many}}}
        fixture = self.write_fixture(document)
        first = self.scan(fixture_path=fixture, max_items=3)
        row = self.conn.execute("SELECT cursor,resume_json FROM fn_sources WHERE source_id='hkex_stock_connect_daily'").fetchone()
        self.assertIsNone(row["cursor"])
        self.assertEqual(json.loads(row["resume_json"])["offset"], 3)
        self.assertEqual(first["counts"]["truncated"], 1)
        broken = deepcopy(document)
        broken["sources"]["hkex_stock_connect_daily"] = {"error": "timeout"}
        self.scan(fixture_path=self.write_fixture(broken), max_items=3)
        saved = self.conn.execute("SELECT resume_json FROM fn_sources WHERE source_id='hkex_stock_connect_daily'").fetchone()[0]
        self.assertEqual(json.loads(saved)["offset"], 3)
        fixture = self.write_fixture(document)
        self.scan(fixture_path=fixture, max_items=3, now="2026-09-15T09:00:00+08:00")
        self.scan(fixture_path=fixture, max_items=3, now="2026-09-15T09:00:00+08:00")
        row = self.conn.execute("SELECT cursor,resume_json FROM fn_sources WHERE source_id='hkex_stock_connect_daily'").fetchone()
        self.assertIsNone(row["resume_json"])
        self.assertEqual(row["cursor"], parse_instant(self.fixture["reference_now"]).isoformat())
        self.assertEqual(self.count("fn_articles"), 7)

    def test_invalid_record_rolls_back_only_its_source(self):
        fixture = deepcopy(self.fixture)
        fixture["sources"]["hkex_news"]["records"][1]["first_published_at"] = "not-a-date"
        result = self.scan(fixture_path=self.write_fixture(fixture))
        bad = next(row for row in result["sources"] if row["source_id"] == "hkex_news")
        self.assertEqual(bad["error_code"], "invalid_metadata")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_article_sources WHERE source_id='hkex_news'").fetchone()[0], 0)
        self.assertGreater(self.count("fn_articles"), 0)

    def test_no_live_adapter_and_fixture_marker_and_mode_required(self):
        with closing(connect(self.root / "live.db", mode="live")) as live:
            sync_registry(live, self.config)
            result = scan_morning(live, self.config, no_network=True)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["coverage"]["live_connected"], 0)
            self.assertEqual(live.execute("SELECT count(*) FROM fn_articles").fetchone()[0], 0)
            with self.assertRaises(ValueError):
                scan_morning(live, self.config, offline=True)
        with self.assertRaises(DiscoveryError):
            FixtureAdapter(self.write_fixture({"sources": {}}), set(self.config["sources"]))

    def test_morning_budget_stops_at_configured_limit(self):
        self.config["runtime"]["morning_scan"]["max_items_per_source"] = 1
        result = self.scan(max_items=1000)
        self.assertTrue(all(row["items_seen"] <= 1 for row in result["sources"]))
        self.assertEqual(result["parameters"]["max_items_per_source"], 1)
        self.assertGreater(result["counts"].get("truncated", 0), 0)
        self.config["runtime"]["morning_scan"]["llm_enabled"] = True
        with self.assertRaises(ValueError):
            self.scan()

    def test_market_date_is_explicit_and_scores_are_hidden_by_default(self):
        self.scan()
        view = route_product(self.conn, self.config, "513530", now=parse_instant(self.fixture["reference_now"]),
                             market_cutoffs={"HK": {"date": "2026-09-14", "source": "user_supplied_fixture"}})
        self.assertEqual(view["view"]["market_cutoffs"]["HK"]["source"], "user_supplied_fixture")
        self.assertTrue(all("route_score" not in event["routing"] for group in view["groups"].values() for event in group))
        with self.assertRaises(ValueError):
            route_product(self.conn, self.config, "513530", now=parse_instant(self.fixture["reference_now"]), market_cutoffs={"HK": {"date": "2026-09-14"}})


if __name__ == "__main__":
    unittest.main()
