"""The drive's Modbus interface (ADR-0015) over the in-memory serial bus: a register-level test double of a
drive, not a drive. These tests prove what the software sends and refuses; they verify no real register map."""
import json
import time
from datetime import timedelta

import pytest
import yaml
from fastapi.testclient import TestClient

from aquacrop import cli
from aquacrop.api.app import create_app
from aquacrop.config.schema import InstallationConfig
from aquacrop.control.bench import CONFIRMATION_PHRASE
from aquacrop.domain.model import CommandOrigin, OutputCommand, Outcome, utcnow
from aquacrop.drivers.rtu import RtuBus, RtuExceptionResponse, RtuInvalidResponse, RtuRefused
from aquacrop.drivers.vfd import VfdCommandError, VfdDevice
from aquacrop.io.modbus_backend import ModbusBackend
from aquacrop.io.sampling import AcqContext
from aquacrop.security.auth import Role

ORIGIN = "http://127.0.0.1:8000"
PW = {"bench1": "bench password 123", "oper1": "operator password 1"}
READY, RUNNING, FAULT = 1, 4, 8
MAP = {"bus_id": "rs485_a", "address": 9, "drive_model": "test double", "manual_reference": "tests/fakes",
       "control_register": 100, "run_sequence": [1, 3], "stop_value": 0, "status_register": 101,
       "running_mask": RUNNING, "fault_mask": FAULT, "ready_mask": READY,
       "frequency": {"address": 102, "scale": 0.1, "unit": "Hz"},
       "current": {"address": 103, "scale": 0.1, "unit": "A"},
       "fault_code": {"address": 104, "signed": True},
       "comm_loss_timeout_s": 5, "comm_loss_action": "stop and trip (test double)", "provenance": "PROPOSED"}


def drive_cfg(cfg, **map_kw) -> InstallationConfig:
    data = cfg.model_dump(mode="json") | {"drive_adapters": [
        {"adapter_id": "MAIN", "kind": "MODBUS_RTU", "signals": ["drive.MAIN.run"], "modbus": MAP | map_kw}]}
    return InstallationConfig.model_validate(data)


def add_vfd(bus, status=READY):
    v = bus.add("vfd", 9)
    v.holding = {100: 0, 101: status, 102: 0, 103: 0, 104: 0}
    v.writable = {100}

    def on_write(a, val):        # 1 then 3 starts; 0 stops (the test double's own convention)
        if val == 3 and v.holding[101] & READY:
            v.holding[101] |= RUNNING
            v.holding[102] = 500
        elif val == 0:
            v.holding[101] &= ~RUNNING
            v.holding[102] = 0
    v.on_write = on_write
    return v


@pytest.fixture
def drv(fake_bus):
    cfg, bus, relay, ai = fake_bus
    return drive_cfg(cfg), bus, add_vfd(bus), relay


# ------------------------------------------------------------------ bus and device
def test_fc06_frame_echo_and_allowlist(drv):
    cfg, bus, v, _ = drv
    rb = RtuBus(cfg.buses[0], register_writes=frozenset({(9, 100)}))
    try:
        rb.write_single_register(9, 100, 1)
        assert bus.write_frames()[-1][:6] == bytes([9, 0x06, 0, 100, 0, 1]) and v.holding[100] == 1
        for unit, reg in ((9, 101), (1, 100), (9, 0)):
            with pytest.raises(RtuRefused):
                rb.write_single_register(unit, reg, 1)
        with pytest.raises(RtuRefused):
            rb.write_single_register(9, 100, 0x10000)
        assert len(bus.write_frames()) == 1          # refusals send nothing
        v.fault = "readback_mismatch"
        with pytest.raises(RtuInvalidResponse, match="echo mismatch"):
            rb.write_single_register(9, 100, 3)
        v.fault = "exception"
        with pytest.raises(RtuExceptionResponse):
            rb.write_single_register(9, 100, 3)
        assert len(bus.write_frames()) == 3          # one frame each, never retried
    finally:
        rb.close()


