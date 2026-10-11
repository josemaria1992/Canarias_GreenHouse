"""AT-IO-01/02, AT-TWN-02..04, AT-CTL-01..03, AT-ALM-01, AT-SCN-01."""
import sys
import subprocess

import pytest

from aquacrop.domain.model import ControllerState, Outcome, Quality, SignalSource
from aquacrop.twin.runtime import SimulationRuntime, replay
from aquacrop.twin.scenarios import presets, run_scenario


def test_io_test_every_point_individually(cfg48):
    rt = SimulationRuntime(cfg48)
    assert rt.set_source("tester", SignalSource.IO_TEST)[0]
    assert rt.request_state("op", ControllerState.MANUAL)[0]
    for p in cfg48.all_points():
        pid = str(p)
        if p.kind.value == "DI":
            rt.inject("tester", pid, True)
            assert rt.samples[pid].value is True
        elif p.kind.value == "AI":
            rt.inject("tester", pid, 12000)
            assert rt.samples[pid].raw_value == 12000
        else:
            rec = rt.operator_command("tester", pid, True)
            assert rec.outcome == Outcome.READBACK_CONFIRMED
    # IO_TEST never drives the plant
    assert not any(a.cmd for a in rt.plant.pumps.values())
    s = rt.samples["analog_A.AI1"]
    assert s.value == pytest.approx(500.0) and s.unit == "L" and s.calibration_ref == "demo_level_reservoir@1"
    assert s.origin.value == "SIMULATED" and s.config_version == cfg48.config_version
    # unscaled channel on analog_B: raw kept, no derived value
    assert rt.samples["analog_B.AI1"].quality == Quality.UNSCALED and rt.samples["analog_B.AI1"].value is None


def test_injection_refused_in_twin_mode(cfg24):
    rt = SimulationRuntime(cfg24)
    with pytest.raises(PermissionError):
        rt.inject("x", "relay_A.DI1", True)


def test_unmapped_points_visible_unknown_in_twin(cfg48):
    rt = SimulationRuntime(cfg48)
    s = rt.samples["relay_B.DI3"]
    assert s.quality == Quality.UNAVAILABLE and s.reason == "UNMAPPED"


