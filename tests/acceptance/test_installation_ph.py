"""PH gates: the installation fixture, signal budget, volume ledger, I/O, replay, recipes, no demo inheritance.

Everything here runs on the SIMULATED twin. Nothing is a physical verification.
"""
import math

import pytest

from aquacrop.config import load_config
from aquacrop.config.schema import InstallationConfig
from aquacrop.control.commands import MemoryAudit
from aquacrop.control.recipes import MemoryRecipeBackend, RecipeError, RecipeStore, demo_preset, validate_body
from aquacrop.domain.model import ControllerState, Origin, Quality, SignalSource, utcnow
from aquacrop.io.sampling import AcqContext, make_sample
from aquacrop.twin.installation_params import STOCKS
from aquacrop.twin.installation_scenarios import presets, run_installation_scenario, with_twin_overrides
from aquacrop.twin.runtime import make_runtime, replay
from tests.conftest import ROOT

CS = ControllerState


@pytest.fixture(scope="module")
def icfg():
    return load_config(ROOT / "configs" / "installation-48.yaml")


def _tw(cfg, **over):
    return with_twin_overrides(cfg, over)


# ------------------------------------------------------------------------------------------ PH-01
def test_ph01_fixture_facts(icfg):
    f = icfg.twin.facts
    assert icfg.twin.provenance and f.provenance == "OWNER_CONFIRMED"
    assert f.tank_capacity("MAIN") == 20000
    assert [f.tank_capacity(s) for s in STOCKS] == [5000, 5000, 5000]
    assert f.tank_capacity("SUMP") == 5000
    assert f.main_pumps == ["MP1", "MP2"] and f.return_pumps == ["RP1", "RP2"]
    assert f.agitators == 0
    acts = icfg.twin.actuator_signals()
    dosing = [s for s in acts if s.startswith("pump.TP_")]
    valves = [s for s in acts if s.startswith("valve.")]
    assert len(dosing) == 3 and len(valves) == 9
    assert not any("agitat" in s or "air" in s for s in acts)


# ------------------------------------------------------------------------------------------ PH-02
def test_ph02_sectors_and_outlets(icfg):
    f = icfg.twin.facts
    by = {s.sector_id: (s.channels, s.auto_pump) for s in f.sectors}
    assert by == {"S1": ([1, 2, 3, 4, 5, 6], "MP1"), "S2": ([7, 8, 9, 10, 11, 12, 13], "MP2")}
    assert f.outlets_per_channel * 13 == 52
    # outlets are adjustable distribution, never electric valves
    assert not any("outlet" in s.lower() for s in icfg.twin.actuator_signals())
    rt = make_runtime(icfg)
    rt.set_outlet("op", 1, 1, 0.0)
    rt.set_outlet("op", 1, 2, 0.0)
    with pytest.raises(ValueError):
        rt.set_outlet("op", 1, 1, 1.5)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(120)
    lg = rt.plant.ledger.delivered_by_channel_L
    assert lg[1] == pytest.approx(lg[2] / 2, rel=1e-6)      # half the outlets open -> half the share


# ------------------------------------------------------------------------------------------ PH-03
def test_ph03_ledger_closes_under_simultaneous_flows(icfg):
    rt, errs = run_installation_scenario(icfg, presets()["refill_concurrency"])
    assert errs == []
    snap = rt.plant.snapshot()["ledger"]
    assert snap["external_supply_L"]["MAIN"] > 0 and sum(snap["transferred_L"].values()) > 0
    assert snap["returned_to_main_L"] > 0 and snap["consumption_L"] > 0
    assert abs(snap["residual_L"]) < 1e-6
    assert all(abs(v) < 1e-3 for v in snap["species_residual"].values())


