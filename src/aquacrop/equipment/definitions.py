"""Equipment type and item bodies, the built-in templates and icons, and their validation.

Everything here is data. An icon is cosmetic: nothing reads it to decide what a channel does. The
category is a label for grouping on the dashboard, not a behaviour: behaviour comes only from the roles,
the permitted operations and the simulation behaviour, each of which is validated.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_serializer, model_validator

from ..config.catalog import CATALOG, AIMode, ModelId
from ..domain.model import PointId, PointKind

ID_RE = r"^[a-z0-9][a-z0-9_\-]{0,47}$"
_ID = re.compile(ID_RE)

# Built-in icon names. The UI draws each one from an inline SVG it ships; nothing is uploaded.
ICONS = ("pump", "valve", "tank", "level", "flow", "pressure", "temperature", "button", "lamp", "fan",
         "heater", "motor", "sensor", "switch", "drop", "gauge", "plug", "generic")


class Direction(str, enum.Enum):
    OUTPUT = "OUTPUT"
    INPUT = "INPUT"


class SignalKind(str, enum.Enum):
    DIGITAL = "DIGITAL"
    ANALOG = "ANALOG"


class Purpose(str, enum.Enum):
    COMMAND = "COMMAND"                      # output: start / open / energise
    RUNNING = "RUNNING"                      # input: running / energised feedback
    FAULT = "FAULT"                          # input: fault / trip (1 = fault unless inverted)
    OPEN = "OPEN"                            # input: position open
    CLOSED = "CLOSED"                        # input: position closed
    STATE = "STATE"                          # input: a generic contact (button, float switch)
    MEASUREMENT = "MEASUREMENT"              # analog input: a measured quantity


class Electrical(str, enum.Enum):
    RELAY_CONTACT = "RELAY_CONTACT"          # Relay (D) dry contact
    OPEN_DRAIN_SINK = "OPEN_DRAIN_SINK"      # IO 8CH 500 mA transistor sink
    DRY_CONTACT_INPUT = "DRY_CONTACT_INPUT"  # any module DI
    CURRENT_4_20 = "CURRENT_4_20"
    CURRENT_0_20 = "CURRENT_0_20"
    VOLTAGE_0_5 = "VOLTAGE_0_5"
    VOLTAGE_1_5 = "VOLTAGE_1_5"


class Operation(str, enum.Enum):
    RUN_FOR = "RUN_FOR"                      # bounded duration ON
    SCHEDULE = "SCHEDULE"                    # weekly windows
    DELIVER_VOLUME = "DELIVER_VOLUME"        # stop at a measured volume
    KEEP_BETWEEN = "KEEP_BETWEEN"            # switch to keep a measurement inside limits
    TRIGGERED = "TRIGGERED"                  # start on a sensor condition
    SEQUENCE_STEP = "SEQUENCE_STEP"          # may be a step of an ordered sequence
    MEASURE = "MEASURE"                      # provides a measurement to rules


class SimBehavior(str, enum.Enum):
    NOT_MODELLED = "NOT_MODELLED"            # the twin shows NOT MODELLED, never a plausible value
    FOLLOW_COMMAND = "FOLLOW_COMMAND"        # feedback inputs follow the command after a delay
    VALVE_TRAVEL = "VALVE_TRAVEL"            # open/closed inputs change after a travel time
    PUMP_FLOW = "PUMP_FLOW"                  # running follows command; a named flow sensor reads a demo rate
    TANK_LEVEL = "TANK_LEVEL"                # level integrates named inflows/outflows (demo constants)
    MANUAL_INPUT = "MANUAL_INPUT"            # the user sets it from the twin (button, float switch)
    MANUAL_ANALOG = "MANUAL_ANALOG"          # the user sets the reading from the twin


# Which module channel kinds and electrical interfaces each signal accepts.
POINT_KIND = {(Direction.OUTPUT, SignalKind.DIGITAL): PointKind.DO,
              (Direction.INPUT, SignalKind.DIGITAL): PointKind.DI,
              (Direction.INPUT, SignalKind.ANALOG): PointKind.AI}
AI_ELECTRICAL = {AIMode.CURRENT_4_20: Electrical.CURRENT_4_20, AIMode.CURRENT_0_20: Electrical.CURRENT_0_20,
                 AIMode.VOLTAGE_FULL: Electrical.VOLTAGE_0_5, AIMode.VOLTAGE_OFFSET: Electrical.VOLTAGE_1_5}
OUTPUT_PURPOSES = {Purpose.COMMAND}
INPUT_PURPOSES = {Purpose.RUNNING, Purpose.FAULT, Purpose.OPEN, Purpose.CLOSED, Purpose.STATE, Purpose.MEASUREMENT}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PropertyDef(_M):
    key: str = Field(pattern=ID_RE)
    label: str = Field(min_length=1, max_length=60)
    kind: Literal["NUMBER", "INTEGER", "TEXT", "BOOLEAN", "CHOICE"]
    unit: str | None = Field(default=None, max_length=16)
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    default: float | int | str | bool | None = None
    required: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"property {self.key}: min is above max")
        if self.kind == "CHOICE" and not self.choices:
            raise ValueError(f"property {self.key}: a CHOICE needs choices")
        if self.default is not None:
            problem = check_property(self, self.default)
            if problem:
                raise ValueError(f"property {self.key}: default {problem}")
        return self


class RoleDef(_M):
    role: str = Field(pattern=ID_RE)
    label: str = Field(min_length=1, max_length=60)
    direction: Direction
    signal: SignalKind
    purpose: Purpose
    required: bool = True
    electrical: tuple[Electrical, ...] = ()      # empty: any interface that fits the signal
    inverted: bool = False                       # 0 means active (normally-closed contact)
    feedback_timeout_s: float | None = Field(default=None, gt=0, le=600)
    unit: str | None = Field(default=None, max_length=16)   # MEASUREMENT: engineering unit

    @model_validator(mode="after")
    def _check(self):
        if (self.direction, self.signal) not in POINT_KIND:
            raise ValueError(f"role {self.role}: an analog output is not supported by these modules")
        if self.direction == Direction.OUTPUT and self.purpose not in OUTPUT_PURPOSES:
            raise ValueError(f"role {self.role}: an output role must be COMMAND")
        if self.direction == Direction.INPUT and self.purpose not in INPUT_PURPOSES:
            raise ValueError(f"role {self.role}: an input role cannot be COMMAND")
        if (self.purpose == Purpose.MEASUREMENT) != (self.signal == SignalKind.ANALOG):
            raise ValueError(f"role {self.role}: MEASUREMENT is the purpose of analog inputs only")
        allowed = _ELECTRICAL_FOR[(self.direction, self.signal)]
        bad = [e.value for e in self.electrical if e not in allowed]
        if bad:
            raise ValueError(f"role {self.role}: {', '.join(bad)} does not fit a {self.direction.value.lower()} "
                             f"{self.signal.value.lower()} signal")
        return self


_ELECTRICAL_FOR = {
    (Direction.OUTPUT, SignalKind.DIGITAL): {Electrical.RELAY_CONTACT, Electrical.OPEN_DRAIN_SINK},
    (Direction.INPUT, SignalKind.DIGITAL): {Electrical.DRY_CONTACT_INPUT},
    (Direction.INPUT, SignalKind.ANALOG): {Electrical.CURRENT_4_20, Electrical.CURRENT_0_20, Electrical.VOLTAGE_0_5,
                                           Electrical.VOLTAGE_1_5},
}


class Limits(_M):
    max_runtime_s: float | None = Field(default=None, gt=0, le=86400)
    min_off_s: float | None = Field(default=None, ge=0, le=86400)
    max_volume_L: float | None = Field(default=None, gt=0, le=1e6)
    max_starts_per_hour: int | None = Field(default=None, ge=1, le=3600)


# What a type is in the hydraulic graph (0.7.0, ADR-0024). It decides the ports a symbol of the type has; the
# category and the icon never do. None: not part of the water graph (a button, a test lamp).
HYDRAULIC_KINDS = ("source", "tank", "pump", "valve", "meter", "sensor", "sector")
PASSIVE_KINDS = ("source", "tank", "sector")       # no channel needed: they have no I/O of their own


class SimSpec(_M):
    behavior: SimBehavior = SimBehavior.NOT_MODELLED
    delay_s: float = Field(default=1.0, ge=0, le=600)            # FOLLOW_COMMAND / VALVE_TRAVEL
    demo_flow_L_min: float | None = Field(default=None, gt=0, le=10000)   # PUMP_FLOW (labelled DEMO)


class TypeBody(_M):
    category: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=500)
    default_icon: str = "generic"
    properties: tuple[PropertyDef, ...] = ()
    roles: tuple[RoleDef, ...] = ()
    operations: tuple[Operation, ...] = ()
    limits: Limits = Limits()
    simulation: SimSpec = SimSpec()
    hydraulic: Literal["source", "tank", "pump", "valve", "meter", "sensor", "sector"] | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.default_icon not in ICONS:
            raise ValueError(f"icon {self.default_icon!r} is not a built-in icon")
        if not self.roles and self.hydraulic not in PASSIVE_KINDS:
            raise ValueError("a type needs at least one channel role, unless it is a passive water part "
                             "(a tank, a water source or a sector)")
        for seq, what in ((self.properties, "property key"), (self.roles, "role")):
            names = [getattr(x, "key", None) or getattr(x, "role") for x in seq]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                raise ValueError(f"duplicate {what}: {', '.join(dup)}")
        outputs = [r for r in self.roles if r.direction == Direction.OUTPUT]
        actuating = {Operation.RUN_FOR, Operation.SCHEDULE, Operation.DELIVER_VOLUME, Operation.KEEP_BETWEEN,
                     Operation.TRIGGERED, Operation.SEQUENCE_STEP}
        if set(self.operations) & actuating and not outputs:
            raise ValueError("operations that switch equipment need a COMMAND output role")
        if outputs and set(self.operations) & actuating and self.limits.max_runtime_s is None:
            raise ValueError("equipment that can be switched needs a maximum runtime")
        if Operation.MEASURE in self.operations and not any(r.purpose == Purpose.MEASUREMENT for r in self.roles):
            raise ValueError("MEASURE needs an analog MEASUREMENT role")
        return self


class Scaling(_M):
    """Linear engineering scaling of one analog role, from the module's own measurement (mA or V)."""
    raw_min: float
    raw_max: float
    raw_unit: Literal["mA", "V"]
    eng_min: float
    eng_max: float
    unit: str = Field(min_length=1, max_length=16)
    provenance: Literal["DATASHEET", "CALIBRATED", "DEMO", "PROPOSED"]
    calibration_ref: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check(self):
        if self.raw_max <= self.raw_min:
            raise ValueError("raw_max must be above raw_min")
        if self.eng_max == self.eng_min:
            raise ValueError("eng_min and eng_max must differ")
        if self.provenance == "CALIBRATED" and not self.calibration_ref:
            raise ValueError("a CALIBRATED scaling names its calibration record")
        return self

    def apply(self, raw: float) -> float:
        return self.eng_min + (raw - self.raw_min) * (self.eng_max - self.eng_min) / (self.raw_max - self.raw_min)


