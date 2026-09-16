"""JSON CLI for source acquisition, evidence, human decisions and trial records."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from .config import config_summary, load_config
from .db import connect, sync_registry
from .metadata import parse_instant
from .pipeline import DEFAULT_FIXTURE, scan_morning
from .routing import list_events, route_product, working_now
from .cli_actions import EXTENDED_COMMANDS, add_options, dispatch, parse_markets


def common_options(parser):
    # SUPPRESS lets common flags appear before or after a subcommand.
    parser.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                        help="use the isolated synthetic fixture database; never access network")
    parser.add_argument("--db", type=Path, default=argparse.SUPPRESS, help="explicit SQLite file; mode is permanently checked")
    parser.add_argument("--config-dir", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON output (also the default)")
    parser.add_argument("--summary", action="store_true", default=argparse.SUPPRESS, help="omit individual event/source records")


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m fastnews", description="华泰柏瑞快讯AI线索看板 · CLI 与本地人工选题工作台")
    common_options(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-config", "init-db", "sync-sources", "source-status", "scan-morning", "list-events", "route-product", *EXTENDED_COMMANDS):
        sub = commands.add_parser(name)
        common_options(sub)
        if name in EXTENDED_COMMANDS:
            add_options(sub, name)
        if name == "scan-morning":
            windows = sub.add_mutually_exclusive_group()
            windows.add_argument("--since-hours", type=int)
            windows.add_argument("--bootstrap-hours", type=int)
            windows.add_argument("--date", dest="collection_date", metavar="YYYY-MM-DD",
                                 help="collect a Shanghai calendar day from currently visible listings; preserves incremental cursors")
            sub.add_argument("--max-items-per-source", type=int)
            sub.add_argument("--fixture", type=Path, help="explicit synthetic JSON, requires --offline")
            sub.add_argument("--now", help="ISO timestamp with timezone; synthetic reference clock by default offline")
            sub.add_argument("--no-network", action="store_true", help="retain live mode but explicitly disable network acquisition")
            sub.add_argument("--source", action="append", help="explicit one-off source collection; original morning flags remain unchanged")
        if name == "list-events":
            sub.add_argument("--today", action="store_true", help="cross-day working pool, not first_seen=today")
            sub.add_argument("--now")
        if name == "route-product":
            sub.add_argument("product_code")
            sub.add_argument("--edition-date")
            sub.add_argument("--news-cutoff", help="explicit ISO timestamp incl. timezone; default previous calendar day 23:59:59")
            sub.add_argument("--now")
            sub.add_argument("--market-cutoff", action="append", default=[], metavar="MARKET=DATE@SOURCE",
                             help="explicit market date plus provenance; no trading calendar is assumed")
            sub.add_argument("--show-scores", action="store_true", help="show routing scores; these are not editorial scores")
    return parser


def capabilities(conn=None):
    connected = 0 if conn is None else conn.execute("SELECT count(*) FROM fn_sources WHERE capability_status IN ('ready_metadata','ready_fulltext') AND last_success_at IS NOT NULL").fetchone()[0]
    return {"live_metadata_adapters_connected": connected, "search_provider": "not_configured",
            "llm_provider": "not_configured", "wind_provider": "not_configured",
            "semantic_assessment": "requires_authorized_llm", "fulltext_fetch": "explicit_bounded_research",
            "fact_evidence_and_human_selection_export": "local_workflow",
            "second_round_budget_execution": "fixed_metadata_then_bounded_providers", "morning_llm_requests": 0,
            "coverage_notice": "来源能力以实际探测和最近采集结果为准；登记数量不表示已覆盖。"}


def source_status(conn):
    latest = conn.execute("SELECT run_id FROM fn_scan_batches ORDER BY rowid DESC LIMIT 1").fetchone()
    results = []
    for row in conn.execute("SELECT * FROM fn_sources ORDER BY source_id"):
        source = json.loads(row["config_json"])
        status = {key: row[key] for key in ("source_id", "capability_status", "adapter_name", "access_review",
            "validated_discovery_url", "last_probe_at", "last_success_at", "cursor", "coverage_start", "coverage_end", "truncation_reason")}
        status.update(name=source["name"], expected_enabled=source["enabled"], expected_morning_scan=source["morning_scan"])
        run = conn.execute("SELECT r.run_id,r.status,r.error_code,r.coverage_incomplete,r.items_seen,b.started_at,b.finished_at FROM fn_source_runs r JOIN fn_scan_batches b USING(run_id) WHERE source_id=? AND r.status!='skipped_disabled' ORDER BY r.rowid DESC LIMIT 1",
                           (row["source_id"],)).fetchone() if latest else None
        status["last_run"] = dict(run) if run else None
        results.append(status)
    connected = sum(s["capability_status"] in {"ready_metadata", "ready_fulltext"} and s["last_success_at"] is not None for s in results)
    probe = conn.execute("SELECT value FROM fn_meta WHERE key='latest_source_probe'").fetchone()
    return {"configured": len(results), "live_connected": connected, "sources": results,
            "latest_probe": json.loads(probe[0]) if probe else None}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    offline = getattr(args, "offline", False)
    envelope = {"data_mode": "fixture" if offline else "live", "is_fixture": offline,
                "banner": "合成测试 / FIXTURE — 非真实联网成果，不可用于新闻" if offline else "LIVE · 真实模式，实际来源覆盖见本次结果",
                "capabilities": capabilities()}
    try:
        config = load_config(getattr(args, "config_dir", None))
        if args.command == "validate-config":
            result = config_summary(config)
        else:
            live_path = Path(config["runtime"]["runtime"]["db_path"])
            default_path = live_path.with_name(live_path.stem + ".fixture" + live_path.suffix) if offline else live_path
            db_path = getattr(args, "db", default_path).resolve()
            envelope["database"] = str(db_path)
            with closing(connect(db_path, mode="fixture" if offline else "live")) as conn:
                synced = sync_registry(conn, config)
                if args.command == "init-db":
                    result = {"status": "initialized", "registry": synced,
                              "tables": [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]}
                elif args.command == "sync-sources":
                    result = synced
                elif args.command == "source-status":
                    result = source_status(conn)
                elif args.command == "scan-morning":
                    result = scan_morning(conn, config, offline=offline, fixture_path=args.fixture,
                        now=args.now, since_hours=args.since_hours, bootstrap_hours=args.bootstrap_hours,
                        max_items=args.max_items_per_source, no_network=args.no_network, source_ids=args.source,
                        collection_date=args.collection_date)
                elif args.command == "list-events":
                    now = parse_instant(args.now) if args.now else working_now(conn, offline)
                    events = list_events(conn, today=args.today, now=now,
                        pool_hours=config["runtime"]["morning_scan"]["bootstrap_hours"])
                    result = {"events": events, "event_count": len(events), "working_clock": now.isoformat(),
                              "today_means": "跨日工作池，保留近期报道、未知时间与已知未来计划",
                              "default_selection": None, "facts_extracted": 0}
                elif args.command in EXTENDED_COMMANDS:
                    result = dispatch(conn, config, args)
                else:
                    markets = parse_markets(args.market_cutoff)
                    result = route_product(conn, config, args.product_code, edition_date=args.edition_date,
                        news_cutoff=args.news_cutoff, market_cutoffs=markets,
                        now=parse_instant(args.now) if args.now else working_now(conn, offline), show_scores=args.show_scores)
                envelope["capabilities"] = capabilities(conn)
        if getattr(args, "summary", False):
            result = dict(result)
            for key in ("events", "sources"):
                if isinstance(result.get(key), list):
                    result[key + "_omitted"] = len(result.pop(key))
            if "groups" in result:
                result["group_counts"] = {key: len(value) for key, value in result.pop("groups").items()}
        envelope["result"] = result
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        envelope["status"] = "error"
        envelope["error"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps(envelope, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
