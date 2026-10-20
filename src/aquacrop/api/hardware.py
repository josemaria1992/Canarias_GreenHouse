"""Hardware service for a HARDWARE_READ_ONLY installation (brief §4, §6, §8).

One worker thread per RS485 bus owns all traffic on that bus. Between polls it executes queued
bench commands, STOP priority first; each command is re-checked against the lease, its expiry and
the configuration version at the moment it runs (CommandService re-validates before dispatch).

Starting, polling, reconnecting and stopping send read frames only, unless the configuration lists
safe-state triggers (`safe_state.outputs_off_on`, ADR-0017): then, at each listed moment, every digital
output that reads ON is switched OFF (origin SAFE_STATE, FC05 once, FC01 readback, audited like any
command). START also covers a module that did not answer at start, at its first answer. "All outputs
OFF" does the same on request. Nothing here ever switches an output ON by itself or writes to a drive.
A drive with a Modbus register map (ADR-0015) is polled on its bus like a module (status word only);
its running and fault changes are written as events.

Polling, edge detection, alarm evaluation and the dashboard keep the bus poll rate. The historian is
separate: a recorder thread writes one complete snapshot of every point on each wall-clock boundary of
`historian.recording_interval_s` from what acquisition already holds (it never touches a bus). Edges,
alarm transitions, commands and outcomes are written when they happen.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections import deque
from concurrent.futures import Future
from datetime import datetime, timezone

from ..config.catalog import DIGITAL_IO_MODELS, ModelId
from ..config.schema import InstallationConfig
from ..control.alarms import AlarmEngine
from ..control.bench import BenchGate, close_stale_leases, historian_persistence
from ..control.commands import CommandService
from ..domain.model import (CommandOrigin, CommandRecord, ControllerState, Origin, Outcome, OutputCommand, PointId,
                            PointKind, Priority, Sample, utcnow)
from ..historian.recording import HistoryRecorder, complete_snapshot, stale_limit_s
from ..historian.retention import RetentionTask, evaluate_recorder_alarm, evaluate_storage_alarm
from ..historian.writer import Historian
from ..drivers.rtu_errors import RtuError
from ..io.modbus_backend import ModbusBackend, safe_state_policy
from ..io.sampling import AcqContext
from ..security.auth import Principal

MAX_FAILURES_PER_10_MIN = 5
RESTART_AFTER_FAILED_S = 30.0      # a FAILED bus worker waits this long, then polls again (counted in restarts)
SAFE_OFF_WAIT_S = 20.0


class BusWorker:
    """Serializes one bus: queued commands (priority, FIFO) run before the next due poll."""

    def __init__(self, bus_id: str, interval_s: float, poll, *, monotonic=time.monotonic,
                 restart_after_s: float = RESTART_AFTER_FAILED_S):
        self.bus_id, self.interval_s, self._poll, self._mono = bus_id, interval_s, poll, monotonic
        self.restart_after_s = restart_after_s
        self.restarts = 0
        self._heap: list = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._stop = False
        self._next_poll = 0.0
        self.state = "NOT_STARTED"
        self.heartbeat: float | None = None
        self.failures: deque[float] = deque(maxlen=20)
        self.last_error: str | None = None
        self.polls = 0
        self._thread = threading.Thread(target=self._run, name=f"bus-{bus_id}", daemon=True)

    def start(self) -> None:
        self.state = "RUNNING"
        self._thread.start()

    def submit(self, cmd: OutputCommand, execute) -> Future:
        return self.submit_job(int(cmd.priority), execute, cmd)

    def submit_job(self, priority: int, execute, cmd: OutputCommand | None = None) -> Future:
        """Queue work for this bus (a command, or a safe-state job when `cmd` is None); it runs before the next poll."""
        fut: Future = Future()
        with self._cv:
            if self._stop:
                raise RuntimeError("bus worker stopped")
            heapq.heappush(self._heap, (priority, next(self._seq), cmd, execute, fut))
            self._cv.notify()
        return fut

    def stop(self, cancel) -> None:
        with self._cv:
            self._stop = True
            pending, self._heap = self._heap, []
            self._cv.notify()
        for _, _, cmd, _, fut in pending:
            fut.set_result(cancel(cmd, "process stopping before dispatch") if cmd is not None else None)
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stop and not self._heap and self._mono() < self._next_poll:
                    self._cv.wait(timeout=max(0.0, min(0.5, self._next_poll - self._mono())))
                    self.heartbeat = self._mono()
                if self._stop:
                    self.state = "STOPPED"
                    return
                item = heapq.heappop(self._heap) if self._heap else None
            self.heartbeat = self._mono()
            if item is not None:
                _, _, cmd, execute, fut = item
                try:
                    fut.set_result(execute())
                except Exception as e:  # CommandService already maps dispatch errors; this is a bug guard
                    fut.set_exception(e)
                continue
            self._next_poll = self._mono() + self.interval_s
            try:
                self._poll(self.bus_id)
                self.polls += 1
                self.state = "RUNNING"
            except Exception as e:   # supervised: record, rate-limit, never silently die
                now = self._mono()
                self.failures.append(now)
                self.last_error = f"{utcnow().isoformat()} {e!r}"
                if len([t for t in self.failures if now - t < 600]) > MAX_FAILURES_PER_10_MIN:
                    self.state = "FAILED"
                    with self._cv:   # visible as FAILED, then one more attempt after the pause
                        self._cv.wait_for(lambda: self._stop, timeout=self.restart_after_s)
                        if self._stop:
                            self.state = "STOPPED"
                            return
                    self.failures.clear()
                    self.restarts += 1
                self.state = "RESTARTING"

    def health(self) -> dict:
        age = None if self.heartbeat is None else self._mono() - self.heartbeat
        ok = self.state == "RUNNING" and age is not None and age < max(5.0, 2 * self.interval_s)
        return {"bus_id": self.bus_id, "state": self.state, "heartbeat_age_s": age, "ok": ok, "polls": self.polls,
                "last_error": self.last_error, "restarts": self.restarts}


class HardwareService:
    kind = "hardware"

    def __init__(self, cfg: InstallationConfig, hist: Historian, session_id: str, *, monotonic=time.monotonic,
                 recording_interval_s: float | None = None, wall=time.time):
        # recording_interval_s is a test hook only; installations use historian.recording_interval_s.
        self.cfg, self.hist, self.session_id, self._mono = cfg, hist, session_id, monotonic
        self.lock = threading.RLock()
        self.samples: dict[str, Sample] = {}
        self.alarms = AlarmEngine(cfg, emit=lambda kind, payload: hist.event(kind, payload, actor="system"))
        self.alarms.restore(hist.load_open_alarms())
        self._cycle = itertools.count(1)
        self.backend = ModbusBackend(cfg, context=self._ctx, monotonic=monotonic)
        closed = close_stale_leases(hist, session_id)
        if closed:
            hist.event("BENCH_LEASES_CLOSED_AT_START", {"count": closed}, actor="system")
        arm, end, renew = historian_persistence(hist)
        self.gate = BenchGate(cfg, session_id, arm, end, self._preflight,
                              lambda kind, payload, actor: hist.event(kind, payload, actor=actor), monotonic,
                              on_end=self._lease_ended, persist_renew=renew)
        self.commands = CommandService(cfg, self.backend, hist, self._policy, lambda: ControllerState.STOPPED)
        self.safe_commands = CommandService(cfg, self.backend, hist, safe_state_policy, lambda: ControllerState.STOPPED)
        self.safe_triggers = frozenset(cfg.safe_state.outputs_off_on)
        self._off_at_first_answer: set[str] = set()   # START could not read these modules
        self._offline: set[str] = set()
        self.last_safe_off: dict | None = None
        self.poll_times: dict[str, float] = {}
        self.workers = {b.bus_id: BusWorker(b.bus_id, b.poll_interval_s, self.poll_bus, monotonic=monotonic)
                        for b in cfg.buses}
        self._sync_authorizer()
        self._prev_valid: dict[str, tuple[bool, datetime]] = {}
        self._prev_drive: dict[str, tuple[bool, bool, datetime]] = {}
        self._clock_ref: tuple[float, float] | None = None   # (monotonic, wall epoch) for jump detection
        self._stale_after = {b.bus_id: stale_limit_s(b.poll_interval_s) for b in cfg.buses}
        self.recorder = HistoryRecorder(recording_interval_s or cfg.historian.recording_interval_s,
                                        self.record_snapshot,
                                        on_event=lambda kind, payload: hist.event(kind, payload, actor="system"),
                                        high_water=hist.newest_wall_label, wall=wall, monotonic=monotonic)
        self.retention = RetentionTask(hist, wall=wall, monotonic=monotonic)
        self.offsite = None                 # OffsiteMonitor, attached by the app

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if "START" in self.safe_triggers:   # queued before the first poll of each bus
            self._queue_safe_off(self._digital_uids(), "START", "system")
        for w in self.workers.values():
            w.start()
        self.recorder.start()

    def stop(self) -> None:
        self.recorder.stop()
        if "STOP" in self.safe_triggers:
            futs = self._queue_safe_off(self._digital_uids(), "STOP", "system")
            for f in futs:
                try:
                    f.result(timeout=SAFE_OFF_WAIT_S)
                except Exception:
                    pass   # recorded by the job itself; stopping goes on
        for w in self.workers.values():
            w.stop(self._cancel)
        self.backend.close()   # releases ports; sends nothing
        with self.lock:
            dirty = self.alarms.take_dirty()
        if dirty:
            self.hist.alarms(dirty)

    def _ctx(self) -> AcqContext:
        return AcqContext(session_id=self.session_id, cycle_id=f"{self.session_id}:{next(self._cycle)}")

    # ------------------------------------------------------------------ acquisition
    def poll_bus(self, bus_id: str) -> None:
        t0 = self._mono()
        self._check_clock()
        got: list[Sample] = []
        status: dict[str, tuple[bool, str | None]] = {}
        for d in self.cfg.devices:
            if d.bus_id != bus_id:
                continue
            ri = self.backend.read_inputs(d.device_uid)
            got += ri.samples
            ok, err = ri.ok, ri.error
            if d.model in DIGITAL_IO_MODELS and ri.ok:
                ro = self.backend.read_outputs(d.device_uid)
                got += ro.samples
                ok, err = ro.ok, ro.error
            elif d.model in DIGITAL_IO_MODELS:
                # inputs already failed this cycle: don't spend another timeout on coils
                got += self.backend.unavailable(d.device_uid, ri.error or "device read failed", (PointKind.DO,)).samples
            status[d.device_uid] = (ok, err)
            self._note_answer(d.device_uid, d.model in DIGITAL_IO_MODELS, ok)
        edges = []
        for target, dv in self.backend.drives.items():
            if dv.m.bus_id == bus_id:
                st, err = self.backend.read_drive(target)
                status[target] = (st is not None, err)
                edges += self._drive_edges(target, st, self.cfg.bus(bus_id).poll_interval_s)
        with self.lock:
            edges = self._edges(got, self.cfg.bus(bus_id).poll_interval_s) + edges
            for s in got:
                self.samples[s.point_id] = s
            self.alarms.evaluate_devices(status, None)
            self.alarms.evaluate_samples(self.samples, self._mono(), None)
            evaluate_storage_alarm(self.hist, self.alarms)
            evaluate_recorder_alarm(self.recorder, self.alarms)
            self.retention.tick(self.alarms)     # clock trust is checked even while snapshots are held
            if self.offsite is not None:
                self.offsite.tick(self.alarms)   # reads state files only; never runs rclone or touches the card
            # Transitions are written now; refreshes of still-active alarms ride with the snapshot.
            dirty = self.alarms.take_dirty() if self.alarms.take_transitioned() else []
        if dirty:
            self.hist.alarms(dirty)
        for kind, payload in edges:
            self.hist.event(kind, payload, actor=None)
        self.gate.active()     # notices an expired lease even when no page is open (and runs its lease-end OFF)
        took = self._mono() - t0
        self.poll_times[bus_id] = took
        if took > self.cfg.bus(bus_id).poll_interval_s:
            self.hist.event("POLL_OVERRUN", {"bus_id": bus_id, "cycle_s": round(took, 3),
                                             "interval_s": self.cfg.bus(bus_id).poll_interval_s}, actor="system")

    # ------------------------------------------------------------------ historian snapshots
    def _stale_reason(self, s: Sample, now: datetime) -> str | None:
        limit = self._stale_after.get(s.bus_id, 9.0)
        age = (now - s.acquired_utc).total_seconds()
        if age > limit:
            return f"stale: last acquired {age:.1f} s before the snapshot (limit {limit:.1f} s)"
        return None

    def record_snapshot(self, at: datetime, interval_s: float) -> bool:
        """Runs on the recorder thread at each boundary: copy the latest samples, mark stale or missing
        points, write one complete cycle. Never touches a bus."""
        now = datetime.now(timezone.utc)
        with self.lock:
            latest = dict(self.samples)
            evaluate_storage_alarm(self.hist, self.alarms)
            dirty = self.alarms.take_dirty()
            self.alarms.take_transitioned()
        snap = complete_snapshot(self.cfg, latest, now=at, ctx=self._ctx(), origin=Origin.REAL,
                                 stale_reason=lambda s: self._stale_reason(s, now))
        stored = self.hist.snapshot(at, snap, interval_s)
        if dirty:
            self.hist.alarms(dirty)
        return stored

    def _edges(self, got: list[Sample], interval_s: float) -> list[tuple[str, dict]]:
        """Physical DI/DO edges = differences between consecutive valid observations. The change
        happened somewhere between the two acquisition times; that window is recorded."""
        out = []
        for s in got:
            if s.raw_unit != "bool" and s.raw_value is not None:
                continue
            prev = self._prev_valid.get(s.point_id)
            if s.quality.value not in ("GOOD", "OVERRIDDEN"):
                if prev is not None:
                    out.append(("OBSERVATION_GAP", {"point": s.point_id, "reason": s.reason,
                                                    "last_valid_utc": prev[1].isoformat()}))
                self._prev_valid.pop(s.point_id, None)
                continue
            if prev is not None and prev[0] != s.value:
                kind = "DO_EDGE" if ".DO" in s.point_id else "DI_EDGE"
                out.append((kind, {"point": s.point_id, "from": prev[0], "to": s.value, "origin": "REAL",
                                   "not_before_utc": prev[1].isoformat(), "not_after_utc": s.acquired_utc.isoformat(),
                                   "resolution_s": interval_s,
                                   "note": "polled observation; actual change time is within the window"}))
            self._prev_valid[s.point_id] = (bool(s.value), s.acquired_utc)
        return out

    def _drive_edges(self, target: str, st, interval_s: float) -> list[tuple[str, dict]]:
        """Running and fault changes of a drive's status word, with the polling window they fall in."""
        now, prev = utcnow(), self._prev_drive.get(target)
        if st is None:
            if prev is not None:
                self._prev_drive.pop(target)
                return [("OBSERVATION_GAP", {"point": target, "reason": "drive status unreadable",
                                             "last_valid_utc": prev[2].isoformat()})]
            return []
        self._prev_drive[target] = (st.running, st.fault, now)
        if prev is None:
            return []
        return [("DRIVE_STATUS_EDGE", {"target": target, "what": what, "from": was, "to": is_,
                                       "status_word": f"0x{st.word:04X}", "origin": "REAL",
                                       "not_before_utc": prev[2].isoformat(), "not_after_utc": now.isoformat(),
                                       "resolution_s": interval_s,
                                       "note": "polled status word; the change happened within the window"})
                for what, was, is_ in (("running", prev[0], st.running), ("fault", prev[1], st.fault))
                if was != is_]

    def _check_clock(self) -> None:
        """Record wall-clock steps (NTP corrections, RTC-less boot) against the monotonic clock."""
        mono, wall = self._mono(), time.time()
        if self._clock_ref is not None:
            dm, dw = mono - self._clock_ref[0], wall - self._clock_ref[1]
            if abs(dw - dm) > 2.0:
                self.hist.event("WALL_CLOCK_STEP", {"step_s": round(dw - dm, 3), "backward": dw < dm,
                                                    "note": "timing uses the monotonic clock; UTC stamps "
                                                            "around this point are uncertain"}, actor="system")
        self._clock_ref = (mono, wall)

    # ------------------------------------------------------------------ safe state (ADR-0017)
    def _digital_uids(self) -> list[str]:
        return [d.device_uid for d in self.cfg.devices if d.model in DIGITAL_IO_MODELS]

    def _note_answer(self, uid: str, digital: bool, ok: bool) -> None:
        """Runs on the bus thread after each module read: a module that answers again after a gap, or for the
        first time after START could not read it, has its ON outputs switched OFF when configured."""
        if not ok:
            self._offline.add(uid)
            return
        if not digital:
            self._offline.discard(uid)
            return
        if uid in self._off_at_first_answer:
            self._off_at_first_answer.discard(uid)
            self._offline.discard(uid)
            self._queue_safe_off([uid], "START", "system")
        elif uid in self._offline:
            self._offline.discard(uid)
            if "MODULE_RETURN" in self.safe_triggers:
                self._queue_safe_off([uid], "MODULE_RETURN", "system")

    def _lease_ended(self, lease) -> None:
        if "LEASE_END" not in self.safe_triggers:
            return
        by_dev: dict[str, set[int]] = {}
        for t in lease.allowlist:
            if t.startswith("drive:"):
                continue
            pid = PointId.parse(t)
            by_dev.setdefault(pid.device_uid, set()).add(pid.channel)
        for uid, chans in by_dev.items():
            self._queue_safe_off([uid], "LEASE_END", lease.username, only=chans)

    def _queue_safe_off(self, uids: list[str], trigger: str, actor: str, *, only: set[int] | None = None) -> list[Future]:
        futs = []
        for uid in uids:
            w = self.workers[self.cfg.device(uid).bus_id]
            try:
                futs.append(w.submit_job(int(Priority.STOP), lambda u=uid: self._safe_off_device(u, trigger, actor, only)))
            except RuntimeError:
                pass    # the worker is stopping
        return futs

    def _safe_off_device(self, uid: str, trigger: str, actor: str, only: set[int] | None) -> dict:
        """Runs on the bus thread: read the coils, switch each one that reads ON to OFF, record the result."""
        rec: dict = {"trigger": trigger, "device_uid": uid, "utc": utcnow().isoformat()}
        try:
            on = self.backend.coils_on(uid)
        except RtuError as e:
            rec |= {"result": "NOT_READ", "detail": f"{e.kind}: {e}", "switched": []}
            if trigger == "START":
                self._off_at_first_answer.add(uid)
                rec["detail"] += "; it is switched OFF at its first answer"
            self._record_safe_off(rec, actor)
            return rec
        targets = [ch for ch in on if only is None or ch in only]
        switched = []
        for ch in targets:
            cmd = self.safe_commands.build(actor=actor, target=f"{uid}.DO{ch}", desired=False,
                                           origin=CommandOrigin.SAFE_STATE, reason=f"safe state ({trigger})")
            r = self.safe_commands.submit(cmd)
            switched.append({"point": cmd.target, "outcome": r.outcome.value, "detail": r.detail})
        ok = all(x["outcome"] == Outcome.READBACK_CONFIRMED.value for x in switched)
        rec |= {"result": "OFF_CONFIRMED" if ok else "INCOMPLETE", "were_on": [f"{uid}.DO{c}" for c in on],
                "switched": switched}
        self._record_safe_off(rec, actor)
        return rec

    def _record_safe_off(self, rec: dict, actor: str) -> None:
        self.last_safe_off = rec
        self.hist.event("SAFE_STATE_OFF", rec, actor=actor)

    def all_off(self, actor: str) -> list[dict]:
        """"All outputs OFF" on request: every digital module, every output that reads ON (no lease needed)."""
        out = []
        for f in self._queue_safe_off(self._digital_uids(), "OPERATOR", actor):
            try:
                out.append(f.result(timeout=SAFE_OFF_WAIT_S))
            except Exception as e:
                out.append({"result": "NOT_DONE", "detail": repr(e)})
        return out

    def renew(self, p: Principal, lease_id: str | None, duration_s: int):
        return self.gate.renew(p, lease_id, duration_s)

    # ------------------------------------------------------------------ bench
    def _preflight(self, devices: set[str]) -> str | None:
        for uid in sorted(devices):
            if uid in self.backend.drives:
                try:
                    st = self.backend.drives[uid].read_status()
                except Exception as e:
                    return f"{uid}: status read failed ({getattr(e, 'kind', type(e).__name__)})"
                self.backend.drive_status[uid] = st
                if st.fault:
                    return f"{uid}: the drive reports a fault (status 0x{st.word:04X}); reset it at the drive"
                continue
            wd = self.backend.devices[uid]
            try:
                modes = wd.read_relay_modes()
            except Exception as e:
                return f"{uid}: relay mode read failed ({getattr(e, 'kind', type(e).__name__)})"
            self.backend.relay_modes[uid] = modes
            if not modes.all_normal:
                return f"{uid}: relay modes {modes.describe()} are not all NORMAL"
        return None

    def _policy(self, cmd: OutputCommand) -> str | None:
        if (why := self.gate.authorize(cmd)) is not None:
            return why
        if cmd.desired:
            ok, why = self.hist.can_accept_starts()
            if not ok:
                return f"storage not durable ({why}): ON refused"
        return None

    def _sync_authorizer(self) -> None:
        # The backend asks the gate on every write; with no lease the gate refuses everything.
        self.backend.authorize = self.gate.authorize

    def arm(self, p: Principal, **kw):
        return self.gate.arm(p, **kw)

    def revoke(self, reason: str, actor: str | None) -> bool:
        return self.gate.revoke(reason, actor)

    def bench_command(self, p: Principal, *, target: str, desired: bool, reason: str, lease_id: str | None,
                      request_id: str | None = None, ttl_s: float = 10.0) -> CommandRecord:
        cmd = self.commands.build(actor=p.username, target=target, desired=desired, origin=CommandOrigin.BENCH,
                                  reason=reason, ttl_s=ttl_s, lease_id=lease_id, request_id=request_id)
        if (why := self.gate.check_session(p, lease_id)) is not None:
            rec = CommandRecord(cmd, Outcome.ACCEPTED)
            rec.advance(Outcome.REJECTED, why)
            self.commands._audit_rejected(rec)
            return rec
        try:
            ad = self.cfg.drive_target(target)
            bus_id = ad.modbus.bus_id if ad is not None else self.cfg.device(target.split(".", 1)[0]).bus_id
        except KeyError:
            rec = CommandRecord(cmd, Outcome.ACCEPTED)
            rec.advance(Outcome.REJECTED, "unknown target")
            self.commands._audit_rejected(rec)
            return rec
        fut = self.workers[bus_id].submit(cmd, lambda: self.commands.submit(cmd))
        return fut.result(timeout=ttl_s + 30)

    def _cancel(self, cmd: OutputCommand, why: str) -> CommandRecord:
        rec = CommandRecord(cmd, Outcome.ACCEPTED)
        rec.advance(Outcome.CANCELLED, why)
        self.commands._audit_rejected(rec)
        return rec

    # ------------------------------------------------------------------ views
    @property
    def banner(self) -> str:
        lease = self.gate.active()
        if lease is None:
            return "REAL — READ ONLY"
        return f"REAL — BENCH WRITES ARMED ({lease.username}, until {lease.expires_utc.isoformat(timespec='seconds')})"

    def task_health(self) -> dict:
        hs = [w.health() for w in self.workers.values()]
        rec = self.recorder.health()
        rec_ok = rec["ok"] or rec["state"] == "NOT_STARTED"
        return {"state": "RUNNING" if all(h["ok"] for h in hs) and rec_ok else
                ("FAILED" if any(h["state"] == "FAILED" for h in hs) else "DEGRADED"),
                "ok": all(h["ok"] for h in hs) and rec_ok, "buses": hs, "restarts": sum(h["restarts"] for h in hs),
                "last_error": next((h["last_error"] for h in hs if h["last_error"]), None),
                "heartbeat_age_s": max((h["heartbeat_age_s"] or 0) for h in hs) if hs else None,
                "recorder": rec, "retention": self.retention.view()}

    def drive_view(self) -> dict:
        out = {}
        for a in self.cfg.drive_adapters:
            m = a.modbus
            st = self.backend.drive_status.get(a.target)
            h = self.backend.health(a.target) if a.target in self.backend.drives else None
            out[a.target] = {"adapter_id": a.adapter_id, "kind": a.kind, "signals": a.signals,
                             "bus_id": m.bus_id if m else None, "address": m.address if m else None,
                             "drive_model": m.drive_model if m else None,
                             "provenance": m.provenance if m else None,
                             "comm_loss_timeout_s": m.comm_loss_timeout_s if m else None,
                             "online": h.online if h else None, "last_error": h.last_error if h else None,
                             "status": st.view() if st is not None else None}
        return out

    def relay_mode_view(self) -> dict:
        return {uid: (m.describe() if m is not None else None) for uid, m in self.backend.relay_modes.items()}

    def lease_view(self) -> dict | None:
        lease = self.gate.active()
        return lease.public(self._mono()) if lease else None

    def freshness(self) -> dict:
        now = datetime.now(timezone.utc)
        with self.lock:
            ages = [(now - s.acquired_utc).total_seconds() for s in self.samples.values()]
        return {"server_utc": now.isoformat(), "newest_sample_age_s": min(ages) if ages else None,
                "oldest_sample_age_s": max(ages) if ages else None}
