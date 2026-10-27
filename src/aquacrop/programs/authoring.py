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

Form version 2 (0.7.1, owner decisions 26 October 2026). How much is one endpoint per cycle: water for a time, or
deliver litres (measured; a litre cycle ends at its measured target, with no second duration). When is a set of
windows on chosen days, each with a start and a finish, and the wait after each verified cycle before the next one is
requested. The first cycle of a window does not wait. At the finish the cycle is stopped through the block's normal
stop, whatever it had left (an unmet volume is WINDOW_ENDED, never DONE). A window that follows another starts its own
first cycle once the earlier operation is verified stopped and its equipment is free; it never inherits the earlier
wait or the earlier litres. The independent maximum runtime is the block's safety setting (Blocks), configured once:
a form never carries it, and a program cannot be saved over a block without one, or whose watering time does not fit
inside it. Version 1 forms (with `max_minutes`) keep compiling exactly as before.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..blocks.definitions import BlockBody
from ..equipment.definitions import ID_RE, Purpose
from . import schedule as sc

FORM_VERSION = 2
UNITS = {"L/min": "flow", "L": "volume", "bar": "pressure", "cm": "length", "%": "fraction", "pH": "ph",
         "mS/cm": "conductivity", "°C": "temperature"}


class _F(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Endpoint(_F):
    kind: Literal["TIME", "VOLUME", "MANUAL"]
    minutes: float | None = Field(default=None, gt=0, le=1440)      # TIME: watering after the startup steps
    litres: float | None = Field(default=None, gt=0, le=1e6)        # VOLUME: measured litres
    max_minutes: float | None = Field(default=None, gt=0, le=1440)  # form 1 only: the guard (form 2: block safety)
    no_flow_s: float = Field(default=20, gt=0, le=600)               # VOLUME: no measured flow for this long stops
    min_flow_L_min: float = Field(default=0.5, gt=0, le=1e4)

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "TIME" and self.minutes is None:
            raise ValueError("water for a time: give the minutes")
        if self.kind == "VOLUME" and self.litres is None:
            raise ValueError("deliver litres: give the litres")
        if self.kind == "TIME" and self.litres is not None or self.kind == "VOLUME" and self.minutes is not None:
            raise ValueError("a cycle has one endpoint: a watering time or a volume, not both")
        if self.max_minutes is not None and self.kind == "TIME" and self.minutes >= self.max_minutes:
            raise ValueError("the maximum operation time must be longer than the watering time (startup and stop take time)")
        return self


class Window(_F):
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    finish: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def _check(self):
        if self.start == self.finish:
            raise ValueError(f"the window {self.start}-{self.finish} has no length")
        return self


class Repeat(_F):
    rest_minutes: float = Field(gt=0, le=1440)       # after each verified completion
    window_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_count: int = Field(ge=1, le=96)


class Schedule(_F):
    kind: Literal["NOW_ONLY", "WEEKLY"]
    days: tuple[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], ...] = ()
    times: tuple[str, ...] = ()                       # form 1
    from_date: date | None = None
    to_date: date | None = None
    repeat: Repeat | None = None                      # form 1
    windows: tuple[Window, ...] = ()                  # form 2: start and finish of each watering window
    rest_minutes: float | None = Field(default=None, gt=0, le=1440)   # form 2: wait after each verified cycle
    max_cycles: int | None = Field(default=None, ge=1, le=96)         # form 2, optional: cycles per window

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "WEEKLY" and self.windows:
            if not self.days:
                raise ValueError("choose at least one day")
            if len(self.windows) > 12:
                raise ValueError("at most twelve windows")
            if self.times or self.repeat:
                raise ValueError("windows replace start times and repeats: give one or the other")
            if self.rest_minutes is None:
                raise ValueError("give the wait after each cycle (minutes)")
            if self.from_date and self.to_date and self.from_date > self.to_date:
                raise ValueError("the date range ends before it starts")
            spans = sorted((sc._minutes(w.start), sc._minutes(w.start) + sc.written_length_s(w.start, w.finish) // 60)
                           for w in self.windows)
            for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
                if b0 < a1:
                    raise ValueError("two windows overlap: one window must finish before (or when) the next starts")
            if spans and spans[-1][1] > 1440 and spans[-1][1] - 1440 > spans[0][0]:
                raise ValueError("an overnight window runs into the next day's first window")
            return self
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
        elif self.days or self.times or self.repeat or self.windows:
            raise ValueError("run now only takes no days, windows, times or repeats")
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
    form_version: Literal[1, 2] = FORM_VERSION
    block_id: str = Field(pattern=ID_RE)
    block_version: int = Field(ge=1)
    endpoint: Endpoint
    schedule: Schedule
    permissives: tuple[Rule, ...] = Field(default=(), max_length=8)
    guards: tuple[Rule, ...] = Field(default=(), max_length=8)
    description: str = Field(default="", max_length=500)

    @model_validator(mode="before")
    @classmethod
    def _infer_version(cls, data):
        """A form sent without its version is version 1 when it has the old fields (a maximum time, start times),
        else version 2. The Programs page always names it."""
        if isinstance(data, dict) and "form_version" not in data:
            e, s = data.get("endpoint") or {}, data.get("schedule") or {}
            old = isinstance(e, dict) and e.get("max_minutes") is not None or isinstance(s, dict) and (
                s.get("times") or s.get("repeat"))
            data = dict(data, form_version=1 if old else 2)
        return data

    @model_validator(mode="after")
    def _version(self):
        e, s = self.endpoint, self.schedule
        if self.form_version == 1:
            if e.max_minutes is None:
                raise ValueError("give the maximum operation time")
            if s.windows:
                raise ValueError("windows need form version 2")
        else:
            if e.kind == "MANUAL":
                raise ValueError("how much: water for a time, or deliver litres")
            if e.max_minutes is not None:
                raise ValueError("the maximum runtime is the block's safety setting, not part of the program")
            if s.kind == "WEEKLY" and not s.windows:
                raise ValueError("give at least one watering window (start and finish)")
        return self


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
    # 0.7.3: the block's own start and stop conditions apply to every program over it, before the program's own
    bp = tuple(Rule.model_validate(c.model_dump()) for c in block.safety.permissives)
    bg = tuple(Rule.model_validate(c.model_dump()) for c in block.safety.guards)
    for i, r in enumerate(bp):
        _check_rule(r, views, f"the block's start condition {i + 1}")
    for i, r in enumerate(bg):
        _check_rule(r, views, f"the block's stop condition {i + 1}")
    for i, r in enumerate(f.permissives):
        _check_rule(r, views, f"start condition {i + 1}")
    for i, r in enumerate(f.guards):
        _check_rule(r, views, f"stop condition {i + 1}")
    bound = block.safety.max_runtime_s
    if f.form_version == 1:
        guard_s = e.max_minutes * 60 if bound is None else min(e.max_minutes * 60, bound)   # the stricter one
    else:
        if bound is None:
            raise FormRefused("Set the block's maximum runtime on Blocks (its safety settings), then preview again",
                              "block_id")
        guard_s = bound
        if e.kind == "TIME" and e.minutes * 60 >= bound:
            raise FormRefused(f"watering {e.minutes:g} min does not fit inside the block's maximum runtime of "
                              f"{bound / 60:g} min (the startup and the verified stop need time too): water for less, "
                              "or change the block's safety setting", "endpoint.minutes")
    sch = f.schedule
    if sch.kind == "NOW_ONLY":
        trigger = {"type": "MANUAL"}
    elif sch.windows:
        trigger = {"type": "WINDOW_REPEAT", "entries": [{"days": list(sch.days), "start": w.start, "end": w.finish,
                                                          "from_date": _iso(sch.from_date), "to_date": _iso(sch.to_date)}
                                                         for w in sch.windows],
                   "rest_s": sch.rest_minutes * 60, "max_count": sch.max_cycles or 96}
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
            "permissives": [_cond(r) for r in bp + f.permissives], "guards": [_cond(r) for r in bg + f.guards],
            "authoring": f.model_dump(mode="json"), "basis": "WATERING_AFTER_STARTUP" if e.kind == "TIME" else None}
    if block.duty is not None:                          # PROG-09: the engine chooses the lead per start
        body["duty"] = list(block.duty.pumps)
    if block.template == "NUTRIENT_PREPARATION":        # PROG-10: simulated in the twin; real dosing stays blocked
        body["simulation_only"] = "NUTRIENT_PREPARATION"
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


