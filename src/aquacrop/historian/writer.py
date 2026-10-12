"""Single bounded historian writer.

All writes go through one thread and one connection. A cycle (all configured points) is one
transaction; events and alarm updates are batched with it. Command intents/outcomes are synchronous:
the caller waits for the commit, so an intent is durable before any side effect.
"""
from __future__ import annotations

import hashlib
import json
import queue
import shutil
import sqlite3
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..config.schema import InstallationConfig
from ..control.alarms import AlarmOccurrence
from ..domain.model import CommandRecord, Sample
from .recording import slot_cycle_id, slot_epoch
from .schema import connect, migrate, utc_iso

# Newest wall-time snapshot. Wall-time labels only ever increase with cycle_pk (the writer refuses any
# label that is not after this one), so this is a reverse rowid scan that stops at the first match.
NEWEST_WALL_CYCLE_SQL = ("SELECT cycle_id, persisted_utc FROM cycles WHERE cadence_basis='WALL' "
                         "ORDER BY cycle_pk DESC LIMIT 1")


def newest_wall_label(c: sqlite3.Connection) -> float | None:
    row = c.execute(NEWEST_WALL_CYCLE_SQL).fetchone()
    if row is None:
        return None
    t = slot_epoch(row[0])
    return t if t is not None else datetime.fromisoformat(row[1]).timestamp()


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v)


def _iso(v: datetime | None) -> str | None:
    return v.isoformat(timespec="microseconds") if v else None


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            out |= _flatten(v, f"{prefix}.{k}" if prefix else str(k))
        return out
    return {prefix: d}


def _diff(old: dict, new: dict) -> list[dict]:
    a, b = _flatten(old), _flatten(new)
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k, "<absent>") != b.get(k, "<absent>"):
            out.append({"key": k, "from": str(a.get(k, "<absent>"))[:200], "to": str(b.get(k, "<absent>"))[:200]})
    return out


def capacity_report(con: sqlite3.Connection, db_path: Path, cfg: InstallationConfig, *,
                    snapshots_paused: bool = False, queue_depth: int = 0, detail: bool = False) -> dict:
    """Read-only and cheap by default (file sizes, page counts, two index lookups). `detail` adds the
    measured growth of the last 7 days (an index range scan; meant for the `storage report` command)."""
    files = [db_path, Path(str(db_path) + "-wal")]
    size = sum(f.stat().st_size for f in files if f.exists())
    du = shutil.disk_usage(db_path.parent)
    h = cfg.historian
    page = con.execute("PRAGMA page_size").fetchone()[0]
    pages = con.execute("PRAGMA page_count").fetchone()[0]
    free_pages = con.execute("PRAGMA freelist_count").fetchone()[0]
    oldest, newest = con.execute("SELECT min(acquired_utc), max(acquired_utc) FROM samples").fetchone()
    used = (pages - free_pages) * page
    span_days = projected = None
    if oldest and newest:
        span_days = (datetime.fromisoformat(newest) - datetime.fromisoformat(oldest)).total_seconds() / 86400
        if span_days >= 1.0:
            projected = int(used * max(1.0, h.retention_days / span_days))
    out = {"db_bytes": size, "db_used_bytes": used, "db_reusable_bytes": free_pages * page,
           "free_bytes": du.free, "disk_bytes": du.total, "budget_bytes": h.db_budget_bytes,
           "warning_free_bytes": h.free_space_warning_bytes, "reserve_bytes": h.free_space_reserve_bytes,
           "snapshot_floor_bytes": h.snapshot_floor_bytes,
           "over_budget": size > h.db_budget_bytes, "below_warning": du.free < h.free_space_warning_bytes,
           "below_reserve": du.free < h.free_space_reserve_bytes,
           "below_snapshot_floor": du.free < h.snapshot_floor_bytes,
           "snapshots_paused": snapshots_paused, "queue_depth": queue_depth,
           "oldest_sample_utc": oldest, "newest_sample_utc": newest, "history_span_days": span_days,
           "projected_window_bytes": projected,
           "projected_over_budget": projected is not None and projected > h.db_budget_bytes,
           "recording_interval_s": h.recording_interval_s,
           "retention_days": h.retention_days, "retention_provenance": h.retention_provenance}
    if detail and newest:
        since = (datetime.fromisoformat(newest) - timedelta(days=7)).isoformat(timespec="microseconds")
        n_samples = con.execute("SELECT count(*) FROM samples WHERE acquired_utc >= ?", (since,)).fetchone()[0]
        n_events = con.execute("SELECT count(*) FROM events WHERE utc >= ?", (since,)).fetchone()[0]
        n_all = con.execute("SELECT count(*) FROM samples").fetchone()[0]
        days = min(7.0, span_days or 0.0)
        per_row = used / n_all if n_all else None
        out["last_7_days"] = {"days_covered": days, "samples": n_samples, "events": n_events,
                              "avg_db_bytes_per_sample_row": per_row,
                              "estimated_growth_bytes_per_day": (n_samples * per_row / days) if days and per_row
                              else None}
    return out


