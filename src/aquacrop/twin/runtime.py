"""Simulation runtime: one run of the twin with its virtual backend, controller, alarms and journal.

Determinism contract
--------------------
* One `step()` = one fixed tick of `dt_s` simulated seconds. Speed changes only how many steps the
  wall-clock driver performs per wall second; it never changes dt or event order.
* Every external input (operator command, injection, override, fault, state request, ack, reset,
  source change) is applied *between* ticks and journaled with `(tick, seq)`. Replay applies the same
  journal before the same tick in the same order and must reproduce the state hash exactly.
* Order inside a tick: expire overrides -> plant.step -> acquire all points -> alarms -> controller
  -> historian stride -> ledger invariant.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config.schema import InstallationConfig
from ..control.alarms import GOOD, AlarmEngine
from ..control.auto import Controller, ObservationView, Phase
from ..control.commands import AuditSink, CommandService, MemoryAudit
from ..domain.model import (CommandOrigin, CommandRecord, ControllerState, OutputCommand, Outcome, PointId,
                            PointKind, Priority, Sample, SignalSource, new_id, utcnow)
from ..io.sampling import AcqContext
from ..io.virtual import Override, VirtualBackend
from .observe import observe
from .plant import Plant

LEDGER_TOL_ABS_L = 1e-6
LEDGER_TOL_PER_TICK_L = 1e-9
STALE_AFTER_SIM_S = 0.5        # controller treats older observations as unusable
PLAUSIBILITY_SUSTAIN_S = 3.0   # flow reported with no pump running feedback


@dataclass
class JournalEntry:
    tick: int
    seq: int
    kind: str
    actor: str
    payload: dict

    def to_dict(self) -> dict:
        return {"tick": self.tick, "seq": self.seq, "kind": self.kind, "actor": self.actor, "payload": self.payload}


class HistorySink:
    """Interface the runtime uses to persist. The historian implements it; tests may use the default no-op."""

    def cycle(self, run: "SimulationRuntime", samples: list[Sample]) -> None: ...

    def alarms(self, occurrences: list) -> None: ...


@dataclass
class InvariantFailure:
    tick: int
    residual_L: float


class SimulationRuntime:
    def __init__(self, cfg: InstallationConfig, *, audit: AuditSink | None = None,
                 history: HistorySink | None = None, session_id: str = "session_local",
                 run_id: str | None = None, source: SignalSource = SignalSource.TWIN,
                 seed: int | None = None,
                 start_permitted: Callable[[], tuple[bool, str | None]] | None = None):
        if cfg.twin is None:
            raise ValueError("configuration has no twin section")
        self.cfg = cfg
        self.params = cfg.twin
        self.seed = self.params.seed if seed is None else seed
        self.session_id = session_id
        self.run_id = run_id or new_id("run")
        self.audit = audit or MemoryAudit()
        self.history = history
        self.rng = random.Random(self.seed)
        self.plant = Plant(self.params)
        self._obs = observe(self.plant, self.rng)
        self.backend = VirtualBackend(cfg, observations=lambda: self._obs, actuate=self.plant.command,
                                      context=self._ctx, source=source)
        self.alarms = AlarmEngine(cfg, emit=self._alarm_event)
        self.commands = CommandService(cfg, self.backend, self.audit, self._policy, lambda: self.controller.state)
        self.controller = Controller(self.params, issue=self._issue_signal, alarms=self.alarms,
                                     emit=self._event, mapped_outputs_on=self._mapped_on)
        self.start_permitted = start_permitted or (lambda: (True, None))
        self.controller.start_permitted = self.start_permitted
        self.journal: list[JournalEntry] = []
        self._seq = 0
        self._scheduled: list[tuple[int, int, str, str, dict]] = []
        self.samples: dict[str, Sample] = {}
        self._prev_valid: dict[str, Any] = {}
        self._cycle = 0
        self.stride_ticks = max(1, int(round(cfg.historian.sim_history_stride_s / self.params.dt_s)))
        self._next_history_tick = self.stride_ticks
        self.invariant_failures: list[InvariantFailure] = []
        self.max_abs_residual_L = 0.0
        self.events: list[dict] = []   # recent events for UI (bounded); durable copy goes to audit
        self.on_event: Callable[[dict], None] | None = None
        self.acquisition_stalled = False
        self._implausible_since: dict[str, float] = {}
        self._device_status: dict[str, tuple[bool, str | None]] = {}
        self._acquire()
        self._event("RUN_STARTED", {"run_id": self.run_id, "seed": self.seed, "config_id": cfg.config_id,
                                    "config_version": cfg.config_version, "source": source.value,
                                    "dt_s": self.params.dt_s, "provenance": self.params.provenance}, "system")

    # ------------------------------------------------------------------ context / events
    @property
    def tick(self) -> int:
        return self.plant.tick

    @property
    def sim_s(self) -> float:
        return self.plant.sim_time_s

    def _ctx(self) -> AcqContext:
        return AcqContext(self.session_id, f"{self.run_id}:{self._cycle}", self.run_id, self.sim_s)

    def _event(self, kind: str, payload: dict, actor: str | None = None) -> None:
        ev = {"kind": kind, "payload": payload, "actor": actor, "sim_s": self.sim_s, "tick": self.tick,
              "run_id": self.run_id, "utc": utcnow().isoformat()}
        self.events.append(ev)
        del self.events[:-1000]
        try:
            self.audit.event(kind, payload | {"run_id": self.run_id, "tick": self.tick}, actor=actor, sim_s=self.sim_s)
        except Exception:
            pass  # audit failure is surfaced by the historian health, not by crashing the simulation
        if self.on_event:
            self.on_event(ev)

    def _alarm_event(self, kind: str, payload: dict) -> None:
        self._event(kind, payload, None)

    def _record(self, kind: str, actor: str, payload: dict) -> None:
        self._seq += 1
        self.journal.append(JournalEntry(self.tick, self._seq, kind, actor, payload))

    # ------------------------------------------------------------------ policy and command routing
    def _mapped_on(self) -> list[str]:
        out = []
        for m in self.cfg.virtual_mapping:
            if m.signal.endswith(".cmd") and self.backend.state.coils.get(m.point):
                out.append(m.signal)
        return out

    def _policy(self, cmd: OutputCommand) -> str | None:
        st, ph = self.controller.state, self.controller.phase
        if cmd.desired:
            ok, why = self.start_permitted()
            if not ok:
                return f"new starts inhibited: {why}"
        if cmd.origin in (CommandOrigin.CONTROLLER, CommandOrigin.SYSTEM):
            return None
        if st == ControllerState.AUTO:
            return "conflicting manual command during AUTO; stop AUTO first"
        if ph == Phase.STOPPING and cmd.desired:
            return "stop sequence in progress"
        if cmd.desired and st != ControllerState.MANUAL:
            return f"ON commands require MANUAL (controller is {st.value})"
        return None

    def _issue_signal(self, signal: str, on: bool, reason: str, priority: Priority) -> CommandRecord | None:
        point = self.cfg.point_for_signal(signal)
        if point is None:
            return None  # actuator not mapped to any virtual DO in this fixture
        cmd = self.commands.build(actor="controller", target=point, desired=on, origin=CommandOrigin.CONTROLLER,
                                  reason=reason, priority=priority)
        rec = self.commands.submit(cmd)
        if rec.outcome != Outcome.READBACK_CONFIRMED:
            self._event("COMMAND_NOT_CONFIRMED", {"target": point, "desired": on, "outcome": rec.outcome.value,
                                                  "detail": rec.detail}, "controller")
        return rec

    # ------------------------------------------------------------------ external inputs (journaled)
    def operator_command(self, actor: str, point: str, desired: bool, reason: str = "manual",
                         request_id: str | None = None) -> CommandRecord:
        self._record("COMMAND", actor, {"point": point, "desired": bool(desired), "reason": reason})
        cmd = self.commands.build(actor=actor, target=point, desired=bool(desired), origin=CommandOrigin.OPERATOR,
                                  reason=reason, request_id=request_id)
        return self.commands.submit(cmd)

    def inject(self, actor: str, point: str, raw) -> None:
        if self.backend.source != SignalSource.IO_TEST:
            raise PermissionError("raw injection is only available in IO_TEST; use an override in TWIN")
        self.backend.inject(point, raw)
        self._record("INJECT", actor, {"point": point, "raw": raw})
        self._event("IO_INJECTION", {"point": point, "raw": raw}, actor)
        self._acquire()

    def set_override(self, actor: str, point: str, raw, reason: str, *, duration_s: float | None = None,
                     kind: str = "VALUE") -> None:
        if not reason.strip():
            raise ValueError("override requires a reason")
        expires = None if duration_s is None else self.sim_s + duration_s
        self.backend.set_override(Override(point, raw, actor, reason, utcnow(), expires, kind))
        self._record("OVERRIDE_SET", actor, {"point": point, "raw": raw, "reason": reason,
                                             "duration_s": duration_s, "kind": kind})
        self._event("OVERRIDE_SET", {"point": point, "raw": raw, "reason": reason, "duration_s": duration_s,
                                     "kind": kind}, actor)
        self._acquire()

    def freeze_observation(self, actor: str, point: str, reason: str = "scenario: frozen sensor") -> None:
        s = self.samples.get(point)
        if s is None or s.raw_value is None:
            raise ValueError(f"{point} has no current raw value to freeze")
        self.set_override(actor, point, s.raw_value, reason, kind="FROZEN")

    def clear_override(self, actor: str, point: str) -> bool:
        ok = self.backend.clear_override(point)
        self._record("OVERRIDE_CLEAR", actor, {"point": point})
        if ok:
            self._event("OVERRIDE_CLEARED", {"point": point}, actor)
        self._acquire()
        return ok

    def set_actuator_fault(self, actor: str, kind: str, ident: str, fault: str | None) -> None:
        self.plant.set_fault(kind, ident, fault)
        self._record("ACTUATOR_FAULT", actor, {"kind": kind, "ident": ident, "fault": fault})
        self._event("ACTUATOR_FAULT", {"kind": kind, "ident": ident, "fault": fault}, actor)

    def set_device_fault(self, actor: str, device_uid: str, fault: str | None) -> None:
        self.cfg.device(device_uid)
        if fault not in (None, "TIMEOUT"):
            raise ValueError(fault)
        if fault:
            self.backend.state.device_faults[device_uid] = fault
        else:
            self.backend.state.device_faults.pop(device_uid, None)
        self._record("DEVICE_FAULT", actor, {"device_uid": device_uid, "fault": fault})
        self._event("DEVICE_FAULT", {"device_uid": device_uid, "fault": fault}, actor)
        self._acquire()

    def request_state(self, actor: str, target: ControllerState) -> tuple[bool, str]:
        self._record("STATE_REQUEST", actor, {"target": target.value})
        ok, msg = self.controller.request(target, actor, source_is_twin=self.backend.source == SignalSource.TWIN)
        if not ok:
            self._event("STATE_REQUEST_REJECTED", {"target": target.value, "reason": msg}, actor)
        return ok, msg

    def acknowledge(self, actor: str, rule_id: str, target: str):
        self._record("ACK", actor, {"rule_id": rule_id, "target": target})
        occ = self.alarms.open.get((rule_id, target))
        if occ is None:
            raise KeyError(f"no open alarm {rule_id}/{target}")
        return self.alarms.acknowledge(occ.occurrence_id, actor)

    def reset_lockout(self, actor: str) -> tuple[bool, str]:
        self._record("RESET", actor, {})
        ok, msg = self.controller.reset_lockout(actor, self.view())
        if not ok:
            self._event("RESET_REJECTED", {"reason": msg}, actor)
        return ok, msg

    def reset_alarm(self, actor: str, rule_id: str, target: str):
        self._record("ALARM_RESET", actor, {"rule_id": rule_id, "target": target})
        occ = self.alarms.open.get((rule_id, target))
        if occ is None:
            raise KeyError(f"no open alarm {rule_id}/{target}")
        if target == "controller":
            raise ValueError("controller fault alarms are reset through the lockout reset")
        return self.alarms.reset(occ.occurrence_id, actor)

    def set_source(self, actor: str, source: SignalSource) -> tuple[bool, str]:
        if source == self.backend.source:
            return True, "unchanged"
        if self.controller.state not in (ControllerState.STOPPED,) or self.controller.phase != Phase.IDLE:
            return False, "change signal source only while STOPPED"
        on = [p for p, v in self.backend.state.coils.items() if v]
        if on:
            return False, f"switch all virtual outputs OFF first: {on}"
        self._record("SOURCE", actor, {"source": source.value})
        self.backend.source = source
        self._event("SIGNAL_SOURCE_CHANGED", {"source": source.value}, actor)
        self._acquire()
        return True, source.value

    def set_acquisition_stall(self, actor: str, stalled: bool) -> None:
        """Scenario: the acquisition cycle stops while the plant keeps running (stale data)."""
        self.acquisition_stalled = bool(stalled)
        self._record("ACQ_STALL", actor, {"stalled": self.acquisition_stalled})
        self._event("ACQUISITION_STALL" if stalled else "ACQUISITION_RESUMED", {}, actor)

    @property
    def history_stride_s(self) -> float:
        return self.stride_ticks * self.params.dt_s

    def set_history_stride(self, stride_s: float, reason: str) -> None:
        """Change the simulation-time history cadence explicitly (never a silent omission)."""
        ticks = max(1, int(round(stride_s / self.params.dt_s)))
        if ticks != self.stride_ticks:
            self.stride_ticks = ticks
            self._next_history_tick = self.tick + ticks
            self._event("HISTORY_STRIDE_CHANGED", {"stride_sim_s": self.history_stride_s, "reason": reason}, "system")

    def schedule(self, at_s: float, kind: str, actor: str, payload: dict) -> None:
        """Queue a scenario input to be applied before the tick at simulated time `at_s`."""
        tick = int(round(at_s / self.params.dt_s))
        self._scheduled.append((tick, len(self._scheduled), kind, actor, payload))
        self._scheduled.sort(key=lambda x: (x[0], x[1]))

    def apply(self, kind: str, actor: str, p: dict):
        """Dispatch a journaled/scheduled input by kind (used by scenarios and replay)."""
        if kind == "COMMAND":
            return self.operator_command(actor, p["point"], p["desired"], p.get("reason", "manual"))
        if kind == "INJECT":
            return self.inject(actor, p["point"], p["raw"])
        if kind == "OVERRIDE_SET":
            return self.set_override(actor, p["point"], p["raw"], p["reason"], duration_s=p.get("duration_s"),
                                     kind=p.get("kind", "VALUE"))
        if kind == "FREEZE":
            return self.freeze_observation(actor, p["point"], p.get("reason", "scenario: frozen sensor"))
        if kind == "OVERRIDE_CLEAR":
            return self.clear_override(actor, p["point"])
        if kind == "ACTUATOR_FAULT":
            return self.set_actuator_fault(actor, p["kind"], p["ident"], p["fault"])
        if kind == "DEVICE_FAULT":
            return self.set_device_fault(actor, p["device_uid"], p["fault"])
        if kind == "STATE_REQUEST":
            return self.request_state(actor, ControllerState(p["target"]))
        if kind == "ACK":
            return self.acknowledge(actor, p["rule_id"], p["target"])
        if kind == "RESET":
            return self.reset_lockout(actor)
        if kind == "ALARM_RESET":
            return self.reset_alarm(actor, p["rule_id"], p["target"])
        if kind == "SOURCE":
            return self.set_source(actor, SignalSource(p["source"]))
        if kind == "ACQ_STALL":
            return self.set_acquisition_stall(actor, p["stalled"])
        raise ValueError(f"unknown input kind {kind}")

    # ------------------------------------------------------------------ the tick
    def view(self) -> ObservationView:
        def get(signal: str):
            point = self.cfg.point_for_signal(signal)
            s = self.samples.get(point) if point else None
            if s is None or s.quality not in GOOD:
                return None
            if s.sim_time_s is not None and self.sim_s - s.sim_time_s > STALE_AFTER_SIM_S + 1e-9:
                return None  # known acquisition age exceeds the limit: stale, not a fresh observation
            return s.value
        return ObservationView(get)

    def _acquire(self) -> dict[str, tuple[bool, str | None]]:
        status = {}
        for d in self.cfg.devices:
            ri = self.backend.read_inputs(d.device_uid)
            ro = self.backend.read_outputs(d.device_uid)
            for s in ri.samples + ro.samples:
                self.samples[s.point_id] = s
            status[d.device_uid] = (ri.ok and ro.ok, ri.error or ro.error)
        self._device_status = status
        return status

    def _edges(self) -> None:
        for pid, s in self.samples.items():
            if s.raw_unit != "bool":
                if s.quality not in GOOD:
                    self._prev_valid.pop(pid, None)
                continue
            if s.quality not in GOOD:
                if pid in self._prev_valid:
                    self._event("OBSERVATION_GAP", {"point": pid, "reason": s.reason}, None)
                self._prev_valid.pop(pid, None)
                continue
            prev = self._prev_valid.get(pid)
            if prev is not None and prev != s.value:
                kind = "DO_EDGE" if ".DO" in pid else "DI_EDGE"
                self._event(kind, {"point": pid, "from": prev, "to": s.value,
                                   "resolution_s": self.params.dt_s, "origin": s.origin.value}, None)
            self._prev_valid[pid] = s.value

    def _acquisition_age_alarm(self) -> None:
        ages = [self.sim_s - s.sim_time_s for s in self.samples.values() if s.sim_time_s is not None]
        if ages and max(ages) > STALE_AFTER_SIM_S + 1e-9:
            self.alarms.raise_or_update("ACQUISITION_STALE", "acquisition", severity="WARNING", sim_s=self.sim_s,
                                        message=f"newest data is {max(ages):.1f}s old (simulated time)")
        else:
            self.alarms.clear("ACQUISITION_STALE", "acquisition")

    def _plausibility(self) -> None:
        """Model-deviation suspicion: a flow observation that the observed equipment cannot explain.
        A steady valid reading alone is never declared 'frozen'; this only flags inconsistency."""
        v = self.view().get
        any_running = any(v(f"pump.{p}.running") for p in self.params.pumps)
        for sec in self.plant.sectors:
            flow = v(f"sector.{sec}.flow_transmitter")
            key = f"sector.{sec}"
            if flow is not None and flow >= self.params.auto.min_flow_L_min and not any_running:
                since = self._implausible_since.setdefault(key, self.sim_s)
                if self.sim_s - since + 1e-9 >= PLAUSIBILITY_SUSTAIN_S:
                    self.alarms.raise_or_update("FLOW_PLAUSIBILITY", key, severity="WARNING", sim_s=self.sim_s,
                                                value=flow, message=f"{sec}: flow {flow:.2f} L/min reported with no "
                                                "pump running feedback (model-deviation suspicion)")
            else:
                self._implausible_since.pop(key, None)
                self.alarms.clear("FLOW_PLAUSIBILITY", key)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self._apply_due_inputs()
            for point, ov in list(self.backend.state.overrides.items()):
                if ov.expires_sim_s is not None and ov.expires_sim_s <= self.sim_s + 1e-9:
                    self.backend.clear_override(point)
                    self._event("OVERRIDE_EXPIRED", {"point": point}, "system")
            self.plant.step()
            self._obs = observe(self.plant, self.rng)
            self._cycle += 1
            if not self.acquisition_stalled:
                status = self._acquire()
                self._edges()
                self.alarms.evaluate_devices(status, self.sim_s)
                self.alarms.evaluate_samples(self.samples, self.sim_s, self.sim_s)
            self._acquisition_age_alarm()
            self._plausibility()
            self.controller.tick(self.sim_s, self.view())
            if self.history is not None and self.tick >= self._next_history_tick:
                self._next_history_tick = self.tick + self.stride_ticks
                self.history.cycle(self, list(self.samples.values()))
                self.history.alarms(self.alarms.take_dirty())
            r = self.plant.ledger_residual_L()
            self.max_abs_residual_L = max(self.max_abs_residual_L, abs(r))
            if abs(r) > LEDGER_TOL_ABS_L + LEDGER_TOL_PER_TICK_L * self.tick:
                self.invariant_failures.append(InvariantFailure(self.tick, r))
                self._event("INVARIANT_VIOLATION", {"residual_L": r}, "system")

    def _apply_due_inputs(self) -> None:
        while self._scheduled and self._scheduled[0][0] <= self.tick:
            _, _, kind, actor, payload = self._scheduled.pop(0)
            try:
                self.apply(kind, actor, payload)
            except Exception as e:  # a scenario step that is rejected is itself an observable outcome
                self._event("SCENARIO_STEP_FAILED", {"kind": kind, "error": str(e)}, actor)

    def run_for(self, seconds: float) -> None:
        self.step(int(round(seconds / self.params.dt_s)))

    def state_hash(self) -> str:
        import hashlib
        h = hashlib.sha256(self.plant.state_hash().encode())
        h.update(repr((self.controller.state.value, self.controller.phase.value, self.controller.sector_idx,
                       sorted(self.backend.state.coils.items()))).encode())
        return h.hexdigest()

    def snapshot(self) -> dict:
        return {"run_id": self.run_id, "seed": self.seed, "tick": self.tick, "sim_s": self.sim_s,
                "source": self.backend.source.value, "plant": self.plant.snapshot(),
                "controller": self.controller.snapshot(),
                "overrides": {k: v.describe(None) for k, v in self.backend.state.overrides.items()},
                "device_faults": dict(self.backend.state.device_faults),
                "devices": {k: {"ok": v[0], "error": v[1]} for k, v in self._device_status.items()},
                "max_abs_ledger_residual_L": self.max_abs_residual_L,
                "acquisition_stalled": self.acquisition_stalled,
                "invariant_failures": len(self.invariant_failures), "provenance": self.params.provenance}


def replay(cfg: InstallationConfig, seed: int, journal: list[JournalEntry], until_tick: int,
           **kw) -> SimulationRuntime:
    """Re-run a recorded journal on a fresh run. No new physical path exists: the runtime is virtual."""
    rt = SimulationRuntime(cfg, seed=seed, **kw)
    entries = sorted(journal, key=lambda e: (e.tick, e.seq))
    i = 0
    while rt.tick < until_tick or (i < len(entries) and entries[i].tick <= rt.tick):
        while i < len(entries) and entries[i].tick <= rt.tick:
            e = entries[i]
            try:
                rt.apply(e.kind, e.actor, e.payload)
            except Exception:
                pass  # the original attempt failed identically; its failure is part of the record
            i += 1
        if rt.tick >= until_tick:
            break
        rt.step()
    return rt
