"""Simulation service: owns the single runtime of this installation, its clock and supervision."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime

from ..config.schema import InstallationConfig
from ..domain.model import utcnow
from ..historian.recording import HistoryRecorder
from ..historian.retention import RetentionTask, evaluate_recorder_alarm, evaluate_storage_alarm
from ..historian.writer import Historian
from ..twin.clock import SimClock
from ..twin.runtime import SimulationRuntime
from ..twin.scenarios import presets, with_twin_overrides

DRIVE_PERIOD_S = 0.05
MAX_FAILURES_PER_10_MIN = 5
STORAGE_CHECK_S = 5.0


class SimulationService:
    banner = "SIMULATION — DEMO MODEL"

    def __init__(self, cfg: InstallationConfig, hist: Historian, session_id: str, *, wall=time.time,
                 monotonic=time.monotonic):
        self.base_cfg = cfg
        self.hist = hist
        self.session_id = session_id
        self._wall, self._mono = wall, monotonic
        self.runtime: SimulationRuntime | None = None
        self.clock: SimClock | None = None
        self.scenario: str | None = None
        self.task = {"state": "NOT_STARTED", "heartbeat_monotonic": None, "failures": deque(maxlen=20),
                     "last_error": None, "restarts": 0}
        self.retention = RetentionTask(hist, wall=wall, monotonic=monotonic)
        self.new_run("process start", restore_alarms=True)
        # Installation history: a complete snapshot every recording_interval_s of WALL time, whatever the
        # simulation speed or pause state; the run id and simulated time are stored with each snapshot.
        self.recorder = HistoryRecorder(cfg.historian.recording_interval_s, self.record_snapshot,
                                        on_event=lambda kind, payload: hist.event(kind, payload, actor="system"),
                                        high_water=hist.newest_wall_label, wall=wall, monotonic=monotonic,
                                        name="sim-recorder")
        self.recorder.start(thread=False)   # ticked by the driver loop, on the thread that owns the runtime
        self._last_drive: float | None = None
        self._next_storage_check: float | None = None
        self._advanced: tuple[str, int, float] | None = None   # (run id, tick, monotonic) of the last advance

    def _idle_s(self) -> float:
        """Monotonic seconds since the run last advanced (paused, or the driver stalled)."""
        rt, now = self.runtime, self._mono()
        if self._advanced is None or self._advanced[:2] != (rt.run_id, rt.tick):
            self._advanced = (rt.run_id, rt.tick, now)
        return now - self._advanced[2]

    # ------------------------------------------------------------------ runs
    def new_run(self, reason: str, *, scenario: str | None = None, restore_alarms: bool = False,
                actor: str = "system") -> SimulationRuntime:
        speed = self.clock.speed if self.clock else 1
        old = self.runtime
        if old is not None:
            for o in list(old.alarms.open.values()):
                o.active, o.closed = False, True
                o.message += " [closed: simulation run reset]"
                old.alarms.dirty.add(o.occurrence_id)
            self.hist.alarms(old.alarms.take_dirty())
            old._event("RUN_RESET", {"reason": reason, "next_scenario": scenario}, actor)
        cfg = self.base_cfg
        sc = presets().get(scenario) if scenario else None
        if scenario and sc is None:
            raise KeyError(scenario)
        if sc is not None:
            cfg = with_twin_overrides(cfg, sc.twin_overrides)
        rt = SimulationRuntime(cfg, audit=self.hist, history=self.hist, session_id=self.session_id,
                               start_permitted=self.hist.can_accept_starts)
        self.hist.register_run(rt.run_id, rt.seed, rt.params.provenance, reason + (f" / scenario {scenario}" if scenario else ""))
        if restore_alarms:
            rt.alarms.restore(self.hist.load_open_alarms())
        if sc is not None:
            for at, kind, payload in sc.steps:
                rt.schedule(at, kind, f"scenario:{scenario}", payload)
        self.runtime, self.scenario = rt, scenario
        self.clock = SimClock(rt, speed)
        self.apply_speed(speed)
        return rt

    def sim_time_recording_s(self, speed: int) -> float | None:
        """Cadence of the optional simulation-time recorder at this speed (None when it is off)."""
        h = self.base_cfg.historian
        if not h.sim_time_recording_s:
            return None
        return max(h.sim_time_recording_s, speed * h.sim_time_recording_min_wall_s)

    def apply_speed(self, speed: int) -> None:
        self.clock.set_speed(speed)
        cadence = self.sim_time_recording_s(speed)
        if cadence is not None:
            self.runtime.set_sim_time_recording(
                cadence, f"speed {speed}x: simulation-time recorder cadence = max(configured, speed x "
                         "sim_time_recording_min_wall_s), to bound its database write rate")

    # ------------------------------------------------------------------ wall-time history
    def record_snapshot(self, at: datetime, interval_s: float) -> bool:
        rt = self.runtime
        snap = rt.wall_snapshot(at, self._idle_s())
        stored = self.hist.snapshot(at, snap, interval_s, run_id=rt.run_id, sim_s=rt.sim_s)
        # Still-active alarms refresh last_seen/value with the snapshot; transitions were written already.
        self.hist.alarms(rt.alarms.take_dirty())
        rt.alarms.take_transitioned()
        return stored

    # ------------------------------------------------------------------ supervised driver
    async def run_forever(self) -> None:
        while True:
            try:
                self.task["state"] = "RUNNING"
                await self._drive()
            except asyncio.CancelledError:
                self.task["state"] = "STOPPED"
                raise
            except Exception as e:  # supervision: record, back off, restart within a bounded rate
                now = time.monotonic()
                self.task["failures"].append(now)
                self.task["last_error"] = f"{utcnow().isoformat()} {e!r}"
                recent = [t for t in self.task["failures"] if now - t < 600]
                try:
                    self.hist.event("ACQUISITION_TASK_FAILED", {"error": repr(e), "recent_failures": len(recent)},
                                    actor="system")
                except Exception:
                    pass
                if len(recent) > MAX_FAILURES_PER_10_MIN:
                    self.task["state"] = "FAILED"
                    return  # stays failed and visible in health; no silent infinite crash loop
                self.task["state"] = "RESTARTING"
                self.task["restarts"] += 1
                await asyncio.sleep(min(30.0, 2 ** len(recent) * 0.5))

    async def _drive(self) -> None:
        self._last_drive = self._mono()     # a restart after a failure does not fast-forward the run
        while True:
            await asyncio.sleep(DRIVE_PERIOD_S)
            self.drive_step()

    def drive_step(self) -> None:
        """One pass of the driver: advance the run by the monotonic time elapsed, then the wall-time
        checks (storage, retention, history snapshot), which do not depend on simulation speed or pause."""
        now = self._mono()
        self.task["heartbeat_monotonic"] = now
        if self._last_drive is not None:
            self.clock.advance_wall(now - self._last_drive)
        self._last_drive = now
        rt = self.runtime
        self._idle_s()                      # notes when the run last advanced
        if self._next_storage_check is None or now >= self._next_storage_check:
            self._next_storage_check = now + STORAGE_CHECK_S
            self.retention.tick(rt.alarms, rt.sim_s)
            evaluate_storage_alarm(self.hist, rt.alarms, rt.sim_s)
        self.recorder.tick()
        evaluate_recorder_alarm(self.recorder, rt.alarms, rt.sim_s)
        rt.persist_alarm_transitions()

    def task_health(self) -> dict:
        hb = self.task["heartbeat_monotonic"]
        age = None if hb is None else self._mono() - hb
        ok = self.task["state"] == "RUNNING" and age is not None and age < 2.0
        return {"state": self.task["state"], "heartbeat_age_s": age, "ok": ok, "restarts": self.task["restarts"],
                "last_error": self.task["last_error"], "retention": self.retention.view(),
                "recorder": self.recorder.health()}
