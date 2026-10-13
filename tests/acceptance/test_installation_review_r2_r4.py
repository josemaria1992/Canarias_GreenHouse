"""Regression cases from the owner's review of 13 October 2026 (R2, R3, R4) on the SIMULATED twin.

Synthetic quantities only; none of this is physical verification or a proposed field recipe.
"""
import pytest

from aquacrop.config import load_config
from aquacrop.domain.model import ControllerState as CS
from aquacrop.twin.installation_scenarios import recipe, tw, with_twin_overrides
from aquacrop.twin.runtime import make_runtime
from tests.conftest import ROOT


@pytest.fixture(scope="module")
def icfg():
    return load_config(ROOT / "configs" / "installation-48.yaml")


def until(rt, cond, limit=1800):
    end = rt.sim_s + limit
    while rt.sim_s < end and not cond():
        rt.step()
    assert cond(), f"not reached by sim {end:.0f} s"


def refill_rt(icfg, changes, **plant):
    rt = make_runtime(with_twin_overrides(icfg, tw({"MAIN": 7900}, dt=0.5, **plant)))
    for s in ("A", "B"):
        assert rt.stock_acknowledge("t", s, "simulation stock prepared")[0]
    for at, kind, payload in recipe(changes):
        rt.schedule(at, kind, "t", payload)
    rt.run_for(1)
    assert rt.recipes.active().recipe_id == "scenario"
    return rt


def ec_point(rt):
    return rt.cfg.point_for_signal("main.ec")


def drive_runs(rt, since):
    return [e for e in rt.events[since:] if e["kind"] == "DRIVE_ADAPTER_COMMAND" and e["payload"]["desired"]]


# ------------------------------------------------------------------------------------------ R3
@pytest.mark.parametrize("how", ["analog_below_guard", "low_low_switch"])
def test_r3_permissive_lost_during_lead_never_starts_the_drive(icfg, how):
    rt = make_runtime(icfg)
    assert rt.process_request("t", "IRRIGATION", CS.AUTO)[0]
    irr = rt.sup.by_id["IRRIGATION"]
    until(rt, lambda: irr.phase == "LEAD", 30)
    before = len(rt.events)
    if how == "analog_below_guard":
        level = rt.cfg.point_for_signal("tank.MAIN.level")
        rt.set_override("t", level, 5600, "valid 2,000 L reading during the valve lead")
    else:
        rt.set_override("t", rt.cfg.point_for_signal("tank.MAIN.low_low"), True, "low-low switch during the lead")
    rt.run_for(5)
    assert irr.state == CS.FAULT_LOCKOUT and irr.fault_reason == "MAIN_LOW_LOW"
    assert drive_runs(rt, before) == [] and rt.plant.drive.speed == 0.0
    assert not any(rt.adapter_state.values())
    # recovery only through the defined operator flow: the level returning restarts nothing
    rt.clear_override("t", rt.cfg.point_for_signal("tank.MAIN.level" if how == "analog_below_guard"
                                                   else "tank.MAIN.low_low"))
    rt.run_for(10)
    assert irr.state == CS.FAULT_LOCKOUT and drive_runs(rt, before) == []


def test_r3_storage_inhibit_during_lead_never_starts_the_drive(icfg):
    rt = make_runtime(icfg)
    assert rt.process_request("t", "IRRIGATION", CS.AUTO)[0]
    irr = rt.sup.by_id["IRRIGATION"]
    until(rt, lambda: irr.phase == "LEAD", 30)
    before = len(rt.events)
    rt.sup.start_permitted = lambda: (False, "test: storage degraded")
    rt.run_for(3)
    assert irr.state == CS.FAULT_LOCKOUT and irr.fault_reason == "START_INHIBITED"
    assert drive_runs(rt, before) == []


def test_r3_healthy_alternation_still_starts(icfg):
    rt = make_runtime(icfg)
    assert rt.process_request("t", "IRRIGATION", CS.AUTO)[0]
    irr = rt.sup.by_id["IRRIGATION"]
    until(rt, lambda: irr.phase == "IRRIGATE", 60)
    assert irr.state == CS.AUTO and drive_runs(rt, 0)