def test_manual_drives_same_plant(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.request_state("op", ControllerState.MANUAL)
    assert rt.operator_command("op", "relay_A.DO4", True).outcome == Outcome.READBACK_CONFIRMED
    assert rt.operator_command("op", "relay_A.DO1", True).outcome == Outcome.READBACK_CONFIRMED
    rt.run_for(10)
    assert rt.plant.flow_actual["S1"] > 11 and rt.samples["analog_A.AI2"].value > 11
    assert rt.samples["relay_A.DI1"].value is True


def test_on_command_rejected_when_stopped_and_audited(cfg24):
    rt = SimulationRuntime(cfg24)
    rec = rt.operator_command("op", "relay_A.DO1", True)
    assert rec.outcome == Outcome.REJECTED
    assert rt.audit.intents[-1].command.request_id == rec.command.request_id
    assert rt.audit.outcomes[-1] == (rec.command.request_id, Outcome.REJECTED)


def test_audit_intent_before_side_effect_and_on_refused_without_audit(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.request_state("op", ControllerState.MANUAL)
    rt.audit.fail_intent = True
    rec = rt.operator_command("op", "relay_A.DO1", True)
    assert rec.outcome == Outcome.REJECTED and "durable audit" in rec.detail
    assert rt.backend.state.coils["relay_A.DO1"] is False
    assert rt.operator_command("op", "relay_A.DO1", False).outcome == Outcome.READBACK_CONFIRMED


def test_outcome_persist_failure_reports_unknown(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.request_state("op", ControllerState.MANUAL)
    rt.audit.fail_outcome = True
    rec = rt.operator_command("op", "relay_A.DO1", True)
    assert rec.outcome == Outcome.OUTCOME_UNKNOWN


def test_write_during_module_timeout_is_outcome_unknown(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.request_state("op", ControllerState.MANUAL)
    rt.set_device_fault("scn", "relay_A", "TIMEOUT")
    assert rt.operator_command("op", "relay_A.DO1", True).outcome == Outcome.OUTCOME_UNKNOWN


def test_single_module_failure_isolated(cfg48):
    rt = SimulationRuntime(cfg48)
    rt.set_device_fault("scn", "analog_B", "TIMEOUT")
    for p, s in rt.samples.items():
        if s.device_uid == "analog_B":
            assert s.quality == Quality.UNAVAILABLE and s.value is None
        elif p.startswith("analog_A"):
            assert s.quality != Quality.UNAVAILABLE or s.reason == "UNMAPPED"
    assert rt.samples["analog_A.AI1"].quality == Quality.GOOD


def test_auto_needs_stopped_and_outputs_off(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.request_state("op", ControllerState.MANUAL)
    rt.operator_command("op", "relay_A.DO1", True)
    ok, msg = rt.request_state("op", ControllerState.AUTO)
    assert not ok and "STOPPED" in msg
    rt.request_state("op", ControllerState.STOPPED)
    rt.run_for(2)
    assert rt.backend.state.coils["relay_A.DO1"] is False  # stop sequence switched it off
    assert rt.request_state("op", ControllerState.AUTO)[0]


def test_auto_rejected_in_io_test(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.set_source("op", SignalSource.IO_TEST)
    ok, msg = rt.request_state("op", ControllerState.AUTO)
    assert not ok and "TWIN" in msg


def test_alarm_value_change_updates_same_occurrence(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.set_override("op", "analog_A.AI1", 19800, "test")
    rt.step()
    occ = rt.alarms.open[("RESERVOIR_LEVEL_HIGH", "analog_A.AI1")]
    for raw in (19900, 19700, 19950):
        rt.set_override("op", "analog_A.AI1", raw, "test")
        rt.step()
    assert rt.alarms.open[("RESERVOIR_LEVEL_HIGH", "analog_A.AI1")].occurrence_id == occ.occurrence_id
    raised = [e for e in rt.events if e["kind"] == "ALARM_RAISED" and e["payload"]["rule_id"] == "RESERVOIR_LEVEL_HIGH"]
    assert len(raised) == 1


def test_alarm_ack_is_not_clear_and_hysteresis(cfg24):
    rt = SimulationRuntime(cfg24)
    rt.set_override("op", "analog_A.AI1", 19800, "test")  # ~987 L
    rt.step()
    occ = rt.acknowledge("op", "RESERVOIR_LEVEL_HIGH", "analog_A.AI1")
    assert occ.acknowledged and occ.active
    rt.set_override("op", "analog_A.AI1", 4000 + 16 * 945, "inside hysteresis band")  # 945 L > 950-10
    rt.step()
    assert occ.active
    rt.clear_override("op", "analog_A.AI1")
    rt.step()
    assert not occ.active and occ.closed  # cleared + acknowledged, not latched => closed


def test_latched_alarm_needs_reset(cfg24):
    from aquacrop.control.alarms import AlarmEngine
    eng = AlarmEngine(cfg24)
    o = eng.raise_or_update("X", "t", severity="CRITICAL", message="m", sim_s=0, latch=True)
    eng.clear("X", "t")
    eng.acknowledge(o.occurrence_id, "u")
    assert not o.closed and o.reset_required
    eng.reset(o.occurrence_id, "u")
    assert o.closed


def test_alarm_restore_no_duplicates(cfg24):
    from aquacrop.control.alarms import AlarmEngine
    eng = AlarmEngine(cfg24)
    o = eng.raise_or_update("X", "t", severity="WARNING", message="m", sim_s=0)
    eng2 = AlarmEngine(cfg24)
    eng2.restore([o])
    o2 = eng2.raise_or_update("X", "t", severity="WARNING", message="m", sim_s=1)
    assert o2.occurrence_id == o.occurrence_id and len(eng2.open) == 1


def test_determinism_and_replay(cfg48):
    def scripted():
        rt = SimulationRuntime(cfg48)
        rt.schedule(0, "STATE_REQUEST", "op", {"target": "AUTO"})
        rt.schedule(7.3, "OVERRIDE_SET", "op", {"point": "analog_A.AI4", "raw": 9000, "reason": "t", "duration_s": 2})
        rt.schedule(40, "STATE_REQUEST", "op", {"target": "STOPPED"})
        rt.run_for(60)
        return rt
    a, b = scripted(), scripted()
    assert a.state_hash() == b.state_hash()
    r = replay(cfg48, a.seed, a.journal, a.tick)
    assert r.state_hash() == a.state_hash()


def test_pause_step_semantics(cfg24):
    from aquacrop.twin.clock import SimClock
    rt = SimulationRuntime(cfg24)
    clk = SimClock(rt)
    clk.pause()
    assert clk.advance_wall(5.0) == 0 and rt.tick == 0
    clk.step_once()
    assert rt.tick == 1
    clk.resume()
    clk.set_speed(10)
    assert clk.advance_wall(1.0) == 100 and rt.tick == 101


def test_speed_does_not_change_results(cfg24):
    from aquacrop.twin.clock import SimClock
    hashes = []
    for speed in (1, 10, 60):
        rt = SimulationRuntime(cfg24)
        rt.request_state("op", ControllerState.AUTO)
        clk = SimClock(rt)
        clk.set_speed(speed)
        while rt.tick < 600:
            clk.advance_wall(min(0.05, (600 - rt.tick) * rt.params.dt_s / speed))
        hashes.append(rt.state_hash())
    assert len(set(hashes)) == 1


@pytest.mark.parametrize("name", list(presets().keys()))
def test_scenario_presets(cfg48, name):
    rt, errs = run_scenario(cfg48, presets()[name])
    assert errs == []
    assert not rt.invariant_failures


def test_conflicting_manual_rejected_and_audited(cfg48):
    rt, _ = run_scenario(cfg48, presets()["conflicting_manual_during_auto"])
    rej = [r for r in rt.audit.intents if r.command.target == "relay_A.DO2"]
    assert rej and rej[0].outcome == Outcome.REJECTED and "AUTO" in rej[0].detail


def test_recovery_requires_ack_before_reset(cfg48):
    rt, _ = run_scenario(cfg48, presets()["recovery_ack_reset"])
    rejected = [e for e in rt.events if e["kind"] == "RESET_REJECTED"]
    assert rejected and "acknowledge" in rejected[0]["payload"]["reason"]


def test_simulation_never_imports_serial(tmp_path):
    code = ("import sys; sys.path.insert(0,'src');"
            "from aquacrop.config import load_config;"
            "from aquacrop.twin.runtime import SimulationRuntime;"
            "from aquacrop.domain.model import ControllerState;"
            "rt=SimulationRuntime(load_config('configs/demo-48.yaml'));"
            "rt.request_state('op', ControllerState.AUTO); rt.run_for(30);"
            "bad=[m for m in sys.modules if m.split('.')[0] in ('serial','pymodbus')];"
            "print(bad); assert not bad")
    from tests.conftest import ROOT
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
