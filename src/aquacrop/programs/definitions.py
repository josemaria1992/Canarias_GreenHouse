"""Program bodies: a trigger and an action over equipment items, as validated data.

Triggers
  MANUAL         started by an authorized user ("run now"), in MANUAL mode only
  WEEKLY         weekly windows in the site time zone (`schedule.py`), optionally inside a local date range
  CONDITION      an input condition held for a debounce time, re-armed only after it clears
  CONTINUOUS     always armed while the program is active (KEEP_BETWEEN), optionally limited to weekly windows
  WINDOW_REPEAT  (0.7.0) a weekly window in which the action repeats: the next cycle starts `rest_s` after the
                 previous one ended verified, at most `max_count` cycles; nothing starts after the window's end
                 and a cycle still running then is stopped through its plan (reported WINDOW_ENDED, not DONE)

Conditions on any program (0.7.0): `permissives` must all hold (for their debounce) when an operation starts;
`guards` are watched while it runs and any one that holds for its debounce stops it through its plan. A missing,
stale or bad reading is UNKNOWN: it prevents the start, and stops a running operation.

Actions
  RUN_FOR         one item ON for a bounded duration (or for the window, bounded by the item's max runtime)
  DELIVER_VOLUME  one item ON until a measured volume, a runtime guard or a no-flow timeout
  KEEP_BETWEEN    one item switched to keep a measurement inside [low, high] with hysteresis and debounce
  SEQUENCE        ordered steps: ON / OFF with verification, WAIT, RUN, DELIVER; optional normal and fault stop
                  plans (OFF / WAIT) the engine follows step by step when it stops the operation early

Bounds that every program carries: a maximum runtime per operation (never above the item's), a maximum
number of steps (no loops exist), timeouts on every verification, and a failure path (`on_failure`).
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Union

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_serializer, model_validator

from ..equipment.definitions import ID_RE, ItemBody, Operation, Purpose, TypeBody, flow_factor
from .schedule import written_length_s

MAX_STEPS = 32


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def _drop_defaults(d: dict, keys: dict) -> dict:
    """Fields added in 0.7.0 are left out of a body while they hold their default, so the digest of every program
    written before them stays exactly what it was."""
    for k, default in keys.items():
        if k not in d:
            continue
        v = d[k]
        if v == default or (isinstance(default, list) and isinstance(v, (list, tuple)) and not v):
            del d[k]
    return d


class WeeklyEntry(_M):
    days: tuple[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], ...] = Field(min_length=1)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    from_date: date | None = None              # inclusive local start dates of the occurrences (0.7.0)
    to_date: date | None = None

    @model_validator(mode="after")
    def _range(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("the date range ends before it starts")
        return self

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_defaults(handler(self), {"from_date": None, "to_date": None})


class Condition(_M):
    """An input condition: a digital role's state, or an analog measurement against a threshold."""
    item: str = Field(pattern=ID_RE)
    role: str = Field(pattern=ID_RE)
    op: Literal["IS_ON", "IS_OFF", "ABOVE", "BELOW"]
    value: float | None = None
    debounce_s: float = Field(default=2.0, ge=0, le=3600)

    @model_validator(mode="after")
    def _v(self):
        if (self.op in ("ABOVE", "BELOW")) != (self.value is not None):
            raise ValueError("ABOVE/BELOW need a value; IS_ON/IS_OFF take none")
        return self


class ManualTrigger(_M):
    type: Literal["MANUAL"]


class WeeklyTrigger(_M):
    type: Literal["WEEKLY"]
    entries: tuple[WeeklyEntry, ...] = Field(min_length=1, max_length=28)


class ConditionTrigger(_M):
    type: Literal["CONDITION"]
    when: Condition
    rearm_after_s: float = Field(default=60, ge=0, le=86400)


class ContinuousTrigger(_M):
    type: Literal["CONTINUOUS"]
    entries: tuple[WeeklyEntry, ...] = ()            # empty: always armed


class WindowRepeatTrigger(_M):
    type: Literal["WINDOW_REPEAT"]
    entries: tuple[WeeklyEntry, ...] = Field(min_length=1, max_length=7)
    rest_s: float = Field(gt=0, le=86400)            # after each verified completion
    max_count: int = Field(ge=1, le=96)


Trigger = Union[ManualTrigger, WeeklyTrigger, ConditionTrigger, ContinuousTrigger, WindowRepeatTrigger]


