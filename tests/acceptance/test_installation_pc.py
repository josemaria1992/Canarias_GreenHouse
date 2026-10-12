"""PC gates: process controllers on the SIMULATED installation twin (scenarios + targeted checks).

Each scenario is also part of `python -m aquacrop demo verify --config installation-48`.
None of this is physical verification.
"""
import pytest

from aquacrop.config import load_config
from aquacrop.control.supervisor import Supervisor
from aquacrop.domain.model import ControllerState, Priority
from aquacrop.twin.installation_scenarios import presets, recipe, run_installation_scenario, tw, with_twin_overrides
from aquacrop.twin.runtime import make_runtime
from tests.conftest import ROOT

CS = ControllerState

GATES = {
    "PC-01": ["irrigation_alternation", "recipe_edit_next_cycle"],
    "PC-02": ["irrigation_alternation", "manual_irrigation_operator_pump", "sector_valve_stuck_closed",
              "vfd_stop_feedback_missing", "contactor_welded", "contactor_no_close", "drive_no_run"],
    "PC-03": ["manual_irrigation_operator_pump"],
    "PC-04": ["refill_ec_target"],
    "PC-05": ["refill_concurrency"],
    "PC-06": ["refill_ec_target", "refill_fixed_volumes", "refill_fixed_ec_out_of_band"],
    "PC-07": ["acid_to_target", "acid_already_in_band", "acid_undershoot", "acid_no_response", "acid_batch_limit",
              "acid_ph_unavailable"],
    "PC-09": ["refill_stock_not_prepared", "refill_stock_empty", "refill_transfer_no_flow", "refill_transfer_timeout",
              "stock_fill_invalidates"],
    "PC-10": ["return_alternation", "return_pump_no_flow"],
    "PC-11": ["sump_high_main_full"],
    "PC-12": ["vfd_trip_recovery", "safety_chain_open", "main_pump_no_flow", "sector_valve_stuck_open",
              "main_level_open_loop", "stale_acquisition", "module_timeout", "main_source_empty", "main_low_low"],
    "PC-13": ["manual_bounds_rejected", "raw_command_refused"],
}


@pytest.fixture(scope="module")
def icfg():
    return load_config(ROOT / "configs" / "installation-48.yaml")


def test_every_scenario_is_mapped_to_a_gate():
    mapped = {n for v in GATES.values() for n in v}
    assert set(presets()) == mapped


@pytest.mark.parametrize("name", sorted(presets()))
def test_scenario(icfg, name):
    rt, errs = run_installation_scenario(icfg, presets()[name])
    assert errs == []
    assert rt.invariant_failures == []


# ------------------------------------------------------------------------------------------ PC-03
def test_pc03_restart_begins_stopped_and_auto_rejects_leftover_outputs(icfg):
    rt = make_runtime(icfg)
    assert all(p.state == CS.STOPPED and p.phase == "IDLE" for p in rt.sup.processes)
    irr = rt.sup.by_id["IRRIGATION"]
    rt.process_request("op", "IRRIGATION", CS.MANUAL)
    rt.process_manual("op", "IRRIGATION", {"pump": "MP1", "sector": "S1", "duration_s": 30})
    rt.run_for(10)
    ok, msg = rt.process_request("op", "IRRIGATION", CS.AUTO)
    assert not ok and "MANUAL" in msg
    irr.state = CS.STOPPED                         # even if the state were forced, owned outputs block AUTO
    ok, msg = rt.process_request("op", "IRRIGATION", CS.AUTO)
    assert not ok and "outputs still ON" in msg


