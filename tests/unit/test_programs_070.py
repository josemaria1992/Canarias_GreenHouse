"""0.7.0 no-code programs: forms compiled to the engine's primitives (PROG-01/02/04/06, TIME-01/02/05).
Logic in simulation only."""
from datetime import date, datetime, timedelta, timezone

import pytest

from aquacrop.blocks.definitions import BlockBody, Plan, Step
from aquacrop.programs import schedule as sc
from aquacrop.programs.authoring import FormRefused, WateringForm, compile_form
from aquacrop.programs.definitions import digest, parse
from aquacrop.programs.engine import Refused
from tests.unit.test_program_engine import FLOW_SCALING, LEVEL_SCALING, Rig, view

PUMP = lambda: view("pump", "pump", {"start": "digital_A.DO1", "running": "digital_A.DI1"},  # noqa: E731
                    limits={"max_runtime_s": 3600})
VALVE = lambda: view("valve", "valve", {"open": "digital_A.DO2"})                             # noqa: E731
METER = lambda: view("meter", "flow", {"flow": "analog_A.AI1"}, body={"scaling": FLOW_SCALING})  # noqa: E731
LEVEL = lambda: view("lvl", "level", {"level": "analog_A.AI2"}, body={"scaling": LEVEL_SCALING})  # noqa: E731


def block(meter=True):
    return BlockBody(template="IRRIGATION_SECTOR", members=("tank", "pump", "meter", "valve", "sector"), supply="tank",
                     destination="sector", meter="meter" if meter else None,
                     plan=Plan(startup=(Step(do="OPEN", node="valve"), Step(do="WAIT", seconds=3),
                                        Step(do="START", node="pump", verify=True)),
                               normal_stop=(Step(do="STOP", node="pump"), Step(do="WAIT", seconds=2),
                                            Step(do="CLOSE", node="valve"))))


def form(**over):
    d = {"block_id": "b1", "block_version": 1,
         "endpoint": {"kind": "TIME", "minutes": 1, "max_minutes": 3},
         "schedule": {"kind": "NOW_ONLY"}} | over
    return WateringForm.model_validate(d)


def views():
    return {"pump": PUMP(), "valve": VALVE(), "meter": METER(), "lvl": LEVEL()}


def test_prog01_a_timed_form_compiles_to_the_blocks_sequence_and_round_trips():
    body = compile_form(form(), block(), "VALIDATED", views())
    steps = body["action"]["steps"]
    assert [s["do"] for s in steps] == ["ON", "WAIT", "ON", "RUN", "OFF", "WAIT", "OFF"]
    assert steps[2] == {"do": "ON", "item": "pump", "timeout_s": 30, "verify_role": "running"}
    assert steps[3]["seconds"] == 60 and body["action"]["max_runtime_s"] == 180 and body["basis"] == "WATERING_AFTER_STARTUP"
    pb = parse(body)
    assert WateringForm.model_validate(pb.authoring) == form()             # the form reopens as written


def test_prog03_volume_needs_the_blocks_meter_in_l_per_min():
    body = compile_form(form(endpoint={"kind": "VOLUME", "litres": 10, "max_minutes": 5}), block(), "VALIDATED", views())
    assert body["action"]["steps"][3] == {"do": "DELIVER", "meter": "meter", "meter_role": "flow", "target_L": 10,
                                          "no_flow_timeout_s": 20, "min_flow_L_min": 0.5}
    with pytest.raises(FormRefused, match="no flow meter"):
        compile_form(form(endpoint={"kind": "VOLUME", "litres": 10, "max_minutes": 5}), block(meter=False), "VALIDATED", views())
    with pytest.raises(FormRefused, match="not validated"):
        compile_form(form(), block(), "DRAFT", views())


def test_form_validation_names_the_problem():
    with pytest.raises(ValueError, match="longer than the watering time"):
        form(endpoint={"kind": "TIME", "minutes": 5, "max_minutes": 5})
    with pytest.raises(ValueError, match="at least one day"):
        form(schedule={"kind": "WEEKLY", "days": [], "times": ["07:00"]})
    v = views()
    with pytest.raises(FormRefused, match="is in cm"):
        compile_form(form(permissives=[{"item": "lvl", "role": "level", "op": "ABOVE", "value": 20, "unit": "L"}]),
                     block(), "VALIDATED", v)
    with pytest.raises(FormRefused, match="on/off"):
        compile_form(form(guards=[{"item": "pump", "role": "running", "op": "ABOVE", "value": 1, "unit": "x"}]),
                     block(), "VALIDATED", v)


