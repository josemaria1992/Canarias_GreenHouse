"""Codex review 0.7.7, C077-01..05 (fake clock, fake I/O, equipment twin; no hardware): the recovery of a failed duty
pump with the owner's decisions of 27 October 2026. Sectors sharing a pump group never water at the same time; a stop
leaves no hidden restart; the failed pump gets OFF until it is verified OFF; a litre step keeps counting the meter
through a changeover; and a waiting remainder has no time limit of its own (only its window, or a stop, ends it)."""
from dataclasses import replace

import pytest

from aquacrop.domain.model import Quality
from aquacrop.programs.definitions import parse
from aquacrop.programs.engine import Refused
from tests.unit.test_program_engine import FLOW_SCALING, Rig, view
from tests.unit.test_pump_replacement_075 import _items, _rig, _sector, _targets

V1, V2, PA, PB = "digital_A.DO2", "digital_A.DO5", "digital_A.DO1", "digital_A.DO3"


def _weekly(valve, start="05:01", end="05:30", run_s=120):
    return parse({"trigger": {"type": "WEEKLY", "entries": [{"days": ["MON"], "start": start, "end": end}]},
                  "duty": ["pump-a", "pump-b"], "action": {"type": "SEQUENCE", "max_runtime_s": 600, "steps": [
                      {"do": "ON", "item": valve}, {"do": "ON", "item": "pump-a"}, {"do": "RUN", "seconds": run_s},
                      {"do": "OFF", "item": "pump-a"}, {"do": "OFF", "item": valve}]}})


def _paused(r, program="s1", hold="bench"):
    """Sector 1 waters on pump-a; something outside the sector programs holds pump-b; pump-a trips: Sector 1 stops
    through its normal stop and the rest of its cycle waits for pump-b (the defensive pause path)."""
    r.e.run_now("t", program)
    r.run(10)
    assert r.e.arbiter.acquire(hold, ["pump-b"]) is None
    r.twin.set_fault("pump-a", "TRIP")
    r.run(8)
    assert program in r.e._resume and not any(o.program_id == program for o in r.e.ops.values())
    assert not r.twin.s.coils.get(PA) and not r.twin.s.coils.get(V1)


def _never_both(r, seconds):
    """Run, checking at every step that the two sector valves are never open together."""
    for _ in range(int(seconds / 0.5)):
        r.run(0.5)
        assert not (r.twin.s.coils.get(V1) and r.twin.s.coils.get(V2)), "both sectors open"


# ------------------------------------------------------------------ C077-01: a stop leaves no hidden restart
def test_c077_01_stop_all_cancels_a_waiting_remainder_and_nothing_switches_on_afterwards():
    r = _rig(s1=_sector("valve-1"))
    _paused(r)
    before = len(r.on_frames())
    r.e.stop_all("vicente", "explicit stop all")
    assert r.e._resume == {}
    cancel = next(p for k, p in r.events if k == "PROGRAM_RESUME_CANCELLED")
    assert cancel["program"] == "s1" and cancel["why"] == "explicit stop all" and cancel["remaining"]
    r.e.arbiter.release("bench")                                  # the pump it waited for is free now
    r.run(120)
    assert len(r.on_frames()) == before                           # no ON after the cancellation
    assert "PROGRAM_RESUMED" not in r.kinds() and not r.e.ops


def test_c077_01_a_user_stop_lease_loss_or_deactivation_also_cancels_the_remainder():
    for how in ("stop_op", "lease", "deactivate"):
        r = _rig(s1=_sector("valve-1"))
        _paused(r)
        before = len(r.on_frames())
        if how == "stop_op":
            from_op = r.e._resume["s1"]["from_op"]
            assert r.e.stop_op("vicente", from_op)["state"] == "CANCELLED"
        elif how == "lease":
            r.e.request_stop_all("bench lease ended")
        else:
            r.programs.pop("s1")
        r.run(1)
        r.e.arbiter.release("bench")
        r.run(60)
        assert r.e._resume == {} and "PROGRAM_RESUME_CANCELLED" in r.kinds(), how
        assert len(r.on_frames()) == before, how


