"""0.7.0 regression tests for the five reproduced review defects (FEEDBACK_FOR_CLAUDE.md, F01-F05).

Each test states the correct behaviour; the review probes asserted the defective one. Logic in simulation only:
nothing here is a physical result.
"""
from aquacrop.programs.engine import Clock, Engine, Refused
from tests.unit.test_program_engine import Rig, prog, view

import pytest


def _restart(r: Rig) -> None:
    """A new engine on the same store and clock, as after a service restart."""
    old = r.e
    r.e = Engine(old.runtime_id, old.tz, old.io, Clock(r.clock.utc, r.clock.mono), r.store,
                 resolve=old.resolve, programs=old.programs, event=old.event, alarm=old.alarm, physical=False)
    r.e.start()


# ---------------------------------------------------------------- F02: limits before every ON
def _limited_led():
    return view("led1", "test_lamp", {"on": "digital_A.DO1"},
                limits={"max_runtime_s": 120, "min_off_s": 30, "max_starts_per_hour": 1})


def test_f02_sequence_second_on_is_refused_by_min_off_and_starts_per_hour():
    p = prog({"type": "MANUAL"}, {"type": "SEQUENCE", "max_runtime_s": 60, "steps": [
        {"do": "ON", "item": "led1"}, {"do": "WAIT", "seconds": 1},
        {"do": "OFF", "item": "led1"}, {"do": "WAIT", "seconds": 1},
        {"do": "ON", "item": "led1"}, {"do": "WAIT", "seconds": 1},
        {"do": "OFF", "item": "led1"}]})
    r = Rig({"led1": _limited_led()}, {"sequence": (1, p)})
    r.e.approve_recovery("review", "MANUAL")
    r.e.run_now("review", "sequence")
    r.run(10)
    assert len(r.on_frames()) == 1                          # the second ON never went out
    f = r.e.finished[-1]
    assert f["result"] == "FAILED" and "must stay OFF for 30 s" in f["reason"]
    assert not r.twin.s.coils["digital_A.DO1"]


def test_f02_starts_per_hour_count_across_programs():
    item = view("led1", "test_lamp", {"on": "digital_A.DO1"}, limits={"max_runtime_s": 120, "max_starts_per_hour": 1})
    a = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 2})
    b = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 2})
    r = Rig({"led1": item}, {"a": (1, a), "b": (1, b)})
    r.e.approve_recovery("review", "MANUAL")
    r.e.run_now("review", "a")
    r.run(4)
    with pytest.raises(Refused, match="started 1 times this hour"):
        r.e.run_now("review", "b")
    assert len(r.on_frames()) == 1


def test_f02_restart_and_recovery_approval_are_not_a_limit_reset():
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 2})
    r = Rig({"led1": _limited_led()}, {"p": (1, p)})
    r.e.approve_recovery("review", "MANUAL")
    r.e.run_now("review", "p")
    r.run(4)
    assert r.e.finished[-1]["result"] == "DONE"
    _restart(r)
    r.run(1)
    r.e.approve_recovery("review", "MANUAL")
    with pytest.raises(Refused, match="must stay OFF|times this hour"):
        r.e.run_now("review", "p")
    r.run(40)                                               # min-off has passed; one start per hour still holds
    with pytest.raises(Refused, match="times this hour"):
        r.e.run_now("review", "p")
    assert len(r.on_frames()) == 1


# ---------------------------------------------------------------- F03: fault inputs
def _pump(**type_over):
    return view("pump", "pump", {"start": "digital_A.DO1", "fault": "digital_A.DI1"}, **type_over)


def test_f03_fault_before_start_inhibits_with_cause_and_alarm():
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "pump", "duration_s": 10})
    r = Rig({"pump": _pump()}, {"run": (1, p)})
    r.twin.set_fault("pump", "TRIP")
    r.run(1)
    with pytest.raises(Refused, match="Fault reads no fault"):
        r.e.approve_recovery("review", "MANUAL")        # the restart checklist shows the fault
    assert r.alarms[("EQUIPMENT_FAULT", "pump")] is True
    r.twin.set_fault("pump", None)
    r.run(1)
    assert r.alarms[("EQUIPMENT_FAULT", "pump")] is False
    r.e.approve_recovery("review", "MANUAL")
    r.twin.set_fault("pump", "TRIP")
    r.run(1)
    with pytest.raises(Refused, match="reports a fault"):
        r.e.run_now("review", "run")
    assert r.on_frames() == []


def test_f03_fault_during_run_stops_and_keeps_a_persistent_alarm():
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "pump", "duration_s": 60})
    r = Rig({"pump": _pump()}, {"run": (1, p)})
    r.e.approve_recovery("review", "MANUAL")
    r.e.run_now("review", "run")
    r.run(3)
    assert r.twin.s.coils["digital_A.DO1"]
    r.twin.set_fault("pump", "TRIP")
    r.run(3)
    f = r.e.finished[-1]
    assert f["result"] == "FAILED" and "reports a fault" in f["reason"]
    assert not r.twin.s.coils["digital_A.DO1"]
    assert r.alarms[("EQUIPMENT_FAULT", "pump")] is True and r.alarms[("PROGRAM_OP_FAILED", "run")] is True
    assert ("EQUIPMENT_FAULT", "pump") in r.alarms and len(r.on_frames()) == 1


