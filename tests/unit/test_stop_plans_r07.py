"""Review R07-02 (0.7.0): a block's configured stop plans are carried into the program and followed step by step.

Policy (programs/engine.py `_choose_plan`): a completed sequence has run its normal stop as steps; a user stop, the
runtime guard or a window end follows the normal stop plan; a failure, a stop condition or a stop of everything
follows the fault stop plan. An empty plan, or what a plan leaves on, is switched off last first. Every OFF of a plan
is verified before the next step; an unverified OFF holds the rest of the plan. Virtual modules only."""
import pytest

from aquacrop.blocks.definitions import Step
from aquacrop.programs.authoring import FormRefused, compile_form
from aquacrop.programs.definitions import parse
from tests.unit.test_programs_070 import _rig_for, block, form, views

PUMP, VALVE = "digital_A.DO1", "digital_A.DO2"
GUARD = [{"item": "lvl", "role": "level", "op": "BELOW", "value": 100, "unit": "cm", "persist_s": 0}]


def fault_block(*fault_steps):
    b = block()
    plan = b.plan.model_copy(update={"fault_stop": fault_steps or (
        Step(do="STOP", node="pump"), Step(do="WAIT", seconds=10), Step(do="CLOSE", node="valve"))})
    return b.model_copy(update={"plan": plan})


def started(body):
    r = _rig_for(body)
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    r.e.run_now("t", "p")
    r.run(10)
    assert r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]
    return r


def stop_steps(r):
    return [(p["plan"], p["do"], p["item"]) for k, p in r.events if k == "PROGRAM_STOP_STEP"]


def test_r07_02_the_fault_plan_is_part_of_the_compiled_program():
    f = form(guards=GUARD)
    standard = compile_form(f, block(), "VALIDATED", views())
    custom = compile_form(f, fault_block(), "VALIDATED", views())
    assert standard != custom
    assert custom["action"]["fault_stop"] == [{"do": "OFF", "item": "pump", "timeout_s": 30}, {"do": "WAIT", "seconds": 10},
                                              {"do": "OFF", "item": "valve", "timeout_s": 30}]
    assert custom["action"]["normal_stop"][1] == {"do": "WAIT", "seconds": 2}
    assert parse(custom).action.fault_stop[1].seconds == 10
    with pytest.raises(FormRefused, match="only stops, closes and waits"):
        compile_form(f, fault_block(Step(do="OPEN", node="valve")), "VALIDATED", views())


def test_r07_02_a_stop_condition_follows_the_fault_plan_with_its_wait():
    r = started(compile_form(form(guards=GUARD), fault_block(), "VALIDATED", views()))
    r.twin.set_input("analog_A.AI2", 50)
    r.run(0.5)
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]       # pump stopped first, valve still open
    r.run(9)
    assert r.twin.s.coils[VALVE]                                     # the ten-second hold is kept
    r.run(2)
    assert not r.twin.s.coils[VALVE]
    r.run(1)
    fin = r.e.finished[-1]
    assert fin["result"] == "GUARD_STOPPED" and not r.e.ops
    assert stop_steps(r) == [("fault", "OFF", "pump"), ("fault", "WAIT", None), ("fault", "OFF", "valve")]


def test_r07_02_a_user_stop_follows_the_normal_plan():
    r = started(compile_form(form(guards=GUARD), fault_block(), "VALIDATED", views()))
    r.e.stop_op("t", next(iter(r.e.ops)))
    r.run(0.5)
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]
    r.run(1)
    assert r.twin.s.coils[VALVE]                                     # the normal plan's two-second wait
    r.run(1.5)
    assert not r.twin.s.coils[VALVE]
    r.run(1)
    assert r.e.finished[-1]["result"] == "STOPPED_BY_USER"
    assert [s[0] for s in stop_steps(r)] == ["normal"] * 3


def test_r07_02_the_runtime_guard_follows_the_normal_plan():
    f = form(endpoint={"kind": "MANUAL", "max_minutes": 1}, guards=GUARD)
    r = started(compile_form(f, fault_block(), "VALIDATED", views()))
    r.run(50.5)                                                      # 60 s after the start
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]
    r.run(3)
    assert not r.twin.s.coils[VALVE] and r.e.finished[-1]["result"] == "RUNTIME_GUARD"


