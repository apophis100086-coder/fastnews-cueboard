"""M1 regression cases from independent review; entirely synthetic and offline."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastnews.adapters import FixtureAdapter
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.metadata import parse_instant
from fastnews.pipeline import scan_morning
from fastnews.routing import list_events, route_product


SOURCE = "hkex_stock_connect_daily"
NOW = "2026-09-15T08:15:00+08:00"


class M1RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = load_config()
        self.conn = connect(self.root / "regression.fixture.db", mode="fixture")
        # LIFO cleanup closes SQLite before removing its temporary directory.
        self.addCleanup(self.conn.close)
        sync_registry(self.conn, self.config)

    def metadata(self, **changes):
        value = {
            "title": "[合成测试] 南向资金市场变化",
            "lead": "合成市场环境元数据；未提供投资去向或原始事实。",
            "url": "https://fixture.example.invalid/reviewed-event",
            "source_id": SOURCE,
            "first_published_at": "2026-09-14T10:00:00+08:00",
            "report_published_at": "2026-09-14T10:00:00+08:00",
            "source_revision_at": "2026-09-14T10:00:00+08:00",
            "phase": "announcement",
            "event_key": "synthetic-reviewed-event",
        }
        value.update(changes)
        return value

    def fixture(self, records):
        path = self.root / "review.synthetic.json"
        document = {"is_fixture": True, "reference_now": NOW,
                    "sources": {SOURCE: {"records": records}}}
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def scan(self, path, **kwargs):
        return scan_morning(self.conn, self.config, offline=True, fixture_path=path, **kwargs)

    @staticmethod
    def source_result(result):
        return next(source for source in result["sources"] if source["source_id"] == SOURCE)

    def test_recent_revision_is_discovered_when_report_keeps_its_old_date(self):
        revised = self.metadata(source_revision_at="2026-09-15T07:00:00+08:00")
        adapter = FixtureAdapter(self.fixture([revised]), set(self.config["sources"]))
        page = adapter.discover(SOURCE, parse_instant("2026-09-15T06:15:00+08:00"), parse_instant(NOW), 50)
        self.assertEqual(len(page.records), 1, "A newly revised version must not be excluded by the original report timestamp")
        self.assertEqual(page.records[0]["first_published_at"], revised["first_published_at"])
        self.assertEqual(page.records[0]["report_published_at"], revised["report_published_at"])
        self.assertEqual(page.records[0]["source_revision_at"], revised["source_revision_at"])

    def test_reverse_source_order_cannot_replace_new_version_with_old(self):
        old = self.metadata()
        new = self.metadata(title="[合成测试] 南向资金及AI应用补充说明",
                            source_revision_at="2026-09-15T07:00:00+08:00")
        self.scan(self.fixture([new, old]))
        events = list_events(self.conn)
        current_versions = [version for event in events for version in event["metadata_versions"]]
        self.assertEqual(len(current_versions), 1)
        self.assertEqual(current_versions[0]["metadata"]["source_revision_at"], new["source_revision_at"])
        self.assertEqual(events[0]["title"], new["title"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_article_versions").fetchone()[0], 2)

    def test_post_cutoff_keyword_cannot_raise_old_event_routing_score(self):
        old = self.metadata()
        new = self.metadata(title="[合成测试] 南向资金及AI应用补充说明",
                            source_revision_at="2026-09-15T07:00:00+08:00")
        self.scan(self.fixture([old, new]))
        view = route_product(self.conn, self.config, "513130", now=parse_instant(NOW),
                             news_cutoff="2026-09-14T23:59:59+08:00", show_scores=True)
        eligible = view["groups"]["within_cutoff"]
        self.assertEqual(len(eligible), 1)
        self.assertIn("SOUTHBOUND", eligible[0]["topic_matches"])
        self.assertNotIn("AI", eligible[0]["topic_matches"])
        self.assertNotIn("AI", eligible[0]["routing"]["matched_topic_weights"])
        self.assertTrue(all(version["cutoff_state"] == "within_cutoff" for version in eligible[0]["metadata_versions"]),
                        "A late revision must be shown as unavailable for replay, outside the cutoff-eligible version set")
        unavailable = view["groups"]["historical_evidence_incomplete"]
        diagnostics = [version for event in unavailable for version in event["metadata_versions"]]
        diagnostics += [version for event in eligible for version in event.get("excluded_versions", [])]
        self.assertTrue(any(version["metadata"]["source_revision_at"] == new["source_revision_at"]
                            and version["cutoff_state"] == "historical_evidence_incomplete"
                            for version in diagnostics),
                        "The excluded newer version must remain visible with an explicit historical-evidence gap")

    def test_cold_short_window_after_truncated_resume_still_bootstraps_missing_daytime(self):
        old = self.metadata(title="[合成测试] 昨日白天南向资金旧窗口线索", event_key="synthetic-daytime",
                            url="https://fixture.example.invalid/yesterday-daytime")
        records = [old]
        for index, hour in enumerate((18, 19, 20)):
            item = deepcopy(old)
            published = f"2026-09-14T{hour}:00:00+08:00"
            item.update(title=f"[合成测试] 夜间南向资金元数据{index}",
                        url=f"https://fixture.example.invalid/evening-{index}", event_key=f"synthetic-evening-{index}",
                        first_published_at=published, report_published_at=published, source_revision_at=published)
            records.append(item)
        path = self.fixture(records)
        first = self.source_result(self.scan(path, since_hours=18, max_items=1))
        self.assertEqual(first["status"], "truncated")
        self.source_result(self.scan(path, max_items=1))
        final_short_page = self.source_result(self.scan(path, max_items=1))
        self.assertEqual(final_short_page["status"], "success")
        self.assertIsNone(self.conn.execute("SELECT article_id FROM fn_articles WHERE canonical_url=?", (old["url"],)).fetchone())
        next_default = self.source_result(self.scan(path))
        self.assertEqual(next_default["window_basis"], "bootstrap")
        self.assertLessEqual(parse_instant(next_default["window_start"]), parse_instant("2026-09-14T10:00:00+08:00"))
        self.assertIsNotNone(self.conn.execute("SELECT article_id FROM fn_articles WHERE canonical_url=?", (old["url"],)).fetchone(),
                             "Finishing an 18-hour page sequence must not erase the older cold-start coverage gap")


if __name__ == "__main__":
    unittest.main()
