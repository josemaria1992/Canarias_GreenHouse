"""Simulation runtime for the installation topology (ADR-0013, ADR-0014).

Same determinism contract as `SimulationRuntime`: one step = one fixed tick; every external input is
journaled with (tick, seq) and applied between ticks; replay reproduces the state hash. Differences:

* the plant is `InstallationPlant` (5 tanks, 13 channels, shared drive, synthetic pH/EC);
* there is no single controller: a `Supervisor` runs the process controllers every control period
  (a whole number of ticks), in a fixed order;
* raw operator output commands are refused on the TWIN source: outputs belong to the processes and
  are moved only by their AUTO sequences or bounded MANUAL operations. The I/O test source (all
  processes idle) keeps point-level commands for the I/O test page;
* the shared drive's run command goes through its drive adapter (SIMULATED here), audited like a
  DO command, because no VFD register map or relay wiring is confirmed.
"""
from __future__ import annotations

import copy
import hashlib

from ..config.schema import AccessPolicy, DriveAdapterConfig, InstallationConfig
from ..control.alarms import GOOD
from ..control.process_base import Ctx, Obs, Process
from ..control.recipes import MemoryRecipeBackend, RecipeStore, physical_readiness
from ..control.supervisor import MemoryReadinessBackend, ReadinessBackend, Supervisor
from ..domain.model import CommandOrigin, ControllerState, OutputCommand, Outcome, Priority, SignalSource, new_id
from .installation import InstallationPlant
from .installation_params import STOCKS, TANKS
from .observe import observe_installation
from .runtime import STALE_AFTER_SIM_S, SimulationRuntime

PLAUSIBILITY_SUSTAIN_S = 10.0     # longer than a pump's coast-down in the DEMO dynamics
PLAUSIBILITY_FLOW_L_MIN = 2.0


