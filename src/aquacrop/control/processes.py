"""The installation's process controllers (brief §4-§6; ADR-0014, cause-and-effect: docs/model/cause_and_effect.md).

IRRIGATION  shared drive + K1/K2 motor selection + sector valves; AUTO alternates the recipe sequence
            (owner: P1/S1 then P2/S2, 300 s each) with the verified changeover; MANUAL runs one bounded
            sector with an operator-chosen pump under the same interlocks.
REFILL      main-tank batch: latches on the low trigger, runs mains water and stock A/B concurrently,
            each path to its own measured endpoint; FIXED_VOLUMES (+ EC validation) or EC_TARGET (+ A:B).
ACID        bounded staged acid doses toward the pH target with settling windows and batch/day limits.
RETURN      sump -> main on sump thresholds with destination-full hold and a declared duty policy.
STOCK_FILL  mains water into one stock tank to a level; invalidates that tank's preparation.
"""
from __future__ import annotations

from ..domain.model import ControllerState, new_id
from .process_base import FLOW_ZERO_L_MIN, Ctx, Path, Process

AUTO = ControllerState.AUTO
MANUAL = ControllerState.MANUAL
LOCKOUT = ControllerState.FAULT_LOCKOUT


def _fmt(v: float | None, unit: str = "") -> str:
    return "unavailable" if v is None else f"{v:.1f}{(' ' + unit) if unit else ''}"