def summary(f: WateringForm, block_name: str, names: dict[str, str], tz: str, now_utc: datetime, block=None) -> dict:
    """Plain words for the program card and the preview, from the form; plus the next three starts."""
    e, s = f.endpoint, f.schedule
    what = (f"water for {e.minutes:g} min (after the startup steps)" if e.kind == "TIME" else
            f"deliver {e.litres:g} L, measured" if e.kind == "VOLUME" else
            f"run by hand until stopped")
    guard = (f"never longer than {e.max_minutes:g} min in all" if e.max_minutes is not None else
             "never longer than the block's maximum runtime")
    if s.kind == "NOW_ONLY":
        when, nxt = "when someone starts it", []
    elif s.windows:
        days = ", ".join(d.title() for d in s.days)
        wins = ", ".join(f"{w.start}-{w.finish}" for w in s.windows)
        when = (f"on {days} in the window{'s' if len(s.windows) > 1 else ''} {wins} ({tz}), a cycle at the start of each "
                f"window and the next {s.rest_minutes:g} min after each verified cycle"
                + (f", at most {s.max_cycles} per window" if s.max_cycles else "")
                + "; a cycle still running at the finish is stopped")
        if s.from_date or s.to_date:
            when += f", from {s.from_date or 'now'} to {s.to_date or 'no end date'}"
        entries = [{"days": list(s.days), "start": w.start, "end": w.finish, "from_date": _iso(s.from_date),
                    "to_date": _iso(s.to_date)} for w in s.windows]
        nxt = [w.start_local for w in sc.upcoming("preview", entries, tz, now_utc, 30) if w.start_utc > now_utc][:3]
        what = what.replace("water for", "water for", 1) + " per cycle"
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
    rule = lambda r: f"{names.get(r.item, r.item)} {r.role} {OPS[r.op]}{'' if r.value is None else f' {r.value:g} {r.unit or ""}'.rstrip()} for {r.persist_s:g} s"  # noqa: E731
    return {"text": f"{block_name}: {what}, {when}; {guard}.", "next": nxt,
            "start_conditions": [rule(r) for r in f.permissives], "stop_conditions": [rule(r) for r in f.guards],
            # 0.7.3: the block's own conditions, applied with the program's
            "block_start_conditions": [rule(r) for r in (block.safety.permissives if block else ())],
            "block_stop_conditions": [rule(r) for r in (block.safety.guards if block else ())]}


