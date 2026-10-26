"""SQLite schema and forward migrations. Every migration runs after a consistent backup."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CURRENT_VERSION = 7

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
    2: ("archive register for retention (prune only what is archived and verified)", """
CREATE TABLE archives (archive_id TEXT PRIMARY KEY, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
    path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, samples INTEGER NOT NULL, events INTEGER NOT NULL,
    created_utc TEXT NOT NULL, verified_utc TEXT);
"""),
    3: ("process recipes, recipe activation log and stock readiness (installation process control, ADR-0014)", """
CREATE TABLE recipes (recipe_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
    provenance TEXT NOT NULL, name TEXT NOT NULL, digest TEXT NOT NULL, created_utc TEXT NOT NULL,
    created_by TEXT NOT NULL, updated_utc TEXT NOT NULL, json TEXT NOT NULL, PRIMARY KEY (recipe_id, version));
CREATE TABLE recipe_activations (seq INTEGER PRIMARY KEY, utc TEXT NOT NULL, actor TEXT NOT NULL,
    recipe TEXT NOT NULL, digest TEXT NOT NULL, action TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE stock_readiness (seq INTEGER PRIMARY KEY, stock TEXT NOT NULL, status TEXT NOT NULL, actor TEXT,
    utc TEXT, json TEXT NOT NULL);
"""),
    4: ("I/O board records: channel checks, assignments and a per-output command index (bench bring-up, ADR-0017)", """
CREATE TABLE io_assignments (seq INTEGER PRIMARY KEY, point TEXT NOT NULL, signal TEXT, status TEXT NOT NULL,
    actor TEXT NOT NULL, utc TEXT NOT NULL, note TEXT, evidence_json TEXT NOT NULL, config_id TEXT NOT NULL,
    config_version TEXT NOT NULL);
CREATE INDEX ix_io_assignments_point ON io_assignments(config_id, point, seq);
CREATE TABLE io_checks (seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL, point TEXT NOT NULL, result TEXT NOT NULL,
    actor TEXT NOT NULL, utc TEXT NOT NULL, note TEXT, evidence_json TEXT NOT NULL, config_id TEXT NOT NULL,
    config_version TEXT NOT NULL);
CREATE INDEX ix_io_checks_point ON io_checks(config_id, point, seq);
CREATE INDEX ix_commands_target ON commands(target, intent_utc);
"""),
    5: ("equipment types and items, channel bindings, programs, occurrences, conflicts, installation mode, "
        "restart recovery and module commissioning transactions (0.5.0)", """
CREATE TABLE equipment_types (type_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
    name TEXT NOT NULL, digest TEXT NOT NULL, created_utc TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_utc TEXT NOT NULL, json TEXT NOT NULL, PRIMARY KEY (type_id, version));
CREATE TABLE equipment_items (item_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
    type_id TEXT NOT NULL, type_version INTEGER NOT NULL, name TEXT NOT NULL, icon TEXT NOT NULL,
    digest TEXT NOT NULL, created_utc TEXT NOT NULL, created_by TEXT NOT NULL, updated_utc TEXT NOT NULL,
    json TEXT NOT NULL, PRIMARY KEY (item_id, version));
CREATE TABLE channel_bindings (seq INTEGER PRIMARY KEY, item_id TEXT NOT NULL, role TEXT NOT NULL, point TEXT,
    status TEXT NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL, note TEXT, evidence_json TEXT NOT NULL,
    config_id TEXT NOT NULL, config_version TEXT NOT NULL);
CREATE INDEX ix_channel_bindings_item ON channel_bindings(config_id, item_id, role, seq);
CREATE INDEX ix_channel_bindings_point ON channel_bindings(config_id, point, seq);
CREATE TABLE equipment_log (seq INTEGER PRIMARY KEY, utc TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
    ref TEXT NOT NULL, action TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE programs (program_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
    name TEXT NOT NULL, digest TEXT NOT NULL, created_utc TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_utc TEXT NOT NULL, validation_json TEXT, simulation_json TEXT, json TEXT NOT NULL,
    PRIMARY KEY (program_id, version));
CREATE TABLE program_activations (seq INTEGER PRIMARY KEY, runtime_id TEXT NOT NULL, program_id TEXT NOT NULL,
    version INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE occurrences (occurrence_key TEXT PRIMARY KEY, runtime_id TEXT NOT NULL, program_id TEXT NOT NULL,
    version INTEGER NOT NULL, scheduled_utc TEXT, state TEXT NOT NULL, created_utc TEXT NOT NULL,
    started_utc TEXT, ended_utc TEXT, outcome TEXT, json TEXT NOT NULL);
CREATE INDEX ix_occurrences_program ON occurrences(runtime_id, program_id, scheduled_utc);
CREATE TABLE conflicts (conflict_id TEXT PRIMARY KEY, runtime_id TEXT NOT NULL, state TEXT NOT NULL,
    opened_utc TEXT NOT NULL, resolved_utc TEXT, resolution TEXT, actor TEXT, json TEXT NOT NULL);
CREATE TABLE installation_mode (seq INTEGER PRIMARY KEY, runtime_id TEXT NOT NULL, mode TEXT NOT NULL,
    phase TEXT NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL, reason TEXT, json TEXT NOT NULL);
CREATE TABLE recovery (seq INTEGER PRIMARY KEY, runtime_id TEXT NOT NULL, process_session_id TEXT NOT NULL,
    state TEXT NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE commissioning_tx (tx_id TEXT PRIMARY KEY, state TEXT NOT NULL, bus_id TEXT NOT NULL,
    address INTEGER, actor TEXT NOT NULL, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL, json TEXT NOT NULL);
CREATE UNIQUE INDEX ux_commissioning_reserved ON commissioning_tx(bus_id, address)
    WHERE state IN ('RESERVED', 'WRITING', 'UNCERTAIN', 'PREPARED')
"""),
    6: ("equipment usage across restarts (starts, last verified OFF), functional blocks, explicit hydraulic "
        "topology and layout revisions, twin run records and durable change journal (0.7.0)", """
CREATE TABLE equipment_usage (runtime_id TEXT NOT NULL, item_id TEXT NOT NULL, updated_utc TEXT NOT NULL,
    json TEXT NOT NULL, PRIMARY KEY (runtime_id, item_id));
CREATE TABLE topology_revisions (workspace TEXT NOT NULL, rev INTEGER NOT NULL, status TEXT NOT NULL,
    digest TEXT NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL, note TEXT, json TEXT NOT NULL,
    PRIMARY KEY (workspace, rev));
CREATE TABLE diagram_revisions (view TEXT NOT NULL, rev INTEGER NOT NULL, topology_rev INTEGER,
    digest TEXT NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL, json TEXT NOT NULL, PRIMARY KEY (view, rev));
CREATE TABLE blocks (block_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, name TEXT NOT NULL,
    digest TEXT NOT NULL, topology_rev INTEGER, created_utc TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_utc TEXT NOT NULL, json TEXT NOT NULL, validation_json TEXT, PRIMARY KEY (block_id, version));
CREATE TABLE twin_runs (run_id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, ended_utc TEXT, actor TEXT NOT NULL,
    model TEXT NOT NULL, context_json TEXT NOT NULL, summary_json TEXT)
"""),
    7: ("editable public ids of equipment and programs over their stable keys, with a rename log; module requests "
        "as a tracked workflow (0.7.1)", """
CREATE TABLE public_ids (kind TEXT NOT NULL, key TEXT NOT NULL, public_id TEXT NOT NULL, rev INTEGER NOT NULL,
    actor TEXT NOT NULL, utc TEXT NOT NULL, PRIMARY KEY (kind, key));
CREATE UNIQUE INDEX ux_public_ids ON public_ids(kind, lower(public_id));
CREATE TABLE public_id_log (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, old_id TEXT,
    new_id TEXT NOT NULL, rev INTEGER NOT NULL, actor TEXT NOT NULL, utc TEXT NOT NULL);
CREATE TABLE module_requests (request_id INTEGER PRIMARY KEY, utc TEXT NOT NULL, requester TEXT NOT NULL,
    model TEXT NOT NULL, purpose TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, status_utc TEXT NOT NULL, handler TEXT, reason TEXT, tx_id TEXT, device_uid TEXT,
    json TEXT NOT NULL DEFAULT '{}');
INSERT INTO module_requests(request_id, utc, requester, model, purpose, location, note, status, status_utc, handler,
    reason, json)
  SELECT e.id, e.utc, coalesce(e.actor, 'unknown'), coalesce(json_extract(e.payload_json, '$.model'), ''),
    coalesce(json_extract(e.payload_json, '$.purpose'), ''), coalesce(json_extract(e.payload_json, '$.location'), ''),
    coalesce(json_extract(e.payload_json, '$.note'), ''),
    CASE WHEN c.id IS NULL THEN 'REQUESTED' ELSE 'CLOSED' END, coalesce(c.utc, e.utc), c.actor,
    json_extract(c.payload_json, '$.outcome'), '{"from": "0.7.0 event log"}'
  FROM events e LEFT JOIN events c ON c.kind = 'MODULE_REGISTRATION_CLOSED'
    AND json_extract(c.payload_json, '$.request_id') = e.id
  WHERE e.kind = 'MODULE_REGISTRATION_REQUESTED'
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


def migrate(con: sqlite3.Connection, db_path: str | Path | None = None, *, backup_dir: str | Path | None = None,
            reserve_bytes: int = 0) -> list[int]:
    """Apply pending migrations. If the DB already has data, a consistent full-size safety copy is taken
    first; it is refused (and nothing migrated) if that copy would take free disk below `reserve_bytes`."""
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
        backup_path = backup_to(con, bdir / f"pre-migrate-v{current}-{utc_iso().replace(':', '')}.sqlite3",
                                reserve_bytes=reserve_bytes)
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
