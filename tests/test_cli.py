"""Offline CLI integration checks, including all original compatibility commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "cli.db"

    def run_cli(self, *args, offline=True, expected=0):
        command = [sys.executable, "-m", "fastnews", *args, "--db", str(self.db)]
        if offline:
            command.append("--offline")
        elif "scan-morning" in args:
            command.append("--no-network")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        for key in ("SEARCH_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
            env.pop(key, None)
        process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        return json.loads(process.stdout if expected == 0 else process.stderr)

    def test_original_commands_work_and_banner_persists(self):
        for command in (("init-db",), ("sync-sources",), ("scan-morning", "--since-hours", "18"),
                        ("list-events", "--today"), ("route-product", "513130")):
            with self.subTest(command=command):
                result = self.run_cli(*command, "--summary")
                self.assertTrue(result["is_fixture"])
                self.assertIn("合成测试", result["banner"])
                self.assertEqual(result["capabilities"]["live_metadata_adapters_connected"], 0)

    def test_unknown_product_and_wrong_database_mode_are_clear_errors(self):
        self.run_cli("init-db")
        error = self.run_cli("route-product", "000000", expected=2)
        self.assertIn("Unknown product", error["error"])
        error = self.run_cli("init-db", offline=False, expected=2)
        self.assertEqual(error["status"], "error")

    def test_fixture_cannot_run_in_live_mode_and_bad_hours_rejected(self):
        result = self.run_cli("scan-morning", "--fixture", "tests/fixtures/morning.synthetic.json", offline=False, expected=2)
        self.assertIn("offline", result["error"])
        # Use live to match the DB already initialized by the invalid invocation.
        result = self.run_cli("scan-morning", "--since-hours", "-1", offline=False, expected=2)
        self.assertIn("positive", result["error"])

    def test_live_scan_reports_zero_coverage_and_no_fake_events(self):
        result = self.run_cli("scan-morning", "--summary", offline=False)
        self.assertFalse(result["is_fixture"])
        self.assertEqual(result["result"]["status"], "unavailable")
        self.assertEqual(result["result"]["articles_new"], 0)
        self.assertEqual(result["result"]["counts"]["unavailable"], 21)

    def test_common_flag_before_command_and_explicit_cutoff(self):
        self.run_cli("--offline", "scan-morning", "--summary")
        result = self.run_cli("route-product", "517120", "--edition-date", "2026-09-15",
                              "--news-cutoff", "2026-09-14T23:59:59+08:00", "--market-cutoff", "CN=2026-09-14@fixture", "--summary")
        self.assertEqual(result["result"]["view"]["market_cutoffs"]["CN"]["source"], "fixture")
        self.assertIsNone(result["result"]["default_selection"])


if __name__ == "__main__":
    unittest.main()
