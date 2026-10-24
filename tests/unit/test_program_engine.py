"""0.5.0 program engine on the equipment twin (virtual modules of the six-module bench, simulated clock).
Logic tests only: no physical behaviour is proven here."""
from datetime import datetime, timezone

import pytest

from aquacrop.config import load_config
from aquacrop.control.commands import CommandService, MemoryAudit
from aquacrop.domain.model import CommandOrigin, ControllerState
from aquacrop.equipment.definitions import TEMPLATES, ItemBody, TypeBody
from aquacrop.programs.definitions import parse
from aquacrop.programs.engine import Clock, Engine, ItemView, Refused
from aquacrop.programs.store import MemoryEngineStore
from aquacrop.twin.equipment_twin import EquipmentTwin, SimClock
from tests.conftest import ROOT

T0 = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)        # Monday 05:00 Stockholm


def view(iid, tmpl, points, body=None, **type_over):
    tb = dict(TEMPLATES[tmpl]["body"]) | type_over
    return ItemView(iid, iid, TypeBody.model_validate(tb), ItemBody.model_validate(body or {}), points,
                    {r: True for r in points})


FLOW_SCALING = {"flow": {"raw_min": 4, "raw_max": 20, "raw_unit": "mA", "eng_min": 0, "eng_max": 100, "unit": "L/min",
                         "provenance": "DEMO"}}
LEVEL_SCALING = {"level": {"raw_min": 4, "raw_max": 20, "raw_unit": "mA", "eng_min": 0, "eng_max": 1000,
                           "unit": "cm", "provenance": "DEMO"}}


class Rig:
    def __init__(self, items, programs):
        self.cfg = load_config(ROOT / "tests" / "fixtures" / "bench-6.yaml")
        self.clock = SimClock(T0)
        self._items = items
        self.twin = EquipmentTwin(self.cfg, self.clock, lambda: self._items)
        self.audit = MemoryAudit()
        self.cmds = CommandService(self.cfg, self.twin.backend, self.audit, lambda c: None, lambda: ControllerState.AUTO)
        self.store = MemoryEngineStore()
        self.events, self.alarms = [], {}
        self.programs = programs
        rig = self

        class IO:
            def sample(self, point):
                return rig.twin.samples().get(point)

            def command(self, point, desired, *, actor, reason, priority):
                cmd = rig.cmds.build(actor=actor, target=point, desired=desired, origin=CommandOrigin.CONTROLLER,
                                     reason=reason, priority=priority)
                return rig.cmds.submit(cmd)

            def module_online(self, uid):
                return rig.twin.backend.health(uid).online
        self.e = Engine("TWIN:test", "Europe/Stockholm", IO(), Clock(self.clock.utc, self.clock.mono), self.store,
                        resolve=lambda: self._items, programs=lambda: self.programs,
                        event=lambda k, p, a: self.events.append((k, p)),
                        alarm=lambda k, t, on, d: self.alarms.__setitem__((k, t), on), physical=False)
        self.twin.step(0.0)
        self.e.start()

    def run(self, seconds, dt=0.5):
        n = int(round(seconds / dt))
        for _ in range(n):
            self.twin.step(dt)
            self.e.tick()

    def on_frames(self):
        return [i for i in self.audit.intents if i.command.desired]

    def kinds(self):
        return [k for k, _ in self.events]


def lamp(iid="led1", do="digital_A.DO1", di=None):
    pts = {"on": do} | ({"lit": di} if di else {})
    return view(iid, "test_lamp", pts)


def prog(trigger, action, **kw):
    return parse({"trigger": trigger, "action": action} | kw)


def test_nothing_runs_before_restart_approval_and_approval_needs_a_clean_checklist():
    r = Rig({"led1": lamp()}, {"p": (1, prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 5}))})
    assert r.e.mode == "RECOVERY"
    with pytest.raises(Refused, match="needs MANUAL"):
        r.e.run_now("bench", "p")
    r.twin.s.coils["digital_A.DO1"] = True                    # an output found ON after the restart
    r.run(1)
    with pytest.raises(Refused, match="output reads OFF"):
        r.e.approve_recovery("vicente", "MANUAL")
    r.twin.s.coils["digital_A.DO1"] = False
    r.run(1)
    r.e.approve_recovery("vicente", "MANUAL")
    assert r.e.mode == "MANUAL" and r.on_frames() == []


