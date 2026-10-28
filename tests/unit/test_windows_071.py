"""0.7.1 programs over windows (owner decisions 26 October 2026), PRG-01..PRG-06, deterministic simulated time.

The owner's example: a ten-minute cycle that started at 09:55 is stopped when its window finishes at 10:00; the next
window, starting at 10:00, runs its own first cycle as soon as the stop is verified and the equipment is free, without
the earlier window's rest. Here the rig's clock starts on Monday 05:00 (Europe/Stockholm), so the example is 05:05 ->
05:10. Commands are read from the audited command path, in order. Virtual modules only."""
from datetime import timedelta

import pytest

from aquacrop.blocks.definitions import Safety
from aquacrop.programs.authoring import FormRefused, WateringForm, compile_form
from tests.unit.test_program_engine import T0
from tests.unit.test_programs_070 import _rig_for, block, views

PUMP, VALVE = "digital_A.DO1", "digital_A.DO2"


def safe_block(max_s=900, meter=True):
    return block(meter).model_copy(update={"safety": Safety(max_runtime_s=max_s)})


def form2(endpoint, windows, rest=5, **sched):
    return WateringForm.model_validate({"form_version": 2, "block_id": "b1", "block_version": 1, "endpoint": endpoint,
                                        "schedule": {"kind": "WEEKLY", "days": ["MON"], "windows": windows,
                                                     "rest_minutes": rest} | sched})


def at(r, hhmm_ss: str):
    """Run the rig until local Monday hh:mm:ss (T0 is 05:00:00 local)."""
    h, m, *s = (int(x) for x in hhmm_ss.split(":"))
    target = T0 + timedelta(hours=h - 5, minutes=m, seconds=s[0] if s else 0)
    left = (target - r.clock.utc()).total_seconds()
    if left > 0:
        r.run(left)


def commands(r):
    """(simulated utc, point, desired) of every command the engine sent through the audited path, in order."""
    return r.sent


def started(r):
    return [p for k, p in r.events if k == "PROGRAM_OP_STARTED"]


def ended(r):
    return [p for k, p in r.events if k == "PROGRAM_OP_ENDED"]


def auto(body):
    r = _rig_for(body)
    r.sent = []
    real = r.e.io.command

    def logged(point, desired, **kw):
        r.sent.append((r.clock.utc(), point, desired))
        return real(point, desired, **kw)
    r.e.io.command = logged
    r.run(1)
    r.e.approve_recovery("t", "AUTO")
    return r


# ---------------------------------------------------------------- PRG-01 one endpoint, the bound from block safety
def test_prg01_one_endpoint_per_cycle_and_the_bound_comes_from_block_safety():
    f = form2({"kind": "TIME", "minutes": 10}, [{"start": "05:05", "finish": "05:10"}])
    body = compile_form(f, safe_block(900), "VALIDATED", views())
    assert body["action"]["max_runtime_s"] == 900 and body["trigger"]["type"] == "WINDOW_REPEAT"
    assert body["trigger"]["entries"] == [{"days": ["MON"], "start": "05:05", "end": "05:10", "from_date": None,
                                           "to_date": None}] and body["trigger"]["rest_s"] == 300
    vol = compile_form(form2({"kind": "VOLUME", "litres": 40}, [{"start": "05:05", "finish": "05:10"}]),
                       safe_block(900), "VALIDATED", views())
    assert [s["do"] for s in vol["action"]["steps"]].count("RUN") == 0              # litres only, no second duration
    with pytest.raises(FormRefused, match="safety settings"):
        compile_form(f, block(), "VALIDATED", views())                              # no bound on the block: refused
    with pytest.raises(FormRefused, match="does not fit"):
        compile_form(form2({"kind": "TIME", "minutes": 15}, [{"start": "05:05", "finish": "05:30"}]),
                     safe_block(900), "VALIDATED", views())
    with pytest.raises(ValueError, match="one endpoint"):
        form2({"kind": "TIME", "minutes": 10, "litres": 5}, [{"start": "05:05", "finish": "05:10"}])
    with pytest.raises(ValueError, match="safety setting, not part of the program"):
        form2({"kind": "TIME", "minutes": 10, "max_minutes": 20}, [{"start": "05:05", "finish": "05:10"}])
    with pytest.raises(ValueError, match="overlap"):
        form2({"kind": "TIME", "minutes": 1}, [{"start": "05:05", "finish": "05:20"}, {"start": "05:10", "finish": "05:30"}])


# ---------------------------------------------------------------- PRG-02 first cycle without rest; rest after completion
def test_prg02_first_cycle_without_rest_then_rest_after_each_verified_completion():
    f = form2({"kind": "TIME", "minutes": 1}, [{"start": "05:05", "finish": "05:20"}], rest=3)
    r = auto(compile_form(f, safe_block(600), "VALIDATED", views()))
    at(r, "05:05:02")
    assert len(started(r)) == 1                                   # at the window start, no initial rest
    at(r, "05:07:00")
    assert len(ended(r)) == 1 and ended(r)[0]["result"] == "DONE"
    assert r.e.view()["programs"]["p"]["state"] == "RESTING"
    first_end = ended(r)[0]["ended_utc"]
    at(r, "05:08:30")
    assert len(started(r)) == 1                                   # 3 min rest after the verified end (~05:06:10)
    at(r, "05:09:40")
    assert len(started(r)) == 2
    from datetime import datetime
    second_on = next(c for c in commands(r) if c[2] and c[0] > datetime.fromisoformat(first_end))
    assert (second_on[0] - datetime.fromisoformat(first_end)).total_seconds() >= 3 * 60