def test_prog06_older_bodies_keep_their_digest():
    old = {"trigger": {"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "06:00", "end": "06:10"}]},
           "action": {"type": "RUN_FOR", "item": "led1", "duration_s": 60}}
    canon = parse(old).model_dump(mode="json")
    assert "permissives" not in canon and "from_date" not in canon["trigger"]["entries"][0]
    assert digest(canon) == digest(parse(canon).model_dump(mode="json"))


def _rig_for(body, extra=None):
    items = views() | (extra or {})
    r = Rig(items, {"p": (1, parse(body))})
    r.twin.set_input("analog_A.AI1", 0.0)                       # no flow, a fresh reading (engineering units)
    r.twin.set_input("analog_A.AI2", 500.0)                     # 500 cm
    return r


def test_prog04_start_condition_unknown_or_false_prevents_the_start_and_true_allows_it():
    f = form(permissives=[{"item": "lvl", "role": "level", "op": "ABOVE", "value": 100, "unit": "cm", "persist_s": 2}])
    r = _rig_for(compile_form(f, block(), "VALIDATED", views()))
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    r.twin.set_input("analog_A.AI2", None)                        # the level reading is lost
    r.run(1)
    with pytest.raises(Refused, match="UNKNOWN"):
        r.e.run_now("t", "p")
    r.twin.set_input("analog_A.AI2", 50.0)                        # 50 cm, below 100
    r.run(1)
    with pytest.raises(Refused, match="does not hold"):
        r.e.run_now("t", "p")
    r.twin.set_input("analog_A.AI2", 500.0)
    r.run(1)
    with pytest.raises(Refused, match="has not held"):
        r.e.run_now("t", "p")
    r.run(2)
    r.e.run_now("t", "p")
    assert r.e.ops


def test_prog04_a_stop_condition_stops_through_the_plan_and_is_not_done():
    f = form(endpoint={"kind": "TIME", "minutes": 2, "max_minutes": 4},
             guards=[{"item": "lvl", "role": "level", "op": "BELOW", "value": 100, "unit": "cm", "persist_s": 1}])
    r = _rig_for(compile_form(f, block(), "VALIDATED", views()))
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    r.e.run_now("t", "p")
    r.run(10)
    assert r.twin.s.coils["digital_A.DO1"] and r.twin.s.coils["digital_A.DO2"]
    r.twin.set_input("analog_A.AI2", 50.0)                        # level falls
    r.run(5)
    f = r.e.finished[-1]
    assert f["result"] == "GUARD_STOPPED" and "stop condition 1" in f["reason"]
    assert not r.twin.s.coils["digital_A.DO1"] and not r.twin.s.coils["digital_A.DO2"]
    assert r.alarms[("PROGRAM_GUARD", "p")] is True


def test_time01_date_range_belongs_to_the_local_start_date():
    entries = [{"days": ["MON", "TUE", "WED"], "start": "23:30", "end": "00:30", "from_date": "2026-11-03",
                "to_date": "2026-11-03"}]
    ws = sc.windows("p", entries, "Europe/Stockholm", date(2026, 11, 2), 3)
    assert [w.local_date.isoformat() for w in ws] == ["2026-11-03"]
    assert ws[0].end_utc - ws[0].start_utc == timedelta(hours=1)    # the overnight run keeps its length


def test_time02_repeat_waits_after_each_completion_respects_count_and_the_hard_end():
    f = form(endpoint={"kind": "TIME", "minutes": 1, "max_minutes": 3},
             schedule={"kind": "WEEKLY", "days": ["MON"], "times": ["05:01"],
                       "repeat": {"rest_minutes": 2, "window_end": "05:12", "max_count": 5}})
    body = compile_form(f, block(), "VALIDATED", views())
    assert body["trigger"]["type"] == "WINDOW_REPEAT" and body["trigger"]["rest_s"] == 120
    r = _rig_for(body)
    r.run(1)
    r.e.approve_recovery("t", "AUTO")                             # 05:00: the 05:01 window is not open yet
    r.run(13 * 60)
    starts = [p["key"] for k, p in r.events if k == "PROGRAM_OP_STARTED"]
    ends = [p for k, p in r.events if k == "PROGRAM_OP_ENDED"]
    # each cycle: ~1 min of watering plus startup/stop (~7 s), then 2 min rest: about 3 cycles before 05:12
    assert 2 <= len(starts) <= 4 and all(s.endswith(f"#{i}") for i, s in enumerate(starts))
    assert all(e["result"] in ("DONE", "WINDOW_ENDED") for e in ends)
    assert not any(k == "PROGRAM_OP_STARTED" and p.get("key", "").endswith(f"#{len(starts)}") for k, p in r.events)
    assert r.e.ops == {}                                         # nothing runs after the window