def test_run_now_is_bounded_and_stops_with_verification():
    r = Rig({"led1": lamp(di="digital_A.DI1")}, {"p": (1, prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 10}))})
    r.e.approve_recovery("vicente", "MANUAL")
    r.e.run_now("bench", "p")
    r.run(5)
    assert r.twin.s.coils["digital_A.DO1"] and r.e.item_state("led1")["feedback"] == "ON"
    r.run(7)
    assert not r.twin.s.coils["digital_A.DO1"] and not r.e.ops
    assert r.e.finished[-1]["result"] == "DONE"
    assert len(r.on_frames()) == 1


def test_missing_feedback_fails_and_stops():
    r = Rig({"led1": lamp(di="digital_A.DI1")}, {"p": (1, prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 60}))})
    r.e.approve_recovery("vicente", "MANUAL")
    r.twin.set_fault("led1", "NO_FEEDBACK")
    r.e.run_now("bench", "p")
    r.run(10)
    assert not r.e.ops and r.e.finished[-1]["result"] == "FAILED" and "should be ON" in r.e.finished[-1]["reason"]
    assert not r.twin.s.coils["digital_A.DO1"] and r.alarms[("PROGRAM_OP_FAILED", "p")]


def test_weekly_window_runs_once_and_window_already_open_at_auto_is_skipped():
    p = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:00", "end": "05:02"},
                                            {"days": ["MON"], "start": "05:10", "end": "05:12"}]},
             {"type": "RUN_FOR", "item": "led1"})
    r = Rig({"led1": lamp()}, {"p": (1, p)})
    r.run(30)                                               # 05:00:30, recovery: nothing
    r.e.approve_recovery("vicente", "AUTO")                 # the 05:00 window is open: skipped
    r.run(60)
    assert r.on_frames() == []
    assert r.store.occ["p@1:2026-11-02:0"]["state"] == "MISSED"
    r.run(12 * 60)                                          # the 05:10 window runs for its 2 minutes
    assert len(r.on_frames()) == 1 and r.store.occ["p@1:2026-11-02:1"]["state"] == "DONE"
    assert abs(r.e.finished[-1]["runtime_s"] - 120) <= 1


def test_auto_to_manual_stops_every_automatic_operation_and_waits_for_verification():
    p = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:01", "end": "05:30"}]},
             {"type": "RUN_FOR", "item": "led1"})
    r = Rig({"led1": lamp(di="digital_A.DI1")}, {"p": (1, p)})
    r.e.approve_recovery("vicente", "AUTO")
    r.run(90)
    assert r.e.ops
    r.twin.set_fault("led1", "STUCK_ON")
    r.e.request_mode("vicente", "MANUAL")
    r.run(15)
    assert r.e.mode == "STOPPING" and r.alarms[("PROGRAM_STOP_UNVERIFIED", "p")]
    with pytest.raises(Refused):
        r.e.run_now("bench", "p")                            # no manual start while a stop is unverified
    r.twin.set_fault("led1", None)
    r.run(15)
    assert r.e.mode == "MANUAL" and not r.e.ops and not r.twin.s.coils["digital_A.DO1"]


def test_return_to_auto_refuses_while_manual_equipment_runs():
    p = prog({"type": "MANUAL"}, {"type": "RUN_FOR", "item": "led1", "duration_s": 60})
    r = Rig({"led1": lamp()}, {"p": (1, p)})
    r.e.approve_recovery("vicente", "MANUAL")
    r.e.run_now("bench", "p")
    r.run(2)
    with pytest.raises(Refused, match="AUTO never takes over"):
        r.e.request_mode("vicente", "AUTO")


def test_deliver_volume_integrates_fresh_flow_and_stops_on_target():
    items = {"pump": view("pump", "pump", {"start": "digital_A.DO2"}, {"sim_links": {"flow_item": "fm"}}),
             "fm": view("fm", "flow", {"flow": "analog_A.AI1"}, {"scaling": FLOW_SCALING})}
    p = prog({"type": "MANUAL"}, {"type": "DELIVER_VOLUME", "item": "pump", "meter": "fm", "target_L": 5,
                                   "max_runtime_s": 120})
    r = Rig(items, {"p": (1, p)})
    r.e.approve_recovery("vicente", "MANUAL")
    r.e.run_now("bench", "p")
    r.run(60)
    f = r.e.finished[-1]
    assert f["result"] == "DONE" and 5.0 <= f["measured_L"] <= 5.5 and f["uncertain_L"] == 0
    assert f["requested_L"] == 5