# ---------------------------------------------------------------- PRG-03 the owner's adjacent-window example
def test_prg03_adjacent_windows_stop_verify_then_new_first_cycle_without_old_rest():
    f = form2({"kind": "TIME", "minutes": 10}, [{"start": "05:05", "finish": "05:10"}, {"start": "05:10", "finish": "05:30"}],
              rest=15)
    r = auto(compile_form(f, safe_block(900), "VALIDATED", views()))
    at(r, "05:05:30")
    assert len(started(r)) == 1 and r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]
    at(r, "05:10:00.5" if False else "05:10:00")
    r.run(0.5)
    old = ended(r) or [None]
    # the old cycle is cut at its window finish through its normal stop: pump OFF first, the valve after its wait
    assert not r.twin.s.coils[PUMP]
    while r.e.ops and next(iter(r.e.ops.values())).occurrence_key.endswith(":0#0"):
        r.run(0.5)
        assert (r.clock.utc() - T0).total_seconds() < 11 * 60, "the old operation never finished its stop"
    old = ended(r)[0]
    assert old["result"] == "WINDOW_ENDED" and old["occurrence"].endswith(":0#0")
    r.run(2)
    new = started(r)
    assert len(new) == 2 and new[1]["key"].endswith(":1#0"), new           # the new window's own first cycle
    cmds = commands(r)
    stop_pump = max(i for i, c in enumerate(cmds) if c[1] == PUMP and not c[2] and c[0] <= T0 + timedelta(minutes=10, seconds=10))
    new_on = min(i for i, c in enumerate(cmds) if c[2] and c[0] >= T0 + timedelta(minutes=10))
    assert stop_pump < new_on                                                # never ON before the verified stop
    start_s = (cmds[new_on][0] - (T0 + timedelta(minutes=10))).total_seconds()
    assert start_s < 30, f"the new window waited {start_s} s: it must not inherit the 15 min rest"


# ---------------------------------------------------------------- PRG-04 litres cut by the window are not DONE
def test_prg04_an_unmet_volume_at_the_finish_is_window_ended_and_the_next_window_has_its_own_target():
    f = form2({"kind": "VOLUME", "litres": 500}, [{"start": "05:05", "finish": "05:08"}, {"start": "05:08", "finish": "05:12"}])
    r = auto(compile_form(f, safe_block(900), "VALIDATED", views()))
    r.twin.set_input("analog_A.AI1", 6.0)                                   # 6 L/min measured: 500 L is far away
    at(r, "05:09:00")
    first = ended(r)[0]
    assert first["result"] == "WINDOW_ENDED" and first["result"] != "DONE"
    assert 0 < first["measured_L"] < first["requested_L"] == 500
    occ = r.store.occ[first["occurrence"]]
    assert occ["state"] == "WINDOW_ENDED" and occ["doc"]["requested_L"] == 500
    assert occ["doc"]["unmet_L"] == pytest.approx(500 - first["measured_L"], abs=0.01)
    second = [o for o in r.e.ops.values()]
    assert second and second[0].requested_L == 500 and second[0].measured_L < 20   # its own target, not leftover litres


# ---------------------------------------------------------------- PRG-05 unverified stop holds; no late start
def test_prg05_a_stuck_pump_holds_the_stop_and_the_next_window_never_starts_late():
    f = form2({"kind": "TIME", "minutes": 10}, [{"start": "05:05", "finish": "05:10"}, {"start": "05:10", "finish": "05:12"}])
    r = auto(compile_form(f, safe_block(900), "VALIDATED", views()))
    at(r, "05:09:30")
    ons = len([c for c in commands(r) if c[2]])
    r.twin.set_fault("pump", "STUCK_ON")
    at(r, "05:11:30")
    op = next(iter(r.e.ops.values()))
    assert op.state == "STOP_UNVERIFIED" and r.twin.s.coils[VALVE]          # the valve is not closed on the pump
    assert r.alarms[("PROGRAM_STOP_UNVERIFIED", "p")] is True
    assert len([c for c in commands(r) if c[2]]) == ons                     # no ON for the new window
    st = r.e.view()["programs"]["p"]
    assert st["state"] == "STOP_UNVERIFIED"
    at(r, "05:12:30")                                                       # the new window has expired meanwhile
    r.twin.set_fault("pump", None)
    r.run(30)
    assert len([c for c in commands(r) if c[2]]) == ons                     # never a late start
    missed = [v for k, v in r.store.occ.items() if k.endswith(":1#0")]
    assert missed and missed[0]["state"] == "MISSED" and "not verified stopped" in missed[0]["doc"]["why"]


# ---------------------------------------------------------------- PRG-06 the block bound stops through the plan
def test_prg06_the_block_runtime_bound_stops_through_the_normal_plan_and_keeps_verifying():
    f = form2({"kind": "VOLUME", "litres": 500}, [{"start": "05:05", "finish": "05:40"}])
    r = auto(compile_form(f, safe_block(120), "VALIDATED", views()))
    r.twin.set_input("analog_A.AI1", 6.0)
    at(r, "05:07:00")
    r.run(1)
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]               # pump first, the valve after its wait
    r.twin.set_fault("pump", "STUCK_ON")
    r.twin.s.coils[PUMP] = True                                             # the pump runs again after its stop
    r.run(20)
    assert r.e.ops and r.twin.s.coils[VALVE]                                # the bound is not a release of the stop
    r.twin.set_fault("pump", None)
    r.run(25)
    fin = ended(r)[0]
    assert fin["result"] == "RUNTIME_GUARD" and not r.e.ops and not r.twin.s.coils[VALVE]
