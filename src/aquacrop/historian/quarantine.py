"""Timestamp quarantine (owner request of 2026-10-12, ADR-0009).

When the wall clock ran ahead and was then corrected, the history holds records with future-dated
UTC stamps, and the newest stored snapshot label blocks new snapshots (HISTORY_CLOCK_BEHIND) until the
real time passes it. An administrator who has checked the date against an independent source can
quarantine those records:

* Nothing is deleted or rewritten. Every record keeps its original values and timestamps. The
  quarantine is one audited event (HISTORY_TIMESTAMPS_QUARANTINED) that names the affected records by
  their storage sequence (cycle, sample, event and command ids), so charts, exports and the audit view
  flag them as having suspect timestamps. The schema is unchanged (no migration).
* Each quarantine starts a new clock epoch. Wall-time snapshot ids carry the epoch
  (`slot/60s/e<n>/<label>`, ADR-0009), and the duplicate check compares a new label only with the newest
  label that is not quarantined. A correctly timed label equal to a quarantined one is a different
  snapshot; the same label twice in one epoch is still refused.
* A quarantine never authorizes deletion. It puts rolling retention on hold (TIMESTAMPS_QUARANTINED),
  and the hold survives restarts until an administrator separately confirms the date and the proposed
  cutoff (RETENTION_CLOCK_CONFIRMED). Quarantined records are kept until the time of the quarantine
  itself leaves the retention window, whatever their (wrong) stamps say.

Which records: the proposal finds the first record stamped later than the current clock (plus
CLOCK_TOLERANCE_S) and widens it to the whole wrong-clock segment: from the forward clock step that
preceded it in that process (or that process's start, when the clock was wrong from boot) to the
backward correction that followed (HISTORY_CLOCK_BEHIND, RETENTION_HELD), or to now if none was seen.
Clock events written by the historian carry a watermark (the newest id of each table at that moment)
so these boundaries are exact; records written before this change fall back to whole processes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .schema import utc_iso

QUARANTINE_EVENT = "HISTORY_TIMESTAMPS_QUARANTINED"
TABLES = ("cycles", "samples", "events", "commands")
_ID = {"cycles": "cycle_pk", "samples": "id", "events": "id", "commands": "rowid"}
_TIME = {"cycles": "persisted_utc", "samples": "acquired_utc", "events": "utc", "commands": "intent_utc"}
FUTURE_TOLERANCE_S = 300.0   # a record this far ahead of the confirmed clock is future-dated


def _forward_marker(kind: str, p: dict) -> bool:
    return ((kind == "RETENTION_HELD" and p.get("code") == "CLOCK_STEP_FORWARD")
            or (kind == "HISTORY_SNAPSHOTS_MISSED" and p.get("cause") in ("CLOCK_STEP_FORWARD", "MIXED"))
            or (kind == "WALL_CLOCK_STEP" and not p.get("backward")))


def _backward_marker(kind: str, p: dict) -> bool:
    return (kind == "HISTORY_CLOCK_BEHIND"
            or (kind == "RETENTION_HELD" and p.get("code") in ("CLOCK_STEP_BACKWARD", "CLOCK_BEHIND_STORED"))
            or (kind == "WALL_CLOCK_STEP" and bool(p.get("backward"))))


MARKER_KINDS = ("RETENTION_HELD", "HISTORY_SNAPSHOTS_MISSED", "HISTORY_CLOCK_BEHIND", "WALL_CLOCK_STEP")


def watermark(c: sqlite3.Connection) -> dict[str, int]:
    """Newest id of every history table (O(1) each): where the storage sequence stands right now."""
    return {t: c.execute(f"SELECT coalesce(max({_ID[t]}), 0) FROM {t}").fetchone()[0] for t in TABLES}


@dataclass(frozen=True)
class Quarantine:
    quarantine_id: str
    clock_epoch: int
    ranges: dict[str, tuple[int, int]]
    alarm_occurrences: frozenset[str]
    sessions: frozenset[str]
    confirmed_utc: str
    confirmed_by: str
    event_id: int | None = None

    def contains(self, table: str, ident: int | None) -> bool:
        r = self.ranges.get(table)
        return ident is not None and r is not None and r[0] <= ident <= r[1]

    def view(self) -> dict:
        return {"quarantine_id": self.quarantine_id, "clock_epoch": self.clock_epoch,
                "ranges": {t: list(r) for t, r in self.ranges.items()}, "confirmed_utc": self.confirmed_utc,
                "confirmed_by": self.confirmed_by, "alarm_occurrences": sorted(self.alarm_occurrences),
                "sessions": sorted(self.sessions)}


@dataclass
class QuarantineRegister:
    """All quarantines in force (they are registers: never pruned). Loaded from the events table."""
    items: list[Quarantine] = field(default_factory=list)

    @classmethod
    def load(cls, c: sqlite3.Connection) -> "QuarantineRegister":
        rows = c.execute("SELECT id, payload_json FROM events WHERE kind=? ORDER BY id", (QUARANTINE_EVENT,)).fetchall()
        return cls([_from_payload(json.loads(p), eid) for eid, p in rows])

    @property
    def clock_epoch(self) -> int:
        return max((q.clock_epoch for q in self.items), default=0)

    def find(self, table: str, ident: int | None) -> Quarantine | None:
        for q in self.items:
            if q.contains(table, ident):
                return q
        return None

    def find_occurrence(self, occurrence_id: str) -> Quarantine | None:
        for q in self.items:
            if occurrence_id in q.alarm_occurrences:
                return q
        return None

    def sessions(self) -> set[str]:
        return set().union(*(q.sessions for q in self.items)) if self.items else set()

    def exclude(self, table: str, col: str | None = None) -> tuple[str, tuple]:
        """SQL ` AND NOT (col BETWEEN lo AND hi)` for every quarantined range of `table`."""
        col = col or _ID[table]
        rs = [q.ranges[table] for q in self.items if table in q.ranges]
        return "".join(f" AND NOT ({col} BETWEEN ? AND ?)" for _ in rs), tuple(x for r in rs for x in r)

    def flags(self, table: str, ident: int | None) -> dict:
        q = self.find(table, ident)
        return {"timestamp_suspect": q is not None, "quarantine_id": q.quarantine_id if q else None}

    def view(self) -> list[dict]:
        return [q.view() for q in self.items]


def _from_payload(p: dict, event_id: int | None) -> Quarantine:
    return Quarantine(quarantine_id=p["quarantine_id"], clock_epoch=int(p["clock_epoch"]),
                      ranges={t: (int(r[0]), int(r[1])) for t, r in (p.get("ranges") or {}).items() if r},
                      alarm_occurrences=frozenset(p.get("alarm_occurrences") or ()),
                      sessions=frozenset(p.get("sessions") or ()), confirmed_utc=p["confirmed_utc"],
                      confirmed_by=p["confirmed_by"], event_id=event_id)


NEWEST_WALL_BEFORE_SQL = ("SELECT cycle_pk, cycle_id, persisted_utc FROM cycles WHERE cadence_basis='WALL' "
                          "AND cycle_pk < ? ORDER BY cycle_pk DESC LIMIT 1")


def newest_trusted_wall_cycle(c: sqlite3.Connection, reg: QuarantineRegister | None) -> tuple | None:
    """Newest wall-time snapshot that is not quarantined: a reverse primary-key walk that jumps over
    quarantined ranges (each step is one index lookup)."""
    bound = 1 << 62
    while True:
        row = c.execute(NEWEST_WALL_BEFORE_SQL, (bound,)).fetchone()
        if row is None:
            return None
        q = reg.find("cycles", row[0]) if reg else None
        if q is None:
            return row
        bound = q.ranges["cycles"][0]


# ---------------------------------------------------------------------------------------- proposal
class QuarantineMismatch(ValueError):
    def __init__(self, proposal: dict):
        super().__init__("the quarantine proposal has changed; review it and confirm again")
        self.proposal = proposal


def _first_sample_at_or_after_cycle(c, pk: int) -> int | None:
    """Samples are written right after their cycle by the single writer, so cycle_pk never decreases
    with the sample id: a binary search over ids (rowid lookups) finds a cycle's first sample."""
    hi_row = c.execute("SELECT max(id) FROM samples").fetchone()[0]
    if hi_row is None:
        return None
    lo, hi, found = 1, hi_row, None
    while lo <= hi:
        mid = (lo + hi) // 2
        r = c.execute("SELECT id, cycle_pk FROM samples WHERE id >= ? ORDER BY id LIMIT 1", (mid,)).fetchone()
        if r is None:
            hi = mid - 1
            continue
        if r[1] >= pk:
            found, hi = r[0], mid - 1
        else:
            lo = r[0] + 1
    return found