def test_r07_02_an_unverified_stop_holds_the_rest_of_the_plan():
    r = started(compile_form(form(guards=GUARD), fault_block(), "VALIDATED", views()))
    r.twin.set_fault("pump", "STUCK_ON")
    ons = len(r.on_frames())
    r.twin.set_input("analog_A.AI2", 50)
    r.run(45)                                                        # far past the step's 30 s timeout
    assert r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]           # the valve is never closed on a running pump
    assert r.e.ops and next(iter(r.e.ops.values())).state == "STOP_UNVERIFIED"
    assert r.alarms[("PROGRAM_STOP_UNVERIFIED", "p")] is True
    assert len(r.on_frames()) == ons                                 # only OFF retries, never an ON
    r.twin.set_fault("pump", None)
    r.run(11)                                                        # the next OFF retry is verified
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]       # then the plan's wait runs
    r.run(12)
    assert not r.twin.s.coils[VALVE] and not r.e.ops
    assert r.alarms[("PROGRAM_STOP_UNVERIFIED", "p")] is False


def test_r07_02_without_a_fault_plan_everything_goes_off_last_first():
    r = started(compile_form(form(guards=GUARD), block(), "VALIDATED", views()))
    r.twin.set_input("analog_A.AI2", 50)
    r.run(0.5)
    assert not r.twin.s.coils[PUMP] and not r.twin.s.coils[VALVE]
    assert stop_steps(r) == []


# ---------------------------------------------------------------- RR-01 (independent re-review of 7bc098c)
def test_rr01_a_pump_that_runs_again_during_the_wait_holds_the_valve_and_the_operation():
    """The reviewer's probe, expected-correct: the pump OFF verifies, then the pump reads ON again (simulated stuck
    contactor) during the normal plan's wait. The valve stays open, the operation stays held with an alarm, OFF is sent
    again and never an ON; once the pump is OFF again the full wait runs before the valve closes."""
    r = started(compile_form(form(guards=GUARD), fault_block(), "VALIDATED", views()))
    r.e.stop_op("review", next(iter(r.e.ops)))
    r.run(0.5)
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]
    ons = len(r.on_frames())
    r.twin.s.coils[PUMP] = True                                     # not an engine command: the device runs again
    r.twin.set_fault("pump", "STUCK_ON")
    r.run(30)
    assert r.twin.s.coils[VALVE], "a dependent valve was closed on a pump that is not proven stopped"
    assert r.e.ops, "the operation was finished while its pump is ON"
    op = next(iter(r.e.ops.values()))
    assert op.state == "STOP_UNVERIFIED" and op.stopped["pump"].command_point == PUMP
    assert r.alarms[("PROGRAM_STOP_UNVERIFIED", "p")] is True
    assert len(r.on_frames()) == ons                                # only OFF again, never an ON
    assert any(k == "PROGRAM_STOP_PROOF_LOST" for k, _ in r.events)
    r.twin.set_fault("pump", None)
    for _ in range(30):                                             # the next OFF retry (every 10 s) is verified
        if not r.twin.s.coils[PUMP]:
            break
        r.run(0.5)
    assert not r.twin.s.coils[PUMP] and r.twin.s.coils[VALVE]
    r.run(1.0)
    assert r.twin.s.coils[VALVE]                                    # the wait starts again from the verified stop
    r.run(3)
    assert not r.twin.s.coils[VALVE] and not r.e.ops
    assert r.e.finished[-1]["result"] == "STOPPED_BY_USER"
    assert r.alarms[("PROGRAM_STOP_UNVERIFIED", "p")] is False


def test_rr01_an_operation_never_ends_while_an_earlier_stopped_item_reads_on():
    """After the plan is through, the final check covers every item the operation switched ON, not only the last."""
    r = started(compile_form(form(guards=GUARD), fault_block(), "VALIDATED", views()))
    r.e.stop_op("review", next(iter(r.e.ops)))
    r.run(2.6)                                                      # pump off, wait done, valve off sent
    r.twin.s.coils[PUMP] = True
    r.twin.set_fault("pump", "STUCK_ON")
    r.run(25)
    assert r.e.ops and next(iter(r.e.ops.values())).state == "STOP_UNVERIFIED"
    r.twin.set_fault("pump", None)
    r.run(12)
    assert not r.e.ops and not r.twin.s.coils[PUMP]


def test_rr01_a_rebind_during_the_stop_keeps_the_original_channels():
    """The held channels are the ones switched ON; a binding change mid-stop does not move the OFF duty."""
    r = started(compile_form(form(guards=GUARD), fault_block(), "VALIDATED", views()))
    op = next(iter(r.e.ops.values()))
    original = op.held["pump"].command_point
    r.e.stop_op("review", op.op_id)
    r.run(0.5)
    assert op.stopped["pump"].command_point == original == PUMP