class RunFor(_M):
    type: Literal["RUN_FOR"]
    item: str = Field(pattern=ID_RE)
    duration_s: float | None = Field(default=None, gt=0, le=86400)    # None: the window (WEEKLY only)


class DeliverVolume(_M):
    type: Literal["DELIVER_VOLUME"]
    item: str = Field(pattern=ID_RE)                 # the actuator
    meter: str = Field(pattern=ID_RE)                # an item with an analog MEASUREMENT role (flow rate, L/min)
    meter_role: str = Field(default="flow", pattern=ID_RE)
    target_L: float = Field(gt=0, le=1e6)
    max_runtime_s: float = Field(gt=0, le=86400)
    no_flow_timeout_s: float = Field(default=20, gt=0, le=600)
    min_flow_L_min: float = Field(default=0.5, gt=0)


class KeepBetween(_M):
    type: Literal["KEEP_BETWEEN"]
    item: str = Field(pattern=ID_RE)
    sensor: str = Field(pattern=ID_RE)
    sensor_role: str = Field(pattern=ID_RE)
    direction: Literal["RAISE", "LOWER"]             # RAISE: ON below low until high; LOWER: ON above high until low
    low: float
    high: float
    debounce_s: float = Field(default=5, ge=0, le=3600)
    max_run_s: float = Field(gt=0, le=86400)
    min_off_s: float = Field(default=30, ge=0, le=86400)

    @model_validator(mode="after")
    def _v(self):
        if self.low >= self.high:
            raise ValueError("low must be below high (the gap is the hysteresis)")
        return self


class Step(_M):
    do: Literal["ON", "OFF", "WAIT", "RUN", "DELIVER"]
    item: str | None = Field(default=None, pattern=ID_RE)
    verify_role: str | None = Field(default=None, pattern=ID_RE)   # ON/OFF: a feedback role that must follow
    timeout_s: float = Field(default=10, gt=0, le=600)
    seconds: float | None = Field(default=None, gt=0, le=86400)    # WAIT / RUN
    meter: str | None = Field(default=None, pattern=ID_RE)         # DELIVER
    meter_role: str = Field(default="flow", pattern=ID_RE)
    target_L: float | None = Field(default=None, gt=0, le=1e6)
    no_flow_timeout_s: float = Field(default=20, gt=0, le=600)
    min_flow_L_min: float = Field(default=0.5, gt=0)

    @model_validator(mode="after")
    def _v(self):
        if self.do in ("ON", "OFF") and not self.item:
            raise ValueError(f"{self.do} needs an item")
        if self.do in ("WAIT", "RUN") and self.seconds is None:
            raise ValueError(f"{self.do} needs seconds")
        if self.do == "DELIVER" and (not self.meter or self.target_L is None):
            raise ValueError("DELIVER needs a meter and target_L")
        return self


class Sequence(_M):
    type: Literal["SEQUENCE"]
    steps: tuple[Step, ...] = Field(min_length=1, max_length=MAX_STEPS)
    max_runtime_s: float = Field(gt=0, le=86400)
    # review R07-02: the process's own ways to stop (OFF and WAIT steps over items the steps switch on). An
    # interrupted operation (stopped by a user, its runtime guard or its window) stops through `normal_stop`; a
    # failure, a stop condition or a stop of everything through `fault_stop`. Either one empty: every item still
    # on is switched off, the last one first. Items a plan does not name are switched off after it, last first.
    normal_stop: tuple[Step, ...] = Field(default=(), max_length=MAX_STEPS)
    fault_stop: tuple[Step, ...] = Field(default=(), max_length=MAX_STEPS)

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_defaults(handler(self), {"normal_stop": [], "fault_stop": []})

    @model_validator(mode="after")
    def _v(self):
        started = {s.item for s in self.steps if s.do == "ON"}
        for name, plan in (("normal stop", self.normal_stop), ("fault stop", self.fault_stop)):
            for s in plan:
                if s.do not in ("OFF", "WAIT"):
                    raise ValueError(f"the {name} only switches off and waits")
                if s.do == "OFF" and s.item not in started:
                    raise ValueError(f"the {name} switches off {s.item}, which the sequence never switches on")
        return self


Action = Union[RunFor, DeliverVolume, KeepBetween, Sequence]