class InstallationRuntime(SimulationRuntime):
    def __init__(self, cfg: InstallationConfig, *, recipes: RecipeStore | None = None,
                 readiness: ReadinessBackend | None = None, **kw):
        self._recipe_arg, self._readiness_arg = recipes, readiness
        self._sig2pt = {m.signal: m.point for m in cfg.virtual_mapping}
        self.adapter_state: dict[str, bool] = {s: False for a in cfg.drive_adapters for s in a.signals}
        super().__init__(cfg, **kw)

    # ------------------------------------------------------------------ topology hooks
    def _make_plant(self):
        return InstallationPlant(self.params)

    def _observe(self) -> dict:
        return observe_installation(self.plant, self.rng)

    def _make_control(self) -> None:
        self.controller = None
        self.simulated = self.cfg.profile == AccessPolicy.SIMULATED
        self.stale_after_s = max(STALE_AFTER_SIM_S, 2.0 * self.params.control_period_s)
        self.control_stride = self.scan_stride = self.params.control_stride
        f = self.params.facts
        self.capacities = {t: f.tank_capacity(t) for t in TANKS}
        self.sector_ids = [s.sector_id for s in f.sectors]
        self.recipes = self._recipe_arg or RecipeStore(MemoryRecipeBackend(), self.capacities, self.sector_ids,
                                                       simulated=self.simulated)
        self.recipes.emit = self._event
        self.recipes.ensure_demo("system")
        self._seed_recipes = (copy.deepcopy(list(self.recipes.records.values())),
                              copy.deepcopy(self.recipes.activations))
        rb = self._readiness_arg or MemoryReadinessBackend()
        self._seed_readiness = copy.deepcopy(rb.load_stock_readiness())
        self.sup = Supervisor(f, alarms=self.alarms, emit=self._event, route=self._route,
                              start_permitted=lambda: self.start_permitted(), source_ok=self._source_ok,
                              simulated=self.simulated, readiness=rb)

    def _run_started_extra(self) -> dict:
        a = self.recipes.active()
        return {"recipe": a.ref if a else None, "recipe_digest": a.digest() if a else None,
                "stocks": {s: r.status for s, r in self.sup.stocks.items()},
                "control_period_s": self.params.control_period_s}

    def replay_kwargs(self) -> dict:
        """Fresh stores holding exactly the state this run started from (for `replay`)."""
        b = MemoryRecipeBackend()
        for r in copy.deepcopy(self._seed_recipes[0]):
            b.records[(r.recipe.recipe_id, r.recipe.version)] = r
        b.activations = copy.deepcopy(self._seed_recipes[1])
        rb = MemoryReadinessBackend()
        rb.entries = copy.deepcopy(self._seed_readiness)
        return {"recipes": RecipeStore(b, self.capacities, self.sector_ids, simulated=self.simulated),
                "readiness": rb}

    def _controller_state(self) -> ControllerState:
        states = {p.state for p in self.sup.processes}
        for s in (ControllerState.FAULT_LOCKOUT, ControllerState.AUTO, ControllerState.MANUAL):
            if s in states:
                return s
        return ControllerState.STOPPED

    def _source_ok(self) -> str | None:
        if self.backend.source != SignalSource.TWIN:
            return f"processes run only on the TWIN signal source (current {self.backend.source.value})"
        return None

    # ------------------------------------------------------------------ observations
    def _obs_get(self, signal: str):
        pt = self._sig2pt.get(signal)
        s = self.samples.get(pt) if pt else None
        if s is None or s.quality not in GOOD or s.value is None:
            return None
        if s.sim_time_s is not None and self.sim_s - s.sim_time_s > self.stale_after_s + 1e-9:
            return None
        return s.value

    def _obs_why(self, signal: str) -> str | None:
        pt = self._sig2pt.get(signal)
        if pt is None:
            return "UNBOUND"
        s = self.samples.get(pt)
        if s is None:
            return "NO_SAMPLE"
        if s.quality not in GOOD:
            return f"{s.quality.value}: {s.reason}" if s.reason else s.quality.value
        if s.value is None:
            return "NO_VALUE"
        if s.sim_time_s is not None and self.sim_s - s.sim_time_s > self.stale_after_s + 1e-9:
            return f"STALE ({self.sim_s - s.sim_time_s:.1f} s old)"
        return None

    def obs(self) -> Obs:
        return Obs(self._obs_get, self._obs_why)

    def make_ctx(self) -> Ctx:
        return Ctx(now=self.sim_s, dt=self.params.control_period_s, obs=self.obs(), recipe=self.recipes.active())

    def view(self):
        from ..control.auto import ObservationView
        return ObservationView(self._obs_get)

    def _control_tick(self) -> None:
        if self.tick % self.control_stride == 0:
            self.sup.tick(self.make_ctx())

    def _plausibility(self) -> None:
        """Model-deviation suspicion: flow that the observed equipment cannot explain."""
        v = self._obs_get
        checks = [(f"sector.{s}", f"flow.{s}", ["drive.MAIN.running"]) for s in self.sector_ids]
        checks += [(f"transfer.{s}", f"flow.TP_{s}", [f"pump.TP_{s}.running"]) for s in STOCKS]
        checks.append(("return", "flow.RETURN", [f"pump.{p}.running" for p in self.params.facts.return_pumps]))
        for key, flow_sig, run_sigs in checks:
            flow = v(flow_sig)
            runs = [v(r) for r in run_sigs]
            if flow is not None and flow >= PLAUSIBILITY_FLOW_L_MIN and runs and all(r is False for r in runs):
                since = self._implausible_since.setdefault(key, self.sim_s)
                if self.sim_s - since + 1e-9 >= PLAUSIBILITY_SUSTAIN_S:
                    self.alarms.raise_or_update("FLOW_PLAUSIBILITY", key, severity="WARNING", sim_s=self.sim_s,
                                                value=flow, message=f"{flow_sig} {flow:.2f} L/min with no running "
                                                f"feedback from {run_sigs} (model-deviation suspicion)")
            else:
                self._implausible_since.pop(key, None)
                self.alarms.clear("FLOW_PLAUSIBILITY", key)

    # ------------------------------------------------------------------ command routing
    def _policy(self, cmd: OutputCommand) -> str | None:
        if cmd.desired:
            ok, why = self.start_permitted()
            if not ok:
                return f"new starts inhibited: {why}"
        if cmd.origin in (CommandOrigin.CONTROLLER, CommandOrigin.SYSTEM):
            if self.backend.source != SignalSource.TWIN:
                return "process commands only on the TWIN source"
            return None
        if self.backend.source == SignalSource.TWIN:
            return ("raw output commands are not available on the installation twin: outputs belong to the "
                    "process controllers; use a process MANUAL operation, or switch to the I/O test source")
        if not self.sup.all_idle():
            return "I/O test commands need every process STOPPED and idle"
        return None

    def _route(self, proc: Process, signal: str, on: bool, reason: str, priority: Priority) -> bool:
        point = self._sig2pt.get(signal)
        if point is not None:
            cmd = self.commands.build(actor="controller", target=point, desired=on, origin=CommandOrigin.CONTROLLER,
                                      reason=reason, priority=priority)
            rec = self.commands.submit(cmd)
            if rec.outcome != Outcome.READBACK_CONFIRMED:
                self._event("COMMAND_NOT_CONFIRMED", {"process": proc.pid, "signal": signal, "target": point,
                                                      "desired": on, "outcome": rec.outcome.value,
                                                      "detail": rec.detail}, "controller")
                return False
            return True
        ad = self.cfg.adapter_for_signal(signal)
        if ad is not None:
            return self._adapter_command(ad, proc, signal, on, reason)
        self._event("COMMAND_UNBOUND", {"process": proc.pid, "signal": signal, "desired": on,
                                        "reason": "no DO point or drive adapter is bound to this signal"}, "controller")
        return False

    def _adapter_command(self, ad: DriveAdapterConfig, proc: Process, signal: str, on: bool, reason: str) -> bool:
        rid = new_id("adp")
        base = {"request_id": rid, "adapter": ad.adapter_id, "kind": ad.kind, "signal": signal, "desired": on,
                "process": proc.pid, "reason": reason, "run_id": self.run_id, "tick": self.tick}
        if ad.kind != "SIMULATED" or self.backend.source != SignalSource.TWIN:
            self._event("DRIVE_ADAPTER_COMMAND_REJECTED", base | {"detail": f"adapter {ad.kind} on source "
                                                                  f"{self.backend.source.value}"}, "controller")
            return False
        if on:
            ok, why = self.start_permitted()
            if not ok:
                self._event("DRIVE_ADAPTER_COMMAND_REJECTED", base | {"detail": f"new starts inhibited: {why}"},
                            "controller")
                return False
        try:
            self.audit.event("DRIVE_ADAPTER_COMMAND_INTENT", base, actor="controller", sim_s=self.sim_s)
        except Exception as e:
            if on:
                self._event("DRIVE_ADAPTER_COMMAND_REJECTED", base | {"detail": f"durable audit unavailable: {e}"},
                            "controller")
                return False
        self.plant.command(signal, on)
        self.adapter_state[signal] = on
        out = base | {"outcome": "SIMULATED_APPLIED", "readback": self.plant.drive.run_cmd}
        self._event("DRIVE_ADAPTER_COMMAND", out, "controller")
        return True

    # ------------------------------------------------------------------ external inputs (journaled)
    def process_request(self, actor: str, pid: str, target: ControllerState) -> tuple[bool, str]:
        self._record("PROCESS_REQUEST", actor, {"process": pid, "target": target.value})
        return self.sup.request(pid, target, actor, self.make_ctx())

    def process_manual(self, actor: str, pid: str, op: dict) -> tuple[bool, str]:
        self._record("PROCESS_MANUAL", actor, {"process": pid, "op": dict(op)})
        return self.sup.manual(pid, dict(op), actor, self.make_ctx())

    def process_reset(self, actor: str, pid: str) -> tuple[bool, str]:
        self._record("PROCESS_RESET", actor, {"process": pid})
        ctx = self.make_ctx()
        p = self.sup.process(pid)
        if p.fault_reason == "SAFETY_CHAIN_OPEN" and ctx.obs.get("safety.chain_ok") is not True:
            msg = "safety chain not reported closed"
            self._event("PROCESS_RESET_REJECTED", {"process": pid, "reason": msg}, actor)
            return False, msg
        ok, msg = self.sup.reset(pid, actor, ctx)
        self.persist_alarm_transitions()
        return ok, msg

    def stop_all(self, actor: str, reason: str = "stop all") -> list[dict]:
        self._record("STOP_ALL", actor, {"reason": reason})
        return self.sup.stop_all(actor, self.make_ctx(), reason)

    def recipe_save_draft(self, actor: str, recipe_id: str, name: str, body: dict, *, version: int | None = None,
                          note: str | None = None):
        self._record("RECIPE_DRAFT", actor, {"recipe_id": recipe_id, "name": name, "body": body, "version": version,
                                             "note": note})
        return self.recipes.save_draft(actor, recipe_id, name, body, version=version, note=note)

    def recipe_validate(self, actor: str, recipe_id: str, version: int):
        self._record("RECIPE_VALIDATE", actor, {"recipe_id": recipe_id, "version": version})
        return self.recipes.validate(recipe_id, version, actor)

    def recipe_activate(self, actor: str, recipe_id: str, version: int, reason: str = ""):
        self._record("RECIPE_ACTIVATE", actor, {"recipe_id": recipe_id, "version": version, "reason": reason})
        return self.recipes.activate(recipe_id, version, actor, reason=reason)

    def recipe_rollback(self, actor: str, reason: str):
        self._record("RECIPE_ROLLBACK", actor, {"reason": reason})
        return self.recipes.rollback(actor, reason)

    def stock_prepare(self, actor: str, stock: str) -> dict:
        """Twin action: the operator's manual preparation, as synthetic species (simulation only)."""
        if not self.simulated:
            raise PermissionError("stock preparation in the twin exists only in a SIMULATED profile")
        self._record("STOCK_PREPARE", actor, {"stock": stock})
        out = self.plant.prepare_stock(stock)
        self.sup.invalidate_stock(stock, "twin: stock tank prepared again (acknowledge the new preparation)", actor)
        self._event("TWIN_STOCK_PREPARED", out, actor)
        return out

    def stock_acknowledge(self, actor: str, stock: str, note: str = "") -> tuple[bool, str]:
        self._record("STOCK_ACK", actor, {"stock": stock, "note": note})
        ok, msg = self.sup.acknowledge_stock(stock, actor, note, self.make_ctx())
        if not ok:
            self._event("STOCK_READINESS_REJECTED", {"stock": stock, "reason": msg}, actor)
        return ok, msg

    def stock_invalidate(self, actor: str, stock: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("a reason is required")
        self._record("STOCK_INVALIDATE", actor, {"stock": stock, "reason": reason})
        self.sup.invalidate_stock(stock, f"operator: {reason}", actor)

    def set_outlet(self, actor: str, channel: int, outlet: int, opening: float) -> None:
        self.plant.set_outlet(int(channel), int(outlet), float(opening))
        self._record("OUTLET", actor, {"channel": int(channel), "outlet": int(outlet), "opening": float(opening)})
        self._event("TWIN_OUTLET_ADJUSTED", {"channel": int(channel), "outlet": int(outlet),
                                             "opening": float(opening)}, actor)

    def request_state(self, actor: str, target: ControllerState) -> tuple[bool, str]:
        if target == ControllerState.STOPPED:
            res = self.stop_all(actor, "stop requested")
            bad = [r for r in res if not r["ok"]]
            return (not bad), "; ".join(f"{r['process']}: {r['detail']}" for r in res)
        self._record("STATE_REQUEST", actor, {"target": target.value})
        msg = "the installation has one controller per process: start AUTO or MANUAL per process"
        self._event("STATE_REQUEST_REJECTED", {"target": target.value, "reason": msg}, actor)
        return False, msg

    def reset_lockout(self, actor: str) -> tuple[bool, str]:
        self._record("RESET", actor, {})
        msg = "reset each process in FAULT_LOCKOUT individually (process reset)"
        self._event("RESET_REJECTED", {"reason": msg}, actor)
        return False, msg

    def reset_alarm(self, actor: str, rule_id: str, target: str):
        occ = self.alarms.open.get((rule_id, target))
        if occ is not None and any(p.fault_occurrence_id == occ.occurrence_id for p in self.sup.processes):
            self._record("ALARM_RESET", actor, {"rule_id": rule_id, "target": target})
            raise ValueError("this alarm holds a process in FAULT_LOCKOUT: reset it through the process reset")
        return super().reset_alarm(actor, rule_id, target)

    def set_source(self, actor: str, source: SignalSource) -> tuple[bool, str]:
        if source == self.backend.source:
            return True, "unchanged"
        if not self.sup.all_idle():
            return False, "change signal source only while every process is STOPPED and idle"
        on = [p for p, v in self.backend.state.coils.items() if v] + [s for s, v in self.adapter_state.items() if v]
        if on:
            return False, f"switch all virtual outputs OFF first: {on}"
        self._record("SOURCE", actor, {"source": source.value})
        self.backend.source = source
        self._event("SIGNAL_SOURCE_CHANGED", {"source": source.value}, actor)
        self._acquire()
        return True, source.value

    def apply(self, kind: str, actor: str, p: dict):
        if kind == "PROCESS_REQUEST":
            return self.process_request(actor, p["process"], ControllerState(p["target"]))
        if kind == "PROCESS_MANUAL":
            return self.process_manual(actor, p["process"], p["op"])
        if kind == "PROCESS_RESET":
            return self.process_reset(actor, p["process"])
        if kind == "STOP_ALL":
            return self.stop_all(actor, p.get("reason", "stop all"))
        if kind == "RECIPE_DRAFT":
            return self.recipe_save_draft(actor, p["recipe_id"], p["name"], p["body"], version=p.get("version"),
                                          note=p.get("note"))
        if kind == "RECIPE_VALIDATE":
            return self.recipe_validate(actor, p["recipe_id"], p["version"])
        if kind == "RECIPE_ACTIVATE":
            return self.recipe_activate(actor, p["recipe_id"], p["version"], p.get("reason", ""))
        if kind == "RECIPE_ROLLBACK":
            return self.recipe_rollback(actor, p.get("reason", "rollback"))
        if kind == "STOCK_PREPARE":
            return self.stock_prepare(actor, p["stock"])
        if kind == "STOCK_ACK":
            return self.stock_acknowledge(actor, p["stock"], p.get("note", ""))
        if kind == "STOCK_INVALIDATE":
            return self.stock_invalidate(actor, p["stock"], p["reason"])
        if kind == "OUTLET":
            return self.set_outlet(actor, p["channel"], p["outlet"], p["opening"])
        return super().apply(kind, actor, p)

    # ------------------------------------------------------------------ state
    def state_hash(self) -> str:
        h = hashlib.sha256(self.plant.state_hash().encode())
        a = self.recipes.active()
        h.update(repr((self.sup.fingerprint(), sorted(self.backend.state.coils.items()),
                       sorted(self.adapter_state.items()), a.ref if a else None)).encode())
        return h.hexdigest()

    def snapshot(self) -> dict:
        a = self.recipes.active()
        return {"run_id": self.run_id, "seed": self.seed, "tick": self.tick, "sim_s": self.sim_s,
                "topology": "installation", "source": self.backend.source.value, "plant": self.plant.snapshot(),
                "controller": None, "aggregate_state": self._controller_state().value,
                "supervisor": self.sup.snapshot(),
                "recipe": {"active": a.ref if a else None, "name": a.name if a else None,
                           "provenance": a.provenance if a else None,
                           "physical_readiness": physical_readiness(a, profile_simulated=self.simulated)},
                "drive_adapters": [{"adapter_id": d.adapter_id, "kind": d.kind, "signals": d.signals,
                                    "state": {s: self.adapter_state.get(s) for s in d.signals}}
                                   for d in self.cfg.drive_adapters],
                "overrides": {k: v.describe(None) for k, v in self.backend.state.overrides.items()},
                "device_faults": dict(self.backend.state.device_faults),
                "devices": {k: {"ok": v[0], "error": v[1]} for k, v in self._device_status.items()},
                "max_abs_ledger_residual_L": self.max_abs_residual_L,
                "acquisition_stalled": self.acquisition_stalled,
                "invariant_failures": len(self.invariant_failures), "provenance": self.params.provenance}