class AlarmLimit(_M):
    role: str = Field(pattern=ID_RE)
    low: float | None = None
    high: float | None = None
    deadband: float = Field(default=0.0, ge=0)
    delay_s: float = Field(default=0.0, ge=0, le=3600)      # 0.7.0: how long the limit is passed before it raises

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        d = handler(self)
        if d.get("delay_s") == 0.0:
            d.pop("delay_s")                      # bodies written before 0.7.0 keep their exact form
        return d

    @model_validator(mode="after")
    def _check(self):
        if self.low is None and self.high is None:
            raise ValueError(f"alarm on {self.role}: give a low or a high limit")
        if self.low is not None and self.high is not None and self.low >= self.high:
            raise ValueError(f"alarm on {self.role}: low must be below high")
        return self


class ItemBody(_M):
    description: str = Field(default="", max_length=500)
    properties: dict[str, float | int | str | bool] = {}
    scaling: dict[str, Scaling] = {}
    alarms: tuple[AlarmLimit, ...] = ()
    stale_after_s: float = Field(default=5.0, gt=0, le=3600)
    simulation: SimSpec | None = None             # None: the type's behaviour
    sim_links: dict[str, str] = {}                # PUMP_FLOW: {"flow_item": id}; TANK_LEVEL: {"in_<n>"/"out_<n>": id}