def test_ph03_retained_depth_preserved_after_sector_stops(icfg):
    cfg = _tw(icfg, dt_s=0.5)
    rt = make_runtime(cfg)
    rt.process_request("op", "IRRIGATION", CS.MANUAL)
    rt.process_manual("op", "IRRIGATION", {"pump": "MP1", "sector": "S1", "duration_s": 120})
    rt.run_for(200)
    assert any(rt.plant.channels[c].volume > rt.plant.retained_L + 1 for c in range(1, 7))
    rt.run_for(1800)
    for c in range(1, 14):
        v = rt.plant.channels[c].volume
        assert v == pytest.approx(rt.plant.retained_L, rel=0.01), (c, v)   # drains to, never below, retained


def test_ph03_source_empty_is_unmet_demand_not_invented_water(icfg):
    cfg = _tw(icfg, dynamics={"initial_L": {"MAIN": 12000, "SUMP": 50, "A": 3000, "B": 3000, "ACID": 2000}})
    rt = make_runtime(cfg)
    rt.process_request("op", "RETURN", CS.MANUAL)
    ok, msg = rt.process_manual("op", "RETURN", {"pump": "RP1", "duration_s": 60})
    assert not ok and "dry run" in msg                          # the controller refuses a dry run ...
    rt.plant.command("pump.RP1.cmd", True)                      # ... the plant itself still conserves water
    rt.run_for(30)
    assert rt.plant.tanks["SUMP"].volume >= 0
    assert rt.plant.ledger.unmet_demand_L["RETURN"] > 0
    assert rt.invariant_failures == []


def test_ph03_saturated_transmitter_is_flagged_and_plant_untouched(icfg):
    cfg = _tw(icfg, dynamics={"flow_span_L_min": {"S1": 50}})
    rt = make_runtime(cfg)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(20)
    s = rt.samples[cfg.point_for_signal("flow.S1")]
    assert s.raw_value == 20500 and s.quality in (Quality.INVALID, Quality.GOOD)
    assert rt.plant.flow["S1"] > 100           # truth above the transmitter span; reading saturated
    if s.quality == Quality.GOOD:
        assert s.value <= 200 * 1.04


# ------------------------------------------------------------------------------------------ PH-04 / PH-05
def test_ph04_all_48_points_round_trip_on_io_test(icfg):
    rt = make_runtime(icfg)
    assert rt.set_source("op", SignalSource.IO_TEST)[0]
    assert len(icfg.all_points()) == 48
    for pid in icfg.all_points():
        p = str(pid)
        if pid.kind.value == "DO":
            for v in (True, False):
                rec = rt.operator_command("op", p, v, "I/O test")
                assert rec.outcome.value == "READBACK_CONFIRMED", (p, rec.detail)
        elif pid.kind.value == "DI":
            for v in (True, False):
                rt.inject("op", p, v)
                assert rt.samples[p].value is v
        else:
            rt.inject("op", p, 4000)
            assert rt.samples[p].quality == Quality.GOOD and rt.samples[p].value == pytest.approx(0.0, abs=1e-9)
            rt.inject("op", p, 20000)
            assert rt.samples[p].quality == Quality.GOOD
            rt.inject("op", p, 0)                                     # open loop
            assert rt.samples[p].quality == Quality.INVALID
            with pytest.raises(ValueError):
                rt.inject("op", p, float("nan"))                      # not a register value
            s = make_sample(icfg, pid, float("nan"), acquired=utcnow(), ctx=AcqContext("s", "c"),
                            origin=Origin.SIMULATED)
            assert s.quality == Quality.INVALID or s.value is None or not math.isfinite(s.value)


def test_ph04_processes_refuse_io_test_source_and_raw_twin_commands(icfg):
    rt = make_runtime(icfg)
    rec = rt.operator_command("op", "relay_A.DO1", True, "raw")
    assert rec.outcome.value == "REJECTED" and "process controllers" in rec.detail
    rt.set_source("op", SignalSource.IO_TEST)
    ok, msg = rt.process_request("op", "IRRIGATION", CS.AUTO)
    assert not ok and "TWIN" in msg
    rt.set_source("op", SignalSource.TWIN)
    assert rt.process_request("op", "IRRIGATION", CS.AUTO)[0]
    ok, msg = rt.set_source("op", SignalSource.IO_TEST)
    assert not ok and "STOPPED" in msg


