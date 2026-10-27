"""Owner decision 27 Oct 2026 (fake services only): a duty pump that fails while Sector 1 waters is switched OFF and
verified OFF, then its partner finishes the remaining watering time of the same cycle with the sector valve kept
open; Sector 2 is served afterwards by the healthy pump. Replacement is refused, with a reason and the normal fault
stop, when the partner is busy elsewhere or the failed pump cannot be verified OFF. Limits are never extended."""
from aquacrop.programs.definitions import parse
from tests.unit.test_program_engine import Rig, view


def _items():
    lim = {"max_runtime_s": 3600}
    return {"pump-a": view("pump-a", "pump", {"start": "digital_A.DO1", "fault": "digital_A.DI1"}, limits=lim),
            "pump-b": view("pump-b", "pump", {"start": "digital_A.DO3", "fault": "digital_A.DI4"}, limits=lim),
            "valve-1": view("valve-1", "valve", {"open": "digital_A.DO2"}),
            "valve-2": view("valve-2", "valve", {"open": "digital_A.DO5"})}


def _sector(valve, run_s=120, limit=600):
    return parse({"trigger": {"type": "MANUAL"}, "duty": ["pump-a", "pump-b"],
                  "action": {"type": "SEQUENCE", "max_runtime_s": limit, "steps": [
                      {"do": "ON", "item": valve}, {"do": "ON", "item": "pump-a"}, {"do": "RUN", "seconds": run_s},
                      {"do": "OFF", "item": "pump-a"}, {"do": "OFF", "item": valve}]}})


def _rig(**programs):
    r = Rig(_items(), {k: (1, v) for k, v in programs.items()})
    for p in ("digital_A.DI1", "digital_A.DI4"):
        r.twin.set_input(p, False)
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    return r


def _targets(r):
    return [(i.command.target, i.command.desired) for i in r.audit.intents]


def test_pump_a_fails_mid_cycle_pump_b_finishes_sector_1_then_sector_2():
    r = _rig(s1=_sector("valve-1"), s2=_sector("valve-2"))
    r.e.run_now("t", "s1")
    r.run(32)                                                     # about 30 s of watering on pump-a
    assert r.twin.s.coils["digital_A.DO1"] and r.twin.s.coils["digital_A.DO2"]
    r.twin.set_fault("pump-a", "TRIP")                             # pump-a reports a fault
    n = len(r.audit.intents)
    r.run(3)
    k = r.kinds()
    assert "PROGRAM_PUMP_FAILED" in k and "PROGRAM_PUMP_REPLACED" in k
    assert r.alarms[("PROGRAM_PUMP_FAILED", "s1")] is True
    after = _targets(r)[n:]
    assert after.index(("digital_A.DO1", False)) < after.index(("digital_A.DO3", True))   # OFF verified, then ON
    assert ("digital_A.DO2", False) not in after                   # valve 1 stays open
    rep = next(p for kk, p in r.events if kk == "PROGRAM_PUMP_REPLACED")
    assert 28 <= rep["watered_s"] <= 34 and rep["by"] == "pump-b"
    op = next(iter(r.e.ops.values()))
    assert op.state == "RUNNING" and r.twin.s.coils["digital_A.DO3"] and r.twin.s.coils["digital_A.DO2"]
    r.run(80)                                                     # not finished early: the rest is about 90 s
    assert op.state == "RUNNING"
    r.run(20)
    f = r.e.finished[-1]
    assert f["result"] == "DONE" and not r.twin.s.coils["digital_A.DO3"] and not r.twin.s.coils["digital_A.DO2"]
    # Sector 2 next: the healthy pump leads (pump-a still reports its fault)
    r.e.run_now("t", "s2")
    r.run(3)
    started = [p for kk, p in r.events if kk == "PROGRAM_OP_STARTED"][-1]
    assert started["lead"] == "pump-b" and r.twin.s.coils["digital_A.DO5"] and r.twin.s.coils["digital_A.DO3"]
    assert not r.twin.s.coils["digital_A.DO1"]


def test_replacement_is_refused_when_the_partner_is_busy_and_the_cycle_fault_stops():
    r = _rig(s1=_sector("valve-1"), other=parse({"trigger": {"type": "MANUAL"}, "action": {
        "type": "SEQUENCE", "max_runtime_s": 600, "steps": [{"do": "ON", "item": "pump-b"}, {"do": "RUN", "seconds": 300}]}}))
    r.e.run_now("t", "other")
    r.e.run_now("t", "s1")
    r.run(10)
    r.twin.set_fault("pump-a", "TRIP")
    r.run(5)
    b = next(p for kk, p in r.events if kk == "PROGRAM_PUMP_REPLACEMENT_BLOCKED")
    assert b["standby"] == "pump-b"
    f = next(x for x in r.e.finished if x["program_id"] == "s1")
    assert f["result"] == "FAILED" and "not possible" in f["reason"]
    assert any(o.program_id == "other" for o in r.e.ops.values())          # the other operation is not preempted


def test_no_replacement_when_the_failed_pump_is_not_verified_off():
    r = _rig(s1=_sector("valve-1"))
    r.e.run_now("t", "s1")
    r.run(10)
    r.e._off_state = lambda op, iid: (False, True, None) if iid == "pump-a" else (True, False, False)
    r.twin.set_fault("pump-a", "TRIP")
    r.run(40)
    assert ("digital_A.DO3", True) not in _targets(r)               # pump-b never started
    f = r.e.finished[-1] if r.e.finished else None
    assert "PROGRAM_PUMP_FAILED" in r.kinds() and (f is None or f["result"] in ("FAILED", "STOP_UNVERIFIED"))


def test_the_operation_limit_is_not_extended_by_the_changeover():
    r = _rig(s1=_sector("valve-1", run_s=100, limit=110))
    r.e.run_now("t", "s1")
    r.run(50)
    op = next(iter(r.e.ops.values()))
    deadline = op.deadline_mono
    r.twin.set_fault("pump-a", "TRIP")
    r.run(3)
    assert "PROGRAM_PUMP_REPLACED" in r.kinds() and op.deadline_mono == deadline
    r.run(80)
    f = r.e.finished[-1]
    assert f["runtime_s"] <= 110 + 15                          # the guard still bounds startup to verified stop