def test_deliver_volume_with_a_lost_sensor_reports_uncertain_not_delivered():
    items = {"pump": view("pump", "pump", {"start": "digital_A.DO2"}, {"sim_links": {"flow_item": "fm"}}),
             "fm": view("fm", "flow", {"flow": "analog_A.AI1"}, {"scaling": FLOW_SCALING})}
    p = prog({"type": "MANUAL"}, {"type": "DELIVER_VOLUME", "item": "pump", "meter": "fm", "target_L": 50,
                                   "max_runtime_s": 600})
    r = Rig(items, {"p": (1, p)})
    r.e.approve_recovery("vicente", "MANUAL")
    r.e.run_now("bench", "p")
    r.run(20)
    r.twin.set_fault("fm", "SENSOR_LOST")
    r.run(20)
    f = r.e.finished[-1]
    assert f["result"] == "FAILED" and f["uncertain_L"] > 0 and f["measured_L"] < 50 and "not measured" in f["reason"]


def test_no_flow_fails_the_volume_operation():
    items = {"pump": view("pump", "pump", {"start": "digital_A.DO2"}, {"sim_links": {"flow_item": "fm"}}),
             "fm": view("fm", "flow", {"flow": "analog_A.AI1"}, {"scaling": FLOW_SCALING})}
    p = prog({"type": "MANUAL"}, {"type": "DELIVER_VOLUME", "item": "pump", "meter": "fm", "target_L": 50,
                                   "max_runtime_s": 600, "no_flow_timeout_s": 10})
    r = Rig(items, {"p": (1, p)})
    r.twin.set_fault("pump", "NO_FLOW")
    r.e.approve_recovery("vicente", "MANUAL")
    r.e.run_now("bench", "p")
    r.run(30)
    assert r.e.finished[-1]["result"] == "FAILED" and "no flow" in r.e.finished[-1]["reason"]


def test_keep_between_with_hysteresis_and_a_stale_sensor_inhibits():
    items = {"pump": view("pump", "pump", {"start": "digital_A.DO2"}),
             "lv": view("lv", "level", {"level": "analog_A.AI2"}, {"scaling": LEVEL_SCALING})}
    p = prog({"type": "CONTINUOUS"}, {"type": "KEEP_BETWEEN", "item": "pump", "sensor": "lv", "sensor_role": "level",
                                       "direction": "RAISE", "low": 100, "high": 200, "debounce_s": 2,
                                       "max_run_s": 600, "min_off_s": 5})
    r = Rig(items, {"p": (1, p)})
    r.twin.set_input("analog_A.AI2", 150)
    r.run(0.5)
    r.e.approve_recovery("vicente", "AUTO")
    r.run(5)
    assert r.on_frames() == []                               # inside the band
    r.twin.set_input("analog_A.AI2", 90)
    r.run(1)
    assert r.on_frames() == []                               # debounce
    r.run(3)
    assert r.e.ops                                           # below low for 2 s: ON
    r.twin.set_input("analog_A.AI2", 150)
    r.run(2)
    assert r.e.ops                                           # hysteresis: keeps running until high
    r.twin.set_input("analog_A.AI2", 210)
    r.run(2)
    assert not r.e.ops
    r.twin.set_input("analog_A.AI2", 50)
    r.twin.set_fault("lv", "SENSOR_LOST")
    r.run(20)
    assert len(r.on_frames()) == 1                           # no reading, no start


def test_condition_trigger_debounces_and_rearms():
    items = {"btn": view("btn", "button", {"pressed": "digital_A.DI3"}), "led1": lamp()}
    p = prog({"type": "CONDITION", "when": {"item": "btn", "role": "pressed", "op": "IS_ON", "debounce_s": 1},
              "rearm_after_s": 2}, {"type": "RUN_FOR", "item": "led1", "duration_s": 3})
    r = Rig(items, {"p": (1, p)})
    r.twin.set_input("digital_A.DI3", False)
    r.e.approve_recovery("vicente", "AUTO")
    r.twin.set_input("digital_A.DI3", True)
    r.run(0.5)
    assert r.on_frames() == []
    r.run(1.5)
    assert len(r.on_frames()) == 1
    r.run(10)                                                # held: no second start
    assert len(r.on_frames()) == 1
    r.twin.set_input("digital_A.DI3", False)
    r.run(3)
    r.twin.set_input("digital_A.DI3", True)
    r.run(2)
    assert len(r.on_frames()) == 2


