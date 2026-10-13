"""The shared drive's Modbus interface on the SIMULATED installation twin (ADR-0015).

The twin's drive answers a DEMO register map; none of this verifies a real drive or a real map.
"""
import pytest

from aquacrop.config import load_config
from aquacrop.twin.runtime import make_runtime, replay
from tests.conftest import ROOT


@pytest.fixture(scope="module")
def icfg():
    return load_config(ROOT / "configs" / "installation-48.yaml")


def test_status_word_is_read_and_shown(icfg):
    rt = make_runtime(icfg)
    rt.run_for(5)
    v = rt.drive_view("MAIN")
    assert v["ok"] is True and v["status"]["running"] is False and v["status"]["fault"] is False
    assert v["interface"]["provenance"] == "DEMO" and v["interface"]["writes"] == 0   # polling never writes
    main = next(a for a in rt.snapshot()["drive_adapters"] if a["adapter_id"] == "MAIN")
    assert main["kind"] == "MODBUS_RTU" and main["modbus"]["status"]["status_word"] == v["status"]["status_word"]


def test_status_mismatch_with_the_hardwired_input_is_alarmed(icfg):
    rt = make_runtime(icfg)
    rt.run_for(5)
    rt.set_override("t", "relay_A.DI1", True, "test: running input stuck on")
    rt.run_for(9)
    assert not rt.alarms.is_active("DRIVE_STATUS_MISMATCH", "MAIN.running")
    rt.run_for(3)
    assert rt.alarms.is_active("DRIVE_STATUS_MISMATCH", "MAIN.running")
    rt.clear_override("t", "relay_A.DI1")
    rt.run_for(3)
    assert not rt.alarms.is_active("DRIVE_STATUS_MISMATCH", "MAIN.running")


def test_comm_loss_while_idle_alarms_and_recovers(icfg):
    rt = make_runtime(icfg)
    rt.run_for(5)
    rt.set_actuator_fault("t", "drive_comm", "MAIN", "NO_RESPONSE")
    rt.run_for(10)
    assert rt.alarms.is_active("DRIVE_COMM", "MAIN") and rt.drive_view("MAIN")["ok"] is False
    assert rt.plant.drive.tripped is None          # the drive's comm-loss timer acts only while it runs
    rt.set_actuator_fault("t", "drive_comm", "MAIN", None)
    rt.run_for(5)
    assert not rt.alarms.is_active("DRIVE_COMM", "MAIN") and rt.drive_view("MAIN")["ok"] is True
    kinds = [e["kind"] for e in rt.events]
    assert "DRIVE_STATUS_UNREADABLE" in kinds and "DRIVE_STATUS_RESTORED" in kinds


def test_unknown_drive_comm_fault_is_refused(icfg):
    rt = make_runtime(icfg)
    with pytest.raises(ValueError):
        rt.set_actuator_fault("t", "drive_comm", "MAIN", "SMOKE")
    with pytest.raises(ValueError):
        rt.set_actuator_fault("t", "drive_comm", "OTHER", "NO_RESPONSE")


def test_replay_with_a_drive_comm_fault_reproduces_state(icfg):
    def run():
        rt = make_runtime(icfg, seed=11)
        rt.schedule(0, "PROCESS_REQUEST", "t", {"process": "IRRIGATION", "target": "AUTO"})
        rt.schedule(40, "ACTUATOR_FAULT", "t", {"kind": "drive_comm", "ident": "MAIN", "fault": "NO_RESPONSE"})
        rt.schedule(70, "ACTUATOR_FAULT", "t", {"kind": "drive_comm", "ident": "MAIN", "fault": None})
        rt.run_for(100)
        return rt
    a, b = run(), run()
    assert a.state_hash() == b.state_hash()
    r = replay(icfg, a.seed, a.journal, a.tick, **a.replay_kwargs())
    assert r.state_hash() == a.state_hash()
