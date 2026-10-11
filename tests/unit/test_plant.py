"""AT-TWN-01: causal plant and water ledger."""
import math
import random

import pytest

from aquacrop.twin.params import TankParams, TwinParams
from aquacrop.twin.plant import Plant, first_order


def test_first_order_tau_zero_and_nonfinite():
    assert first_order(0.0, 1.0, 0.1, 0.0) == 1.0
    assert first_order(0.0, 1.0, 0.1, 1.0) == pytest.approx(1 - math.exp(-0.1))
    with pytest.raises(ValueError):
        first_order(float("nan"), 1.0, 0.1, 1.0)


def test_tank_equation_single_sector():
    p = TwinParams(pump_tau_s=0, valve_tau_s=0, return_transfer_enabled=False, return_transit_s=0)
    pl = Plant(p)
    pl.command("valve.S1.cmd", True)
    pl.command("pump.P1.cmd", True)
    pl.step()
    assert pl.flow_actual["S1"] == 12.0 and pl.flow_actual["S2"] == 0.0
    assert pl.volume["reservoir"] == pytest.approx(600 - 12 * 0.1 / 60)
    assert sum(pl.channel_flow[c] for c in range(1, 7)) == pytest.approx(12.0)
    assert pl.volume["return_tank"] == pytest.approx(0.8 * 12 * 0.1 / 60)


def test_no_valve_means_no_flow_deadhead():
    pl = Plant(TwinParams(pump_tau_s=0))
    pl.command("pump.P1.cmd", True)
    pl.step()
    assert pl.flow_actual == {"S1": 0.0, "S2": 0.0} and pl.deadhead


def test_starvation_reduces_flow_and_records_unmet_demand():
    p = TwinParams(reservoir=TankParams(capacity_L=1000, initial_L=0.01, low_level_fraction=0.1,
                                        high_level_fraction=0.95), pump_tau_s=0, valve_tau_s=0)
    pl = Plant(p)
    for v in ("valve.S1.cmd", "valve.S2.cmd", "pump.P1.cmd"):
        pl.command(v, True)
    for _ in range(50):
        pl.step()
        assert pl.volume["reservoir"] >= 0
    assert pl.ledger.unmet_demand_L > 0
    assert abs(pl.ledger_residual_L()) < 1e-9


def test_overflow_goes_to_spill_not_lost():
    p = TwinParams(reservoir=TankParams(capacity_L=100, initial_L=99.9, high_level_fraction=0.95),
                   valve_tau_s=0)
    pl = Plant(p)
    pl.command("valve.FILL.cmd", True)
    for _ in range(100):
        pl.step()
    assert pl.volume["reservoir"] == 100
    assert pl.ledger.spill_L["reservoir"] > 0
    assert abs(pl.ledger_residual_L()) < 1e-9


def test_ledger_holds_under_random_commands_long_run():
    rng = random.Random(7)
    pl = Plant(TwinParams())
    sig = ["pump.P1.cmd", "pump.P2.cmd", "pump.P3.cmd", "valve.S1.cmd", "valve.S2.cmd", "valve.FILL.cmd"]
    worst = 0.0
    for i in range(20000):
        if i % 37 == 0:
            pl.command(rng.choice(sig), rng.random() < 0.5)
        pl.step()
        worst = max(worst, abs(pl.ledger_residual_L()))
    assert worst < 1e-6
    assert all(v >= 0 for v in pl.volume.values())


def test_stuck_valve_diverges_from_command():
    pl = Plant(TwinParams(valve_tau_s=0))
    pl.set_fault("valve", "S1", "STUCK_CLOSED")
    pl.command("valve.S1.cmd", True)
    pl.step()
    assert pl.valves["S1"].cmd is True and pl.valves["S1"].position == 0.0


def test_unknown_signal_rejected():
    pl = Plant(TwinParams())
    with pytest.raises(KeyError):
        pl.command("pump.P9.cmd", True)
    with pytest.raises(ValueError):
        pl.set_fault("pump", "P1", "EXPLODE")


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        TankParams(capacity_L=10, initial_L=11)
    with pytest.raises(ValueError):
        TwinParams(auto={"selected_pump": "P9"})
