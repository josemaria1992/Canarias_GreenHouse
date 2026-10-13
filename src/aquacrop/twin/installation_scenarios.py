"""Installation scenario presets with machine-checked expectations (brief §7; acceptance PH/PC gates).

Same rules as the demo presets: optional DEMO twin overrides (validated), timed journaled inputs that
go through the same operator paths, a duration and checks. Recipes used by a scenario are created,
validated and activated through the recipe store like an operator would. Every number here is a
synthetic test condition, not a production setting.
"""
from __future__ import annotations

import copy

from ..control.recipes import demo_preset
from ..domain.model import ControllerState, Quality
from .scenarios import SCN, Check, Scenario, with_twin_overrides

ACK_ALL = ("A", "B", "ACID")
S_ID = "scenario"


def _ack_stocks(at: float = 0.0, stocks=ACK_ALL) -> list[tuple[float, str, dict]]:
    return [(at, "STOCK_ACK", {"stock": s, "note": "scenario: prepared"}) for s in stocks]


def _start(pid: str, at: float = 0.0, target: str = "AUTO") -> tuple[float, str, dict]:
    return (at, "PROCESS_REQUEST", {"process": pid, "target": target})


def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def recipe(changes: dict, at: float = 0.0, sectors=("S1", "S2"), version: int = 1) -> list[tuple[float, str, dict]]:
    """Steps that save, validate and activate the DEMO body with `changes` as an operator recipe."""
    body = _merge(demo_preset(list(sectors)).body_json(), changes)
    return [(at, "RECIPE_DRAFT", {"recipe_id": S_ID, "name": "scenario recipe (synthetic)", "body": body,
                                  "version": None}),
            (at, "RECIPE_VALIDATE", {"recipe_id": S_ID, "version": version}),
            (at, "RECIPE_ACTIVATE", {"recipe_id": S_ID, "version": version, "reason": "scenario"})]


def tw(initial: dict | None = None, dt: float | None = None, **extra) -> dict:
    out: dict = dict(extra)
    if initial:
        out.setdefault("dynamics", {})["initial_L"] = initial
    if dt is not None:
        out["dt_s"] = dt
    return out


# ---------------------------------------------------------------------------- checks
def proc(pid: str, state: str, reason: str | None = None) -> Check:
    def c(rt) -> list[str]:
        p = rt.sup.by_id[pid]
        errs = []
        if p.state.value != state:
            errs.append(f"{pid} is {p.state.value}/{p.phase} ({p.fault_reason}: {p.fault_detail}), expected {state}")
        elif reason is not None and p.fault_reason != reason:
            errs.append(f"{pid} fault {p.fault_reason} ({p.fault_detail}), expected {reason}")
        return errs
    return c


def idle(pid: str) -> Check:
    def c(rt) -> list[str]:
        p = rt.sup.by_id[pid]
        errs = []
        if p.stopping or p.on:
            errs.append(f"{pid} still stopping={p.stopping} on={sorted(p.on)}")
        held = rt.sup.arbiter.held_by(pid)
        if held:
            errs.append(f"{pid} still owns {held}")
        return errs
    return c


def outputs_off(rt) -> list[str]:
    on = [p for p, v in rt.backend.state.coils.items() if v] + [s for s, v in rt.adapter_state.items() if v]
    return [f"outputs still ON: {on}"] if on else []


def ledger(rt) -> list[str]:
    errs = [f"ledger residual {f.residual_L:.3g} L at tick {f.tick}" for f in rt.invariant_failures[:1]]
    sp = rt.plant.species_residual()
    bad = {k: v for k, v in sp.items() if abs(v) > 1e-3}
    return errs + ([f"species residual {bad}"] if bad else [])


def no_hazards(rt) -> list[str]:
    return [f"plant hazard {h['kind']}: {h['detail']}" for h in rt.plant.hazards]


def alarm(rule: str, target: str, active: bool = True) -> Check:
    return lambda rt: [] if rt.alarms.is_active(rule, target) == active else [
        f"alarm {rule}/{target} active={rt.alarms.is_active(rule, target)}, expected {active}"]


