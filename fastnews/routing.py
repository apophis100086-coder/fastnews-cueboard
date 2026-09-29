"""Read-only metadata views and explainable product routing, without adoption."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from zoneinfo import ZoneInfo

from .metadata import cutoff_state, parse_instant, time_bounds
from .pipeline import json_text, stable_id
from .product_discovery import product_match


def working_now(conn, offline=False):
    if offline:
        row = conn.execute("SELECT parameters_json FROM fn_scan_batches ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            return parse_instant(json.loads(row[0])["scan_now"])
    return datetime.now(timezone.utc)


def list_events(conn, *, cutoff=None, today=False, now=None, pool_hours=72):
    versions = conn.execute(
        "SELECT v.*,v.rowid AS revision_order,a.canonical_url FROM fn_article_versions v JOIN fn_articles a USING(article_id) ORDER BY v.rowid").fetchall()
    by_article = defaultdict(list)
    for row in versions:
        metadata = json.loads(row["metadata_json"])
        by_article[row["article_id"]].append((row, metadata))
    keep = {}
    def version_order(item):
        row, meta = item
        value = meta.get("source_revision_at") or meta.get("report_published_at") or meta.get("first_published_at")
        lower, _ = time_bounds(value)
        return lower or datetime.min.replace(tzinfo=timezone.utc), row["revision_order"]
    for article_versions in by_article.values():
        # Keep latest acquired version, and an older cutoff-eligible version if needed.
        current = max(article_versions, key=version_order)
        keep[current[0]["version_id"]] = current
        if cutoff is not None:
            eligible = [item for item in article_versions if cutoff_state(item[1], cutoff) == "within_cutoff"]
            if eligible:
                last_eligible = max(eligible, key=version_order)
                keep[last_eligible[0]["version_id"]] = last_eligible
    event_rows = conn.execute("SELECT e.*,ea.version_id FROM fn_events e JOIN fn_event_articles ea USING(event_id) ORDER BY e.event_id").fetchall()
    events = {}
    day_start = None
    if today:
        if now is None:
            now = datetime.now(timezone.utc)
        local_day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        day_start = datetime.combine(local_day, time.min, ZoneInfo("Asia/Shanghai"))
    for row in event_rows:
        item = keep.get(row["version_id"])
        if item is None:
            continue
        version, metadata = item
        if day_start is not None:
            lower, upper = time_bounds(metadata.get("source_revision_at") or metadata.get("report_published_at") or metadata.get("first_published_at"))
            effective_lower, effective_upper = time_bounds(metadata.get("effective_at"))
            still_planned = metadata.get("kind") == "plan" and effective_upper is not None and effective_upper >= day_start
            if upper is not None and upper < day_start - timedelta(hours=pool_hours) and not still_planned:
                continue
            if lower is not None and lower > now:
                continue
        event = events.setdefault(row["event_id"], {
            "event_id": row["event_id"], "phase": row["phase"], "related_key": row["related_key"],
            "title": version["title"], "metadata_versions": [], "source_chain": [], "topic_matches": {},
            "evidence_state": "pending", "facts": [], "selected": False,
            "semantic_assessment": "not_performed", "history_overlap": "unknown",
        })
        event["metadata_versions"].append({
            "version_id": version["version_id"], "article_id": version["article_id"],
            "content_hash": version["content_hash"], "canonical_url": version["canonical_url"],
            "cutoff_state": cutoff_state(metadata, cutoff) if cutoff else "not_evaluated",
            "metadata": metadata,
        })
        for source in conn.execute("SELECT * FROM fn_article_sources WHERE version_id=? ORDER BY source_id", (version["version_id"],)):
            event["source_chain"].append({"source_id": source["source_id"], "source_url": source["source_url"],
                "version_id": version["version_id"], "origin_role": source["origin_role"],
                "attribution_chain": json.loads(source["attribution_json"]),
                "attribution_chain_verified": False, "retrieved_at": source["retrieved_at"],
                "access_status": "fixture_metadata" if version["is_fixture"] else "metadata_only",
                "is_fixture": bool(version["is_fixture"])})
        for tag in conn.execute("SELECT * FROM fn_article_tags WHERE version_id=? ORDER BY topic_id", (version["version_id"],)):
            topic = tag["topic_id"]
            event["topic_matches"][topic] = sorted(set(event["topic_matches"].get(topic, [])) | set(json.loads(tag["matches_json"])))
    for event in events.values():
        states = {v["cutoff_state"] for v in event["metadata_versions"]}
        event["cutoff_state"] = next((s for s in ("within_cutoff", "pending_time", "historical_evidence_incomplete", "out_of_cutoff") if s in states), "not_evaluated")
        # Late revisions are diagnostics, never contributors to an eligible view's routing.
        active = [v for v in event["metadata_versions"] if v["cutoff_state"] == event["cutoff_state"]]
        event["excluded_versions"] = [v for v in event["metadata_versions"] if v not in active]
        event["metadata_versions"] = active
        event["title"] = active[0]["metadata"]["title"]
        active_ids = {v["version_id"] for v in active}
        event["source_chain"] = [s for s in event["source_chain"] if s["version_id"] in active_ids]
        event["topic_matches"] = {}
        for version_id in sorted(active_ids):
            for tag in conn.execute("SELECT topic_id,matches_json FROM fn_article_tags WHERE version_id=?", (version_id,)):
                matches = set(event["topic_matches"].get(tag["topic_id"], []))
                event["topic_matches"][tag["topic_id"]] = sorted(matches | set(json.loads(tag["matches_json"])))
    return list(events.values())


def route_product(conn, config, code, *, edition_date=None, news_cutoff=None,
                  market_cutoffs=None, now=None, show_scores=False):
    if code not in config["products"]:
        raise ValueError("Unknown product code; retained routes: " + ", ".join(config["products"]))
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo(config["runtime"]["runtime"]["timezone"])
    day = date.fromisoformat(edition_date) if isinstance(edition_date, str) else edition_date or now.astimezone(zone).date()
    cutoff = parse_instant(news_cutoff) if isinstance(news_cutoff, str) else news_cutoff
    cutoff = cutoff or datetime.combine(day - timedelta(days=1), time(23, 59, 59), zone).astimezone(timezone.utc)
    if cutoff.tzinfo is None:
        raise ValueError("news cutoff requires explicit timezone")
    if cutoff > now:
        raise ValueError("news cutoff cannot be later than the research clock")
    markets = market_cutoffs or {}
    for market, entry in markets.items():
        if not market or not isinstance(entry, dict) or not entry.get("source"):
            raise ValueError("market cutoff requires a market, explicit date, and source")
        if date.fromisoformat(entry["date"]) > day:
            raise ValueError("market cutoff cannot be after edition date")
    rule = config["routing"]
    product = config["products"][code]
    view = {"edition_date": day.isoformat(), "product_code": code,
            "news_cutoff": cutoff.isoformat(), "market_cutoffs": markets,
            "timezone": zone.key, "rule_version": rule["version"],
            "profile_version": None, "history_snapshot_id": None}
    view["edition_id"] = stable_id("view_", json_text(view))
    view["persistence"] = "M1_read_only_view; frozen Edition persistence is M3"
    candidates = list_events(conn, cutoff=cutoff)
    for event in candidates:
        weights = {topic: product["topic_weights"][topic] for topic in event["topic_matches"] if topic in product["topic_weights"]}
        source_ids = {source["source_id"] for source in event["source_chain"]}
        # Personal sources from another profile can be present in the shared
        # article store. They never acquire an original source's tier by default.
        tier_weight = max((rule["source_tier_weights"][config["sources"].get(sid,{}).get('tier','T4_OTHER')] for sid in source_ids), default=1.0)
        preferred = sorted(source_ids.intersection(product["preferred_sources"]))
        preferred_weight = rule["preferred_source_multiplier"] if preferred else 1.0
        first_dates = [time_bounds(version["metadata"].get("first_published_at"))[0] for version in event["metadata_versions"]]
        publication = min((value for value in first_dates if value is not None), default=None)
        age_hours = max(0, (cutoff - publication).total_seconds() / 3600) if publication else None
        decay = None
        if age_hours is not None:
            if rule.get("freshness_mode", "steps") == "steps":
                decay = next(step["multiplier"] for step in rule["freshness_steps"]
                             if step["max_hours"] is None or age_hours <= step["max_hours"])
            else:
                decay = math.pow(0.5, age_hours / rule["freshness_half_life_hours"])
        score = sum(weights.values()) * tier_weight * preferred_weight * decay if decay is not None else None
        event["routing"] = {"matched_topic_weights": weights, "topic_weight_sum": sum(weights.values()),
            "source_tier_weight": tier_weight, "preferred_sources": preferred,
            "preferred_source_multiplier": preferred_weight, "first_publication_age_hours": age_hours,
            "freshness_multiplier": decay, "rule_version": rule["version"],
            "unmatched_or_unknown": not bool(weights), "score_kind": "rule_routing_only"}
        event['product_match'] = product_match(event,config,code,cutoff)
        event["_route_sort"] = score if score is not None else -1
        if show_scores:
            event["routing"]["route_score"] = round(score, 6) if score is not None else None
        event["editorial_score"] = None
        event["product_structure"] = {"status": "not_imported", "holdings": None, "weights": None}
        event["gaps"] = ["仅有元数据，未核实原文或事实", "尚未导入产品结构和历史稿件", "未做语义评估"]
        if "SOUTHBOUND" in weights:
            event["gaps"].append("南向总量仅支持港股资金环境线索；没有去向证据，不推断流入红利或科技")
        if not markets:
            event["gaps"].append("未提供有来源的行情截点；未计算上一交易日")
    # Missing first-publication dates keep a null freshness score. Separate
    # discovery matching still gives a deterministic, useful metadata order.
    candidates.sort(key=lambda event: (
        {'core':0,'background':1,'unmatched':2}[event['product_match']['lane']],
        {'in_window':0,'undated':1,'older':2,'after_cutoff':3}[event['product_match']['report_window']],
        -event['routing']['topic_weight_sum'], -event['_route_sort'],
        -(parse_instant(event['product_match']['latest_report_at']).timestamp() if event['product_match']['latest_report_at'] else 0),
        event['event_id']))
    groups = {"within_cutoff": [], "out_of_cutoff": [], "pending_time": [], "historical_evidence_incomplete": []}
    for event in candidates:
        event.pop("_route_sort")
        groups[event["cutoff_state"]].append(event)
    return {"view": view, "product_name": product["name"], "groups": groups,
            "event_count": len(candidates), "default_selection": None, "auto_select": False,
            "route_config_is_holdings_evidence": False,
            "note": "截点内表示时间允许继续核验；全部元数据仍为pending，无可发布事实。未命中线索仍保留。"}
