"""Simulation service: owns the single runtime of this installation, its clock and supervision."""
from __future__ import annotations

import asyncio
import time
from collections import deque

from ..config.schema import InstallationConfig
from ..domain.model import utcnow
from ..historian.writer import Historian
from ..twin.clock import SimClock
from ..twin.runtime import SimulationRuntime
from ..twin.scenarios import presets, with_twin_overrides

DRIVE_PERIOD_S = 0.05
MAX_FAILURES_PER_10_MIN = 5


class SimulationService:
    banner = "SIMULATION — DEMO MODEL"

    def __init__(self, cfg: InstallationConfig, hist: Historian, session_id: str):
        self.base_cfg = cfg
        self.hist = hist
        self.session_id = session_id
        self.runtime: SimulationRuntime | None = None
        self.clock: SimClock | None = None
        self.scenario: str | None = None
        self.task = {"state": "NOT_STARTED", "heartbeat_monotonic": None, "failures": deque(maxlen=20),
                     "last_error": None, "restarts": 0}
        self.new_run("process start", restore_alarms=True)

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

    def effective_stride_s(self, speed: int) -> float:
        h = self.base_cfg.historian
        return max(h.sim_history_stride_s, speed * h.sim_history_min_wall_interval_s)

    def apply_speed(self, speed: int) -> None:
        self.clock.set_speed(speed)
        self.runtime.set_history_stride(self.effective_stride_s(speed),
                                        f"speed {speed}x: history cadence = max(configured stride, speed x min wall "
                                        "interval) to bound database write rate")

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
        last = time.monotonic()
        while True:
            await asyncio.sleep(DRIVE_PERIOD_S)
            now = time.monotonic()
            self.task["heartbeat_monotonic"] = now
            self.clock.advance_wall(now - last)
            last = now

    def task_health(self) -> dict:
        hb = self.task["heartbeat_monotonic"]
        age = None if hb is None else time.monotonic() - hb
        ok = self.task["state"] == "RUNNING" and age is not None and age < 2.0
        return {"state": self.task["state"], "heartbeat_age_s": age, "ok": ok, "restarts": self.task["restarts"],
                "last_error": self.task["last_error"]}
