"""Read-only daily publication view, independent of product editions and fetching.

The selected day is a publication-date filter over locally saved metadata. It is
never a claim that every source, or an entire historical day, has been collected.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
import re
from zoneinfo import ZoneInfo

from .metadata import parse_instant, time_bounds
from .routing import working_now


def _publication(metadata, zone):
    """Keep a reported date distinct from the underlying fact's first release."""
    for field in ("report_published_at", "first_published_at"):
        value = metadata.get(field)
        if value is None:
            continue
        lower, _ = time_bounds(value, zone.key)
        precision = "date" if len(value) == 10 else "second"
        display = value if precision == "date" else lower.astimezone(zone).date().isoformat()
        return display, field, precision
    return None, None, "unknown"


def _version_order(item, zone):
    row, metadata = item
    value = metadata.get("source_revision_at") or metadata.get("report_published_at") or metadata.get("first_published_at")
    lower, _ = time_bounds(value, zone.key)
    return lower or datetime.min.replace(tzinfo=timezone.utc), row["revision_order"]


def _coverage(conn, config, day_counts, undated_counts, offline):
    latest = conn.execute("SELECT * FROM fn_scan_batches ORDER BY rowid DESC LIMIT 1").fetchone()
    last_scan = None
    if latest:
        summary = json.loads(latest["summary_json"]) if latest["summary_json"] else {}
        last_scan = {
            "run_id": latest["run_id"], "status": latest["status"],
            "started_at": latest["started_at"], "finished_at": latest["finished_at"],
            "parameters": json.loads(latest["parameters_json"]),
            "articles_new": summary.get("articles_new"), "counts": summary.get("counts", {}),
        }
    states = {row["source_id"]: dict(row) for row in conn.execute("SELECT * FROM fn_sources")}
    last_runs = {}
    for row in conn.execute("SELECT r.*, b.finished_at, b.started_at FROM fn_source_runs r JOIN fn_scan_batches b USING(run_id) ORDER BY b.rowid, r.rowid"):
        # A source intentionally skipped by a one-source scan did not fail and
        # should retain its last actual attempt as its per-source status.
        if row["status"] != "skipped_disabled":
            last_runs[row["source_id"]] = dict(row)
    sources = []
    for source_id, source in config["sources"].items():
        state, run = states.get(source_id, {}), last_runs.get(source_id)
        sources.append({
            "source_id": source_id, "source_name": source["name"],
            "morning_enabled": bool(source["enabled"] and source["morning_scan"]),
            "capability_status": state.get("capability_status", "needs_probe"),
            "last_probe_at": state.get("last_probe_at"), "last_success_at": state.get("last_success_at"),
            "last_run_status": run["status"] if run else "not_run",
            "last_run_at": (run["finished_at"] or run["started_at"]) if run else None,
            "error_code": run["error_code"] if run else None,
            "coverage_incomplete": bool(run["coverage_incomplete"]) if run else True,
            "coverage_start": state.get("coverage_start"), "coverage_end": state.get("coverage_end"),
            "coverage_reason": state.get("truncation_reason"),
            "day_items": day_counts[source_id], "undated_items": undated_counts[source_id],
        })
    connected = sum(row["capability_status"] in {"ready_metadata", "ready_fulltext"} and row["last_success_at"] is not None for row in sources)
    return {
        "registry_total": len(config["sources"]),
        "morning_enabled": sum(row["morning_enabled"] for row in sources),
        "connected_sources": connected if not offline else 0,
        "latest_scan": last_scan, "source_statuses": sources,
        "complete": False, "scope": "locally_stored_publication_metadata",
        "notice": "仅展示已采集并保存的列表信息，不代表全站或当天全部新闻。来源访问失败与当天没有新闻是两回事。",
    }


