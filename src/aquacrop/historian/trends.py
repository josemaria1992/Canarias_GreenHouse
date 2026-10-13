"""Aggregated trends for long ranges (OP-03): a year-long chart is drawn from one row per bucket.

Buckets are UTC prefixes of the stored ISO stamps (10 min, 1 h or 1 day), so each point is one indexed
range read, grouped; no sample is skipped. Used by `/api/trends` and by `scripts/stress_concurrency.py`,
which times the same query on a year of history.
"""
from __future__ import annotations

from datetime import datetime

# (name, bucket seconds, length of the UTC stamp prefix that identifies the bucket)
TREND_LEVELS = (("10 min", 600, 15), ("1 h", 3600, 13), ("1 day", 86400, 10))
_PAD = {15: "0:00+00:00", 13: ":00:00+00:00", 10: "T00:00:00+00:00"}


def trend_level(span_s: float, max_points: int) -> tuple:
    """The smallest bucket that keeps `span_s` within `max_points` buckets (else the largest)."""
    return next((lv for lv in TREND_LEVELS if span_s / lv[1] <= max_points), TREND_LEVELS[-1])


def trend_buckets(hist, pid: str, start: str, end: str, level: tuple, basis: str | None = None) -> list:
    """One row per bucket (and origin, and trusted/suspect stamps): mean, min and max of the GOOD or
    OVERRIDDEN values, with the sample counts, in the column order of `/api/trends`."""
    name, size, width = level
    rs = [q.ranges["samples"] for q in hist.quarantines.items if "samples" in q.ranges]
    sus = "(" + (" OR ".join("s.id BETWEEN ? AND ?" for _ in rs) or "0") + ")"
    good = "s.quality IN ('GOOD','OVERRIDDEN')"
    join = " JOIN cycles c ON c.cycle_pk=s.cycle_pk AND c.cadence_basis=?" if basis else ""
    rows = hist.query(
        f"SELECT substr(s.acquired_utc,1,{width}) AS b, s.origin, {sus} AS sus, count(*) AS n, "
        f"sum({good} AND s.value IS NOT NULL) AS nv, min(CASE WHEN {good} THEN s.value END) AS vmin, "
        f"avg(CASE WHEN {good} THEN s.value END) AS vavg, max(CASE WHEN {good} THEN s.value END) AS vmax, "
        f"max(s.unit) AS unit, max(s.sim_run_id) AS run FROM samples s{join} WHERE s.point_id=? "
        f"AND s.acquired_utc>=? AND s.acquired_utc<=? GROUP BY b, s.origin, sus ORDER BY b",
        tuple(x for r in rs for x in r) + ((basis,) if basis else ()) + (pid, start, end))
    out, last, prev = [], {}, None
    for r in rows:
        t = r["b"] + _PAD[width]
        key = (r["origin"], bool(r["sus"]))
        gap = prev is not None and (prev != key or (key in last and (datetime.fromisoformat(t) - last[key])
                                                    .total_seconds() > 1.5 * size))
        q = "GOOD" if r["nv"] == r["n"] else "PARTIAL" if r["nv"] else "UNAVAILABLE"
        out.append([t, r["vavg"], None, q, r["origin"], r["unit"], None, r["run"], gap, bool(r["sus"]), None,
                    r["vmin"], r["vmax"], r["n"], r["nv"]])
        last[key], prev = datetime.fromisoformat(t), key
    return out