# ------------------------------------------------------------------------------------------ PC-04 latch
def test_pc04_level_chatter_and_repeated_samples_latch_one_batch(icfg):
    """Demo preset: trigger 8,000 L, debounce 10 s, re-arm above 9,000 L, final level 15,000 L. The main
    level is overridden at the transmitter (raw uA, DEMO span 0-20,000 L)."""
    rt = make_runtime(icfg)
    refill = rt.sup.by_id["REFILL"]
    ok, why = rt.process_request("t", "REFILL", CS.AUTO)
    assert ok, why

    def level(litres):
        rt.set_override("t", "analog_A.AI1", int(round(4000 + 16000 * litres / 20000)), "PC-04 level")

    def chatter():
        for i in range(10):                      # each dip is shorter than the debounce
            level(7900 if i % 2 == 0 else 8100)
            rt.run_for(4)

    chatter()
    assert refill.batch is None and refill.batches == []
    level(7900)
    rt.run_for(30)                               # repeated samples below the trigger latch one batch
    assert refill.batch is not None
    first = refill.batch["batch_id"]
    chatter()                                    # chatter while the batch runs changes nothing
    assert refill.batch["batch_id"] == first
    level(15000)                                 # water reaches the final level; the batch ends
    while refill.batch is not None:
        rt.step()
    level(8500)                                  # above the trigger, below the re-arm level
    rt.run_for(20)
    assert not refill.armed
    chatter()
    level(7900)
    rt.run_for(30)
    assert [b["batch_id"] for b in refill.batches] == [first] and refill.batch is None
    assert len([e for e in rt.events if e["kind"] == "REFILL_BATCH_STARTED"]) == 1
    level(9200)                                  # above trigger + hysteresis: re-arms
    rt.run_for(5)
    assert refill.armed and refill.batch is None
    assert any(e["kind"] == "REFILL_REARMED" for e in rt.events)


# ------------------------------------------------------------------------------------------ PC-05 ownership
def test_pc05_a_process_cannot_command_another_process_equipment(icfg):
    rt = make_runtime(icfg)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(10)
    refill = rt.sup.by_id["REFILL"]
    assert rt.sup.arbiter.owner["valve.S1.cmd"] == "IRRIGATION"
    assert rt.sup.issue(refill, "valve.S1.cmd", False, "test", Priority.STOP) is False
    assert rt.plant.valves["S1"].cmd is True
    assert any(e["kind"] == "PROCESS_COMMAND_REFUSED" for e in rt.events)


def test_pc05_unrelated_mode_change_does_not_touch_other_processes(icfg):
    rt = make_runtime(icfg)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.process_request("op", "RETURN", CS.AUTO)
    rt.run_for(30)
    before = rt.sup.by_id["IRRIGATION"].phase
    rt.process_request("op", "RETURN", CS.STOPPED)
    rt.process_request("op", "ACID", CS.MANUAL)
    rt.run_for(1)
    assert rt.sup.by_id["IRRIGATION"].state == CS.AUTO and rt.sup.by_id["IRRIGATION"].phase == before


# ------------------------------------------------------------------------------------------ PC-08
def test_pc08_mixing_readiness_is_not_verified_on_a_physical_profile(icfg):
    rt = make_runtime(icfg)
    sup = Supervisor(icfg.twin.facts, alarms=rt.alarms, emit=lambda *a: None, route=lambda *a: False,
                     start_permitted=lambda: (True, None), source_ok=lambda: None, simulated=False)
    ok, why = sup.mixing_ready("PH", 600, rt.make_ctx())
    assert not ok and "NOT_VERIFIED" in why
    ok, why = rt.sup.mixing_ready("PH", 600, rt.make_ctx())
    assert ok and "SIMULATED" in why
    snap = rt.snapshot()
    assert snap["recipe"]["physical_readiness"]                     # always non-empty in this release


# ------------------------------------------------------------------------------------------ PC-09
def test_pc09_stock_acknowledgement_is_audited_and_invalidated_by_level_rise(icfg):
    rt = make_runtime(icfg)
    ok, _ = rt.stock_acknowledge("alice", "A", "200 kg batch 12")
    assert ok and rt.sup.stocks["A"].status == "PREPARED" and rt.sup.stocks["A"].actor == "alice"
    rt.plant.command("valve.FILL_A.cmd", True)                     # water added outside any process
    rt.run_for(60)
    rt.plant.command("valve.FILL_A.cmd", False)
    rt.run_for(5)
    ok, why = rt.sup.stock_ready("A", rt.obs().num("tank.A.level"))
    assert not ok and "INVALIDATED" in why
    kinds = [e["kind"] for e in rt.events]
    assert "STOCK_READINESS_ACKNOWLEDGED" in kinds and "STOCK_READINESS_INVALIDATED" in kinds
    assert [e["status"] for e in rt.sup.readiness_backend.entries] == ["PREPARED", "INVALIDATED"]


def test_pc09_readiness_survives_restart_through_its_store(icfg):
    a = make_runtime(icfg)
    a.stock_acknowledge("alice", "B", "")
    b = make_runtime(icfg, readiness=a.sup.readiness_backend)
    assert b.sup.stocks["B"].status == "PREPARED" and b.sup.stocks["A"].status == "NOT_PREPARED"


