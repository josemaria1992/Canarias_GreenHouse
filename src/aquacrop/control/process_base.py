"""Process-controller framework (brief §3, §4; ADR-0014).

Each installation process (irrigation, refill, acid, return, stock filling) is its own controller with
its own STOPPED / MANUAL / AUTO / FAULT_LOCKOUT state, phase, permissives and fault latch. A shared
arbiter grants exclusive use of actuators and shared resources (a stock tank), so one process can never
command another process's equipment, and an unrelated mode change never starts or stops another process.

Controllers see only tagged observations (the same scaled samples the UI shows) and act only through
audited commands. Acknowledgement, reset and restart are three different operator actions:
acknowledge an alarm; reset a FAULT_LOCKOUT to STOPPED once the condition has cleared, the fault alarm
is acknowledged and every owned output is verified off; restart with a deliberate AUTO request.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..domain.model import ControllerState, Priority

if TYPE_CHECKING:
    from .recipes import Recipe
    from .supervisor import Supervisor

FLOW_ZERO_L_MIN = 0.5            # below this a meter reading counts as "no flow"
VALVE_NO_FEEDBACK_WAIT_S = 2.0   # path valve without position feedback: wait, then prove by flow/level
STOP_VERIFY_TIMEOUT_S = 10.0


class Obs:
    """Observation access by logical signal. `get` returns None for anything unusable; `why` says why."""

    def __init__(self, get: Callable[[str], Any], why: Callable[[str], str | None]):
        self._get, self._why = get, why

    def get(self, signal: str):
        return self._get(signal)

    def why(self, signal: str) -> str | None:
        return self._why(signal)

    def bound(self, signal: str) -> bool:
        return self._why(signal) != "UNBOUND"

    def num(self, signal: str) -> float | None:
        v = self._get(signal)
        if v is None or isinstance(v, bool) or not math.isfinite(float(v)):
            return None
        return float(v)


@dataclass
class Ctx:
    now: float                       # simulated seconds, monotonic within a run
    dt: float                        # control period
    obs: Obs
    recipe: "Recipe | None"          # currently active recipe (processes snapshot it at cycle/batch start)


class Arbiter:
    """Exclusive ownership of actuators and shared resources between processes."""

    def __init__(self) -> None:
        self.owner: dict[str, str] = {}

    def acquire(self, pid: str, resources: list[str]) -> str | None:
        busy = [f"{r} (owned by {self.owner[r]})" for r in resources if self.owner.get(r, pid) != pid]
        if busy:
            return "resource in use: " + ", ".join(busy)
        for r in resources:
            self.owner[r] = pid
        return None

    def release(self, pid: str, resources: list[str] | None = None) -> None:
        for r in list(self.owner):
            if self.owner[r] == pid and (resources is None or r in resources):
                del self.owner[r]

    def held_by(self, pid: str) -> list[str]:
        return sorted(r for r, o in self.owner.items() if o == pid)


@dataclass
class Transition:
    sim_s: float
    from_state: str
    to_state: str
    from_phase: str
    to_phase: str
    reason: str
    actor: str
    recipe: str | None


class Process:
    pid = "PROCESS"
    title = "Process"
    auto_capable = True
    # fault reasons whose own cause the reset checks observe while the process is stopped (fault_cleared and the
    # feedback check); any other cause (a valve that did not open, no flow) shows itself only on the next attempt
    checkable_faults: frozenset[str] = frozenset({"STOP_NOT_VERIFIED"})

    def __init__(self, sup: "Supervisor"):
        self.sup = sup
        self.state = ControllerState.STOPPED
        self.phase = "IDLE"
        self.phase_since = 0.0
        self.now = 0.0
        self.fault_reason: str | None = None
        self.fault_detail: str | None = None
        self.fault_occurrence_id: str | None = None
        self.recipe: Recipe | None = None          # effective version for the current cycle/batch/op
        self.manual_op: dict | None = None
        self.permissives: dict[str, dict] = {}
        self.inhibit: str | None = None            # why AUTO is waiting (not a fault)
        self.next_step: str = ""
        self.history: list[Transition] = []
        self.on: set[str] = set()                  # actuator signals this process has commanded ON
        self.stopping = False
        self._stop_target = ControllerState.STOPPED
        self._ctx: Ctx | None = None
        self._stop_t0 = 0.0
        self.last_result: dict | None = None
        self.counters: dict[str, Any] = {}

    # ------------------------------------------------------------------ identity / helpers
    @property
    def resources(self) -> list[str]:
        return []

    @property
    def recipe_ref(self) -> str | None:
        return self.recipe.ref if self.recipe else None

    def _to(self, state: ControllerState | None, phase: str | None, reason: str, actor: str = "controller") -> None:
        tr = Transition(self.now, self.state.value, (state or self.state).value, self.phase, phase or self.phase,
                        reason, actor, self.recipe_ref)
        self.history.append(tr)
        del self.history[:-200]
        if state is not None:
            self.state = state
        if phase is not None:
            self.phase, self.phase_since = phase, self.now
        self.sup.emit("PROCESS_TRANSITION", {"process": self.pid} | tr.__dict__, actor)

    def elapsed(self) -> float:
        return self.now - self.phase_since + 1e-9

    def cmd(self, signal: str, on: bool, reason: str) -> bool:
        ok = self.sup.issue(self, signal, on, reason, Priority.NORMAL if on else Priority.STOP)
        if on and ok:
            self.on.add(signal)
        if not on:
            self.on.discard(signal)
        return ok

    def perm(self, name: str, ok: bool, detail: str = "") -> bool:
        self.permissives[name] = {"ok": bool(ok), "detail": detail}
        return bool(ok)

    def actuating(self) -> bool:
        return bool(self.on) or self.stopping or self.phase not in ("IDLE", "MONITOR", "WAIT")

    # ------------------------------------------------------------------ operator requests
    def request(self, target: ControllerState, actor: str, ctx: Ctx) -> tuple[bool, str]:
        self._ctx = ctx
        s = self.state
        if target == ControllerState.STOPPED:
            if s == ControllerState.FAULT_LOCKOUT:
                return False, "in FAULT_LOCKOUT: acknowledge the fault alarm and reset instead"
            if s == ControllerState.STOPPED and not self.actuating():
                return True, "already stopped"
            self.begin_stop(ControllerState.STOPPED, f"stop requested by {actor}", actor)
            return True, "stopping"
        if s == ControllerState.FAULT_LOCKOUT:
            return False, "in FAULT_LOCKOUT: acknowledge, reset, then start deliberately"
        if self.stopping:
            return False, "stop sequence still running"
        if target == ControllerState.MANUAL:
            if s == ControllerState.MANUAL:
                return True, "already MANUAL"
            if s != ControllerState.STOPPED:
                return False, f"MANUAL only from STOPPED (current {s.value}); stop first"
            why = self.sup.mode_blockers(self, ctx)
            if why:
                return False, "; ".join(why)
            self.recipe = ctx.recipe
            self._to(ControllerState.MANUAL, "IDLE", f"MANUAL by {actor}", actor)
            return True, "manual"
        if target == ControllerState.AUTO:
            if not self.auto_capable:
                return False, f"{self.title} has no AUTO mode"
            if s == ControllerState.AUTO:
                return True, "already AUTO"
            if s != ControllerState.STOPPED:
                return False, f"AUTO only from STOPPED (current {s.value}); stop MANUAL first so no manual " \
                              "operation is adopted"
            if self.on:
                return False, f"outputs still ON {sorted(self.on)}; AUTO will not adopt equipment"
            why = self.sup.start_blockers(self, ctx, auto=True) + self.auto_blockers(ctx)
            if why:
                return False, "; ".join(why)
            self.recipe = ctx.recipe
            self.on_auto_start(ctx)
            self._to(ControllerState.AUTO, self.auto_entry_phase(), f"AUTO started by {actor} with recipe "
                     f"{self.recipe_ref}", actor)
            return True, "auto"
        return False, f"unsupported target {target.value}"

    def manual_request(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        self._ctx = ctx
        if self.state != ControllerState.MANUAL:
            return False, f"bounded manual operations need MANUAL (current {self.state.value})"
        if self.stopping or self.manual_op is not None:
            return False, "a manual operation is already running; stop it first"
        why = self.sup.start_blockers(self, ctx, auto=False)
        if why:
            return False, "; ".join(why)
        self.recipe = ctx.recipe
        if self.recipe is None:
            return False, "no active recipe: limits for manual operations come from the active recipe"
        ok, msg = self.start_manual(op, actor, ctx)
        if ok:
            self.manual_op = {"op": op, "actor": actor, "started_s": self.now, "recipe": self.recipe_ref,
                              "bounds": msg}
            self.sup.emit("PROCESS_MANUAL_OP_STARTED", {"process": self.pid, "op": op, "bounds": msg,
                                                        "recipe": self.recipe_ref}, actor)
        return ok, msg

    def reset(self, actor: str, ctx: Ctx) -> tuple[bool, str]:
        self._ctx = ctx
        if self.state != ControllerState.FAULT_LOCKOUT:
            return False, "not in FAULT_LOCKOUT"
        if self.stopping:
            return False, "stop sequence still running"
        ok, why = self.fault_cleared(ctx)
        if not ok:
            return False, f"fault condition still present: {why}"
        if self.on:
            return False, f"outputs still commanded ON: {sorted(self.on)}"
        bad = self.outputs_feedback_active(ctx)
        if bad:
            return False, f"equipment still reports activity: {bad}"
        occ = self.sup.alarms.get(self.fault_occurrence_id) if self.fault_occurrence_id else None
        if occ is not None:
            if not occ.acknowledged:
                return False, "acknowledge the fault alarm before reset"
            self.sup.alarms.clear(occ.rule_id, occ.target)
            self.sup.alarms.reset(occ.occurrence_id, actor)
        was = self.fault_reason
        self.fault_reason = self.fault_detail = self.fault_occurrence_id = None
        self.sup.arbiter.release(self.pid)
        self.on_reset()
        self._to(ControllerState.STOPPED, "IDLE", f"lockout reset by {actor} (was {was}); restart requires a "
                 "deliberate AUTO or MANUAL request", actor)
        return True, "reset; process STOPPED"

    # ------------------------------------------------------------------ faults and stopping
    def fault(self, reason: str, detail: str, occurrence=None) -> None:
        if self.state == ControllerState.FAULT_LOCKOUT:
            return
        occ = occurrence or self.sup.alarms.raise_or_update(
            f"PROC_{reason}", f"process.{self.pid}", severity="CRITICAL", latch=True,
            message=f"{self.title}: {reason}: {detail}", sim_s=self.now)
        self.fault_reason, self.fault_detail, self.fault_occurrence_id = reason, detail, occ.occurrence_id
        self.begin_stop(ControllerState.FAULT_LOCKOUT, f"FAULT {reason}: {detail}", "controller")

    def begin_stop(self, target: ControllerState, reason: str, actor: str) -> None:
        self._stop_target = target
        self.stopping = True
        self.next_step, self.inhibit = "", None          # a stale plan must not outlive the stop
        if self.manual_op is not None:
            self.finish_manual("STOPPED", reason)
        self._to(target, "STOPPING", reason, actor)
        self.start_stop()

    def finish_manual(self, result: str, reason: str) -> None:
        op, self.manual_op = self.manual_op, None
        if op is None:
            return
        self.last_result = {"kind": "manual", "result": result, "reason": reason, "op": op["op"],
                            "recipe": op["recipe"], "end_s": self.now}
        self.sup.emit("PROCESS_MANUAL_OP_ENDED", {"process": self.pid, "result": result, "reason": reason,
                                                  "op": op["op"], "recipe": op["recipe"]}, op["actor"])

    def tick(self, ctx: Ctx) -> None:
        self.now, self._ctx = ctx.now, ctx
        if self.stopping:
            if self.stop_tick(ctx):
                self.stopping = False
                self.sup.arbiter.release(self.pid)
                self._to(None, "IDLE", "stop sequence complete" + ("" if not self.on else f"; still ON {sorted(self.on)}"))
            return
        if self.state == ControllerState.AUTO:
            self.auto_tick(ctx)
        elif self.state == ControllerState.MANUAL and self.manual_op is not None:
            self.manual_tick(ctx)

    # ------------------------------------------------------------------ hooks
    def auto_blockers(self, ctx: Ctx) -> list[str]:
        return []

    def auto_entry_phase(self) -> str:
        return "MONITOR"

    def on_auto_start(self, ctx: Ctx) -> None:
        pass

    def on_reset(self) -> None:
        pass

    def auto_tick(self, ctx: Ctx) -> None:
        pass

    def start_manual(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        return False, "no manual operations"

    def manual_tick(self, ctx: Ctx) -> None:
        pass

    def start_stop(self) -> None:
        """Begin bringing owned outputs to their safe state (default: pumps off now, valves after 1 s)."""
        self._stop_t0 = self.now
        for sig in sorted(self.on):
            if sig.startswith("pump.") or sig.startswith("drive."):
                self.cmd(sig, False, "stop sequence: pumps off")

    def stop_tick(self, ctx: Ctx) -> bool:
        if self.now - self._stop_t0 + 1e-9 >= 1.0:
            for sig in sorted(self.on):
                self.cmd(sig, False, "stop sequence: valves closed")
        if self.on:
            return False
        active = self.outputs_feedback_active(ctx)
        if active and self.now - self._stop_t0 < STOP_VERIFY_TIMEOUT_S:
            return False
        if active and self.state != ControllerState.FAULT_LOCKOUT:
            self.stopping = False
            self.fault("STOP_NOT_VERIFIED", f"still active after the stop sequence: {active}")
            return False
        return True

    def outputs_feedback_active(self, ctx: Ctx) -> list[str]:
        """Observed signs that this process's equipment is still running/flowing (bound signals only)."""
        return []

    def fault_cleared(self, ctx: Ctx) -> tuple[bool, str]:
        return True, ""

    def reset_blocker(self) -> str | None:
        """Why a lockout reset would be refused right now, from the same checks as reset() on the last tick's
        observations, changing nothing; None when nothing is locked out or the reset would pass. Shown next to
        the reset action so the operator sees why it is not available yet."""
        if self.state != ControllerState.FAULT_LOCKOUT:
            return None
        ctx = self._ctx
        if ctx is None:
            return "no observation yet"
        if self.stopping:
            return "stop sequence still running"
        ok, why = self.fault_cleared(ctx)
        if not ok:
            return f"fault condition still present: {why}"
        if self.on:
            return f"outputs still commanded ON: {sorted(self.on)}"
        bad = self.outputs_feedback_active(ctx)
        if bad:
            return f"equipment still reports activity: {bad}"
        occ = self.sup.alarms.get(self.fault_occurrence_id) if self.fault_occurrence_id else None
        if occ is not None and not occ.acknowledged:
            return "acknowledge the fault alarm before reset"
        return None

    def fault_present(self) -> str | None:
        """While locked out: the fault condition the reset checks, if it is still there (even while the stop
        sequence runs), else None. Read-only, for the recovery steps on the page."""
        if self.state != ControllerState.FAULT_LOCKOUT or self._ctx is None:
            return None
        ok, why = self.fault_cleared(self._ctx)
        return None if ok else why

    def fault_checkable(self) -> bool | None:
        """While locked out: whether the reset's checks can see the fault's own cause while stopped (True), or
        cannot (False: passing them does not show the cause is gone); None otherwise. Read-only, for the
        recovery wording; it changes no check."""
        if self.state != ControllerState.FAULT_LOCKOUT or self.fault_reason is None:
            return None
        return self.fault_reason in self.checkable_faults

    def view(self) -> dict:
        return {"process": self.pid, "title": self.title, "state": self.state.value, "phase": self.phase,
                "phase_since_s": self.phase_since, "fault_reason": self.fault_reason, "fault_detail": self.fault_detail,
                "fault_occurrence_id": self.fault_occurrence_id, "recipe": self.recipe_ref,
                "manual_op": self.manual_op, "permissives": self.permissives, "inhibit": self.inhibit,
                "next": self.next_step, "outputs_on": sorted(self.on), "stopping": self.stopping,
                "resources": self.sup.arbiter.held_by(self.pid), "last_result": self.last_result,
                "auto_capable": self.auto_capable, "counters": self.counters,
                "reset_blocker": self.reset_blocker(), "fault_present": self.fault_present(),
                "fault_checkable": self.fault_checkable(),
                "history": [t.__dict__ for t in self.history[-25:]]}