def cycles(expected: list[tuple[str, str]], min_s: float = 300.0) -> Check:
    def c(rt) -> list[str]:
        log = rt.sup.by_id["IRRIGATION"].cycle_log
        got = [(x["sector"], x["pump"]) for x in log]
        errs = [] if got[:len(expected)] == expected else [f"irrigation steps {got}, expected {expected}"]
        short = [x for x in log if x["mode"] == "AUTO" and x["irrigated_s"] + 1e-6 < min_s]
        return errs + ([f"steps shorter than {min_s:g} s: {short}"] if short else [])
    return c


def changeover_order(rt) -> list[str]:
    """Brief PC-02: every step follows select -> open -> lead -> start -> verify -> irrigate -> stop drive
    -> settle -> close -> deselect; no pump selection change and no valve change while the drive runs."""
    want = ["SELECT", "OPEN", "LEAD", "START", "VERIFY_FLOW", "IRRIGATE", "STOP_DRIVE", "STOP_SETTLE", "CLOSE",
            "DESELECT"]
    seq = [e["payload"]["to_phase"] for e in rt.events if e["kind"] == "PROCESS_TRANSITION"
           and e["payload"]["process"] == "IRRIGATION" and e["payload"]["from_phase"] != e["payload"]["to_phase"]]
    errs, i = [], 0
    while i < len(seq):
        if seq[i] == "SELECT":
            got = seq[i:i + len(want)]
            if got != want[:len(got)]:
                errs.append(f"changeover order {got}")
            i += len(want)
        else:
            i += 1
    return errs + no_hazards(rt)


def batch(result: str, nutrients_prefix: str | None = None) -> Check:
    def c(rt) -> list[str]:
        b = rt.sup.by_id["REFILL"].batches
        if not b:
            return ["no refill batch completed"]
        last = b[-1]
        errs = [] if last["result"] == result else [f"batch {last['result']} ({last['nutrients']}; "
                                                    f"{ {k: v['result'] for k, v in last.get('paths', {}).items()} }), "
                                                    f"expected {result}"]
        if nutrients_prefix and not str(last["nutrients"]).startswith(nutrients_prefix):
            errs.append(f"nutrients {last['nutrients']}, expected {nutrients_prefix}...")
        return errs
    return c


def n_batches(n: int) -> Check:
    return lambda rt: [] if len(rt.sup.by_id["REFILL"].batches) == n else [
        f"{len(rt.sup.by_id['REFILL'].batches)} refill batches, expected {n}"]


def ec_steps_keep_ratio(rt) -> list[str]:
    steps = [e["payload"] for e in rt.events if e["kind"] == "REFILL_EC_STEP"]
    if not steps:
        return ["no EC step"]
    return [f"A:B {s['a_L']}:{s['b_L']}" for s in steps if abs(s["a_L"] - s["b_L"]) > 1e-9]


def acid_result(result: str) -> Check:
    def c(rt) -> list[str]:
        b = rt.sup.by_id["ACID"].batches
        return [] if b and b[-1]["result"] == result else [f"acid batches {[x['result'] for x in b]}, "
                                                          f"expected last {result}"]
    return c


def no_event(kind: str) -> Check:
    return lambda rt: [f"unexpected {kind}: {e['payload']}" for e in rt.events if e["kind"] == kind][:3]


def event(kind: str, **match) -> Check:
    def c(rt) -> list[str]:
        for e in rt.events:
            if e["kind"] == kind and all(e["payload"].get(k) == v for k, v in match.items()):
                return []
        return [f"no {kind} event matching {match}"]
    return c


def return_runs(pumps: list[str]) -> Check:
    def c(rt) -> list[str]:
        got = [r["pump"] for r in rt.sup.by_id["RETURN"].runs]
        return [] if got[:len(pumps)] == pumps else [f"return runs {got}, expected {pumps}"]
    return c


