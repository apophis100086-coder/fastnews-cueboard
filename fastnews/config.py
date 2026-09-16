"""Strict offline configuration loading; registry intent is not live capability."""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    """Configuration error with a file/field path."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ConfigError(f"mapping keys must be strings at line {key_node.start_mark.line + 1}")
        if key in result:
            raise ConfigError(f"duplicate YAML key at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)

SOURCE_IDS = frozenset("cls_morning stcn_egs_morning yicai_overnight wallstreetcn_breakfast cls_hk_morning zhitong_hk_morning hkex_stock_connect_daily hkex_news ubs_southbound eastmoney_hsgt trendforce miit nda csia semi nmpa cde medcube dxy_insight fda clinicaltrials moa moa_agri_market mysteel smm gov_policy_library".split())
TOPIC_IDS = frozenset("AI SEMICONDUCTOR HK_TECH SOUTHBOUND DIVIDEND INNOVATIVE_DRUG AGRICULTURE METALS MACRO_POLICY".split())
PRODUCT_CODES = frozenset(("513130", "513530", "588710", "517120"))
TIERS = frozenset(("T1_PRIMARY", "T2_PROFESSIONAL", "T3_FINANCIAL_MEDIA", "T4_OTHER"))
CAPABILITIES = frozenset(("needs_probe", "ready_metadata", "ready_fulltext", "search_only", "manual_only", "blocked", "degraded"))
SOURCE_DEFAULT_FIELDS = {"enabled", "obey_robots_txt", "rate_limit_per_minute", "morning_scan", "fetch_full_text_in_morning"}
SOURCE_FIELDS = SOURCE_DEFAULT_FIELDS | {"id", "name", "home_url", "discovery_url", "discovery_mode", "discovery_hint", "tier", "tags", "notes"}
STATE_FIELDS = {"capability_status", "access_review", "adapter_name", "validated_discovery_url", "last_probe_at", "last_success_at", "cursor", "coverage_start", "coverage_end", "truncation_reason"}


def _mapping(value, path, allowed=None, required=()):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigError(f"{path}: expected mapping with string keys")
    if allowed is not None and set(value) - set(allowed):
        raise ConfigError(f"{path}: unknown fields: {', '.join(sorted(set(value) - set(allowed)))}")
    if set(required) - set(value):
        raise ConfigError(f"{path}: missing fields: {', '.join(sorted(set(required) - set(value)))}")
    return value


def _text(value, path, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: expected nonempty string" + (" or null" if nullable else ""))


def _number(value, path, minimum=0, integer=False, nullable=False):
    if nullable and value is None:
        return
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{path}: expected finite {'integer' if integer else 'number'} >= {minimum}")


def _boolean(value, path):
    if type(value) is not bool:
        raise ConfigError(f"{path}: expected boolean")


def _strings(value, path):
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected list of strings")
    for item in value:
        _text(item, path)
    if len(set(value)) != len(value):
        raise ConfigError(f"{path}: duplicate entries")


def _scope(actual, expected, path):
    if set(actual) != set(expected):
        missing = ', '.join(sorted(set(expected) - set(actual))) or 'none'
        extra = ', '.join(sorted(set(actual) - set(expected))) or 'none'
        raise ConfigError(f"{path}: retained scope mismatch; missing={missing}; unexpected={extra}")


def _url(value, path, nullable=False):
    if nullable and value is None:
        return
    _text(value, path)
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        parsed.port
    except ValueError as exc:
        raise ConfigError(f"{path}: expected HTTP(S) URL without credentials") from exc


def _read(root, filename):
    try:
        with (root / filename).open(encoding="utf-8-sig") as handle:
            return yaml.load(handle, Loader=_UniqueLoader)
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{filename}: cannot read configuration ({type(exc).__name__})") from exc
    except (yaml.YAMLError, ConfigError) as exc:
        # Parser errors may contain secret-bearing source lines.
        message = str(exc) if isinstance(exc, ConfigError) else "invalid YAML syntax"
        raise ConfigError(f"{filename}: {message}") from exc


def _sources(doc):
    path = "sources.yaml"
    _mapping(doc, path, {"version", "defaults", "sources"}, {"version", "defaults", "sources"})
    _number(doc["version"], path + ".version", 1, integer=True)
    defaults = _mapping(doc["defaults"], path + ".defaults", SOURCE_DEFAULT_FIELDS, SOURCE_DEFAULT_FIELDS)
    if not isinstance(doc["sources"], list):
        raise ConfigError(path + ".sources: expected list")
    result = {}
    for item in doc["sources"]:
        _mapping(item, path + ".sources[]", SOURCE_FIELDS, {"id", "name", "home_url", "discovery_mode", "tier", "tags"})
        sid = item["id"]
        _text(sid, path + ".id")
        if sid in result:
            raise ConfigError(f"{path}: duplicate source ID {sid}")
        merged = defaults | item
        for field in SOURCE_DEFAULT_FIELDS - {"rate_limit_per_minute"}:
            _boolean(merged[field], f"{path}.{sid}.{field}")
        _number(merged["rate_limit_per_minute"], f"{path}.{sid}.rate_limit_per_minute", 1, integer=True)
        for field in ("name", "discovery_mode", "discovery_hint", "notes", "tier"):
            if field in merged:
                _text(merged[field], f"{path}.{sid}.{field}")
        for field in ("home_url", "discovery_url"):
            if field in merged:
                _url(merged[field], f"{path}.{sid}.{field}")
        if merged["tier"] not in TIERS:
            raise ConfigError(f"{path}.{sid}.tier: unknown source tier")
        _strings(merged["tags"], f"{path}.{sid}.tags")
        if not merged["obey_robots_txt"] or merged["fetch_full_text_in_morning"]:
            raise ConfigError(f"{path}.{sid}: robots must be obeyed and morning full text disabled")
        result[sid] = merged
    _scope(result, SOURCE_IDS, path + ".sources (26 originals)")
    if sum(s["morning_scan"] for s in result.values()) != 21:
        raise ConfigError(path + ": expected 21 original morning_scan sources")
    return result


def _topics(doc):
    path = "topic_taxonomy.yaml"
    _mapping(doc, path, {"version", "topics"}, {"version", "topics"})
    _number(doc["version"], path + ".version", 1, integer=True)
    topics = _mapping(doc["topics"], path + ".topics")
    _scope(topics, TOPIC_IDS, path + ".topics (9 originals)")
    for tid, topic in topics.items():
        _mapping(topic, f"{path}.{tid}", {"keywords", "entities"}, {"keywords", "entities"})
        for key, words in topic.items():
            _strings(words, f"{path}.{tid}.{key}")
    return topics


def _products(doc, sources, topics):
    path = "products.example.yaml"
    _mapping(doc, path, {"version", "products"}, {"version", "products"})
    _number(doc["version"], path + ".version", 1, integer=True)
    if not isinstance(doc["products"], list):
        raise ConfigError(path + ".products: expected list")
    result = {}
    for product in doc["products"]:
        fields = {"code", "name", "topic_weights", "preferred_sources", "search_expansion"}
        _mapping(product, path + ".products[]", fields, fields)
        code = product["code"]
        _text(code, path + ".code")
        if code in result:
            raise ConfigError(f"{path}: duplicate product code {code}")
        _text(product["name"], f"{path}.{code}.name")
        weights = _mapping(product["topic_weights"], f"{path}.{code}.topic_weights", topics)
        for key, weight in weights.items():
            _number(weight, f"{path}.{code}.topic_weights.{key}")
        for field in ("preferred_sources", "search_expansion"):
            _strings(product[field], f"{path}.{code}.{field}")
        if set(product["preferred_sources"]) - set(sources):
            raise ConfigError(f"{path}.{code}.preferred_sources: unknown source reference")
        result[code] = product
    _scope(result, PRODUCT_CODES, path + ".products (4 originals)")
    return result


def _runtime(doc):
    path = "runtime.defaults.yaml"
    schemas = {
        "provenance": {"inherited": "str", "proposed": "str"},
        "runtime": {"timezone": "str", "db_path": "str", "mode": "str", "serve_publicly": "bool"},
        "edition": {"news_cutoff_policy": "str", "market_cutoff_policy": "str", "include_post_cutoff": "bool", "require_explicit_same_day_switch": "bool"},
        "morning_scan": {"llm_enabled": "bool", "fetch_full_text": "bool", "max_items_per_source": "positive", "legacy_since_hours": "positive", "bootstrap_hours": "positive", "cursor_overlap_hours": "integer", "silent_cursor_advance_on_truncation": "bool", "max_retry_attempts": "integer"},
        "second_round": {"max_event_candidates": "positive", "exploration_slots_within_candidates": "integer", "max_fulltext_articles": "positive", "max_articles_per_event": "positive", "prefer_primary_sources": "bool", "llm_allowed_when_configured": "bool", "max_search_requests": "integer", "max_llm_requests_including_retries": "integer", "max_input_tokens": "integer", "max_output_tokens": "integer", "max_cost": "nullable_number", "cost_currency": "nullable_str", "on_budget_exhausted": "str"},
        "providers": {"search": "str", "llm": "str", "wind": "str", "auto_use_developer_chat_session": "bool"},
        "presentation": {"primary_candidate_limit": "positive", "backup_limit": "integer", "fill_missing_candidates": "bool", "show_score_by_default": "bool", "default_selection": "null", "preserve_human_selection_on_refresh": "bool", "show_adverse_evidence": "bool"},
        "privacy": {"private_material_external_llm_requires_consent": "bool", "log_secrets": "bool", "execute_document_instructions": "bool"},
        "export": {"require_human_selected_lead": "bool", "include_pending_as_usable_facts": "bool", "formats": "strings"},
    }
    _mapping(doc, path, set(schemas) | {"version", "status"}, set(schemas) | {"version", "status"})
    for field in ("version", "status"):
        _text(doc[field], f"{path}.{field}")
    for section, schema in schemas.items():
        _mapping(doc[section], f"{path}.{section}", schema, schema)
        for field, kind in schema.items():
            value, loc = doc[section][field], f"{path}.{section}.{field}"
            if kind == "bool":
                _boolean(value, loc)
            elif kind in {"positive", "integer"}:
                _number(value, loc, 1 if kind == "positive" else 0, integer=True)
            elif kind == "nullable_number":
                _number(value, loc, nullable=True)
            elif kind == "strings":
                _strings(value, loc)
            elif kind == "null":
                if value is not None:
                    raise ConfigError(loc + ": must be null; human selection is required")
            else:
                _text(value, loc, nullable=kind == "nullable_str")
    fixed = {
        "runtime.serve_publicly": False, "morning_scan.llm_enabled": False,
        "morning_scan.fetch_full_text": False, "morning_scan.silent_cursor_advance_on_truncation": False,
        "providers.auto_use_developer_chat_session": False,
        "presentation.fill_missing_candidates": False, "presentation.preserve_human_selection_on_refresh": True,
        "presentation.show_adverse_evidence": True, "privacy.private_material_external_llm_requires_consent": True,
        "privacy.log_secrets": False, "privacy.execute_document_instructions": False,
        "export.require_human_selected_lead": True, "export.include_pending_as_usable_facts": False,
    }
    for field, expected in fixed.items():
        section, key = field.split(".")
        if doc[section][key] is not expected:
            raise ConfigError(f"{path}.{field}: unsupported policy; required {expected}")
    for provider in ("search", "llm", "wind"):
        if doc["providers"][provider] not in {"none", "fixture"}:
            raise ConfigError(f"{path}.providers.{provider}: provider is not implemented; use none or fixture")
    try:
        ZoneInfo(doc["runtime"]["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(path + ".runtime.timezone: unknown timezone or missing tzdata") from exc
    if doc["second_round"]["exploration_slots_within_candidates"] > doc["second_round"]["max_event_candidates"]:
        raise ConfigError(path + ".second_round: exploration slots exceed candidate budget")
    if set(doc["export"]["formats"]) != {"markdown", "json"}:
        raise ConfigError(path + ".export.formats: expected markdown and json")
    return doc


def _source_runtime(doc, sources):
    path = "source_runtime.example.yaml"
    fields = {"version", "note", "defaults", "sources", "source_backlog"}
    _mapping(doc, path, fields, fields)
    _number(doc["version"], path + ".version", 1, integer=True)
    _text(doc["note"], path + ".note")
    defaults = _mapping(doc["defaults"], path + ".defaults", STATE_FIELDS, {"capability_status", "access_review", "adapter_name", "validated_discovery_url", "last_probe_at", "last_success_at"})
    entries = _mapping(doc["sources"], path + ".sources")
    _scope(entries, sources, path + ".sources")
    result = {}
    for sid, entry in entries.items():
        state = defaults | _mapping(entry, f"{path}.{sid}", STATE_FIELDS)
        _text(state["capability_status"], f"{path}.{sid}.capability_status")
        if state["capability_status"] not in CAPABILITIES:
            raise ConfigError(f"{path}.{sid}.capability_status: unknown capability")
        _text(state["access_review"], f"{path}.{sid}.access_review")
        for field in STATE_FIELDS - {"capability_status", "access_review", "validated_discovery_url"}:
            if field in state:
                _text(state[field], f"{path}.{sid}.{field}", nullable=True)
        _url(state["validated_discovery_url"], f"{path}.{sid}.validated_discovery_url", nullable=True)
        if state["capability_status"].startswith("ready_") and (not state["adapter_name"] or not state["last_probe_at"] or state["access_review"] == "not_checked"):
            raise ConfigError(f"{path}.{sid}: ready requires adapter, probe time, and access review")
        result[sid] = state
    if not isinstance(doc["source_backlog"], list):
        raise ConfigError(path + ".source_backlog: expected list")
    for entry in doc["source_backlog"]:
        fields = {"role", "reason", "initial_path", "enabled"}
        _mapping(entry, path + ".source_backlog[]", fields, fields)
        for field in ("role", "reason", "initial_path"):
            _text(entry[field], path + ".source_backlog[]." + field)
        _boolean(entry["enabled"], path + ".source_backlog[].enabled")
        if entry["enabled"]:
            raise ConfigError(path + ".source_backlog: unverified backlog cannot be enabled in M1")
    return result


def _routing(doc):
    path = "routing.defaults.yaml"
    fields = {"version", "status", "provenance", "source_tier_weights", "preferred_source_multiplier", "freshness_mode", "freshness_steps", "freshness_half_life_hours", "unknown_time_multiplier"}
    _mapping(doc, path, fields, fields)
    for field in ("version", "status", "freshness_mode"):
        _text(doc[field], f"{path}.{field}")
    provenance_fields = {"source_weights_and_freshness", "topic_weights", "distinct_topic_sum", "preferred_multiplier_and_exponential_option"}
    _mapping(doc["provenance"], path + ".provenance", provenance_fields, provenance_fields)
    for field, value in doc["provenance"].items():
        _text(value, f"{path}.provenance.{field}")
    _mapping(doc["source_tier_weights"], path + ".source_tier_weights", TIERS, TIERS)
    for tier, value in doc["source_tier_weights"].items():
        _number(value, f"{path}.source_tier_weights.{tier}")
    _number(doc["preferred_source_multiplier"], path + ".preferred_source_multiplier")
    _number(doc["freshness_half_life_hours"], path + ".freshness_half_life_hours", minimum=0.000001)
    if doc["unknown_time_multiplier"] is not None:
        raise ConfigError(path + ".unknown_time_multiplier: unknown time must remain null")
    if doc["freshness_mode"] not in {"steps", "exponential"}:
        raise ConfigError(path + ".freshness_mode: expected steps or exponential")
    steps = doc["freshness_steps"]
    if not isinstance(steps, list) or not steps:
        raise ConfigError(path + ".freshness_steps: expected nonempty list")
    previous = 0
    for index, step in enumerate(steps):
        _mapping(step, path + ".freshness_steps[]", {"max_hours", "multiplier"}, {"max_hours", "multiplier"})
        _number(step["multiplier"], path + ".freshness_steps[].multiplier")
        maximum = step["max_hours"]
        if maximum is None:
            if index != len(steps) - 1:
                raise ConfigError(path + ".freshness_steps: only final max_hours can be null")
        else:
            _number(maximum, path + ".freshness_steps[].max_hours", minimum=0.000001)
            if maximum <= previous:
                raise ConfigError(path + ".freshness_steps: bounds must increase")
            previous = maximum
    if steps[-1]["max_hours"] is not None:
        raise ConfigError(path + ".freshness_steps: final max_hours must be null")
    return doc


def load_config(config_dir: Path | str | None = None) -> dict:
    """Load six M0/M1 files; later-module profile/scoring/output files are unused."""
    root = Path(config_dir) if config_dir is not None else Path(__file__).resolve().parent.parent / "config"
    sources = _sources(_read(root, "sources.yaml"))
    topics = _topics(_read(root, "topic_taxonomy.yaml"))
    result = {
        "sources": sources, "topics": topics,
        "products": _products(_read(root, "products.example.yaml"), sources, topics),
        "runtime": _runtime(_read(root, "runtime.defaults.yaml")),
        "source_runtime": _source_runtime(_read(root, "source_runtime.example.yaml"), sources),
        "routing": _routing(_read(root, "routing.defaults.yaml")),
    }
    if (root / 'research_discovery.yaml').exists():
        doc = _read(root, 'research_discovery.yaml')
        fields = {'version','note','lookback_hours','morning_sources','supplemental_sources','products'}
        _mapping(doc, 'research_discovery', fields, fields)
        _text(doc['version'], 'research_discovery.version')
        _text(doc['note'], 'research_discovery.note')
        _number(doc['lookback_hours'], 'research_discovery.lookback_hours', integer=True, minimum=1)
        if doc['lookback_hours'] > 720:
            raise ConfigError('research_discovery.lookback_hours must be <= 720')
        for name in ('morning_sources','supplemental_sources'):
            _strings(doc[name], 'research_discovery.' + name)
            if set(doc[name]) - set(sources):
                raise ConfigError('research_discovery sources must remain in original registry')
        _mapping(doc['products'], 'research_discovery.products', PRODUCT_CODES, PRODUCT_CODES)
        for code, entry in doc['products'].items():
            _mapping(entry, code, {'core_keywords','excluded_keywords'}, {'core_keywords','excluded_keywords'})
            for key, value in entry.items():
                _strings(value, code + '.' + key)
        result['discovery'] = doc
    return result


def config_summary(config) -> dict:
    counts = Counter(state["capability_status"] for state in config["source_runtime"].values())
    return {
        "counts": {"sources": len(config["sources"]), "morning_sources": sum(s["morning_scan"] for s in config["sources"].values()), "topics": len(config["topics"]), "products": len(config["products"])},
        "source_tiers": dict(sorted(Counter(s["tier"] for s in config["sources"].values()).items())),
        "providers": {name: config["runtime"]["providers"][name] for name in ("search", "llm", "wind")},
        "capabilities": {status: counts.get(status, 0) for status in sorted(CAPABILITIES)},
        "morning_llm_enabled": config["runtime"]["morning_scan"]["llm_enabled"],
        "morning_fulltext_enabled": config["runtime"]["morning_scan"]["fetch_full_text"],
        "live_adapters_implemented_in_m1": 0,
        "note": "来源登记与能力覆盖层是配置；M1没有真实采集适配器，fixture不构成联网探测。",
    }