def test_c077_01_leaving_auto_cancels_an_automatic_remainder():
    r = _rig()
    r.programs["s1"] = (1, _weekly("valve-1"))
    r.e.request_mode("t", "AUTO")
    r.run(70)                                                    # 05:01: the window opens, Sector 1 starts
    assert any(o.program_id == "s1" for o in r.e.ops.values())
    r.e.arbiter.acquire("bench", ["pump-b"])
    r.twin.set_fault("pump-a", "TRIP")
    r.run(8)
    assert r.e._resume["s1"]["origin"] == "AUTO"
    r.e.request_mode("vicente", "MANUAL")
    r.run(2)
    assert r.e._resume == {} and next(p for k, p in r.events if k == "PROGRAM_RESUME_CANCELLED")["why"] == "mode changed to MANUAL"
    before = len(r.on_frames())
    r.e.arbiter.release("bench")
    r.run(60)
    assert len(r.on_frames()) == before


# ------------------------------------------------------------------ C077-05: no invented time limit
def test_c077_05_a_remainder_waits_longer_than_an_hour_while_nothing_ends_it_then_finishes():
    r = _rig(s1=_sector("valve-1", run_s=120))
    _paused(r)
    rem = r.e._resume["s1"]["remaining"]
    r.run(3700, dt=5.0)                                         # more than an hour, no window: still waiting
    assert "s1" in r.e._resume and "PROGRAM_RESUME_ABANDONED" not in r.kinds()
    assert r.e.program_states()["s1"]["state"] == "WAITING"
    r.e.arbiter.release("bench")
    r.run(5)
    assert "PROGRAM_RESUMED" in r.kinds() and r.twin.s.coils.get(PB) and r.twin.s.coils.get(V1)
    r.run(rem + 15)
    f = [x for x in r.e.finished if x["program_id"] == "s1"][-1]
    assert f["result"] == "DONE" and f["origin"] == "RESUME"


def test_c077_05_a_remainder_ends_when_its_window_ends_and_says_so():
    r = _rig()
    r.programs["s1"] = (1, _weekly("valve-1", start="05:01", end="05:03", run_s=100))
    r.e.request_mode("t", "AUTO")
    r.run(70)
    r.e.arbiter.acquire("bench", ["pump-b"])
    r.twin.set_fault("pump-a", "TRIP")
    r.run(8)
    assert "s1" in r.e._resume
    r.run(120)                                                  # 05:03 passes
    ab = next(p for k, p in r.events if k == "PROGRAM_RESUME_ABANDONED")
    assert ab["why"] == "its window ended" and "s1" not in r.e._resume
    r.e.arbiter.release("bench")
    before = len(r.on_frames())
    r.run(30)
    assert len(r.on_frames()) == before


# ------------------------------------------------------------------ C077-02: sectors take turns
def test_c077_02_a_second_sector_on_the_same_pump_group_is_refused_by_hand_with_the_reason():
    r = _rig(s1=_sector("valve-1"), s2=_sector("valve-2"))
    r.e.run_now("t", "s1")
    r.run(3)
    with pytest.raises(Refused, match="waits its turn: s1 is using the same pump group"):
        r.e.run_now("t", "s2")
    _never_both(r, 10)
    assert r.twin.s.coils.get(V1) and not r.twin.s.coils.get(V2) and not r.twin.s.coils.get(PB)


def test_c077_02_a_sector_using_one_pump_of_the_pair_by_name_is_in_the_group_too():
    s2 = parse({"trigger": {"type": "MANUAL"}, "action": {"type": "SEQUENCE", "max_runtime_s": 600, "steps": [
        {"do": "ON", "item": "valve-2"}, {"do": "ON", "item": "pump-b"}, {"do": "RUN", "seconds": 60}]}})
    r = _rig(s1=_sector("valve-1"), s2=s2)
    r.e.run_now("t", "s2")
    r.run(3)
    with pytest.raises(Refused, match="waits its turn"):
        r.e.run_now("t", "s1")


