"""The program engine: one per runtime (the live controller, or the twin), ticked on the controller.

It owns three things and nothing else:
* the installation mode of its runtime: RECOVERY (after every start), MANUAL, AUTO, and STOPPING (AUTO to MANUAL
  until every automatic operation is verified stopped; an unverified stop keeps it there, with an alarm);
* the running operations, each holding its actuators through an `Arbiter` (the same ownership class the
  process controllers use), so two operations never command one item;
* occurrences and conflicts, persisted through `EngineStore` before anything starts.

Every output goes through `io.command`, which is the runtime's audited CommandService with its own policy (the
bench-test allowlist and session on hardware, the virtual modules in the twin). The engine never writes a
coil itself, never retries an ON whose outcome is not confirmed, and always sends OFF on a stop path.

Rules that the tests hold it to:
* Nothing starts while the mode is RECOVERY or STOPPING; automatic starts need AUTO; "run now" needs MANUAL.
* A restart abandons every operation: rows that were RUNNING become INTERRUPTED; nothing is resumed or replayed.
* Leaving AUTO stops every automatic operation through its stop path; MANUAL is reached only when every stop is
  verified (coil readback OFF and, where the item has one, its feedback OFF).
* Entering AUTO (from MANUAL or from recovery) skips windows that are already open: they are MISSED, not caught up.
* A due request whose item is owned by another operation opens a conflict and does not start. The running
  operation keeps all its limits; the conflict expires when the waiting window ends.
* A missing, stale or bad reading is UNKNOWN, never OFF: a verification waiting for it times out and fails;
  a volume being measured stops, and the quantity it did not see is reported as uncertain, not delivered.
* The equipment's limits (minimum OFF time, starts per hour) are checked immediately before every ON, including
  later ON steps of a sequence, and the usage behind them survives a restart (0.7.0, F02).
* A FAULT input that reads fault, or that is required and unknown, prevents a start and the re-arm after a
  restart; during a run it stops the operation and holds a persistent EQUIPMENT_FAULT alarm (0.7.0, F03).
* Start conditions (permissives) must hold for their debounce when an operation starts; UNKNOWN prevents it. Stop
  conditions (guards) stop a running operation through its plan when one holds for its debounce; UNKNOWN stops
  it too. A stop always wins over a start in the same tick (0.7.0).
* A repeat inside a window starts its next cycle only after the previous one ended verified plus the rest time,
  never after the window's end; a cycle still running at the end is stopped and reported WINDOW_ENDED (0.7.0).
* An item verified OFF stays the operation's duty until the operation ends (RR-01): if it reads ON or UNKNOWN again
  during the stop, the rest of the plan holds (no dependent valve closes), the stop is STOP_UNVERIFIED with an alarm
  and OFF is sent again; the operation never ends while any item it switched ON is not verified OFF. Escalation
  during a stop: fault inputs and stop conditions raise their alarms but never release, skip or reorder a step.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from ..control.process_base import Arbiter
from ..domain.model import Outcome, PointId, PointKind, Priority, Quality, new_id
from ..equipment.definitions import ItemBody, Purpose, TypeBody
from . import schedule as sc
from .definitions import ProgramBody, actuators

OK = {Outcome.READBACK_CONFIRMED}
USABLE = {Quality.GOOD, Quality.UNSCALED, Quality.OVERRIDDEN}
STOP_VERIFY_S = 10.0


class IO(Protocol):
    def sample(self, point: str): ...                                     # Sample | None
    def command(self, point: str, desired: bool, *, actor: str, reason: str, priority: Priority): ...  # CommandRecord
    def module_online(self, device_uid: str) -> bool | None: ...


@dataclass
class Clock:
    utc: Callable[[], datetime]
    mono: Callable[[], float]


class Refused(Exception):
    pass


@dataclass
class ItemView:
    item_id: str
    name: str
    type: TypeBody
    body: ItemBody
    points: dict[str, str]                 # role -> point
    verified: dict[str, bool]              # role -> VERIFIED

    def role(self, purpose: Purpose):
        return next((r for r in self.type.roles if r.purpose == purpose), None)

    @property
    def command_point(self) -> str | None:
        r = self.role(Purpose.COMMAND)
        return self.points.get(r.role) if r else None

    @property
    def feedback_role(self):
        return next((r for r in self.type.roles if r.purpose == Purpose.RUNNING and r.role in self.points), None)


@dataclass
class Op:
    op_id: str
    program_id: str
    version: int
    origin: str                            # AUTO | MANUAL
    body: ProgramBody
    occurrence_key: str | None
    actor: str
    started_mono: float
    deadline_mono: float
    window_end_utc: datetime | None = None
    state: str = "RUNNING"                 # RUNNING | STOPPING | DONE | FAILED | STOP_UNVERIFIED
    step: int = 0
    step_since: float = 0.0
    on: list[str] = field(default_factory=list)      # item ids switched ON, in order, until verified OFF
    held: dict[str, ItemView] = field(default_factory=dict)   # item -> its channels when it was switched ON
    off_sent: set[str] = field(default_factory=set)  # sequence OFF sent, not yet verified (still in `on`)
    measured_L: float = 0.0
    uncertain_L: float = 0.0
    requested_L: float | None = None
    last_rate: tuple[datetime, float] | None = None
    last_flow_ok_mono: float | None = None
    step_base_L: float = 0.0
    guard_since: dict = field(default_factory=dict)  # stop condition index -> mono since it holds
    reason: str | None = None
    result: str | None = None
    stop_since: float | None = None
    stopping_items: list[str] = field(default_factory=list)
    last_off_retry: float | None = None
    stop_plan: list = field(default_factory=list)    # R07-02: the plan this stop follows (Steps), then the rest OFF
    stop_plan_name: str = "none"                     # normal | fault | none
    stop_idx: int = 0
    stop_step_since: float = 0.0
    stop_sent: set[str] = field(default_factory=set)  # plan OFFs sent in this stop
    plan_done: bool = False
    # RR-01: items this operation switched ON and has since verified OFF. They keep their held channels and stay this
    # operation's duty until it ends: re-checked before every later stop step and before the operation is finished.
    stopped: dict[str, ItemView] = field(default_factory=dict)
    stop_hold: bool = False                          # the plan holds because an earlier OFF is no longer proven

    def public(self, now: float) -> dict:
        return {"op_id": self.op_id, "program_id": self.program_id, "version": self.version, "origin": self.origin,
                "state": self.state, "step": self.step, "on": list(self.on), "occurrence": self.occurrence_key,
                "runtime_s": round(now - self.started_mono, 1), "remaining_s": round(max(0.0, self.deadline_mono - now), 1),
                "measured_L": round(self.measured_L, 3), "uncertain_L": round(self.uncertain_L, 3),
                "requested_L": self.requested_L, "reason": self.reason, "result": self.result, "actor": self.actor}


class EngineStore(Protocol):
    def mode_load(self) -> dict | None: ...
    def mode_save(self, mode: str, phase: str, actor: str, reason: str, extra: dict) -> None: ...
    def occurrence_claim(self, key: str, program_id: str, version: int, scheduled_utc: datetime | None,
                         state: str, doc: dict) -> bool: ...           # False when the key exists
    def occurrence_update(self, key: str, state: str, *, outcome: str | None = None, doc: dict | None = None) -> None: ...
    def interrupt_running(self) -> list[str]: ...
    def conflict_open(self, conflict_id: str, doc: dict) -> None: ...
    def conflict_close(self, conflict_id: str, resolution: str, actor: str) -> None: ...
    def recovery_save(self, state: str, actor: str, doc: dict) -> None: ...
    # optional (0.7.0): usage_load() -> {item: {"off_utc": iso | None, "starts_utc": [iso]}}; usage_save(item, doc)


class Engine:
    def __init__(self, runtime_id: str, tz: str, io: IO, clock: Clock, store: EngineStore, *,
                 resolve: Callable[[], dict[str, ItemView]], programs: Callable[[], dict[str, tuple[int, ProgramBody]]],
                 event: Callable[[str, dict, str | None], None], alarm: Callable[[str, str, bool, dict], None],
                 physical: bool):
        self.runtime_id, self.tz, self.io, self.clock, self.store = runtime_id, tz, io, clock, store
        self.resolve, self.programs, self.event, self.alarm, self.physical = resolve, programs, event, alarm, physical
        self.arbiter = Arbiter()
        self.ops: dict[str, Op] = {}
        self.finished: list[dict] = []
        self.conflicts: dict[str, dict] = {}
        self.mode = "RECOVERY"
        self.auto_since_utc: datetime | None = None
        self._cond: dict[str, dict] = {}         # program -> condition state (since, armed, last_end)
        self._keep: dict[str, dict] = {}
        self._usage: dict[str, dict] = {}        # item -> {"off": mono of its last verified OFF, "starts": [mono]}
        self._lock = threading.RLock()
        self._items: dict[str, ItemView] = {}
        self.last_tick_utc: datetime | None = None
        self._pending_stop: str | None = None
        self._faults: dict[str, str] = {}        # item -> fault inputs while its EQUIPMENT_FAULT alarm is raised
        self._perm_since: dict[tuple, float | None] = {}   # (program, start condition) -> mono since it holds
        self._rep: dict[str, dict] = {}          # repeat window key -> {"n": cycles requested, "last_end": mono}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> dict:
        """Every start is a recovery: nothing that was running continues."""
        with self._lock:
            self._load_usage()
            interrupted = self.store.interrupt_running()
            prev = self.store.mode_load()
            self.mode = "RECOVERY"
            self.store.mode_save("RECOVERY", "INHIBITED", "system", "service started",
                                 {"previous": prev, "interrupted": interrupted})
            self.store.recovery_save("REQUIRED", "system", {"interrupted": interrupted, "previous_mode": prev})
            self.event("PROGRAM_RECOVERY_REQUIRED", {"interrupted": interrupted, "previous": prev}, "system")
            self.alarm("RECOVERY_APPROVAL_REQUIRED", self.runtime_id, True,
                       {"message": "restart: approve before any program or manual run starts"})
            return {"interrupted": interrupted}

    # ------------------------------------------------------------------ views
    def items(self) -> dict[str, ItemView]:
        return self._items

    def item_state(self, iid: str) -> dict:
        """Commanded, coil readback and independent feedback, kept apart. Unknown is UNKNOWN, never OFF."""
        it = self._items.get(iid)
        if it is None:
            return {"item_id": iid, "state": "UNKNOWN", "why": "no such item"}
        cp = it.command_point
        commanded = any(iid in o.on for o in self.ops.values())
        coil = self._digital(cp) if cp else None
        fb = it.feedback_role
        feedback = self._role_value(it, fb.role) if fb else None
        owner = self.arbiter.owner.get(iid)
        return {"item_id": iid, "name": it.name, "commanded": commanded,
                "coil": "NONE" if cp is None else "UNKNOWN" if coil is None else ("ON" if coil else "OFF"),
                "feedback": "NONE" if fb is None else ("UNKNOWN" if feedback is None else ("ON" if feedback else "OFF")),
                "owner": owner, "verified_channels": all(it.verified.values()) if it.verified else False}

    def checklist(self) -> list[dict]:
        """What must hold before re-arming after a restart (or for returning to AUTO)."""
        out = []
        self._items = self.resolve()
        modules = sorted({PointId.parse(p).device_uid for it in self._items.values() for p in it.points.values()})
        for m in modules:
            online = self.io.module_online(m)
            out.append({"check": f"module {m} answers", "ok": online is True,
                        "detail": "answering" if online else "not answering" if online is False else "unknown"})
        for iid, it in sorted(self._items.items()):
            cp = it.command_point
            if cp:
                coil = self._digital(cp)
                out.append({"check": f"{it.name}: output reads OFF", "ok": coil is False,
                            "detail": "UNKNOWN" if coil is None else ("ON" if coil else "OFF")})
            fb = it.feedback_role
            if fb:
                v = self._role_value(it, fb.role)
                out.append({"check": f"{it.name}: {fb.label} reads OFF", "ok": v is False,
                            "detail": "UNKNOWN" if v is None else ("ON" if v else "OFF")})
            for label, v in self._fault_state(it):
                out.append({"check": f"{it.name}: {label} reads no fault", "ok": v is False,
                            "detail": "UNKNOWN" if v is None else ("FAULT" if v else "no fault")})
            for r in it.type.roles:
                if r.purpose == Purpose.MEASUREMENT and r.role in it.points:
                    v = self._analog(it, r.role)
                    out.append({"check": f"{it.name}: {r.label} is fresh", "ok": v is not None,
                                "detail": "fresh" if v is not None else "missing, stale or unscaled"})
        unver = [o.op_id for o in self.ops.values() if o.state == "STOP_UNVERIFIED"]
        out.append({"check": "no stop is unverified", "ok": not unver, "detail": ", ".join(unver) or "none"})
        return out

    def view(self) -> dict:
        now = self.clock.mono()
        with self._lock:
            return {"runtime_id": self.runtime_id, "mode": self.mode, "timezone": self.tz,
                    "auto_since_utc": self.auto_since_utc.isoformat() if self.auto_since_utc else None,
                    "ops": [o.public(now) for o in self.ops.values()], "finished": self.finished[-20:],
                    "conflicts": list(self.conflicts.values()),
                    "owners": dict(self.arbiter.owner),
                    "items": {iid: self.item_state(iid) for iid in self._items}}

    # ------------------------------------------------------------------ readings
    def _fresh(self, s, max_age_s: float) -> bool:
        if s is None or s.quality not in USABLE:
            return False
        return (self.clock.utc() - s.acquired_utc).total_seconds() <= max_age_s

    def _digital(self, point: str | None, max_age_s: float = 5.0) -> bool | None:
        if point is None:
            return None
        s = self.io.sample(point)
        if not self._fresh(s, max_age_s) or s.value is None:
            return None
        return bool(s.value)

    def _role_value(self, it: ItemView, role: str) -> bool | None:
        r = next((x for x in it.type.roles if x.role == role), None)
        v = self._digital(it.points.get(role), it.body.stale_after_s)
        if v is None or r is None:
            return None
        return (not v) if r.inverted else v

    def _analog(self, it: ItemView, role: str) -> float | None:
        """The engineering value from the module's own measurement (mA or V) through the item's scaling."""
        p = it.points.get(role)
        sc_ = it.body.scaling.get(role)
        if p is None or sc_ is None:
            return None
        s = self.io.sample(p)
        if not self._fresh(s, it.body.stale_after_s) or s.raw_value is None or s.raw_unit not in ("uA", "mV"):
            return None
        raw = float(s.raw_value) / 1000.0
        if (s.raw_unit == "uA") != (sc_.raw_unit == "mA"):
            return None
        v = sc_.apply(raw)
        return v if math.isfinite(v) else None

    # ------------------------------------------------------------------ mode
    def request_mode(self, actor: str, target: str, reason: str = "") -> dict:
        with self._lock:
            if target not in ("AUTO", "MANUAL"):
                raise Refused("the mode is AUTO or MANUAL")
            if self.mode == "RECOVERY":
                raise Refused("approve the restart checklist first")
            if target == self.mode:
                return self.view()
            if target == "MANUAL":
                self.mode = "STOPPING"
                self.store.mode_save("STOPPING", "STOPPING_AUTOMATIC", actor, reason or "AUTO to MANUAL", {})
                for c in list(self.conflicts):
                    self._close_conflict(c, "CANCELLED_BY_MANUAL", actor)
                for o in list(self.ops.values()):
                    if o.origin == "AUTO" and o.state == "RUNNING":
                        self._begin_stop(o, "mode changed to MANUAL", result="CANCELLED")
                self.event("INSTALLATION_MODE_REQUESTED", {"target": "MANUAL", "reason": reason}, actor)
                self._settle_stopping()
                return self.view()
            # MANUAL -> AUTO
            if self.mode == "STOPPING":
                raise Refused("automatic operations are still stopping; wait until MANUAL")
            busy = [o.program_id for o in self.ops.values()]
            if busy:
                raise Refused(f"manual operations are running ({', '.join(busy)}): stop them before AUTO; "
                              "AUTO never takes over running equipment")
            bad = [c for c in self.checklist() if not c["ok"]]
            if bad:
                raise Refused("not ready for AUTO: " + "; ".join(f"{c['check']} ({c['detail']})" for c in bad))
            self._enter_auto(actor, reason or "MANUAL to AUTO")
            return self.view()

    def _enter_auto(self, actor: str, reason: str) -> None:
        self.mode = "AUTO"
        self.auto_since_utc = self.clock.utc()
        self.store.mode_save("AUTO", "RUNNING", actor, reason, {"since_utc": self.auto_since_utc.isoformat()})
        self._skip_open_windows(self.auto_since_utc, "open when AUTO began: not caught up")
        self.event("INSTALLATION_MODE", {"mode": "AUTO", "reason": reason}, actor)

    def _skip_open_windows(self, at: datetime, why: str) -> None:
        for pid, (ver, body) in self.programs().items():
            t = body.trigger
            if t.type in ("WEEKLY", "WINDOW_REPEAT") and body.enabled:
                for w in sc.due(f"{pid}@{ver}", [e.model_dump() for e in t.entries], self.tz, at):
                    key = w.key if t.type == "WEEKLY" else f"{w.key}#0"
                    if t.type == "WINDOW_REPEAT":
                        self._rep[w.key] = {"n": t.max_count, "last_end": None}      # the whole window is skipped
                    if self.store.occurrence_claim(key, pid, ver, w.start_utc, "MISSED", {"why": why}):
                        self.event("PROGRAM_OCCURRENCE_MISSED", {"key": key, "why": why}, "system")

    def approve_recovery(self, actor: str, target: str) -> dict:
        """The authorized user's re-arm after a restart. Refused while any check fails."""
        with self._lock:
            if self.mode != "RECOVERY":
                raise Refused("there is no restart to approve")
            if target not in ("AUTO", "MANUAL"):
                raise Refused("re-arm into AUTO or MANUAL")
            checks = self.checklist()
            bad = [c for c in checks if not c["ok"]]
            if bad:
                raise Refused("cannot re-arm: " + "; ".join(f"{c['check']} ({c['detail']})" for c in bad))
            consequences = self.approval_consequences(target)
            self.store.recovery_save("APPROVED", actor, {"target": target, "checks": checks, "consequences": consequences})
            self.alarm("RECOVERY_APPROVAL_REQUIRED", self.runtime_id, False, {})
            self.event("PROGRAM_RECOVERY_APPROVED", {"target": target, "consequences": consequences}, actor)
            if target == "AUTO":
                self._enter_auto(actor, "restart approved into AUTO")
            else:
                self.mode = "MANUAL"
                self.store.mode_save("MANUAL", "IDLE", actor, "restart approved into MANUAL", {})
            return self.view()

    def approval_consequences(self, target: str) -> list[str]:
        """What approving will allow, for the confirmation screen."""
        if target == "MANUAL":
            return ["No program starts by itself. Authorized users can run programs by hand."]
        out = ["Interrupted operations stay cancelled; nothing resumes.",
               "Schedule windows already open now are skipped; each program waits for its next window."]
        for pid, (ver, body) in sorted(self.programs().items()):
            t = body.trigger
            if not body.enabled:
                continue
            if t.type == "WEEKLY":
                nxt = sc.upcoming(f"{pid}@{ver}", [e.model_dump() for e in t.entries], self.tz, self.clock.utc(), 3)
                nxt = [w for w in nxt if w.start_utc > self.clock.utc()]
                out.append(f"{pid}: next window {nxt[0].start_local}" if nxt else f"{pid}: no window in the next week")
            elif t.type == "CONDITION":
                c = t.when
                out.append(f"{pid}: may start as soon as {c.item}.{c.role} {c.op.lower().replace('_', ' ')}"
                           f"{'' if c.value is None else ' ' + format(c.value, 'g')} holds for {c.debounce_s:g} s "
                           "(evaluated afresh on current readings)")
            elif t.type == "CONTINUOUS":
                a = body.action
                out.append(f"{pid}: may switch {a.item} at once if {a.sensor} is outside {a.low:g}-{a.high:g}")
        return out

    # ------------------------------------------------------------------ manual
    def run_now(self, actor: str, program_id: str, *, duration_s: float | None = None) -> dict:
        with self._lock:
            if self.mode != "MANUAL":
                raise Refused(f"run now needs MANUAL (the installation is {self.mode})")
            progs = self.programs()
            if program_id not in progs:
                raise Refused(f"{program_id} is not an active program of this runtime")
            ver, body = progs[program_id]
            if body.action.type == "KEEP_BETWEEN":
                raise Refused("a keep-between program runs only in AUTO")
            op = self._start(program_id, ver, body, origin="MANUAL", actor=actor, key=None, window_end=None,
                             duration_s=duration_s)
            return op.public(self.clock.mono())

    def stop_op(self, actor: str, op_id: str, reason: str = "stopped by user") -> dict:
        with self._lock:
            o = self.ops.get(op_id)
            if o is None:
                raise Refused("no such running operation")
            if o.state == "RUNNING":
                self._begin_stop(o, reason, result="STOPPED_BY_USER")
            return o.public(self.clock.mono())

    def request_stop_all(self, reason: str) -> None:
        """From another thread (a bus worker noticing a lease end): stop everything on the next tick, without
        waiting for the engine's lock, which a tick may hold while it waits for that same worker."""
        self._pending_stop = reason

    def on_refusal(self) -> str | None:
        """For the command policy at dispatch, under the bus lock: may a program's ON go out now?"""
        if self.mode in ("RECOVERY", "STOPPING"):
            return f"program ON refused: the installation is {self.mode}"
        return None

    def stop_all(self, actor: str, reason: str) -> None:
        with self._lock:
            for o in list(self.ops.values()):
                if o.state == "RUNNING":
                    self._begin_stop(o, reason, result="STOPPED")

    # ------------------------------------------------------------------ conflicts
    def resolve_conflict(self, actor: str, conflict_id: str, choice: str) -> dict:
        with self._lock:
            c = self.conflicts.get(conflict_id)
            if c is None:
                raise Refused("no such open conflict")
            if choice == "KEEP_FIRST":
                self.store.occurrence_update(c["waiting"]["key"], "CANCELLED", outcome="conflict: kept the first program")
                self._close_conflict(conflict_id, "KEEP_FIRST", actor)
            elif choice == "SWITCH":
                o = self.ops.get(c["running"]["op_id"])
                if o is not None and o.state == "RUNNING":
                    self._begin_stop(o, f"conflict resolved by {actor}: switch to {c['waiting']['program_id']}",
                                     result="STOPPED_FOR_CONFLICT")
                c["switch"] = True
                c["state"] = "SWITCHING"
                self.event("PROGRAM_CONFLICT_SWITCHING", {"conflict_id": conflict_id}, actor)
            else:
                raise Refused("choose KEEP_FIRST or SWITCH")
            return self.view()

    def _open_conflict(self, pid: str, ver: int, key: str, window_end: datetime | None, holder: Op, item: str) -> None:
        cid = new_id("conflict")
        doc = {"conflict_id": cid, "state": "OPEN", "item": item, "opened_utc": self.clock.utc().isoformat(),
               "running": {"op_id": holder.op_id, "program_id": holder.program_id, "version": holder.version,
                           "consequence": "keeps running within its own limits; stopping it switches the item over"},
               "waiting": {"program_id": pid, "version": ver, "key": key,
                           "window_end_utc": window_end.isoformat() if window_end else None,
                           "consequence": "does not start until you choose; it is dropped when its window ends"}}
        self.conflicts[cid] = doc
        self.store.conflict_open(cid, doc)
        self.alarm("PROGRAM_CONFLICT", item, True, doc)
        self.event("PROGRAM_CONFLICT", doc, "system")

    def _close_conflict(self, cid: str, resolution: str, actor: str) -> None:
        c = self.conflicts.pop(cid, None)
        if c is None:
            return
        self.store.conflict_close(cid, resolution, actor)
        self.alarm("PROGRAM_CONFLICT", c["item"], False, {})
        self.event("PROGRAM_CONFLICT_CLOSED", {"conflict_id": cid, "resolution": resolution}, actor)

    # ------------------------------------------------------------------ tick
    def tick(self) -> None:
        with self._lock:
            now_utc = self.clock.utc()
            if self.last_tick_utc is not None and now_utc < self.last_tick_utc - timedelta(seconds=5):
                self.event("CLOCK_STEPPED_BACK", {"from": self.last_tick_utc.isoformat(), "to": now_utc.isoformat()},
                           "system")
            if self.last_tick_utc is not None and now_utc > self.last_tick_utc + timedelta(minutes=5) and self.mode == "AUTO":
                for pid, (ver, body) in self.programs().items():
                    if body.trigger.type in ("WEEKLY", "WINDOW_REPEAT"):
                        for w in sc.ended_between(f"{pid}@{ver}", [e.model_dump() for e in body.trigger.entries],
                                                  self.tz, self.last_tick_utc, now_utc):
                            key = w.key if body.trigger.type == "WEEKLY" else f"{w.key}#0"
                            if self.store.occurrence_claim(key, pid, ver, w.start_utc, "MISSED",
                                                           {"why": "the clock passed the whole window"}):
                                self.event("PROGRAM_OCCURRENCE_MISSED", {"key": key, "why": "clock jump"}, "system")
            self.last_tick_utc = now_utc
            self._items = self.resolve()
            self._faults_tick()
            self._permissives_tick()
            if self._pending_stop is not None:
                why, self._pending_stop = self._pending_stop, None
                for o in list(self.ops.values()):
                    if o.state == "RUNNING":
                        self._begin_stop(o, why, result="STOPPED")
            for o in list(self.ops.values()):
                self._advance(o)
            if self.mode == "STOPPING":
                self._settle_stopping()
            if self.mode == "AUTO":
                self._triggers(now_utc)
            for cid, c in list(self.conflicts.items()):
                if c.get("switch"):
                    holder = self.ops.get(c["running"]["op_id"])
                    if holder is None:
                        self._close_conflict(cid, "SWITCH", "system")
                        w = c["waiting"]
                        progs = self.programs()
                        end = datetime.fromisoformat(w["window_end_utc"]) if w["window_end_utc"] else None
                        if w["program_id"] in progs and (end is None or end > now_utc) and self.mode == "AUTO":
                            ver, body = progs[w["program_id"]]
                            self._start(w["program_id"], ver, body, origin="AUTO", actor="system", key=w["key"],
                                        window_end=end, claimed=True)
                    continue
                end = c["waiting"]["window_end_utc"]
                if end and datetime.fromisoformat(end) <= now_utc:
                    self.store.occurrence_update(c["waiting"]["key"], "MISSED", outcome="conflict unresolved until the window ended")
                    self._close_conflict(cid, "EXPIRED", "system")

    def _settle_stopping(self) -> None:
        auto = [o for o in self.ops.values() if o.origin == "AUTO"]
        if any(o.state == "STOP_UNVERIFIED" for o in auto):
            return                                  # stays STOPPING with an alarm: never a successful transition
        if not auto:
            self.mode = "MANUAL"
            self.auto_since_utc = None
            self.store.mode_save("MANUAL", "IDLE", "system", "every automatic operation stopped and verified", {})
            self.event("INSTALLATION_MODE", {"mode": "MANUAL"}, "system")

    # ------------------------------------------------------------------ triggers
    def _triggers(self, now_utc: datetime) -> None:
        for pid, (ver, body) in sorted(self.programs().items()):
            if not body.enabled:
                continue
            t, ref = body.trigger, f"{pid}@{ver}"
            if t.type == "WEEKLY":
                for w in sc.due(ref, [e.model_dump() for e in t.entries], self.tz, now_utc):
                    if self.auto_since_utc and w.start_utc < self.auto_since_utc:
                        continue                    # already marked MISSED when AUTO began
                    self._request(pid, ver, body, w.key, w.start_utc, w.end_utc)
            elif t.type == "CONDITION":
                self._condition(pid, ver, body, now_utc)
            elif t.type == "CONTINUOUS":
                self._keep_between(pid, ver, body, now_utc)
            elif t.type == "WINDOW_REPEAT":
                self._repeat(pid, ver, body, now_utc)

    def _repeat(self, pid, ver, body, now_utc) -> None:
        t = body.trigger
        for w in sc.due(f"{pid}@{ver}", [e.model_dump() for e in t.entries], self.tz, now_utc):
            if self.auto_since_utc and w.start_utc < self.auto_since_utc:
                continue                            # already marked MISSED when AUTO began
            st = self._rep.setdefault(w.key, {"n": 0, "last_end": None})
            if st["n"] >= t.max_count:
                continue
            if any(o.program_id == pid for o in self.ops.values()):
                continue                            # the current cycle runs; the rest starts after it ends
            if any(c["waiting"]["program_id"] == pid for c in self.conflicts.values()):
                continue
            if st["n"] and (st["last_end"] is None or self.clock.mono() - st["last_end"] < t.rest_s):
                continue
            key = f"{w.key}#{st['n']}"
            st["n"] += 1
            self._request(pid, ver, body, key, w.start_utc, w.end_utc)

    def _request(self, pid, ver, body, key, start_utc, end_utc) -> None:
        if any(o.occurrence_key == key for o in self.ops.values()):
            return
        if any(c["waiting"]["key"] == key for c in self.conflicts.values()):
            return
        need = actuators(body)
        holders = {self.arbiter.owner[i] for i in need if i in self.arbiter.owner}
        if holders:
            if not self.store.occurrence_claim(key, pid, ver, start_utc, "WAITING_CONFLICT", {}):
                return
            holder = next(o for o in self.ops.values() if o.op_id in holders)
            self._open_conflict(pid, ver, key, end_utc, holder, next(i for i in need if i in self.arbiter.owner))
            return
        if not self.store.occurrence_claim(key, pid, ver, start_utc, "STARTING", {}):
            return                                   # this window already had its occurrence
        try:
            self._start(pid, ver, body, origin="AUTO", actor="system", key=key, window_end=end_utc, claimed=True)
        except Refused as e:                         # recorded on the occurrence by _start
            self.alarm("PROGRAM_OP_FAILED", pid, True, {"key": key, "why": str(e)})
            self.event("PROGRAM_START_REFUSED", {"key": key, "why": str(e)}, "system")

    def _condition(self, pid, ver, body, now_utc) -> None:
        t = body.trigger
        st = self._cond.setdefault(pid, {"since": None, "armed": True, "cleared_at": None, "n": 0})
        holds = self._cond_holds(t.when)
        mono = self.clock.mono()
        if holds is None:
            st["since"] = None
            return
        if not holds:
            st["since"] = None
            if not st["armed"]:
                st["cleared_at"] = st["cleared_at"] or mono
                if mono - st["cleared_at"] >= t.rearm_after_s:
                    st["armed"], st["cleared_at"] = True, None
            return
        st["cleared_at"] = None
        st["since"] = st["since"] or mono
        if st["armed"] and mono - st["since"] >= t.when.debounce_s:
            st["armed"] = False
            key = f"{pid}@{ver}:cond:{now_utc.strftime('%Y%m%dT%H%M%S')}"
            self._request(pid, ver, body, key, now_utc, None)

    def _cond_holds(self, c) -> bool | None:
        it = self._items.get(c.item)
        if it is None:
            return None
        if c.op in ("IS_ON", "IS_OFF"):
            v = self._role_value(it, c.role)
            return None if v is None else (v if c.op == "IS_ON" else not v)
        v = self._analog(it, c.role)
        return None if v is None else (v > c.value if c.op == "ABOVE" else v < c.value)

    def _keep_between(self, pid, ver, body, now_utc) -> None:
        a, t = body.action, body.trigger
        window_end = None
        if t.entries:
            open_ = sc.due(f"{pid}@{ver}", [e.model_dump() for e in t.entries], self.tz, now_utc)
            if not open_:
                return
            window_end = max(w.end_utc for w in open_)
        st = self._keep.setdefault(pid, {"since": None, "last_end": None, "n": 0})
        running = next((o for o in self.ops.values() if o.program_id == pid), None)
        if running:
            return                                   # the op itself watches the stop side
        it = self._items.get(a.sensor)
        v = self._analog(it, a.sensor_role) if it else None
        mono = self.clock.mono()
        if v is None:
            st["since"] = None
            return
        need = v < a.low if a.direction == "RAISE" else v > a.high
        if not need:
            st["since"] = None
            return
        if st["last_end"] is not None and mono - st["last_end"] < a.min_off_s:
            return
        if a.item in self._items and self._limit_refusal(body, [a.item]) is not None:
            return                                   # the equipment's own limits hold it off: asked again later
        st["since"] = st["since"] or mono
        if mono - st["since"] >= a.debounce_s:
            st["n"] += 1
            st["since"] = None
            key = f"{pid}@{ver}:keep:{now_utc.strftime('%Y%m%dT%H%M%S')}"
            self._request(pid, ver, body, key, now_utc, window_end)

    # ------------------------------------------------------------------ operations
    def _start(self, pid, ver, body: ProgramBody, *, origin, actor, key, window_end, duration_s=None,
               claimed=False) -> Op:
        if self.mode in ("RECOVERY", "STOPPING"):
            raise Refused(f"nothing starts while the installation is {self.mode}")
        if (why := getattr(self.io, "start_refusal", lambda: None)()) is not None:
            if key:
                self.store.occurrence_update(key, "FAILED", outcome=why)
            raise Refused(why)
        # the real system: a program whose twin proof no longer matches its equipment, channels or network never
        # starts again until it is tested in the new context (R07-01); running operations keep their channels
        if (why := getattr(self.io, "program_refusal", lambda p, v: None)(pid, ver)) is not None:
            if key:
                self.store.occurrence_update(key, "FAILED", outcome=why)
            raise Refused(f"{pid} v{ver}: {why}")
        need = actuators(body)
        for iid in need:
            it = self._items.get(iid)
            if it is None or it.command_point is None:
                why = f"{iid} has no command channel"
                if key:
                    self.store.occurrence_update(key, "FAILED", outcome=why)
                raise Refused(why)
        if (why := self._limit_refusal(body, need)) is not None:
            if key:
                self.store.occurrence_update(key, "FAILED", outcome=why)
            raise Refused(why)
        if (why := self._permissive_refusal(pid, body)) is not None:
            if key:
                self.store.occurrence_update(key, "FAILED", outcome=why)
            raise Refused(why)
        busy = self.arbiter.acquire(f"op:{pid}", need)
        if busy:
            if key:
                self.store.occurrence_update(key, "FAILED", outcome=busy)
            raise Refused(busy)
        mono = self.clock.mono()
        a = body.action
        limit = {"RUN_FOR": duration_s or a.duration_s if a.type == "RUN_FOR" else None,
                 "DELIVER_VOLUME": getattr(a, "max_runtime_s", None), "KEEP_BETWEEN": getattr(a, "max_run_s", None),
                 "SEQUENCE": getattr(a, "max_runtime_s", None)}[a.type]
        if a.type == "RUN_FOR" and limit is None and window_end is not None:
            limit = (window_end - self.clock.utc()).total_seconds()
        caps = [self._items[i].type.limits.max_runtime_s for i in need if self._items[i].type.limits.max_runtime_s]
        if caps:
            limit = min([limit] + caps) if limit else min(caps)
        if not limit or limit <= 0:
            self.arbiter.release(f"op:{pid}")
            raise Refused("no bounded runtime")
        op = Op(new_id("op"), pid, ver, origin, body, key, actor, mono, mono + limit, window_end_utc=window_end,
                step_since=mono, requested_L=getattr(a, "target_L", None))
        # the arbiter's owner names the op, so a conflict can point at it
        self.arbiter.release(f"op:{pid}")
        self.arbiter.acquire(op.op_id, need)
        self.ops[op.op_id] = op
        if key:
            self.store.occurrence_update(key, "RUNNING", doc={"op_id": op.op_id, "limit_s": limit})
        self.event("PROGRAM_OP_STARTED", {"op_id": op.op_id, "program": pid, "version": ver, "origin": origin,
                                          "key": key, "limit_s": round(limit, 1)}, actor)
        if a.type in ("RUN_FOR", "DELIVER_VOLUME", "KEEP_BETWEEN"):
            if not self._switch_on(op, a.item):
                return op
        return op

    @staticmethod
    def _volume_cap(views) -> float | None:
        caps = [v.type.limits.max_volume_L for v in views if v.type.limits.max_volume_L]
        return min(caps) if caps else None

    # ------------------------------------------------------------------ usage (limits) across restarts
    def _load_usage(self) -> None:
        """Usage is kept as UTC instants in the store and on the monotonic clock in memory. A restart is not a
        limit reset: a start or OFF from before it still counts (a backward clock step counts it as recent)."""
        load = getattr(self.store, "usage_load", None)
        if load is None:
            return
        now_u, now_m = self.clock.utc(), self.clock.mono()

        def mono_of(iso: str) -> float:
            return now_m - max(0.0, (now_u - datetime.fromisoformat(iso)).total_seconds())
        for iid, doc in (load() or {}).items():
            use = self._usage.setdefault(iid, {"off": None, "starts": []})
            if doc.get("off_utc"):
                off = mono_of(doc["off_utc"])
                use["off"] = off if use["off"] is None else max(use["off"], off)
            use["starts"] = sorted(set(use["starts"]) | {mono_of(t) for t in doc.get("starts_utc", [])})

    def _save_usage(self, iid: str) -> None:
        save = getattr(self.store, "usage_save", None)
        if save is None:
            return
        use = self._usage.get(iid) or {}
        now_u, now_m = self.clock.utc(), self.clock.mono()

        def utc_of(m: float) -> str:
            return (now_u - timedelta(seconds=now_m - m)).isoformat()
        save(iid, {"off_utc": utc_of(use["off"]) if use.get("off") is not None else None,
                   "starts_utc": [utc_of(t) for t in use.get("starts", []) if now_m - t < 3600]})

    def _mark_off(self, iid: str) -> None:
        self._usage.setdefault(iid, {"off": None, "starts": []})["off"] = self.clock.mono()
        self._save_usage(iid)

    def _item_refusal(self, iid: str) -> str | None:
        """Why this item may not be switched ON now: its limits, or a fault input. Checked before every ON."""
        it = self._items[iid]
        lim = it.type.limits
        mono = self.clock.mono()
        use = self._usage.get(iid, {})
        if lim.min_off_s and use.get("off") is not None and mono - use["off"] < lim.min_off_s:
            return (f"{it.name} must stay OFF for {lim.min_off_s:g} s between runs "
                    f"({mono - use['off']:.0f} s so far)")
        if lim.max_starts_per_hour:
            recent = [t for t in use.get("starts", []) if mono - t < 3600]
            if len(recent) >= lim.max_starts_per_hour:
                return f"{it.name} has started {len(recent)} times this hour (limit {lim.max_starts_per_hour})"
        return self._fault_refusal(it)

    # ------------------------------------------------------------------ fault inputs
    def _fault_state(self, it: ItemView) -> list[tuple[str, bool | None]]:
        """Each FAULT role of the item: True = fault, False = no fault, None = unknown (stale, bad, or a required
        role with no channel). Inversion is the role's own."""
        out = []
        for r in it.type.roles:
            if r.purpose != Purpose.FAULT:
                continue
            if r.role not in it.points:
                if r.required:
                    out.append((r.label, None))
                continue
            out.append((r.label, self._role_value(it, r.role)))
        return out

    def _fault_refusal(self, it: ItemView) -> str | None:
        for label, v in self._fault_state(it):
            if v is True:
                return f"{it.name}: {label} reports a fault"
            if v is None:
                return f"{it.name}: {label} is UNKNOWN (missing, stale or not connected), so it cannot show the item is healthy"
        return None

    def _faults_tick(self) -> None:
        """Persistent EQUIPMENT_FAULT alarms follow every item's fault inputs, running or not. An input that can no
        longer be read does not clear the alarm: only every input reading no fault does."""
        for iid, it in self._items.items():
            st = self._fault_state(it)
            active = [label for label, v in st if v is True]
            if active and iid not in self._faults:
                self._faults[iid] = ", ".join(active)
                self.alarm("EQUIPMENT_FAULT", iid, True, {"item": it.name, "inputs": active,
                                                          "message": f"{it.name}: {', '.join(active)} reports a fault"})
                self.event("EQUIPMENT_FAULT", {"item_id": iid, "inputs": active}, "system")
            elif iid in self._faults and st and all(v is False for _, v in st):
                self._faults.pop(iid)
                self.alarm("EQUIPMENT_FAULT", iid, False, {})
                self.event("EQUIPMENT_FAULT_CLEARED", {"item_id": iid}, "system")

    def _limit_refusal(self, body: ProgramBody, need) -> str | None:
        """The equipment type's limits and fault inputs, per item and across every operation and manual run of
        this runtime."""
        for iid in need:
            if (why := self._item_refusal(iid)) is not None:
                return why
        cap = self._volume_cap(self._items[i] for i in need)
        a = body.action
        asked = (a.target_L if a.type == "DELIVER_VOLUME" else
                 sum(s.target_L for s in a.steps if s.do == "DELIVER") if a.type == "SEQUENCE" else 0.0)
        if cap is not None and asked > cap:
            return f"{asked:g} L is above the equipment's maximum volume {cap:g} L"
        return None

    # ------------------------------------------------------------------ start and stop conditions
    def _permissives_tick(self) -> None:
        """How long each active program's start conditions have held (UNKNOWN and FALSE reset it)."""
        live = set()
        for pid, (ver, body) in self.programs().items():
            for i, c in enumerate(body.permissives):
                k = (pid, i)
                live.add(k)
                v = self._cond_holds(c) if c.item in self._items else None
                if v is True:
                    self._perm_since[k] = self._perm_since.get(k) or self.clock.mono()
                else:
                    self._perm_since[k] = None
        for k in [k for k in self._perm_since if k not in live]:
            del self._perm_since[k]

    def _permissive_refusal(self, pid: str, body: ProgramBody) -> str | None:
        mono = self.clock.mono()
        for i, c in enumerate(body.permissives):
            it = self._items.get(c.item)
            v = self._cond_holds(c) if it is not None else None
            name = f"{it.name if it else c.item} {c.role}"
            if v is None:
                return f"start condition {i + 1}: {name} is UNKNOWN (missing, stale or bad): not started"
            if v is False:
                return f"start condition {i + 1} does not hold: {name} {c.op.lower().replace('_', ' ')}" \
                       f"{'' if c.value is None else ' ' + format(c.value, 'g')} is not true"
            since = self._perm_since.get((pid, i))
            if c.debounce_s and (since is None or mono - since < c.debounce_s):
                return f"start condition {i + 1} has not held for {c.debounce_s:g} s yet"
        return None

    def _guards(self, op: Op) -> bool:
        """True when a stop condition stopped the operation now."""
        mono = self.clock.mono()
        for i, g in enumerate(op.body.guards):
            it = self._items.get(g.item)
            v = self._cond_holds(g) if it is not None else None
            name = f"{it.name if it else g.item} {g.role}"
            if v is None:
                self.alarm("PROGRAM_GUARD", op.program_id, True, {"op_id": op.op_id, "why": f"{name} UNKNOWN"})
                self._fail(op, f"stop condition {i + 1}: {name} is UNKNOWN (missing, stale or bad)")
                return True
            if v is False:
                op.guard_since.pop(i, None)
                continue
            since = op.guard_since.setdefault(i, mono)
            if mono - since >= g.debounce_s:
                why = (f"stop condition {i + 1}: {name} {g.op.lower().replace('_', ' ')}"
                       f"{'' if g.value is None else ' ' + format(g.value, 'g')} for {g.debounce_s:g} s")
                self.alarm("PROGRAM_GUARD", op.program_id, True, {"op_id": op.op_id, "why": why})
                self.event("PROGRAM_GUARD_STOP", {"op_id": op.op_id, "why": why}, "system")
                self._begin_stop(op, why, result="GUARD_STOPPED")
                return True
        return False

    def _held(self, op: Op, iid: str) -> ItemView | None:
        """The channels an item had when this operation switched it ON: a binding changed or cleared while it runs
        must not redirect or drop the OFF that the original output still needs."""
        return op.held.get(iid) or self._items.get(iid)

    def _switch_on(self, op: Op, iid: str) -> bool:
        it = self._items.get(iid)
        if it is None or it.command_point is None:
            self._fail(op, f"{iid} has no command channel")
            return False
        if (why := self._item_refusal(iid)) is not None:     # before every ON, also a later step of a sequence
            self._faults_tick()
            self._fail(op, why)
            return False
        op.held[iid] = it
        op.stopped.pop(iid, None)
        use = self._usage.setdefault(iid, {"off": None, "starts": []})
        use["starts"] = [t for t in use["starts"] if self.clock.mono() - t < 3600] + [self.clock.mono()]
        self._save_usage(iid)                        # an attempted start counts, confirmed or not
        rec = self.io.command(it.command_point, True, actor=op.actor if op.origin == "MANUAL" else "program",
                              reason=f"program {op.program_id}@{op.version} op {op.op_id}", priority=Priority.NORMAL)
        if rec.outcome in OK:
            op.on.append(iid)
            return True
        op.on.append(iid)                          # an unconfirmed ON may have switched: the stop path sends OFF
        self._fail(op, f"{it.name}: ON not confirmed ({rec.outcome.value}{': ' + rec.detail if rec.detail else ''})")
        return False

    def _fail(self, op: Op, why: str) -> None:
        self.alarm("PROGRAM_OP_FAILED", op.program_id, True, {"op_id": op.op_id, "why": why})
        self._begin_stop(op, why, result="FAILED")

    # Which plan a stop follows (review R07-02). A completed sequence has already run its normal stop as steps.
    FAULT_RESULTS = ("FAILED", "GUARD_STOPPED", "STOPPED")      # a failure, a stop condition, a stop of everything
    NORMAL_RESULTS = ("STOPPED_BY_USER", "RUNTIME_GUARD", "WINDOW_ENDED", "CANCELLED", "DONE")

    def _choose_plan(self, op: Op, result: str) -> tuple[str, list]:
        a = op.body.action
        if a.type != "SEQUENCE":
            return "none", []
        if result in self.FAULT_RESULTS and a.fault_stop:
            return "fault", list(a.fault_stop)
        if result in self.NORMAL_RESULTS and a.normal_stop and not (result == "DONE" and op.step >= len(a.steps)):
            return "normal", list(a.normal_stop)
        return "none", []

    def _begin_stop(self, op: Op, why: str, *, result: str) -> None:
        if op.state != "RUNNING":
            return
        op.state, op.reason, op.result = "STOPPING", why, result
        op.stop_since = op.stop_step_since = self.clock.mono()
        op.stop_plan_name, op.stop_plan = self._choose_plan(op, result)
        op.stop_idx, op.stop_sent, op.stopping_items, op.plan_done = 0, set(), [], False
        self.event("PROGRAM_OP_STOPPING", {"op_id": op.op_id, "why": why, "result": result,
                                           "plan": op.stop_plan_name}, "system")
        self._advance_stop(op)

    def _off_state(self, op: Op, iid: str) -> tuple[bool, bool | None, bool | None]:
        """(verified OFF, coil, feedback) of an item this operation holds."""
        it = self._held(op, iid)
        if it is None or it.command_point is None:
            return False, None, None                 # nothing to read it by: never assumed OFF
        coil = self._digital(it.command_point)
        fb = it.feedback_role
        fbv = self._role_value(it, fb.role) if fb else False
        return coil is False and fbv is False, coil, fbv

    def _run_stop_plan(self, op: Op) -> bool:
        """Follow the stop plan in order: each OFF is sent once and verified (output and feedback) before the next
        step; a WAIT holds for its time. An OFF not verified within its timeout holds the rest of the plan, since
        closing a valve on a pump that may still run is what the plan exists to avoid: the operation reports
        STOP_UNVERIFIED and sends that OFF again, never an ON. True when the plan is through."""
        now = self.clock.mono()
        while op.stop_idx < len(op.stop_plan):
            if not self._stopped_still_off(op, now):
                return False                         # an earlier OFF is no longer proven: nothing dependent happens
            s = op.stop_plan[op.stop_idx]
            if s.do == "WAIT":
                if now - op.stop_step_since < s.seconds:
                    return False
            elif s.item in op.on:                    # only what this operation switched on and still owns
                it = self._held(op, s.item)
                if s.item not in op.stop_sent:
                    op.stop_sent.add(s.item)
                    op.stop_step_since = now
                    if it and it.command_point:
                        self.io.command(it.command_point, False, actor="program",
                                        reason=f"stop {op.op_id} ({op.stop_plan_name} plan step {op.stop_idx + 1}): "
                                               f"{op.reason}", priority=Priority.STOP)
                ok, coil, fbv = self._off_state(op, s.item)
                if not ok:
                    if now - op.stop_step_since > s.timeout_s:
                        detail = [f"{getattr(it, 'name', s.item)}: output "
                                  f"{'UNKNOWN' if coil is None else 'ON' if coil else 'OFF'}, feedback "
                                  f"{'UNKNOWN' if fbv is None else 'ON' if fbv else 'OFF'}; "
                                  f"the rest of the {op.stop_plan_name} stop waits for it"]
                        if op.state != "STOP_UNVERIFIED":
                            op.state = "STOP_UNVERIFIED"
                            self.alarm("PROGRAM_STOP_UNVERIFIED", op.program_id, True, {"op_id": op.op_id, "items": detail})
                            self.event("PROGRAM_STOP_UNVERIFIED", {"op_id": op.op_id, "items": detail}, "system")
                        if it and it.command_point and now - (op.last_off_retry or op.stop_step_since) >= STOP_VERIFY_S:
                            op.last_off_retry = now
                            self.io.command(it.command_point, False, actor="program",
                                            reason=f"stop {op.op_id}: not verified, OFF again", priority=Priority.STOP)
                    return False
                op.on.remove(s.item)
                op.off_sent.discard(s.item)
                self._verified_off(op, s.item)
                if op.state == "STOP_UNVERIFIED":
                    op.state = "STOPPING"
                    self.alarm("PROGRAM_STOP_UNVERIFIED", op.program_id, False, {})
            self.event("PROGRAM_STOP_STEP", {"op_id": op.op_id, "plan": op.stop_plan_name, "step": op.stop_idx + 1,
                                             "do": s.do, "item": s.item}, "system")
            op.stop_idx += 1
            op.stop_step_since = now
        return True

    def _verified_off(self, op: Op, iid: str) -> None:
        """An item this operation switched ON is verified OFF: its min-off clock starts, and it stays held (RR-01)."""
        it = self._held(op, iid)
        if it is not None:
            op.stopped[iid] = it
        self._mark_off(iid)

    def _regressed(self, op: Op) -> list[tuple[str, bool | None, bool | None]]:
        """Items verified OFF earlier in this operation that no longer read OFF (ON or UNKNOWN)."""
        out = []
        for iid in op.stopped:
            ok, coil, fbv = self._off_state(op, iid)
            if not ok:
                out.append((iid, coil, fbv))
        return out

    def _stopped_still_off(self, op: Op, now: float) -> bool:
        """RR-01: before any later stop step, every earlier OFF must still be proven. Otherwise hold the plan, report
        STOP_UNVERIFIED with an alarm, and send that OFF again (never an ON). When it is proven again, the current
        step's time restarts, so a wait before a dependent valve is a full wait after a verified stop."""
        bad = self._regressed(op)
        if not bad:
            if op.stop_hold:
                op.stop_hold = False
                op.stop_step_since = now
                if op.state == "STOP_UNVERIFIED":
                    op.state = "STOPPING"
                    self.alarm("PROGRAM_STOP_UNVERIFIED", op.program_id, False, {})
                self.event("PROGRAM_STOP_REVERIFIED", {"op_id": op.op_id}, "system")
            return True
        detail = [f"{getattr(self._held(op, i), 'name', i)}: verified OFF earlier, now output "
                  f"{'UNKNOWN' if c is None else 'ON' if c else 'OFF'}, feedback "
                  f"{'UNKNOWN' if f is None else 'ON' if f else 'OFF'}; the rest of the {op.stop_plan_name} stop "
                  "waits for it" for i, c, f in bad]
        if not op.stop_hold:
            op.stop_hold = True
            op.last_off_retry = None
            self.event("PROGRAM_STOP_PROOF_LOST", {"op_id": op.op_id, "items": detail}, "system")
        if op.state != "STOP_UNVERIFIED":
            op.state = "STOP_UNVERIFIED"
            self.alarm("PROGRAM_STOP_UNVERIFIED", op.program_id, True, {"op_id": op.op_id, "items": detail})
            self.event("PROGRAM_STOP_UNVERIFIED", {"op_id": op.op_id, "items": detail}, "system")
        if op.last_off_retry is None or now - op.last_off_retry >= STOP_VERIFY_S:
            op.last_off_retry = now
            for iid, *_ in bad:
                it = self._held(op, iid)
                if it and it.command_point:
                    self.io.command(it.command_point, False, actor="program",
                                    reason=f"stop {op.op_id}: verified OFF earlier, no longer OFF: OFF again",
                                    priority=Priority.STOP)
        return False

    def _advance_stop(self, op: Op) -> None:
        if not op.plan_done:
            if not self._run_stop_plan(op):
                return
            # the plan is through (or there is none): whatever is still on goes OFF now, the last one first
            op.plan_done = True
            op.stop_since = self.clock.mono()
            op.stopping_items = list(reversed(op.on)) + [i for i in op.stopped if i not in op.on]
            for iid in op.stopping_items:
                it = self._held(op, iid)
                if it and it.command_point:
                    self.io.command(it.command_point, False, actor="program", reason=f"stop {op.op_id}: {op.reason}",
                                    priority=Priority.STOP)
        pending = []
        for iid in op.stopping_items:
            it = self._held(op, iid)
            if it is None or it.command_point is None:
                pending.append((iid, None, None))    # nothing to read it by: never assumed OFF
                continue
            coil = self._digital(it.command_point)
            fb = it.feedback_role
            fbv = self._role_value(it, fb.role) if fb else False
            if coil is not False or fbv is not False:
                pending.append((iid, coil, fbv))
        if not pending:
            self._end(op)
            return
        now = self.clock.mono()
        if now - op.stop_since > STOP_VERIFY_S:
            if op.state != "STOP_UNVERIFIED":
                op.state = "STOP_UNVERIFIED"
                detail = [f"{getattr(self._held(op, i), 'name', i)}: output {'UNKNOWN' if c is None else 'ON' if c else 'OFF'}, "
                          f"feedback {'UNKNOWN' if f is None else 'ON' if f else 'OFF'}" for i, c, f in pending]
                self.alarm("PROGRAM_STOP_UNVERIFIED", op.program_id, True, {"op_id": op.op_id, "items": detail})
                self.event("PROGRAM_STOP_UNVERIFIED", {"op_id": op.op_id, "items": detail}, "system")
            if now - (op.last_off_retry or op.stop_since) >= STOP_VERIFY_S:
                op.last_off_retry = now
                for iid, *_ in pending:              # OFF again while the stop is unverified; never an ON
                    it = self._held(op, iid)
                    if it and it.command_point:
                        self.io.command(it.command_point, False, actor="program",
                                        reason=f"stop {op.op_id}: not verified, OFF again", priority=Priority.STOP)

    def _end(self, op: Op) -> None:
        for iid in op.held:                          # verified OFF now: the min-off clock starts
            self._mark_off(iid)
        self.arbiter.release(op.op_id)
        self.ops.pop(op.op_id, None)
        if op.state == "STOP_UNVERIFIED":
            self.alarm("PROGRAM_STOP_UNVERIFIED", op.program_id, False, {})
        res = op.result or "DONE"
        rec = op.public(self.clock.mono()) | {"result": res, "ended_utc": self.clock.utc().isoformat()}
        self.finished.append(rec)
        del self.finished[:-200]
        if op.occurrence_key:
            self.store.occurrence_update(op.occurrence_key, res, outcome=op.reason,
                                         doc={"measured_L": op.measured_L, "uncertain_L": op.uncertain_L,
                                              "requested_L": op.requested_L})
        if op.body.action.type == "KEEP_BETWEEN":
            self._keep.setdefault(op.program_id, {"since": None, "last_end": None, "n": 0})["last_end"] = self.clock.mono()
        if op.body.trigger.type == "WINDOW_REPEAT" and op.occurrence_key and "#" in op.occurrence_key:
            st = self._rep.setdefault(op.occurrence_key.rsplit("#", 1)[0], {"n": 0, "last_end": None})
            st["last_end"] = self.clock.mono()
            if res != "DONE":                         # a cycle that did not complete ends the window's repeats
                st["n"] = op.body.trigger.max_count
        self.event("PROGRAM_OP_ENDED", rec, "system")

    def _advance(self, op: Op) -> None:
        if op.state in ("STOPPING", "STOP_UNVERIFIED"):
            self._advance_stop(op)
            return
        mono = self.clock.mono()
        if mono >= op.deadline_mono:
            if op.body.action.type == "RUN_FOR":
                self._begin_stop(op, "duration reached", result="DONE")
            else:
                self.alarm("PROGRAM_RUNTIME_GUARD", op.program_id, True, {"op_id": op.op_id})
                self._begin_stop(op, "maximum runtime reached", result="RUNTIME_GUARD")
            return
        a = op.body.action
        if op.window_end_utc is not None and a.type in ("RUN_FOR", "KEEP_BETWEEN") and self.clock.utc() >= op.window_end_utc \
                and op.body.trigger.type != "WINDOW_REPEAT":
            self._begin_stop(op, "window ended", result="DONE")
            return
        if op.body.trigger.type == "WINDOW_REPEAT" and op.window_end_utc is not None and self.clock.utc() >= op.window_end_utc:
            self._begin_stop(op, "the window ended before this cycle completed", result="WINDOW_ENDED")
            return
        if self._guards(op):
            return
        for iid in list(op.on):                      # a fault input on running equipment stops the operation
            held = self._held(op, iid)
            if iid not in op.off_sent and held is not None and (why := self._fault_refusal(held)) is not None:
                self._fail(op, why)
                return
        for iid in list(op.stopped):                 # RR-01: switched OFF by this operation, now ON again
            if self._off_state(op, iid)[1] is True:
                self._fail(op, f"{getattr(self._held(op, iid), 'name', iid)}: verified OFF earlier, output reads ON again")
                return
        for iid in list(op.on):                      # running equipment must keep reporting ON where it can
            if iid not in op.off_sent and not self._feedback_ok(op, iid):
                return
        if a.type == "DELIVER_VOLUME":
            self._deliver(op, a.meter, a.meter_role, a.target_L, a.no_flow_timeout_s, a.min_flow_L_min,
                          finish=lambda: self._begin_stop(op, f"delivered {op.measured_L:.2f} L", result="DONE"))
        elif a.type == "KEEP_BETWEEN":
            it = self._items.get(a.sensor)
            v = self._analog(it, a.sensor_role) if it else None
            if v is None:
                self._fail(op, f"{a.sensor}: reading missing or stale")
            elif (a.direction == "RAISE" and v >= a.high) or (a.direction == "LOWER" and v <= a.low):
                self._begin_stop(op, f"{a.sensor} reached {v:.2f}", result="DONE")
        elif a.type == "SEQUENCE":
            self._sequence(op)

    def _feedback_ok(self, op: Op, iid: str) -> bool:
        it = self._held(op, iid)
        fb = it.feedback_role
        if fb is None:
            return True
        v = self._role_value(it, fb.role)
        if v is True:
            return True
        grace = fb.feedback_timeout_s or 5.0
        since = op.step_since if op.body.action.type == "SEQUENCE" else op.started_mono
        if self.clock.mono() - since <= grace:
            return True
        self._fail(op, f"{it.name}: {fb.label} is {'UNKNOWN' if v is None else 'OFF'} while it should be ON")
        return False

    def _deliver(self, op: Op, meter: str, role: str, target: float, no_flow_s: float, min_flow: float, finish) -> bool:
        it = self._items.get(meter)
        p = it.points.get(role) if it else None
        s = self.io.sample(p) if p else None
        rate = self._analog(it, role) if it else None
        mono = self.clock.mono()
        if rate is None or s is None:
            if op.last_rate is not None:
                gap = (self.clock.utc() - op.last_rate[0]).total_seconds()
                if gap > (it.body.stale_after_s if it else 5.0):
                    op.uncertain_L += max(0.0, op.last_rate[1]) * gap / 60.0
                    self._fail(op, f"{meter}: flow reading lost; {op.uncertain_L:.2f} L not measured")
                    return False
            elif mono - op.step_since > no_flow_s:
                self._fail(op, f"{meter}: no flow reading")
                return False
            return True
        t = s.acquired_utc
        if op.last_rate is not None and t > op.last_rate[0]:
            dt = (t - op.last_rate[0]).total_seconds()
            litres = max(0.0, (op.last_rate[1] + rate) / 2.0) * dt / 60.0
            if dt > it.body.stale_after_s:           # readings missed in between: that volume was not measured
                op.uncertain_L += litres
                self._fail(op, f"{meter}: {dt:.0f} s between flow readings; {op.uncertain_L:.2f} L not measured")
                return False
            op.measured_L += litres
        if op.last_rate is None or t > op.last_rate[0]:
            op.last_rate = (t, rate)
        if rate >= min_flow:
            op.last_flow_ok_mono = mono
        if mono - (op.last_flow_ok_mono or op.step_since) > no_flow_s:
            self._fail(op, f"{meter}: no flow for {no_flow_s:g} s")
            return False
        if op.measured_L - op.step_base_L >= target:
            finish()
            return False
        cap = self._volume_cap(op.held.values())
        if cap is not None and op.measured_L >= cap:
            self.alarm("PROGRAM_RUNTIME_GUARD", op.program_id, True, {"op_id": op.op_id})
            self._begin_stop(op, f"equipment maximum volume {cap:g} L reached", result="RUNTIME_GUARD")
            return False
        return True

    def _sequence(self, op: Op) -> None:
        steps = op.body.action.steps
        mono = self.clock.mono()
        while op.state == "RUNNING" and op.step < len(steps):
            s = steps[op.step]
            if s.do == "ON":
                if s.item not in op.on:
                    if not self._switch_on(op, s.item):
                        return
                    op.step_since = mono
                if s.verify_role:
                    v = self._role_value(self._items[s.item], s.verify_role)
                    if v is not True:
                        if mono - op.step_since > s.timeout_s:
                            self._fail(op, f"step {op.step + 1}: {s.item}.{s.verify_role} did not report ON "
                                           f"within {s.timeout_s:g} s ({'UNKNOWN' if v is None else 'OFF'})")
                        return
            elif s.do == "OFF":
                it = self._held(op, s.item)
                if s.item in op.on and s.item not in op.off_sent:
                    self.io.command(it.command_point, False, actor="program", reason=f"sequence {op.op_id}",
                                    priority=Priority.STOP)
                    op.off_sent.add(s.item)        # stays in `on` (the stop path's duty) until verified OFF
                    op.step_since = mono
                coil = self._digital(it.command_point) if it else None
                fbv = self._role_value(it, s.verify_role) if (it and s.verify_role) else False
                if coil is not False or fbv is not False:
                    if mono - op.step_since > s.timeout_s:
                        self._fail(op, f"step {op.step + 1}: {getattr(it, 'name', s.item)} not verified OFF")
                    return
                if s.item in op.on:
                    op.on.remove(s.item)
                    self._verified_off(op, s.item)
                op.off_sent.discard(s.item)
            elif s.do in ("WAIT", "RUN"):
                if mono - op.step_since < s.seconds:
                    return
            elif s.do == "DELIVER":
                if not self._deliver(op, s.meter, s.meter_role, s.target_L, s.no_flow_timeout_s, s.min_flow_L_min,
                                     finish=lambda: None):
                    if op.state != "RUNNING":
                        return
                    # target reached: next step
                else:
                    return
            op.step += 1
            op.step_since = mono
            op.last_rate = None
            op.step_base_L = op.measured_L
            self.event("PROGRAM_STEP", {"op_id": op.op_id, "step": op.step}, "system")
        if op.state == "RUNNING" and op.step >= len(steps):
            self._begin_stop(op, "sequence complete", result="DONE")