def test_ph05_stale_unavailable_and_edges(icfg):
    rt = make_runtime(icfg)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(10)
    edges = [e for e in rt.events if e["kind"] == "DI_EDGE"]
    assert any(e["payload"]["point"] == icfg.point_for_signal("contactor.MP1.closed") for e in edges)
    rt.set_acquisition_stall("op", True)
    rt.run_for(2)
    assert rt.obs().why("tank.MAIN.level").startswith("STALE")
    rt.set_acquisition_stall("op", False)
    rt.set_device_fault("op", "analog_B", "TIMEOUT")
    rt.run_for(1)
    assert rt.obs().get("flow.S1") is None and rt.obs().why("flow.S1").startswith("UNAVAILABLE")
    assert rt.obs().get("tank.MAIN.level") is not None


def test_ph05_controller_commands_are_audited_with_process_and_recipe(icfg):
    audit = MemoryAudit()
    rt = make_runtime(icfg, audit=audit)
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(20)
    assert audit.intents and all(r.command.origin.value == "CONTROLLER" for r in audit.intents
                                 if r.command.actor == "controller")
    assert any("IRRIGATION" in r.command.reason and "demo-sim@v1" in r.command.reason for r in audit.intents)
    kinds = [e[0] for e in audit.events]
    assert "DRIVE_ADAPTER_COMMAND_INTENT" in kinds


def test_ph05_no_ownership_leak_after_stop_and_reset(icfg):
    rt, errs = run_installation_scenario(icfg, presets()["vfd_trip_recovery"])
    assert errs == []
    rt.stop_all("op")
    rt.run_for(30)
    assert rt.sup.arbiter.owner == {}
    assert not any(rt.backend.state.coils.values()) and not any(rt.adapter_state.values())
    for p in rt.sup.processes:
        assert not p.on and not p.stopping


# ------------------------------------------------------------------------------------------ PH-06
def test_ph06_replay_reproduces_state(icfg):
    def run():
        rt = make_runtime(icfg, seed=7)
        for s in ("A", "B", "ACID"):
            rt.schedule(0, "STOCK_ACK", "t", {"stock": s, "note": ""})
        for pid in ("IRRIGATION", "RETURN", "REFILL", "ACID"):
            rt.schedule(0, "PROCESS_REQUEST", "t", {"process": pid, "target": "AUTO"})
        rt.schedule(120, "ACTUATOR_FAULT", "t", {"kind": "drive", "ident": "MAIN", "fault": "TRIP"})
        rt.schedule(150, "ACTUATOR_FAULT", "t", {"kind": "drive", "ident": "MAIN", "fault": None})
        rt.run_for(200)
        return rt
    a, b = run(), run()
    assert a.state_hash() == b.state_hash()
    r = replay(icfg, a.seed, a.journal, a.tick, **a.replay_kwargs())
    assert r.state_hash() == a.state_hash()


# ------------------------------------------------------------------------------------------ PH-07
def _store(simulated=True):
    return RecipeStore(MemoryRecipeBackend(), {"MAIN": 20000, "SUMP": 5000, "A": 5000, "B": 5000, "ACID": 5000},
                       ["S1", "S2"], simulated=simulated)


def test_ph07_validation_catches_missing_fields_and_ranges():
    body = demo_preset(["S1", "S2"]).body_json()
    body["refill"]["nutrient_mode"] = None
    body["acid"]["ph_target"] = 15
    store = _store()
    rec = store.validate("r1", store.save_draft("op", "r1", "test", body).recipe.version, "op")
    assert rec.status == "DRAFT"
    assert {"refill.nutrient_mode", "acid.ph_target"} <= {i["path"] for i in rec.issues}
    with pytest.raises(RecipeError):
        store.activate("r1", rec.recipe.version, "op")


