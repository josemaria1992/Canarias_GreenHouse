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
def _litre_rig(target=50, no_flow=120, delay_s=8, lose_meter=False, cap=None):
    body = _sector("valve-1").model_dump(mode="json")
    body["action"]["steps"][2] = {"do": "DELIVER", "meter": "fm", "target_L": target, "no_flow_timeout_s": no_flow}
    items = _items()
    if cap is not None:                                          # the pumps' own maximum volume per run
        for iid, start, fault in (("pump-a", PA, "digital_A.DI1"), ("pump-b", PB, "digital_A.DI4")):
            items[iid] = view(iid, "pump", {"start": start, "fault": fault}, limits={"max_runtime_s": 3600, "max_volume_L": cap})
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


# ------------------------------------------------------------------ R402-01: a start waiting its turn is cancelled by a stop
def _steps(valve, run_s=60):
    return {"type": "SEQUENCE", "max_runtime_s": 600, "steps": [
        {"do": "ON", "item": valve}, {"do": "ON", "item": "pump-a"}, {"do": "RUN", "seconds": run_s},
        {"do": "OFF", "item": "pump-a"}, {"do": "OFF", "item": valve}]}


def _waiting_rig(second):
    """Sector 1 waters (weekly); Sector 2, on the same pump group, waits its turn with the given trigger."""
    from tests.unit.test_program_engine import Rig
    items = _items() | {"btn": view("btn", "button", {"pressed": "digital_A.DI3"})}
    r = Rig(items, {"s1": (1, _weekly("valve-1", run_s=60)), "s2": (1, parse({"trigger": second, "duty": ["pump-a", "pump-b"],
                                                                               "action": _steps("valve-2")}))})
    for p in ("digital_A.DI1", "digital_A.DI4"):
        r.twin.set_input(p, False)
    r.twin.set_input("digital_A.DI3", False)
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    r.e.request_mode("t", "AUTO")
    r.run(65)                                                     # the windows open at 05:01; Sector 1 goes first
    r.twin.set_input("digital_A.DI3", True)                       # (the condition holds from now on)
    r.run(5)
    assert [o.program_id for o in r.e.ops.values()] == ["s1"] and r.twin.s.coils.get(V1)
    assert r.e.program_states()["s2"]["state"] == "WAITING", r.e.program_states()["s2"]
    return r


SECOND = {"weekly": {"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:01", "end": "05:30"}]},
          "repeat": {"type": "WINDOW_REPEAT", "entries": [{"days": ["MON"], "start": "05:01", "end": "05:30"}],
                     "rest_s": 30, "max_count": 3},
          "condition": {"type": "CONDITION", "when": {"item": "btn", "role": "pressed", "op": "IS_ON", "debounce_s": 1},
                        "rearm_after_s": 5}}


@pytest.mark.parametrize("trigger", sorted(SECOND))
@pytest.mark.parametrize("how", ["stop_all", "lease"])
def test_r402_01_a_start_waiting_its_turn_does_not_spring_back_after_a_stop(trigger, how):
    r = _waiting_rig(SECOND[trigger])
    before = len(r.on_frames())
    if how == "stop_all":
        r.e.stop_all("vicente", "explicit stop all")
    else:
        r.e.request_stop_all("bench lease ended")
    r.run(180)                                                    # Sector 1 stops verified; the group is free
    assert len(r.on_frames()) == before, (trigger, how)            # no new ON: nothing started by itself
    assert not r.e.ops and not r.e._turn_wait
    ev = [p for k, p in r.events if k == "PROGRAM_START_CANCELLED"]
    assert ev and ev[0]["program"] == "s2" and ev[0]["why"] in ("explicit stop all", "bench lease ended")
    if trigger != "condition":                                    # the window's occurrence is settled, not left open
        assert any(k.startswith("s2@1") and v["state"] == "CANCELLED" for k, v in r.e.store.occ.items()), r.e.store.occ
    assert r.e.mode == "AUTO"                                     # the mode policy is unchanged


def test_r402_01_a_cancelled_condition_rearms_only_after_its_condition_clears():
    r = _waiting_rig(SECOND["condition"])
    r.e.stop_all("vicente", "explicit stop all")
    r.run(180)
    before = len(r.on_frames())
    r.twin.set_input("digital_A.DI3", False)                      # the condition clears, re-arms, then holds again
    r.run(8)
    r.twin.set_input("digital_A.DI3", True)
    r.run(5)
    assert len(r.on_frames()) > before and any(o.program_id == "s2" for o in r.e.ops.values())


# ------------------------------------------------------------------ R402-03: a target met during a changeover starts no partner
def _drop_off(r, seconds):
    """The failed pump's OFF commands are lost for a while, so it keeps pumping measured water (residual flow)."""
    base, t0 = r.e.io.command, r.clock.mono()

    def command(point, desired, **kw):
        if point == PA and desired is False and r.clock.mono() - t0 < seconds:
            return type("Dropped", (), {"outcome": None, "detail": "dropped"})()
        return base(point, desired, **kw)
    r.e.io.command = command