# ------------------------------------------------------------------------------------------ PC-12
def test_pc12_ack_reset_restart_are_distinct(icfg):
    rt = make_runtime(icfg)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(30)
    rt.set_actuator_fault("op", "valve", "S1", "STUCK_CLOSED")   # (no effect on an open valve until closing)
    rt.set_actuator_fault("op", "drive", "MAIN", "TRIP")
    rt.run_for(15)
    irr = rt.sup.by_id["IRRIGATION"]
    assert irr.state == CS.FAULT_LOCKOUT
    assert irr.next_step == "" and irr.inhibit is None              # no stale "next" plan on the page
    rt.set_actuator_fault("op", "drive", "MAIN", None)
    rt.set_actuator_fault("op", "valve", "S1", None)
    rt.run_for(10)
    occ = rt.alarms.get(irr.fault_occurrence_id)
    rt.acknowledge("op", occ.rule_id, occ.target)
    assert irr.state == CS.FAULT_LOCKOUT                            # acknowledging does not reset
    with pytest.raises(ValueError):
        rt.reset_alarm("op", occ.rule_id, occ.target)               # the lockout alarm resets with the process
    ok, _ = rt.process_reset("op", "IRRIGATION")
    assert ok and irr.state == CS.STOPPED                           # reset does not restart
    rt.run_for(30)
    assert irr.state == CS.STOPPED and not irr.on
    assert rt.process_request("op", "IRRIGATION", CS.AUTO)[0]       # restart is a deliberate request


def test_pc12_storage_fault_inhibits_new_starts(icfg):
    state = {"ok": True}
    rt = make_runtime(icfg, start_permitted=lambda: (state["ok"], None if state["ok"] else "historian unwritable"))
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(60)
    state["ok"] = False
    ok, msg = rt.process_request("op", "RETURN", CS.AUTO)
    assert not ok and "historian unwritable" in msg
    rt.run_for(300)                                                 # the running step ends; no new step starts
    irr = rt.sup.by_id["IRRIGATION"]
    assert irr.phase == "PRECHECK" and "historian unwritable" in (irr.inhibit or "")
    assert not any(rt.backend.state.coils.values()) and not any(rt.adapter_state.values())
    state["ok"] = True
    rt.run_for(5)
    assert irr.phase not in ("PRECHECK",)


def test_pc12_stop_all_brings_every_output_off(icfg):
    rt = make_runtime(icfg)
    for s in ("A", "B", "ACID"):
        rt.stock_acknowledge("op", s, "")
    for pid in ("IRRIGATION", "RETURN", "REFILL", "ACID"):
        rt.process_request("op", pid, CS.AUTO)
    rt.run_for(60)
    rt.stop_all("op")
    rt.run_for(30)
    assert all(p.state == CS.STOPPED and p.phase == "IDLE" and not p.on for p in rt.sup.processes)
    assert not any(rt.backend.state.coils.values()) and not any(rt.adapter_state.values())
    assert rt.sup.arbiter.owner == {}
    assert rt.plant.hazards == []


# ------------------------------------------------------------------------------------------ interlocks at every start
def _twin(icfg, initial=None, **extra):
    return with_twin_overrides(icfg, tw(initial, **extra))


def test_pc11_return_never_starts_or_keeps_running_into_an_active_main_high_high(icfg):
    # the switch activates after AUTO was granted, with the analog main level well below the return inhibit
    rt = make_runtime(_twin(icfg, {"SUMP": 3000}))
    ret = rt.sup.by_id["RETURN"]
    assert rt.process_request("op", "RETURN", CS.AUTO)[0]
    rt.set_override("op", "relay_B.DI4", True, "test: main high-high active")
    rt.run_for(30)
    assert ret.state == CS.AUTO and ret.running_pump is None and not ret.on
    assert "tank.MAIN.high_high active" in (ret.inhibit or "")
    assert rt.plant.ledger.returned_to_main_L == 0
    # ... and a run already in progress stops on the switch even though the analog level allows it
    rt = make_runtime(_twin(icfg, {"SUMP": 3000}))
    ret = rt.sup.by_id["RETURN"]
    rt.process_request("op", "RETURN", CS.AUTO)
    rt.run_for(20)
    assert ret.phase == "RUNNING"
    rt.set_override("op", "relay_B.DI4", True, "test: main high-high active")
    rt.run_for(30)
    assert ret.state == CS.AUTO and ret.running_pump is None and not ret.on
    assert "main high-high switch active" in ret.runs[-1]["reason"]
    assert "tank.MAIN.high_high active" in (ret.inhibit or "")


