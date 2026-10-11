"""Mandatory scenario presets (brief §7) with machine-checked expectations.

A scenario is: optional DEMO parameter overrides (validated; produces a new run), a list of timed,
journaled inputs using the same precedence rules as operators, a duration, and checks. Scenarios
never touch plant volumes directly and have no path to physical outputs.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

from ..config.schema import InstallationConfig
from ..domain.model import ControllerState, Quality
from .runtime import SimulationRuntime

Check = Callable[[SimulationRuntime], list[str]]
SCN = "scenario"


@dataclass
class Scenario:
    name: str
    description: str
    duration_s: float
    steps: list[tuple[float, str, dict]] = field(default_factory=list)
    twin_overrides: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)


def with_twin_overrides(cfg: InstallationConfig, overrides: dict) -> InstallationConfig:
    if not overrides:
        return cfg
    data = cfg.model_dump(mode="json")
    twin = data["twin"]

    def merge(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)
    merge(twin, overrides)
    return InstallationConfig.model_validate(data)


# ---------------------------------------------------------------------------- check helpers
def _state(expected: ControllerState) -> Check:
    return lambda rt: [] if rt.controller.state == expected else [
        f"controller {rt.controller.state.value}, expected {expected.value}"]


def _fault(reason: str) -> Check:
    def c(rt: SimulationRuntime) -> list[str]:
        if rt.controller.state != ControllerState.FAULT_LOCKOUT:
            return [f"expected FAULT_LOCKOUT({reason}), controller is {rt.controller.state.value}"]
        return [] if rt.controller.fault_reason == reason else [
            f"fault {rt.controller.fault_reason}, expected {reason}"]
    return c


def _alarm(rule: str, target: str, active: bool = True) -> Check:
    return lambda rt: [] if rt.alarms.is_active(rule, target) == active else [
        f"alarm {rule}/{target} active={rt.alarms.is_active(rule, target)}, expected {active}"]


def _ledger(rt: SimulationRuntime) -> list[str]:
    return [f"ledger invariant violated {len(rt.invariant_failures)}x"] if rt.invariant_failures else []


def _equipment_stopped(rt: SimulationRuntime) -> list[str]:
    errs = [f"pump {k} still requested ON" for k, a in rt.plant.pumps.items() if a.cmd]
    errs += [f"valve {k} still requested OPEN" for k, a in rt.plant.valves.items() if a.cmd]
    return errs


def _cycles(n: int) -> Check:
    def c(rt):
        sectors = [x["sector"] for x in rt.controller.cycle_log]
        errs = [] if len(sectors) >= n else [f"{len(sectors)} completed sector cycles, expected >= {n}"]
        if len(set(sectors)) < min(n, 2):
            errs.append(f"sectors did not alternate: {sectors}")
        return errs
    return c


def _point_quality(point: str, q: Quality) -> Check:
    return lambda rt: [] if rt.samples[point].quality == q else [
        f"{point} quality {rt.samples[point].quality.value}, expected {q.value}"]


def _volume_unchanged_by_override(rt: SimulationRuntime) -> list[str]:
    truth = rt.plant.volume["reservoir"]
    s = rt.samples["analog_A.AI1"]
    return [] if abs((s.value or 0) - truth) > 100 else ["override did not diverge from plant truth (test invalid)"]


def _spill_positive(rt):
    return [] if rt.plant.ledger.spill_L["reservoir"] > 0 else ["no spill recorded in overflow scenario"]


def _unaffected(device: str) -> Check:
    def c(rt):
        bad = [p for p, s in rt.samples.items() if s.device_uid == device and s.quality == Quality.UNAVAILABLE]
        return [f"healthy device {device} lost points {bad}"] if bad else []
    return c


def _only_device_unavailable(device: str) -> Check:
    def c(rt):
        errs = [f"{p} not UNAVAILABLE" for p, s in rt.samples.items()
                if s.device_uid == device and s.quality != Quality.UNAVAILABLE]
        return errs
    return c


AUTO = (0.0, "STATE_REQUEST", {"target": "AUTO"})


def presets() -> dict[str, Scenario]:
    S = [
        Scenario("normal_cycle", "Two-sector AUTO: precheck, open, start, verify, irrigate, stop, close, pause, "
                 "alternate.", 130, [AUTO],
                 checks=[_state(ControllerState.AUTO), _cycles(3), _ledger]),
        Scenario("pump_no_flow", "Pump spins (run feedback) but delivers no flow.", 20,
                 [(0.0, "ACTUATOR_FAULT", {"kind": "pump", "ident": "P1", "fault": "NO_FLOW"}), AUTO],
                 checks=[_fault("NO_FLOW"), _equipment_stopped, _ledger]),
        Scenario("pump_fails_to_start", "Pump commanded but never runs.", 12,
                 [(0.0, "ACTUATOR_FAULT", {"kind": "pump", "ident": "P1", "fault": "NO_RUN"}), AUTO],
                 checks=[_fault("PUMP_RUN_FEEDBACK_TIMEOUT"), _equipment_stopped]),
        Scenario("reservoir_low", "Reservoir below demo low-level at precheck.", 5, [AUTO],
                 twin_overrides={"reservoir": {"initial_L": 80.0}},
                 checks=[_fault("RESERVOIR_LOW"), _alarm("RESERVOIR_LOW_SWITCH", "relay_A.DI7")]),
        Scenario("reservoir_empty", "Reservoir drains during irrigation; starvation reduces actual flow.", 60,
                 [AUTO], twin_overrides={"reservoir": {"initial_L": 103.0},
                                         "auto": {"min_reservoir_L": 50.0},
                                         "return_transfer_enabled": False, "return_fraction": 0.0,
                                         "sectors": [{"sector_id": "S1", "channels": [1, 2, 3, 4, 5, 6],
                                                      "rated_flow_L_min": 300.0},
                                                     {"sector_id": "S2", "channels": [7, 8, 9, 10, 11, 12, 13],
                                                      "rated_flow_L_min": 300.0}],
                                         "flow_transmitter_span_L_min": 400.0},
                 checks=[_fault("RESERVOIR_LOW"), _equipment_stopped, _ledger]),
        Scenario("overflow_fill_stuck_open", "Fill valve stuck open during AUTO; high-level switch trips, "
                 "excess goes to the spill ledger.", 240,
                 [(0.0, "ACTUATOR_FAULT", {"kind": "valve", "ident": "FILL", "fault": "STUCK_OPEN"}), AUTO],
                 twin_overrides={"reservoir": {"initial_L": 900.0}},
                 checks=[_fault("OVERFLOW_HIGH_LEVEL"), _alarm("RESERVOIR_HIGH_SWITCH", "relay_A.DI6"),
                         _spill_positive, _ledger]),
        Scenario("ai_above_limit", "Level transmitter overridden high; plant volume untouched.", 3,
                 [(0.0, "OVERRIDE_SET", {"point": "analog_A.AI1", "raw": 19800, "reason": "scenario: AI high"})],
                 checks=[_alarm("RESERVOIR_LEVEL_HIGH", "analog_A.AI1"), _volume_unchanged_by_override,
                         _point_quality("analog_A.AI1", Quality.OVERRIDDEN)]),
        Scenario("ai_below_limit", "Level transmitter overridden low.", 3,
                 [(0.0, "OVERRIDE_SET", {"point": "analog_A.AI1", "raw": 5000, "reason": "scenario: AI low"})],
                 checks=[_alarm("RESERVOIR_LEVEL_LOW", "analog_A.AI1")]),
        Scenario("loop_open_circuit", "4-20 mA open loop on S1 flow during AUTO: INVALID, AUTO faults.", 10,
                 [AUTO, (5.0, "OVERRIDE_SET", {"point": "analog_A.AI2", "raw": 0, "reason": "scenario: open loop"})],
                 checks=[_alarm("AI_SIGNAL_INVALID", "analog_A.AI2"), _fault("OBSERVATION_UNAVAILABLE"),
                         _point_quality("analog_A.AI2", Quality.INVALID)]),
        Scenario("loop_over_range", "Current above 20.5 mA.", 2,
                 [(0.0, "OVERRIDE_SET", {"point": "analog_A.AI3", "raw": 22000, "reason": "scenario: over-range"})],
                 checks=[_alarm("AI_SIGNAL_INVALID", "analog_A.AI3")]),
        Scenario("valve_stuck_closed", "Sector valve does not open.", 8,
                 [(0.0, "ACTUATOR_FAULT", {"kind": "valve", "ident": "S1", "fault": "STUCK_CLOSED"}), AUTO],
                 checks=[_fault("VALVE_OPEN_TIMEOUT"), _equipment_stopped]),
        Scenario("di_failed_low", "Pump run-feedback DI stuck low although the pump runs.", 10,
                 [(0.0, "OVERRIDE_SET", {"point": "relay_A.DI1", "raw": False, "reason": "scenario: DI stuck low"}),
                  AUTO],
                 checks=[_fault("PUMP_RUN_FEEDBACK_TIMEOUT"), _equipment_stopped]),
        Scenario("frozen_flow_sensor", "Flow transmitter frozen during irrigation; after stop it disagrees "
                 "with the stopped pump (model-deviation suspicion, not a 'frozen' verdict).", 30,
                 [AUTO, (10.0, "FREEZE", {"point": "analog_A.AI2"})],
                 checks=[_alarm("FLOW_PLAUSIBILITY", "sector.S1")]),
        Scenario("stale_acquisition", "Acquisition stops while AUTO irrigates.", 14,
                 [AUTO, (8.0, "ACQ_STALL", {"stalled": True})],
                 checks=[_fault("OBSERVATION_UNAVAILABLE"), _alarm("ACQUISITION_STALE", "acquisition"),
                         _equipment_stopped]),
        Scenario("module_timeout", "analog_A stops answering; only its points become unavailable.", 10,
                 [AUTO, (6.0, "DEVICE_FAULT", {"device_uid": "analog_A", "fault": "TIMEOUT"})],
                 checks=[_only_device_unavailable("analog_A"), _unaffected("relay_A"),
                         _alarm("DEVICE_COMM", "analog_A"), _fault("OBSERVATION_UNAVAILABLE")]),
        Scenario("conflicting_manual_during_auto", "Operator tries to drive a pump during AUTO.", 6,
                 [AUTO, (5.0, "COMMAND", {"point": "relay_A.DO2", "desired": True, "reason": "conflict test"}),
                  (5.1, "STATE_REQUEST", {"target": "MANUAL"})],
                 checks=[_state(ControllerState.AUTO)]),
        Scenario("recovery_ack_reset", "Fault, attempted early reset, ack, reset, deliberate restart.", 60,
                 [(0.0, "ACTUATOR_FAULT", {"kind": "valve", "ident": "S1", "fault": "STUCK_CLOSED"}), AUTO,
                  (8.0, "RESET", {}),                                  # rejected: not acknowledged
                  (9.0, "ACK", {"rule_id": "CTRL_VALVE_OPEN_TIMEOUT", "target": "controller"}),
                  (9.5, "ACTUATOR_FAULT", {"kind": "valve", "ident": "S1", "fault": None}),
                  (10.0, "RESET", {}),
                  (11.0, "STATE_REQUEST", {"target": "AUTO"})],
                 checks=[_state(ControllerState.AUTO), _cycles(1)]),
    ]
    return {s.name: s for s in S}


def run_scenario(cfg: InstallationConfig, sc: Scenario, *, seed: int | None = None, **kw) -> tuple[SimulationRuntime, list[str]]:
    rt = SimulationRuntime(with_twin_overrides(cfg, sc.twin_overrides), seed=seed, **kw)
    for at, kind, payload in sc.steps:
        rt.schedule(at, kind, SCN, payload)
    rt.run_for(sc.duration_s)
    errs: list[str] = []
    for c in sc.checks:
        errs += c(rt)
    return rt, errs