def test_c077_02_scheduled_sectors_follow_each_other_without_overlap_in_healthy_operation():
    r = _rig()
    r.programs["s1"] = (1, _weekly("valve-1", run_s=60))
    r.programs["s2"] = (1, _weekly("valve-2", run_s=60))         # same window: the second waits its turn
    r.e.request_mode("t", "AUTO")
    r.run(62)
    _never_both(r, 200)
    done = {x["program_id"]: x for x in r.e.finished}
    assert done["s1"]["result"] == "DONE" and done["s2"]["result"] == "DONE"
    assert "PROGRAM_WAITING_TURN" in r.kinds() and "PROGRAM_CONFLICT" not in r.kinds()
    ends = {p["program_id"]: i for i, (k, p) in enumerate(r.events) if k == "PROGRAM_OP_ENDED"}
    starts = {p["program"]: i for i, (k, p) in enumerate(r.events) if k == "PROGRAM_OP_STARTED"}
    first, second = sorted(("s1", "s2"), key=lambda p: starts[p])
    assert ends[first] < starts[second]                          # the next starts only after the verified stop


def test_c077_02_scheduled_progression_after_a_pump_failure_healthy_pump_finishes_then_next_sector():
    r = _rig()
    r.programs["s1"] = (1, _weekly("valve-1", run_s=60))
    r.programs["s2"] = (1, _weekly("valve-2", run_s=60))
    r.e.request_mode("t", "AUTO")
    r.run(62)
    first = next(o for o in r.e.ops.values())
    lead = PA if r.twin.s.coils.get(PA) else PB
    r.run(20)
    r.twin.set_fault("pump-a" if lead == PA else "pump-b", "TRIP")
    _never_both(r, 260)
    assert "PROGRAM_PUMP_REPLACED" in r.kinds()
    done = {x["program_id"]: x for x in r.e.finished}
    assert done[first.program_id]["result"] == "DONE"
    other = "s2" if first.program_id == "s1" else "s1"
    assert done[other]["result"] == "DONE"
    st = [p for k, p in r.events if k == "PROGRAM_OP_STARTED" and p["program"] == other][-1]
    assert st["lead"] == ("pump-b" if lead == PA else "pump-a")  # the healthy pump serves the next sector


def test_c077_02_programs_outside_the_pump_group_are_not_serialized():
    lamp = parse({"trigger": {"type": "MANUAL"}, "action": {"type": "SEQUENCE", "max_runtime_s": 60, "steps": [
        {"do": "ON", "item": "valve-2"}, {"do": "RUN", "seconds": 20}]}})
    r = _rig(s1=_sector("valve-1"), drain=lamp)
    r.e.run_now("t", "s1")
    r.e.run_now("t", "drain")                                    # no pump of the pair: runs alongside
    r.run(3)
    assert r.twin.s.coils.get(V1) and r.twin.s.coils.get(V2)


# ------------------------------------------------------------------ C077-03: OFF until verified
def test_c077_03_the_failed_pump_gets_off_again_until_verified_and_the_partner_waits():
    r = _rig(s1=_sector("valve-1"))
    r.e.run_now("t", "s1")
    r.run(10)
    real, t0 = r.e._off_state, r.clock.mono()
    r.e._off_state = lambda op, iid: (False, True, None) if iid == "pump-a" and r.clock.mono() - t0 < 8 else real(op, iid)
    n = len(r.audit.intents)
    r.twin.set_fault("pump-a", "TRIP")
    r.run(7.5)
    after = _targets(r)[n:]
    assert after.count((PA, False)) >= 3                         # every 2 s while unverified, never an ON
    assert (PB, True) not in after and (PA, True) not in after
    r.run(5)
    after = _targets(r)[n:]
    assert "PROGRAM_PUMP_REPLACED" in r.kinds() and after.index((PB, True)) > max(
        i for i, x in enumerate(after) if x == (PA, False))     # partner ON only after the last OFF


def test_c077_03_a_dropped_first_off_is_sent_again():
    r = _rig(s1=_sector("valve-1"))
    r.e.run_now("t", "s1")
    r.run(10)
    io, dropped = r.e.io, []
    send = io.command

    def lossy(point, desired, **kw):
        if point == PA and desired is False and not dropped:
            dropped.append(1)                                    # the first OFF never reaches the module
            return type("Dropped", (), {"outcome": None, "detail": "dropped"})()
        return send(point, desired, **kw)
    io.command = lossy
    r.twin.set_fault("pump-a", "TRIP")
    r.run(6)
    assert dropped and not r.twin.s.coils.get(PA) and "PROGRAM_PUMP_REPLACED" in r.kinds()


