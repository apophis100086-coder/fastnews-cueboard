"""CLI adapters for the source, editorial and research services."""
from datetime import datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .routing import working_now


EXTENDED_COMMANDS = ("probe-sources", "create-edition", "list-editions", "show-edition",
                     "import-evidence", "import-product-data", "import-history", "assess-events",
                     "save-decision", "export-handoff", "research-product", "serve",
                     "record-pilot-feedback", "pilot-report")


def parse_markets(values):
    markets = {}
    for value in values:
        market, separator, payload = value.partition("=")
        day, provenance, source = payload.partition("@")
        if not separator or not provenance or not market or not source or market in markets:
            raise ValueError("market cutoff must be unique MARKET=YYYY-MM-DD@SOURCE")
        markets[market] = {"date": day, "source": source}
    return markets


def add_options(sub, name):
    if name == "record-pilot-feedback":
        sub.add_argument("file", type=Path)
    if name == "pilot-report":
        sub.add_argument("product_code", nargs="?")
    if name in {"create-edition", "research-product"}:
        sub.add_argument("product_code")
        sub.add_argument("--edition-date")
        sub.add_argument("--news-cutoff")
        sub.add_argument("--market-cutoff", action="append", default=[], metavar="MARKET=DATE@SOURCE")
        sub.add_argument("--edition-id")
    if name == "research-product":
        sub.add_argument("--fetch-public-text", action="store_true", help="read a bounded shortlist of public source pages")
    if name in {"show-edition"}:
        sub.add_argument("edition_id")
    if name in {"import-evidence", "import-product-data", "import-history"}:
        sub.add_argument("file", type=Path, help="explicit local file; private by default, public context requires --privacy-class public")
        sub.add_argument("--declared-source")
        sub.add_argument("--privacy-class", choices=["private_local", "public"], default="private_local")
    if name == "import-evidence":
        sub.add_argument("--edition-id", required=True)
        sub.add_argument("--event-id")
    if name in {"import-product-data", "import-history"}:
        sub.add_argument("--product-code", required=True)
    if name == "import-history":
        sub.add_argument("--title")
        sub.add_argument("--publication-time")
        sub.add_argument("--completeness", choices=["title_only", "partial_text", "full_text"])
    if name in {"assess-events", "save-decision", "export-handoff"}:
        sub.add_argument("--edition-id", required=True)
    if name == "assess-events":
        sub.add_argument("--event-id", action="append")
    if name == "save-decision":
        sub.add_argument("--actor", required=True, choices=["human"])
        sub.add_argument("--selected-event-id")
        sub.add_argument("--fact-id", action="append", default=[])
        sub.add_argument("--expected-revision", type=int, required=True)
        sub.add_argument("--mainline-note")
    if name == "export-handoff":
        sub.add_argument("--decision-revision", required=True, type=int)
        sub.add_argument("--output-dir", type=Path, default=Path("exports"))
    if name == "probe-sources":
        sub.add_argument("--output", type=Path)
    if name == "serve":
        from .workspace import settings
        sub.add_argument("--port", type=int, default=settings()['port'])


def dispatch(conn, config, args):
    command = args.command
    offline = getattr(args, "offline", False)
    if command == "pilot-report":
        from .pilot import pilot_report
        return pilot_report(conn, args.product_code)
    if command == "record-pilot-feedback":
        from .pilot import record_pilot_feedback
        from .editorial import _read_authorized
        _, _, payload = _read_authorized(args.file, True)
        allowed = {"edition_id", "research_run_id", "feedback_date", "accepted_event_ids", "baseline_minutes",
                   "actual_minutes", "correction_count", "missed_clues", "reason", "trading_day_confirmed",
                   "trading_day_source", "expected_revision"}
        if not isinstance(payload, dict) or set(payload) - allowed or not {"edition_id", "research_run_id"} <= set(payload):
            raise ValueError("Invalid pilot feedback fields; edition_id and research_run_id are required")
        return record_pilot_feedback(conn, config, actor_type="human", **payload)
    if command == "probe-sources":
        if offline:
            raise ValueError("probe-sources requires live mode; fixture is not a live probe")
        from .live_sources import probe_sources
        report = probe_sources(config)
        with conn:
            conn.execute("INSERT INTO fn_meta(key,value) VALUES('latest_source_probe',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps(report, ensure_ascii=False),))
            for state in report["sources"]:
                conn.execute("UPDATE fn_sources SET capability_status=?,adapter_name=?,access_review=?,validated_discovery_url=?,last_probe_at=?,last_success_at=COALESCE(?,last_success_at) WHERE source_id=?",
                             (state["capability_status"], state.get("adapter_name"), state.get("access_review", "not_checked"),
                              state.get("validated_discovery_url"), state.get("last_probe_at"), state.get("last_success_at"), state["source_id"]))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    if command == "serve":
        from .server import serve
        path = conn.execute("PRAGMA database_list").fetchone()[2]
        serve(path, config, offline=offline, port=args.port)
        return {"status": "stopped"}
    from . import editorial
    if command == "create-edition":
        now = working_now(conn, offline).astimezone(ZoneInfo("Asia/Shanghai"))
        day = datetime.fromisoformat(args.edition_date).date() if args.edition_date else now.date()
        cutoff = args.news_cutoff or datetime.combine(day - timedelta(days=1), time(23, 59, 59), now.tzinfo).isoformat()
        return editorial.create_edition(conn, config, product_code=args.product_code,
            edition_date=day.isoformat(), news_cutoff=cutoff, market_cutoffs=parse_markets(args.market_cutoff), edition_id=args.edition_id)
    if command == "list-editions":
        return {"editions": editorial.list_editions(conn)}
    if command == "show-edition":
        return editorial.edition_detail(conn, args.edition_id)
    if command == "import-evidence":
        return editorial.import_evidence(conn, args.file, authorized=True, edition_id=args.edition_id,
                                        event_id=args.event_id, declared_source=args.declared_source, privacy_class=args.privacy_class)
    if command == "import-product-data":
        return editorial.import_product_data(conn, args.file, authorized=True, product_code=args.product_code, declared_source=args.declared_source, privacy_class=args.privacy_class)
    if command == "import-history":
        return editorial.import_history(conn, args.file, authorized=True, product_code=args.product_code,
                                        title=args.title, publication_time=args.publication_time, completeness=args.completeness, privacy_class=args.privacy_class)
    if command == "assess-events":
        return editorial.assess_events(conn, config, edition_id=args.edition_id, event_ids=args.event_id)
    if command == "save-decision":
        return editorial.save_decision(conn, edition_id=args.edition_id, selected_event_id=args.selected_event_id,
            selected_fact_ids=args.fact_id, expected_revision=args.expected_revision, actor_type=args.actor, mainline_note=args.mainline_note)
    if command == "export-handoff":
        return editorial.export_handoff(conn, config, edition_id=args.edition_id,
                                       decision_revision=args.decision_revision, output_dir=args.output_dir)
    if command == "research-product":
        from .research import research_product
        options = {"edition_id": args.edition_id, "edition_date": args.edition_date, "news_cutoff": args.news_cutoff,
                   "market_cutoffs": parse_markets(args.market_cutoff)}
        if args.fetch_public_text:
            if offline:
                raise ValueError("Public text fetching requires live mode")
            from .public_text import PublicTextProvider
            options["fulltext_provider"] = PublicTextProvider(config)
            options["external_calls_authorized"] = True
        return research_product(conn, config, args.product_code, **options)
    raise ValueError("Unknown service command")
