"""Controller state machine and the two-sector DEMO AUTO sequence (brief §5, §7).

The sequence is a demonstration policy, not a field-approved sequence. The controller only sees
*observations* (scaled samples via the virtual mapping) and only acts through audited DO commands,
exactly like an operator. It never touches plant internals.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable

from ..domain.model import CommandOrigin, CommandRecord, ControllerState, Outcome, Priority
from ..twin.params import TwinParams
from .alarms import AlarmEngine


class Phase(str, enum.Enum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    OPEN_SELECTED_VALVE = "OPEN_SELECTED_VALVE"
    START_SELECTED_PUMP = "START_SELECTED_PUMP"
    VERIFY_FLOW = "VERIFY_FLOW"
    IRRIGATE = "IRRIGATE"
    STOP_PUMP = "STOP_PUMP"
    CLOSE_VALVE = "CLOSE_VALVE"
    PAUSE = "PAUSE"
    NEXT_SECTOR = "NEXT_SECTOR"
    STOPPING = "STOPPING"


# Faults whose *condition* is a live observation; reset requires that observation to be healthy.
LEVEL_FAULTS = {"OVERFLOW_HIGH_LEVEL": ("reservoir.high_level", True),
                "RESERVOIR_LOW": ("reservoir.low_level", True)}


@dataclass
class Transition:
    sim_s: float
    from_state: str
    to_state: str
    from_phase: str
    to_phase: str
    reason: str
    actor: str
    permissives: dict | None = None


@dataclass
class ObservationView:
    """Read-only access to observations by twin signal; None means not usable (bad quality/unmapped)."""
    get: Callable[[str], float | bool | None]


@dataclass
class Controller:
    params: TwinParams
    issue: Callable[[str, bool, str, Priority], CommandRecord | None]
    alarms: AlarmEngine
    emit: Callable[[str, dict, str | None], None]
    mapped_outputs_on: Callable[[], list[str]]       # signals whose virtual DO coil is ON
    state: ControllerState = ControllerState.STOPPED
    phase: Phase = Phase.IDLE
    phase_since_s: float = 0.0
    sector_idx: int = 0
    fault_reason: str | None = None
    fault_occurrence_id: str | None = None
    history: list[Transition] = field(default_factory=list)
    last_permissives: dict = field(default_factory=dict)
    _low_flow_since: float | None = None
    _irrigated_s: float = 0.0
    _delivered_L: float = 0.0
    _stop_valves_at: float | None = None
    _stop_target: ControllerState | None = None
    _now: float = 0.0
    cycle_log: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    @property
    def auto(self):
        return self.params.auto

    @property
    def sector(self) -> str:
        return self.auto.sector_order[self.sector_idx % len(self.auto.sector_order)]

    def _transition(self, state: ControllerState | None, phase: Phase | None, reason: str, actor: str = "controller",
                    permissives: dict | None = None) -> None:
        tr = Transition(self._now, self.state.value, (state or self.state).value, self.phase.value,
                        (phase or self.phase).value, reason, actor, permissives)
        self.history.append(tr)
        del self.history[:-500]
        if state is not None:
            self.state = state
        if phase is not None and phase != self.phase:
            self.phase, self.phase_since_s = phase, self._now
        elif phase is not None:
            self.phase_since_s = self._now
        self.emit("CONTROLLER_TRANSITION", tr.__dict__, actor)

    def _cmd(self, signal: str, on: bool, reason: str) -> bool:
        rec = self.issue(signal, on, reason, Priority.NORMAL if on else Priority.STOP)
        return rec is not None and rec.outcome == Outcome.READBACK_CONFIRMED

    # ------------------------------------------------------------------ operator requests
    def request(self, target: ControllerState, actor: str, *, source_is_twin: bool) -> tuple[bool, str]:
        s = self.state
        if target == ControllerState.STOPPED:
            if s == ControllerState.FAULT_LOCKOUT:
                return False, "in FAULT_LOCKOUT: acknowledge and reset instead"
            self._begin_stop(ControllerState.STOPPED, f"stop requested by {actor}", actor)
            return True, "stopping"
        if target == ControllerState.MANUAL:
            if s == ControllerState.STOPPED and self.phase == Phase.IDLE:
                self._transition(ControllerState.MANUAL, Phase.IDLE, f"manual mode by {actor}", actor)
                return True, "manual"
            return False, f"MANUAL only from STOPPED (current {s.value}/{self.phase.value}); stop first"
        if target == ControllerState.AUTO:
            if not source_is_twin:
                return False, "AUTO requires the TWIN signal source (process control is inactive in IO_TEST)"
            if s != ControllerState.STOPPED or self.phase != Phase.IDLE:
                return False, f"AUTO only from STOPPED (current {s.value}); explicit stop/ownership transfer required"
            on = self.mapped_outputs_on()
            if on:
                return False, f"virtual outputs still ON {on}; AUTO will not adopt equipment"
            self._low_flow_since, self._irrigated_s, self._delivered_L = None, 0.0, 0.0
            self._transition(ControllerState.AUTO, Phase.PRECHECK, f"AUTO started by {actor}", actor)
            return True, "auto"
        return False, f"unsupported target {target.value}"

    def reset_lockout(self, actor: str, obs: ObservationView) -> tuple[bool, str]:
        if self.state != ControllerState.FAULT_LOCKOUT:
            return False, "not in FAULT_LOCKOUT"
        if self.phase == Phase.STOPPING:
            return False, "stop sequence still running"
        ok, why = self._condition_cleared(obs)
        if not ok:
            return False, f"fault condition still present: {why}"
        occ = self.alarms.get(self.fault_occurrence_id) if self.fault_occurrence_id else None
        if occ is not None:
            if not occ.acknowledged:
                return False, "acknowledge the fault alarm before reset"
            self.alarms.clear(occ.rule_id, occ.target)
            self.alarms.reset(occ.occurrence_id, actor)
        reason = self.fault_reason
        self.fault_reason, self.fault_occurrence_id = None, None
        self._transition(ControllerState.STOPPED, Phase.IDLE, f"lockout reset by {actor} (was {reason}); "
                         "restart requires a deliberate AUTO start", actor)
        return True, "reset; controller STOPPED"

    def _condition_cleared(self, obs: ObservationView) -> tuple[bool, str]:
        if self.fault_reason in LEVEL_FAULTS:
            sig, bad = LEVEL_FAULTS[self.fault_reason]
            v = obs.get(sig)
            if v is None:
                return False, f"{sig} unavailable"
            if v == bad:
                return False, f"{sig} still active"
        if self.fault_reason == "OBSERVATION_UNAVAILABLE":
            missing = [s for s in self._required_signals(self.sector) if obs.get(s) is None]
            if missing:
                return False, f"still unavailable: {missing}"
        running = [p for p in self.params.pumps if obs.get(f"pump.{p}.running")]
        if running:
            return False, f"pump feedback still running: {running}"
        if self.mapped_outputs_on():
            return False, f"outputs still ON: {self.mapped_outputs_on()}"
        return True, ""

    # ------------------------------------------------------------------ fault / stop
    def fault(self, reason: str, detail: str) -> None:
        if self.state == ControllerState.FAULT_LOCKOUT:
            return
        occ = self.alarms.raise_or_update(f"CTRL_{reason}", "controller", severity="CRITICAL", latch=True,
                                          message=f"{reason}: {detail}", sim_s=self._now)
        self.fault_reason, self.fault_occurrence_id = reason, occ.occurrence_id
        self._begin_stop(ControllerState.FAULT_LOCKOUT, f"FAULT {reason}: {detail}", "controller")

    def _begin_stop(self, target: ControllerState, reason: str, actor: str) -> None:
        self._transition(target, Phase.STOPPING, reason, actor)
        self._stop_target = target
        for p in self.params.pumps:
            self.issue(f"pump.{p}.cmd", False, f"stop sequence: {reason}", Priority.STOP)
        self._stop_valves_at = self._now + self.auto.stop_valve_delay_s

    def _continue_stop(self) -> None:
        if self._stop_valves_at is not None and self._now + 1e-9 >= self._stop_valves_at:
            for s in [s.sector_id for s in self.params.sectors] + ["FILL"]:
                self.issue(f"valve.{s}.cmd", False, "stop sequence: close valves", Priority.STOP)
            self._stop_valves_at = None
            self._transition(None, Phase.IDLE, "stop sequence complete")

    # ------------------------------------------------------------------ AUTO evaluation
    def _required_signals(self, sector: str) -> list[str]:
        p = self.auto.selected_pump
        return ["reservoir.level_transmitter", "reservoir.high_level", "reservoir.low_level",
                f"pump.{p}.running", f"valve.{sector}.open_limit", f"sector.{sector}.flow_transmitter"]

    def tick(self, now_s: float, obs: ObservationView) -> None:
        self._now = now_s
        if self.phase == Phase.STOPPING:
            self._continue_stop()
            return
        if self.state != ControllerState.AUTO:
            return
        a, sector, pump = self.auto, self.sector, self.auto.selected_pump
        dt = self.params.dt_s
        # ---- global faults first: they take priority over any timing transition in this tick
        missing = [s for s in self._required_signals(sector) if obs.get(s) is None]
        if missing:
            return self.fault("OBSERVATION_UNAVAILABLE", f"required observations unusable: {missing}")
        if obs.get("reservoir.high_level"):
            return self.fault("OVERFLOW_HIGH_LEVEL", "reservoir high-level switch active")
        pumping = self.phase in (Phase.START_SELECTED_PUMP, Phase.VERIFY_FLOW, Phase.IRRIGATE)
        if pumping and obs.get("reservoir.low_level"):
            return self.fault("RESERVOIR_LOW", "reservoir low-level switch active while pumping")
        for other in self.params.pumps:
            if other != pump and obs.get(f"pump.{other}.running"):
                return self.fault("UNCOMMANDED_PUMP_RUN", f"pump {other} running feedback while not owned")
        if not pumping and self.phase not in (Phase.STOP_PUMP,) and obs.get(f"pump.{pump}.running"):
            return self.fault("UNCOMMANDED_PUMP_RUN", f"pump {pump} running outside pumping phases")
        flow = obs.get(f"sector.{sector}.flow_transmitter")
        elapsed = now_s - self.phase_since_s + 1e-9

        if self.phase == Phase.PRECHECK:
            perm = {
                "config_valid": True,
                "reservoir_volume_ok": (obs.get("reservoir.level_transmitter") or 0) >= a.min_reservoir_L,
                "low_level_inactive": not obs.get("reservoir.low_level"),
                "required_observations_healthy": True,
                "no_blocking_alarm": not [o for o in self.alarms.active_blocking() if o.target != "controller"],
                "one_pump_ownership": not any(obs.get(f"pump.{p}.running") for p in self.params.pumps),
                "no_conflicting_sector_open": not any(obs.get(f"valve.{s.sector_id}.open_limit")
                                                      for s in self.params.sectors),
            }
            self.last_permissives = perm
            failed = [k for k, v in perm.items() if not v]
            if failed:
                reason = "RESERVOIR_LOW" if ("reservoir_volume_ok" in failed or "low_level_inactive" in failed) \
                    else "PRECHECK_FAILED"
                return self.fault(reason, f"precheck failed for {sector}: {failed}")
            self._transition(None, Phase.OPEN_SELECTED_VALVE, f"precheck passed for {sector}", permissives=perm)
            self._cmd(f"valve.{sector}.cmd", True, f"AUTO open {sector}")
        elif self.phase == Phase.OPEN_SELECTED_VALVE:
            if obs.get(f"valve.{sector}.open_limit"):
                self._transition(None, Phase.START_SELECTED_PUMP, f"valve {sector} open confirmed")
                self._cmd(f"pump.{pump}.cmd", True, f"AUTO start {pump} for {sector}")
            elif elapsed >= a.valve_feedback_timeout_s:
                self.fault("VALVE_OPEN_TIMEOUT", f"valve {sector} open feedback not seen in {a.valve_feedback_timeout_s}s")
        elif self.phase == Phase.START_SELECTED_PUMP:
            if obs.get(f"pump.{pump}.running"):
                self._low_flow_since = None
                self._transition(None, Phase.VERIFY_FLOW, f"pump {pump} running feedback confirmed")
            elif elapsed >= a.pump_feedback_timeout_s:
                self.fault("PUMP_RUN_FEEDBACK_TIMEOUT", f"{pump} run feedback not seen in {a.pump_feedback_timeout_s}s")
        elif self.phase in (Phase.VERIFY_FLOW, Phase.IRRIGATE):
            if not obs.get(f"pump.{pump}.running"):
                return self.fault("PUMP_STOPPED", f"{pump} running feedback lost during {self.phase.value}")
            low = flow is None or flow < a.min_flow_L_min
            in_grace = self.phase == Phase.VERIFY_FLOW and elapsed < a.flow_grace_s
            if low and not in_grace:
                if self._low_flow_since is None:
                    self._low_flow_since = now_s
                if now_s - self._low_flow_since + 1e-9 >= a.no_flow_sustain_s:
                    return self.fault("NO_FLOW", f"{sector} flow {flow!s} < {a.min_flow_L_min} L/min for "
                                                 f"{a.no_flow_sustain_s}s")
            elif not low:
                self._low_flow_since = None
            if self.phase == Phase.VERIFY_FLOW:
                if not in_grace and not low:
                    self._irrigated_s, self._delivered_L = 0.0, 0.0
                    self._transition(None, Phase.IRRIGATE, f"flow verified {flow:.2f} L/min on {sector}")
            else:
                if not low:  # runtime counts only verified actual flow, not the requested timer
                    self._irrigated_s += dt
                self._delivered_L += (flow or 0.0) * dt / 60.0
                if self._irrigated_s + 1e-9 >= a.irrigate_s:
                    self.cycle_log.append({"sector": sector, "end_sim_s": now_s, "irrigated_s": self._irrigated_s,
                                           "delivered_L_observed": self._delivered_L})
                    self._transition(None, Phase.STOP_PUMP,
                                     f"{sector} irrigated {self._irrigated_s:.1f}s, ~{self._delivered_L:.2f} L observed")
                    self._cmd(f"pump.{pump}.cmd", False, f"AUTO stop {pump}")
        elif self.phase == Phase.STOP_PUMP:
            if not obs.get(f"pump.{pump}.running"):
                self._transition(None, Phase.CLOSE_VALVE, f"{pump} stopped")
                self._cmd(f"valve.{sector}.cmd", False, f"AUTO close {sector}")
            elif elapsed >= a.pump_feedback_timeout_s:
                self.fault("PUMP_STOP_FEEDBACK_TIMEOUT", f"{pump} still running after stop command")
        elif self.phase == Phase.CLOSE_VALVE:
            if not obs.get(f"valve.{sector}.open_limit"):
                self._transition(None, Phase.PAUSE, f"valve {sector} open-limit released")
            elif elapsed >= a.valve_feedback_timeout_s:
                self.fault("VALVE_CLOSE_TIMEOUT", f"valve {sector} still open after close command")
        elif self.phase == Phase.PAUSE:
            if elapsed >= a.pause_s:
                self._transition(None, Phase.NEXT_SECTOR, "pause complete")
        elif self.phase == Phase.NEXT_SECTOR:
            self.sector_idx = (self.sector_idx + 1) % len(a.sector_order)
            self._transition(None, Phase.PRECHECK, f"next sector {self.sector}")

    def snapshot(self) -> dict:
        return {"state": self.state.value, "phase": self.phase.value, "phase_since_s": self.phase_since_s,
                "sector": self.sector, "selected_pump": self.auto.selected_pump, "fault_reason": self.fault_reason,
                "fault_occurrence_id": self.fault_occurrence_id, "permissives": self.last_permissives,
                "irrigated_s": self._irrigated_s, "delivered_L_observed": self._delivered_L,
                "history": [t.__dict__ for t in self.history[-30:]], "cycles": self.cycle_log[-20:],
                "policy_note": "DEMO sequence; not a field-approved control sequence"}
