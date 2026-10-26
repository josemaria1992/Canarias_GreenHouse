"""No-code watering programs (0.7.0): a typed form over a block, compiled into the engine's own primitives.

The form is what the operator fills in on the Programs page, in four parts:

* where: a block (its validated version) whose plan opens and starts the path;
* how much (the endpoint): run for a time, deliver a measured volume, or a manual run within a bound;
* when (the trigger): run now only, or selected weekdays at one or more start times, optionally inside a date
  range, optionally repeated inside a window after a rest (wait after each verified completion; hard window end);
* conditions: start permissives (all must hold; unknown blocks the start) and stop guards (any one stops the
  operation through its plan), each a measurement, a comparison, a value with its unit and a persistence.

`compile_form` turns it into a `ProgramBody` the engine runs: a SEQUENCE of the block's startup steps, the endpoint
(RUN or DELIVER), and the block's normal stop. The block's normal and fault stop plans are also carried as the
sequence's `normal_stop` and `fault_stop` (review R07-02): the engine follows the normal plan when an operation is
stopped early by a user, its guard time or its window, and the fault plan after a failure or a stop condition, step
by step with their waits and verified stops (programs/engine.py). The body carries the form
itself (`authoring`), so the program reopens in the same form, and its digest covers both. Time is a watering time
after the startup steps (labelled); the guard bounds the whole operation. A volume needs the block's inline meter,
in L/min, on the block's path: elapsed time or a pump's rated flow is never a volume.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..blocks.definitions import BlockBody
from ..equipment.definitions import ID_RE, Purpose
from . import schedule as sc

FORM_VERSION = 1
UNITS = {"L/min": "flow", "L": "volume", "bar": "pressure", "cm": "length", "%": "fraction", "pH": "ph",
         "mS/cm": "conductivity", "°C": "temperature"}


class _F(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Endpoint(_F):
    kind: Literal["TIME", "VOLUME", "MANUAL"]
    minutes: float | None = Field(default=None, gt=0, le=1440)      # TIME: watering after the startup steps
    litres: float | None = Field(default=None, gt=0, le=1e6)        # VOLUME: measured litres
    max_minutes: float = Field(gt=0, le=1440)                         # the guard on the whole operation
    no_flow_s: float = Field(default=20, gt=0, le=600)               # VOLUME: no measured flow for this long stops
    min_flow_L_min: float = Field(default=0.5, gt=0, le=1e4)

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "TIME" and self.minutes is None:
            raise ValueError("run for a time: give the minutes")
        if self.kind == "VOLUME" and self.litres is None:
            raise ValueError("deliver a volume: give the litres")
        if self.kind == "TIME" and self.minutes >= self.max_minutes:
            raise ValueError("the maximum operation time must be longer than the watering time (startup and stop take time)")
        return self


class Repeat(_F):
    rest_minutes: float = Field(gt=0, le=1440)       # after each verified completion
    window_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_count: int = Field(ge=1, le=96)


class Schedule(_F):
    kind: Literal["NOW_ONLY", "WEEKLY"]
    days: tuple[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], ...] = ()
    times: tuple[str, ...] = ()
    from_date: date | None = None
    to_date: date | None = None
    repeat: Repeat | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "WEEKLY":
            if not self.days:
                raise ValueError("choose at least one day")
            if not self.times or len(self.times) > 12:
                raise ValueError("give one to twelve start times")
            for t in self.times:
                sc._minutes(t)
            if len(set(self.times)) != len(self.times):
                raise ValueError("a start time is given twice")
            if self.from_date and self.to_date and self.from_date > self.to_date:
                raise ValueError("the date range ends before it starts")
            if self.repeat and len(self.times) != 1:
                raise ValueError("repeating within a window starts from one start time")
        elif self.days or self.times or self.repeat:
            raise ValueError("run now only takes no days, times or repeats")
        return self


class Rule(_F):
    item: str = Field(pattern=ID_RE)
    role: str = Field(pattern=ID_RE)
    op: Literal["ABOVE", "BELOW", "IS_ON", "IS_OFF"]
    value: float | None = None
    unit: str | None = Field(default=None, max_length=16)
    persist_s: float = Field(default=5, ge=0, le=3600)

    @model_validator(mode="after")
    def _check(self):
        if (self.op in ("ABOVE", "BELOW")) != (self.value is not None):
            raise ValueError("above/below need a value with its unit; is on/is off take none")
        return self


class WateringForm(_F):
    form: Literal["WATERING"] = "WATERING"
    form_version: Literal[1] = FORM_VERSION
    block_id: str = Field(pattern=ID_RE)
    block_version: int = Field(ge=1)
    endpoint: Endpoint
    schedule: Schedule
    permissives: tuple[Rule, ...] = Field(default=(), max_length=8)
    guards: tuple[Rule, ...] = Field(default=(), max_length=8)
    description: str = Field(default="", max_length=500)


class FormRefused(ValueError):
    def __init__(self, msg: str, field: str | None = None):
        super().__init__(msg)
        self.field = field


def _verify_role(view) -> str | None:
    """The role that shows an item really followed its command (running feedback, open switch), if it has one."""
    for p in (Purpose.RUNNING, Purpose.OPEN):
        r = next((x for x in view.type.roles if x.purpose == p and x.role in view.points), None)
        if r is not None:
            return r.role
    return None


def _check_rule(r: Rule, views: dict, where: str) -> None:
    it = views.get(r.item)
    if it is None:
        raise FormRefused(f"{where}: {r.item} is not equipment of this installation", where)
    role = next((x for x in it.type.roles if x.role == r.role), None)
    if role is None:
        raise FormRefused(f"{where}: {it.name} has no reading {r.role}", where)
    analog = role.signal.value == "ANALOG"
    if analog != (r.op in ("ABOVE", "BELOW")):
        raise FormRefused(f"{where}: {it.name} {role.label} is {'a measurement: compare it above or below a value' if analog else 'on/off: test it is on or off'}", where)
    if role.purpose == Purpose.COMMAND:
        raise FormRefused(f"{where}: a command output is not independent evidence; choose a feedback or a sensor", where)
    if analog:
        sc_ = it.body.scaling.get(r.role)
        unit = (sc_.unit if sc_ else role.unit) or ""
        if not unit or (r.unit or "") != unit:
            raise FormRefused(f"{where}: {it.name} {role.label} is in {unit or 'no unit (it needs its scaling)'}; "
                              f"the value is in {r.unit or 'no unit'}", where)


def compile_form(f: WateringForm, block: BlockBody, block_status: str, views: dict, *, meter_role: str = "flow") -> dict:
    """The ProgramBody (as a dict) for the form, or FormRefused naming the field to correct."""
    if block_status != "VALIDATED":
        raise FormRefused("the block is not validated: validate it on Blocks first", "block_id")
    steps = []
    for s in block.plan.startup:
        if s.do == "WAIT":
            steps.append({"do": "WAIT", "seconds": s.seconds})
            continue
        v = views.get(s.node)
        if v is None or v.command_point is None:
            raise FormRefused(f"{s.node} in the block's startup has no command channel in this installation", "block_id")
        step = {"do": "ON", "item": s.node, "timeout_s": s.timeout_s}
        if s.verify:
            vr = _verify_role(v)
            if vr is None:
                raise FormRefused(f"the block's startup waits for {v.name}'s feedback, but it has no feedback channel: "
                                  f"give {v.name} one, or untick \"wait for its feedback\" in the block's plan", "block_id")
            step["verify_role"] = vr
        steps.append(step)
    e = f.endpoint
    if e.kind == "VOLUME":
        if not block.meter:
            raise FormRefused("this block has no flow meter on its path: deliver a volume needs one (or run for a time)",
                              "endpoint.kind")
        mv = views.get(block.meter)
        role = next((x for x in mv.type.roles if x.role == meter_role), None) if mv else None
        unit = ((mv.body.scaling.get(meter_role).unit if mv and mv.body.scaling.get(meter_role) else None)
                or (role.unit if role else None) or "")
        if mv is None or role is None or role.purpose != Purpose.MEASUREMENT or unit.replace(" ", "").lower() != "l/min":
            raise FormRefused(f"{block.meter} does not measure a flow rate in L/min: it cannot measure litres",
                              "endpoint.kind")
        steps.append({"do": "DELIVER", "meter": block.meter, "meter_role": meter_role, "target_L": e.litres,
                      "no_flow_timeout_s": e.no_flow_s, "min_flow_L_min": e.min_flow_L_min})
    elif e.kind == "TIME":
        steps.append({"do": "RUN", "seconds": e.minutes * 60})
    else:                                               # MANUAL: runs until stopped, within the guard
        steps.append({"do": "RUN", "seconds": e.max_minutes * 60})
    normal_stop = _stop_plan(block.plan.normal_stop, steps, "normal stop")
    fault_stop = _stop_plan(block.plan.fault_stop, steps, "fault stop")
    steps.extend(normal_stop)
    for i, r in enumerate(f.permissives):
        _check_rule(r, views, f"start condition {i + 1}")
    for i, r in enumerate(f.guards):
        _check_rule(r, views, f"stop condition {i + 1}")
    guard_s = e.max_minutes * 60
    sch = f.schedule
    if sch.kind == "NOW_ONLY":
        trigger = {"type": "MANUAL"}
    else:
        if sch.repeat:
            end = sch.repeat.window_end
            trigger = {"type": "WINDOW_REPEAT", "entries": [{"days": list(sch.days), "start": sch.times[0], "end": end,
                                                              "from_date": _iso(sch.from_date), "to_date": _iso(sch.to_date)}],
                       "rest_s": sch.repeat.rest_minutes * 60, "max_count": sch.repeat.max_count}
        else:
            entries = []
            for t in sch.times:                       # each start opens a window as long as the guard
                m = sc._minutes(t) + math.ceil(guard_s / 60)
                entries.append({"days": list(sch.days), "start": t, "end": f"{(m // 60) % 24:02d}:{m % 60:02d}",
                                "from_date": _iso(sch.from_date), "to_date": _iso(sch.to_date)})
            trigger = {"type": "WEEKLY", "entries": entries}
    body = {"description": f.description, "trigger": trigger,
            "action": {"type": "SEQUENCE", "steps": steps, "max_runtime_s": guard_s}
            | ({"normal_stop": normal_stop} if normal_stop else {}) | ({"fault_stop": fault_stop} if fault_stop else {}),
            "permissives": [_cond(r) for r in f.permissives], "guards": [_cond(r) for r in f.guards],
            "authoring": f.model_dump(mode="json"), "basis": "WATERING_AFTER_STARTUP" if e.kind == "TIME" else None}
    if len(steps) > 32:
        raise FormRefused("the block's plan and the watering make more than 32 steps", "block_id")
    return body


def _stop_plan(plan, steps: list[dict], name: str) -> list[dict]:
    """A block's stop plan as engine steps: STOP/CLOSE switch off (verified), WAIT waits; only items its startup
    switched on, since an operation stops what it owns and never starts anything on the way out."""
    started = {s["item"] for s in steps if s["do"] == "ON"}
    out = []
    for s in plan:
        if s.do == "WAIT":
            out.append({"do": "WAIT", "seconds": s.seconds})
        elif s.do in ("START", "OPEN"):
            raise FormRefused(f"the block's {name} {s.do.lower()}s {s.node}: a stop plan only stops, closes and waits",
                              "block_id")
        elif s.node not in started:
            raise FormRefused(f"the block's {name} acts on {s.node}, which its startup never switches on", "block_id")
        else:
            out.append({"do": "OFF", "item": s.node, "timeout_s": s.timeout_s})
    return out


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _cond(r: Rule) -> dict:
    return {"item": r.item, "role": r.role, "op": r.op, "value": r.value, "debounce_s": r.persist_s}


OPS = {"ABOVE": "is above", "BELOW": "is below", "IS_ON": "is on", "IS_OFF": "is off"}


def summary(f: WateringForm, block_name: str, names: dict[str, str], tz: str, now_utc: datetime) -> dict:
    """Plain words for the program card and the preview, from the form; plus the next three starts."""
    e, s = f.endpoint, f.schedule
    what = (f"water for {e.minutes:g} min (after the startup steps)" if e.kind == "TIME" else
            f"deliver {e.litres:g} L, measured" if e.kind == "VOLUME" else
            f"run by hand until stopped")
    guard = f"never longer than {e.max_minutes:g} min in all"
    if s.kind == "NOW_ONLY":
        when, nxt = "when someone starts it", []
    else:
        days = ", ".join(d.title() for d in s.days)
        when = f"on {days} at {', '.join(s.times)} ({tz})"
        if s.from_date or s.to_date:
            when += f", from {s.from_date or 'now'} to {s.to_date or 'no end date'}"
        if s.repeat:
            when += (f", then again {s.repeat.rest_minutes:g} min after each completion, at most {s.repeat.max_count} times, "
                     f"never starting or running after {s.repeat.window_end}")
        entries = [{"days": list(s.days), "start": t, "end": t, "from_date": _iso(s.from_date), "to_date": _iso(s.to_date)}
                   for t in s.times]
        nxt = [w.start_local for w in sc.upcoming("preview", entries, tz, now_utc, 30)
               if w.start_utc > now_utc][:3]
    rule = lambda r: f"{names.get(r.item, r.item)} {r.role} {OPS[r.op]}{'' if r.value is None else f' {r.value:g} {r.unit}'} for {r.persist_s:g} s"  # noqa: E731
    return {"text": f"{block_name}: {what}, {when}; {guard}.", "next": nxt,
            "start_conditions": [rule(r) for r in f.permissives], "stop_conditions": [rule(r) for r in f.guards]}
