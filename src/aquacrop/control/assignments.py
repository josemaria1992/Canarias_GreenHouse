"""I/O assignment on the I/O board (ADR-0016): which module channel carries which installation signal,
and the check that proved it.

An assignment is a record, not a binding. Nothing here changes what a controller reads or commands:
`physical_mapping` stays empty until a mapping is approved, and the export is a proposal for that
approval. Each point's history is append-only (`io_assignments`):

* PROPOSED: a commissioning user picked a signal for the channel;
* VERIFIED: the channel was exercised and the user saw the device respond. The server checks what it
  can see itself; the user's observation is recorded beside it and is the only proof of the device;
* CLEARED: the channel was freed.

Changing a channel's signal starts again at PROPOSED. A signal is on at most one channel.

What the server checks before VERIFIED, all within CHECK_WINDOW_S and after the assignment was made:
* DO: this user sent the channel ON and OFF, and both were confirmed by coil readback;
* DI: the input was seen to change both ways (0 to 1 and 1 to 0) with good readings;
* AI: a fresh reading (at most FRESH_S old, raw value present) is within the user's tolerance of the
  reference the user applied (mA or V, compared with the module's own measurement, not with a scaling).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..domain.model import PointId, PointKind, Quality

KIND_FOR = {PointKind.DO: "actuator", PointKind.DI: "digital", PointKind.AI: "analog"}
CHECK_WINDOW_S = 900
FRESH_S = 5.0
EDGE_HISTORY = 8


class AssignmentRefused(Exception):
    pass


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text)


def electrical(sample) -> tuple[float, str] | None:
    """The module's own measurement in mA or V (raw µA / mV divided by 1000); None for a code or no raw."""
    if sample is None or sample.raw_value is None or sample.raw_unit not in ("uA", "mV"):
        return None
    return float(sample.raw_value) / 1000.0, "mA" if sample.raw_unit == "uA" else "V"


@dataclass
class EdgeWatch:
    """Changes of each digital input, as seen by the acquisition (good readings only)."""
    last: dict[str, bool] = field(default_factory=dict)
    edges: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)
    _seen: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, samples: dict) -> None:
        with self._lock:
            for pid, s in list(samples.items()):
                if ".DI" not in pid or self._seen.get(pid) == s.sequence:
                    continue
                self._seen[pid] = s.sequence
                if s.quality != Quality.GOOD or s.value is None:
                    self.last.pop(pid, None)          # a gap is not a change
                    continue
                v, prev = bool(s.value), self.last.get(pid)
                self.last[pid] = v
                if prev is not None and prev != v:
                    e = self.edges.setdefault(pid, [])
                    e.append((s.acquired_utc.isoformat(), v))
                    del e[:-EDGE_HISTORY]

    def since(self, pid: str, start: datetime) -> list[tuple[str, bool]]:
        with self._lock:
            return [x for x in self.edges.get(pid, []) if _utc(x[0]) >= start]