def _session_first_ids(c, sid: str) -> dict[str, int | None]:
    out = {"cycles": c.execute("SELECT min(cycle_pk) FROM cycles WHERE session_id=?", (sid,)).fetchone()[0],
           "events": c.execute("SELECT min(id) FROM events WHERE session_id=?", (sid,)).fetchone()[0],
           "commands": c.execute("SELECT min(rowid) FROM commands WHERE session_id=?", (sid,)).fetchone()[0]}
    return out


def _session_order(c) -> dict[str, int]:
    return {sid: rid for rid, sid in c.execute("SELECT rowid, session_id FROM process_sessions")}


def propose(c: sqlite3.Connection, reg: QuarantineRegister, *, wall_s: float,
            tolerance_s: float = FUTURE_TOLERANCE_S) -> dict:
    """What a quarantine would mark now. `needed` is False when no record is stamped later than the
    current clock (plus `tolerance_s`). Read-only."""
    ahead = datetime.fromtimestamp(wall_s + tolerance_s, timezone.utc).isoformat(timespec="microseconds")
    now_iso = datetime.fromtimestamp(wall_s, timezone.utc).isoformat()
    base = {"wall_utc": now_iso, "future_after_utc": ahead, "current_clock_epoch": reg.clock_epoch}
    first: dict[str, tuple[int, str]] = {}
    for t, extra in (("cycles", ""), ("events", f" AND kind != '{QUARANTINE_EVENT}'"), ("commands", "")):
        ex, args = reg.exclude(t)
        row = c.execute(f"SELECT {_ID[t]}, session_id FROM {t} WHERE {_TIME[t]} > ?{extra}{ex} "
                        f"ORDER BY {_ID[t]} LIMIT 1", (ahead,) + args).fetchone()
        if row:
            first[t] = (row[0], row[1])
    if not first:
        return base | {"needed": False, "note": "no record outside a quarantine is stamped later than the current "
                                                "clock; nothing to quarantine"}
    order = _session_order(c)
    sid = min((s for _, s in first.values()), key=lambda s: order.get(s, 1 << 62))
    mark_rows = c.execute(f"SELECT id, kind, utc, session_id, payload_json FROM events WHERE kind IN "
                          f"({','.join('?' * len(MARKER_KINDS))}) ORDER BY id", MARKER_KINDS).fetchall()
    markers = []
    for eid, kind, utc, msid, pj in mark_rows:
        p = json.loads(pj)
        if isinstance(p.get("watermark"), dict):
            markers.append((eid, kind, utc, msid, p))
    first_ids = {t: v[0] for t, v in first.items()}

    def before_first(wm: dict) -> bool:
        return all(wm.get(t, 0) < first_ids[t] for t in first_ids)

    # Start: the last forward clock step in that process before the first future-dated record, else
    # the process's own start (the clock was already wrong when it started).
    fwd = [m for m in markers if m[3] == sid and _forward_marker(m[1], m[4]) and before_first(m[4]["watermark"])]
    if fwd:
        m = fwd[-1]
        start = {t: m[4]["watermark"][t] + 1 for t in ("cycles", "events", "commands")}
        start_anchor = {"kind": m[1], "event_id": m[0], "utc": m[2], "session_id": sid,
                        "why": "forward clock step detected in this process"}
    else:
        f, wm_now = _session_first_ids(c, sid), watermark(c)
        start = {t: (f[t] if f[t] is not None else first_ids.get(t, wm_now[t] + 1))   # none: an empty range
                 for t in ("cycles", "events", "commands")}
        start_anchor = {"kind": "PROCESS_START", "session_id": sid,
                        "why": "no forward clock step was recorded in this process: its clock is taken as wrong "
                               "from its start"}
    # End: the first backward correction after the start, else now.
    after = [m for m in markers if _backward_marker(m[1], m[4]) and m[0] >= start["events"]]
    now_wm = watermark(c)
    if after:
        m = after[0]
        end = {t: m[4]["watermark"][t] for t in ("cycles", "events", "commands")}
        if m[3] != sid and order.get(m[3], 0) > order.get(sid, 0):    # corrected by a later process at start
            f = _session_first_ids(c, m[3])
            end = {t: min(end[t], f[t] - 1) if f[t] is not None else end[t] for t in end}
        end_anchor = {"kind": m[1], "event_id": m[0], "utc": m[2], "session_id": m[3],
                      "why": "the clock was corrected backward here"}
    else:
        end = {t: now_wm[t] for t in ("cycles", "events", "commands")}
        end_anchor = {"kind": "NOW", "why": "no backward correction was recorded; up to the newest record"}
    for q in reg.items:                       # never overlap an earlier quarantine
        for t in end:
            r = q.ranges.get(t)
            if r and start[t] <= r[1]:
                start[t] = max(start[t], r[1] + 1)
    ranges: dict[str, tuple[int, int]] = {t: (start[t], end[t]) for t in end if start[t] <= end[t]}
    if "cycles" in ranges:
        lo = _first_sample_at_or_after_cycle(c, ranges["cycles"][0])
        nxt = _first_sample_at_or_after_cycle(c, ranges["cycles"][1] + 1)
        hi = (nxt - 1) if nxt is not None else c.execute("SELECT max(id) FROM samples").fetchone()[0]
        if lo is not None and hi is not None and lo <= hi:
            ranges["samples"] = (lo, hi)
    counts, spans = {}, {}
    for t, (lo, hi) in ranges.items():
        n, a, b = c.execute(f"SELECT count(*), min({_TIME[t]}), max({_TIME[t]}) FROM {t} "
                            f"WHERE {_ID[t]} BETWEEN ? AND ?", (lo, hi)).fetchone()
        counts[t], spans[t] = n, [a, b]
    occ, sessions = set(), set()
    if "events" in ranges:
        lo, hi = ranges["events"]
        for kind, pj, s in c.execute("SELECT kind, payload_json, session_id FROM events WHERE id BETWEEN ? AND ?",
                                     (lo, hi)):
            if s:
                sessions.add(s)
            if kind.startswith("ALARM_"):
                o = json.loads(pj).get("occurrence_id")
                if o:
                    occ.add(o)
    for t in ("cycles", "commands"):
        if t in ranges:
            sessions |= {r[0] for r in c.execute(f"SELECT DISTINCT session_id FROM {t} WHERE {_ID[t]} BETWEEN ? AND ?",
                                                 ranges[t])}
    body = {"clock_epoch": reg.clock_epoch + 1, "ranges": {t: list(r) for t, r in sorted(ranges.items())},
            "alarm_occurrences": sorted(occ), "sessions": sorted(sessions)}
    pid = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    return base | body | {
        "needed": True, "proposal_id": pid, "counts": counts, "stamped_utc": spans,
        "first_future_dated": {t: v[0] for t, v in first.items()}, "start": start_anchor, "end": end_anchor,
        "note": "Quarantine flags these records as having suspect timestamps and starts clock epoch "
                f"{reg.clock_epoch + 1}. Nothing is deleted and no timestamp is changed. Rolling retention stays "
                "held until an administrator separately confirms the date and the proposed cutoff."}


