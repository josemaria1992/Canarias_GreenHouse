"""Periodic historian snapshots, decoupled from acquisition (owner requirements of 2026-10-12, ADR-0008).

Acquisition, alarm evaluation, control and the dashboard run at their own rate and keep the latest
sample of every point in memory. Independently of them, the historian writes one *complete* snapshot
every `recording_interval_s` of wall time (60 s; 300 s only on the owner's request): every configured
point, unchanged values included, each with its quality. A point whose latest reading is older than its
staleness limit is written as STALE (value null, the old reading kept as last_good_*); a point never
acquired in this process is written as UNAVAILABLE. Events, commands and alarm transitions do not
wait for a snapshot; they are written when they happen.

Timing contract (ADR-0008, revised after the owner review of 2026-10-12):

* Real and simulation profiles both record on wall time. A simulation's run id and simulated time are
  stored with each snapshot as provenance; the simulation speed (1x, 10x, 60x) or a pause never changes
  how many snapshots an hour of wall time produces.
* Scheduling is monotonic: the next wake-up is computed on the monotonic clock, so a wall-clock step
  never causes a burst of snapshots and never makes the recorder sleep for the length of the step.
* The UTC label ("slot") of a snapshot is the wall-clock boundary at or before the moment it is taken:
  hh:mm:00 for 60 s; hh:00, hh:05, ... hh:55 for 300 s (multiples of the interval since the Unix epoch,
  which are also five-minute boundaries in every local time zone in use). The label is presentation
  and identity only; it never decides when the recorder runs.
* A slot label is written at most once per database, across restarts and clock corrections. The
  recorder starts from the newest label already stored, and the writer refuses, inside the same
  transaction, any label not after the newest stored one (`Historian.snapshot`).
* Labels that were never written are reported, never back-filled: HISTORY_SNAPSHOTS_MISSED with the
  cause (NOT_RUNNING between processes, STALLED process, CLOCK_STEP_FORWARD, PAUSED_LOW_DISK is reported
  by the writer). When the wall clock is behind the newest stored label, snapshots are held
  (HISTORY_CLOCK_BEHIND) until the clock passes it, and the held count is reported on resume.
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


def slot_cycle_id(at: datetime, interval_s: float, clock_epoch: int = 0) -> str:
    """Durable identity of a wall-time snapshot: (clock epoch, label). The same label gives the same id
    in every process of one clock epoch. Epoch 0 (no timestamp quarantine yet) keeps the original form
    `slot/60s/<label>`; after the n-th quarantine (ADR-0009) ids are `slot/60s/e<n>/<label>`, so a
    correctly timed label equal to a quarantined, future-dated one is a different snapshot."""
    label = at.astimezone(timezone.utc).isoformat()
    return f"slot/{interval_s:g}s/{label}" if not clock_epoch else f"slot/{interval_s:g}s/e{clock_epoch}/{label}"


def slot_clock_epoch(cycle_id: str) -> int | None:
    """Clock epoch of a wall-time snapshot id (0 before any quarantine), None if it is not one."""
    if not cycle_id.startswith("slot/"):
        return 0 if ":snap:" in cycle_id else None
    parts = cycle_id.split("/")
    return int(parts[2][1:]) if len(parts) == 4 and parts[2].startswith("e") else 0


def slot_epoch(cycle_id: str) -> float | None:
    """UTC label (epoch seconds) of a wall-time snapshot id, or None if `cycle_id` is not one. Also reads
    the `<session>:snap:<label>` ids written by the first revision of this change."""
    if cycle_id.startswith("slot/"):
        label = cycle_id.rsplit("/", 1)[-1]
    elif ":snap:" in cycle_id:
        label = cycle_id.split(":snap:", 1)[1]
    else:
        return None
    try:
        return datetime.fromisoformat(label).timestamp()
    except ValueError:
        return None


def labels_between(older_s: float, newer_s: float, interval_s: float) -> int:
    """Number of interval labels strictly between two labels (0 for consecutive labels)."""
    return max(0, math.ceil((newer_s - older_s) / interval_s - 1e-6) - 1)


class HistoryRecorder:
    """Calls `record(label_utc, interval_s) -> bool` once per interval of wall time.

    `record` returns False when the writer did not store the snapshot (paused for low disk); that label
    is then not durable but is not reported again as missed. `high_water()` returns the newest label
    already stored (epoch seconds) so that a restart continues after it. `on_event(kind, payload)`
    receives HISTORY_SNAPSHOTS_MISSED, HISTORY_CLOCK_BEHIND and HISTORY_CLOCK_RESUMED.

    Run it as a thread (`start()`) or drive `tick()` from an existing loop (`start(thread=False)`).
    It never touches the buses: it only reads what acquisition already holds.
    """

    def __init__(self, interval_s: float, record: Callable[[datetime, float], bool | None], *,
                 on_event: Callable[[str, dict], None] | None = None,
                 high_water: Callable[[], float | None] | None = None, wall: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, name: str = "historian-recorder"):
        if interval_s <= 0:
            raise ValueError("interval must be positive")
        self.interval_s = interval_s
        self._record, self._on_event = record, on_event or (lambda kind, payload: None)
        self._high_water = high_water or (lambda: None)
        self._wall, self._mono = wall, monotonic
        self._stop = threading.Event()
        self._durable_s: float | None = None    # newest label stored (this or an earlier process)
        self._base_s: float | None = None       # newest label accounted for (written, reported or paused)
        self._base_wall: float | None = None
        self._base_mono: float | None = None
        self._next_mono: float | None = None
        self.behind: dict | None = None         # set while the wall clock is behind the newest label
        self.state = "NOT_STARTED"
        self.snapshots = 0
        self.missed = 0
        self.not_written = 0
        self.held = 0
        self.last_error: str | None = None
        self.heartbeat: float | None = None
        self.last_snapshot_utc: str | None = None
        self._thread: threading.Thread | None = None
        self._name = name

    # ------------------------------------------------------------------ lifecycle
    def start(self, *, thread: bool = True) -> None:
        self._begin()
        self.state = "RUNNING"
        if thread:
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)
        self.state = "STOPPED"

    def _begin(self) -> None:
        wall, mono, iv = self._wall(), self._mono(), self.interval_s
        current = boundary_index(wall, iv) * iv
        stored = self._high_water()
        self._durable_s = stored
        # The first label written is the next one after start: a restart never back-fills.
        self._base_s, self._base_wall, self._base_mono = current, wall, mono
        self._next_mono = mono + max(0.0, current + iv - wall)
        if stored is not None and stored < current:
            n = labels_between(stored, current, iv) + 1
            self.missed += n
            self._on_event("HISTORY_SNAPSHOTS_MISSED", {
                "labels": n, "cause": "NOT_RUNNING", "interval_s": iv,
                "after_utc": _utc(stored), "before_utc": _utc(current + iv),
                "note": "no process was recording between these labels (stopped, restarting or powered "
                        "off); nothing is back-filled"})

    # ------------------------------------------------------------------ scheduling
    def tick(self) -> bool:
        """Write the snapshot for the current label if one is due. True if a snapshot was stored."""
        if self._next_mono is None:
            self._begin()
        mono = self._mono()
        self.heartbeat = mono
        if mono + 1e-9 < self._next_mono:
            return False
        wall, iv = self._wall(), self.interval_s
        label = boundary_index(wall, iv) * iv
        newest = max(x for x in (self._durable_s, self._base_s) if x is not None)
        if label <= newest + 1e-6:
            if wall >= newest - 1e-6:                  # early for the next label (or a small step back)
                self._next_mono = mono + max(0.01, newest + iv - wall)
            else:                                       # the clock is behind a label already stored
                self._hold(wall, newest, mono)
            return False
        if self.behind is not None:
            self._resume(wall, label, mono)
        gap = labels_between(self._base_s, label, iv)
        if gap:
            self._report_gap(gap, label, wall, mono)
        self._base_s, self._base_wall, self._base_mono = label, wall, mono
        self._next_mono = mono + max(0.5 * iv, label + iv - wall)   # monotonic; at least half an interval
        at = datetime.fromtimestamp(label, tz=timezone.utc)
        try:
            stored = self._record(at, iv) is not False
        except Exception as e:      # visible in health; the next interval is tried
            self.last_error = f"{datetime.now(timezone.utc).isoformat()} {e!r}"
            self.state = "DEGRADED"
            self.not_written += 1
            return False
        self.state = "RUNNING"
        if stored:
            self._durable_s = label
            self.snapshots += 1
            self.last_snapshot_utc = at.isoformat()
        else:
            self.not_written += 1
        return stored

    def _report_gap(self, gap: int, label: float, wall: float, mono: float) -> None:
        iv = self.interval_s
        elapsed = mono - self._base_mono
        step_s = (wall - self._base_wall) - elapsed      # wall-clock step since the previous label
        stepped = min(gap, max(0, round(step_s / iv))) if step_s > 0.5 * iv else 0
        stalled = gap - stepped
        self.missed += gap
        cause = "STALLED" if not stepped else ("CLOCK_STEP_FORWARD" if not stalled else "MIXED")
        self._on_event("HISTORY_SNAPSHOTS_MISSED", {
            "labels": gap, "cause": cause, "stalled_labels": stalled, "clock_step_labels": stepped,
            "interval_s": iv, "after_utc": _utc(self._base_s), "before_utc": _utc(label),
            "monotonic_elapsed_s": round(elapsed, 3), "wall_step_s": round(step_s, 3),
            "note": "these labels were not written and are not back-filled"})

    def _hold(self, wall: float, newest: float, mono: float) -> None:
        iv = self.interval_s
        if self.behind is None:
            self.behind = {"since_utc": _utc(wall), "newest_label_utc": _utc(newest),
                           "behind_s": round(newest - wall, 3), "held": 0}
            self._on_event("HISTORY_CLOCK_BEHIND", {
                "wall_utc": _utc(wall), "newest_label_utc": _utc(newest), "behind_s": self.behind["behind_s"],
                "interval_s": iv,
                "note": "the wall clock is behind the newest stored snapshot label; snapshots are held so "
                        "no label is written twice or out of order. Events, commands and alarms are still "
                        "recorded."})
        self.behind["held"] += 1
        self.held += 1
        self._next_mono = mono + min(iv, max(0.01, newest + iv - wall))

    def _resume(self, wall: float, label: float, mono: float) -> None:
        b, self.behind = self.behind, None
        self._on_event("HISTORY_CLOCK_RESUMED", {"held_intervals": b["held"], "since_utc": b["since_utc"],
                                                 "newest_label_utc": b["newest_label_utc"],
                                                 "first_label_utc": _utc(label)})
        # The held intervals are reported here; the labels they span are not reported again as missed.
        self._base_s = max(self._base_s, label - self.interval_s)
        self._base_wall, self._base_mono = wall, mono

    def rebase(self, reason: str) -> None:
        """Re-read the stored high-water label after a timestamp quarantine (ADR-0009): the quarantined,
        future-dated labels no longer count, so recording resumes from the current wall clock. A held
        state ends here and is reported; nothing is back-filled."""
        wall, mono = self._wall(), self._mono()
        stored = self._high_water()
        b, self.behind = self.behind, None
        self._durable_s = stored
        iv = self.interval_s
        current = boundary_index(wall, iv) * iv
        # As at a start, the first label written is the next boundary; the current one is not back-filled.
        self._base_s = current if stored is None else max(stored, current)
        self._base_wall, self._base_mono = wall, mono
        self._next_mono = mono + max(0.0, current + iv - wall)
        if b is not None:
            self._on_event("HISTORY_CLOCK_RESUMED", {"held_intervals": b["held"], "since_utc": b["since_utc"],
                                                     "newest_label_utc": b["newest_label_utc"], "reason": reason,
                                                     "newest_trusted_label_utc": _utc(stored) if stored else None})

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:   # a failed snapshot is visible in health and the next one is tried
                self.last_error = f"{datetime.now(timezone.utc).isoformat()} {e!r}"
                self.state = "DEGRADED"
            wait = (self._next_mono or 0.0) - self._mono()
            self._stop.wait(timeout=min(1.0, max(0.01, wait)))

    def health(self) -> dict:
        age = None if self.heartbeat is None else self._mono() - self.heartbeat
        return {"state": self.state, "interval_s": self.interval_s, "basis": "WALL", "snapshots": self.snapshots,
                "missed_labels": self.missed, "not_written_low_disk": self.not_written, "held_clock_behind": self.held,
                "clock_behind": self.behind, "last_snapshot_utc": self.last_snapshot_utc,
                "heartbeat_age_s": age, "ok": self.state == "RUNNING" and age is not None and age < 5.0
                and self.behind is None, "last_error": self.last_error}


def _utc(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()