def test_c077_03_the_failed_pump_reading_on_again_after_the_changeover_fails_the_operation():
    r = _rig(s1=_sector("valve-1"))
    r.e.run_now("t", "s1")
    r.run(10)
    r.twin.set_fault("pump-a", "TRIP")
    r.run(4)
    assert "PROGRAM_PUMP_REPLACED" in r.kinds()
    r.twin.s.coils[PA] = True                                    # the failed pump's output reads ON again
    r.run(1)
    op = next(iter(r.e.ops.values()), None)
    assert op is None or op.state != "RUNNING"
    assert "verified OFF earlier, output reads ON again" in (op.reason if op else r.e.finished[-1]["reason"])


# ------------------------------------------------------------------ C077-04: litres through a changeover
def _litre_rig(target=50, no_flow=120, delay_s=8, lose_meter=False):
    body = _sector("valve-1").model_dump(mode="json")
    body["action"]["steps"][2] = {"do": "DELIVER", "meter": "fm", "target_L": target, "no_flow_timeout_s": no_flow}
    items = _items()
    items["fm"] = view("fm", "flow", {"flow": "analog_A.AI1"}, {"scaling": FLOW_SCALING})
    r = Rig(items, {"s1": (1, parse(body))})
    sample = r.e.io.sample
    state = {"lost": False}

    def meter(point):                                            # 60 L/min while either pump runs, 0 when both stop
        s = sample(point)
        if point != "analog_A.AI1":
            return s
        if state["lost"]:
            return None
        pumping = bool(r.twin.s.coils.get(PA) or r.twin.s.coils.get(PB))
        return replace(s, acquired_utc=r.clock.utc(), raw_value=13600 if pumping else 4000, raw_unit="uA",
                       quality=Quality.GOOD, reason=None)
    r.e.io.sample = meter
    for p in ("digital_A.DI1", "digital_A.DI4"):
        r.twin.set_input(p, False)
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    r.e.run_now("t", "s1")
    r.run(10)
    real, t0 = r.e._off_state, r.clock.mono()
    r.e._off_state = lambda op, iid: (False, False, None) if iid == "pump-a" and r.clock.mono() - t0 < delay_s else real(op, iid)
    op = next(iter(r.e.ops.values()))
    r.twin.set_fault("pump-a", "TRIP")
    if lose_meter:
        state["lost"] = True
    return r, op


@pytest.mark.parametrize("delay_s", [0, 8, 20])
def test_c077_04_a_litre_step_counts_through_short_and_long_changeovers_and_finishes_its_target(delay_s):
    r, op = _litre_rig(delay_s=delay_s)
    r.run(delay_s + 3)
    assert "PROGRAM_PUMP_REPLACED" in r.kinds()
    stopped_at = op.measured_L
    assert 9.0 <= stopped_at <= 13.5                             # ~11 s at 60 L/min whatever the delay: 0 L while stopped
    r.run(60)
    f = r.e.finished[-1]
    assert f["result"] == "DONE" and f["requested_L"] == 50 and f["uncertain_L"] == 0
    assert 50.0 <= f["measured_L"] <= 51.5                         # the original cycle's target, not a new one
    rep = next(p for k, p in r.events if k == "PROGRAM_PUMP_REPLACED")
    assert rep["op_id"] == f["op_id"] and rep["by"] == "pump-b"


def test_c077_04_a_meter_genuinely_lost_during_the_changeover_still_fails_with_the_unmeasured_volume():
    r, op = _litre_rig(delay_s=8, lose_meter=True)
    r.run(12)
    f = r.e.finished[-1] if r.e.finished else op.public(r.clock.mono())
    assert f["result"] == "FAILED" or op.state != "RUNNING"
    assert "flow reading lost" in (op.reason or "") and op.uncertain_L > 0