def test_status_decode_signed_diagnostics_and_failing_diagnostic(drv):
    cfg, bus, v, _ = drv
    rb = RtuBus(cfg.buses[0])
    try:
        dv = VfdDevice(rb, cfg.drive_adapters[0].modbus)
        v.holding |= {101: READY | FAULT, 102: 498, 103: 37, 104: 0xFFFE}
        st = dv.read_status()
        assert (st.running, st.fault, st.ready) == (False, True, True)
        assert st.diagnostics["frequency"] == pytest.approx(49.8) and st.diagnostics["fault_code"] == -2
        assert st.view()["status_word"] == "0x0009"
        del v.holding[103]                           # a diagnostic the drive does not answer
        st = dv.read_status()
        assert st.fault is True and st.diagnostics["current"] is None and "current" in st.diagnostic_errors
        assert bus.write_frames() == []
    finally:
        rb.close()


def test_command_sequence_and_failure_midway(drv):
    cfg, bus, v, _ = drv
    rb = RtuBus(cfg.buses[0], register_writes=frozenset({(9, 100)}))
    try:
        dv = VfdDevice(rb, cfg.drive_adapters[0].modbus)
        assert dv.command(True) == 2 and v.holding[101] & RUNNING
        assert [f[4:6] for f in bus.write_frames()] == [b"\x00\x01", b"\x00\x03"]
        assert dv.command(False) == 1 and not v.holding[101] & RUNNING
        v.on_write = lambda a, val: setattr(v, "fault", "timeout")     # echoes the first value, then goes silent
        with pytest.raises(VfdCommandError) as e:
            dv.command(True)
        assert (e.value.sent, e.value.total) == (1, 2) and e.value.cause.kind == "NO_RESPONSE"
        assert len(bus.write_frames()) == 5              # the second value was sent once, not retried
    finally:
        rb.close()


# ------------------------------------------------------------------ backend
def run_cmd(cfg, desired=True):
    now = utcnow()
    return OutputCommand("req-1", "t", "drive:MAIN", desired, cfg.config_version, None, now,
                         now + timedelta(seconds=30), CommandOrigin.BENCH, "test")


def test_backend_writes_nothing_without_a_lease_and_refuses_a_faulted_run(drv):
    cfg, bus, v, _ = drv
    be = ModbusBackend(cfg, context=lambda: AcqContext(session_id="s-test", cycle_id="c1"))
    try:
        st, err = be.read_drive("drive:MAIN")
        assert err is None and st.ready and not st.running
        cmd = run_cmd(cfg)
        assert be.write_output(cmd).outcome == Outcome.REJECTED
        be.authorize = lambda c: None
        v.holding[101] = READY | FAULT
        r = be.write_output(cmd)
        assert r.outcome == Outcome.REJECTED and "fault" in r.detail
        v.holding[101] = 0
        r = be.write_output(cmd)
        assert r.outcome == Outcome.REJECTED and "not ready" in r.detail
        assert bus.write_frames() == []
        v.holding[101] = READY | FAULT                   # a stop is still allowed while tripped
        r = be.write_output(run_cmd(cfg, False))
        assert r.outcome == Outcome.ECHO_CONFIRMED and len(bus.write_frames()) == 1
        v.holding[101] = READY
        r = be.write_output(cmd)
        assert r.outcome == Outcome.ECHO_CONFIRMED and r.readback is True and "not motor proof" in r.detail
        v.fault = "exception"
        assert be.write_output(cmd).outcome == Outcome.REJECTED       # preflight read refused
    finally:
        be.close()


