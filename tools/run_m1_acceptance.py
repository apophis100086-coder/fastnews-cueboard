"""Save a reviewable CLI run in NEW isolated databases; no network integrations.

Application behavior is tested by unittest separately. This helper records real
CLI outputs and immutable synthetic examples for the current source checkout.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing


ROOT = Path(__file__).resolve().parents[1]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "data" / "acceptance" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    fixture_db, live_db = directory / "fixture.db", directory / "live.db"
    cases = [
        ("m0_config", ["validate-config"], None),
        ("fixture_init", ["init-db"], True),
        ("fixture_sync", ["sync-sources"], True),
        ("fixture_scan", ["scan-morning", "--bootstrap-hours", "72"], True),
        ("fixture_repeat", ["scan-morning"], True),
        ("fixture_events", ["list-events", "--today"], True),
        *[("route_" + code, ["route-product", code, "--edition-date", "2026-09-15",
                            "--news-cutoff", "2026-09-14T23:59:59+08:00"], True)
          for code in ("513130", "513530", "588710", "517120")],
        ("fixture_sources", ["source-status"], True),
        ("live_init", ["init-db"], False),
        ("live_sync", ["sync-sources"], False),
        ("live_scan", ["scan-morning", "--since-hours", "18", "--no-network"], False),
        ("live_events", ["list-events", "--today"], False),
        ("live_sources", ["source-status"], False),
    ]
    records = []
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    for name, arguments, fixture in cases:
        command = [sys.executable, "-m", "fastnews", *arguments]
        if fixture is not None:
            command += ["--db", str(fixture_db if fixture else live_db)]
        if fixture:
            command += ["--offline"]
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        output = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        (directory / (name + ".json")).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        records.append({"case": name, "command": command, "exit_code": result.returncode,
                        "output": str((directory / (name + ".json")).relative_to(ROOT)),
                        "status": output.get("result", {}).get("status"),
                        "articles_new": output.get("result", {}).get("articles_new"),
                        "article_versions_new": output.get("result", {}).get("article_versions_new"),
                        "event_count": output.get("result", {}).get("event_count")})
        if result.returncode:
            raise SystemExit("Acceptance command failed; inspect " + str(directory / (name + ".json")))
    counts = {}
    for mode, db_path in (("fixture", fixture_db), ("live", live_db)):
        with closing(sqlite3.connect(db_path)) as conn:
            counts[mode] = {table: conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
                            for table in ("fn_sources", "fn_topics", "fn_products", "fn_articles",
                                          "fn_article_versions", "fn_events", "fn_article_sources", "fn_scan_batches")}
    report = {"executed_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
              "scope": "M0/M1 CLI execution, synthetic data explicitly isolated from empty live DB",
              "live_sources_connected": 0, "counts": counts, "commands": records,
              "unit_tests": {"command": "python -m unittest discover -s tests -v",
                             "note": "Run separately; this helper does not claim to have run unittest."}}
    report_path = directory / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "commands_passed": len(records), "counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