def test_sequence_verifies_each_step_and_stops_in_reverse_on_failure():
    items = {"v1": view("v1", "valve", {"open": "digital_A.DO3", "is_open": "digital_A.DI4"}),
             "pump": view("pump", "pump", {"start": "digital_A.DO2", "running": "digital_A.DI2"},
                          {"sim_links": {"flow_item": "fm"}}),
             "fm": view("fm", "flow", {"flow": "analog_A.AI1"}, {"scaling": FLOW_SCALING})}
    steps = [{"do": "ON", "item": "v1", "verify_role": "is_open", "timeout_s": 10},
             {"do": "ON", "item": "pump", "verify_role": "running", "timeout_s": 5},
             {"do": "DELIVER", "meter": "fm", "target_L": 3},
             {"do": "OFF", "item": "pump", "verify_role": "running", "timeout_s": 5},
             {"do": "OFF", "item": "v1", "timeout_s": 10}]
    p = prog({"type": "MANUAL"}, {"type": "SEQUENCE", "steps": steps, "max_runtime_s": 300})
    r = Rig(items, {"p": (1, p)})
    r.e.approve_recovery("vicente", "MANUAL")
    r.e.run_now("bench", "p")
    r.run(60)
    f = r.e.finished[-1]
    assert f["result"] == "DONE" and f["measured_L"] >= 3
    order = [(i.command.target, i.command.desired) for i in r.audit.intents]
    assert order[:2] == [("digital_A.DO3", True), ("digital_A.DO2", True)]
    assert order.index(("digital_A.DO2", False)) < order.index(("digital_A.DO3", False))
    # the valve never opens: the pump is never started and the valve is closed again
    r.twin.set_fault("v1", "NO_FEEDBACK")
    n = len(r.audit.intents)
    r.e.run_now("bench", "p")
    r.run(20)
    new = [(i.command.target, i.command.desired) for i in r.audit.intents[n:]]
    assert ("digital_A.DO2", True) not in new and new[-1] == ("digital_A.DO3", False)
    assert "is_open did not report ON" in r.e.finished[-1]["reason"]


def test_conflict_holds_the_second_program_until_a_choice_and_expires_with_its_window():
    a = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:01", "end": "05:20"}]}, {"type": "RUN_FOR", "item": "led1"})
    b = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:05", "end": "05:08"}]}, {"type": "RUN_FOR", "item": "led1"})
    r = Rig({"led1": lamp()}, {"a": (1, a), "b": (1, b)})
    r.e.approve_recovery("vicente", "AUTO")
    r.run(6 * 60)
    assert len(r.e.conflicts) == 1 and r.alarms[("PROGRAM_CONFLICT", "led1")]
    c = next(iter(r.e.conflicts.values()))
    assert c["running"]["program_id"] == "a" and c["waiting"]["program_id"] == "b"
    r.run(3 * 60)                                            # nobody chose: b's window ended
    assert not r.e.conflicts and r.store.occ["b@1:2026-11-02:0"]["state"] == "MISSED"
    assert len(r.on_frames()) == 1                           # b never started


def test_conflict_switch_stops_the_first_then_starts_the_second():
    a = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:01", "end": "05:20"}]}, {"type": "RUN_FOR", "item": "led1"})
    b = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:05", "end": "05:08"}]}, {"type": "RUN_FOR", "item": "led1"})
    r = Rig({"led1": lamp()}, {"a": (1, a), "b": (1, b)})
    r.e.approve_recovery("vicente", "AUTO")
    r.run(5 * 60 + 10)
    cid = next(iter(r.e.conflicts))
    r.e.resolve_conflict("bench", cid, "SWITCH")
    r.run(5)
    assert [o.program_id for o in r.e.ops.values()] == ["b"]
    order = [(i.command.target, i.command.desired) for i in r.audit.intents]
    assert order[-2:] == [("digital_A.DO1", False), ("digital_A.DO1", True)]


def test_restart_abandons_running_operations():
    p = prog({"type": "WEEKLY", "entries": [{"days": ["MON"], "start": "05:01", "end": "05:30"}]}, {"type": "RUN_FOR", "item": "led1"})
    r = Rig({"led1": lamp()}, {"p": (1, p)})
    r.e.approve_recovery("vicente", "AUTO")
    r.run(90)
    assert r.store.occ["p@1:2026-11-02:0"]["state"] == "RUNNING"
    r.e.start()                                              # a restart (the twin keeps its coils)
    assert r.store.occ["p@1:2026-11-02:0"]["state"] == "INTERRUPTED" and r.e.mode == "RECOVERY"


def test_not_modelled_equipment_has_no_value():
    custom = view("x", "pump", {"start": "digital_A.DO4", "running": "digital_A.DI5"},
                  simulation={"behavior": "NOT_MODELLED"})
    r = Rig({"x": custom}, {})
    s = r.twin.samples()["digital_A.DI5"]
    assert s.value is None and "no simulated behaviour" in s.reason
    r.run(0.5)
    assert r.e.item_state("x")["feedback"] == "UNKNOWN"