def test_backend_maps_write_failures(drv):
    cfg, bus, v, _ = drv
    be = ModbusBackend(cfg, context=lambda: AcqContext(session_id="s-test", cycle_id="c1"))
    be.authorize = lambda c: None
    cmd = run_cmd(cfg)
    try:
        v.writable = set()                               # the drive answers FC06 with an exception
        assert be.write_output(cmd).outcome == Outcome.FAILED
        v.writable = {100}
        v.fault = "apply_then_timeout"                   # applied, never echoed
        r = be.write_output(cmd)
        assert r.outcome == Outcome.OUTCOME_UNKNOWN and "may have applied" in r.detail and v.holding[100] == 1
        v.fault = None
        v.on_write = lambda a, val: setattr(v, "fault", "timeout")
        r = be.write_output(cmd)                         # first value echoed, second unanswered
        assert r.outcome == Outcome.OUTCOME_UNKNOWN and r.detail.startswith("1 of 2")
        v.fault = "timeout"
        assert be.write_output(cmd).outcome == Outcome.REJECTED      # preflight read unanswered: nothing sent
        assert len(bus.write_frames()) == 4
    finally:
        be.close()


# ------------------------------------------------------------------ service and API
@pytest.fixture
def hwd(tmp_path, drv):
    cfg, bus, v, relay = drv
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    app.state.accounts.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    app.state.accounts.create_user("oper1", PW["oper1"], Role.OPERATOR)
    with TestClient(app, base_url=ORIGIN) as c:
        deadline = time.monotonic() + 5
        while app.state.svc.workers["rs485_a"].polls < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        yield app, c, bus, v


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


def arm(c, h, allowlist=("drive:MAIN",)):
    return c.post("/api/bench/lease", json={"confirmation": CONFIRMATION_PHRASE, "allowlist": list(allowlist),
                                             "setup_description": "drive on the bench, motor disconnected",
                                             "duration_s": 300}, headers=h)


def wait_polls(app, n=2):
    w = app.state.svc.workers["rs485_a"]
    target, deadline = w.polls + n, time.monotonic() + 5
    while w.polls < target and time.monotonic() < deadline:
        time.sleep(0.05)


def test_polling_reads_the_drive_and_records_status_edges(hwd):
    app, c, bus, v = hwd
    login(c, "oper1")
    st = c.get("/api/status").json()
    d = st["drives"]["drive:MAIN"]
    assert d["provenance"] == "PROPOSED" and d["online"] is True and d["status"]["ready"] is True
    v.holding[101] = READY | RUNNING
    wait_polls(app)
    v.fault = "timeout"
    wait_polls(app)
    v.fault = None
    wait_polls(app)
    hist = app.state.hist
    hist.wait_idle()
    ev = [(k, json.loads(p)) for k, p in hist.query("SELECT kind, payload_json FROM events WHERE kind IN "
                                                   "('DRIVE_STATUS_EDGE', 'OBSERVATION_GAP') ORDER BY id")]
    assert ("DRIVE_STATUS_EDGE", "running", True) in [(k, p.get("what"), p.get("to")) for k, p in ev]
    assert any(k == "OBSERVATION_GAP" and p["point"] == "drive:MAIN" for k, p in ev)
    assert bus.write_frames() == []                      # polling never writes


def test_bench_lease_runs_and_stops_the_drive(hwd):
    app, c, bus, v = hwd
    h = login(c, "bench1")
    b = c.get("/api/bench").json()
    assert b["drive_targets"] == ["drive:MAIN"] and "drive:MAIN" in b["drives"]
    cmd = {"backend": "REAL_BENCH", "config_version": "test-1", "target": "drive:MAIN", "desired": True}
    out = c.post("/api/bench/command", json=cmd | {"lease_id": "none", "request_id": "req-00000020"},
                 headers=h).json()
    assert out["outcome"] == "REJECTED" and bus.write_frames() == []
    v.holding[101] = READY | FAULT
    r = arm(c, h)
    assert r.status_code == 409 and "fault" in r.text and bus.write_frames() == []
    v.holding[101] = READY
    assert arm(c, h, ["drive:OTHER"]).status_code == 409
    lease = arm(c, h).json()
    out = c.post("/api/bench/command", json=cmd | {"lease_id": lease["lease_id"], "request_id": "req-00000021"},
                 headers=h).json()
    assert out["outcome"] == "ECHO_CONFIRMED" and out["readback"] is True and "not actuator proof" in out["note"]
    assert len(bus.write_frames()) == 2 and v.holding[101] & RUNNING
    out = c.post("/api/bench/command", json=cmd | {"lease_id": lease["lease_id"], "request_id": "req-00000022",
                                                   "desired": False}, headers=h).json()
    assert out["outcome"] == "ECHO_CONFIRMED" and out["readback"] is False and len(bus.write_frames()) == 3
    out = c.post("/api/bench/command", json=cmd | {"lease_id": lease["lease_id"], "request_id": "req-00000023",
                                                   "target": "relay_A.DO1"}, headers=h).json()
    assert out["outcome"] == "REJECTED" and "allowlist" in out["detail"] and len(bus.write_frames()) == 3
    c.post("/api/bench/lease/revoke", json={}, headers=h)
    assert len(bus.write_frames()) == 3                  # revocation sends nothing
    hist = app.state.hist
    hist.wait_idle()
    rows = [r[0] for r in hist.query("SELECT outcome FROM commands WHERE target='drive:MAIN' ORDER BY rowid")]
    assert rows.count("ECHO_CONFIRMED") == 2


