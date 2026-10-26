"""A block: one purpose (water a sector, return drainage, fill a tank, prepare nutrients) over existing equipment.

* Members are graph nodes, so one pump shared by two blocks is one item with one history and one owner at a time.
  Membership commands nothing and reserves nothing: an operation claims exactly the actuators its plan uses.
* The block names where its water comes from (`supply`: a source or a tank), where it goes (`destination`: a sector
  or a tank) and, when it can measure what it delivers, the inline flow `meter` on that path.
* Its operating plan is data the operator can read and edit: the startup steps, the normal stop and the fault stop.
  A template proposes a plan as an example; nothing is inferred from the drawing or the order of icons (a suction
  valve may legitimately come before a pump, a shared drive has its own sequence). Every step names a member.

`check` says what is missing before a block validates: the members exist, an explicit path joins supply and
destination through members only, every actuator on that path is operated by the plan, a meter really sits on the
path, and each started item is stopped again by both stop plans.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..topology.definitions import ID, Topology
from ..topology.validation import paths_from

Template = Literal["IRRIGATION_SECTOR", "DRAINAGE_RETURN", "TANK_FILLING", "NUTRIENT_PREPARATION", "GENERIC"]
TEMPLATES = {
    "IRRIGATION_SECTOR": "Irrigation sector: water from a tank to one sector",
    "DRAINAGE_RETURN": "Drainage return: drained water back to a tank",
    "TANK_FILLING": "Tank filling: water from a source into a tank",
    "NUTRIENT_PREPARATION": "Nutrient preparation (simulation only until its chemistry is engineered)",
    "GENERIC": "Generic process: a supply, a destination and a plan you write",
}
ACTUATING = ("pump", "valve")


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Step(_B):
    do: Literal["OPEN", "CLOSE", "START", "STOP", "WAIT"]
    node: str | None = Field(default=None, pattern=ID)
    seconds: float | None = Field(default=None, gt=0, le=3600)     # WAIT: how long
    verify: bool = False                     # START/OPEN: wait for the item's own feedback before going on
    timeout_s: float = Field(default=30, gt=0, le=600)

    @model_validator(mode="after")
    def _check(self):
        if self.do == "WAIT":
            if self.seconds is None or self.node is not None:
                raise ValueError("a wait has a duration and no equipment")
        elif self.node is None:
            raise ValueError(f"{self.do.lower()} names the equipment it acts on")
        return self


class Plan(_B):
    startup: tuple[Step, ...] = Field(default=(), max_length=40)
    normal_stop: tuple[Step, ...] = Field(default=(), max_length=40)
    fault_stop: tuple[Step, ...] = Field(default=(), max_length=40)     # empty: every started item OFF, last first
    provenance: Literal["TEMPLATE_EXAMPLE", "OWNER_CONFIGURED", "REVIEWED"] = "TEMPLATE_EXAMPLE"


class BlockBody(_B):
    template: Template = "GENERIC"
    description: str = Field(default="", max_length=500)
    members: tuple[str, ...] = Field(default=(), max_length=200)
    supply: str | None = Field(default=None, pattern=ID)
    destination: str | None = Field(default=None, pattern=ID)
    meter: str | None = Field(default=None, pattern=ID)
    plan: Plan = Plan()

    @model_validator(mode="after")
    def _check(self):
        dup = sorted({m for m in self.members if self.members.count(m) > 1})
        if dup:
            raise ValueError(f"a member appears twice: {', '.join(dup)} (share an item, never duplicate it)")
        return self


def digest(body: BlockBody) -> str:
    return hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse(doc: dict) -> BlockBody:
    return BlockBody.model_validate(doc)


def _issue(out, rule, obj, message, fix, severity="ERROR", field=None):
    out.append({"id": f"{rule}:{obj}", "severity": severity, "object": obj, "field": field, "rule": rule,
                "message": message, "fix": fix})


def process_path(b: BlockBody, t: Topology) -> dict | None:
    """The explicit path from supply to destination through members only (the first, in a stable order)."""
    if not b.supply or not b.destination:
        return None
    members = set(b.members)
    ok = [p for p in paths_from(t, b.supply) if p["to"] == b.destination and set(p["nodes"]) <= members]
    return sorted(ok, key=lambda p: (len(p["nodes"]), p["edges"]))[0] if ok else None


def check(b: BlockBody, t: Topology, names: dict[str, str] | None = None) -> list[dict]:
    names = names or {}
    nm = lambda n: names.get(n, n)                    # noqa: E731
    out: list[dict] = []
    nodes = {n.id: n for n in t.nodes}
    for m in b.members:
        if m not in nodes:
            _issue(out, "MEMBER_MISSING", m, f"{nm(m)} is not in the installation graph", "add it in the editor or remove it",
                   field="members")
    for fld, want in (("supply", ("source", "tank")), ("destination", ("sector", "tank"))):
        v = getattr(b, fld)
        if v is None:
            _issue(out, f"{fld.upper()}_MISSING", fld, f"choose the block's {fld}", f"pick a {' or '.join(want)}", field=fld)
        elif v not in nodes or nodes[v].kind not in want:
            _issue(out, f"{fld.upper()}_KIND", v, f"{nm(v)} cannot be the {fld}: it is not a {' or '.join(want)}",
                   f"pick a {' or '.join(want)}", field=fld)
        elif v not in b.members:
            _issue(out, f"{fld.upper()}_MEMBER", v, f"{nm(v)} is the {fld} but not a member", "add it to the block", field=fld)
    path = process_path(b, t) if not out else None
    if not out and path is None:
        _issue(out, "PATH_MISSING", b.destination or "block",
               f"no connected path from {nm(b.supply)} to {nm(b.destination)} through the block's members",
               "connect the pipes explicitly, or add the equipment on the way to the block")
    if b.meter is not None:
        if b.meter not in nodes or nodes[b.meter].kind != "meter":
            _issue(out, "METER_KIND", b.meter, f"{nm(b.meter)} is not an inline flow meter", "choose a flow meter",
                   field="meter")
        elif path is not None and b.meter not in path["nodes"]:
            _issue(out, "METER_OFF_PATH", b.meter, f"{nm(b.meter)} is not on the path from {nm(b.supply)} to "
                   f"{nm(b.destination)}: it cannot measure this block's water", "choose the meter on that path", field="meter")
    for phase in ("startup", "normal_stop", "fault_stop"):
        for i, s in enumerate(getattr(b.plan, phase)):
            if s.node is not None and s.node not in b.members:
                _issue(out, "STEP_MEMBER", f"{phase}[{i}]", f"{phase.replace('_', ' ')} step {i + 1}: {nm(s.node)} is not a member",
                       "add it to the block or change the step", field=f"plan.{phase}")
            elif s.node is not None and s.node in nodes:
                k = nodes[s.node].kind
                if s.do in ("OPEN", "CLOSE") and k != "valve" or s.do in ("START", "STOP") and k != "pump":
                    _issue(out, "STEP_KIND", f"{phase}[{i}]",
                           f"{phase.replace('_', ' ')} step {i + 1}: {s.do.lower()} does not apply to {nm(s.node)} ({k})",
                           "valves open and close, pumps start and stop", field=f"plan.{phase}")
    if path is not None:
        on_path = [n for n in path["nodes"] if nodes[n].kind in ACTUATING]
        started = [s.node for s in b.plan.startup if s.do in ("OPEN", "START")]
        for n in on_path:
            if n not in started:
                _issue(out, "PATH_NOT_OPERATED", n, f"{nm(n)} is on the path but the startup never "
                       f"{'opens' if nodes[n].kind == 'valve' else 'starts'} it",
                       "add the step, or confirm another route", severity="WARNING")
        for phase in ("normal_stop", "fault_stop"):
            stops = [s.node for s in getattr(b.plan, phase) if s.do in ("CLOSE", "STOP")]
            if phase == "fault_stop" and not getattr(b.plan, phase):
                continue                              # the engine's default: every started item OFF, last first
            for n in started:
                if n not in stops:
                    _issue(out, "STOP_INCOMPLETE", f"{phase}:{n}", f"the {phase.replace('_', ' ')} leaves {nm(n)} "
                           f"{'open' if nodes[n].kind == 'valve' else 'running'}", "add a step that stops it",
                           field=f"plan.{phase}")
        if not started:
            _issue(out, "PLAN_EMPTY", "plan", "the startup does nothing", "add the steps that open and start the path",
                   field="plan.startup")
    return out


def template_plan(template: str, path_nodes: list[str], kinds: dict[str, str], travel_s: float | None) -> Plan:
    """An example plan for the path, to be read and edited (never applied silently): open the valves on the path,
    wait their configured travel, start the pumps (verified), and stop the reverse way. A suction valve before a
    pump is opened like any other valve on the path; a different order is the owner's edit."""
    valves = [n for n in path_nodes if kinds.get(n) == "valve"]
    pumps = [n for n in path_nodes if kinds.get(n) == "pump"]
    start = [Step(do="OPEN", node=v, verify=False) for v in valves]
    if valves and pumps and travel_s:
        start.append(Step(do="WAIT", seconds=travel_s))
    start += [Step(do="START", node=p, verify=True) for p in pumps]
    stop = [Step(do="STOP", node=p) for p in reversed(pumps)]
    if valves and pumps:
        stop.append(Step(do="WAIT", seconds=2))
    stop += [Step(do="CLOSE", node=v) for v in reversed(valves)]
    return Plan(startup=tuple(start), normal_stop=tuple(stop), fault_stop=(), provenance="TEMPLATE_EXAMPLE")