FIELD_WORDS = {"block_id": "the block", "endpoint.minutes": "minutes per cycle", "endpoint.litres": "litres per cycle",
               "schedule.days": "the days", "schedule.windows": "the watering windows",
               "schedule.rest_minutes": "the wait after each cycle"}


def form_problems(form: dict) -> list[dict]:
    """Everything the operator still has to fill in or correct, as [{field, message}], before the form is parsed
    (0.7.3, owner correction): no validator text, no regular expressions, every problem at once, conditions by row."""
    out: list[dict] = []
    add = lambda field, msg: out.append({"field": field, "message": msg})  # noqa: E731
    f = form if isinstance(form, dict) else {}
    if not f.get("block_id"):
        add("block_id", "Choose a validated block (blocks are made and validated on Blocks)")
    e = f.get("endpoint") or {}
    if e.get("kind") == "TIME" and not e.get("minutes"):
        add("endpoint.minutes", "Enter the minutes per cycle")
    if e.get("kind") == "VOLUME" and not e.get("litres"):
        add("endpoint.litres", "Enter the litres per cycle")
    if e.get("kind") not in ("TIME", "VOLUME", "MANUAL"):
        add("endpoint.kind", "Choose how much: water for a time, or deliver litres")
    s_ = f.get("schedule") or {}
    if s_.get("kind") == "WEEKLY":
        if not s_.get("days"):
            add("schedule.days", "Choose at least one day")
        v1 = f.get("form_version") == 1 or "form_version" not in f and (e.get("max_minutes") is not None
                                                                         or s_.get("times") or s_.get("repeat"))
        if not v1:
            if not s_.get("windows"):
                add("schedule.windows", "Add a watering window (start and finish)")
            for i, w in enumerate(s_.get("windows") or []):
                if not (w or {}).get("start") or not (w or {}).get("finish"):
                    add(f"schedule.windows.{i}", f"Window {i + 1}: give its start and its finish")
            if not s_.get("rest_minutes"):
                add("schedule.rest_minutes", "Enter the wait after each completed cycle (minutes)")
    for key, word in (("permissives", "Start condition"), ("guards", "Stop condition")):
        for i, r in enumerate(f.get(key) or []):
            r = r or {}
            where = f"{key}.{i}"
            if not r.get("item") or not r.get("role"):
                add(where, f"{word} {i + 1}: choose a reading (or remove the row)")
                continue
            if r.get("op") in ("ABOVE", "BELOW") and r.get("value") in (None, ""):
                add(where, f"{word} {i + 1}: enter the value to compare with")
    return out


def pydantic_problems(e) -> list[dict]:
    """A validation error as [{field, message}] in words: never the validator's internal text."""
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "").removeprefix("Value error, ")
        if err.get("type") in ("string_pattern_mismatch", "string_type", "missing", "int_parsing", "float_parsing"):
            msg = f"{FIELD_WORDS.get(loc, loc.replace('.', ' ') or 'a value')}: missing or not valid"
        out.append({"field": loc or None, "message": msg[:1].upper() + msg[1:]})
    return out
