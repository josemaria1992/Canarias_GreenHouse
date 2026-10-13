"""Aggregated trends for long ranges (OP-03): a year-long chart is drawn from one row per bucket.

Buckets are UTC prefixes of the stored ISO stamps (10 min, 1 h or 1 day), so each read is one indexed
range read of one point, grouped. A bucket never mixes origins, trusted and suspect stamps, simulation
runs or recorders: each combination is its own row, and the chart breaks its line between them. Unless a
cadence basis is asked for, the chart is drawn from the wall-clock snapshots (`WALL`, and imported
`LEGACY_UNKNOWN` history); the optional simulation-time recorder is a finer copy of the same signals that
would outweigh them in a mean, and stays available raw and in the export. A year of one point takes about a second on a
desktop (more on the CM4), so finished buckets are kept in a `TrendCache` and a refresh reads only the
buckets that can still change and a first bucket that the range cuts. Used by `/api/trends` and by
`scripts/stress_concurrency.py`, which times the uncached read.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

# (name, bucket seconds, length of the UTC stamp prefix that identifies the bucket)
TREND_LEVELS = (("10 min", 600, 15), ("1 h", 3600, 13), ("1 day", 86400, 10))
_PAD = {15: "0:00+00:00", 13: ":00:00+00:00", 10: "T00:00:00+00:00"}
# A sample keeps its acquisition time, which can be a few seconds before the snapshot that writes it
# (and the writer queues), so a bucket is treated as final only this long after it ended.
SETTLE_S = 600


def trend_level(span_s: float, max_points: int) -> tuple:
    """The smallest bucket that keeps `span_s` within `max_points` buckets (else the largest)."""
    return next((lv for lv in TREND_LEVELS if span_s / lv[1] <= max_points), TREND_LEVELS[-1])


def _floor(ts: str, width: int) -> str:
    """Start of the bucket holding the UTC ISO stamp `ts`, in the same format."""
    return ts[:width] + _PAD[width]


def _next(b: str, size: int) -> str:
    return (datetime.fromisoformat(b) + timedelta(seconds=size)).isoformat()


class TrendCache:
    """Final buckets per (point, bucket size, basis, quarantines), in memory, least recently used out
    first. A final bucket does not change: samples are never back-filled, retention removes only what
    is older than the window (the first bucket of a range is always read again), and a new or changed
    quarantine is a different key. A restore needs the server stopped, which empties it."""

    def __init__(self, max_keys: int = 64) -> None:
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.max_keys = max_keys

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, lo: str, hi: str, rows: list) -> None:
        with self._lock:
            self._d[key] = (lo, hi, rows)
            self._d.move_to_end(key)
            while len(self._d) > self.max_keys:
                self._d.popitem(last=False)


def _grouped(hist, pid: str, lo: str, hi: str, width: int, basis: str | None, rs: list, *,
             hi_inclusive: bool) -> list[dict]:
    sus = "(" + (" OR ".join("s.id BETWEEN ? AND ?" for _ in rs) or "0") + ")"
    good = "s.quality IN ('GOOD','OVERRIDDEN')"
    which = "c.cadence_basis=?" if basis else "c.cadence_basis IN ('WALL','LEGACY_UNKNOWN')"
    rows = hist.query(
        f"SELECT substr(s.acquired_utc,1,{width}) AS b, s.origin, {sus} AS sus, s.sim_run_id AS run, "
        f"c.cadence_basis AS basis, count(*) AS n, sum({good} AND s.value IS NOT NULL) AS nv, "
        f"min(CASE WHEN {good} THEN s.value END) AS vmin, avg(CASE WHEN {good} THEN s.value END) AS vavg, "
        f"max(CASE WHEN {good} THEN s.value END) AS vmax, max(s.unit) AS unit FROM samples s "
        f"JOIN cycles c ON c.cycle_pk=s.cycle_pk AND {which} WHERE s.point_id=? AND s.acquired_utc>=? "
        f"AND s.acquired_utc{'<=' if hi_inclusive else '<'}? GROUP BY b, s.origin, sus, run, basis "
        f"ORDER BY b, s.origin, sus, run, basis",
        tuple(x for r in rs for x in r) + ((basis,) if basis else ()) + (pid, lo, hi))
    return [dict(r) for r in rows]


def trend_buckets(hist, pid: str, start: str, end: str, level: tuple, basis: str | None = None,
                  cache: TrendCache | None = None, now: datetime | None = None) -> list:
    """One row per bucket (and origin, trusted/suspect stamps, simulation run and recorder): mean, min and
    max of the GOOD or OVERRIDDEN values, with the sample counts, in the column order of `/api/trends`.
    `start` and `end` are UTC ISO stamps in the historian's format."""
    name, size, width = level
    rs = [tuple(q.ranges["samples"]) for q in hist.quarantines.items if "samples" in q.ranges]
    first = _floor(start, width)
    full_lo = first if start == first else _next(first, size)          # the first bucket the range holds whole
    settled = ((now or datetime.now(timezone.utc)) - timedelta(seconds=SETTLE_S)).isoformat()
    cut = _floor(min(end, settled), width)                              # buckets before it are final
    if cache is None or full_lo >= cut:
        rows = _grouped(hist, pid, start, end, width, basis, rs, hi_inclusive=True)
    else:
        key = (pid, width, basis, tuple(rs))
        lo, hi, kept = cache.get(key) or (None, None, [])
        if lo is not None and lo <= full_lo < hi:
            # the final buckets already kept, plus those that became final since
            new = _grouped(hist, pid, hi, cut, width, basis, rs, hi_inclusive=False) if hi < cut else []
            whole = [r for r in kept if r["b"] + _PAD[width] >= full_lo] + new
            cache.put(key, full_lo, max(hi, cut), whole)
        else:
            whole = _grouped(hist, pid, full_lo, cut, width, basis, rs, hi_inclusive=False)
            cache.put(key, full_lo, cut, whole)
        whole = [r for r in whole if r["b"] + _PAD[width] < cut]
        head = _grouped(hist, pid, start, full_lo, width, basis, rs, hi_inclusive=False) if start < full_lo else []
        rows = head + whole + _grouped(hist, pid, cut, end, width, basis, rs, hi_inclusive=True)
    out, last, prev = [], {}, None
    for r in rows:
        t = r["b"] + _PAD[width]
        k = (r["origin"], bool(r["sus"]), r["run"], r["basis"])
        gap = prev is not None and (prev != k or (k in last and (datetime.fromisoformat(t) - last[k])
                                                  .total_seconds() > 1.5 * size))
        q = "GOOD" if r["nv"] == r["n"] else "PARTIAL" if r["nv"] else "UNAVAILABLE"
        out.append([t, r["vavg"], None, q, r["origin"], r["unit"], None, r["run"], gap, bool(r["sus"]), None,
                    r["vmin"], r["vmax"], r["n"], r["nv"]])
        last[k], prev = datetime.fromisoformat(t), k
    return out