def confirm(hist, proposal_id: str, *, actor: str, wall_s: float, clock_checked: bool) -> Quarantine:
    """Record the quarantine an administrator reviewed (the proposal id must match the current one).
    Runs inside the single writer, so the next snapshot already sees the new epoch."""
    if not clock_checked:
        raise ValueError("confirm that the current date and time were checked against an independent source")

    def op(c):
        prop = propose(c, hist.quarantines, wall_s=wall_s)
        if not prop["needed"] or prop["proposal_id"] != proposal_id:
            return None, prop                # nothing written; reported to the caller below
        payload = {k: prop[k] for k in ("clock_epoch", "ranges", "alarm_occurrences", "sessions", "counts",
                                        "stamped_utc", "start", "end", "first_future_dated", "proposal_id")}
        payload |= {"quarantine_id": "q_" + prop["proposal_id"], "confirmed_by": actor,
                    "confirmed_utc": prop["wall_utc"], "clock_checked_by_administrator": True,
                    "note": "timestamps of these records are suspect; values and timestamps are kept unchanged; "
                            "this does not authorize deletion"}
        cur = c.execute("INSERT INTO events(utc,kind,actor,session_id,payload_json) VALUES(?,?,?,?,?)",
                        (utc_iso(), QUARANTINE_EVENT, actor, hist.session_id, json.dumps(payload)))
        q = _from_payload(payload, cur.lastrowid)
        hist.quarantines = QuarantineRegister(hist.quarantines.items + [q])
        return q, prop

    try:
        q, prop = hist.submit(op, wait=True, timeout=120)
    except Exception:
        hist.reload_quarantines()      # a failed transaction leaves the register as stored
        raise
    if q is None:
        if not prop["needed"]:
            raise LookupError(prop["note"])
        raise QuarantineMismatch(prop)
    return q