# ------------------------------------------------------------------ diagnostics
def test_diagnostics_reads_the_drive_without_writing(drv, tmp_path, capsys):
    cfg, bus, v, _ = drv
    v.holding[101] = READY | FAULT
    p = tmp_path / "hw.yaml"
    p.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    rc = cli.main(["diagnostics", "read", "--config", str(p), "--state-dir", str(tmp_path), "--device", "drive:MAIN"])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 0
    rec = next(x for x in lines if x.get("device_uid") == "drive:MAIN")
    assert rec["register_map"] == "PROPOSED" and rec["status"]["fault"] is True and rec["control_register"] == 0
    assert lines[-1]["summary"]["writes_sent"] == 0 and bus.write_frames() == []


@pytest.mark.parametrize('boundary', ['expiry_in_preflight', 'revoke_after_first_frame'])
def test_drive_lease_is_rechecked_at_every_write_frame(drv, boundary):
    from aquacrop.control.bench import BenchGate
    from aquacrop.control.commands import CommandService, MemoryAudit
    from aquacrop.domain.model import ControllerState
    from tests.hardware.test_modbus import Clock, principal
    cfg, bus, v, _ = drv
    clock = Clock()
    be = ModbusBackend(cfg, context=lambda: AcqContext('test', 'cycle'))
    try:
        gate = BenchGate(cfg, 'test', lambda l: None, lambda l: None, lambda devices: None,
                         lambda *a: None, clock)
        lease = gate.arm(principal(), confirmation=CONFIRMATION_PHRASE,
                         setup_description='isolated simulated drive, no physical serial device or motor',
                         allowlist=['drive:MAIN'], duration_s=60)
        be.authorize = gate.authorize
        cs = CommandService(cfg, be, MemoryAudit(), gate.authorize, lambda: ControllerState.STOPPED)
        if boundary == 'expiry_in_preflight':
            read = be.drives['drive:MAIN'].read_status
            def expire():
                result = read()
                clock.t = lease.expires_monotonic + 0.01
                return result
            be.drives['drive:MAIN'].read_status = expire
        else:
            apply = v.on_write
            def revoke(address, value):
                apply(address, value)
                gate.revoke('test: revoke after first echoed value', 'bench1')
            v.on_write = revoke
        record = cs.submit(cs.build(actor='bench1', target='drive:MAIN', desired=True,
                                   origin=CommandOrigin.BENCH, reason=boundary, lease_id=lease.lease_id))
        if boundary == 'expiry_in_preflight':
            assert record.outcome == Outcome.REJECTED and bus.write_frames() == []
        else:
            assert record.outcome == Outcome.OUTCOME_UNKNOWN and len(bus.write_frames()) == 1
            assert "1 of 2 control value(s) echoed" in record.detail      # the partial sequence is audited
            assert v.holding[100] == 1 and not (v.holding[101] & RUNNING)
        assert gate.active() is None
    finally:
        be.close()