def test_r402_03_a_target_met_during_the_changeover_ends_the_cycle_without_starting_the_partner():
    r, op = _litre_rig(target=11, delay_s=20)
    _drop_off(r, 8)
    r.run(90)
    assert not any(t == PB and d for t, d in _targets(r))           # the standby pump never got an ON
    f = r.e.finished[-1]
    assert f["result"] == "DONE" and f["measured_L"] >= 11 and "target reached during the pump changeover" in f["reason"]
    ev = next(p for k, p in r.events if k == "PROGRAM_PUMP_REPLACEMENT_NOT_NEEDED")
    assert ev["failed"] == "pump-a" and ev["standby"] == "pump-b"
    assert not r.twin.s.coils.get(PA) and not r.twin.s.coils.get(V1)  # every OFF went out through the stop path
    assert "PROGRAM_PUMP_REPLACED" not in r.kinds()


def _with_run_after(r, op, target):
    body = _sector("valve-1").model_dump(mode="json")
    steps = body["action"]["steps"]
    steps[2] = {"do": "DELIVER", "meter": "fm", "target_L": target, "no_flow_timeout_s": 120}
    steps.insert(3, {"do": "RUN", "seconds": 20})                  # watering continues after the litre step
    r.programs["s1"] = (1, parse(body))
    op.body = parse(body)


def test_r402_03_the_volume_cap_crossed_by_residual_flow_during_the_changeover_stops_without_the_partner():
    # the target (11 L) is below the cap (12 L), as the engine requires at the start; the failed pump's lost OFFs let
    # it keep pumping, so the counted water crosses the cap while the partner would still be needed for the RUN step
    r, op = _litre_rig(target=11, delay_s=20, cap=12.0)
    _with_run_after(r, op, 11)
    _drop_off(r, 12)
    r.run(90)
    assert not any(t == PB and d for t, d in _targets(r))
    f = r.e.finished[-1]
    assert f["result"] == "RUNTIME_GUARD" and "maximum volume 12 L" in f["reason"], f
    assert not r.twin.s.coils.get(PA) and not r.twin.s.coils.get(V1)


def test_r402_03_a_met_target_with_more_watering_after_it_still_brings_in_the_partner():
    r, op = _litre_rig(target=11, delay_s=8)
    _with_run_after(r, op, 11)
    r.run(60)
    assert any(t == PB and d for t, d in _targets(r)) and "PROGRAM_PUMP_REPLACED" in r.kinds()


# ------------------------------------------------------------------ R402-02: litres are counted in the meter's own unit
def _unit_rig(unit, eng_max, target=5.0):
    """A meter whose type and scaling read in `unit`; 13.6 mA (60 % of 4-20 mA) while the pump runs."""
    from aquacrop.equipment.definitions import TEMPLATES
    body = _sector("valve-1").model_dump(mode="json")
    body["action"]["steps"][2] = {"do": "DELIVER", "meter": "fm", "target_L": target, "no_flow_timeout_s": 120}
    items = _items()
    roles = [dict(x, unit=unit) for x in TEMPLATES["flow"]["body"]["roles"]]
    scaling = {"flow": {"raw_min": 4, "raw_max": 20, "raw_unit": "mA", "eng_min": 0, "eng_max": eng_max, "unit": unit,
                        "provenance": "DEMO"}}
    items["fm"] = view("fm", "flow", {"flow": "analog_A.AI1"}, {"scaling": scaling}, roles=roles)
    r = Rig(items, {"p": (1, parse(body))})
    base = r.e.io.sample

    def sample(point):
        s = base(point)
        if point != "analog_A.AI1":
            return s
        on = r.twin.s.coils.get(PA) or r.twin.s.coils.get(PB)
        return replace(s, acquired_utc=r.clock.utc(), raw_value=13600 if on else 4000, raw_unit="uA", quality=Quality.GOOD, reason=None)
    r.e.io.sample = sample
    for p in ("digital_A.DI1", "digital_A.DI4"):
        r.twin.set_input(p, False)
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    return r


@pytest.mark.parametrize("unit,eng_max", [("L/min", 1000 / 60), ("L/h", 1000), ("m³/h", 1.0), ("m3/h", 1.0), ("L/s", 1000 / 3600)])
def test_r402_02_a_litre_target_counts_the_same_water_whatever_the_flow_unit(unit, eng_max):
    # every scaling is the same physical meter: 60 % of range = 10 L/min, so 5 L take about 30 s of pumping
    r = _unit_rig(unit, eng_max)
    r.e.run_now("t", "p")
    actual = 0.0
    for _ in range(240):
        if r.twin.s.coils.get(PA) or r.twin.s.coils.get(PB):
            actual += 10.0 * 0.5 / 60.0
        r.run(0.5)
        if r.e.finished:
            break
    f = r.e.finished[-1]
    assert f["result"] == "DONE", f
    assert 4.9 <= f["measured_L"] <= 5.5 and abs(actual - f["measured_L"]) <= 0.6, (unit, actual, f["measured_L"])


def test_r402_02_a_meter_reading_another_quantity_is_refused_for_a_litre_target():
    r = _unit_rig("bar", 10)
    with pytest.raises(Refused, match="not a flow rate"):
        r.e.run_now("t", "p")
    assert r.on_frames() == []