def check_property(p: PropertyDef, v) -> str | None:
    if p.kind in ("NUMBER", "INTEGER"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "must be a number"
        if p.kind == "INTEGER" and float(v) != int(v):
            return "must be a whole number"
        if p.min is not None and v < p.min:
            return f"is below the minimum {p.min:g}"
        if p.max is not None and v > p.max:
            return f"is above the maximum {p.max:g}"
    elif p.kind == "BOOLEAN" and not isinstance(v, bool):
        return "must be true or false"
    elif p.kind == "TEXT" and (not isinstance(v, str) or len(v) > 200):
        return "must be text of at most 200 characters"
    elif p.kind == "CHOICE" and v not in p.choices:
        return f"must be one of {', '.join(p.choices)}"
    return None


def digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_type(body: dict) -> TypeBody:
    return TypeBody.model_validate(body)


def item_issues(t: TypeBody, item: ItemBody, icon: str) -> list[str]:
    """What is wrong with an item against its type version (empty: valid). Bindings are checked apart."""
    out = []
    if icon not in ICONS:
        out.append(f"icon {icon!r} is not a built-in icon")
    defs = {p.key: p for p in t.properties}
    for k in item.properties:
        if k not in defs:
            out.append(f"property {k} is not defined by the type")
    for k, p in defs.items():
        v = item.properties.get(k, p.default)
        if v is None:
            if p.required:
                out.append(f"property {p.label} is required")
            continue
        problem = check_property(p, v)
        if problem:
            out.append(f"property {p.label} {problem}")
    roles = {r.role: r for r in t.roles}
    for role, sc in item.scaling.items():
        r = roles.get(role)
        if r is None or r.purpose != Purpose.MEASUREMENT:
            out.append(f"scaling for {role}: not an analog measurement role of the type")
        elif r.unit and sc.unit != r.unit:
            out.append(f"scaling for {role}: unit {sc.unit} differs from the type's {r.unit}")
    for a in item.alarms:
        if a.role not in roles or roles[a.role].purpose != Purpose.MEASUREMENT:
            out.append(f"alarm on {a.role}: not an analog measurement role")
        elif a.role not in item.scaling:
            out.append(f"alarm on {a.role}: needs the role's scaling (limits are in engineering units)")
    return out


def channel_issues(cfg, role: RoleDef, point: str, scaling: Scaling | None) -> list[str]:
    """Whether a module channel can carry a role: kind, direction and electrical interface.
    The module's model comes from the configuration (the installer's SKU statement at setup)."""
    try:
        pid = PointId.parse(point)
        dev = cfg.device(pid.device_uid)
    except (KeyError, ValueError):
        return [f"{point} is not a channel of this configuration"]
    if str(pid) not in {str(x) for x in cfg.all_points()}:
        return [f"{point} is not a channel of this configuration"]
    want = POINT_KIND[(role.direction, role.signal)]
    if pid.kind != want:
        return [f"{role.label} needs a {want.value} channel; {point} is a {pid.kind.value}"]
    spec = CATALOG[ModelId(dev.model)]
    out = []
    if pid.kind == PointKind.DO:
        have = Electrical(spec.output_type)
        if role.electrical and have not in role.electrical:
            out.append(f"{role.label} accepts {', '.join(e.value for e in role.electrical)}; {point} is "
                       f"{have.value} ({spec.vendor_name})")
    elif pid.kind == PointKind.AI:
        mode = _ai_mode(dev, pid.channel)
        have = AI_ELECTRICAL.get(mode)
        if have is None:
            out.append(f"{point} is in mode {mode.value}: its range is not verified, so it cannot carry a measurement")
        elif role.electrical and have not in role.electrical:
            out.append(f"{role.label} accepts {', '.join(e.value for e in role.electrical)}; {point} is set to "
                       f"{have.value}")
        if scaling is not None and have is not None:
            unit = "mA" if have in (Electrical.CURRENT_4_20, Electrical.CURRENT_0_20) else "V"
            if scaling.raw_unit != unit:
                out.append(f"the scaling of {role.label} is in {scaling.raw_unit}; {point} measures {unit}")
    return out


def _ai_mode(dev, channel: int) -> AIMode:
    ai = getattr(dev, "ai", None) or {}
    ch = ai.get(channel) or ai.get(str(channel))
    mode = getattr(ch, "mode", None) if ch is not None else None
    try:
        return AIMode(mode.value if isinstance(mode, enum.Enum) else str(mode))
    except ValueError:
        return AIMode.UNKNOWN


def errors_text(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "").removeprefix("Value error, ")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


# --------------------------------------------------------------------------------------------
# Editable templates. Seeded as DRAFT types the user reviews and activates; nothing here is a field
# value of the installation. Demo simulation constants are labelled DEMO in the twin.
# --------------------------------------------------------------------------------------------
def _role(role, label, direction, signal, purpose, **kw) -> dict:
    return {"role": role, "label": label, "direction": direction, "signal": signal, "purpose": purpose} | kw


TEMPLATES: dict[str, dict] = {
    "pump": {"name": "Pump", "body": {
        "category": "Pumps", "default_icon": "pump",
        "description": "A pump started by one output, with optional running and fault feedback.",
        "properties": [{"key": "rated_flow_l_min", "label": "Rated flow", "kind": "NUMBER", "unit": "L/min",
                        "min": 0, "max": 10000}],
        "roles": [_role("start", "Start", "OUTPUT", "DIGITAL", "COMMAND"),
                  _role("running", "Running", "INPUT", "DIGITAL", "RUNNING", required=False, feedback_timeout_s=5),
                  _role("fault", "Fault", "INPUT", "DIGITAL", "FAULT", required=False)],
        "operations": ["RUN_FOR", "SCHEDULE", "DELIVER_VOLUME", "KEEP_BETWEEN", "TRIGGERED", "SEQUENCE_STEP"],
        "limits": {"max_runtime_s": 3600, "min_off_s": 10},
        "simulation": {"behavior": "PUMP_FLOW", "delay_s": 1.0, "demo_flow_L_min": 20}, "hydraulic": "pump"}},
    "valve": {"name": "Valve", "body": {
        "category": "Valves", "default_icon": "valve",
        "description": "A valve opened by one output, with optional open/closed position switches.",
        "roles": [_role("open", "Open", "OUTPUT", "DIGITAL", "COMMAND"),
                  _role("is_open", "Open switch", "INPUT", "DIGITAL", "OPEN", required=False, feedback_timeout_s=10),
                  _role("is_closed", "Closed switch", "INPUT", "DIGITAL", "CLOSED", required=False,
                        feedback_timeout_s=10)],
        "operations": ["RUN_FOR", "SCHEDULE", "SEQUENCE_STEP"],
        "limits": {"max_runtime_s": 7200},
        "simulation": {"behavior": "VALVE_TRAVEL", "delay_s": 3.0}, "hydraulic": "valve"}},
    "level": {"name": "Level sensor", "body": {
        "category": "Level sensors", "default_icon": "level",
        "description": "A 4-20 mA level transmitter.",
        "roles": [_role("level", "Level", "INPUT", "ANALOG", "MEASUREMENT", electrical=["CURRENT_4_20"], unit="cm")],
        "operations": ["MEASURE"], "simulation": {"behavior": "MANUAL_ANALOG"}, "hydraulic": "sensor"}},
    "flow": {"name": "Flow meter (4-20 mA flow rate)", "body": {
        "category": "Flow meters", "default_icon": "flow",
        "description": "A flow-rate transmitter on an analog input. Volume is integrated from fresh readings.",
        "roles": [_role("flow", "Flow rate", "INPUT", "ANALOG", "MEASUREMENT", electrical=["CURRENT_4_20"],
                        unit="L/min")],
        "operations": ["MEASURE"], "simulation": {"behavior": "MANUAL_ANALOG"}, "hydraulic": "meter"}},
    # 0.6.2: the installation's other transmitters (owner request: the starter creates every part)
    "pressure": {"name": "Pressure transmitter", "body": {
        "category": "Pressure sensors", "default_icon": "pressure",
        "description": "A 4-20 mA pressure transmitter (a sector's line pressure).",
        "roles": [_role("pressure", "Pressure", "INPUT", "ANALOG", "MEASUREMENT", electrical=["CURRENT_4_20"],
                        unit="bar")],
        "operations": ["MEASURE"], "simulation": {"behavior": "MANUAL_ANALOG"}, "hydraulic": "sensor"}},
    "ph": {"name": "pH probe", "body": {
        "category": "Water quality", "default_icon": "drop",
        "description": "A pH transmitter on a 4-20 mA input.",
        "roles": [_role("ph", "pH", "INPUT", "ANALOG", "MEASUREMENT", electrical=["CURRENT_4_20"], unit="pH")],
        "operations": ["MEASURE"], "simulation": {"behavior": "MANUAL_ANALOG"}, "hydraulic": "sensor"}},
    "ec": {"name": "EC probe", "body": {
        "category": "Water quality", "default_icon": "gauge",
        "description": "An electrical conductivity transmitter on a 4-20 mA input.",
        "roles": [_role("ec", "EC", "INPUT", "ANALOG", "MEASUREMENT", electrical=["CURRENT_4_20"], unit="mS/cm")],
        "operations": ["MEASURE"], "simulation": {"behavior": "MANUAL_ANALOG"}, "hydraulic": "sensor"}},
    "temperature": {"name": "Temperature transmitter", "body": {
        "category": "Water quality", "default_icon": "temperature",
        "description": "A water temperature transmitter on a 4-20 mA input.",
        "roles": [_role("temperature", "Temperature", "INPUT", "ANALOG", "MEASUREMENT", electrical=["CURRENT_4_20"],
                        unit="°C")],
        "operations": ["MEASURE"], "simulation": {"behavior": "MANUAL_ANALOG"}, "hydraulic": "sensor"}},
    "button": {"name": "Button", "body": {
        "category": "Buttons", "default_icon": "button", "description": "A push button or switch on a digital input.",
        "roles": [_role("pressed", "Pressed", "INPUT", "DIGITAL", "STATE")],
        "operations": [], "simulation": {"behavior": "MANUAL_INPUT"}}},
    "test_lamp": {"name": "Test LED", "body": {
        "category": "Test loads", "default_icon": "lamp",
        "description": "A bench LED on an IO 8CH transistor output, optionally read back by a digital input.",
        "roles": [_role("on", "On", "OUTPUT", "DIGITAL", "COMMAND", electrical=["OPEN_DRAIN_SINK"]),
                  _role("lit", "Lit (feedback)", "INPUT", "DIGITAL", "RUNNING", required=False, feedback_timeout_s=3)],
        "operations": ["RUN_FOR", "SCHEDULE", "TRIGGERED", "SEQUENCE_STEP"],
        "limits": {"max_runtime_s": 1800},
        "simulation": {"behavior": "FOLLOW_COMMAND", "delay_s": 0.5}}},
    # 0.7.0 passive water parts: no channel of their own. Their numbers are the item's properties; unknown values
    # stay unset (the twin then shows NOT MODELLED for what needs them), and a demo value says so in its provenance.
    "tank": {"name": "Tank", "body": {
        "category": "Tanks", "default_icon": "tank", "hydraulic": "tank",
        "description": "A tank. Its level is measured by a separate level sensor associated with it.",
        "properties": [{"key": "capacity_l", "label": "Capacity", "kind": "NUMBER", "unit": "L", "min": 0.1,
                        "max": 1e7},
                       {"key": "sim_initial_l", "label": "Water at the start of a simulation", "kind": "NUMBER",
                        "unit": "L", "min": 0, "max": 1e7},
                       {"key": "level_full", "label": "Level reading when full", "kind": "NUMBER", "min": 0,
                        "max": 1e6},
                       {"key": "provenance", "label": "Where the numbers come from", "kind": "CHOICE",
                        "choices": ["MEASURED", "DATASHEET", "OWNER_CONFIGURED", "DEMO", "UNKNOWN"],
                        "default": "UNKNOWN"}]}},
    "water_source": {"name": "Water source", "body": {
        "category": "Sources", "default_icon": "drop", "hydraulic": "source",
        "description": "Where water comes from (mains, a well). It supplies water only along explicit pipes.",
        "properties": [{"key": "supply_l_min", "label": "Supply rate in a simulation", "kind": "NUMBER",
                        "unit": "L/min", "min": 0.01, "max": 1e5},
                       {"key": "provenance", "label": "Where the numbers come from", "kind": "CHOICE",
                        "choices": ["MEASURED", "DATASHEET", "OWNER_CONFIGURED", "DEMO", "UNKNOWN"],
                        "default": "UNKNOWN"}]}},
    "sector": {"name": "Irrigation sector", "body": {
        "category": "Sectors", "default_icon": "drop", "hydraulic": "sector",
        "description": "Where water is delivered. A drain share, if configured, leaves by its drain port.",
        "properties": [{"key": "drain_fraction", "label": "Share that drains back in a simulation", "kind": "NUMBER",
                        "min": 0, "max": 1},
                       {"key": "drain_delay_s", "label": "Drain delay in a simulation", "kind": "NUMBER", "unit": "s",
                        "min": 0, "max": 86400},
                       {"key": "provenance", "label": "Where the numbers come from", "kind": "CHOICE",
                        "choices": ["MEASURED", "DATASHEET", "OWNER_CONFIGURED", "DEMO", "UNKNOWN"],
                        "default": "UNKNOWN"}]}},
}