def daily_news(conn, config, day, *, now=None, offline=False):
    """List one day's saved publications without requiring an Edition.

    ``report_published_at`` has priority; only its absence permits an explicitly
    labelled ``first_published_at`` fallback. Unknown dates remain separate.
    Known post-day revisions are excluded, with missing history made visible.
    """
    if type(offline) is not bool:
        raise ValueError("offline must be an explicit boolean")
    mode = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
    if mode is None or mode[0] != ("fixture" if offline else "live"):
        raise ValueError("daily view mode and database mode differ")
    zone = ZoneInfo(config["runtime"]["runtime"]["timezone"])
    clock = parse_instant(now) if isinstance(now, str) else now
    clock = clock or working_now(conn, offline)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("daily view clock requires an explicit timezone")
    if isinstance(day, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("date must be YYYY-MM-DD")
        day = date.fromisoformat(day)
    elif type(day) is not date:
        raise ValueError("date must be YYYY-MM-DD")
    current_day = clock.astimezone(zone).date()
    if day > current_day:
        raise ValueError("不能选择未来日期")
    selected_day = day.isoformat()
    _, day_end = time_bounds(selected_day, zone.key)
    historical = day < current_day

    versions = defaultdict(list)
    for row in conn.execute("SELECT v.*, v.rowid AS revision_order, a.canonical_url FROM fn_article_versions v JOIN fn_articles a USING(article_id) ORDER BY v.rowid"):
        versions[row["article_id"]].append((row, json.loads(row["metadata_json"])))
    source_rows, tags, events = defaultdict(list), defaultdict(dict), defaultdict(list)
    for row in conn.execute("SELECT * FROM fn_article_sources ORDER BY source_id, source_url"):
        source_rows[row["version_id"]].append(dict(row))
    for row in conn.execute("SELECT * FROM fn_article_tags ORDER BY topic_id"):
        tags[row["version_id"]][row["topic_id"]] = json.loads(row["matches_json"])
    for row in conn.execute("SELECT * FROM fn_event_articles ORDER BY event_id"):
        events[row["version_id"]].append(row["event_id"])

    def make_item(pair):
        row, metadata = pair
        display_date, basis, precision = _publication(metadata, zone)
        version_id = row["version_id"]
        chain = [{
            "source_id": source["source_id"],
            "source_name": config["sources"].get(source["source_id"], {}).get("name", source["source_id"]),
            "source_url": source["source_url"], "version_id": version_id,
            "origin_role": source["origin_role"],
            "attribution_chain": json.loads(source["attribution_json"]),
            "attribution_chain_verified": False, "retrieved_at": source["retrieved_at"],
            "access_status": "fixture_metadata" if offline else "metadata_only", "is_fixture": offline,
        } for source in source_rows[version_id]]
        source_id = metadata.get("source_id") or (chain[0]["source_id"] if chain else None)
        warnings = ["仅有列表元数据，事实尚待核验。"]
        if basis == "first_published_at":
            warnings.append("来源未提供本篇报道时间，此处按记录的首次公开日期归类。")
        if display_date is None:
            warnings.append("发布日期未知；这条信息不计入所选日期的新闻数量。")
        revision = metadata.get("source_revision_at")
        retrieved = parse_instant(row["retrieved_at"])
        if not historical:
            replay = "current_day_reading"
        elif retrieved <= day_end:
            replay = "saved_before_day_end"
        elif revision is None:
            replay = "revision_time_unknown"
            warnings.append("所选日期之后才取得本版本，且修订时间未知；不能视为当时的页面快照。")
        else:
            replay = "source_revision_only"
            warnings.append("所选日期之后才取得本版本；来源修订时间在当天截点内，但缺少当时保存的页面快照。")
        return {
            "article_id": row["article_id"], "version_id": version_id,
            "event_id": events[version_id][0] if events[version_id] else None,
            "event_ids": events[version_id], "title": row["title"], "url": row["canonical_url"],
            "source_id": source_id,
            "source_name": config["sources"].get(source_id, {}).get("name", source_id),
            "report_published_at": metadata.get("report_published_at"),
            "first_published_at": metadata.get("first_published_at"),
            "source_revision_at": revision, "retrieved_at": row["retrieved_at"],
            "display_date": display_date, "date_basis": basis, "date_precision": precision,
            "lead": row["lead"], "topics": list(tags[version_id]), "topic_matches": tags[version_id],
            "source_chain": chain, "pending": True, "evidence_state": "pending",
            "is_fixture": offline, "replay_state": replay, "warnings": warnings,
        }

    items, undated, gaps = [], [], []
    for article_id, article_versions in versions.items():
        matching = [pair for pair in article_versions if _publication(pair[1], zone)[0] == selected_day]
        eligible = [pair for pair in matching if time_bounds(pair[1].get("source_revision_at"), zone.key)[1] is None or time_bounds(pair[1].get("source_revision_at"), zone.key)[1] <= day_end]
        if eligible:
            items.append(make_item(max(eligible, key=lambda pair: _version_order(pair, zone))))
        elif matching:
            excluded = make_item(max(matching, key=lambda pair: _version_order(pair, zone)))
            gaps.append({
                "article_id": article_id, "url": excluded["url"], "source_id": excluded["source_id"],
                "source_name": excluded["source_name"], "display_date": selected_day,
                "excluded_version_ids": [pair[0]["version_id"] for pair in matching],
                "reason": "只保存了所选日期之后修订的版本，缺少当天可展示的旧版本。",
            })
        else:
            newest = max(article_versions, key=lambda pair: _version_order(pair, zone))
            revision_end = time_bounds(newest[1].get("source_revision_at"), zone.key)[1]
            if _publication(newest[1], zone)[0] is None and (revision_end is None or revision_end <= day_end):
                undated.append(make_item(newest))

    def sort_key(item):
        value = item["report_published_at"] or item["first_published_at"]
        lower = time_bounds(value, zone.key)[0]
        return (lower or datetime.min.replace(tzinfo=timezone.utc), item["article_id"])

    items.sort(key=sort_key, reverse=True)
    undated.sort(key=lambda item: (item["retrieved_at"], item["article_id"]), reverse=True)
    # Multiple tracked source URLs can lead to the same saved article/version.
    # Count that publication once for a source while retaining every source URL.
    day_counts = Counter(source_id for item in items for source_id in {source["source_id"] for source in item["source_chain"]})
    undated_counts = Counter(source_id for item in undated for source_id in {source["source_id"] for source in item["source_chain"]})
    coverage = _coverage(conn, config, day_counts, undated_counts, offline)
    notices = [coverage["notice"], "按本篇报道日期筛选；报道日期缺失时才采用已知首次公开日期。抓取日、统计期和实施日不作为发布日期。"]
    if not items:
        notices.append("本地记录中没有匹配所选日期的新闻；这不表示该日没有新闻。")
    if undated:
        notices.append(f"另有 {len(undated)} 条发布日期待核实的信息，单独列出，不计入当日新闻。")
    if historical:
        notices.append("历史日期显示已存元数据；后续修订版本不会混入，缺少当时快照的条目会提示，不能保证完整历史回放。")
    if gaps:
        notices.append(f"有 {len(gaps)} 篇报道仅保存了后来修订的版本，未计入当天可展示新闻。")
    if offline:
        notices.insert(0, "离线合成数据演示；这些条目不是实际联网采集成果。")
    return {
        "date": selected_day, "timezone": zone.key, "is_fixture": offline,
        "items": items, "undated_items": undated, "historical_gaps": gaps,
        "counts": {"items": len(items), "undated": len(undated), "historical_gaps": len(gaps),
                   "sources_with_items": len(day_counts), "stored_articles": len(versions)},
        "coverage": coverage, "notice": " ".join(notices), "notices": notices,
    }