# ------------------------------------------------------------------------------------------ R4
def _max_run(icfg, changes, **plant):
    rt = refill_rt(icfg, changes, **plant)
    assert rt.process_request("t", "REFILL", CS.AUTO)[0]
    rf = rt.sup.by_id["REFILL"]
    sc = next(s for s in rt.cfg.scaling_profiles if s.profile_id == rt.cfg.device(ec_point(rt).split(".")[0])
              .ai[int(ec_point(rt).rsplit("AI", 1)[1])].scaling)
    rt.set_override("t", ec_point(rt), int(sc.raw_low), "valid low EC forces every bounded step")
    a0, b0 = rt.plant.tanks["A"].volume, rt.plant.tanks["B"].volume
    until(rt, lambda: (rf.batch or {}).get("nutrients", "").startswith(("MAX_REACHED", "MAX_EXCEEDED", "STOPPED"))
          or (rf.batch is None and rf.batches))
    rt.run_for(10)
    b = rf.batch if rf.batch is not None else rf.batches[-1]
    return rt, b, a0 - rt.plant.tanks["A"].volume, b0 - rt.plant.tanks["B"].volume


def test_r4_batch_maxima_hold_on_measured_delivery(icfg):
    rt, b, taken_a, taken_b = _max_run(icfg, {"refill": {"ec": {"step_total_L": 20, "max_a_L": 50, "max_b_L": 50,
                                                                 "settle_s": 1}}})
    steps = b["ec_steps"]
    da, db = sum(x["a_delivered_L"] for x in steps), sum(x["b_delivered_L"] for x in steps)
    assert b["nutrients"].startswith("MAX_REACHED") and "measured" in b["nutrients"]
    assert da <= 50 + 1e-9 and db <= 50 + 1e-9                       # the maximum holds on what was measured
    assert any(x["a_delivered_L"] > x["a_L"] for x in steps)          # the coast is real and counted
    assert sum(x["a_L"] for x in steps) < da                          # commanded kept separately for audit
    assert taken_a == pytest.approx(da, abs=0.5) and taken_b == pytest.approx(db, abs=0.5)
    assert not rt.alarms.is_active("REFILL_EC_LIMIT_EXCEEDED", "process.REFILL")


def test_r4_unequal_ratio_and_several_small_steps(icfg):
    rt, b, taken_a, taken_b = _max_run(icfg, {"refill": {"ec": {"step_total_L": 9, "ratio_a_to_b": 2.0,
                                                                 "max_a_L": 30, "max_b_L": 30, "settle_s": 1}}})
    steps = b["ec_steps"]
    da, db = sum(x["a_delivered_L"] for x in steps), sum(x["b_delivered_L"] for x in steps)
    assert len(steps) >= 4 and da <= 30 + 1e-9 and db <= 30 + 1e-9
    assert all(x["a_L"] == pytest.approx(2.0 * x["b_L"]) for x in steps)


def test_r4_overrun_is_reported_not_hidden(icfg):
    """A maximum smaller than one step's coast cannot be kept: the measured excess is reported as an
    overrun (alarm, OUT_OF_SPEC), never as a maximum enforced."""
    rt, b, taken_a, taken_b = _max_run(icfg, {"refill": {"ec": {"step_total_L": 20, "max_a_L": 10, "max_b_L": 10,
                                                                 "settle_s": 1}}})
    rf = rt.sup.by_id["REFILL"]
    assert len(b["ec_steps"]) == 1 and b["ec_steps"][0]["a_L"] == pytest.approx(10.0)   # clipped to the maximum
    assert b["overrun"] and b["nutrients"].startswith("MAX_EXCEEDED") and "measured" in b["nutrients"]
    assert rt.alarms.is_active("REFILL_EC_LIMIT_EXCEEDED", "process.REFILL")
    assert taken_a > 10 and taken_a == pytest.approx(b["ec_steps"][0]["a_delivered_L"], abs=0.05)
    until(rt, lambda: rf.batch is None, 7200)
    assert rf.batches[-1]["result"].startswith("OUT_OF_SPEC")


def test_r4_partner_failure_records_both_deliveries(icfg):
    rt = refill_rt(icfg, {"refill": {"ec": {"step_total_L": 200}}})
    assert rt.process_request("t", "REFILL", CS.AUTO)[0]
    rf = rt.sup.by_id["REFILL"]
    until(rt, lambda: "B" in rf.paths and rf.paths["B"].state == "RUNNING")
    rt.set_actuator_fault("t", "pump", "TP_B", "NO_FLOW")
    until(rt, lambda: rf.batch is None or rf.batch["nutrients"].startswith("STOPPED"), 120)
    rt.run_for(20)
    b = rf.batch if rf.batch is not None else rf.batches[-1]
    s = b["ec_steps"][-1]
    assert "a_delivered_L" in s and "b_delivered_L" in s and s["a_delivered_L"] < s["a_L"]
