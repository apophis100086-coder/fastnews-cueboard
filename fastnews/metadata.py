"""Pure metadata rules for the explicitly synthetic M1 discovery pipeline.

No function fetches URLs, resolves DNS, reads article bodies, or verifies claims.
The returned records remain pending metadata even when fixture input claims an
original source. Date precision is retained as an interval, never a guessed hour.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = datetime_timezone.utc
TIME_FIELDS = (
    "first_published_at", "report_published_at", "occurred_at", "effective_at",
    "data_period_start", "data_period_end", "source_revision_at",
)
PHASES = frozenset({"unknown", "announcement", "implementation", "revision", "cancellation"})
KINDS = frozenset({"observed", "company_statement", "reported", "estimate", "plan", "interpretation"})
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_INSTANT = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"})
_OPTIONAL_TEXT = ("subject", "action", "object", "event_key", "related_key")
_ALLOWED_INPUTS = frozenset(
    {"title", "url", "source_id", "lead", "phase", "kind", "origin_role",
     "attribution_chain", "event_date", "is_fixture"} | set(TIME_FIELDS) | set(_OPTIONAL_TEXT)
)


class MetadataError(ValueError):
    """Invalid or unsafe metadata; the caller can isolate this source failure."""


def parse_instant(value: str) -> datetime:
    """Parse an explicit ISO timestamp and return an aware UTC datetime."""
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise MetadataError("time must be an ISO timestamp with an explicit timezone")
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise MetadataError("invalid ISO timezone offset")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise MetadataError("invalid ISO timestamp") from exc


def time_bounds(value: str | None, timezone: str = "Asia/Shanghai") -> tuple[datetime | None, datetime | None]:
    """Return inclusive UTC bounds; date intervals use the app's second precision."""
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise MetadataError("time metadata must be a string or null")
    if not _DATE.fullmatch(value):
        instant = parse_instant(value)
        return instant, instant
    try:
        zone = ZoneInfo(timezone)
        local_date = date.fromisoformat(value)
        lower = datetime.combine(local_date, time.min, zone).astimezone(UTC)
        upper = datetime.combine(local_date + timedelta(days=1), time.min, zone).astimezone(UTC)
        return lower, upper - timedelta(seconds=1)
    except (ValueError, OverflowError, ZoneInfoNotFoundError, TypeError) as exc:
        raise MetadataError("invalid date or timezone (install tzdata for IANA timezone support)") from exc


def normalize_url(url: str) -> str:
    """Canonicalize conservatively and reject unsafe literal destinations.

    This is only syntax/literal-address validation. A future live adapter must
    validate DNS resolution, all redirects, and the connected address as well.
    """
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise MetadataError("url must be a nonempty HTTP(S) URL without whitespace or backslashes")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise MetadataError("only public HTTP(S) URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise MetadataError("URL credentials are forbidden")
        host = parts.hostname.rstrip(".").lower()
        if "%" in host:
            raise MetadataError("encoded or scoped hostnames are forbidden")
        if ":" not in host:
            try:
                # IDNA maps Unicode dot variants; perform safety checks AFTER
                # this normalization so 127.0.0。1 cannot bypass literal checks.
                host = host.encode("idna").decode("ascii").rstrip(".").lower()
            except UnicodeError as exc:
                raise MetadataError("invalid hostname") from exc
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host and ":" not in host:
            raise MetadataError("local hostnames are forbidden")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Block noncanonical numeric formats that some HTTP stacks decode
            # as loopback/private addresses (integer, octal and hex variants).
            if re.fullmatch(r"[0-9.]+", host) or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host):
                raise MetadataError("noncanonical numeric hostnames are forbidden")
            labels = host.split(".")
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                raise MetadataError("invalid hostname")
        else:
            if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified or address.is_loopback:
                raise MetadataError("nonpublic IP literal addresses are forbidden")
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        port = parts.port
        scheme = parts.scheme.lower()
        netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
        kept = []
        for parameter in parts.query.split("&"):
            key = parameter.split("=", 1)[0].casefold()
            if key.startswith("utm_") or key in _TRACKING_KEYS:
                continue
            kept.append(parameter)
        return urlunsplit((scheme, netloc, parts.path or "/", "&".join(kept), ""))
    except MetadataError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise MetadataError("invalid URL") from exc


