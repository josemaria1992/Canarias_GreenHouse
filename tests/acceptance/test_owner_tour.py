"""The owner tour (docs/operator/walkthrough.md, UAT-01 to UAT-16): what the guide promises after each tour
preset, checked headless on the installation-80 configuration with the DEMO recipe.

The browser rehearsal (scripts/ui_check/run_tour_rehearsal.py) follows the same steps through the screen;
these tests keep the guide's numbers honest when the code changes. Every quantity is DEMO/synthetic and
none of this is physical verification. The owner's own results stay NOT RUN until the owner runs the tour.
"""
import dataclasses

import pytest

from aquacrop.config import load_config
from aquacrop.twin.installation_scenarios import (
    TOUR_LOW_MAIN_L, _ack_stocks, _start, presets, run_installation_scenario, tour_presets,
)
from tests.conftest import ROOT


@pytest.fixture(scope="module")
def cfg80():
    return load_config(ROOT / "configs" / "installation-80.yaml")


def _tour(name: str, steps: list, duration: float):
    """A tour preset (its DEMO starting levels) followed by the buttons the guide asks the owner to press."""
    return dataclasses.replace(presets()[name], steps=steps, duration_s=duration, checks=[])


def test_tour_presets_are_labelled_as_simulation_and_start_nothing():
    names = [s.name for s in tour_presets()]
    assert names == ["tour_low_main", "tour_ph_high", "tour_sump_high", "tour_main_full_sump_high",
                     "tour_ec_lost_at_valve_opening"]
    for s in tour_presets():
        assert s.description.startswith("Owner tour (UAT-"), s.name
        assert "DEMO" in s.description or "runs by itself" in s.description, s.name
        if s.name != "tour_ec_lost_at_valve_opening":
            assert s.steps == [] and "Nothing is started" in s.description, s.name


def test_uat06_ec_target_refill_reaches_the_final_level(cfg80):
    rt, _ = run_installation_scenario(cfg80, _tour("tour_low_main", _ack_stocks(0, ("A", "B")) + [_start("REFILL", 1)], 6000),
                                      seed=42)
    ref = rt.sup.by_id["REFILL"]
    assert rt.recipes.active().ref == "demo-sim@v1"
    b = ref.batches[0]
    assert b["result"] == "COMPLETED" and b["nutrients"].startswith("EC_TARGET_REACHED"), b
    assert b["end_level_L"] == pytest.approx(15000, abs=30)          # the DEMO final operating level
    assert 0 < b["delivered_a_L"] <= 150 and 0 < b["delivered_b_L"] <= 150   # DEMO maxima per batch, measured
    assert abs(b["delivered_a_L"] - b["delivered_b_L"]) < 1.0         # DEMO ratio 1:1
    assert 1.7 <= b["end_ec"] <= 1.9


def test_uat06_starting_level_is_below_the_demo_refill_trigger():
    assert TOUR_LOW_MAIN_L < 8000                                       # DEMO refill trigger


def test_uat08_acid_doses_in_steps_until_ph_is_in_band(cfg80):
    rt, _ = run_installation_scenario(cfg80, _tour("tour_ph_high", _ack_stocks(0, ("ACID",)) + [_start("ACID", 1)], 6000),
                                      seed=42)
    acid = rt.sup.by_id["ACID"]
    b = acid.batches[0]
    assert b["result"] == "PH_IN_BAND", b
    assert 2 <= len(b["doses"]) <= 15 and b["total_L"] <= 30             # DEMO 2 L steps, 30 L per batch
    assert all(d["requested_L"] == 2.0 for d in b["doses"])
    assert b["ph_start"] == pytest.approx(6.8, abs=0.01) and 5.8 <= b["ph_end"] <= 6.2


def test_uat09_return_alternates_and_holds_when_the_main_tank_is_full(cfg80):
    rt, _ = run_installation_scenario(cfg80, _tour("tour_sump_high", [_start("RETURN", 0), _start("IRRIGATION", 0)], 4000),
                                      seed=42)
    runs = rt.sup.by_id["RETURN"].runs
    assert len(runs) >= 2 and runs[0]["pump"] != runs[1]["pump"], runs
    rt, _ = run_installation_scenario(cfg80, _tour("tour_main_full_sump_high", [_start("RETURN", 0)], 60), seed=42)
    ret = rt.sup.by_id["RETURN"]
    assert ret.phase == "HOLD" and not ret.on
    assert ("SUMP_HIGH_MAIN_FULL", "process.RETURN") in rt.sup.alarms.open


def test_uat11_ec_lost_preset_runs_clean_on_installation_80(cfg80):
    rt, errs = run_installation_scenario(cfg80, presets()["tour_ec_lost_at_valve_opening"], seed=42)
    assert errs == []