def test_pc07_acid_never_doses_without_a_known_acid_stock_level(icfg):
    cfg = _twin(icfg, chemistry={"initial_main_ph": 6.8})
    rt = make_runtime(cfg)
    rt.stock_acknowledge("op", "ACID", "")
    rt.set_override("op", "analog_A.AI5", 0, "test: acid stock level loop open")     # stays PREPARED
    acid = rt.sup.by_id["ACID"]
    assert rt.sup.stocks["ACID"].status == "PREPARED"
    assert rt.process_request("op", "ACID", CS.AUTO)[0]
    rt.run_for(30)
    assert acid.phase != "DOSING" and not acid.on
    assert "acid stock level unavailable" in (acid.inhibit or "")
    rt.process_request("op", "ACID", CS.STOPPED)
    rt.run_for(2)
    rt.process_request("op", "ACID", CS.MANUAL)
    ok, msg = rt.process_manual("op", "ACID", {"volume_L": 1})
    assert not ok and "acid stock level unavailable" in msg
    # a level lost during a dose stops it as a failure
    rt = make_runtime(cfg)
    rt.stock_acknowledge("op", "ACID", "")
    acid = rt.sup.by_id["ACID"]
    rt.process_request("op", "ACID", CS.AUTO)
    rt.run_for(3)
    assert acid.phase == "DOSING"
    rt.set_override("op", "analog_A.AI5", 0, "test: acid stock level loop open")
    rt.run_for(15)
    assert acid.state == CS.FAULT_LOCKOUT and not acid.on
    assert "STOCK_LEVEL_UNAVAILABLE" in acid.batches[-1]["doses"][-1]["result"]


def test_pc06_manual_transfer_is_bounded_by_the_requested_stock_in_the_active_mode(icfg):
    def manual(mode: str, a: dict, path: str, vol: float):
        rt = make_runtime(icfg)
        for s in ("A", "B"):
            rt.stock_acknowledge("op", s, "")
        for at, kind, payload in recipe({"refill": {"nutrient_mode": mode} | a}):
            rt.schedule(at, kind, "test", payload)
        rt.run_for(1)
        rt.process_request("op", "REFILL", CS.MANUAL)
        return rt.process_manual("op", "REFILL", {"path": path, "volume_L": vol})

    fixed = {"fixed": {"a_L": 10, "b_L": 100}, "ec": {"max_a_L": 200, "max_b_L": 200}}
    ok, msg = manual("FIXED_VOLUMES", fixed, "A", 50)
    assert not ok and "limited to 10 L (recipe fixed volume for stock A)" in msg
    assert manual("FIXED_VOLUMES", fixed, "B", 50)[0]
    ec = {"fixed": {"a_L": 200, "b_L": 200}, "ec": {"max_a_L": 30, "max_b_L": 150}}
    ok, msg = manual("EC_TARGET", ec, "A", 50)                     # the inactive mode's 200 L does not apply
    assert not ok and "limited to 30 L (recipe EC-target batch maximum for stock A)" in msg
    assert manual("EC_TARGET", ec, "B", 50)[0]


def test_pc07_acid_does_not_start_in_the_scan_a_refill_starts_an_addition(icfg):
    # both are held by the same global blocker; when it clears, REFILL (scanned first) starts its batch
    # and ACID must see the commanded addition before any flow is observed
    state = {"ok": True}
    rt = make_runtime(_twin(icfg, {"MAIN": 7900}, chemistry={"initial_main_ph": 6.8}),
                      start_permitted=lambda: (state["ok"], None if state["ok"] else "historian unwritable"))
    for s in ("A", "B", "ACID"):
        rt.stock_acknowledge("op", s, "")
    assert rt.process_request("op", "REFILL", CS.AUTO)[0] and rt.process_request("op", "ACID", CS.AUTO)[0]
    state["ok"] = False
    rt.run_for(30)
    refill, acid = rt.sup.by_id["REFILL"], rt.sup.by_id["ACID"]
    assert refill.batch is None and acid.phase != "DOSING"
    state["ok"] = True
    for _ in range(100):                                         # simulation ticks up to the next control scan
        rt.step(1)
        if refill.batch is not None:
            break
    assert refill.batch is not None and "valve.FILL_MAIN.cmd" in refill.on
    assert acid.phase != "DOSING" and "pump.TP_ACID.cmd" not in acid.on
    assert "an addition is commanded now" in (acid.inhibit or "")
    rt.run_for(60)
    assert acid.phase != "DOSING" and "settling" in (acid.inhibit or "").lower()