class ProgramBody(_M):
    description: str = Field(default="", max_length=500)
    trigger: Trigger = Field(discriminator="type")
    action: Action = Field(discriminator="type")
    on_failure: Literal["STOP_AND_ALARM"] = "STOP_AND_ALARM"
    enabled: bool = True
    permissives: tuple[Condition, ...] = Field(default=(), max_length=16)    # 0.7.0; 0.7.3: the block's own first
    guards: tuple[Condition, ...] = Field(default=(), max_length=16)         # 0.7.0
    authoring: dict | None = None           # 0.7.0: the form this body was compiled from (programs/authoring.py)
    basis: Literal["WATERING_AFTER_STARTUP"] | None = None   # what a timed run's duration counts
    duty: tuple[str, str] | None = None     # 0.7.3 PROG-09: (first, second) pump; each start leads with the one
    #                                         started least recently, the standby takes a start the lead cannot
    simulation_only: Literal["NUTRIENT_PREPARATION"] | None = None   # 0.7.3 PROG-10: never starts on real equipment

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_defaults(handler(self), {"permissives": [], "guards": [], "authoring": None, "basis": None,
                                              "duty": None, "simulation_only": None})

    @model_validator(mode="after")
    def _v(self):
        t, a = self.trigger.type, self.action.type
        if a == "KEEP_BETWEEN" and t != "CONTINUOUS":
            raise ValueError("KEEP_BETWEEN runs with a CONTINUOUS trigger")
        if t == "CONTINUOUS" and a != "KEEP_BETWEEN":
            raise ValueError("a CONTINUOUS trigger is for KEEP_BETWEEN only")
        if a == "RUN_FOR" and self.action.duration_s is None and t != "WEEKLY":
            raise ValueError("RUN_FOR without a duration runs for its weekly window; give a duration otherwise")
        if t == "WINDOW_REPEAT" and a not in ("SEQUENCE", "RUN_FOR", "DELIVER_VOLUME"):
            raise ValueError("a repeat inside a window runs a sequence, a timed run or a volume")
        if self.authoring is not None:
            from .authoring import WateringForm
            WateringForm.model_validate(self.authoring)     # a stored form is a valid form
        return self


def parse(body: dict) -> ProgramBody:
    return ProgramBody.model_validate(body)


def digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def actuators(p: ProgramBody) -> list[str]:
    """The items whose COMMAND outputs this program switches: its resources for ownership."""
    a = p.action
    if a.type == "SEQUENCE":
        return sorted({s.item for s in a.steps if s.do in ("ON", "OFF") and s.item})
    return [a.item]


def sensors(p: ProgramBody) -> list[tuple[str, str]]:
    out = []
    a, t = p.action, p.trigger
    if t.type == "CONDITION":
        out.append((t.when.item, t.when.role))
    for c in p.permissives + p.guards:
        out.append((c.item, c.role))
    if a.type == "DELIVER_VOLUME":
        out.append((a.meter, a.meter_role))
    if a.type == "KEEP_BETWEEN":
        out.append((a.sensor, a.sensor_role))
    if a.type == "SEQUENCE":
        for s in a.steps:
            if s.verify_role:
                out.append((s.item, s.verify_role))
            if s.do == "DELIVER":
                out.append((s.meter, s.meter_role))
    return out


def required_operation(p: ProgramBody) -> dict[str, Operation]:
    a, t = p.action, p.trigger
    if a.type == "SEQUENCE":
        return {i: Operation.SEQUENCE_STEP for i in actuators(p)}
    op = {"RUN_FOR": Operation.SCHEDULE if t.type == "WEEKLY" else Operation.TRIGGERED if t.type == "CONDITION"
          else Operation.RUN_FOR, "DELIVER_VOLUME": Operation.DELIVER_VOLUME, "KEEP_BETWEEN": Operation.KEEP_BETWEEN}[a.type]
    return {a.item: op}


