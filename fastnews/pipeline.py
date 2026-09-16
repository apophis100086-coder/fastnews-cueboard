"""Transactional, source-isolated M1 discovery and metadata persistence."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time as day_time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

from .adapters import DiscoveryError, FixtureAdapter, NoneAdapter
from .metadata import content_hash, event_identity, normalize_url, parse_instant, tag_record


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "morning.synthetic.json"
COLLECTION_TIMEZONE = "Asia/Shanghai"


def collection_window(collection_date, scan_now):
    """An inclusive Shanghai calendar-day window, separate from cursor scans.

    Adapter contracts use inclusive ends, so subtract one microsecond from the
    following midnight. The collection day never substitutes for the clock.
    """
    if not isinstance(collection_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", collection_date):
        raise ValueError("collection_date must be YYYY-MM-DD")
    try:
        selected = date.fromisoformat(collection_date)
    except ValueError:
        raise ValueError("collection_date must be a valid YYYY-MM-DD date") from None
    if not isinstance(scan_now, datetime) or scan_now.tzinfo is None:
        raise ValueError("scan time requires an explicit timezone")
    local_zone = ZoneInfo(COLLECTION_TIMEZONE)
    if selected > scan_now.astimezone(local_zone).date():
        raise ValueError("collection_date cannot be in the future")
    start = datetime.combine(selected, day_time.min, local_zone).astimezone(timezone.utc)
    next_start = datetime.combine(selected + timedelta(days=1), day_time.min, local_zone).astimezone(timezone.utc)
    end = min(next_start - timedelta(microseconds=1), scan_now.astimezone(timezone.utc))
    return start, end


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def source_window(state, scan_now, settings, since_hours, bootstrap_hours):
    cursor = state["cursor"]
    resume = json.loads(state["resume_json"]) if state["resume_json"] else None
    end = scan_now
    start = scan_now - timedelta(hours=bootstrap_hours or settings["bootstrap_hours"])
    basis = "bootstrap"
    if resume:
        start, end = parse_instant(resume["window_start"]), parse_instant(resume["window_end"])
        basis = "resume_incomplete_window"
    elif since_hours is not None:
        start = scan_now - timedelta(hours=since_hours)
        basis = "explicit_since_hours"
    elif bootstrap_hours is None and cursor:
        start = parse_instant(cursor) - timedelta(hours=settings["cursor_overlap_hours"])
        basis = "cursor_with_overlap"
    if end < start or end > scan_now or (cursor is not None and scan_now < parse_instant(cursor)):
        raise ValueError("scan clock precedes saved cursor/window; use an independent fixture database for replay")
    gap = bool(resume and resume.get("earlier_coverage_unknown")) or (
        basis == "explicit_since_hours" and (
            (cursor is None and since_hours < settings["bootstrap_hours"])
            or (cursor is not None and start > parse_instant(cursor))))
    return start, end, basis, resume, gap


def store_record(conn, record, config, retrieved_at):
    """Preserve every source and immutable content version, without extracting facts."""
    canonical = normalize_url(record["url"])
    article_id = stable_id("art_", canonical)
    digest = content_hash(record)
    version_id = stable_id("ver_", article_id + ":" + digest)
    article_inserted = conn.execute("INSERT OR IGNORE INTO fn_articles(article_id,canonical_url,first_seen_at) VALUES(?,?,?)",
                                    (article_id, canonical, retrieved_at)).rowcount
    metadata = dict(record)
    metadata["retrieved_at"] = retrieved_at
    inserted = conn.execute(
        "INSERT OR IGNORE INTO fn_article_versions(version_id,article_id,content_hash,title,lead,metadata_json,retrieved_at,is_fixture) VALUES(?,?,?,?,?,?,?,?)",
        (version_id, article_id, digest, record["title"], record["lead"],
         json_text(metadata), retrieved_at, int(record["is_fixture"]))).rowcount
    conn.execute(
        "INSERT OR IGNORE INTO fn_article_sources(version_id,source_id,source_url,origin_role,attribution_json,retrieved_at) VALUES(?,?,?,?,?,?)",
        (version_id, record["source_id"], record.get("raw_url", record["url"]), record.get("origin_role", "unknown"),
         json_text(record.get("attribution_chain", [])), retrieved_at))
    for topic, matches in tag_record(record, config["topics"]).items():
        conn.execute("INSERT OR IGNORE INTO fn_article_tags(version_id,topic_id,matches_json) VALUES(?,?,?)",
                     (version_id, topic, json_text(matches)))
    signature, phase, related_key = event_identity(record)
    event_id = stable_id("evt_", signature)
    conn.execute("INSERT OR IGNORE INTO fn_events(event_id,signature,phase,related_key,created_at) VALUES(?,?,?,?,?)",
                 (event_id, signature, phase, related_key, retrieved_at))
    conn.execute("INSERT OR IGNORE INTO fn_event_articles(event_id,version_id) VALUES(?,?)",
                 (event_id, version_id))
    return article_inserted, inserted


def scan_morning(conn: sqlite3.Connection, config: dict, *, offline=False,
                 fixture_path=None, now=None, since_hours=None, bootstrap_hours=None,
                 max_items=None, adapter=None, no_network=False, progress=None, source_ids=None,
                 collection_date=None):
    settings = config["runtime"]["morning_scan"]
    if settings["llm_enabled"] or settings["fetch_full_text"]:
        raise ValueError("M1 only supports zero-LLM metadata scanning; requested exception is not implemented")
    for name, number in (("since_hours", since_hours), ("bootstrap_hours", bootstrap_hours),
                         ("max_items", max_items)):
        if number is not None and (type(number) is not int or number <= 0):
            raise ValueError(name + " must be a positive integer")
    if since_hours is not None and bootstrap_hours is not None:
        raise ValueError("since_hours and bootstrap_hours are mutually exclusive")
    if collection_date is not None and (since_hours is not None or bootstrap_hours is not None):
        raise ValueError("collection_date, since_hours and bootstrap_hours are mutually exclusive")
    if fixture_path and not offline:
        raise ValueError("fixture requires --offline")
    if source_ids is not None and (not isinstance(source_ids, list) or not source_ids or
                                  any(sid not in config['sources'] for sid in source_ids)):
        raise ValueError('Explicit source IDs must belong to the original registry')
    mode_row = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
    if mode_row is None or mode_row[0] != ("fixture" if offline else "live"):
        raise ValueError("scan mode and database mode differ")
    if adapter is None:
        if offline:
            adapter = FixtureAdapter(fixture_path or DEFAULT_FIXTURE, set(config["sources"]))
        elif no_network:
            adapter = NoneAdapter()
        else:
            from .live_sources import LiveAdapter
            adapter = LiveAdapter(config)
    reference = getattr(adapter, "reference_now", None)
    scan_now = parse_instant(now) if isinstance(now, str) else now
    clock_basis = "explicit" if now is not None else "fixture_reference" if reference else "system_clock"
    scan_now = scan_now or (parse_instant(reference) if reference else datetime.now(timezone.utc))
    if scan_now.tzinfo is None:
        raise ValueError("scan time requires an explicit timezone")
    scan_now = scan_now.astimezone(timezone.utc)
    dated_window = collection_window(collection_date, scan_now) if collection_date is not None else None
    limit = min(max_items or settings["max_items_per_source"], settings["max_items_per_source"])
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()
    monotonic_start = time.monotonic()
    parameters = {"scan_now": scan_now.isoformat(), "clock_basis": clock_basis,
                  "since_hours": since_hours, "bootstrap_hours": bootstrap_hours,
                  "max_items_per_source": limit, "fetch_full_text": False, "llm_enabled": False,
                  "no_network": no_network, "adapter": type(adapter).__name__,
                  "explicit_source_ids": source_ids}
    if dated_window:
        parameters.update(collection_date=collection_date, date_timezone=COLLECTION_TIMEZONE,
                          window_start=dated_window[0].isoformat(), window_end=dated_window[1].isoformat(),
                          cursor_policy="independent_date_window")
    # Validate all windows before creating a run, so rejected clocks leave no running batch.
    states = {row["source_id"]: row for row in conn.execute("SELECT * FROM fn_sources")}
    windows = {sid: (*dated_window, "explicit_collection_date", None, False) if dated_window
               else source_window(states[sid], scan_now, settings, since_hours, bootstrap_hours)
               for sid in config["sources"]}
    with conn:
        conn.execute("INSERT INTO fn_scan_batches(run_id,mode,started_at,status,parameters_json) VALUES(?,?,?,?,?)",
                     (run_id, "fixture" if offline else "live", started, "running", json_text(parameters)))
    results = []
    for source_id, source in config["sources"].items():
        state = states[source_id]
        cursor = state["cursor"]
        window_start, window_end, window_basis, resume, gap = windows[source_id]
        result = {"source_id": source_id, "status": "skipped_disabled",
                  "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
                  "window_basis": window_basis, "items_seen": 0, "articles_new": 0, "article_versions_new": 0,
                  "coverage_incomplete": False, "error_code": None,
                  "cursor_before": cursor, "cursor_after": cursor, "resume": resume,
                  "earlier_coverage_unknown": gap if source["enabled"] and source["morning_scan"] else False}
        if dated_window:
            result.update(collection_date=collection_date, date_timezone=COLLECTION_TIMEZONE,
                          cursor_updated=False, resume_persisted=False)
        expected = source["enabled"] and (source_id in source_ids if source_ids is not None else source["morning_scan"])
        result['earlier_coverage_unknown'] = gap if expected else False
        if expected:
            try:
                page = adapter.discover(source_id, window_start, window_end, limit, resume)
                result["items_seen"] = len(page.records)
                result["total_items_in_window"] = page.total_in_window
                if offline:
                    result["total_fixture_items_in_window"] = page.total_in_window
                result["coverage_reason"] = page.coverage_reason
                retrieved_at = datetime.now(timezone.utc).isoformat()
                with conn:
                    for record in page.records:
                        articles_new, versions_new = store_record(conn, record, config, retrieved_at)
                        result["articles_new"] += articles_new
                        result["article_versions_new"] += versions_new
                    result["coverage_incomplete"] = not page.complete
                    result["status"] = "success" if page.records else "empty"
                    if not page.complete:
                        result["status"] = "truncated" if page.resume else "partial"
                        if offline:
                            result["status"] = "truncated"
                    result["cursor_after"] = window_end.isoformat() if page.complete and not dated_window else cursor
                    result["earlier_coverage_unknown"] = gap
                    if gap:
                        # A narrower scan cannot establish full cold-start coverage or skip backlog.
                        result["cursor_after"] = cursor
                    if page.resume is not None:
                        page.resume["earlier_coverage_unknown"] = gap
                    result["resume"] = page.resume
                    if not dated_window:
                        conn.execute(
                            "UPDATE fn_sources SET cursor=?,resume_json=?,coverage_start=?,coverage_end=?,truncation_reason=? WHERE source_id=?",
                            (result["cursor_after"], json_text(page.resume) if page.resume else None,
                             window_start.isoformat() if page.complete else (page.coverage_start or state["coverage_start"]),
                             window_end.isoformat() if page.complete else (page.coverage_end or state["coverage_end"]),
                             "item_limit_with_earlier_coverage_unknown" if not page.complete and gap
                             else (page.coverage_reason or "item_limit") if not page.complete else "explicit_window_earlier_coverage_unknown" if gap else None,
                             source_id))
                    if not offline:
                        # Success means actual metadata was parsed, not full-window coverage.
                        conn.execute("UPDATE fn_sources SET last_success_at=?,capability_status='ready_metadata',adapter_name=? WHERE source_id=?",
                                     (retrieved_at, type(adapter).__name__, source_id))
            except DiscoveryError as exc:
                result.update(status="unavailable" if exc.code in {"adapter_not_connected", "fixture_not_provided"}
                              else "failed", coverage_incomplete=True, error_code=exc.code, articles_new=0, article_versions_new=0)
                if not offline and exc.code != "adapter_not_connected":
                    with conn:
                        if exc.code == 'source_terms_manual_only':
                            conn.execute("UPDATE fn_sources SET capability_status='manual_only' WHERE source_id=?", (source_id,))
                        else:
                            conn.execute("UPDATE fn_sources SET capability_status=CASE WHEN last_success_at IS NULL THEN capability_status ELSE 'degraded' END WHERE source_id=?", (source_id,))
            except (ValueError, sqlite3.IntegrityError):
                # Per-source transaction rolls back malformed records; other sources still run.
                result.update(status="failed", coverage_incomplete=True,
                              error_code="invalid_source_records", articles_new=0, article_versions_new=0)
        with conn:
            conn.execute(
                "INSERT INTO fn_source_runs(run_id,source_id,status,window_start,window_end,items_seen,articles_new,coverage_incomplete,error_code,cursor_before,cursor_after,resume_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, source_id, result["status"], result["window_start"], result["window_end"],
                 result["items_seen"], result["articles_new"], int(result["coverage_incomplete"]),
                 result["error_code"], cursor, result["cursor_after"],
                 json_text(result["resume"]) if result["resume"] else None))
        results.append(result)
        if progress is not None:
            progress(dict(result))
    counts = Counter(result["status"] for result in results)
    completed = counts["success"] + counts["empty"]
    has_gap = any(r["coverage_incomplete"] or r.get("earlier_coverage_unknown") for r in results)
    status = "partial" if has_gap and (completed or counts["truncated"] or counts["partial"]) else "unavailable" if has_gap else "success"
    elapsed = round(time.monotonic() - monotonic_start, 6)
    network_stats = getattr(adapter, "stats", {})
    if callable(network_stats):
        network_stats = network_stats()
    network_requests = int(network_stats.get("network_requests", 0))
    usage = {"llm_requests": 0, "search_requests": 0, "network_requests": network_requests,
             "input_tokens": 0, "output_tokens": 0, "cost": 0, "cost_status": "not_called",
             "elapsed_seconds": elapsed, "network": network_stats}
    connected = conn.execute("SELECT count(*) FROM fn_sources WHERE capability_status IN ('ready_metadata','ready_fulltext') AND last_success_at IS NOT NULL").fetchone()[0]
    summary = {"run_id": run_id, "status": status, "parameters": parameters,
               "counts": dict(counts), "articles_new": sum(r["articles_new"] for r in results),
               "article_versions_new": sum(r["article_versions_new"] for r in results),
               "coverage": {"configured": len(results), "expected_morning": sum(
                   bool(s["enabled"] and s["morning_scan"]) for s in config["sources"].values()),
                   "requested_sources_this_run": len(source_ids) if source_ids is not None else sum(bool(s['enabled'] and s['morning_scan']) for s in config['sources'].values()),
                   "live_connected": connected if not offline else 0,
                   "live_sources_read_this_run": completed + counts["partial"] + counts["truncated"] if not offline else 0,
                   "fixture_sources_completed": completed if offline else 0,
                   "coverage_incomplete": has_gap}, "usage": usage, "sources": results}
    if dated_window:
        summary["collection"] = {
            "date": collection_date, "timezone": COLLECTION_TIMEZONE,
            "window_start": dated_window[0].isoformat(), "window_end": dated_window[1].isoformat(),
            "historical_date": collection_date < scan_now.astimezone(ZoneInfo(COLLECTION_TIMEZONE)).date().isoformat(),
            "default_cursor_preserved": True,
            "complete_historical_snapshot": False,
            "notice": ("合成离线资料按日期读取，非真实联网成果。" if offline else
                       "按所选日期补取当前公开列表中仍可见的资料，不能复原完整历史快照。")
                      + "日期不明的条目单独待核实；采集日期不替代报道日期或写稿截点。",
        }
    with conn:
        conn.execute("UPDATE fn_scan_batches SET status=?,finished_at=?,summary_json=? WHERE run_id=?",
                     (status, datetime.now(timezone.utc).isoformat(), json_text(summary), run_id))
        conn.execute("INSERT INTO fn_usage(run_id,llm_requests,search_requests,network_requests,input_tokens,output_tokens,cost,cost_status,elapsed_seconds) VALUES(?,?,?,?,?,?,?,?,?)",
                     (run_id, 0, 0, network_requests, 0, 0, 0, "not_called", elapsed))
    return summary