def _text(value: Any, name: str, *, optional: bool = False, allow_empty: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise MetadataError(f"{name} must be a string" + (" or null" if optional else ""))
    if len(value) > (4000 if name == "lead" else 2000):
        raise MetadataError(f"{name} exceeds metadata length limit")
    value = value.strip()
    if not value and not allow_empty:
        raise MetadataError(f"{name} must not be empty")
    return value


def _iso_bounds(value: str | None) -> list[str | None]:
    return [bound.isoformat() if bound else None for bound in time_bounds(value)]


def normalize_record(raw: dict, *, is_fixture: bool = True) -> dict:
    """Validate explicitly mode-bound metadata; titles/lead never become evidence."""
    if type(is_fixture) is not bool:
        raise MetadataError("metadata mode must be an explicit boolean")
    if not isinstance(raw, dict):
        raise MetadataError("metadata record must be an object")
    if "full_text" in raw:
        raise MetadataError("full_text is not accepted by metadata-only morning discovery")
    unknown = set(raw) - _ALLOWED_INPUTS
    if unknown:
        raise MetadataError("unknown metadata fields: " + ", ".join(sorted(str(key) for key in unknown)))
    if "is_fixture" in raw and raw["is_fixture"] is not is_fixture:
        raise MetadataError("metadata fixture marker does not match the ingestion mode")
    record = {name: _text(raw.get(name), name, allow_empty=name == "lead") for name in ("title", "url", "source_id", "lead")}
    record["raw_url"] = raw["url"]
    record["url"] = normalize_url(record["url"])
    record["normalized_bounds"] = {}
    for field in TIME_FIELDS:
        value = raw.get(field)
        record["normalized_bounds"][field] = _iso_bounds(value)
        record[field] = value
    start = time_bounds(record["data_period_start"])[0]
    end = time_bounds(record["data_period_end"])[1]
    if start and end and start > end:
        raise MetadataError("data_period_start must not be after data_period_end")
    first = record["first_published_at"]
    record["publication_precision"] = "unknown" if first is None else "date" if _DATE.fullmatch(first) else "second"
    phase, kind = raw.get("phase", "unknown"), raw.get("kind", "reported")
    if not isinstance(phase, str) or phase not in PHASES:
        raise MetadataError("phase must be unknown/announcement/implementation/revision/cancellation")
    if not isinstance(kind, str) or kind not in KINDS:
        raise MetadataError("kind must be observed/company_statement/reported/estimate/plan/interpretation")
    record.update(phase=phase, kind=kind)
    for field in _OPTIONAL_TEXT:
        record[field] = _text(raw.get(field), field, optional=True)
    record["event_date"] = raw.get("event_date")
    record["normalized_bounds"]["event_date"] = _iso_bounds(record["event_date"])
    record["origin_role"] = _text(raw.get("origin_role", "unknown"), "origin_role")
    chain = raw.get("attribution_chain", [])
    if not isinstance(chain, list) or len(chain) > 20:
        raise MetadataError("attribution_chain must be a list with at most 20 entries")
    for entry in chain:
        if isinstance(entry, str):
            _text(entry, "attribution_chain")
        elif isinstance(entry, dict):
            if len(entry) > 12 or any(not isinstance(key, str) or value is not None and not isinstance(value, (str, bool)) for key, value in entry.items()):
                raise MetadataError("attribution_chain entries must contain bounded string/bool/null metadata")
            for key, value in entry.items():
                if isinstance(value, str):
                    _text(value, "attribution_chain", allow_empty=True)
                    if key in {"url", "source_url"}:
                        normalize_url(value)
        else:
            raise MetadataError("attribution_chain entries must be strings or objects")
    # JSON copy prevents caller mutation from silently changing saved lineage.
    record["attribution_chain"] = json.loads(json.dumps(chain, ensure_ascii=False))
    record.update(
        attribution_chain_verified=False, primary_source_found=False,
        is_fixture=is_fixture, access_status="fixture_metadata" if is_fixture else "metadata_only", evidence_state="pending",
    )
    return record


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def content_hash(record: dict) -> str:
    """Content/version identity excludes discovery source and attribution."""
    fields = ("title", "lead", "phase", "kind", "subject", "action", "object", "event_key", "related_key")
    content = {field: record.get(field) for field in fields}
    content["times"] = {field: _iso_bounds(record.get(field)) for field in (*TIME_FIELDS, "event_date")}
    return _hash(content)


def _fold(value: str) -> str:
    return " ".join(value.casefold().split())


def _claim_numbers(record: dict) -> list[str]:
    # Comparing signatures conservatively separates absent/different numbers;
    # it does not claim arithmetic or semantic equivalence between units.
    text = str(record.get("title", "")) + " " + str(record.get("lead", ""))
    pattern = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:万亿元|万亿美元|亿元|亿美元|万元|万美元|亿港元|港元|美元|人民币|元|亿|万|%|％|个百分点|年|月|日|倍|家|个|台|吨)?"
    return sorted({re.sub(r"\s+", "", match).replace(",", "").replace("％", "%") for match in re.findall(pattern, text)})


