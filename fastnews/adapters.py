"""Bounded metadata discovery contract. No live network adapter exists in M1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Protocol

from .metadata import MetadataError, normalize_record, time_bounds


class DiscoveryError(ValueError):
    def __init__(self, code: str):
        # Log controlled codes only, never external exception text or credentials.
        self.code = code
        super().__init__(code)


@dataclass
class DiscoveryPage:
    records: list[dict]
    complete: bool
    resume: dict | None
    total_in_window: int
    coverage_reason: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None


class MetadataAdapter(Protocol):
    def discover(self, source_id: str, start: datetime, end: datetime,
                 limit: int, resume: dict | None = None) -> DiscoveryPage: ...


class NoneAdapter:
    supported_sources = frozenset()
    def discover(self, source_id, start, end, limit, resume=None):
        raise DiscoveryError("adapter_not_connected")


class FixtureAdapter:
    """Reads explicitly synthetic JSON only. It never retrieves any URL."""

    def __init__(self, path: Path | str, known_sources: set[str]):
        path = Path(path)
        if path.stat().st_size > 5_000_000:
            raise DiscoveryError("fixture_too_large")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise DiscoveryError("fixture_invalid_json") from None
        if (not isinstance(document, dict) or document.get("is_fixture") is not True
                or not isinstance(document.get("sources"), dict)):
            raise DiscoveryError("fixture_marker_required")
        if set(document["sources"]) - known_sources:
            raise DiscoveryError("fixture_unknown_source")
        self.document = document
        self.reference_now = document.get("reference_now")

    def discover(self, source_id, start, end, limit, resume=None):
        entry = self.document["sources"].get(source_id)
        if entry is None:
            raise DiscoveryError("fixture_not_provided")
        if not isinstance(entry, dict):
            raise DiscoveryError("fixture_invalid_source")
        if entry.get("error"):
            code = entry["error"]
            if code not in {"parse_error", "http_403", "http_404", "timeout", "access_denied"}:
                code = "fixture_source_error"
            raise DiscoveryError(code)
        if not isinstance(entry.get("records"), list):
            # A malformed listing is not a successful empty result.
            raise DiscoveryError("parse_error")
        records = []
        try:
            for raw in entry["records"]:
                if not isinstance(raw, dict) or raw.get("source_id") != source_id:
                    raise DiscoveryError("fixture_source_mismatch")
                record = normalize_record(raw)
                # A revision may keep the original report date. Discover the new version.
                # Editorial eligibility still uses FIRST publication plus revision checks.
                lower, upper = time_bounds(record.get("source_revision_at") or record.get("report_published_at")
                                           or record.get("first_published_at"))
                if lower is not None and (upper < start or lower > end):
                    continue
                records.append(record)
        except MetadataError:
            raise DiscoveryError("invalid_metadata") from None
        # Preserve source order. The token pins the complete filtered dataset.
        digest = hashlib.sha256(json.dumps(records, sort_keys=True,
                                          ensure_ascii=False).encode()).hexdigest()
        offset = 0
        if resume and resume.get("dataset_hash") == digest:
            offset = resume.get("offset", 0)
        if type(offset) is not int or offset < 0 or offset > len(records):
            raise DiscoveryError("invalid_resume_token")
        chosen = records[offset:offset + limit]
        next_offset = offset + len(chosen)
        complete = next_offset >= len(records)
        token = None if complete else {
            "offset": next_offset, "dataset_hash": digest,
            "window_start": start.isoformat(), "window_end": end.isoformat(),
        }
        return DiscoveryPage(chosen, complete, token, len(records))
