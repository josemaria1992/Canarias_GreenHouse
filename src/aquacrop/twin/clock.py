"""Wall-clock scheduling for a simulation run. Speed changes scheduling only, never dt or ordering."""
from __future__ import annotations

from ..domain.model import TwinActivity

SPEEDS = (1, 10, 60)
MAX_TICKS_PER_ADVANCE = 2000   # bound catch-up work; excess wall time is dropped and reported


class SimClock:
    def __init__(self, runtime, speed: int = 1):
        self.rt = runtime
        self.speed = speed
        self.activity = TwinActivity.RUN
        self._frac = 0.0
        self.overruns = 0

    def set_speed(self, speed: int) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        self.speed = speed

    def pause(self) -> None:
        self.activity = TwinActivity.PAUSE
        self._frac = 0.0

    def resume(self) -> None:
        self.activity = TwinActivity.RUN

    def step_once(self) -> None:
        """Advance exactly one fixed tick; intended while paused."""
        self.rt.step()

    def advance_wall(self, wall_dt_s: float) -> int:
        if self.activity != TwinActivity.RUN or wall_dt_s <= 0:
            return 0
        self._frac += wall_dt_s * self.speed / self.rt.params.dt_s
        n = int(self._frac + 1e-9)
        self._frac -= n
        if n > MAX_TICKS_PER_ADVANCE:
            self.overruns += 1
            self.rt._event("SIM_SCHEDULE_OVERRUN", {"requested_ticks": n, "executed_ticks": MAX_TICKS_PER_ADVANCE},
                           "system")
            n = MAX_TICKS_PER_ADVANCE
            self._frac = 0.0
        if n:
            self.rt.step(n)
        return n