def event_identity(record: dict) -> tuple[str, str, str]:
    """Conservative deterministic clustering; topic tags never identify events."""
    phase = record.get("phase", "unknown")
    event_key = record.get("event_key")
    structural = [record.get(field) for field in ("subject", "action", "object", "event_date")]
    first_lower = time_bounds(record.get("first_published_at"))[0]
    guard = {
        "numbers": _claim_numbers(record),
        # Publication-day differences can signal repeated or genuinely new
        # announcements even when a fixture reuses an explicit event key.
        "first_publication_date": first_lower.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() if first_lower else None,
        "times": {field: _iso_bounds(record.get(field)) for field in ("event_date", "occurred_at", "effective_at", "data_period_start", "data_period_end")},
    }
    if event_key:
        base = ["explicit", event_key]
    elif all(structural):
        base = ["structured", *[_fold(value) for value in structural[:3]], _iso_bounds(structural[3])]
    else:
        first = record.get("first_published_at")
        base = ["exact_title", _fold(record.get("title", "")), _iso_bounds(first)]
        # Without a publication time the matching title is insufficient evidence
        # for joining independent URLs into one real-world event.
        if first is None:
            base.append(normalize_url(record["url"]))
    signature = "event_" + _hash([base, phase, guard])
    if record.get("related_key"):
        related = record["related_key"]
    elif event_key:
        related = "related_" + _hash(["explicit", event_key])
    elif all(structural):
        related = "related_" + _hash(["structured", *[_fold(value) for value in structural[:3]]])
    else:
        related = "related_" + _hash(["url", normalize_url(record["url"])])
    return signature, phase, related


def tag_record(record: dict, topics: dict) -> dict[str, list[str]]:
    """Match distinct configured keywords/entities with ASCII-word boundaries."""
    if "topics" in topics and isinstance(topics["topics"], dict):
        topics = topics["topics"]
    text = str(record.get("title", "")) + " " + str(record.get("lead", ""))
    hits = {}
    for topic_id, topic in topics.items():
        matched, seen = [], set()
        for keyword in [*topic.get("keywords", []), *topic.get("entities", [])]:
            folded = keyword.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            pattern = re.escape(keyword)
            if keyword and keyword[0].isascii() and keyword[0].isalnum():
                pattern = r"(?<![A-Za-z0-9_])" + pattern
            if keyword and keyword[-1].isascii() and keyword[-1].isalnum():
                pattern += r"(?![A-Za-z0-9_])"
            if keyword and re.search(pattern, text, re.IGNORECASE):
                matched.append(keyword)
        if matched:
            hits[topic_id] = matched
    return hits


def cutoff_state(record: dict, cutoff: datetime) -> str:
    """Assess publication/version timing only, without verifying the content."""
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise MetadataError("cutoff must be a timezone-aware datetime")
    cutoff = cutoff.astimezone(UTC)
    lower, upper = time_bounds(record.get("first_published_at"))
    if lower is None:
        # A known later report cannot stand in for an unknown earlier fact.
        # Keep the first-publication field null and the item outside this issue.
        report_lower, _ = time_bounds(record.get('report_published_at'))
        if report_lower is not None and report_lower > cutoff:
            return 'out_of_cutoff'
        _, revision_upper = time_bounds(record.get('source_revision_at'))
        if revision_upper is not None and revision_upper > cutoff:
            return 'historical_evidence_incomplete'
        return "pending_time"
    if lower > cutoff:
        return "out_of_cutoff"
    if upper > cutoff:
        return "pending_time"
    revision_lower, revision_upper = time_bounds(record.get("source_revision_at"))
    if revision_upper is not None and revision_upper > cutoff:
        return "historical_evidence_incomplete"
    return "within_cutoff"