class Assignments:
    def __init__(self, hist, cfg, signals: dict[str, set[str]]):
        self.h, self.cfg = hist, cfg
        self.signals = signals                           # kind ("actuator"/"digital"/"analog") -> names
        self._by_signal = {n: k for k, names in signals.items() for n in names}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reads
    def current(self) -> dict[str, dict]:
        rows = self.h.query(
            "SELECT a.seq, a.point, a.signal, a.status, a.actor, a.utc, a.note, a.evidence_json FROM io_assignments a "
            "JOIN (SELECT point, max(seq) AS seq FROM io_assignments WHERE config_id=? GROUP BY point) m "
            "ON a.seq = m.seq", (self.cfg.config_id,))
        out = {}
        for r in rows:
            if r["status"] == "CLEARED":
                continue
            out[r["point"]] = {"point": r["point"], "signal": r["signal"], "status": r["status"], "actor": r["actor"],
                               "utc": r["utc"], "note": r["note"], "evidence": json.loads(r["evidence_json"] or "{}")}
        return out

    def history(self, point: str) -> list[dict]:
        rows = self.h.query("SELECT seq, signal, status, actor, utc, note, evidence_json FROM io_assignments "
                            "WHERE config_id=? AND point=? ORDER BY seq", (self.cfg.config_id, point))
        return [dict(r) | {"evidence": json.loads(r["evidence_json"] or "{}")} for r in rows]

    def options(self) -> dict[str, list[str]]:
        return {k.value: sorted(self.signals.get(KIND_FOR[k], ())) for k in KIND_FOR}

    # ------------------------------------------------------------------ writes
    def _point(self, point: str) -> PointId:
        try:
            pid = PointId.parse(point)
            self.cfg.device(pid.device_uid)
        except (KeyError, ValueError):
            raise AssignmentRefused(f"{point} is not a channel of this configuration")
        if str(pid) not in {str(x) for x in self.cfg.all_points()}:
            raise AssignmentRefused(f"{point} is not a channel of this configuration")
        return pid

    def _write(self, point: str, signal: str | None, status: str, actor: str, note: str | None, evidence: dict) -> dict:
        utc = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        row = (point, signal, status, actor, utc, note, json.dumps(evidence, sort_keys=True), self.cfg.config_id,
               self.cfg.config_version)
        self.h.submit(lambda c: c.execute(
            "INSERT INTO io_assignments(point, signal, status, actor, utc, note, evidence_json, config_id, config_version)"
            " VALUES(?,?,?,?,?,?,?,?,?)", row), wait=True)
        self.h.event("IO_ASSIGNMENT", {"point": point, "signal": signal, "status": status, "note": note,
                                       "evidence": evidence}, actor=actor)
        return {"point": point, "signal": signal, "status": status, "actor": actor, "utc": utc, "note": note,
                "evidence": evidence}

    def assign(self, actor: str, point: str, signal: str | None, note: str | None = None) -> dict:
        pid = self._point(point)
        with self._lock:
            cur = self.current()
            if signal is None:
                if point not in cur:
                    raise AssignmentRefused(f"{point} has no signal to clear")
                return self._write(point, None, "CLEARED", actor, note, {})
            want = KIND_FOR[pid.kind]
            if self._by_signal.get(signal) is None:
                raise AssignmentRefused(f"{signal} is not a signal of the installation")
            if self._by_signal[signal] != want:
                raise AssignmentRefused(f"{signal} is a {self._by_signal[signal]} signal; {point} is a {pid.kind.value} channel")
            other = [p for p, a in cur.items() if a["signal"] == signal and p != point]
            if other:
                raise AssignmentRefused(f"{signal} is already on {other[0]}: clear it there first")
            if point in cur and cur[point]["signal"] == signal:
                raise AssignmentRefused(f"{point} already carries {signal}")
            return self._write(point, signal, "PROPOSED", actor, note, {})

    def verify(self, actor: str, point: str, *, observation: str, samples: dict, edges: EdgeWatch,
               reference: float | None = None, tolerance: float | None = None, now: datetime | None = None) -> dict:
        pid = self._point(point)
        observation = (observation or "").strip()
        if len(observation) < 3:
            raise AssignmentRefused("describe what you saw the device do (at least 3 characters)")
        now = now or datetime.now(timezone.utc)
        with self._lock:
            cur = self.current().get(point)
            if cur is None:
                raise AssignmentRefused(f"{point} has no signal: assign one first")
            start = max(_utc(cur["utc"]), now - timedelta(seconds=CHECK_WINDOW_S))
            if pid.kind == PointKind.DO:
                evidence = self._do_evidence(actor, point, start)
            elif pid.kind == PointKind.DI:
                evidence = self._di_evidence(point, start, edges)
            else:
                evidence = self._ai_evidence(point, samples.get(point), now, reference, tolerance)
            evidence["observation"] = observation
            evidence["proof"] = "the user's observation of the device; readback and readings show the module only"
            return self._write(point, cur["signal"], "VERIFIED", actor, cur["note"], evidence)

    def _do_evidence(self, actor: str, point: str, start: datetime) -> dict:
        rows = self.h.query("SELECT request_id, desired, outcome, intent_utc, origin FROM commands WHERE target=? "
                            "AND actor=? AND intent_utc>=? ORDER BY intent_utc", (point, actor, start.isoformat(timespec="microseconds")))
        ok = [r for r in rows if r["outcome"] == "READBACK_CONFIRMED"]
        on = [r for r in ok if r["desired"]]
        off = [r for r in ok if not r["desired"] and on and r["intent_utc"] > on[0]["intent_utc"]]
        if not on or not off:
            raise AssignmentRefused(f"switch {point} ON and then OFF from this page first (both confirmed by readback, "
                                    f"within {CHECK_WINDOW_S // 60} minutes)")
        return {"check": "DO_ON_OFF", "on": on[0]["request_id"], "off": off[0]["request_id"],
                "on_utc": on[0]["intent_utc"], "off_utc": off[0]["intent_utc"], "origin": on[0]["origin"]}

    def _di_evidence(self, point: str, start: datetime, edges: EdgeWatch) -> dict:
        seen = edges.since(point, start)
        rise = next((t for t, v in seen if v), None)
        fall = next((t for t, v in seen if not v), None)
        if rise is None or fall is None:
            raise AssignmentRefused(f"change the input {point} both ways first (0 to 1 and back), "
                                    f"within {CHECK_WINDOW_S // 60} minutes; hold each state for 2 seconds")
        return {"check": "DI_BOTH_WAYS", "rise_utc": rise, "fall_utc": fall}

    def _ai_evidence(self, point: str, s, now: datetime, reference: float | None, tolerance: float | None) -> dict:
        if reference is None or tolerance is None or tolerance <= 0:
            raise AssignmentRefused("enter the reference you applied and the tolerance you accept")
        e = electrical(s)
        if e is None or s.quality not in (Quality.GOOD, Quality.UNSCALED):
            raise AssignmentRefused(f"{point} has no current reading in mA or V")
        age = (now - s.acquired_utc).total_seconds()
        if age > FRESH_S:
            raise AssignmentRefused(f"the reading of {point} is {age:.0f} s old; wait for a fresh one")
        value, unit = e
        if abs(value - reference) > tolerance:
            raise AssignmentRefused(f"{point} reads {value:.3f} {unit}, {abs(value - reference):.3f} {unit} from the "
                                    f"reference {reference:g} {unit} (tolerance {tolerance:g} {unit})")
        return {"check": "AI_REFERENCE", "reference": reference, "tolerance": tolerance, "reading": round(value, 4),
                "unit": unit, "raw": s.raw_value, "raw_unit": s.raw_unit, "sample_utc": s.acquired_utc.isoformat()}

    # ------------------------------------------------------------------ export
    def export(self, software_version: str) -> dict:
        cur = self.current()
        pts = []
        for p in self.cfg.all_points():
            a = cur.get(str(p))
            dev = self.cfg.device(p.device_uid)
            pts.append({"point": str(p), "module": dev.device_uid, "bus": dev.bus_id, "address": dev.address,
                        "kind": p.kind.value, "channel": p.channel, "signal": a["signal"] if a else None,
                        "status": a["status"] if a else "UNASSIGNED", "by": a["actor"] if a else None,
                        "utc": a["utc"] if a else None, "note": a["note"] if a else None,
                        "evidence": a["evidence"] if a else None})
        n = {s: sum(1 for x in pts if x["status"] == s) for s in ("VERIFIED", "PROPOSED", "UNASSIGNED")}
        return {"kind": "AQUACROP_IO_ASSIGNMENT_PROPOSAL", "config_id": self.cfg.config_id,
                "config_version": self.cfg.config_version, "profile": self.cfg.profile.value,
                "software_version": software_version,
                "exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "counts": n,
                "note": ("A proposal for the physical mapping, not a mapping: AquaCrop does not read it. "
                         "VERIFIED means the channel was exercised on the bench and the user saw the device respond; "
                         "it says nothing about scaling, setpoints or the plant."),
                "points": pts}
