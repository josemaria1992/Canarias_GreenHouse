"""The equipment twin (0.5.0): virtual modules with the live configuration's devices and channels, and a
simulated behaviour for each user-defined equipment item.

What is simulated, and only that:
* every DO channel is a virtual coil whose readback is the coil (unless a fault says otherwise);
* each item's inputs follow its type's `simulation.behavior` (DEMO dynamics, labelled as such):
  FOLLOW_COMMAND (feedback follows the command after `delay_s`), PUMP_FLOW (feedback, and a linked flow meter
  reads `demo_flow_L_min` while running), VALVE_TRAVEL (open/closed switches after the travel time),
  TANK_LEVEL (a level integrates linked flows; demo constants), MANUAL_INPUT / MANUAL_ANALOG (set by the user);
* NOT_MODELLED: the item's inputs have no value at all, so the dashboard shows NOT MODELLED and every rule that
  needs them waits or fails, instead of reading a plausible-looking number.
Nothing infers hydraulics from an icon or a category; links between items are explicit (`sim_links`).

The twin's clock is simulated: `utc()` is the scenario start plus simulated seconds, so a week of schedules runs
in minutes at speed. Its commands go through a CommandService of its own, with a virtual backend: the twin
process has no serial port and no route to the live command service.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config.catalog import CATALOG, Capability
from ..domain.model import (AcquisitionResult, DeviceHealth, Origin, Outcome, OutputCommand, PointId, PointKind,
                            Quality, Sample, WriteResult)
from ..equipment.definitions import Purpose, SimBehavior

FAULTS = ("NO_FEEDBACK", "STUCK_ON", "NO_READBACK", "SENSOR_LOST", "NO_FLOW", "MODULE_OFFLINE")


@dataclass
class SimClock:
    start_utc: datetime
    sim_s: float = 0.0

    def utc(self) -> datetime:
        return self.start_utc + timedelta(seconds=self.sim_s)

    def mono(self) -> float:
        return self.sim_s


@dataclass
class TwinState:
    coils: dict[str, bool] = field(default_factory=dict)
    di: dict[str, bool] = field(default_factory=dict)
    ai_uA: dict[str, float] = field(default_factory=dict)
    on_since: dict[str, float] = field(default_factory=dict)       # point -> sim_s the coil went ON
    off_since: dict[str, float] = field(default_factory=dict)
    faults: dict[str, str] = field(default_factory=dict)            # item_id or device_uid -> fault
    manual: dict[str, float | bool] = field(default_factory=dict)   # point -> user-set value
    levels_L: dict[str, float] = field(default_factory=dict)        # TANK_LEVEL item -> litres (demo)
    seq: int = 0


class TwinBackend:
    """Backend protocol over the twin state. Writes change a virtual coil only."""

    def __init__(self, cfg, state: TwinState, clock: SimClock, lock: threading.RLock):
        self.cfg, self.s, self.clock, self.lock = cfg, state, clock, lock
        self.items_of_point: dict[str, str] = {}

    def capabilities(self, device_uid: str) -> frozenset[Capability]:
        return CATALOG[self.cfg.device(device_uid).model].capabilities

    def health(self, device_uid: str) -> DeviceHealth:
        d = self.cfg.device(device_uid)
        on = self.s.faults.get(device_uid) != "MODULE_OFFLINE"
        return DeviceHealth(device_uid, d.bus_id, online=on, last_ok_utc=self.clock.utc() if on else None)

    def read_inputs(self, device_uid: str) -> AcquisitionResult:
        raise NotImplementedError("the twin publishes samples directly")

    def read_outputs(self, device_uid: str) -> AcquisitionResult:
        raise NotImplementedError("the twin publishes samples directly")

    def write_output(self, command: OutputCommand) -> WriteResult:
        with self.lock:
            pid = PointId.parse(command.target)
            if self.s.faults.get(pid.device_uid) == "MODULE_OFFLINE":
                return WriteResult(Outcome.FAILED, None, "virtual module offline (twin fault)")
            iid = self.items_of_point.get(command.target)
            fault = self.s.faults.get(iid) if iid else None
            if fault == "STUCK_ON" and not command.desired:
                return WriteResult(Outcome.FAILED, True, "twin fault: output stuck ON (readback ON after OFF)")
            prev = self.s.coils.get(command.target, False)
            self.s.coils[command.target] = command.desired
            if command.desired and not prev:
                self.s.on_since[command.target] = self.clock.sim_s
            if not command.desired and prev:
                self.s.off_since[command.target] = self.clock.sim_s
            if fault == "NO_READBACK":
                return WriteResult(Outcome.OUTCOME_UNKNOWN, None, "twin fault: no readback")
            return WriteResult(Outcome.READBACK_CONFIRMED, command.desired, None)

    def close(self) -> None:
        pass


class EquipmentTwin:
    """Simulated inputs for the user's equipment. `step(dt)` advances behaviours; `samples()` publishes them."""

    def __init__(self, cfg, clock: SimClock, items: callable):
        self.cfg, self.clock, self.items = cfg, clock, items      # items() -> dict[item_id, ItemView]
        self.lock = threading.RLock()
        self.s = TwinState()
        self.backend = TwinBackend(cfg, self.s, clock, self.lock)
        self._samples: dict[str, Sample] = {}
        self.not_modelled: dict[str, str] = {}                    # point -> why

    # ------------------------------------------------------------------ user inputs and faults
    def set_input(self, point: str, value) -> None:
        """A user-set input: a digital input takes true/false, an analog input a finite number; nothing else."""
        try:
            pid = PointId.parse(point)
            self.cfg.device(pid.device_uid)
        except (ValueError, KeyError) as e:
            raise ValueError(f"{point} is not a channel of this installation") from e
        if pid.kind == PointKind.DI and not (value is None or isinstance(value, bool)):
            raise ValueError("a digital input is true or false")
        if pid.kind == PointKind.AI and value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("an analog input is a finite number")
        if pid.kind not in (PointKind.DI, PointKind.AI):
            raise ValueError("only inputs can be set by hand; outputs follow commands")
        with self.lock:
            self.s.manual[point] = value

    def set_fault(self, target: str, fault: str | None) -> None:
        if fault is not None and fault not in FAULTS:
            raise ValueError(f"fault is one of {', '.join(FAULTS)}")
        with self.lock:
            if fault is None:
                self.s.faults.pop(target, None)
            else:
                self.s.faults[target] = fault

    def reset(self) -> None:
        with self.lock:
            self.s = TwinState()
            self.backend.s = self.s
            self._samples.clear()

    # ------------------------------------------------------------------ dynamics
    def step(self, dt: float) -> None:
        with self.lock:
            self.clock.sim_s += dt
            items = self.items()
            self.backend.items_of_point = {it.command_point: iid for iid, it in items.items() if it.command_point}
            values: dict[str, float | bool | None] = {}
            self.not_modelled = {}
            flows: dict[str, float] = {}             # flow item -> L/min this step
            for iid, it in items.items():
                beh = (it.body.simulation or it.type.simulation)
                cp = it.command_point
                on = bool(self.s.coils.get(cp)) if cp else False
                since = self.s.on_since.get(cp, 0.0) if on else self.s.off_since.get(cp, 0.0)
                settled = self.clock.sim_s - since >= beh.delay_s
                fault = self.s.faults.get(iid)
                for r in it.type.roles:
                    p = it.points.get(r.role)
                    if p is None or r.direction.value != "INPUT":
                        continue
                    b = beh.behavior
                    if b == SimBehavior.NOT_MODELLED:
                        values[p] = None
                        self.not_modelled[p] = f"{it.name}: no simulated behaviour for {r.label}"
                    elif b in (SimBehavior.MANUAL_INPUT, SimBehavior.MANUAL_ANALOG):
                        values[p] = self.s.manual.get(p)
                        if values[p] is None:
                            self.not_modelled[p] = f"{it.name}: set {r.label} from the twin"
                    elif r.purpose == Purpose.RUNNING:
                        v = on and settled and fault != "NO_FEEDBACK"
                        values[p] = (not v) if r.inverted else v
                    elif r.purpose == Purpose.FAULT:
                        values[p] = (fault is not None) != r.inverted
                    elif r.purpose == Purpose.OPEN and b == SimBehavior.VALVE_TRAVEL:
                        values[p] = on and settled and fault != "NO_FEEDBACK"
                    elif r.purpose == Purpose.CLOSED and b == SimBehavior.VALVE_TRAVEL:
                        values[p] = (not on) and settled and fault != "NO_FEEDBACK"
                    elif r.purpose == Purpose.STATE:
                        values[p] = self.s.manual.get(p, False)
                    elif r.purpose == Purpose.MEASUREMENT and b == SimBehavior.TANK_LEVEL:
                        pass                                  # below, after flows are known
                    else:
                        values[p] = None
                        self.not_modelled[p] = f"{it.name}: {b.value} does not model {r.label}"
                if beh.behavior == SimBehavior.PUMP_FLOW and it.body.sim_links.get("flow_item"):
                    running = on and settled and fault not in ("NO_FEEDBACK", "NO_FLOW")
                    flows[it.body.sim_links["flow_item"]] = (beh.demo_flow_L_min or 0.0) if running else 0.0
            for iid, it in items.items():
                for r in it.type.roles:
                    p = it.points.get(r.role)
                    if p is None or r.purpose != Purpose.MEASUREMENT:
                        continue
                    beh = (it.body.simulation or it.type.simulation).behavior
                    if iid in flows and beh in (SimBehavior.MANUAL_ANALOG, SimBehavior.NOT_MODELLED):
                        values[p] = self._to_raw(it, r.role, flows[iid])
                        self.not_modelled.pop(p, None)
                    elif beh == SimBehavior.MANUAL_ANALOG and isinstance(values.get(p), (int, float)):
                        values[p] = self._to_raw(it, r.role, float(values[p]))
                    elif beh == SimBehavior.TANK_LEVEL:
                        lv = self.s.levels_L.setdefault(iid, float(it.body.properties.get("initial_L", 0.0)))
                        for k, src in it.body.sim_links.items():
                            q = flows.get(src, 0.0) * dt / 60.0
                            lv += q if k.startswith("in") else -q
                        lv = max(0.0, lv)
                        self.s.levels_L[iid] = lv
                        values[p] = self._to_raw(it, r.role, lv)
                    if self.s.faults.get(iid) == "SENSOR_LOST":
                        values[p] = None
            self.s.seq += 1
            now = self.clock.utc()
            # the module reports µA in a current mode and mV in a voltage mode: follow the channel's scaling
            units = {it.points[role]: ("mV" if sc.raw_unit == "V" else "uA")
                     for it in items.values() for role, sc in it.body.scaling.items() if role in it.points}
            out = {}
            for d in self.cfg.devices:
                offline = self.s.faults.get(d.device_uid) == "MODULE_OFFLINE"
                spec = CATALOG[d.model]
                for kind, n in ((PointKind.DO, spec.do), (PointKind.DI, spec.di), (PointKind.AI, spec.ai)):
                    for ch in range(1, n + 1):
                        pid = str(PointId(d.device_uid, kind, ch))
                        if kind == PointKind.DO:
                            v = self.s.coils.get(pid, False)
                            out[pid] = self._sample(pid, now, v, None, None, offline)
                        elif kind == PointKind.DI:
                            v = values.get(pid, False) if pid in values else self.s.manual.get(pid, False)
                            out[pid] = self._sample(pid, now, v, None, None, offline or v is None)
                        else:
                            raw = values.get(pid)
                            if raw is None and pid not in values:
                                raw = self.s.manual.get(pid)
                            if raw is not None and not math.isfinite(raw):
                                raw = None                    # never a NaN or infinite reading
                            out[pid] = self._sample(pid, now, None if raw is None else round(raw), units.get(pid, "uA"),
                                                    None, offline or raw is None)
            self._samples = out

    def _to_raw(self, it, role: str, eng: float) -> float | None:
        sc = it.body.scaling.get(role)
        if sc is None:
            return None
        raw = sc.raw_min + (eng - sc.eng_min) * (sc.raw_max - sc.raw_min) / (sc.eng_max - sc.eng_min)
        return raw * 1000.0                          # the module reports µA (mA scaling) or mV (V scaling)

    def _sample(self, pid, now, value, raw_unit, unit, unavailable) -> Sample:
        d = self.cfg.device(PointId.parse(pid).device_uid)
        common = dict(point_id=pid, acquired_utc=now, cycle_id=f"twin:{self.s.seq}", sequence=self.s.seq,
                      calibration_ref=None, origin=Origin.SIMULATED, source_profile=self.cfg.config_id,
                      config_version=self.cfg.config_version, device_uid=d.device_uid, bus_id=d.bus_id,
                      session_id="twin", sim_time_s=self.clock.sim_s)
        if unavailable:
            why = self.not_modelled.get(pid) or ("virtual module offline" if self.s.faults.get(d.device_uid)
                                                 else "NOT MODELLED")
            return Sample(quality=Quality.UNAVAILABLE, reason=why, raw_value=None, raw_unit=raw_unit, value=None,
                          unit=None, **common)
        if raw_unit:
            return Sample(quality=Quality.UNSCALED, reason=None, raw_value=value, raw_unit=raw_unit, value=None,
                          unit=None, **common)
        return Sample(quality=Quality.GOOD, reason=None, raw_value=bool(value), raw_unit=None, value=bool(value),
                      unit=None, **common)

    def samples(self) -> dict[str, Sample]:
        return self._samples
