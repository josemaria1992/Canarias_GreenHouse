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