class StorageUnavailable(OSError):
    pass


class Historian:
    def __init__(self, db_path: str | Path, cfg: InstallationConfig, session_id: str, *, queue_max: int = 512,
                 backend_name: str = "SIMULATED"):
        self.db_path = Path(db_path)
        self.cfg = cfg
        self.session_id = session_id
        self.backend_name = backend_name
        con = connect(self.db_path)
        self.applied_migrations = migrate(con, self.db_path)
        self._con = con
        self._read_lock = threading.Lock()
        self._read = connect(self.db_path)
        self._q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._stop = threading.Event()
        self.fail_writes = False   # test hook: simulate disk/database failure
        self.health: dict[str, Any] = {"ok": True, "last_commit_utc": None, "last_error": None,
                                       "dropped_cycles": 0, "commits": 0, "degraded_reason": None,
                                       "paused_snapshots": 0, "labels_refused": 0}
        self.snapshots_paused = False
        self._pause = {"count": 0, "first": None, "last": None}   # labels not written in the current pause
        self._thread = threading.Thread(target=self._run, name="historian-writer", daemon=True)
        self._thread.start()
        self.submit(lambda c: c.execute(
            "INSERT INTO process_sessions(session_id,started_utc,config_id,config_version,profile,software_version)"
            " VALUES(?,?,?,?,?,?)", (session_id, utc_iso(), cfg.config_id, cfg.config_version, cfg.profile.value,
                                     __version__)), wait=True)

    # ------------------------------------------------------------------ queue plumbing
    def submit(self, fn: Callable[[sqlite3.Connection], Any], *, wait: bool = False, timeout: float = 3.0,
               droppable: bool = False, solo_without_fk: bool = False):
        """Queue `fn` for the writer thread. `solo_without_fk` runs it in its own transaction with
        foreign-key enforcement off (bulk retention deletes; the caller keeps references consistent)."""
        fut: Future = Future()
        try:
            self._q.put((fn, fut, solo_without_fk), block=not droppable, timeout=None if droppable else timeout)
        except queue.Full:
            if droppable:
                self.health["dropped_cycles"] += 1
                self._degrade("historian queue full: scheduled history dropped (counted, not silent)")
                return None
            raise StorageUnavailable("historian queue full")
        if wait:
            return fut.result(timeout=timeout)
        return fut

    def _degrade(self, reason: str) -> None:
        self.health["ok"] = False
        self.health["degraded_reason"] = reason

    def _run(self) -> None:
        stash = None
        while not self._stop.is_set() or not self._q.empty() or stash is not None:
            if stash is not None:
                first, stash = stash, None
            else:
                try:
                    first = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
            if first[2]:
                self._execute([first], fk_off=True)
                continue
            batch = [first]
            while len(batch) < 64:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item[2]:
                    stash = item          # solo item runs right after this batch, in queue order
                    break
                batch.append(item)
            self._execute(batch)

    def _execute(self, batch: list, *, fk_off: bool = False) -> None:
        try:
            if self.fail_writes:
                raise sqlite3.OperationalError("disk I/O error (simulated)")
            if fk_off:
                self._con.execute("PRAGMA foreign_keys=OFF")
            self._con.execute("BEGIN IMMEDIATE")
            results = [fn(self._con) for fn, _, _ in batch]
            self._con.execute("COMMIT")
            self.health.update(ok=True, last_commit_utc=utc_iso(), last_error=None, degraded_reason=None)
            self.health["commits"] += 1
            for (_, fut, _), r in zip(batch, results):
                fut.set_result(r)
        except Exception as e:  # whole batch fails together: nothing is half-written
            try:
                self._con.execute("ROLLBACK")
            except Exception:
                pass
            self.health["last_error"] = repr(e)
            self._degrade(f"historian write failed: {e!r}")
            for _, fut, _ in batch:
                if not fut.done():
                    fut.set_exception(StorageUnavailable(str(e)))
        finally:
            if fk_off:
                try:
                    self._con.execute("PRAGMA foreign_keys=ON")
                except Exception:
                    pass

    def flush(self, timeout: float = 5.0) -> None:
        self.submit(lambda c: None, wait=True, timeout=timeout)

    def close(self) -> None:
        try:
            self.submit(lambda c: c.execute("UPDATE process_sessions SET ended_utc=?, clean_shutdown=1 "
                                            "WHERE session_id=?", (utc_iso(), self.session_id)), wait=True)
        except Exception:
            pass
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        self._con.close()
        self._read.close()

    # ------------------------------------------------------------------ HistorySink
    def register_run(self, run_id: str, seed: int, provenance: str, reason: str) -> None:
        self.submit(lambda c: c.execute("INSERT OR IGNORE INTO sim_runs VALUES(?,?,?,?,?,?,?,?)",
                                        (run_id, self.session_id, seed, self.cfg.config_id, self.cfg.config_version,
                                         utc_iso(), provenance, reason)))

    def write_cycle(self, cycle_id: str, samples: list[Sample], *, run_id: str | None, sim_s: float | None,
                    cadence_s: float | None = None, cadence_basis: str | None = None,
                    label_s: float | None = None) -> None:
        """One cycle in one transaction. With `label_s` (wall-time snapshots) the label must be after the
        newest one stored; the check runs inside the writer's BEGIN IMMEDIATE transaction, so no other
        connection or process can write a label in between. A refused label is counted and recorded."""
        persisted = utc_iso()
        rows = [(s.point_id, _iso(s.acquired_utc), persisted, s.sequence, s.quality.value, s.reason, _num(s.raw_value),
                 type(s.raw_value).__name__ if s.raw_value is not None else None, s.raw_unit, _num(s.value), s.unit,
                 s.calibration_ref, s.origin.value, s.source_profile, s.config_version, s.device_uid, s.bus_id,
                 s.session_id, s.sim_run_id, s.sim_time_s, json.dumps(s.override) if s.override else None,
                 _num(s.last_good_value), _iso(s.last_good_utc)) for s in samples]

        def op(c: sqlite3.Connection):
            if label_s is not None:
                newest = newest_wall_label(c)
                if newest is not None and label_s <= newest + 1e-6:
                    self.health["labels_refused"] += 1
                    c.execute("INSERT INTO events(utc,kind,actor,session_id,run_id,sim_s,payload_json) "
                              "VALUES(?,?,?,?,?,?,?)", (persisted, "HISTORY_LABEL_REFUSED", "system", self.session_id,
                                                        run_id, sim_s, json.dumps({
                                                            "cycle_id": cycle_id, "newest_stored_label_utc":
                                                            datetime.fromtimestamp(newest, timezone.utc).isoformat(),
                                                            "note": "a snapshot label is written once and in order; "
                                                                    "this one was not written"})))
                    return False
            cur = c.execute("INSERT INTO cycles(cycle_id,session_id,run_id,sim_s,persisted_utc,n_points,cadence_s,"
                            "cadence_basis) VALUES(?,?,?,?,?,?,?,?)", (cycle_id, self.session_id, run_id, sim_s,
                                                                      persisted, len(rows), cadence_s, cadence_basis))
            pk = cur.lastrowid
            c.executemany("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,reason,"
                          "raw_value,raw_type,raw_unit,value,unit,calibration_ref,origin,source_profile,config_version,"
                          "device_uid,bus_id,session_id,sim_run_id,sim_s,override_json,last_good_value,last_good_utc)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(pk,) + r for r in rows])
            return True
        self.submit(op, droppable=True)

    def cycle(self, run, samples: list[Sample]) -> None:
        """Optional simulation-time recorder (`historian.sim_time_recording_s`, off by default): a
        separately labelled series for desktop analysis, never the installation's history cadence."""
        if self.snapshot_permitted():
            self.write_cycle(f"simtime/{run.run_id}/{run.tick}", samples, run_id=run.run_id, sim_s=run.sim_s,
                             cadence_s=run.sim_time_recording_s, cadence_basis="SIMULATION_TIME")

    def snapshot(self, at: datetime, samples: list[Sample], interval_s: float, *, run_id: str | None = None,
                 sim_s: float | None = None) -> bool:
        """Wall-time snapshot of every point (real and simulation profiles). `run_id`/`sim_s` record a
        simulation's provenance. False if paused for disk (the label is then reported, not written)."""
        if not self.snapshot_permitted(at):
            return False
        self.write_cycle(slot_cycle_id(at, interval_s), samples, run_id=run_id, sim_s=sim_s, cadence_s=interval_s,
                         cadence_basis="WALL", label_s=at.timestamp())
        return True

    def newest_wall_label(self) -> float | None:
        """Newest wall-time label stored (epoch seconds): where a restarted recorder continues."""
        with self._read_lock:
            return newest_wall_label(self._read)

    def snapshot_permitted(self, at: datetime | None = None) -> bool:
        """Low/full-disk handling: below `snapshot_floor_bytes` free, periodic snapshots pause (counted,
        announced once, alarmed) so events, commands and alarms keep the remaining space. Recording
        resumes by itself once free space is back above the reserve. Nothing is deleted to make room."""
        h = self.cfg.historian
        free = shutil.disk_usage(self.db_path.parent).free
        if not self.snapshots_paused and free < h.snapshot_floor_bytes:
            self.snapshots_paused = True
            self.event("HISTORY_SNAPSHOTS_PAUSED", {"free_bytes": free, "floor_bytes": h.snapshot_floor_bytes,
                                                    "note": "periodic snapshots paused; events, commands and "
                                                            "alarms are still recorded; nothing was deleted"},
                       actor="system")
        elif self.snapshots_paused and free >= max(h.free_space_reserve_bytes, h.snapshot_floor_bytes + 64 * 2**20):
            self.snapshots_paused = False
            pause, self._pause = self._pause, {"count": 0, "first": None, "last": None}
            self.event("HISTORY_SNAPSHOTS_RESUMED", {
                "free_bytes": free, "snapshots_not_written": pause["count"],
                "first_label_not_written_utc": pause["first"], "last_label_not_written_utc": pause["last"],
                "note": "the paused labels are not back-filled"}, actor="system")
        if self.snapshots_paused:
            self.health["paused_snapshots"] += 1
            self._pause["count"] += 1
            if at is not None:
                self._pause["first"] = self._pause["first"] or at.isoformat()
                self._pause["last"] = at.isoformat()
            return False
        return True

    def alarms(self, occurrences: list[AlarmOccurrence]) -> None:
        if not occurrences:
            return
        rows = [(o.occurrence_id, o.rule_id, o.target, o.severity, int(o.closed), int(o.active), int(o.acknowledged),
                 _iso(o.first_seen_utc), _iso(o.last_seen_utc), utc_iso(), json.dumps(o.to_dict()))
                for o in occurrences]
        self.submit(lambda c: c.executemany("INSERT OR REPLACE INTO alarm_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                            rows))

    def load_open_alarms(self) -> list[AlarmOccurrence]:
        with self._read_lock:
            rows = self._read.execute("SELECT json FROM alarm_occurrences WHERE closed=0").fetchall()
        out = []
        for (j,) in rows:
            d = json.loads(j)
            d.pop("reset_required", None)
            for k in ("first_seen_utc", "last_seen_utc", "cleared_utc", "ack_utc", "reset_utc"):
                if d.get(k):
                    d[k] = datetime.fromisoformat(d[k])
            out.append(AlarmOccurrence(**d))
        return out

    # ------------------------------------------------------------------ AuditSink
    def command_intent(self, rec: CommandRecord) -> None:
        c = rec.command
        row = (c.request_id, c.actor, c.target, int(c.desired), c.origin.value, c.reason, _iso(c.issued_utc),
               _iso(c.expires_utc), c.expected_config_version,
               c.expected_controller_state.value if c.expected_controller_state else None, c.lease_id,
               int(c.priority), self.backend_name, self.session_id, utc_iso(), rec.outcome.value, rec.detail)
        self.submit(lambda con: con.execute(
            "INSERT INTO commands(request_id,actor,target,desired,origin,reason,issued_utc,expires_utc,"
            "expected_config_version,expected_state,lease_id,priority,backend,session_id,intent_utc,outcome,detail)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row), wait=True)

    def command_outcome(self, rec: CommandRecord) -> None:
        row = (rec.outcome.value, rec.detail, None if rec.readback is None else int(rec.readback), utc_iso(),
               rec.command.request_id)
        self.submit(lambda con: con.execute(
            "UPDATE commands SET outcome=?, detail=?, readback=?, outcome_utc=? WHERE request_id=?", row), wait=True)

    def event(self, kind: str, payload: dict, *, actor: str | None = None, sim_s: float | None = None) -> None:
        row = (utc_iso(), kind, actor, self.session_id, payload.get("run_id"), sim_s,
               json.dumps(payload, default=str))
        self.submit(lambda c: c.execute("INSERT INTO events(utc,kind,actor,session_id,run_id,sim_s,payload_json)"
                                        " VALUES(?,?,?,?,?,?,?)", row))

    def reconcile_pending_commands(self) -> list[str]:
        """On restart: intents without a terminal outcome become OUTCOME_UNKNOWN (never replayed)."""
        def op(c):
            ids = [r[0] for r in c.execute("SELECT request_id FROM commands WHERE outcome IN "
                                           "('ACCEPTED','EXECUTING')").fetchall()]
            c.executemany("UPDATE commands SET outcome='OUTCOME_UNKNOWN', detail=coalesce(detail,'')||"
                          "' [reconciled at restart: outcome never persisted]', outcome_utc=? WHERE request_id=?",
                          [(utc_iso(), i) for i in ids])
            return ids
        return self.submit(op, wait=True)

    def record_config(self, cfg: InstallationConfig) -> dict:
        """CONFIG_LOADED at every start, plus CONFIG_CHANGED (with the changed keys) when the effective
        configuration differs from the one the previous process recorded."""
        doc = json.loads(cfg.model_dump_json())
        canon = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        sha = hashlib.sha256(canon.encode()).hexdigest()
        prev = self.query("SELECT payload_json FROM events WHERE kind='CONFIG_LOADED' AND session_id != ? "
                          "ORDER BY id DESC LIMIT 1", (self.session_id,))
        loaded = {"config_id": cfg.config_id, "config_version": cfg.config_version, "profile": cfg.profile.value,
                  "sha256": sha, "historian": doc.get("historian"), "config": doc}
        self.event("CONFIG_LOADED", loaded, actor="system")
        out = {"sha256": sha, "changed": None}
        if prev:
            old = json.loads(prev[0][0])
            if old.get("sha256") != sha:
                changes = _diff(old.get("config") or {}, doc)
                ch = {"from_sha256": old.get("sha256"), "to_sha256": sha,
                      "from_version": old.get("config_version"), "to_version": cfg.config_version,
                      "changes": changes[:200], "changes_total": len(changes)}
                self.event("CONFIG_CHANGED", ch, actor="system")
                out["changed"] = ch
        return out

    # ------------------------------------------------------------------ reads / storage health
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._read_lock:
            self._read.row_factory = sqlite3.Row
            return self._read.execute(sql, params).fetchall()

    def storage(self) -> dict:
        """Capacity picture for alarms, /api/status and `storage report`."""
        with self._read_lock:
            return capacity_report(self._read, self.db_path, self.cfg, snapshots_paused=self.snapshots_paused,
                                   queue_depth=self._q.qsize())

    def can_accept_starts(self) -> tuple[bool, str | None]:
        st = self.storage()
        if not self.health["ok"]:
            return False, self.health["degraded_reason"]
        if st["below_reserve"]:
            return False, "free disk below reserve"
        return True, None

    def wait_idle(self, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while self._q.qsize() and time.monotonic() < end:
            time.sleep(0.01)
        self.flush(timeout=timeout)
