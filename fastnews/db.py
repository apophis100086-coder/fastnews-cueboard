"""Non-destructive, versioned SQLite storage for the M1 metadata pipeline.

Legacy V1 tables are preserved verbatim. Their contents are not imported into
the new metadata model and must not be reported as converted V2 evidence.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


MIGRATION_PATH = Path(__file__).resolve().parents[1] / "db" / "migrations" / "001_m1_metadata.sql"
SCHEMA_VERSION = 1  # Original M1 format, retained for compatibility.
LEGACY_TABLES = frozenset({"sources", "scan_runs", "articles", "article_tags", "events",
                         "event_articles", "event_tags", "products", "daily_candidates", "usage_log"})


def _mode(mode):
    if mode not in {"live", "fixture"}:
        raise ValueError("Database mode must be live or fixture")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _schema(conn):
    # Compare all owned SQL, including CHECK, UNIQUE, and foreign-key constraints.
    return {row[0]: " ".join(row[1].split()) for row in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE name GLOB 'fn_*' AND sql IS NOT NULL")}


def _statements(script):
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("Migration contains an incomplete SQL statement")


def _expected_schema(script):
    with closing(sqlite3.connect(":memory:")) as reference:
        for statement in _statements(script):
            reference.execute(statement)
        return _schema(reference)


def migration_files():
    """Sequential add-only migration files; applied SQL is never rewritten."""
    paths = sorted(MIGRATION_PATH.parent.glob("[0-9][0-9][0-9]_*.sql"))
    if not paths or paths[0] != MIGRATION_PATH:
        raise ValueError("Initial migration is missing")
    if [int(path.name[:3]) for path in paths] != list(range(1, len(paths) + 1)):
        raise ValueError("Migration versions must be contiguous, with no duplicates")
    return paths


def _migration_plan():
    plan = []
    with closing(sqlite3.connect(":memory:")) as reference:
        for version, path in enumerate(migration_files(), 1):
            script = path.read_text(encoding="utf-8")
            for statement in _statements(script):
                reference.execute(statement)
            plan.append({"version": version, "name": path.name, "script": script,
                         "checksum": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                         "schema": _schema(reference)})
    return plan


def _apply_migration(conn, script):
    for statement in _statements(script):
        conn.execute(statement)


def _backup(conn):
    main_path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    if not main_path:
        raise ValueError("Existing in-memory database cannot be upgraded with a durable backup; use a SQLite file")
    original = Path(main_path).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = original.with_name(original.name + ".backup-" + stamp + "-" + uuid.uuid4().hex[:8] + ".sqlite3")
    with closing(sqlite3.connect(backup_path)) as destination:
        conn.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Pre-migration SQLite backup verification failed")
    return str(backup_path)


def connect(path: str | Path, mode: str = "live") -> sqlite3.Connection:
    """Open and initialize a database; close the connection on every failure."""
    _mode(mode)
    if str(path) != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        path = Path(path).expanduser().resolve()
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        initialize(conn, mode)
        return conn
    except BaseException:
        conn.close()
        raise


def initialize(conn: sqlite3.Connection, mode: str = "live") -> None:
    """Apply unapplied migrations transactionally, backing up existing user data.

    An active caller transaction is rejected, never implicitly committed. Any
    unsupported fn_* structure or modified migration is rejected before writes.
    """
    _mode(mode)
    if conn.in_transaction:
        raise ValueError("Commit or roll back the caller transaction before database initialization")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    plan = _migration_plan()
    tables = _tables(conn)
    owned = {table for table in tables if table.startswith("fn_")}
    applied = 0
    if owned:
        if "fn_schema_migrations" not in tables or "fn_meta" not in tables:
            raise ValueError("Conflicting fn_* schema; database left unchanged. Manual migration review required")
        migrations = conn.execute("SELECT version,name,checksum FROM fn_schema_migrations ORDER BY version").fetchall()
        applied = len(migrations)
        expected_ledger = [(p["version"], p["name"], p["checksum"]) for p in plan[:applied]]
        if not applied or applied > len(plan) or [tuple(row) for row in migrations] != expected_ledger:
            raise ValueError("Unsupported migration ledger or changed migration checksum; database left unchanged")
        if _schema(conn) != plan[applied - 1]["schema"]:
            raise ValueError("Conflicting fn_* schema; database left unchanged. Manual migration review required")
        stored_mode = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
        if stored_mode is None or stored_mode[0] != mode:
            raise ValueError("Database mode mismatch; live and fixture records require separate databases")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Database contains foreign-key violations; no records were changed")
        if applied == len(plan):
            return
    if not owned and any(name.startswith("fn_") for name in _schema(conn)):
        raise ValueError("Conflicting fn_* schema object; database left unchanged")
    legacy = sorted(tables & LEGACY_TABLES)
    legacy_counts = {table: conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] for table in legacy}
    if not owned and mode == "fixture" and any(legacy_counts.values()):
        raise ValueError("Legacy records have unknown provenance; use a separate empty fixture database")
    backup_path = _backup(conn) if tables else None
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _tables(conn) != tables:
            raise ValueError("Database schema changed during initialization; retry after other writers finish")
        for migration in plan[applied:]:
            _apply_migration(conn, migration["script"])
            if migration["version"] == 1:
                conn.execute("INSERT INTO fn_meta(key,value) VALUES('mode',?)", (mode,))
                conn.execute("INSERT INTO fn_meta(key,value) VALUES('legacy_import_status',?)",
                             ("preserved_not_converted" if legacy else "not_present",))
                conn.execute("INSERT INTO fn_meta(key,value) VALUES('legacy_tables_json',?)",
                             (json.dumps(legacy_counts, sort_keys=True),))
            conn.execute("INSERT INTO fn_schema_migrations(version,name,checksum,applied_at,backup_path) VALUES(?,?,?,?,?)",
                         (migration["version"], migration["name"], migration["checksum"], datetime.now(timezone.utc).isoformat(), backup_path))
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Migration produced invalid foreign-key references")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def sync_registry(conn: sqlite3.Connection, config: dict) -> dict:
    """Upsert registry configuration without resetting learned source state."""
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    mode_row = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
    if mode_row is None or mode_row[0] not in {"live", "fixture"}:
        raise ValueError("Registry sync requires an initialized, mode-marked database")
    fixture = mode_row[0] == "fixture"
    with conn:
        for source_id, source in config["sources"].items():
            runtime = dict(config["source_runtime"][source_id])
            if fixture:
                # Synthetic data never establish a real adapter's capabilities.
                runtime.update(capability_status="needs_probe", adapter_name=None,
                               access_review="not_checked", validated_discovery_url=None,
                               last_probe_at=None, last_success_at=None)
            conn.execute(
                "INSERT INTO fn_sources(source_id,config_json,capability_status,adapter_name,access_review,validated_discovery_url,last_probe_at,last_success_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET config_json=excluded.config_json",
                (source_id, dump(source), runtime.get("capability_status", "needs_probe"), runtime.get("adapter_name"),
                 runtime.get("access_review", "not_checked"), runtime.get("validated_discovery_url"),
                 runtime.get("last_probe_at"), runtime.get("last_success_at")))
        from .product_cards import registry
        members={c for p in config['products'].values() for c in p.get('member_codes',[])}
        cards=registry()
        all_products={c:dict(cards[c],code=c,kind='fund',selectable=False) for c in members if c in cards}
        all_products.update(config['products'])
        for code, product in all_products.items():
            conn.execute("INSERT INTO fn_products(code,config_json) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET config_json=excluded.config_json",
                         (code, dump(product)))
        for topic_id, topic in config["topics"].items():
            conn.execute("INSERT INTO fn_topics(topic_id,config_json) VALUES(?,?) ON CONFLICT(topic_id) DO UPDATE SET config_json=excluded.config_json",
                         (topic_id, dump(topic)))
    status = conn.execute("SELECT value FROM fn_meta WHERE key='legacy_import_status'").fetchone()
    ready = conn.execute("SELECT COUNT(*) FROM fn_sources WHERE capability_status IN ('ready_metadata','ready_fulltext') AND last_success_at IS NOT NULL").fetchone()[0]
    return {"sources": len(config["sources"]), "topics": len(config["topics"]), "products": len(config["products"]),
            "legacy_import_status": status[0] if status else "unknown", "live_adapters_connected": ready if not fixture else 0,
            "note": "Registry entries describe configured sources, not verified collectors. Legacy rows are preserved without conversion."}