# =================================================================================================
class Irrigation(Process):
    pid = "IRRIGATION"
    title = "Irrigation"
    ACTIVE = ("SELECT", "OPEN", "LEAD", "START", "VERIFY_FLOW", "IRRIGATE", "STOP_DRIVE", "STOP_SETTLE", "CLOSE",
              "DESELECT")

    def __init__(self, sup):
        super().__init__(sup)
        f = sup.facts
        self.drive = f"drive.{f.shared_drive}"
        self.pumps = list(f.main_pumps)
        self.sector_ids = [s.sector_id for s in f.sectors]
        self.auto_pump = {s.sector_id: s.auto_pump for s in f.sectors}
        self.step_idx = 0
        self.cycle = 0
        self.sector: str | None = None
        self.pump: str | None = None
        self.duration_s = 0.0
        self.irrigated_s = 0.0
        self.delivered_L = 0.0
        self.low_since: float | None = None
        self.open_cmd_s = 0.0
        self.cycle_log: list[dict] = []
        self.stop_stage = ""

    @property
    def resources(self) -> list[str]:
        return ([f"{self.drive}.run"] + [f"contactor.{p}.cmd" for p in self.pumps]
                + [f"valve.{s}.cmd" for s in self.sector_ids])

    def required(self, sector: str | None) -> list[str]:
        sig = [f"{self.drive}.running", f"{self.drive}.fault", "tank.MAIN.level", "tank.MAIN.low_low", "safety.chain_ok"]
        sig += [f"contactor.{p}.closed" for p in self.pumps]
        if sector:
            sig += [f"valve.{sector}.open_limit", f"flow.{sector}"]
        return sig

    def _ir(self):
        return self.recipe.body.irrigation

    # ------------------------------------------------------------------ AUTO
    def auto_blockers(self, ctx: Ctx) -> list[str]:
        out = []
        if ctx.recipe is None or not ctx.recipe.body.irrigation.sequence:
            out.append("the active recipe has no irrigation sequence")
            return out
        self.recipe = ctx.recipe
        return out + self._initial_permissives(ctx, None) + self._level_bad(ctx)

    def _initial_permissives(self, ctx: Ctx, sector: str | None) -> list[str]:
        """Brief §4: at start, the drive must be stopped and pump selection permissive before any valve opens."""
        o, bad = ctx.obs, []
        miss = [f"{s} ({o.why(s)})" for s in self.required(sector) if o.get(s) is None]
        if miss:
            bad.append(f"required observations unusable: {miss}")
            return bad
        if not self.perm("drive_stopped", not o.get(f"{self.drive}.running"), "drive running feedback off"):
            bad.append("drive is not reported stopped")
        if not self.perm("drive_healthy", not o.get(f"{self.drive}.fault"), "drive fault input inactive"):
            bad.append("drive fault input active")
        sel = [p for p in self.pumps if o.get(f"contactor.{p}.closed")]
        if not self.perm("no_pump_selected", not sel, f"selected: {sel}"):
            bad.append(f"a main pump is already selected: {sel}")
        open_ = [s for s in self.sector_ids if o.bound(f"valve.{s}.open_limit") and o.get(f"valve.{s}.open_limit")]
        if not self.perm("sector_valves_closed", not open_, f"open: {open_}"):
            bad.append(f"sector valve(s) reported open: {open_}")
        return bad

    def on_auto_start(self, ctx: Ctx) -> None:
        self.step_idx, self.cycle = 0, 0

    def auto_entry_phase(self) -> str:
        return "PRECHECK"

    def _begin_step(self, ctx: Ctx, sector: str, pump: str, duration: float, why: str) -> None:
        self.sector, self.pump, self.duration_s = sector, pump, duration
        self.irrigated_s = self.delivered_L = 0.0
        self.low_since = None
        self._to(None, "SELECT", why)
        self._select(ctx)

    def auto_tick(self, ctx: Ctx) -> None:
        if self.phase == "PRECHECK":
            if self.step_idx == 0:
                self.recipe = ctx.recipe          # new cycle: the active version applies from here
                self.cycle += 1
            if self.recipe is None or not self._ir().sequence:
                return self.fault("NO_RECIPE", "no irrigation sequence in the active recipe")
            step = self._ir().sequence[self.step_idx % len(self._ir().sequence)]
            bad = self._initial_permissives(ctx, step.sector)
            if bad:
                return self.fault("PRECHECK_FAILED", f"{step.sector}: {bad}")
            why = self.sup.run_blockers(self.pid, ctx) + self._level_bad(ctx)     # wait (e.g. for the refill)
            if why:
                self.inhibit = "; ".join(why)
                return
            self.inhibit = None
            pump = self.auto_pump[step.sector]
            return self._begin_step(ctx, step.sector, pump, step.duration_s, f"cycle {self.cycle} step "
                                    f"{self.step_idx + 1}: {step.sector} with {pump} for {step.duration_s:g} s "
                                    f"(recipe {self.recipe_ref})")
        self._sequence(ctx)

    def _level_bad(self, ctx: Ctx) -> list[str]:
        o = ctx.obs
        lv = o.num("tank.MAIN.level")
        out = []
        if o.get("tank.MAIN.low_low"):
            out.append("main low-low switch active")
        if lv is not None and self.recipe and lv < self.recipe.body.main.low_low_L:
            out.append(f"main level {lv:.0f} L below the dry-run guard {self.recipe.body.main.low_low_L:g} L")
        return out

    # ------------------------------------------------------------------ MANUAL
    def start_manual(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        pump, sector = op.get("pump"), op.get("sector")
        try:
            dur = float(op.get("duration_s"))
        except (TypeError, ValueError):
            return False, "duration_s is required (seconds)"
        if pump not in self.pumps:
            return False, f"pump must be one of {self.pumps}"
        if sector not in self.sector_ids:
            return False, f"sector must be one of {self.sector_ids}"
        mx = self._ir().max_manual_s
        if not 0 < dur <= mx:
            return False, f"duration must be 1..{mx:g} s (recipe {self.recipe_ref})"
        bad = self._initial_permissives(ctx, sector) + self._level_bad(ctx)
        if bad:
            return False, "; ".join(bad)
        self._begin_step(ctx, sector, pump, dur, f"MANUAL by {actor}: {sector} with {pump} for {dur:g} s")
        return True, f"{sector} with {pump}, at most {dur:g} s irrigation, stops on any fault"

    def manual_tick(self, ctx: Ctx) -> None:
        self._sequence(ctx)

    # ------------------------------------------------------------------ the verified sequence
    def _select(self, ctx: Ctx) -> None:
        if not self.cmd(f"contactor.{self.pump}.cmd", True, f"select {self.pump} for {self.sector}"):
            self.fault("COMMAND_REJECTED", f"selection of {self.pump} rejected")

    def _sequence(self, ctx: Ctx) -> None:
        o, ir, now = ctx.obs, self._ir(), ctx.now
        sec, pump = self.sector, self.pump
        if self.phase not in self.ACTIVE:
            return
        # ---- faults first (they take priority over any timing transition in this scan)
        miss = [f"{s} ({o.why(s)})" for s in self.required(sec) if o.get(s) is None]
        if miss:
            return self.fault("OBSERVATION_UNAVAILABLE", f"required observations unusable: {miss}")
        if not o.get("safety.chain_ok"):
            return self.fault("SAFETY_CHAIN_OPEN", "safety chain reported open")
        if o.get(f"{self.drive}.fault"):
            return self.fault("DRIVE_FAULT", "drive fault input active")
        sel = [p for p in self.pumps if o.get(f"contactor.{p}.closed")]
        if len(sel) > 1:
            return self.fault("BOTH_PUMPS_SELECTED", f"selection feedback from {sel}")
        if sel and sel[0] != pump:
            return self.fault("UNEXPECTED_PUMP_SELECTED", f"{sel[0]} selected while {pump} is owned")
        running = o.get(f"{self.drive}.running")
        pumping_phases = ("START", "VERIFY_FLOW", "IRRIGATE", "STOP_DRIVE")
        if running and self.phase not in pumping_phases:
            return self.fault("UNCOMMANDED_DRIVE_RUN", f"drive running during {self.phase}")
        if self.phase in ("START", "VERIFY_FLOW", "IRRIGATE"):
            bad = self._level_bad(ctx)
            if bad:
                return self.fault("MAIN_LOW_LOW", "; ".join(bad))
        flow = o.num(f"flow.{sec}")
        e = self.elapsed()

        if self.phase == "SELECT":
            if o.get(f"contactor.{pump}.closed"):
                self._to(None, "OPEN", f"{pump} selected (auxiliary contact closed)")
                self.open_cmd_s = now
                if not self.cmd(f"valve.{sec}.cmd", True, f"open {sec}"):
                    self.fault("COMMAND_REJECTED", f"open {sec} rejected")
            elif e >= ir.contactor_timeout_s:
                self.fault("SELECTION_TIMEOUT", f"{pump} selection feedback not seen in {ir.contactor_timeout_s:g} s")
        elif self.phase == "OPEN":
            if o.get(f"valve.{sec}.open_limit"):
                self._to(None, "LEAD", f"{sec} open confirmed")
            elif e >= ir.valve_open_timeout_s:
                self.fault("VALVE_OPEN_TIMEOUT", f"{sec} open limit not seen in {ir.valve_open_timeout_s:g} s")
        elif self.phase == "LEAD":
            if not o.get(f"valve.{sec}.open_limit"):
                return self.fault("VALVE_LOST_OPEN", f"{sec} open limit released before the pump started")
            lead = max(1.0, ir.open_lead_s)
            if now - self.open_cmd_s + 1e-9 >= lead:
                self._to(None, "START", f"{now - self.open_cmd_s:.1f} s since the open command (lead {lead:g} s)")
                if not self.cmd(f"{self.drive}.run", True, f"start drive for {pump}/{sec}"):
                    self.fault("COMMAND_REJECTED", "drive start rejected")
        elif self.phase == "START":
            if running:
                self._to(None, "VERIFY_FLOW", "drive running feedback confirmed")
            elif e >= ir.drive_start_timeout_s:
                self.fault("DRIVE_START_TIMEOUT", f"no running feedback in {ir.drive_start_timeout_s:g} s")
        elif self.phase in ("VERIFY_FLOW", "IRRIGATE"):
            if not running:
                return self.fault("DRIVE_STOPPED", f"running feedback lost during {self.phase}")
            low = flow is None or flow < ir.min_flow_L_min
            in_grace = self.phase == "VERIFY_FLOW" and e < ir.flow_grace_s
            if low and not in_grace:
                self.low_since = self.low_since if self.low_since is not None else now
                if now - self.low_since + 1e-9 >= ir.no_flow_sustain_s:
                    return self.fault("NO_FLOW", f"{sec} flow {_fmt(flow, 'L/min')} below {ir.min_flow_L_min:g} L/min")
            elif not low:
                self.low_since = None
            if self.phase == "VERIFY_FLOW":
                if not low:
                    self._to(None, "IRRIGATE", f"flow verified {flow:.1f} L/min on {sec}")
            else:
                if not low:      # irrigation time counts verified flow only
                    self.irrigated_s += ctx.dt
                self.delivered_L += (flow or 0.0) * ctx.dt / 60.0
                self.next_step = f"stop {sec} in {max(0.0, self.duration_s - self.irrigated_s):.0f} s"
                if self.irrigated_s + 1e-9 >= self.duration_s:
                    self._to(None, "STOP_DRIVE", f"{sec} irrigated {self.irrigated_s:.0f} s, ~{self.delivered_L:.0f} L "
                             "measured")
                    self.cmd(f"{self.drive}.run", False, f"stop drive ({sec} complete)")
        elif self.phase == "STOP_DRIVE":
            if not running:
                self._to(None, "STOP_SETTLE", "drive stop verified")
            elif e >= ir.drive_stop_timeout_s:
                self.fault("STOP_NOT_VERIFIED", f"drive still reports running {ir.drive_stop_timeout_s:g} s after stop")
        elif self.phase == "STOP_SETTLE":
            if e >= ir.stop_settle_s:
                self._to(None, "CLOSE", f"close {sec}")
                self.cmd(f"valve.{sec}.cmd", False, f"close {sec}")
        elif self.phase == "CLOSE":
            closed = self._valve_closed(o, sec)
            if closed:
                self._to(None, "DESELECT", f"{sec} closure verified ({closed})")
                self.cmd(f"contactor.{pump}.cmd", False, f"deselect {pump}")
            elif e >= ir.valve_close_timeout_s:
                self.fault("VALVE_CLOSE_TIMEOUT", f"{sec} not verified closed in {ir.valve_close_timeout_s:g} s")
        elif self.phase == "DESELECT":
            if not o.get(f"contactor.{pump}.closed"):
                self.cycle_log.append({"cycle": self.cycle, "sector": sec, "pump": pump, "end_sim_s": now,
                                       "irrigated_s": self.irrigated_s, "delivered_L_measured": self.delivered_L,
                                       "recipe": self.recipe_ref, "mode": self.state.value})
                del self.cycle_log[:-200]
                self.sup.emit("IRRIGATION_STEP_COMPLETED", self.cycle_log[-1], "controller")
                if self.state == MANUAL:
                    self.finish_manual("COMPLETED", f"{sec} irrigated {self.irrigated_s:.0f} s")
                    self._to(None, "IDLE", "manual irrigation complete")
                    self.sector = self.pump = None
                    return
                self.step_idx = (self.step_idx + 1) % len(self._ir().sequence)
                nxt = self._ir().sequence[self.step_idx]
                self._to(None, "PRECHECK", f"{pump} deselected; changeover to {nxt.sector}")
            elif e >= ir.contactor_timeout_s:
                self.fault("DESELECT_TIMEOUT", f"{pump} still selected {ir.contactor_timeout_s:g} s after release")

    def _valve_closed(self, o, sec: str) -> str | None:
        """Closure evidence per the observation contract: closed limit if bound, otherwise open limit
        released plus no measured flow (weaker evidence, documented in the signal budget)."""
        if o.bound(f"valve.{sec}.closed_limit"):
            return "closed limit" if o.get(f"valve.{sec}.closed_limit") else None
        flow = o.num(f"flow.{sec}")
        if not o.get(f"valve.{sec}.open_limit") and flow is not None and flow < FLOW_ZERO_L_MIN:
            return "open limit released and no flow"
        return None

    # ------------------------------------------------------------------ stop / fault sequence
    def start_stop(self) -> None:
        self._stop_t0 = self.now
        self.stop_stage = "DRIVE"
        self.cmd(f"{self.drive}.run", False, "stop sequence: drive stop")

    def stop_tick(self, ctx: Ctx) -> bool:
        o, ir = ctx.obs, (self.recipe.body.irrigation if self.recipe else None)
        stop_to = ir.drive_stop_timeout_s if ir else 10.0
        t = self.now - self._stop_t0 + 1e-9
        if self.stop_stage == "DRIVE":
            running = o.get(f"{self.drive}.running")
            if running is False:
                self.stop_stage, self._stop_t0 = "SETTLE", self.now
            elif t >= stop_to and not self.counters.get("stop_unverified_alarmed"):
                # Stop not verified: never switch a contactor or close the valve under a possibly running
                # pump. Hold, alarm, and keep waiting for verified stop (operator action on site).
                self.counters["stop_unverified_alarmed"] = True
                if self.state != LOCKOUT:
                    self.stopping = False
                    self.fault("STOP_NOT_VERIFIED", f"drive running feedback {running!r} {stop_to:g} s after the stop "
                                                    "command; valves and pump selection held")
                    self.stopping = True
                    self.stop_stage = "DRIVE"
                else:
                    self.sup.alarms.raise_or_update("PROC_STOP_NOT_VERIFIED", f"process.{self.pid}", severity="CRITICAL",
                                                    latch=True, sim_s=self.now, message=f"{self.title}: drive stop not "
                                                    "verified; valves and pump selection held")
            return False
        if self.stop_stage == "SETTLE":
            if t >= (ir.stop_settle_s if ir else 0.5):
                for s in self.sector_ids:
                    if f"valve.{s}.cmd" in self.on:
                        self.cmd(f"valve.{s}.cmd", False, "stop sequence: close sector valve")
                self.stop_stage, self._stop_t0 = "VALVES", self.now
            return False
        if self.stop_stage == "VALVES":
            open_ = [s for s in self.sector_ids if o.bound(f"valve.{s}.open_limit") and o.get(f"valve.{s}.open_limit")]
            if open_ and t < (ir.valve_close_timeout_s if ir else 5.0):
                return False
            if open_:
                self.sup.alarms.raise_or_update("PROC_VALVE_CLOSE_TIMEOUT", f"process.{self.pid}", severity="CRITICAL",
                                                latch=True, sim_s=self.now,
                                                message=f"{self.title}: sector valve(s) {open_} not closed after stop")
            for p in self.pumps:
                if f"contactor.{p}.cmd" in self.on:
                    self.cmd(f"contactor.{p}.cmd", False, "stop sequence: deselect pump")
            self.stop_stage, self._stop_t0 = "DESELECT", self.now
            return False
        if self.stop_stage == "DESELECT":
            sel = [p for p in self.pumps if o.get(f"contactor.{p}.closed")]
            if sel and t < (ir.contactor_timeout_s if ir else 2.0):
                return False
            if sel:
                self.sup.alarms.raise_or_update("PROC_DESELECT_TIMEOUT", f"process.{self.pid}", severity="CRITICAL",
                                                latch=True, sim_s=self.now,
                                                message=f"{self.title}: {sel} still selected after stop")
            self.counters.pop("stop_unverified_alarmed", None)
            self.sector = self.pump = None
            self.stop_stage = ""
            return True
        return True

    def outputs_feedback_active(self, ctx: Ctx) -> list[str]:
        o, out = ctx.obs, []
        if o.get(f"{self.drive}.running"):
            out.append(f"{self.drive}.running")
        out += [f"contactor.{p}.closed" for p in self.pumps if o.get(f"contactor.{p}.closed")]
        out += [f"valve.{s}.open_limit" for s in self.sector_ids
                if o.bound(f"valve.{s}.open_limit") and o.get(f"valve.{s}.open_limit")]
        return out

    def fault_cleared(self, ctx: Ctx) -> tuple[bool, str]:
        o = ctx.obs
        miss = [s for s in self.required(None) if o.get(s) is None]
        if miss:
            return False, f"observations unusable: {miss}"
        if o.get(f"{self.drive}.fault"):
            return False, "drive fault input still active (reset the drive locally)"
        if not o.get("safety.chain_ok"):
            return False, "safety chain still open"
        if self.fault_reason == "MAIN_LOW_LOW" and self._level_bad(ctx):
            return False, "; ".join(self._level_bad(ctx))
        return True, ""

    def on_reset(self) -> None:
        self.step_idx = 0

    def view(self) -> dict:
        v = super().view()
        v |= {"sector": self.sector, "pump": self.pump, "duration_s": self.duration_s, "irrigated_s": self.irrigated_s,
              "delivered_L_measured": self.delivered_L, "cycle": self.cycle, "step_index": self.step_idx,
              "auto_pump_for_sector": self.auto_pump, "cycles": self.cycle_log[-20:], "stop_stage": self.stop_stage,
              "sequence": ([s.model_dump() for s in self.recipe.body.irrigation.sequence] if self.recipe else [])}
        return v


# =================================================================================================
class _PathProcess(Process):
    """Common stop handling for processes built from transfer paths."""

    def active_paths(self) -> list[Path]:
        return []

    def start_stop(self) -> None:
        self._stop_t0 = self.now
        for p in self.active_paths():
            if not p.finished and self._ctx is not None:
                p.stop(self._ctx, "process stop")

    def stop_tick(self, ctx: Ctx) -> bool:
        busy = False
        for p in self.active_paths():
            if not p.finished:
                p.tick(ctx)
                busy = busy or not p.finished
        if busy:
            return False
        for sig in sorted(self.on):
            self.cmd(sig, False, "stop sequence: output off")
        self.on_stopped()
        return True

    def on_stopped(self) -> None:
        pass

    def outputs_feedback_active(self, ctx: Ctx) -> list[str]:
        o, out = ctx.obs, []
        for sig in self.feedback_signals():
            if not o.bound(sig):
                continue
            v = o.get(sig)
            if sig.startswith("flow."):
                if v is not None and v > FLOW_ZERO_L_MIN:
                    out.append(f"{sig}={v:.1f}")
            elif v:
                out.append(sig)
        return out

    def feedback_signals(self) -> list[str]:
        return []

    def path_alarm(self, p: Path):
        return self.sup.alarms.raise_or_update("PROC_PATH_FAULT", f"process.{self.pid}.{p.name}", severity="CRITICAL",
                                               latch=True, sim_s=self.now,
                                               message=f"{self.title}: path {p.name} failed: {p.result} "
                                                       f"(delivered {p.delivered_L:.1f} L measured)")


# =================================================================================================
class Refill(_PathProcess):
    pid = "REFILL"
    title = "Main-tank refill"

    def __init__(self, sup):
        super().__init__(sup)
        self.armed = True
        self.below_since: float | None = None
        self.batch: dict | None = None
        self.paths: dict[str, Path] = {}
        self.batches: list[dict] = []
        self.pending_fault = None

    @property
    def resources(self) -> list[str]:
        return ["valve.FILL_MAIN.cmd", "pump.TP_A.cmd", "pump.TP_B.cmd", "valve.OUT_A.cmd", "valve.OUT_B.cmd",
                "stock.A", "stock.B"]

    def feedback_signals(self) -> list[str]:
        return ["flow.FILL_MAIN", "flow.TP_A", "flow.TP_B", "pump.TP_A.running", "pump.TP_B.running"]

    def active_paths(self) -> list[Path]:
        return list(self.paths.values())

    def _guards_main(self, ctx: Ctx, *, final_level: float | None = None):
        m = self.recipe.body.main

        def check(c: Ctx) -> str | None:
            o = c.obs
            if o.get("tank.MAIN.high_high"):
                return "FAIL:HIGH_HIGH_SWITCH"
            lv = o.num("tank.MAIN.level")
            if lv is None:
                return f"FAIL:MAIN_LEVEL_UNAVAILABLE ({o.why('tank.MAIN.level')})"
            if final_level is not None and lv >= final_level:
                return "FINAL_LEVEL_REACHED"
            if lv >= m.fill_stop_L:
                return f"FILL_STOP_GUARD ({lv:.0f} >= {m.fill_stop_L:g} L)"
            return None
        return check

    def _stock_guard(self, stock: str):
        rf = self.recipe.body.refill
        main_guard = self._guards_main(None)

        def check(c: Ctx) -> str | None:
            why = main_guard(c)
            if why:
                return why
            lv = c.obs.num(f"tank.{stock}.level")
            if lv is None:
                return f"FAIL:STOCK_LEVEL_UNAVAILABLE ({c.obs.why(f'tank.{stock}.level')})"
            if lv <= rf.stock_reserve_L:
                return f"STOCK_RESERVE_REACHED ({lv:.0f} <= {rf.stock_reserve_L:g} L: stock empty)"
            ok, why = self.sup.stock_ready(stock, lv)
            if not ok:
                return f"STOCK_NOT_READY ({why})"
            return None
        return check

    def _water_path(self, ctx: Ctx, target_L: float | None, final_level: float | None, max_time: float) -> Path:
        w = self.recipe.body.refill.water
        return Path(self, "WATER", valve="valve.FILL_MAIN.cmd", open_feedback="valve.FILL_MAIN.open_limit",
                    flow="flow.FILL_MAIN", target_L=target_L, max_time_s=max_time, min_flow_L_min=w.min_flow_L_min,
                    grace_s=w.flow_grace_s, stop_when=self._guards_main(ctx, final_level=final_level))

    def _stock_path(self, stock: str, target_L: float) -> Path:
        rf = self.recipe.body.refill
        return Path(self, stock, valve=f"valve.OUT_{stock}.cmd", pump=f"pump.TP_{stock}.cmd",
                    run_feedback=f"pump.TP_{stock}.running", open_feedback=f"valve.OUT_{stock}.open_limit",
                    flow=f"flow.TP_{stock}", target_L=target_L, max_time_s=rf.transfer_max_time_s,
                    min_flow_L_min=rf.transfer_min_flow_L_min, grace_s=rf.transfer_flow_grace_s,
                    stop_when=self._stock_guard(stock), extra_resources=[f"stock.{stock}"])

    def _start_path(self, ctx: Ctx, p: Path) -> bool:
        """Start a path unless its own guard already says stop (stock not ready or empty, level reached):
        a pump is never started just to be stopped on the next scan."""
        why = p.stop_when(ctx) if p.stop_when else None
        why = (why[5:] if why and why.startswith("FAIL:") else why) or p.start(ctx)
        self.paths[p.name] = p
        target = f"process.{self.pid}.{p.name}"
        if why:
            p.state, p.result, p.failed = "DONE", f"NOT_STARTED: {why}", False
            self.sup.emit("REFILL_PATH_NOT_STARTED", {"path": p.name, "reason": why, "batch": self._bid()}, "controller")
            self.sup.alarms.raise_or_update("REFILL_PATH_NOT_STARTED", target, severity="WARNING", sim_s=ctx.now,
                                            message=f"{self.title}: {p.name} not started: {why}")
            return False
        self.sup.alarms.clear("REFILL_PATH_NOT_STARTED", target)
        return True

    def _bid(self) -> str | None:
        return self.batch["batch_id"] if self.batch else None

    # ------------------------------------------------------------------ AUTO
    def auto_blockers(self, ctx: Ctx) -> list[str]:
        if ctx.recipe is None:
            return ["no active recipe"]
        b = ctx.recipe.body.refill
        out = []
        if b.endpoint_mode is None or b.nutrient_mode is None:
            out.append("the recipe must select a refill endpoint and a nutrient mode explicitly")
        return out

    def on_auto_start(self, ctx: Ctx) -> None:
        self.armed, self.below_since = True, None

    def auto_tick(self, ctx: Ctx) -> None:
        if self.batch is not None:
            return self._batch_tick(ctx)
        rec = ctx.recipe
        if rec is None:
            self.inhibit = "no active recipe"
            return
        m, rf = rec.body.main, rec.body.refill
        o = ctx.obs
        lv = o.num("tank.MAIN.level")
        if lv is None:
            self.inhibit = f"main level unavailable ({o.why('tank.MAIN.level')}): no batch can latch"
            self.below_since = None
            return
        self.inhibit = None
        if not self.armed:
            if lv >= m.refill_trigger_L + m.rearm_hysteresis_L:
                self.armed = True
                self.sup.emit("REFILL_REARMED", {"level_L": lv, "rearm_above_L": m.refill_trigger_L + m.rearm_hysteresis_L,
                                                 "recipe": rec.ref}, "controller")
            else:
                self.next_step = (f"re-arms when the main level is above {m.refill_trigger_L + m.rearm_hysteresis_L:g} L "
                                  f"(now {lv:.0f} L)")
                return
        if lv <= m.refill_trigger_L:
            self.below_since = self.below_since if self.below_since is not None else ctx.now
            held = ctx.now - self.below_since + 1e-9
            if held >= rf.trigger_debounce_s:
                why = self.sup.run_blockers(self.pid, ctx)
                if why:
                    self.inhibit = "; ".join(why)
                    return
                return self._start_batch(ctx, lv)
            self.next_step = f"batch latches if the level stays at or below {m.refill_trigger_L:g} L for " \
                             f"{rf.trigger_debounce_s - held:.0f} s more"
        else:
            self.below_since = None
            self.next_step = f"batch latches when the main level falls to {m.refill_trigger_L:g} L (now {lv:.0f} L)"

    def _start_batch(self, ctx: Ctx, level: float, *, manual: dict | None = None) -> None:
        self.recipe = ctx.recipe
        rf = self.recipe.body.refill
        o = ctx.obs
        self.armed = False
        self.below_since = None
        self.paths = {}
        self.batch = {"batch_id": new_id("batch"), "recipe": self.recipe_ref, "start_s": ctx.now, "start_level_L": level,
                      "start_ec": o.num("main.ec"), "start_ph": o.num("main.ph"), "endpoint_mode": rf.endpoint_mode,
                      "nutrient_mode": rf.nutrient_mode, "ec_steps": [], "ec_state": "EVAL", "nutrients": "PENDING",
                      "fault_occurrence": None, "events": []}
        self._to(None, "BATCH", f"batch {self.batch['batch_id']} latched at {level:.0f} L (recipe {self.recipe_ref}, "
                 f"{rf.endpoint_mode}, {rf.nutrient_mode})")
        self.sup.emit("REFILL_BATCH_STARTED", {k: v for k, v in self.batch.items() if k != "events"}, "controller")
        if rf.endpoint_mode == "FIXED_ADDITIONS":
            self._start_path(ctx, self._water_path(ctx, rf.water.target_L, None, rf.water.max_time_s))
        else:
            self._start_path(ctx, self._water_path(ctx, None, rf.final_level_L, rf.water.max_time_s))
        if rf.nutrient_mode == "FIXED_VOLUMES":
            for s, amount in (("A", rf.fixed.a_L), ("B", rf.fixed.b_L)):
                if amount and amount > 0:
                    self._start_path(ctx, self._stock_path(s, amount))
            self.batch["nutrients"] = "TRANSFERRING"

    def _batch_tick(self, ctx: Ctx) -> None:
        b, rf = self.batch, self.recipe.body.refill
        for p in list(self.paths.values()):
            was = p.finished
            p.tick(ctx)
            if p.finished and not was:
                self._path_done(ctx, p)
        if rf.nutrient_mode == "EC_TARGET":
            self._ec_tick(ctx)
        elif b["nutrients"] == "TRANSFERRING" and all(self.paths[s].finished for s in ("A", "B") if s in self.paths):
            b["nutrients"], b["validate_from_s"] = "SETTLING", ctx.now
        if rf.nutrient_mode == "FIXED_VOLUMES" and b["nutrients"] == "SETTLING":
            water = self.paths.get("WATER")
            if water is None or water.finished:
                ok, why = self.sup.mixing_ready("EC_VALIDATE", rf.fixed.validation_settle_s, ctx)
                if ok:
                    ec = ctx.obs.num("main.ec")
                    if ec is None:
                        b["nutrients"] = f"EC_UNVERIFIED ({ctx.obs.why('main.ec')})"
                    elif rf.fixed.ec_min_mS_cm <= ec <= rf.fixed.ec_max_mS_cm:
                        b["nutrients"] = f"EC_VALID ({ec:.2f} mS/cm)"
                    else:
                        b["nutrients"] = f"EC_OUT_OF_BAND ({ec:.2f} mS/cm not in {rf.fixed.ec_min_mS_cm:g}.." \
                                         f"{rf.fixed.ec_max_mS_cm:g})"
                        self.sup.alarms.raise_or_update("REFILL_EC_VALIDATION_FAILED", "process.REFILL",
                                                        severity="WARNING", sim_s=ctx.now, value=ec,
                                                        message=f"Refill {b['batch_id']}: EC {ec:.2f} mS/cm outside the "
                                                                f"recipe band; no automatic correction in FIXED_VOLUMES")
                else:
                    self.next_step = f"EC validation waits: {why}"
        if all(p.finished for p in self.paths.values()) and b["nutrients"] not in ("PENDING", "TRANSFERRING",
                                                                                      "SETTLING", "SETTLE", "DOSING"):
            self._end_batch(ctx)

    def _path_done(self, ctx: Ctx, p: Path) -> None:
        b = self.batch
        self.sup.emit("REFILL_PATH_ENDED", {"batch": b["batch_id"] if b else None, "recipe": self.recipe_ref} | p.view(),
                      "controller")
        if p.failed:
            occ = self.path_alarm(p)
            if b is not None and b["fault_occurrence"] is None:
                b["fault_occurrence"] = occ
            if b is not None and b["nutrient_mode"] == "EC_TARGET" and p.name in ("A", "B"):
                other = self.paths.get("B" if p.name == "A" else "A")
                if other and not other.finished:
                    other.stop(ctx, f"partner path {p.name} failed: A:B ratio cannot be kept")
                b["nutrients"] = f"STOPPED (path {p.name} failed)"

    def _ec_tick(self, ctx: Ctx) -> None:
        b, ec_r = self.batch, self.recipe.body.refill.ec
        if b["nutrients"].startswith(("STOPPED", "EC_", "MAX", "ABOVE")) or b["nutrients"] == "DONE":
            return
        steps = b["ec_steps"]
        doing = [self.paths[s] for s in ("A", "B") if s in self.paths and not self.paths[s].finished]
        if doing:
            b["nutrients"] = "DOSING"
            return
        if b["nutrients"] == "DOSING":
            b["nutrients"] = "SETTLE"
        water = self.paths.get("WATER")
        water_done = water is None or water.finished
        ok, why = self.sup.mixing_ready("EC_VALIDATE" if water_done else "EC", ec_r.settle_s, ctx)
        if not ok:
            self.next_step = f"EC evaluation waits: {why}"
            if b["nutrients"] == "PENDING":
                b["nutrients"] = "SETTLE"
            return
        ec = ctx.obs.num("main.ec")
        if ec is None:
            b["nutrients"] = f"STOPPED (EC unavailable: {ctx.obs.why('main.ec')})"
            self.sup.alarms.raise_or_update("REFILL_EC_UNAVAILABLE", "process.REFILL", severity="WARNING", sim_s=ctx.now,
                                            message="Refill: EC probe unavailable, nutrient dosing stopped")
            return
        if ec > ec_r.ec_target_mS_cm + ec_r.ec_tolerance_mS_cm and water_done:
            b["nutrients"] = f"ABOVE_TARGET ({ec:.2f} mS/cm)"
            self.sup.alarms.raise_or_update("REFILL_EC_ABOVE_TARGET", "process.REFILL", severity="WARNING", sim_s=ctx.now,
                                            value=ec, message=f"Refill: EC {ec:.2f} mS/cm above target + tolerance; "
                                                              "nutrients cannot be removed automatically")
            return
        if ec >= ec_r.ec_target_mS_cm - ec_r.ec_tolerance_mS_cm:
            if water_done:
                b["nutrients"] = f"EC_TARGET_REACHED ({ec:.2f} mS/cm)"
            else:
                self.next_step = f"EC {ec:.2f} mS/cm in band; re-evaluated while water dilutes"
            return
        used_a = sum(x["a_L"] for x in steps)
        used_b = sum(x["b_L"] for x in steps)
        r = ec_r.ratio_a_to_b
        a = min(ec_r.step_total_L * r / (1 + r), ec_r.max_a_L - used_a)
        bb = min(ec_r.step_total_L / (1 + r), ec_r.max_b_L - used_b)
        scale = min(a / (ec_r.step_total_L * r / (1 + r)), bb / (ec_r.step_total_L / (1 + r)))  # keep the ratio
        a, bb = ec_r.step_total_L * r / (1 + r) * scale, ec_r.step_total_L / (1 + r) * scale
        if scale <= 1e-6:
            b["nutrients"] = f"MAX_REACHED ({used_a:.1f} L A, {used_b:.1f} L B; EC {ec:.2f} mS/cm)"
            self.sup.alarms.raise_or_update("REFILL_EC_LIMIT_REACHED", "process.REFILL", severity="WARNING",
                                            sim_s=ctx.now, value=ec, message=f"Refill: batch nutrient maxima reached "
                                            f"with EC {ec:.2f} mS/cm below target")
            return
        pa, pb = self._stock_path("A", a), self._stock_path("B", bb)
        why = [f"{p.name}: {r[5:] if r.startswith('FAIL:') else r}" for p in (pa, pb) if (r := p.stop_when(ctx))]
        if why:      # both or neither: the A:B ratio is kept
            b["nutrients"] = f"STOPPED ({'; '.join(why)})"
            self.sup.alarms.raise_or_update("REFILL_PATH_NOT_STARTED", f"process.{self.pid}.AB", severity="WARNING",
                                            sim_s=ctx.now, message=f"{self.title}: nutrient step not started: {why}")
            return
        self.sup.alarms.clear("REFILL_PATH_NOT_STARTED", f"process.{self.pid}.AB")
        steps.append({"n": len(steps) + 1, "ec_before": ec, "a_L": a, "b_L": bb, "at_s": ctx.now})
        self.sup.emit("REFILL_EC_STEP", {"batch": b["batch_id"], "recipe": self.recipe_ref} | steps[-1], "controller")
        started = [self._start_path(ctx, pa), self._start_path(ctx, pb)]
        if not all(started):
            for s in ("A", "B"):
                p = self.paths.get(s)
                if p and not p.finished:
                    p.stop(ctx, "partner path could not start: A:B ratio cannot be kept")
            b["nutrients"] = "STOPPED (a nutrient path could not start)"
            return
        b["nutrients"] = "DOSING"

    def _end_batch(self, ctx: Ctx) -> None:
        b = self.batch
        b["end_s"] = ctx.now
        b["end_level_L"] = ctx.obs.num("tank.MAIN.level")
        b["end_ec"], b["end_ph"] = ctx.obs.num("main.ec"), ctx.obs.num("main.ph")
        b["paths"] = {k: p.view() for k, p in self.paths.items()}
        failed = [k for k, p in self.paths.items() if p.failed]
        nut = b["nutrients"]
        if failed:
            b["result"] = "FAILED"
        elif nut.startswith("STOPPED") or any((p.result or "").startswith(("STOCK_", "NOT_STARTED"))
                                              for p in self.paths.values()):
            b["result"] = "INCOMPLETE"
        elif nut.startswith(("EC_OUT_OF_BAND", "EC_UNVERIFIED", "ABOVE_TARGET", "MAX_REACHED")):
            b["result"] = "OUT_OF_SPEC"
        else:
            b["result"] = "COMPLETED"
        occ = b.pop("fault_occurrence")
        summary = {k: v for k, v in b.items() if k != "events"}
        self.batches.append(summary)
        del self.batches[:-50]
        self.last_result = {"kind": "batch"} | summary
        self.sup.emit("REFILL_BATCH_ENDED", summary, "controller")
        self.batch, self.paths = None, {}
        if self.manual_op is not None:
            self.finish_manual(b["result"], "; ".join(f"{k}: {v['result']}" for k, v in b["paths"].items()))
            self._to(None, "IDLE", f"manual transfer ended: {b['result']}")
        else:
            self._to(None, "MONITOR", f"batch {b['batch_id']} {b['result']}")
        if failed:
            self.fault("PATH_FAILED", f"batch {b['batch_id']} path(s) {failed} failed; review before the next batch",
                       occurrence=occ)

    def on_stopped(self) -> None:
        if self.batch is not None:
            b = self.batch
            b["paths"] = {k: p.view() for k, p in self.paths.items()}
            b["result"], b["end_s"] = "STOPPED", self.now
            b.pop("fault_occurrence", None)
            summary = {k: v for k, v in b.items() if k != "events"}
            self.batches.append(summary)
            self.last_result = {"kind": "batch"} | summary
            self.sup.emit("REFILL_BATCH_ENDED", summary, "controller")
        self.batch, self.paths = None, {}

    # ------------------------------------------------------------------ MANUAL
    def start_manual(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        path = op.get("path")
        try:
            vol = float(op.get("volume_L"))
        except (TypeError, ValueError):
            return False, "volume_L is required (litres)"
        rec = self.recipe
        m, rf = rec.body.main, rec.body.refill
        lv = ctx.obs.num("tank.MAIN.level")
        if lv is None:
            return False, f"main level unavailable ({ctx.obs.why('tank.MAIN.level')})"
        room = m.fill_stop_L - lv
        if vol <= 0 or vol > room:
            return False, f"volume must be 0..{max(room, 0):.0f} L (headroom to the refill stop level {m.fill_stop_L:g} L)"
        self.batch = {"batch_id": new_id("manual"), "recipe": rec.ref, "start_s": ctx.now, "start_level_L": lv,
                      "start_ec": ctx.obs.num("main.ec"), "start_ph": ctx.obs.num("main.ph"), "endpoint_mode": "MANUAL",
                      "nutrient_mode": "MANUAL", "ec_steps": [], "nutrients": "MANUAL", "fault_occurrence": None,
                      "events": []}
        self.paths = {}
        if path == "WATER":
            p = self._water_path(ctx, vol, None, rf.water.max_time_s)
        elif path in ("A", "B"):
            # the requested stock's batch quantity in the recipe's active nutrient mode, never another
            # stock's or the inactive mode's value
            if rf.nutrient_mode == "FIXED_VOLUMES":
                mx, src = (rf.fixed.a_L if path == "A" else rf.fixed.b_L), "fixed volume"
            else:
                mx, src = (rf.ec.max_a_L if path == "A" else rf.ec.max_b_L), "EC-target batch maximum"
            if mx is None:
                self.batch = None
                return False, f"the active recipe sets no {src} for stock {path}"
            if vol > mx:
                self.batch = None
                return False, f"a manual {path} transfer is limited to {mx:g} L (recipe {src} for stock {path})"
            stock_lv = ctx.obs.num(f"tank.{path}.level")
            ok, why = self.sup.stock_ready(path, stock_lv)
            if not ok:
                self.batch = None
                return False, f"stock {path} not ready: {why}"
            p = self._stock_path(path, vol)
        else:
            self.batch = None
            return False, "path must be WATER, A or B"
        if not self._start_path(ctx, p):
            self.batch = None
            return False, f"cannot start: {p.result}"
        self._to(None, "BATCH", f"MANUAL {path} {vol:g} L by {actor}")
        return True, (f"{path}: at most {vol:g} L measured, at most {p.max_time_s:g} s, stops at the refill stop level "
                      f"{m.fill_stop_L:g} L")

    def manual_tick(self, ctx: Ctx) -> None:
        if self.batch is None:
            return
        for p in list(self.paths.values()):
            was = p.finished
            p.tick(ctx)
            if p.finished and not was:
                self._path_done(ctx, p)
        if all(p.finished for p in self.paths.values()):
            self.batch["nutrients"] = "DONE"
            self._end_batch(ctx)

    def fault_cleared(self, ctx: Ctx) -> tuple[bool, str]:
        if ctx.obs.get("tank.MAIN.high_high"):
            return False, "main high-high switch active"
        return True, ""

    def view(self) -> dict:
        v = super().view()
        b = self.batch
        v |= {"armed": self.armed, "batch": ({k: x for k, x in b.items() if k not in ("events", "fault_occurrence")}
                                             if b else None),
              "paths": {k: p.view() for k, p in self.paths.items()}, "batches": self.batches[-10:]}
        return v


# =================================================================================================
class Acid(_PathProcess):
    pid = "ACID"
    title = "Acid pH dosing"

    def __init__(self, sup):
        super().__init__(sup)
        self.path: Path | None = None
        self.batch: dict | None = None
        self.day: list[tuple[float, float]] = []
        self.no_response = 0
        self.dose_ph_before: float | None = None
        self.batches: list[dict] = []

    @property
    def resources(self) -> list[str]:
        return ["pump.TP_ACID.cmd", "valve.OUT_ACID.cmd", "stock.ACID"]

    def feedback_signals(self) -> list[str]:
        return ["flow.TP_ACID", "pump.TP_ACID.running"]

    def active_paths(self) -> list[Path]:
        return [self.path] if self.path else []

    def _day_total(self, now: float) -> float:
        self.day = [(t, v) for t, v in self.day if now - t < 86400.0]
        return sum(v for _, v in self.day)

    def _dose_blockers(self, ctx: Ctx, ph: float | None) -> list[str]:
        a, m = self.recipe.body.acid, self.recipe.body.main
        o, out = ctx.obs, []
        if ph is None:
            out.append(f"pH unavailable ({o.why('main.ph')})")
        lv = o.num("tank.MAIN.level")
        if lv is None:
            out.append(f"main level unavailable ({o.why('tank.MAIN.level')})")
        elif lv < m.min_dosing_L:
            out.append(f"main volume {lv:.0f} L below the dosing minimum {m.min_dosing_L:g} L")
        elif lv >= m.high_inhibit_L:
            out.append(f"main level {lv:.0f} L at the high-level inhibit")
        st = o.num("tank.ACID.level")
        ok, why = self.sup.stock_ready("ACID", st)
        if st is None:
            out.append(f"acid stock level unavailable ({o.why('tank.ACID.level')}): the reserve cannot be checked")
        elif not ok:
            out.append(f"acid stock not ready: {why}")
        elif st <= self.recipe.body.refill.stock_reserve_L:
            out.append(f"acid stock at reserve ({st:.0f} L)")
        return out

    def _dose_path(self, vol: float) -> Path:
        a = self.recipe.body.acid
        main_guard = Refill._guards_main(self, None)

        reserve = self.recipe.body.refill.stock_reserve_L

        def guard(c: Ctx) -> str | None:
            why = main_guard(c)
            if why and not why.startswith("FILL_STOP"):
                return why
            if c.obs.num("main.ph") is None:
                return f"FAIL:PH_UNAVAILABLE ({c.obs.why('main.ph')})"
            st = c.obs.num("tank.ACID.level")
            if st is None:
                return f"FAIL:STOCK_LEVEL_UNAVAILABLE ({c.obs.why('tank.ACID.level')})"
            if st <= reserve:
                return f"STOCK_RESERVE_REACHED ({st:.0f} <= {reserve:g} L: acid stock empty)"
            return None
        return Path(self, "ACID", valve="valve.OUT_ACID.cmd", pump="pump.TP_ACID.cmd", run_feedback="pump.TP_ACID.running",
                    open_feedback="valve.OUT_ACID.open_limit", flow="flow.TP_ACID", target_L=vol,
                    max_time_s=a.dose_max_time_s, min_flow_L_min=a.min_flow_L_min, grace_s=a.flow_grace_s,
                    stop_when=guard, extra_resources=["stock.ACID"])

    def auto_blockers(self, ctx: Ctx) -> list[str]:
        return [] if ctx.recipe is not None else ["no active recipe"]

    def on_auto_start(self, ctx: Ctx) -> None:
        self.no_response = 0

    def auto_tick(self, ctx: Ctx) -> None:
        o = ctx.obs
        if self.phase == "DOSING":
            return self._dose_tick(ctx)
        if self.batch is None:
            self.recipe = ctx.recipe            # a new batch (or idle monitoring) uses the active version
        if self.recipe is None:
            self.inhibit = "no active recipe"
            return
        a = self.recipe.body.acid
        ph = o.num("main.ph")
        if ph is not None and ph < a.ph_target - a.undershoot_margin:
            return self.fault("PH_UNDERSHOOT", f"pH {ph:.2f} below {a.ph_target - a.undershoot_margin:.2f}; no base "
                              "dosing exists: operator action (e.g. dilution by a refill), then reset")
        if self.phase == "SETTLING":
            ok, why = self.sup.mixing_ready("PH", a.settle_s, ctx)
            if not ok:
                self.next_step = f"pH evaluation waits: {why}"
                return
            if ph is None:
                self.inhibit = f"pH unavailable ({o.why('main.ph')})"
                return
            drop = (self.dose_ph_before or ph) - ph
            self.batch["doses"][-1]["ph_after"] = ph
            if drop < -a.ph_tolerance:
                return self.fault("PH_WRONG_RESPONSE", f"pH rose {-drop:.2f} after an acid dose")
            if drop < a.min_response_ph:
                self.no_response += 1
                self.sup.emit("ACID_NO_RESPONSE", {"drop": drop, "count": self.no_response, "batch": self.batch["batch_id"]},
                              "controller")
                if self.no_response >= a.max_no_response:
                    return self.fault("PH_NO_RESPONSE", f"{self.no_response} settled doses without the expected pH drop "
                                      f"({a.min_response_ph:g}); check probe, acid stock and mixing")
            else:
                self.no_response = 0
            self._to(None, "MONITOR", f"settled: pH {ph:.2f} (drop {drop:.2f})")
        why = self.sup.run_blockers(self.pid, ctx) + (self._dose_blockers(ctx, ph))
        if ph is not None and ph <= a.ph_target + a.ph_tolerance:
            self.inhibit = None
            if self.batch is not None:
                self._end_batch(ctx, "PH_IN_BAND", ph)
            self.next_step = f"pH {ph:.2f} within target {a.ph_target:g} + {a.ph_tolerance:g}: no dose"
            return
        if why:
            self.inhibit = "; ".join(why)
            return
        ok, mwhy = self.sup.mixing_ready("PH", a.settle_s, ctx)
        if not ok:
            self.inhibit = f"mixing/settling: {mwhy}"
            return
        self.inhibit = None
        total = self.batch["total_L"] if self.batch else 0.0
        if total + 1e-9 >= a.max_batch_L:
            return self.fault("ACID_BATCH_LIMIT", f"{total:.1f} L dosed in this batch (maximum {a.max_batch_L:g} L) and "
                              f"pH {ph:.2f} still above target: operator review")
        day = self._day_total(ctx.now)
        vol = min(a.dose_step_L, a.max_batch_L - total, a.max_day_L - day)
        if vol <= 1e-6:
            self.inhibit = f"24 h acid limit reached ({day:.1f} of {a.max_day_L:g} L)"
            return
        if self.batch is None:
            self.batch = {"batch_id": new_id("acid"), "recipe": self.recipe_ref, "start_s": ctx.now, "ph_start": ph,
                          "doses": [], "total_L": 0.0}
            self.sup.emit("ACID_BATCH_STARTED", dict(self.batch), "controller")
        self._start_dose(ctx, vol, ph)

    def _start_dose(self, ctx: Ctx, vol: float, ph: float | None) -> bool:
        self.path = self._dose_path(vol)
        why = self.path.start(ctx)
        if why:
            self.path = None
            self.inhibit = f"dose could not start: {why}"
            return False
        self.dose_ph_before = ph
        self.batch["doses"].append({"n": len(self.batch["doses"]) + 1, "requested_L": vol, "ph_before": ph,
                                    "at_s": ctx.now})
        self._to(None, "DOSING", f"acid dose {vol:g} L (pH {_fmt(ph)} > target band; recipe {self.recipe_ref})")
        return True

    def _dose_tick(self, ctx: Ctx) -> None:
        p = self.path
        p.tick(ctx)
        if not p.finished:
            self.next_step = f"dosing: {p.delivered_L:.2f} of {p.target_L:g} L"
            return
        d = self.batch["doses"][-1]
        d |= {"delivered_L": p.delivered_L, "result": p.result}
        self.batch["total_L"] += p.delivered_L
        self.day.append((ctx.now, p.delivered_L))
        self.sup.emit("ACID_DOSE_ENDED", {"batch": self.batch["batch_id"], "recipe": self.recipe_ref} | d, "controller")
        self.path = None
        if p.failed:
            occ = self.path_alarm(p)
            return self.fault("DOSE_FAILED", f"acid dose failed: {p.result}", occurrence=occ)
        if self.manual_op is not None:
            self.finish_manual("COMPLETED", f"{p.delivered_L:.2f} L dosed ({p.result})")
            self._end_batch(ctx, "MANUAL_DOSE", ctx.obs.num("main.ph"))
            self._to(None, "IDLE", "manual dose complete")
            return
        self._to(None, "SETTLING", f"dose complete {p.delivered_L:.2f} L; settling window starts")

    def _end_batch(self, ctx: Ctx, result: str, ph: float | None) -> None:
        b, self.batch = self.batch, None
        b |= {"end_s": ctx.now, "ph_end": ph, "result": result}
        self.batches.append(b)
        del self.batches[:-50]
        self.last_result = {"kind": "acid_batch"} | b
        self.sup.emit("ACID_BATCH_ENDED", b, "controller")

    def start_manual(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        a = self.recipe.body.acid
        try:
            vol = float(op.get("volume_L"))
        except (TypeError, ValueError):
            return False, "volume_L is required (litres)"
        if not 0 < vol <= a.dose_step_L:
            return False, f"a manual acid dose is limited to the recipe dose step {a.dose_step_L:g} L"
        ph = ctx.obs.num("main.ph")
        why = self._dose_blockers(ctx, ph)
        if ph is not None and ph < a.ph_target - a.undershoot_margin:
            why.append(f"pH {ph:.2f} already below the undershoot limit")
        if why:
            return False, "; ".join(why)
        day = self._day_total(ctx.now)
        if day + vol > a.max_day_L:
            return False, f"24 h acid limit {a.max_day_L:g} L would be exceeded ({day:.1f} L dosed)"
        self.batch = {"batch_id": new_id("acid"), "recipe": self.recipe_ref, "start_s": ctx.now, "ph_start": ph,
                      "doses": [], "total_L": 0.0, "manual": True}
        if not self._start_dose(ctx, vol, ph):
            self.batch = None
            return False, self.inhibit or "dose could not start"
        return True, f"acid dose at most {vol:g} L measured, at most {a.dose_max_time_s:g} s"

    def manual_tick(self, ctx: Ctx) -> None:
        if self.phase == "DOSING":
            self._dose_tick(ctx)

    def on_stopped(self) -> None:
        if self.batch is not None and self.path is None:
            pass
        if self.path is not None:
            p = self.path
            if self.batch and self.batch["doses"]:
                self.batch["doses"][-1] |= {"delivered_L": p.delivered_L, "result": p.result}
                self.batch["total_L"] += p.delivered_L
                self.day.append((self.now, p.delivered_L))
            self.path = None
        if self.batch is not None and self._ctx is not None:
            self._end_batch(self._ctx, "STOPPED", self._ctx.obs.num("main.ph"))

    def fault_cleared(self, ctx: Ctx) -> tuple[bool, str]:
        if self.fault_reason == "PH_UNDERSHOOT" and self.recipe is not None:
            a, ph = self.recipe.body.acid, ctx.obs.num("main.ph")
            if ph is None:
                return False, "pH unavailable"
            if ph < a.ph_target - a.undershoot_margin:
                return False, f"pH {ph:.2f} still below {a.ph_target - a.undershoot_margin:.2f}"
        return True, ""

    def on_reset(self) -> None:
        self.no_response = 0
        self.batch = None
        self.phase = "IDLE"

    def view(self) -> dict:
        v = super().view()
        v |= {"batch": self.batch, "dose": self.path.view() if self.path else None, "no_response_count": self.no_response,
              "day_total_L": self._day_total(self.now), "batches": self.batches[-10:]}
        return v


# =================================================================================================
class Return(Process):
    pid = "RETURN"
    title = "Drainage return"

    def __init__(self, sup):
        super().__init__(sup)
        self.pumps = list(sup.facts.return_pumps)
        self.running_pump: str | None = None
        self.last_started: str | None = None
        self.run_t0 = 0.0
        self.low_since: float | None = None
        self.runs: list[dict] = []
        self.delivered_L = 0.0
        self.manual_limit_s: float | None = None

    @property
    def resources(self) -> list[str]:
        return [f"pump.{p}.cmd" for p in self.pumps]

    def _rt(self):
        return self.recipe.body.return_

    def auto_blockers(self, ctx: Ctx) -> list[str]:
        if ctx.recipe is None:
            return ["no active recipe"]
        if ctx.recipe.body.return_.duty_policy is None:
            return ["the recipe must declare the return duty policy (no implicit rotation or failover)"]
        return []

    def _select_pump(self) -> str:
        pol = self._rt().duty_policy
        if pol == "FIXED_RP1":
            return self.pumps[0]
        if pol == "FIXED_RP2":
            return self.pumps[1 % len(self.pumps)]
        # ALTERNATE_EACH_START: the other pump than the last start (DEMO policy, declared in the recipe)
        if self.last_started is None:
            return self.pumps[0]
        return self.pumps[(self.pumps.index(self.last_started) + 1) % len(self.pumps)]

    def _conditions(self, ctx: Ctx) -> tuple[float | None, float | None, list[str]]:
        o = ctx.obs
        sump, main = o.num("tank.SUMP.level"), o.num("tank.MAIN.level")
        bad = [f"{s} ({o.why(s)})" for s in ("tank.SUMP.level", "tank.MAIN.level", "tank.SUMP.low", "safety.chain_ok")
               if o.get(s) is None]
        return sump, main, bad

    def auto_tick(self, ctx: Ctx) -> None:
        if self.running_pump is None and self.phase in ("MONITOR", "HOLD"):
            self.recipe = ctx.recipe
        rt, o = self._rt(), ctx.obs
        sump, main, bad = self._conditions(ctx)
        if self.phase in ("MONITOR", "HOLD"):
            if bad:
                self.inhibit = f"observations unusable: {bad}"
                return
            full = main >= rt.main_inhibit_L if self.phase == "MONITOR" else main >= rt.main_resume_L
            sump_high = bool(o.get("tank.SUMP.high")) or sump >= rt.sump_start_L
            if full:
                if self.phase != "HOLD":
                    self._to(None, "HOLD", f"destination full: main {main:.0f} L >= {rt.main_inhibit_L:g} L")
                self.inhibit = (f"main tank full ({main:.0f} L): return held until it is below {rt.main_resume_L:g} L; "
                                "the main tank is never filled above the inhibit to hide a sump fault")
                self._conflict(ctx, sump_high and bool(o.get("tank.SUMP.high")), sump, main)
                return
            if self.phase == "HOLD":
                self._to(None, "MONITOR", f"main {main:.0f} L below {rt.main_resume_L:g} L: return available")
            self._conflict(ctx, False, sump, main)
            if o.get("tank.SUMP.low") or sump <= rt.sump_stop_L:
                self.inhibit = None
                self.next_step = f"starts when the sump reaches {rt.sump_start_L:g} L (now {sump:.0f} L)"
                return
            if sump >= rt.sump_start_L:
                why = self.sup.run_blockers(self.pid, ctx)
                if why:
                    self.inhibit = "; ".join(why)
                    return
                return self._start(ctx, self._select_pump(), f"sump {sump:.0f} L >= {rt.sump_start_L:g} L")
            self.inhibit = None
            self.next_step = f"starts when the sump reaches {rt.sump_start_L:g} L (now {sump:.0f} L)"
            return
        self._run_tick(ctx, sump, main, bad)

    def _conflict(self, ctx: Ctx, active: bool, sump, main) -> None:
        if active:
            self.sup.alarms.raise_or_update(
                "SUMP_HIGH_MAIN_FULL", "process.RETURN", severity="CRITICAL", sim_s=ctx.now,
                message=f"Sump high ({sump:.0f} L) while the main tank is full ({main:.0f} L): return is held. Check why "
                        "the main tank is full (refill endpoint, stuck fill valve) and reduce irrigation drainage; do not "
                        "override the inhibit.")
        else:
            self.sup.alarms.clear("SUMP_HIGH_MAIN_FULL", "process.RETURN")

    def _start(self, ctx: Ctx, pump: str, why: str) -> None:
        sig = f"pump.{pump}.cmd"
        busy = self.sup.arbiter.acquire(self.pid, [sig])
        if busy:
            self.inhibit = busy
            return
        self.running_pump, self.last_started, self.run_t0 = pump, pump, ctx.now
        self.low_since, self.delivered_L = None, 0.0
        self._to(None, "STARTING", f"{why}: start {pump} (policy {self._rt().duty_policy}, recipe {self.recipe_ref})")
        if not self.cmd(sig, True, f"return: start {pump}"):
            self.fault("COMMAND_REJECTED", f"start {pump} rejected")

    def _run_tick(self, ctx: Ctx, sump, main, bad) -> None:
        rt, o, now = self._rt(), ctx.obs, ctx.now
        pump = self.running_pump
        if bad:
            return self.fault("OBSERVATION_UNAVAILABLE", f"observations unusable while running: {bad}")
        if not o.get("safety.chain_ok"):
            return self.fault("SAFETY_CHAIN_OPEN", "safety chain reported open")
        if o.get("tank.SUMP.low"):
            return self.fault("SUMP_DRY_RUN", "sump low switch while the return pump runs (stop level not reached in time)")
        flow = o.num("flow.RETURN")
        if flow is not None:
            self.delivered_L += flow * ctx.dt / 60.0
        fb = f"pump.{pump}.running"
        if self.phase == "STARTING":
            if o.bound(fb):
                v = o.get(fb)
                if v is None:
                    return self.fault("OBSERVATION_UNAVAILABLE", f"{fb} unusable")
                if v:
                    self._to(None, "RUNNING", f"{pump} running feedback confirmed")
                elif self.elapsed() >= rt.run_feedback_timeout_s:
                    return self.fault("RUN_FEEDBACK_TIMEOUT", f"{pump} not running {rt.run_feedback_timeout_s:g} s after start")
            else:
                self._to(None, "RUNNING", f"{pump} started (no run feedback bound: flow verifies)")
            return
        if self.phase == "RUNNING":
            if o.bound(fb) and o.get(fb) is False:
                return self.fault("PUMP_STOPPED", f"{pump} running feedback lost")
            if o.bound("flow.RETURN"):
                if flow is None:
                    return self.fault("OBSERVATION_UNAVAILABLE", f"return flow unusable ({o.why('flow.RETURN')})")
                if now - self.run_t0 + 1e-9 >= rt.flow_grace_s and flow < rt.min_flow_L_min:
                    self.low_since = self.low_since if self.low_since is not None else now
                    if now - self.low_since + 1e-9 >= 2.0:
                        return self.fault("NO_FLOW", f"{pump} return flow {flow:.1f} < {rt.min_flow_L_min:g} L/min")
                else:
                    self.low_since = None
            if main >= rt.main_inhibit_L:
                return self._stop_run(ctx, f"destination full: main {main:.0f} L >= {rt.main_inhibit_L:g} L", hold=True)
            if o.get("tank.MAIN.high_high"):
                return self._stop_run(ctx, "main high-high switch active: no inflow to the main tank", hold=True)
            if sump <= rt.sump_stop_L:
                return self._stop_run(ctx, f"sump {sump:.0f} L <= stop level {rt.sump_stop_L:g} L")
            if now - self.run_t0 + 1e-9 >= rt.max_runtime_s:
                return self.fault("MAX_RUNTIME", f"{pump} ran {rt.max_runtime_s:g} s without reaching the stop level")
            if self.manual_limit_s is not None and now - self.run_t0 + 1e-9 >= self.manual_limit_s:
                return self._stop_run(ctx, f"manual duration {self.manual_limit_s:g} s reached")
            self.next_step = f"stops at sump {rt.sump_stop_L:g} L (now {sump:.0f} L)"
            return
        if self.phase == "STOPPING_RUN":
            v = o.get(fb) if o.bound(fb) else False
            q = flow if flow is not None else 0.0
            if (v is False and q <= FLOW_ZERO_L_MIN) or self.elapsed() >= 10.0:
                if v or q > FLOW_ZERO_L_MIN:
                    return self.fault("STOP_NOT_VERIFIED", f"{pump} still running/flowing after stop")
                self.sup.arbiter.release(self.pid)
                run = {"pump": pump, "start_s": self.run_t0, "end_s": now, "delivered_L_measured": self.delivered_L,
                       "reason": self._stop_reason, "recipe": self.recipe_ref}
                self.runs.append(run)
                del self.runs[:-50]
                self.last_result = {"kind": "return_run"} | run
                self.sup.emit("RETURN_RUN_ENDED", run, "controller")
                self.running_pump = None
                if self.manual_op is not None:
                    self.finish_manual("COMPLETED", self._stop_reason)
                    self.manual_limit_s = None
                    self._to(None, "IDLE", "manual return run complete")
                else:
                    self._to(None, "HOLD" if self._hold else "MONITOR", "return pump stop verified")

    def _stop_run(self, ctx: Ctx, why: str, hold: bool = False) -> None:
        self._stop_reason, self._hold = why, hold
        self._to(None, "STOPPING_RUN", why)
        self.cmd(f"pump.{self.running_pump}.cmd", False, f"return: stop ({why})")

    def start_manual(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        rt = self._rt()
        pump = op.get("pump")
        try:
            dur = float(op.get("duration_s"))
        except (TypeError, ValueError):
            return False, "duration_s is required (seconds)"
        if pump not in self.pumps:
            return False, f"pump must be one of {self.pumps}"
        if not 0 < dur <= rt.max_runtime_s:
            return False, f"duration must be 1..{rt.max_runtime_s:g} s"
        sump, main, bad = self._conditions(ctx)
        if bad:
            return False, f"observations unusable: {bad}"
        if main >= rt.main_inhibit_L:
            return False, f"main tank full ({main:.0f} L >= {rt.main_inhibit_L:g} L)"
        if sump <= rt.sump_stop_L or ctx.obs.get("tank.SUMP.low"):
            return False, f"sump at or below the stop level ({sump:.0f} L)"
        self.manual_limit_s = dur
        self._start(ctx, pump, f"MANUAL by {actor}")
        if self.running_pump is None:
            self.manual_limit_s = None
            return False, self.inhibit or "could not start"
        return True, f"{pump} for at most {dur:g} s; stops at sump {rt.sump_stop_L:g} L or main {rt.main_inhibit_L:g} L"

    def manual_tick(self, ctx: Ctx) -> None:
        if self.running_pump is not None:
            sump, main, bad = self._conditions(ctx)
            self._run_tick(ctx, sump, main, bad)

    def start_stop(self) -> None:
        self._stop_t0 = self.now
        for p in self.pumps:
            if f"pump.{p}.cmd" in self.on:
                self.cmd(f"pump.{p}.cmd", False, "stop sequence: return pump off")

    def stop_tick(self, ctx: Ctx) -> bool:
        done = super().stop_tick(ctx)
        if done:
            self.running_pump, self.manual_limit_s = None, None
        return done

    def outputs_feedback_active(self, ctx: Ctx) -> list[str]:
        o, out = ctx.obs, []
        for p in self.pumps:
            s = f"pump.{p}.running"
            if o.bound(s) and o.get(s):
                out.append(s)
        q = o.num("flow.RETURN")
        if o.bound("flow.RETURN") and q is not None and q > FLOW_ZERO_L_MIN:
            out.append(f"flow.RETURN={q:.1f}")
        return out

    def fault_cleared(self, ctx: Ctx) -> tuple[bool, str]:
        if not ctx.obs.get("safety.chain_ok"):
            return False, "safety chain still open"
        if self.fault_reason == "SUMP_DRY_RUN" and ctx.obs.get("tank.SUMP.low"):
            return False, "sump low switch still active"
        return True, ""

    def view(self) -> dict:
        v = super().view()
        v |= {"running_pump": self.running_pump, "last_started": self.last_started,
              "duty_policy": self.recipe.body.return_.duty_policy if self.recipe else None,
              "delivered_L_measured": self.delivered_L, "runs": self.runs[-10:]}
        return v


# =================================================================================================
class StockFill(_PathProcess):
    title = "Stock tank fill"

    def __init__(self, sup, stock: str):
        self.stock = stock
        self.pid = f"STOCK_FILL_{stock}"
        self.title = f"Stock {stock} water fill"
        super().__init__(sup)
        self.path: Path | None = None
        self.fills: list[dict] = []
        self.target_level: float | None = None

    @property
    def resources(self) -> list[str]:
        return [f"valve.FILL_{self.stock}.cmd", f"stock.{self.stock}"]

    def feedback_signals(self) -> list[str]:
        return [f"flow.FILL_{self.stock}"]

    def active_paths(self) -> list[Path]:
        return [self.path] if self.path else []

    def _sf(self):
        return self.recipe.body.stock_fill[self.stock]

    def _path(self, target_level: float, max_time: float) -> Path:
        sf = self._sf()
        lvl = f"tank.{self.stock}.level"

        def guard(c: Ctx) -> str | None:
            lv = c.obs.num(lvl)
            if lv is None:
                return f"FAIL:LEVEL_UNAVAILABLE ({c.obs.why(lvl)})"
            if lv >= sf.high_inhibit_L:
                return f"HIGH_INHIBIT ({lv:.0f} L)"
            if lv >= target_level:
                return f"TARGET_LEVEL_REACHED ({lv:.0f} L)"
            return None
        return Path(self, f"FILL_{self.stock}", valve=f"valve.FILL_{self.stock}.cmd",
                    open_feedback=f"valve.FILL_{self.stock}.open_limit", flow=f"flow.FILL_{self.stock}", level=lvl,
                    max_time_s=max_time, rise_L=sf.min_rise_L, rise_window_s=sf.rise_window_s, stop_when=guard,
                    extra_resources=[f"stock.{self.stock}"])

    def _begin(self, ctx: Ctx, target: float, max_time: float, why: str) -> tuple[bool, str]:
        self.path = self._path(target, max_time)
        err = self.path.start(ctx)
        if err:
            self.path = None
            return False, err
        self.target_level = target
        self.sup.invalidate_stock(self.stock, f"mains water fill started ({why})")
        self._to(None, "FILLING", f"{why}: fill stock {self.stock} to {target:g} L (recipe {self.recipe_ref})")
        return True, f"fill to {target:g} L, at most {max_time:g} s; stops at {self._sf().high_inhibit_L:g} L"

    def auto_blockers(self, ctx: Ctx) -> list[str]:
        if ctx.recipe is None or self.stock not in ctx.recipe.body.stock_fill:
            return ["the active recipe has no filling settings for this stock tank"]
        return []

    def auto_tick(self, ctx: Ctx) -> None:
        if self.phase == "FILLING":
            return self._fill_tick(ctx)
        self.recipe = ctx.recipe
        sf = self._sf()
        lv = ctx.obs.num(f"tank.{self.stock}.level")
        if lv is None:
            self.inhibit = f"level unavailable ({ctx.obs.why(f'tank.{self.stock}.level')})"
            return
        self.inhibit = None
        if lv <= sf.auto_trigger_L:
            why = self.sup.run_blockers(self.pid, ctx)
            if why:
                self.inhibit = "; ".join(why)
                return
            ok, msg = self._begin(ctx, sf.target_L, sf.max_time_s, f"AUTO: level {lv:.0f} L <= {sf.auto_trigger_L:g} L")
            if not ok:
                self.inhibit = msg
            return
        self.next_step = f"auto-fills at {sf.auto_trigger_L:g} L (now {lv:.0f} L)"

    def _fill_tick(self, ctx: Ctx) -> None:
        p = self.path
        p.tick(ctx)
        if not p.finished:
            self.next_step = f"filling to {self.target_level:g} L (+{p.delivered_L:.0f} L)"
            return
        rec = {"stock": self.stock, "result": p.result, "failed": p.failed, "added_L_by_level": p.delivered_L,
               "end_s": ctx.now, "recipe": self.recipe_ref}
        self.fills.append(rec)
        self.last_result = {"kind": "stock_fill"} | rec
        self.sup.emit("STOCK_FILL_ENDED", rec, "controller")
        self.path = None
        if p.failed:
            occ = self.path_alarm(p)
            return self.fault("FILL_FAILED", f"stock {self.stock} fill failed: {p.result}", occurrence=occ)
        if self.manual_op is not None:
            self.finish_manual("COMPLETED", p.result or "")
            self._to(None, "IDLE", "manual fill complete")
        else:
            self._to(None, "MONITOR", f"fill ended: {p.result}")

    def start_manual(self, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        sf = self._sf()
        try:
            target = float(op.get("target_L"))
        except (TypeError, ValueError):
            return False, "target_L is required (final stock level, litres)"
        lv = ctx.obs.num(f"tank.{self.stock}.level")
        if lv is None:
            return False, f"level unavailable ({ctx.obs.why(f'tank.{self.stock}.level')})"
        if not lv < target < sf.high_inhibit_L:
            return False, f"target must be above the current level ({lv:.0f} L) and below {sf.high_inhibit_L:g} L"
        return self._begin(ctx, target, sf.max_time_s, f"MANUAL by {actor}")

    def manual_tick(self, ctx: Ctx) -> None:
        if self.phase == "FILLING":
            self._fill_tick(ctx)

    def on_stopped(self) -> None:
        self.path = None

    def view(self) -> dict:
        v = super().view()
        v |= {"stock": self.stock, "fill": self.path.view() if self.path else None, "target_level_L": self.target_level,
              "fills": self.fills[-10:]}
        return v