def validate(p: ProgramBody, items: dict[str, dict], types: dict[tuple[str, int], TypeBody], *,
             physical: bool, bindings: dict[str, dict], readiness: dict[str, dict]) -> list[str]:
    """Problems that block activation (empty list: valid). `physical` adds the checks a real runtime needs:
    every role used is bound and VERIFIED, every measurement scaled with non-DEMO provenance."""
    out = []

    def item(iid: str):
        it = items.get(iid)
        if it is None or it["status"] != "ACTIVE":
            out.append(f"equipment {iid} does not exist or is retired")
            return None, None
        return it, types[(it["type_id"], it["type_version"])]

    for iid, op in required_operation(p).items():
        it, tb = item(iid)
        if it is None:
            continue
        if op not in tb.operations and not (op == Operation.RUN_FOR and Operation.SCHEDULE in tb.operations):
            out.append(f"{it['name']}: its type does not permit {op.value}")
        if not any(r.purpose == Purpose.COMMAND for r in tb.roles):
            out.append(f"{it['name']} has no command output")
        cap = tb.limits.max_runtime_s
        for need, what in ((_runtime(p), "the program's runtime"),):
            if cap is not None and need is not None and need > cap:
                out.append(f"{it['name']}: {what} {need:g} s is above the type's maximum {cap:g} s")
    a = p.action
    meters = ({(a.meter, a.meter_role)} if a.type == "DELIVER_VOLUME" else
              {(s.meter, s.meter_role) for s in a.steps if s.do == "DELIVER"} if a.type == "SEQUENCE" else set())
    analog_sensors = {(a.sensor, a.sensor_role)} if a.type == "KEEP_BETWEEN" else set()
    for iid, role in sensors(p):
        it, tb = item(iid)
        if it is None:
            continue
        r = next((x for x in tb.roles if x.role == role), None)
        if r is None:
            out.append(f"{it['name']} has no role {role}")
            continue
        if r.direction.value != "INPUT":
            out.append(f"{it['name']} {r.label} is not an input")
        if (iid, role) in meters:                    # a volume needs a flow rate (R402-02: converted to L/min)
            sc = ItemBody.model_validate(it["body"]).scaling.get(role)
            unit = (sc.unit if sc else r.unit) or ""
            if r.purpose != Purpose.MEASUREMENT or flow_factor(unit) is None:
                out.append(f"{it['name']} {r.label} reads {unit or 'no unit'}, not a flow rate, so it cannot measure "
                           "a volume: choose a flow meter, or set its unit on Equipment to L/min, L/h, m³/h or L/s")
        elif (iid, role) in analog_sensors and r.purpose != Purpose.MEASUREMENT:
            out.append(f"{it['name']} {r.label} is not an analog measurement")
        if physical:
            b = bindings.get(iid, {}).get(role)
            if not b or b["status"] != "VERIFIED":
                out.append(f"{it['name']} {r.label}: no VERIFIED channel")
            if r.purpose == Purpose.MEASUREMENT:
                sc = ItemBody.model_validate(it["body"]).scaling.get(role)
                if sc is None:
                    out.append(f"{it['name']} {r.label}: no scaling")
                elif sc.provenance == "DEMO":
                    out.append(f"{it['name']} {r.label}: DEMO scaling cannot measure a real operation")
    if physical:
        for iid in actuators(p):
            rd = readiness.get(iid)
            if rd and not rd["ready"]:
                out.append(f"equipment {iid} is not ready: " + "; ".join(
                    x for x in (", ".join(rd["missing_required"]) and f"no channel for {', '.join(rd['missing_required'])}",
                                ", ".join(rd["unverified"]) and f"not verified: {', '.join(rd['unverified'])}") if x))
    t = p.trigger
    conds = ([("the trigger", t.when)] if t.type == "CONDITION" else []) + \
        [(f"start condition {i + 1}", c) for i, c in enumerate(p.permissives)] + \
        [(f"stop condition {i + 1}", c) for i, c in enumerate(p.guards)]
    for what, c in conds:
        it, tb = item(c.item)
        if tb is not None:
            r = next((x for x in tb.roles if x.role == c.role), None)
            if r is not None and (r.signal.value == "ANALOG") != (c.op in ("ABOVE", "BELOW")):
                out.append(f"{what}: the comparison does not fit its signal (above/below for a measurement, "
                           "is on/is off for an on/off input)")
            if r is not None and r.purpose == Purpose.COMMAND:
                out.append(f"{what}: a command output is not independent evidence")
    if t.type == "WEEKLY" and p.action.type == "RUN_FOR" and p.action.duration_s is None:
        longest = max(written_length_s(e.start, e.end) for e in t.entries)
        it, tb = item(p.action.item)
        if tb is not None and tb.limits.max_runtime_s is not None and longest > tb.limits.max_runtime_s:
            out.append(f"a window of {longest / 60:g} min is longer than {it['name']}'s maximum runtime "
                       f"{tb.limits.max_runtime_s / 60:g} min")
    return out


def _runtime(p: ProgramBody) -> float | None:
    a = p.action
    return {"RUN_FOR": getattr(a, "duration_s", None), "DELIVER_VOLUME": getattr(a, "max_runtime_s", None),
            "KEEP_BETWEEN": getattr(a, "max_run_s", None), "SEQUENCE": getattr(a, "max_runtime_s", None)}[a.type]


def errors_text(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        out.append(f"{loc}: {err.get('msg', '').removeprefix('Value error, ')}")
    return out
