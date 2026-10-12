"""Periodic historian snapshots, decoupled from acquisition (owner requirements of 2026-10-12, ADR-0008).

Acquisition, alarm evaluation, control and the dashboard run at their own rate and keep the latest
sample of every point in memory. Independently of them, the historian writes one *complete* snapshot
every `recording_interval_s` (60 s; 300 s only on the owner's request): every configured point,
unchanged values included, each with its quality. A point whose latest reading is older than its
staleness limit is written as STALE (value null, the old reading kept as last_good_*); a point never
acquired in this process is written as UNAVAILABLE. Events, commands and alarm transitions do not
wait for a snapshot; they are written when they happen.

Real installations snapshot on wall-clock boundaries (hh:mm:00 for 60 s, every fifth minute for
300 s). A boundary is written at most once per process; boundaries that could not be written (clock
step, stalled process, low disk) are counted and reported as events, never silently skipped.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from ..config.schema import InstallationConfig
from ..domain.model import Origin, Quality, Sample
from ..io.sampling import AcqContext, make_sample


def complete_snapshot(cfg: InstallationConfig, latest: dict[str, Sample], *, now: datetime, ctx: AcqContext,
                      origin: Origin, stale_reason: Callable[[Sample], str | None]) -> list[Sample]:
    """One sample per configured point. `stale_reason(sample)` returns a reason when the reading is too
    old to be recorded as current."""
    out: list[Sample] = []
    for pid in cfg.all_points():
        key = str(pid)
        s = latest.get(key)
        if s is None:
            out.append(make_sample(cfg, pid, None, acquired=now, ctx=ctx, origin=origin,
                                   reason="no acquisition yet in this process"))
            continue
        why = stale_reason(s)
        if why is None:
            out.append(s)
            continue
        good = s.quality in (Quality.GOOD, Quality.OVERRIDDEN)
        out.append(replace(s, quality=Quality.STALE, reason=why, acquired_utc=now, raw_value=None, value=None,
                           sim_time_s=ctx.sim_time_s if ctx.sim_time_s is not None else s.sim_time_s,
                           last_good_value=s.value if good else s.last_good_value,
                           last_good_utc=s.acquired_utc if good else s.last_good_utc))
    return out


def stale_limit_s(poll_interval_s: float) -> float:
    """A reading older than this is not current: three missed polls, at least one second past a poll."""
    return max(3.0 * poll_interval_s, poll_interval_s + 1.0)


def boundary_index(wall_s: float, interval_s: float) -> int:
    """Index of the most recent wall-clock boundary at or before `wall_s`."""
    return math.floor(wall_s / interval_s + 1e-9)


class HistoryRecorder:
    """Thread that calls `record(boundary_utc, interval_s)` once per wall-clock boundary.

    It never touches the buses: it only reads what acquisition already holds. The wait is re-computed
    from the wall clock at least every second, so clock steps are followed; a boundary index is never
    written twice in one process, and skipped boundaries are reported through `on_missed(count, why)`.
    """

    def __init__(self, interval_s: float, record: Callable[[datetime, float], None], *,
                 on_missed: Callable[[int, str], None] | None = None, wall: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, name: str = "historian-recorder"):
        if interval_s <= 0:
            raise ValueError("interval must be positive")
        self.interval_s = interval_s
        self._record, self._on_missed = record, on_missed or (lambda n, why: None)
        self._wall, self._mono = wall, monotonic
        self._stop = threading.Event()
        self._last_index: int | None = None
        self.state = "NOT_STARTED"
        self.snapshots = 0
        self.missed = 0
        self.last_error: str | None = None
        self.heartbeat: float | None = None
        self.last_snapshot_utc: str | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self.state = "RUNNING"
        # The first boundary written is the next one after start: a restart never back-fills.
        self._last_index = boundary_index(self._wall(), self.interval_s)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self.state = "STOPPED"

    def due(self) -> int | None:
        """Boundary index to write now, or None. Also accounts for skipped boundaries."""
        idx = boundary_index(self._wall(), self.interval_s)
        if self._last_index is None:
            self._last_index = idx - 1
        if idx <= self._last_index:
            return None          # clock stepped back, or this boundary is already written
        gap = idx - self._last_index - 1
        if gap > 0:
            self.missed += gap
            self._on_missed(gap, "recorder did not run at these boundaries (wall-clock step forward or "
                                 "stalled process)")
        return idx

    def tick(self) -> bool:
        """Write the snapshot for the current boundary if it is due. Returns True if one was written."""
        idx = self.due()
        if idx is None:
            return False
        self._last_index = idx
        at = datetime.fromtimestamp(idx * self.interval_s, tz=timezone.utc)
        self._record(at, self.interval_s)
        self.snapshots += 1
        self.last_snapshot_utc = at.isoformat()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self.heartbeat = self._mono()
            try:
                self.tick()
                self.state = "RUNNING"
            except Exception as e:   # a failed snapshot is visible in health and the next one is tried
                self.last_error = f"{datetime.now(timezone.utc).isoformat()} {e!r}"
                self.state = "DEGRADED"
            nxt = (boundary_index(self._wall(), self.interval_s) + 1) * self.interval_s
            self._stop.wait(timeout=min(1.0, max(0.01, nxt - self._wall())))

    def health(self) -> dict:
        age = None if self.heartbeat is None else self._mono() - self.heartbeat
        return {"state": self.state, "interval_s": self.interval_s, "snapshots": self.snapshots,
                "missed_boundaries": self.missed, "last_snapshot_utc": self.last_snapshot_utc,
                "heartbeat_age_s": age, "ok": self.state == "RUNNING" and age is not None and age < 5.0,
                "last_error": self.last_error}
