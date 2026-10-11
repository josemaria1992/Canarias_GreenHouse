"""Hardware service for a HARDWARE_READ_ONLY installation (brief §4, §6, §8).

One worker thread per RS485 bus owns all traffic on that bus. Between polls it executes queued
bench commands, STOP priority first; each command is re-checked against the lease, its expiry and
the configuration version at the moment it runs (CommandService re-validates before dispatch).

Starting, polling, reconnecting and stopping send read frames only. Nothing here writes on start,
on reconnect, on lease expiry or on shutdown.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections import deque
from concurrent.futures import Future
from datetime import datetime, timezone

from ..config.catalog import ModelId
from ..config.schema import InstallationConfig
from ..control.alarms import AlarmEngine
from ..control.bench import BenchGate, close_stale_leases, historian_persistence
from ..control.commands import CommandService
from ..domain.model import (CommandOrigin, CommandRecord, ControllerState, Outcome, OutputCommand, PointKind, Sample,
                            new_id, utcnow)
from ..historian.retention import evaluate_storage_alarm
from ..historian.writer import Historian
from ..io.modbus_backend import ModbusBackend
from ..io.sampling import AcqContext
from ..security.auth import Principal

MAX_FAILURES_PER_10_MIN = 5


class BusWorker:
    """Serializes one bus: queued commands (priority, FIFO) run before the next due poll."""

    def __init__(self, bus_id: str, interval_s: float, poll, *, monotonic=time.monotonic):
        self.bus_id, self.interval_s, self._poll, self._mono = bus_id, interval_s, poll, monotonic
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
        fut: Future = Future()
        with self._cv:
            if self._stop:
                raise RuntimeError("bus worker stopped")
            heapq.heappush(self._heap, (int(cmd.priority), next(self._seq), cmd, execute, fut))
            self._cv.notify()
        return fut

    def stop(self, cancel) -> None:
        with self._cv:
            self._stop = True
            pending, self._heap = self._heap, []
            self._cv.notify()
        for _, _, cmd, _, fut in pending:
            fut.set_result(cancel(cmd, "process stopping before dispatch"))
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
                    return
                self.state = "RESTARTING"

    def health(self) -> dict:
        age = None if self.heartbeat is None else self._mono() - self.heartbeat
        ok = self.state == "RUNNING" and age is not None and age < max(5.0, 2 * self.interval_s)
        return {"bus_id": self.bus_id, "state": self.state, "heartbeat_age_s": age, "ok": ok, "polls": self.polls,
                "last_error": self.last_error}


class HardwareService:
    kind = "hardware"

    def __init__(self, cfg: InstallationConfig, hist: Historian, session_id: str, *, monotonic=time.monotonic):
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
        arm, end = historian_persistence(hist)
        self.gate = BenchGate(cfg, session_id, arm, end, self._preflight,
                              lambda kind, payload, actor: hist.event(kind, payload, actor=actor), monotonic)
        self.commands = CommandService(cfg, self.backend, hist, self.gate.authorize, lambda: ControllerState.STOPPED)
        self.workers = {b.bus_id: BusWorker(b.bus_id, b.poll_interval_s, self.poll_bus, monotonic=monotonic)
                        for b in cfg.buses}
        self._sync_authorizer()
        self._prev_valid: dict[str, tuple[bool, datetime]] = {}
        self._clock_ref: tuple[float, float] | None = None   # (monotonic, wall epoch) for jump detection

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        for w in self.workers.values():
            w.start()

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop(self._cancel)
        self.backend.close()   # releases ports; sends nothing

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
            if d.model == ModelId.WAVESHARE_RELAY_D and ri.ok:
                ro = self.backend.read_outputs(d.device_uid)
                got += ro.samples
                ok, err = ro.ok, ro.error
            elif d.model == ModelId.WAVESHARE_RELAY_D:
                # inputs already failed this cycle: don't spend another timeout on coils
                got += self.backend.unavailable(d.device_uid, ri.error or "device read failed", (PointKind.DO,)).samples
            status[d.device_uid] = (ok, err)
        with self.lock:
            edges = self._edges(got, self.cfg.bus(bus_id).poll_interval_s)
            for s in got:
                self.samples[s.point_id] = s
            self.alarms.evaluate_devices(status, None)
            self.alarms.evaluate_samples(self.samples, self._mono(), None)
            evaluate_storage_alarm(self.hist, self.alarms)
            dirty = self.alarms.take_dirty()
        cycle_id = got[0].cycle_id if got else new_id("cyc")
        self.hist.write_cycle(f"{bus_id}:{cycle_id}", got, run_id=None, sim_s=None,
                              cadence_s=self.cfg.bus(bus_id).poll_interval_s, cadence_basis="WALL")
        if dirty:
            self.hist.alarms(dirty)
        for kind, payload in edges:
            self.hist.event(kind, payload, actor=None)
        took = self._mono() - t0
        if took > self.cfg.bus(bus_id).poll_interval_s:
            self.hist.event("POLL_OVERRUN", {"bus_id": bus_id, "cycle_s": round(took, 3),
                                             "interval_s": self.cfg.bus(bus_id).poll_interval_s}, actor="system")

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

    # ------------------------------------------------------------------ bench
    def _preflight(self, devices: set[str]) -> str | None:
        for uid in sorted(devices):
            wd = self.backend.devices[uid]
            try:
                modes = wd.read_relay_modes()
            except Exception as e:
                return f"{uid}: relay mode read failed ({getattr(e, 'kind', type(e).__name__)})"
            self.backend.relay_modes[uid] = modes
            if not modes.all_normal:
                return f"{uid}: relay modes {modes.describe()} are not all NORMAL"
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
            bus_id = self.cfg.device(target.split(".", 1)[0]).bus_id
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
        return {"state": "RUNNING" if all(h["ok"] for h in hs) else
                ("FAILED" if any(h["state"] == "FAILED" for h in hs) else "DEGRADED"),
                "ok": all(h["ok"] for h in hs), "buses": hs, "restarts": 0,
                "last_error": next((h["last_error"] for h in hs if h["last_error"]), None),
                "heartbeat_age_s": max((h["heartbeat_age_s"] or 0) for h in hs) if hs else None}

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