def test_f03_inverted_fault_input_reads_healthy_when_closed():
    roles = [dict(r) for r in __import__("aquacrop.equipment.definitions", fromlist=["TEMPLATES"]).TEMPLATES["pump"]["body"]["roles"]]
    for x in roles:
        if x["purpose"] == "FAULT":
            x["inverted"] = True                    # normally-closed: 0 means fault
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "pump", "duration_s": 5})
    r = Rig({"pump": _pump(roles=roles)}, {"run": (1, p)})
    r.run(1)
    r.e.approve_recovery("review", "MANUAL")        # the twin holds the NC contact closed while healthy
    r.e.run_now("review", "run")
    r.run(7)
    assert r.e.finished[-1]["result"] == "DONE"
    r.twin.set_fault("pump", "TRIP")                 # the contact opens
    r.run(12)                                        # past the pump's minimum OFF time
    with pytest.raises(Refused, match="reports a fault"):
        r.e.run_now("review", "run")


def test_f03_required_fault_input_without_a_channel_is_unknown_and_inhibits():
    roles = [dict(r) for r in __import__("aquacrop.equipment.definitions", fromlist=["TEMPLATES"]).TEMPLATES["pump"]["body"]["roles"]]
    for x in roles:
        if x["purpose"] == "FAULT":
            x["required"] = True
    item = view("pump", "pump", {"start": "digital_A.DO1"}, roles=roles)
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "pump", "duration_s": 5})
    r = Rig({"pump": item}, {"run": (1, p)})
    r.run(1)
    with pytest.raises(Refused, match="UNKNOWN"):
        r.e.approve_recovery("review", "MANUAL")


def _starter_doc():
    from aquacrop.layout import default_layout
    from aquacrop.twin.starter import link_layout, parts
    return link_layout(default_layout(), {p["item_id"] for p in parts()})[0].model_dump(mode="json")


# ---------------------------------------------------------------- F05: flow cache and rates
def test_f05_changed_pump_rate_with_unchanged_gates_changes_the_flow_next_step():
    from aquacrop.twin.water import Water
    doc, w, on = _starter_doc(), Water(), {"mp1", "v-s1"}
    w.step(0.5, "drawing", doc, lambda item: item in on, lambda item: 20.0)
    assert w.state["sectors"]["sector-1"] == 20.0
    w.step(0.5, "drawing", doc, lambda item: item in on, lambda item: 80.0)
    assert w.state["sectors"]["sector-1"] == 80.0
    w.step(0.5, "drawing", doc, lambda item: item in on, lambda item: 80.0)   # cached again at the new rate
    assert w.state["sectors"]["sector-1"] == 80.0


# ---------------------------------------------------------------- F04: batch size and speed independence
def _delivery(batch: int, steps: int = 40):
    from threading import RLock
    from types import SimpleNamespace
    from aquacrop.twin.equipment_service import EquipmentTwinService
    from aquacrop.twin.water import Water
    from tests.unit.test_program_engine import FLOW_SCALING
    doc = _starter_doc()
    items = {"mp1": view("mp1", "pump", {"start": "digital_A.DO1"}),
             "v-s1": view("v-s1", "valve", {"open": "digital_A.DO2"}),
             "flow-s1": view("flow-s1", "flow", {"flow": "analog_A.AI1"}, body={"scaling": FLOW_SCALING})}
    p = prog({"type": "MANUAL"}, {"type": "DELIVER_VOLUME", "item": "mp1", "meter": "flow-s1",
                                  "target_L": 1, "max_runtime_s": 40, "no_flow_timeout_s": 5})
    r = Rig(items, {"deliver": (1, p)})
    s = EquipmentTwinService.__new__(EquipmentTwinService)
    s.lock, s.clock, s.twin, s.engine = RLock(), r.clock, r.twin, r.e
    s.items = SimpleNamespace(views=lambda: r._items)
    s.water, s.layout_fn, s._frame = Water(), lambda: ("drawing", doc), lambda: None
    s.step_water(0)
    r.twin.step(0)
    r.e.approve_recovery("review", "MANUAL")
    r.twin.s.coils["digital_A.DO2"] = True
    r.e.run_now("review", "deliver")
    done = 0
    while done < steps:
        done += s.step(min(batch, steps - done))
    return [{"result": x["result"], "measured_L": x["measured_L"]} for x in r.e.finished], r


def test_f04_same_scenario_gives_the_same_result_at_every_batch_size():
    out = {b: _delivery(b)[0] for b in (1, 2, 40)}
    for b, res in out.items():
        assert len(res) == 1 and res[0]["result"] == "DONE", (b, res)
    vols = [res[0]["measured_L"] for res in out.values()]
    assert max(vols) - min(vols) <= 1e-6                  # identical steps: identical integration
    assert 1.0 <= vols[0] <= 1.0 + 20.0 * 0.5 / 60.0 + 1e-6   # target plus at most one step of 20 L/min


def test_f04_readings_published_each_step_carry_that_steps_value():
    res, r = _delivery(40)
    flow = r.twin.samples()["analog_A.AI1"]
    assert flow.acquired_utc == r.clock.utc()


def test_f03_stale_fault_input_inhibits_a_start():
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "pump", "duration_s": 5})
    r = Rig({"pump": _pump()}, {"run": (1, p)})
    r.run(1)
    r.e.approve_recovery("review", "MANUAL")
    r.twin.set_fault("digital_A", "MODULE_OFFLINE")
    r.run(1)
    with pytest.raises(Refused):
        r.e.run_now("review", "run")
    assert r.on_frames() == []