def quality(point: str, q: Quality) -> Check:
    return lambda rt: [] if rt.samples[point].quality == q else [f"{point} quality {rt.samples[point].quality}"]


def stock_status(stock: str, status: str) -> Check:
    return lambda rt: [] if rt.sup.stocks[stock].status == status else [
        f"stock {stock} {rt.sup.stocks[stock].status}, expected {status}"]


def manual_result(pid: str, result: str) -> Check:
    def c(rt) -> list[str]:
        r = rt.sup.by_id[pid].last_result
        return [] if r and r.get("result") == result else [f"{pid} last result {r}, expected {result}"]
    return c


# ---------------------------------------------------------------------------- presets
IRR = _start("IRRIGATION")
FAST = 0.5      # coarser tick for long process scenarios (control period stays 0.5 s)

# A small refill (final level close to the trigger) so a batch completes in minutes.
SMALL_REFILL = {"main": {"refill_trigger_L": 8000, "rearm_hysteresis_L": 500},
                "refill": {"final_level_L": 9000, "ec": {"settle_s": 300}}}


def presets() -> dict[str, Scenario]:
    S = [
        # -------------------------------------------------------------- irrigation (PC-01..03, PC-13)
        Scenario("irrigation_alternation", "AUTO: two full cycles P1/S1 then P2/S2, 300 s each, with the verified "
                 "shared-drive changeover (owner sequence).", 1250, [IRR],
                 checks=[cycles([("S1", "MP1"), ("S2", "MP2"), ("S1", "MP1"), ("S2", "MP2")]), changeover_order,
                         proc("IRRIGATION", "AUTO"), ledger]),
        Scenario("recipe_edit_next_cycle", "A recipe activated mid-step applies from the next cycle only.", 1300,
                 [IRR] + recipe({"irrigation": {"sequence": [{"sector": "S1", "duration_s": 120},
                                                             {"sector": "S2", "duration_s": 120}]}}, at=100),
                 checks=[lambda rt: [] if [round(x["irrigated_s"]) for x in
                                           rt.sup.by_id["IRRIGATION"].cycle_log[:4]] == [300, 300, 120, 120]
                         else [f"durations {[round(x['irrigated_s']) for x in rt.sup.by_id['IRRIGATION'].cycle_log]}"],
                         lambda rt: [] if rt.sup.by_id["IRRIGATION"].cycle_log[0]["recipe"] == "demo-sim@v1" and
                         rt.sup.by_id["IRRIGATION"].cycle_log[2]["recipe"] == "scenario@v1" else ["recipe refs"]]),
        Scenario("manual_irrigation_operator_pump", "MANUAL: operator chooses P2 for S1, bounded 60 s; AUTO is "
                 "refused while MANUAL; after stop, AUTO starts cleanly.", 200,
                 [_start("IRRIGATION", 0, "MANUAL"),
                  (1, "PROCESS_MANUAL", {"process": "IRRIGATION", "op": {"pump": "MP2", "sector": "S1",
                                                                          "duration_s": 60}}),
                  _start("IRRIGATION", 20),                                       # refused: MANUAL active
                  _start("IRRIGATION", 90, "STOPPED"), _start("IRRIGATION", 95)],
                 checks=[lambda rt: [] if rt.sup.by_id["IRRIGATION"].cycle_log[0]["pump"] == "MP2" and
                         rt.sup.by_id["IRRIGATION"].cycle_log[0]["sector"] == "S1" else ["manual step not MP2/S1"],
                         event("PROCESS_REQUEST_REJECTED", process="IRRIGATION", target="AUTO"),
                         proc("IRRIGATION", "AUTO"), changeover_order]),
        Scenario("manual_bounds_rejected", "MANUAL operations outside the recipe bounds are rejected.", 5,
                 [_start("IRRIGATION", 0, "MANUAL"),
                  (1, "PROCESS_MANUAL", {"process": "IRRIGATION", "op": {"pump": "MP1", "sector": "S1",
                                                                          "duration_s": 99999}}),
                  (1, "PROCESS_MANUAL", {"process": "IRRIGATION", "op": {"pump": "MP3", "sector": "S1",
                                                                          "duration_s": 10}})],
                 checks=[lambda rt: [] if len([e for e in rt.events if e["kind"] == "PROCESS_MANUAL_OP_REJECTED"]) == 2
                         else ["manual ops not rejected"], outputs_off]),
        Scenario("raw_command_refused", "Raw operator DO commands are refused on the installation twin.", 2,
                 [(0.5, "COMMAND", {"point": "relay_A.DO1", "desired": True, "reason": "scenario"})],
                 checks=[outputs_off]),
        # -------------------------------------------------------------- irrigation faults (PC-12)
        Scenario("vfd_trip_recovery", "Drive trips mid-step: lockout; reset refused while the drive fault is "
                 "active; clear, acknowledge, reset; restart only on a deliberate AUTO request.", 200,
                 [IRR, (60, "ACTUATOR_FAULT", {"kind": "drive", "ident": "MAIN", "fault": "TRIP"}),
                  (80, "PROCESS_RESET", {"process": "IRRIGATION"}),               # refused: drive fault
                  (90, "ACTUATOR_FAULT", {"kind": "drive", "ident": "MAIN", "fault": None}),
                  (95, "PROCESS_RESET", {"process": "IRRIGATION"}),               # refused: not acknowledged
                  (100, "ACK", {"rule_id": "PROC_DRIVE_FAULT", "target": "process.IRRIGATION"}),
                  (101, "PROCESS_RESET", {"process": "IRRIGATION"}),
                  _start("IRRIGATION", 110)],
                 checks=[lambda rt: [] if [e["payload"]["reason"][:20] for e in rt.events
                                           if e["kind"] == "PROCESS_RESET_REJECTED"][:2] ==
                         ["fault condition sti", "acknowledge the fau"][:2] or len(
                             [e for e in rt.events if e["kind"] == "PROCESS_RESET_REJECTED"]) == 2 else
                         ["expected two rejected resets"],
                         proc("IRRIGATION", "AUTO"), lambda rt: [] if rt.sup.by_id["IRRIGATION"].phase == "IRRIGATE"
                         else [f"phase {rt.sup.by_id['IRRIGATION'].phase}"]]),
        Scenario("vfd_stop_feedback_missing", "Drive keeps reporting running after the stop command: the valve "
                 "and pump selection are held (no switching under load); STOP_NOT_VERIFIED.", 340,
                 [IRR, (200, "ACTUATOR_FAULT", {"kind": "drive", "ident": "MAIN", "fault": "STUCK_ON"})],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "STOP_NOT_VERIFIED"), no_hazards,
                         lambda rt: [] if rt.backend.state.coils["relay_A.DO1"] and rt.backend.state.coils["relay_A.DO3"]
                         else ["contactor/valve released while the drive may run"]]),
        Scenario("drive_no_run", "Drive never reports running.", 30,
                 [(0, "ACTUATOR_FAULT", {"kind": "drive", "ident": "MAIN", "fault": "NO_RUN"}), IRR],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "DRIVE_START_TIMEOUT"), outputs_off, idle("IRRIGATION")]),
        Scenario("main_pump_no_flow", "P1 runs but delivers no flow.", 60,
                 [(0, "ACTUATOR_FAULT", {"kind": "pump", "ident": "MP1", "fault": "NO_FLOW"}), IRR],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "NO_FLOW"), outputs_off, idle("IRRIGATION"), no_hazards]),
        Scenario("contactor_no_close", "K1 never closes.", 10,
                 [(0, "ACTUATOR_FAULT", {"kind": "contactor", "ident": "MP1", "fault": "NO_CLOSE"}), IRR],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "SELECTION_TIMEOUT"), outputs_off]),
        Scenario("contactor_welded", "K1 welded: deselection fails after the step; never switched under load.",
                 340, [(0, "ACTUATOR_FAULT", {"kind": "contactor", "ident": "MP1", "fault": "WELDED"}), IRR],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "DESELECT_TIMEOUT"), no_hazards]),
        Scenario("sector_valve_stuck_closed", "S1 valve does not open: the drive never starts.", 12,
                 [(0, "ACTUATOR_FAULT", {"kind": "valve", "ident": "S1", "fault": "STUCK_CLOSED"}), IRR],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "VALVE_OPEN_TIMEOUT"), outputs_off,
                         lambda rt: [] if not any(e["kind"] == "DRIVE_ADAPTER_COMMAND" and e["payload"]["desired"]
                                                  for e in rt.events) else ["drive started with the valve closed"]]),
        Scenario("sector_valve_stuck_open", "S1 valve does not close after the step.", 330,
                 [IRR, (100, "ACTUATOR_FAULT", {"kind": "valve", "ident": "S1", "fault": "STUCK_OPEN"})],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "VALVE_CLOSE_TIMEOUT"), no_hazards]),
        Scenario("safety_chain_open", "Safety chain opens: every running process locks out; restoring the chain "
                 "restarts nothing.", 120,
                 _ack_stocks() + [IRR, _start("RETURN"), _start("REFILL"),
                                  (50, "ACTUATOR_FAULT", {"kind": "safety", "ident": "chain", "fault": "CHAIN_OPEN"}),
                                  (70, "ACTUATOR_FAULT", {"kind": "safety", "ident": "chain", "fault": None})],
                 twin_overrides=tw({"MAIN": 7900}),
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "SAFETY_CHAIN_OPEN"),
                         proc("REFILL", "FAULT_LOCKOUT", "SAFETY_CHAIN_OPEN"),
                         proc("RETURN", "FAULT_LOCKOUT", "SAFETY_CHAIN_OPEN"), outputs_off,
                         alarm("SAFETY_CHAIN_OPEN", "relay_B.DI8", active=False)]),
        Scenario("main_low_low", "Main tank at the dry-run guard: irrigation does not start.", 5,
                 [IRR], twin_overrides=tw({"MAIN": 2000}),
                 checks=[proc("IRRIGATION", "STOPPED"), event("PROCESS_REQUEST_REJECTED", process="IRRIGATION")]),
        Scenario("main_source_empty", "Main tank drains below the dry-run guard during irrigation.", 200,
                 [IRR, (0, "PROCESS_REQUEST", {"process": "RETURN", "target": "STOPPED"})],
                 twin_overrides=tw({"MAIN": 2900, "SUMP": 0}),
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "MAIN_LOW_LOW"), outputs_off, ledger]),
        Scenario("main_level_open_loop", "Main level 4-20 mA loop open during irrigation: INVALID, irrigation "
                 "faults, refill cannot latch.", 40,
                 _ack_stocks() + [IRR, _start("REFILL"),
                                  (20, "OVERRIDE_SET", {"point": "analog_A.AI1", "raw": 0, "reason": "scenario: open loop"})],
                 checks=[quality("analog_A.AI1", Quality.INVALID), alarm("AI_SIGNAL_INVALID", "analog_A.AI1"),
                         proc("IRRIGATION", "FAULT_LOCKOUT", "OBSERVATION_UNAVAILABLE"),
                         lambda rt: [] if (rt.sup.by_id["REFILL"].inhibit or "").startswith("main level unavailable")
                         else [f"refill inhibit {rt.sup.by_id['REFILL'].inhibit}"], outputs_off]),
        Scenario("stale_acquisition", "Acquisition stops while irrigating: stale observations fault the step.", 30,
                 [IRR, (15, "ACQ_STALL", {"stalled": True})],
                 checks=[proc("IRRIGATION", "FAULT_LOCKOUT", "OBSERVATION_UNAVAILABLE"),
                         alarm("ACQUISITION_STALE", "acquisition")]),
        Scenario("module_timeout", "analog_B stops answering: sector flow unavailable, irrigation faults; "
                 "other modules unaffected.", 30,
                 [IRR, (15, "DEVICE_FAULT", {"device_uid": "analog_B", "fault": "TIMEOUT"})],
                 checks=[alarm("DEVICE_COMM", "analog_B"), proc("IRRIGATION", "FAULT_LOCKOUT", "OBSERVATION_UNAVAILABLE"),
                         lambda rt: [p for p, s in rt.samples.items() if s.device_uid == "analog_A"
                                     and s.quality == Quality.UNAVAILABLE]]),
        # -------------------------------------------------------------- refill (PC-04..06, PC-09)
        Scenario("refill_ec_target", "Refill latches once at the trigger, runs water and staged A:B steps "
                 "concurrently, ends at the final level with EC in band; re-arms only above trigger + hysteresis.",
                 1500, _ack_stocks() + recipe(SMALL_REFILL) + [_start("REFILL", 1)],
                 twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[batch("COMPLETED", "EC_TARGET_REACHED"), n_batches(1), ec_steps_keep_ratio,
                         proc("REFILL", "AUTO"), ledger,
                         lambda rt: [] if rt.sup.by_id["REFILL"].armed else ["refill did not re-arm"]]),
        Scenario("refill_fixed_volumes", "FIXED_VOLUMES + FIXED_ADDITIONS: water and A/B run concurrently to "
                 "their measured endpoints; EC validated after settling.", 1100,
                 _ack_stocks() + recipe({"refill": {"endpoint_mode": "FIXED_ADDITIONS", "final_level_L": None, "nutrient_mode": "FIXED_VOLUMES",
                                                    "water": {"target_L": 1000},
                                                    "fixed": {"a_L": 15, "b_L": 15, "ec_min_mS_cm": 1.4,
                                                              "ec_max_mS_cm": 2.2, "validation_settle_s": 300}}})
                 + [_start("REFILL", 1)], twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[batch("COMPLETED", "EC_VALID"), ledger,
                         lambda rt: [] if all(abs(p["delivered_L"] - p["target_L"]) < max(1.0, 0.01 * p["target_L"]) for p in
                                              rt.sup.by_id["REFILL"].batches[-1]["paths"].values()) else
                         [f"delivered {[(k, round(p['delivered_L'], 2)) for k, p in rt.sup.by_id['REFILL'].batches[-1]['paths'].items()]}"]]),
        Scenario("refill_fixed_ec_out_of_band", "FIXED_VOLUMES too small for the band: validation fails, alarm, "
                 "no automatic correction.", 1100,
                 _ack_stocks() + recipe({"refill": {"endpoint_mode": "FIXED_ADDITIONS", "final_level_L": None, "nutrient_mode": "FIXED_VOLUMES",
                                                    "water": {"target_L": 1000},
                                                    "fixed": {"a_L": 1, "b_L": 1, "ec_min_mS_cm": 1.55,
                                                              "ec_max_mS_cm": 2.2, "validation_settle_s": 300}}})
                 + [_start("REFILL", 1)], twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[batch("OUT_OF_SPEC", "EC_OUT_OF_BAND"), alarm("REFILL_EC_VALIDATION_FAILED", "process.REFILL")]),
        Scenario("refill_stock_not_prepared", "Stock preparation not acknowledged: nutrient steps never start; "
                 "batch INCOMPLETE with an alarm.", 900,
                 recipe(SMALL_REFILL) + [_start("REFILL", 1)], twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[batch("INCOMPLETE", "STOPPED"), no_event("REFILL_EC_STEP"),
                         lambda rt: [] if rt.plant.ledger.transferred_L["A"] == 0 else ["stock A transferred"]]),
        Scenario("refill_stock_empty", "Stock A at its reserve: no step starts (ratio kept), INCOMPLETE.", 900,
                 _ack_stocks() + recipe(SMALL_REFILL) + [_start("REFILL", 1)],
                 twin_overrides=tw({"MAIN": 7900, "A": 250}, FAST),
                 checks=[batch("INCOMPLETE", "STOPPED"), alarm("STOCK_A_LEVEL_LOW", "analog_A.AI3"),
                         lambda rt: [] if rt.plant.ledger.transferred_L["B"] == 0 else ["B dosed without A"]]),
        Scenario("refill_transfer_no_flow", "Transfer pump A runs without flow: path fails, partner stopped, "
                 "batch FAILED, refill locks out.", 900,
                 _ack_stocks() + recipe(SMALL_REFILL) +
                 [(0, "ACTUATOR_FAULT", {"kind": "pump", "ident": "TP_A", "fault": "NO_FLOW"}), _start("REFILL", 1)],
                 twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[batch("FAILED"), proc("REFILL", "FAULT_LOCKOUT", "PATH_FAILED"),
                         alarm("PROC_PATH_FAULT", "process.REFILL.A"), outputs_off, idle("REFILL")]),
        Scenario("refill_transfer_timeout", "A transfer step exceeds its maximum time: path fails.", 900,
                 _ack_stocks() + recipe(SMALL_REFILL | {"refill": {**SMALL_REFILL["refill"], "transfer_max_time_s": 20}})
                 + [_start("REFILL", 1)], twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[batch("FAILED"), proc("REFILL", "FAULT_LOCKOUT", "PATH_FAILED")]),
        Scenario("stock_fill_invalidates", "Filling stock A with water invalidates its preparation; the next "
                 "refill does not dose until the operator acknowledges again.", 900,
                 _ack_stocks() + recipe(SMALL_REFILL) +
                 [_start("STOCK_FILL_A", 0, "MANUAL"),
                  (1, "PROCESS_MANUAL", {"process": "STOCK_FILL_A", "op": {"target_L": 3100}}), _start("REFILL", 200)],
                 twin_overrides=tw({"MAIN": 7900}, FAST),
                 checks=[stock_status("A", "INVALIDATED"), manual_result("STOCK_FILL_A", "COMPLETED"),
                         batch("INCOMPLETE", "STOPPED"), no_event("REFILL_EC_STEP")]),
        Scenario("refill_concurrency", "Refill batch, irrigation, return and acid run together without "
                 "ownership conflicts; the volume ledger closes.", 1500,
                 _ack_stocks() + recipe(SMALL_REFILL) +
                 [_start("REFILL", 1), _start("IRRIGATION", 1), _start("RETURN", 1), _start("ACID", 1)],
                 twin_overrides=tw({"MAIN": 8100, "SUMP": 2000}, FAST),
                 checks=[n_batches(1), no_event("PROCESS_COMMAND_REFUSED"), ledger, no_hazards,
                         lambda rt: [p.pid for p in rt.sup.processes if p.state == ControllerState.FAULT_LOCKOUT]]),
        # -------------------------------------------------------------- acid (PC-07, PC-08)
        Scenario("acid_to_target", "pH above target: staged doses with settling until in band.", 3000,
                 _ack_stocks() + recipe({"acid": {"dose_step_L": 6, "settle_s": 300}}) + [_start("ACID", 1), IRR],
                 twin_overrides=tw(None, FAST, chemistry={"initial_main_ph": 6.8}),
                 checks=[acid_result("PH_IN_BAND"), lambda rt: [] if 5.5 <= rt.plant.bulk_ph_ec()[0] <= 6.2 else
                         [f"bulk pH {rt.plant.bulk_ph_ec()[0]:.2f}"], proc("ACID", "AUTO")]),
        Scenario("acid_already_in_band", "pH already within target: no dose.", 120,
                 _ack_stocks() + [_start("ACID", 1)],
                 checks=[no_event("ACID_DOSE_ENDED"), proc("ACID", "AUTO")]),
        Scenario("acid_undershoot", "pH below target minus the undershoot margin: fault, no base dosing.", 10,
                 _ack_stocks() + [_start("ACID", 1)], twin_overrides=tw(chemistry={"initial_main_ph": 5.3}),
                 checks=[proc("ACID", "FAULT_LOCKOUT", "PH_UNDERSHOOT"), no_event("ACID_DOSE_ENDED")]),
        Scenario("acid_no_response", "pH probe frozen: settled doses show no response; fault after the "
                 "configured count.", 2500,
                 _ack_stocks() + recipe({"acid": {"settle_s": 300}}) +
                 [(0.5, "FREEZE", {"point": "analog_A.AI6"}), _start("ACID", 1)],
                 twin_overrides=tw(None, FAST, chemistry={"initial_main_ph": 6.8}),
                 checks=[proc("ACID", "FAULT_LOCKOUT", "PH_NO_RESPONSE")]),
        Scenario("acid_batch_limit", "Batch maximum reached with pH still high: fault for operator review.", 2500,
                 _ack_stocks() + recipe({"acid": {"settle_s": 300, "max_batch_L": 4, "max_no_response": 10}}) +
                 [(0.5, "FREEZE", {"point": "analog_A.AI6"}), _start("ACID", 1)],
                 twin_overrides=tw(None, FAST, chemistry={"initial_main_ph": 6.8}),
                 checks=[proc("ACID", "FAULT_LOCKOUT", "ACID_BATCH_LIMIT")]),
        Scenario("acid_ph_unavailable", "pH loop open: no dose starts; the reason is shown.", 30,
                 _ack_stocks() + [(0.5, "OVERRIDE_SET", {"point": "analog_A.AI6", "raw": 0, "reason": "scenario"}),
                                  _start("ACID", 1)], twin_overrides=tw(chemistry={"initial_main_ph": 6.8}),
                 checks=[lambda rt: [] if "pH unavailable" in (rt.sup.by_id["ACID"].inhibit or "") else
                         [f"acid inhibit {rt.sup.by_id['ACID'].inhibit}"], no_event("ACID_DOSE_ENDED")]),
        # -------------------------------------------------------------- return (PC-10, PC-11)
        Scenario("return_alternation", "Sump thresholds start/stop the return; ALTERNATE_EACH_START swaps pumps.",
                 2900, [IRR, _start("RETURN")], twin_overrides=tw({"SUMP": 2400}, FAST),
                 checks=[return_runs(["RP1", "RP2"]), proc("RETURN", "AUTO"), ledger,
                         no_event("ALARM_RAISED")]),
        Scenario("sump_high_main_full", "Sump high while the main tank is full: return held, conflict alarm, "
                 "main never filled above the inhibit.", 60, [_start("RETURN")],
                 twin_overrides=tw({"MAIN": 18800, "SUMP": 4700}),
                 checks=[alarm("SUMP_HIGH_MAIN_FULL", "process.RETURN"), no_event("RETURN_RUN_ENDED"),
                         lambda rt: [] if rt.sup.by_id["RETURN"].phase == "HOLD" else ["return not in HOLD"],
                         lambda rt: [] if rt.plant.ledger.returned_to_main_L == 0 else ["returned into a full main"]]),
        Scenario("return_pump_no_flow", "Return pump runs without flow: fault.", 60,
                 [(0, "ACTUATOR_FAULT", {"kind": "pump", "ident": "RP1", "fault": "NO_FLOW"}), _start("RETURN")],
                 twin_overrides=tw({"SUMP": 3000}),
                 checks=[proc("RETURN", "FAULT_LOCKOUT", "NO_FLOW"), outputs_off]),
    ]
    return {s.name: s for s in S}


def run_installation_scenario(cfg, sc: Scenario, *, seed: int | None = None, **kw):
    from .runtime import make_runtime
    rt = make_runtime(with_twin_overrides(cfg, sc.twin_overrides), seed=seed, **kw)
    for at, kind, payload in sc.steps:
        rt.schedule(at, kind, SCN, payload)
    rt.run_for(sc.duration_s)
    errs: list[str] = []
    for c in sc.checks:
        errs += c(rt)
    return rt, errs