# ------------------------------------------------------------------------------------------------
@dataclass
class Path:
    """One bounded transfer: optional pump, a valve, measured by a flow meter (integrated) or, without a
    meter, by the destination/source level. Missing measurement means the delivered volume is unverified:
    the path fails, it never counts as delivered."""
    proc: Process
    name: str
    valve: str
    pump: str | None = None
    run_feedback: str | None = None
    open_feedback: str | None = None
    flow: str | None = None
    level: str | None = None              # level-rise verification when no meter (stock fills)
    target_L: float | None = None
    max_time_s: float = 600.0
    min_flow_L_min: float = 1.0
    grace_s: float = 10.0
    rise_L: float | None = None
    rise_window_s: float | None = None
    valve_timeout_s: float = 5.0
    run_timeout_s: float = 3.0
    stop_when: Callable[[Ctx], str | None] | None = None
    extra_resources: list[str] = field(default_factory=list)
    state: str = "PENDING"                # PENDING OPENING STARTING RUNNING STOPPING DONE FAILED
    result: str | None = None             # TARGET_REACHED / guard reason / failure reason
    failed: bool = False
    delivered_L: float = 0.0
    t0: float = 0.0
    run_t0: float = 0.0
    phase_t0: float = 0.0
    low_since: float | None = None
    level0: float | None = None
    level_start_t: float = 0.0
    lv_mark: float = 0.0
    _fail_after_stop: str | None = None

    @property
    def outputs(self) -> list[str]:
        return [x for x in (self.pump, self.valve) if x]

    def measurement_problem(self, obs: Obs) -> str | None:
        if self.flow and obs.bound(self.flow):
            return None
        if self.level and obs.bound(self.level) and self.rise_L and self.rise_window_s:
            return None
        return f"{self.name}: no flow meter or level verification bound; delivered volume cannot be measured"

    def start(self, ctx: Ctx) -> str | None:
        why = self.measurement_problem(ctx.obs)
        if why:
            return why
        why = self.proc.sup.arbiter.acquire(self.proc.pid, self.outputs + self.extra_resources)
        if why:
            return why
        self.t0 = self.phase_t0 = ctx.now
        self.state = "OPENING"
        if not self.proc.cmd(self.valve, True, f"{self.name}: open"):
            self._finish(ctx, "COMMAND_REJECTED", failed=True)
            return "open command rejected"
        return None

    def _flow(self, obs: Obs) -> float | None:
        return obs.num(self.flow) if self.flow else None

    def tick(self, ctx: Ctx) -> None:
        obs, now = ctx.obs, ctx.now
        if self.state in ("DONE", "FAILED", "PENDING"):
            return
        q = self._flow(obs)
        if q is not None:      # whatever the meter reports while the path holds its valve counts as delivered,
            self.delivered_L += q * ctx.dt / 60.0      # including the pump's start-up and stopping coast
        if self.state == "OPENING":
            if self.open_feedback and obs.bound(self.open_feedback):
                v = obs.get(self.open_feedback)
                if v:
                    self._started(ctx)
                elif now - self.phase_t0 + 1e-9 >= self.valve_timeout_s:
                    self.stop(ctx, "VALVE_OPEN_TIMEOUT", failed=True)
            elif now - self.phase_t0 + 1e-9 >= VALVE_NO_FEEDBACK_WAIT_S:
                self._started(ctx)
        elif self.state == "STARTING":
            if self.run_feedback and obs.bound(self.run_feedback):
                v = obs.get(self.run_feedback)
                if v is None:
                    return self.stop(ctx, "RUN_FEEDBACK_UNAVAILABLE", failed=True)
                if v:
                    self._running(ctx)
                elif now - self.phase_t0 + 1e-9 >= self.run_timeout_s:
                    self.stop(ctx, "RUN_FEEDBACK_TIMEOUT", failed=True)
            else:
                self._running(ctx)
        elif self.state == "RUNNING":
            if self.run_feedback and obs.bound(self.run_feedback) and obs.get(self.run_feedback) is False:
                return self.stop(ctx, "PUMP_STOPPED", failed=True)
            if self.flow and obs.bound(self.flow):
                if q is None:
                    return self.stop(ctx, f"FLOW_UNAVAILABLE ({obs.why(self.flow)}): delivered volume unverified",
                                     failed=True)
                if now - self.run_t0 + 1e-9 >= self.grace_s and q < self.min_flow_L_min:
                    self.low_since = self.low_since if self.low_since is not None else now
                    if now - self.low_since + 1e-9 >= 2.0:
                        return self.stop(ctx, f"NO_FLOW ({q:.2f} < {self.min_flow_L_min:g} L/min)", failed=True)
                else:
                    self.low_since = None
            else:
                lv = obs.num(self.level)
                if lv is None:
                    return self.stop(ctx, f"LEVEL_UNAVAILABLE ({obs.why(self.level)}): fill unverified", failed=True)
                if self.level0 is None:
                    self.level0 = lv
                self.delivered_L = max(0.0, lv - self.level0)
                if now - self.level_start_t + 1e-9 >= self.rise_window_s:
                    if lv - self.lv_mark < self.rise_L:
                        return self.stop(ctx, f"NO_FILL (level rose {lv - self.lv_mark:.1f} L < {self.rise_L:g} L in "
                                              f"{self.rise_window_s:g} s)", failed=True)
                    self.level_start_t, self.lv_mark = now, lv
            if self.target_L is not None and self.delivered_L + 1e-9 >= self.target_L:
                return self.stop(ctx, "TARGET_REACHED")
            if self.stop_when is not None:
                why = self.stop_when(ctx)
                if why:
                    return self.stop(ctx, why, failed=why.startswith("FAIL:"))
            if now - self.t0 + 1e-9 >= self.max_time_s:
                return self.stop(ctx, f"MAX_TIME ({self.max_time_s:g} s)", failed=True)
        elif self.state == "STOPPING":
            if self.pump in self.proc.on:
                return
            if self.valve in self.proc.on:
                if now - self.phase_t0 + 1e-9 >= 1.0 or not self.pump:
                    self.proc.cmd(self.valve, False, f"{self.name}: close")
                return
            still = []
            if self.run_feedback and obs.bound(self.run_feedback) and obs.get(self.run_feedback):
                still.append(self.run_feedback)
            if self.flow and obs.bound(self.flow) and (q is None or q > FLOW_ZERO_L_MIN):
                still.append(f"{self.flow}={q}")
            if still and now - self.phase_t0 + 1e-9 < STOP_VERIFY_TIMEOUT_S:
                return
            reason = self._fail_after_stop
            if still:
                self.failed, reason = True, (reason + "; " if reason else "") + f"STOP_NOT_VERIFIED {still}"
            self._finish(ctx, reason or self.result, failed=self.failed)

    def _started(self, ctx: Ctx) -> None:
        self.phase_t0 = ctx.now
        if self.pump:
            self.state = "STARTING"
            if not self.proc.cmd(self.pump, True, f"{self.name}: start"):
                self.stop(ctx, "COMMAND_REJECTED", failed=True)
        else:
            self._running(ctx)

    def _running(self, ctx: Ctx) -> None:
        self.state, self.run_t0 = "RUNNING", ctx.now
        if self.level:
            self.level_start_t = ctx.now
            self.lv_mark = ctx.obs.num(self.level) or 0.0

    def stop(self, ctx: Ctx, reason: str, failed: bool = False) -> None:
        if self.state in ("DONE", "FAILED", "STOPPING"):
            return
        reason = reason[5:] if reason.startswith("FAIL:") else reason
        self.result, self.failed = reason, failed
        self._fail_after_stop = reason
        self.state, self.phase_t0 = "STOPPING", ctx.now
        if self.pump and self.pump in self.proc.on:
            self.proc.cmd(self.pump, False, f"{self.name}: stop ({reason})")
        if not self.pump or self.pump not in self.proc.on:
            pass

    def _finish(self, ctx: Ctx, reason: str | None, failed: bool) -> None:
        for sig in self.outputs:
            if sig in self.proc.on:
                self.proc.cmd(sig, False, f"{self.name}: off")
        self.state = "FAILED" if failed else "DONE"
        self.result, self.failed = reason, failed
        self.proc.sup.arbiter.release(self.proc.pid, self.outputs + self.extra_resources)

    @property
    def finished(self) -> bool:
        return self.state in ("DONE", "FAILED")

    def view(self) -> dict:
        return {"name": self.name, "state": self.state, "result": self.result, "failed": self.failed,
                "delivered_L": self.delivered_L, "target_L": self.target_L, "max_time_s": self.max_time_s,
                "measured_by": self.flow if self.flow else f"level {self.level}",
                "outputs": self.outputs}
