"""Single bounded historian writer.

All writes go through one thread and one connection. A cycle (all configured points) is one
transaction; events and alarm updates are batched with it. Command intents/outcomes are synchronous:
the caller waits for the commit, so an intent is durable before any side effect.
"""
from __future__ import annotations

import json
import queue
import shutil
import sqlite3
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..config.schema import InstallationConfig
from ..control.alarms import AlarmOccurrence
from ..domain.model import CommandRecord, Sample
from .schema import connect, migrate, utc_iso


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v)


def _iso(v: datetime | None) -> str | None:
    return v.isoformat(timespec="microseconds") if v else None


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
                                       "dropped_cycles": 0, "commits": 0, "degraded_reason": None}
        self._thread = threading.Thread(target=self._run, name="historian-writer", daemon=True)
        self._thread.start()
        self.submit(lambda c: c.execute(
            "INSERT INTO process_sessions(session_id,started_utc,config_id,config_version,profile,software_version)"
            " VALUES(?,?,?,?,?,?)", (session_id, utc_iso(), cfg.config_id, cfg.config_version, cfg.profile.value,
                                     __version__)), wait=True)

    # ------------------------------------------------------------------ queue plumbing
    def submit(self, fn: Callable[[sqlite3.Connection], Any], *, wait: bool = False, timeout: float = 3.0,
               droppable: bool = False):
        fut: Future = Future()
        try:
            self._q.put((fn, fut), block=not droppable, timeout=None if droppable else timeout)
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
        while not self._stop.is_set() or not self._q.empty():
            try:
                first = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < 64:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            try:
                if self.fail_writes:
                    raise sqlite3.OperationalError("disk I/O error (simulated)")
                self._con.execute("BEGIN IMMEDIATE")
                results = [fn(self._con) for fn, _ in batch]
                self._con.execute("COMMIT")
                self.health.update(ok=True, last_commit_utc=utc_iso(), last_error=None, degraded_reason=None)
                self.health["commits"] += 1
                for (_, fut), r in zip(batch, results):
                    fut.set_result(r)
            except Exception as e:  # whole batch fails together: nothing is half-written
                try:
                    self._con.execute("ROLLBACK")
                except Exception:
                    pass
                self.health["last_error"] = repr(e)
                self._degrade(f"historian write failed: {e!r}")
                for _, fut in batch:
                    if not fut.done():
                        fut.set_exception(StorageUnavailable(str(e)))

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

    def write_cycle(self, cycle_id: str, samples: list[Sample], *, run_id: str | None, sim_s: float | None) -> None:
        persisted = utc_iso()
        rows = [(s.point_id, _iso(s.acquired_utc), persisted, s.sequence, s.quality.value, s.reason, _num(s.raw_value),
                 type(s.raw_value).__name__ if s.raw_value is not None else None, s.raw_unit, _num(s.value), s.unit,
                 s.calibration_ref, s.origin.value, s.source_profile, s.config_version, s.device_uid, s.bus_id,
                 s.session_id, s.sim_run_id, s.sim_time_s, json.dumps(s.override) if s.override else None,
                 _num(s.last_good_value), _iso(s.last_good_utc)) for s in samples]

        def op(c: sqlite3.Connection):
            cur = c.execute("INSERT INTO cycles(cycle_id,session_id,run_id,sim_s,persisted_utc,n_points)"
                            " VALUES(?,?,?,?,?,?)", (cycle_id, self.session_id, run_id, sim_s, persisted, len(rows)))
            pk = cur.lastrowid
            c.executemany("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,reason,"
                          "raw_value,raw_type,raw_unit,value,unit,calibration_ref,origin,source_profile,config_version,"
                          "device_uid,bus_id,session_id,sim_run_id,sim_s,override_json,last_good_value,last_good_utc)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(pk,) + r for r in rows])
        self.submit(op, droppable=True)

    def cycle(self, run, samples: list[Sample]) -> None:  # SimulationRuntime HistorySink
        self.write_cycle(f"{run.run_id}:{run.tick}", samples, run_id=run.run_id, sim_s=run.sim_s)

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

    # ------------------------------------------------------------------ reads / storage health
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._read_lock:
            self._read.row_factory = sqlite3.Row
            return self._read.execute(sql, params).fetchall()

    def storage(self) -> dict:
        files = [self.db_path, Path(str(self.db_path) + "-wal")]
        size = sum(f.stat().st_size for f in files if f.exists())
        du = shutil.disk_usage(self.db_path.parent)
        h = self.cfg.historian
        st = {"db_bytes": size, "free_bytes": du.free, "budget_bytes": h.db_budget_bytes,
              "reserve_bytes": h.free_space_reserve_bytes, "over_budget": size > h.db_budget_bytes,
              "below_reserve": du.free < h.free_space_reserve_bytes, "queue_depth": self._q.qsize(),
              "retention_days": h.retention_days, "retention_provenance": h.retention_provenance}
        return st

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
