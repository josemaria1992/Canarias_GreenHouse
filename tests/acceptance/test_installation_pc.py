"""PC gates: process controllers on the SIMULATED installation twin (scenarios + targeted checks).

Each scenario is also part of `python -m aquacrop demo verify --config installation-48`.
None of this is physical verification.
"""
import pytest

from aquacrop.config import load_config
from aquacrop.control.supervisor import Supervisor
from aquacrop.domain.model import ControllerState, Priority
from aquacrop.twin.installation_scenarios import presets, run_installation_scenario
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