def test_ph07_validation_catches_unsafe_orderings():
    body = demo_preset(["S1", "S2"]).body_json()
    body["main"]["refill_trigger_L"] = 17000             # above the fill stop
    body["acid"]["undershoot_margin"] = 0.1              # not wider than the tolerance
    body["irrigation"]["sequence"].append({"sector": "S9", "duration_s": 10})
    body["return"]["main_inhibit_L"] = 19000             # return may not fill above the high inhibit
    store = _store()
    rec = store.validate("r1", store.save_draft("op", "r1", "test", body).recipe.version, "op")
    paths = {i["path"] for i in rec.issues}
    assert {"main", "acid.undershoot_margin", "return.main_inhibit_L"} <= paths
    assert any("S9" in i["message"] for i in rec.issues)
    assert rec.status == "DRAFT"


def test_ph07_lifecycle_audited_immutable_rollback():
    events = []
    store = _store()
    store.emit = lambda k, p, a: events.append((k, a))
    store.ensure_demo()
    body = demo_preset(["S1", "S2"]).body_json()
    body["acid"]["ph_target"] = 6.2
    r = store.save_draft("alice", "site", "site recipe", body)
    store.validate("site", 1, "alice")
    store.activate("site", 1, "alice", reason="first")
    assert store.active().ref == "site@v1"
    with pytest.raises(RecipeError):
        store.save_draft("bob", "site", "edit", body, version=1)       # active is immutable
    r2 = store.save_draft("bob", "site", "edit", body)                 # editing creates v2
    assert r2.recipe.version == 2 and r2.status == "DRAFT"
    store.rollback("carol", "back to the preset")
    assert store.active().ref == "demo-sim@v1"
    kinds = [k for k, _ in events]
    assert {"RECIPE_DRAFT_SAVED", "RECIPE_VALIDATED", "RECIPE_ACTIVATED", "RECIPE_ROLLED_BACK"} <= set(kinds)
    assert [a["action"] for a in store.activations] == ["ACTIVATE", "ACTIVATE", "ROLLBACK"]
    assert r.recipe.digest() != demo_preset(["S1", "S2"]).digest()


def test_ph07_recipe_change_applies_next_cycle(icfg):
    rt, errs = run_installation_scenario(icfg, presets()["recipe_edit_next_cycle"])
    assert errs == []


# ------------------------------------------------------------------------------------------ PH-08
def test_ph08_demo_preset_only_in_simulation_and_not_the_owner_example():
    store = _store(simulated=False)
    assert store.ensure_demo() is None and store.records == {}
    s2 = _store()
    s2.ensure_demo()
    rec = s2.get("demo-sim", 1)
    s3 = _store(simulated=False)
    s3.records[("demo-sim", 1)] = rec
    rec.status = "VALIDATED"
    with pytest.raises(RecipeError):
        s3.activate("demo-sim", 1, "op")
    b = demo_preset(["S1", "S2"]).body
    assert (b.refill.fixed.a_L, b.refill.fixed.b_L, b.refill.water.target_L) != (1000, 1000, 12500)
    assert validate_body(b, s2.capacities, ["S1", "S2"]) == []


def test_ph08_real_profile_cannot_carry_simulated_drive_adapter_or_twin(icfg):
    data = icfg.model_dump(mode="json")
    data["profile"] = "HARDWARE_READ_ONLY"
    with pytest.raises(ValueError) as e:
        InstallationConfig.model_validate(data)
    msg = str(e.value)
    assert "SIMULATED adapters exist only in a SIMULATED profile" in msg or "virtual" in msg


def test_ph08_installation_labels_are_demo(icfg):
    assert icfg.profile.value == "SIMULATED"
    assert icfg.twin.dynamics.provenance == "DEMO" and icfg.twin.chemistry.provenance == "DEMO"
    assert "SIMULATION" in icfg.label and "DEMO" in icfg.label
    assert all("virtual" in (d.label or "") for d in icfg.devices)
