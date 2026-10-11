"""SQLite schema and forward migrations. Every migration runs after a consistent backup."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CURRENT_VERSION = 1

MIGRATIONS: dict[int, tuple[str, str]] = {
    1: ("initial AquaCrop v0.2 schema", """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE migrations (version INTEGER PRIMARY KEY, applied_utc TEXT NOT NULL, description TEXT NOT NULL,
                         backup_path TEXT);
CREATE TABLE process_sessions (session_id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, ended_utc TEXT,
    config_id TEXT NOT NULL, config_version TEXT NOT NULL, profile TEXT NOT NULL, software_version TEXT NOT NULL,
    clean_shutdown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sim_runs (run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, seed INTEGER NOT NULL,
    config_id TEXT NOT NULL, config_version TEXT NOT NULL, started_utc TEXT NOT NULL, provenance TEXT NOT NULL,
    reason TEXT);
CREATE TABLE cycles (cycle_pk INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, session_id TEXT NOT NULL, run_id TEXT,
    sim_s REAL, persisted_utc TEXT NOT NULL, n_points INTEGER NOT NULL,
    cadence_s REAL, cadence_basis TEXT);
CREATE TABLE samples (id INTEGER PRIMARY KEY, cycle_pk INTEGER NOT NULL REFERENCES cycles(cycle_pk),
    point_id TEXT NOT NULL, acquired_utc TEXT NOT NULL, persisted_utc TEXT NOT NULL, sequence INTEGER NOT NULL,
    quality TEXT NOT NULL, reason TEXT, raw_value REAL, raw_type TEXT, raw_unit TEXT, value REAL, unit TEXT,
    calibration_ref TEXT, origin TEXT NOT NULL, source_profile TEXT NOT NULL, config_version TEXT NOT NULL,
    device_uid TEXT NOT NULL, bus_id TEXT NOT NULL, session_id TEXT NOT NULL, sim_run_id TEXT, sim_s REAL,
    override_json TEXT, last_good_value REAL, last_good_utc TEXT);
CREATE INDEX ix_samples_point_time ON samples(point_id, acquired_utc);
CREATE INDEX ix_samples_time ON samples(acquired_utc);
CREATE TABLE events (id INTEGER PRIMARY KEY, utc TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT,
    session_id TEXT, run_id TEXT, sim_s REAL, payload_json TEXT NOT NULL);
CREATE INDEX ix_events_time ON events(utc);
CREATE INDEX ix_events_kind ON events(kind, utc);
CREATE TABLE commands (request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, target TEXT NOT NULL,
    desired INTEGER NOT NULL, origin TEXT NOT NULL, reason TEXT, issued_utc TEXT NOT NULL, expires_utc TEXT NOT NULL,
    expected_config_version TEXT NOT NULL, expected_state TEXT, lease_id TEXT, priority INTEGER NOT NULL,
    backend TEXT NOT NULL, session_id TEXT NOT NULL, intent_utc TEXT NOT NULL, outcome TEXT NOT NULL,
    detail TEXT, readback INTEGER, outcome_utc TEXT);
CREATE INDEX ix_commands_time ON commands(intent_utc);
CREATE TABLE alarm_occurrences (occurrence_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, target TEXT NOT NULL,
    severity TEXT NOT NULL, closed INTEGER NOT NULL, active INTEGER NOT NULL, acknowledged INTEGER NOT NULL,
    first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL, updated_utc TEXT NOT NULL, json TEXT NOT NULL);
CREATE INDEX ix_alarm_open ON alarm_occurrences(closed, rule_id, target);
CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
    commissioning INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0, created_utc TEXT NOT NULL,
    password_changed_utc TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 0);
CREATE TABLE auth_sessions (session_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
    csrf_hash TEXT NOT NULL, created_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL, absolute_expiry_utc TEXT NOT NULL,
    revoked_utc TEXT, revoke_reason TEXT, client TEXT);
CREATE TABLE bench_leases (lease_id TEXT PRIMARY KEY, username TEXT NOT NULL, session_hash TEXT NOT NULL,
    process_session_id TEXT NOT NULL, config_version TEXT NOT NULL, allowlist_json TEXT NOT NULL,
    loads_disconnected_confirmation TEXT NOT NULL, setup_description TEXT NOT NULL, created_utc TEXT NOT NULL,
    expires_utc TEXT NOT NULL, revoked_utc TEXT, revoke_reason TEXT);
"""),
}


def utc_iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat(timespec="microseconds")


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=5.0, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA wal_autocheckpoint=1000")      # bounded WAL growth (pages)
    con.execute("PRAGMA journal_size_limit=67108864")  # truncate WAL to <=64 MiB after checkpoint
    return con


def schema_version(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone()
    if not row:
        return 0
    v = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(v[0]) if v else 0


def migrate(con: sqlite3.Connection, db_path: str | Path | None = None, *, backup_dir: str | Path | None = None) -> list[int]:
    """Apply pending migrations. If the DB already has data, a consistent backup is taken first."""
    from .backup import backup_to

    current = schema_version(con)
    if current > CURRENT_VERSION:
        raise RuntimeError(f"database schema {current} is newer than this software ({CURRENT_VERSION}); "
                           "downgrade requires a supported restore, not a migration")
    applied = []
    backup_path = None
    has_tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    if current < CURRENT_VERSION and has_tables and db_path and str(db_path) != ":memory:":
        bdir = Path(backup_dir or Path(db_path).parent / "backups")
        backup_path = backup_to(con, bdir / f"pre-migrate-v{current}-{utc_iso().replace(':', '')}.sqlite3")
    for v in range(current + 1, CURRENT_VERSION + 1):
        desc, sql = MIGRATIONS[v]
        con.execute("BEGIN IMMEDIATE")
        try:
            for stmt in [s for s in sql.split(";") if s.strip()]:
                con.execute(stmt)
            con.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)", (str(v),))
            con.execute("INSERT INTO migrations VALUES(?,?,?,?)",
                        (v, utc_iso(), desc, str(backup_path) if backup_path else None))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        applied.append(v)
    return applied
