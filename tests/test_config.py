"""Offline configuration acceptance and corruption tests."""
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

import yaml

from fastnews.config import ConfigError, config_summary, load_config

ROOT = Path(__file__).resolve().parent.parent


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_dir = Path(self.temp.name) / "config"
        shutil.copytree(ROOT / "config", self.config_dir)

    def mutate(self, filename, change):
        path = self.config_dir / filename
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(doc)
        path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def assert_bad(self, pattern):
        with self.assertRaisesRegex(ConfigError, pattern):
            load_config(self.config_dir)

    def test_defaults_preserve_scope_and_unconnected_state(self):
        config = load_config()
        summary = config_summary(config)
        self.assertEqual(summary["counts"], {"sources": 26, "morning_sources": 21, "topics": 9, "products": 4})
        self.assertEqual(summary["providers"], {"search": "none", "llm": "none", "wind": "none"})
        self.assertEqual(summary["capabilities"]["needs_probe"], 26)
        self.assertEqual(summary["live_adapters_implemented_in_m1"], 0)
        self.assertFalse(summary["morning_llm_enabled"])
        self.assertFalse(summary["morning_fulltext_enabled"])
        self.assertTrue(config["sources"]["trendforce"]["enabled"])
        self.assertIsNone(config["source_runtime"]["trendforce"]["last_success_at"])

    def test_original_three_files_are_byte_identical_to_handoff(self):
        with zipfile.ZipFile(ROOT / "references/inputs/SOURCE_HANDOFF_V1.zip") as archive:
            for filename in ("sources.yaml", "topic_taxonomy.yaml", "products.example.yaml"):
                member = next(n for n in archive.namelist() if n.endswith("/config/" + filename))
                self.assertEqual((ROOT / "config" / filename).read_bytes(), archive.read(member), filename)

    def test_original_routing_weights_and_steps(self):
        routing = load_config()["routing"]
        self.assertEqual(routing["source_tier_weights"], {"T1_PRIMARY": 1.0, "T2_PROFESSIONAL": .9, "T3_FINANCIAL_MEDIA": .75, "T4_OTHER": .5})
        self.assertEqual(routing["freshness_mode"], "steps")
        self.assertEqual([s["multiplier"] for s in routing["freshness_steps"]], [1.0, .9, .7, .45])
        self.assertIsNone(routing["unknown_time_multiplier"])

    def test_unknown_root_field(self):
        self.mutate("sources.yaml", lambda d: d.update(undocumented="x"))
        self.assert_bad(r"sources.yaml: unknown fields: undocumented")

    def test_unknown_nested_field(self):
        self.mutate("runtime.defaults.yaml", lambda d: d["morning_scan"].update(llm_enabeld=False))
        self.assert_bad(r"morning_scan: unknown fields: llm_enabeld")

    def test_duplicate_yaml_key(self):
        path = self.config_dir / "sources.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\nversion: 1\n", encoding="utf-8")
        self.assert_bad("duplicate YAML key")

    def test_duplicate_source_id(self):
        self.mutate("sources.yaml", lambda d: d["sources"].append(d["sources"][0].copy()))
        self.assert_bad("duplicate source ID")

    def test_duplicate_product_code(self):
        self.mutate("products.example.yaml", lambda d: d["products"].append(d["products"][0].copy()))
        self.assert_bad("duplicate product code")

    def test_removed_source(self):
        self.mutate("sources.yaml", lambda d: d["sources"].pop())
        self.assert_bad("26 originals")

    def test_removed_topic(self):
        self.mutate("topic_taxonomy.yaml", lambda d: d["topics"].pop("AGRICULTURE"))
        self.assert_bad("9 originals")

    def test_removed_product(self):
        self.mutate("products.example.yaml", lambda d: d["products"].pop())
        self.assert_bad("4 originals")

    def test_morning_registry_scope(self):
        self.mutate("sources.yaml", lambda d: d["sources"][0].update(morning_scan=False))
        self.assert_bad("21 original morning_scan")

    def test_bad_source_reference(self):
        self.mutate("products.example.yaml", lambda d: d["products"][0]["preferred_sources"].append("unknown"))
        self.assert_bad("unknown source reference")

    def test_bad_topic_reference(self):
        self.mutate("products.example.yaml", lambda d: d["products"][0]["topic_weights"].update(UNKNOWN=1.0))
        self.assert_bad("topic_weights: unknown fields: UNKNOWN")

    def test_bool_is_not_budget_number(self):
        self.mutate("runtime.defaults.yaml", lambda d: d["morning_scan"].update(max_items_per_source=True))
        self.assert_bad("max_items_per_source: expected finite integer")

    def test_bool_is_not_routing_weight(self):
        self.mutate("products.example.yaml", lambda d: d["products"][0]["topic_weights"].update(AI=True))
        self.assert_bad("topic_weights.AI: expected finite number")

    def test_nonfinite_weight_is_rejected(self):
        self.mutate("routing.defaults.yaml", lambda d: d.update(preferred_source_multiplier=float("nan")))
        self.assert_bad("preferred_source_multiplier: expected finite number")

    def test_missing_runtime_state(self):
        self.mutate("source_runtime.example.yaml", lambda d: d["sources"].pop("trendforce"))
        self.assert_bad("retained scope mismatch")

    def test_ready_status_without_probe_is_rejected(self):
        self.mutate("source_runtime.example.yaml", lambda d: d["sources"]["trendforce"].update(capability_status="ready_metadata"))
        self.assert_bad("ready requires adapter")

    def test_unsupported_provider_is_not_silently_accepted(self):
        self.mutate("runtime.defaults.yaml", lambda d: d["providers"].update(search="developer_browser"))
        self.assert_bad("provider is not implemented")

    def test_policy_does_not_allow_automatic_selection(self):
        self.mutate("runtime.defaults.yaml", lambda d: d["presentation"].update(default_selection="first"))
        self.assert_bad("human selection is required")

    def test_policy_does_not_allow_morning_llm(self):
        self.mutate("runtime.defaults.yaml", lambda d: d["morning_scan"].update(llm_enabled=True))
        self.assert_bad("morning_scan.llm_enabled: unsupported policy")

    def test_yaml_error_does_not_echo_sensitive_line(self):
        (self.config_dir / "runtime.defaults.yaml").write_text("private_api_key: [DO_NOT_ECHO\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.config_dir)
        self.assertNotIn("DO_NOT_ECHO", str(caught.exception))
        self.assertIn("invalid YAML syntax", str(caught.exception))

    def test_freshness_steps_must_increase(self):
        self.mutate("routing.defaults.yaml", lambda d: d["freshness_steps"][1].update(max_hours=1))
        self.assert_bad("bounds must increase")

    def test_missing_file_reports_file_name(self):
        (self.config_dir / "routing.defaults.yaml").unlink()
        self.assert_bad("routing.defaults.yaml: cannot read configuration")

    def test_independent_loads_do_not_share_mutable_defaults(self):
        first = load_config()
        first["sources"]["trendforce"]["tags"].append("not retained")
        first["source_runtime"]["trendforce"]["capability_status"] = "blocked"
        second = load_config()
        self.assertNotIn("not retained", second["sources"]["trendforce"]["tags"])
        self.assertEqual(second["source_runtime"]["trendforce"]["capability_status"], "needs_probe")


if __name__ == "__main__":
    unittest.main()
