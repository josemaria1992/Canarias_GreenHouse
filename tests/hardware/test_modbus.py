"""AT-MB-01..07 against the in-memory serial bus (tests/fakes). The real pymodbus 3.11.3 client,
RTU framer and CRC run unmodified; only the wire is simulated. This is not hardware evidence."""
from __future__ import annotations

import struct

import pytest
from pymodbus.framer import FramerRTU
from pymodbus.pdu import DecodePDU
from pymodbus.pdu.bit_message import ReadCoilsRequest, ReadDiscreteInputsRequest, WriteSingleCoilRequest
from pymodbus.pdu.register_message import ReadHoldingRegistersRequest, ReadInputRegistersRequest

from aquacrop.control.bench import CONFIRMATION_PHRASE, BenchGate, BenchRefused
from aquacrop.domain.model import CommandOrigin, Origin, Outcome, Quality
from aquacrop.drivers.rtu import (FC_READ_HOLDING_REGISTERS, RtuBus, RtuExceptionResponse, RtuInvalidResponse,
                                  RtuNoResponse, RtuRefused)
from aquacrop.drivers.waveshare import WaveshareDevice
from aquacrop.io.modbus_backend import ModbusBackend
from aquacrop.io.sampling import AcqContext
from aquacrop.security.auth import Principal, Role


def ctx():
    return AcqContext(session_id="s-test", cycle_id="c1")


def principal(commissioning=True, session="h1", user="bench1", role=Role.OPERATOR):
    return Principal(user, role, commissioning, session, False)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# --------------------------------------------------------------------------- AT-MB-01
@pytest.mark.parametrize("req,expected", [
    (ReadCoilsRequest(address=0, count=8, dev_id=1), "01 01 00 00 00 08 3D CC"),
    (ReadDiscreteInputsRequest(address=0, count=8, dev_id=1), "01 02 00 00 00 08 79 CC"),
    (ReadHoldingRegistersRequest(address=0x1000, count=8, dev_id=1), "01 03 10 00 00 08 40 CC"),
    (ReadInputRegistersRequest(address=0, count=8, dev_id=1), "01 04 00 00 00 08 F1 CC"),
    (WriteSingleCoilRequest(address=0, bits=[True], dev_id=1), "01 05 00 00 FF 00 8C 3A"),
])
def test_at_mb_01_request_vectors_byte_exact(req, expected):
    frame = FramerRTU(DecodePDU(False)).buildFrame(req)
    assert frame.hex(" ").upper() == expected


def test_at_mb_01_response_vectors_decode():
    fr = FramerRTU(DecodePDU(False))
    _, pdu = fr.handleFrame(bytes.fromhex("0102014161B8"), 1, 0)
    assert pdu.function_code == 2 and pdu.bits[:8] == [True, False, False, False, False, False, True, False]
    _, pdu = fr.handleFrame(bytes.fromhex("010101005188"), 1, 0)
    assert pdu.function_code == 1 and not any(pdu.bits)
    _, pdu = fr.handleFrame(bytes.fromhex("0102014161B9"), 1, 0)  # corrupted CRC
    assert pdu is None


# --------------------------------------------------------------------------- AT-MB-02
def test_at_mb_02_reads_through_real_client(fake_bus):
    cfg, bus, relay, ai = fake_bus
    relay.di = [True, False, False, False, False, False, True, False]
    relay.coils[2] = True
    ai.ai = [12000, 0, 0, 0, 0, 0, 0, 0]
    ai.modes = [3, 0, 0, 0, 0, 0, 0, 0]
    rb = RtuBus(cfg.buses[0])
    r, a = WaveshareDevice(rb, cfg.devices[0].model, 1), WaveshareDevice(rb, cfg.devices[1].model, 2)
    assert r.read_discrete_inputs() == relay.di
    assert r.read_coils()[2] is True
    assert r.read_relay_modes().all_normal
    assert a.read_analog()[0] == 12000
    assert a.read_ai_modes()[0].value == "3"
    assert bus.write_frames() == []
    rb.close()


def test_at_mb_02_backend_samples_real_origin_and_scaling(fake_bus):
    cfg, bus, relay, ai = fake_bus
    ai.ai[0], ai.modes[0] = 12000, 3
    be = ModbusBackend(cfg, context=ctx)
    res = {s.point_id: s for s in be.read_inputs("analog_A").samples}
    s1 = res["analog_A.AI1"]
    assert s1.origin == Origin.REAL and s1.quality == Quality.GOOD and s1.value == pytest.approx(50.0)
    assert s1.calibration_ref == "test_level@1"
    # channel 2 is configured UNKNOWN: raw kept, no engineering value
    assert res["analog_A.AI2"].quality == Quality.UNSCALED and res["analog_A.AI2"].value is None
    be.close()


def test_at_mb_02_observed_mode_mismatch_is_unscaled(fake_bus):
    cfg, bus, relay, ai = fake_bus
    ai.ai[0], ai.modes[0] = 2500, 0       # module says 0-5 V, config says 4-20 mA
    be = ModbusBackend(cfg, context=ctx)
    s1 = {s.point_id: s for s in be.read_inputs("analog_A").samples}["analog_A.AI1"]
    assert s1.quality == Quality.UNSCALED and s1.value is None and "observed mode" in s1.reason
    be.close()


# --------------------------------------------------------------------------- AT-MB-03
@pytest.mark.parametrize("fault,exc", [
    ("timeout", RtuNoResponse), ("bad_crc", RtuNoResponse), ("short", RtuNoResponse),
    ("wrong_unit", RtuNoResponse), ("exception", RtuExceptionResponse), ("wrong_fc", RtuInvalidResponse),
])
def test_at_mb_03_bad_replies_rejected_on_bit_reads(fake_bus, fault, exc):
    cfg, bus, relay, ai = fake_bus
    relay.fault = fault
    rb = RtuBus(cfg.buses[0])
    with pytest.raises(exc):
        WaveshareDevice(rb, cfg.devices[0].model, 1).read_discrete_inputs()
    rb.close()


@pytest.mark.parametrize("fault", ["short_modes", "empty_modes", "wrong_fc"])
def test_at_mb_03_short_or_empty_register_replies_rejected(fake_bus, fault):
    """pymodbus 3.11.3 itself accepts these; the adapter must not."""
    cfg, bus, relay, ai = fake_bus
    relay.fault = fault
    rb = RtuBus(cfg.buses[0])
    with pytest.raises(RtuInvalidResponse):
        WaveshareDevice(rb, cfg.devices[0].model, 1).read_relay_modes()
    rb.close()


def test_at_mb_03_read_retries_are_bounded(fake_bus):
    cfg, bus, relay, ai = fake_bus
    relay.fault = "timeout"
    rb = RtuBus(cfg.buses[0])           # read_retries: 1 -> exactly 2 frames
    with pytest.raises(RtuNoResponse):
        WaveshareDevice(rb, cfg.devices[0].model, 1).read_coils()
    assert len(bus.frames_in) == 2
    rb.close()


def test_at_mb_03_broadcast_and_out_of_range_units_never_sent(fake_bus):
    cfg, bus, relay, ai = fake_bus
    rb = RtuBus(cfg.buses[0])
    for unit in (0, 248, -1, True):
        with pytest.raises(RtuRefused):
            rb.read_bits(1, unit, 0, 8)
    with pytest.raises(RtuRefused):
        rb.read_registers(FC_READ_HOLDING_REGISTERS, 1, 0x1000, 9)
    assert bus.frames_in == []
    rb.close()


def test_at_mb_03_adapter_has_no_generic_write_surface():
    names = {n for n in dir(RtuBus) if not n.startswith("_")} | {n for n in dir(WaveshareDevice) if not n.startswith("_")}
    assert not {n for n in names if "register" in n and "write" in n}
    assert "write_coils" not in names and "write_registers" not in names
    assert {n for n in names if "write" in n} == {"write_single_coil", "write_coil"}


def test_pymodbus_version_pinned():
    import pymodbus
    assert pymodbus.__version__ == "3.11.3"


# --------------------------------------------------------------------------- AT-MB-05
def test_at_mb_05_read_only_start_poll_reconnect_shutdown_zero_writes(fake_bus):
    from aquacrop.api.hardware import HardwareService
    from aquacrop.historian.writer import Historian
    import tempfile, time
    cfg, bus, relay, ai = fake_bus
    with tempfile.TemporaryDirectory() as d:
        hist = Historian(f"{d}/h.sqlite3", cfg, "sess-1")
        svc = HardwareService(cfg, hist, "sess-1")
        svc.start()
        deadline = time.monotonic() + 5
        while svc.workers["rs485_a"].polls < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        relay.fault = "timeout"         # disconnect ...
        time.sleep(0.5)
        relay.fault = None              # ... and reconnect
        time.sleep(0.5)
        # A write attempt without a lease is rejected before any frame
        rec = svc.bench_command(principal(), target="relay_A.DO1", desired=True, reason="t", lease_id=None)
        assert rec.outcome == Outcome.REJECTED
        svc.stop()
        hist.close()
    assert svc.workers["rs485_a"].polls >= 3
    assert len(bus.frames_in) > 0
    assert bus.write_frames() == []
    assert relay.writes_applied == 0


def test_dl03_backup_restore_and_restore_drill_send_no_frame(fake_bus, tmp_path):
    """DL-03: backing up, the full restore drill (which starts the historian on the restored copy with
    the hardware configuration) and a restore to a new instance never open the bus: not one frame."""
    import time
    from aquacrop.api.hardware import HardwareService
    from aquacrop.historian.backup import create_backup, restore_backup, verify_backup
    from aquacrop.historian.writer import Historian
    cfg, bus, relay, ai = fake_bus
    hist = Historian(tmp_path / "h.sqlite3", cfg, "sess-1")
    svc = HardwareService(cfg, hist, "sess-1")
    svc.start()
    deadline = time.monotonic() + 5
    while svc.workers["rs485_a"].polls < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    svc.stop()
    hist.close()
    frames = len(bus.frames_in)
    assert frames > 0
    man = create_backup(tmp_path / "h.sqlite3", tmp_path / "backups", config_id=cfg.config_id,
                        config_version=cfg.config_version)
    v = verify_backup(man["path"], full=True, scratch_dir=tmp_path / "scratch", cfg=cfg)
    assert v["ok"] and v["full"]["smoke"]["historian_started"], v
    restore_backup(man["path"], tmp_path / "restored" / "h.sqlite3")
    assert len(bus.frames_in) == frames and bus.write_frames() == [] and relay.writes_applied == 0


def test_at_mb_05_backend_refuses_writes_without_gate(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    from aquacrop.control.commands import CommandService, MemoryAudit
    from aquacrop.domain.model import ControllerState
    cs = CommandService(cfg, be, MemoryAudit(), lambda c: None, lambda: ControllerState.STOPPED)
    rec = cs.submit(cs.build(actor="x", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH, reason="t"))
    assert rec.outcome == Outcome.REJECTED and bus.write_frames() == []
    be.close()


def test_at_mb_05_simulated_and_shadow_profiles_cannot_open_a_bus(cfg48):
    with pytest.raises(RtuRefused):
        ModbusBackend(cfg48, context=ctx)


# --------------------------------------------------------------------------- bench gate fixtures
def make_gate(cfg, be, clock, events=None, fail_persist=False):
    events = events if events is not None else []
    arms, ends = [], []

    def persist_arm(lease):
        if fail_persist:
            raise OSError("disk full")
        arms.append(lease)

    def preflight(devices):
        for uid in devices:
            m = be.devices[uid].read_relay_modes()
            if not m.all_normal:
                return f"{uid} not normal"
        return None
    gate = BenchGate(cfg, "proc-1", persist_arm, ends.append, preflight,
                     lambda k, p, a: events.append((k, p, a)), clock)
    be.authorize = gate.authorize
    return gate, arms, ends


def bench_cs(cfg, be, gate):
    from aquacrop.control.commands import CommandService, MemoryAudit
    from aquacrop.domain.model import ControllerState
    audit = MemoryAudit()
    return CommandService(cfg, be, audit, gate.authorize, lambda: ControllerState.STOPPED), audit


def arm(gate, **kw):
    args = dict(confirmation=CONFIRMATION_PHRASE, setup_description="isolated bench, lamps on DO1",
                allowlist=["relay_A.DO1"], duration_s=600)
    args.update(kw)
    return gate.arm(principal(), **args)


# --------------------------------------------------------------------------- AT-MB-04
@pytest.mark.parametrize("modes", [[0, 0, 0, 0, 0, 0, 0, 1], [2] * 8, [0, 0, 0, 3, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 9]])
def test_at_mb_04_non_normal_mode_blocks_arm(fake_bus, modes):
    cfg, bus, relay, ai = fake_bus
    relay.modes = modes
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock())
    with pytest.raises(BenchRefused, match="preflight"):
        arm(gate)
    assert bus.write_frames() == []


@pytest.mark.parametrize("fault", ["short_modes", "empty_modes", "timeout"])
def test_at_mb_04_unreadable_modes_block_arm(fake_bus, fault):
    cfg, bus, relay, ai = fake_bus
    relay.fault = fault
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock())
    with pytest.raises(Exception):
        arm(gate)
    assert gate.active() is None and bus.write_frames() == []


def test_at_mb_04_mode_change_after_arm_blocks_dispatch(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    clock = Clock()
    gate, *_ = make_gate(cfg, be, clock)
    lease = arm(gate)
    relay.modes[5] = 2                       # someone set channel 6 to toggle mode
    cs, _ = bench_cs(cfg, be, gate)
    rec = cs.submit(cs.build(actor="bench1", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH,
                             reason="t", lease_id=lease.lease_id))
    assert rec.outcome == Outcome.REJECTED and "preflight" in rec.detail
    assert bus.write_frames() == []


# --------------------------------------------------------------------------- AT-MB-06
def test_at_mb_06_armed_write_confirmed_by_readback(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    gate, arms, _ = make_gate(cfg, be, Clock())
    lease = arm(gate)
    assert arms == [lease]
    cs, audit = bench_cs(cfg, be, gate)
    rec = cs.submit(cs.build(actor="bench1", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH,
                             reason="t", lease_id=lease.lease_id))
    assert rec.outcome == Outcome.READBACK_CONFIRMED and rec.readback is True
    assert bus.write_frames() == [bytes.fromhex("01050000FF008C3A")]
    assert relay.coils[0] is True


def test_at_mb_06_arm_requirements(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock())
    with pytest.raises(BenchRefused, match="commissioning"):
        gate.arm(principal(commissioning=False, role=Role.ADMIN), confirmation=CONFIRMATION_PHRASE,
                 setup_description="isolated bench xx", allowlist=["relay_A.DO1"], duration_s=600)
    for kw, msg in [({"confirmation": "yes"}, "exact confirmation"),
                    ({"confirmation": CONFIRMATION_PHRASE.lower()}, "exact confirmation"),
                    ({"setup_description": "x"}, "describe"),
                    ({"duration_s": 1801}, "duration"), ({"duration_s": 10}, "duration"),
                    ({"allowlist": []}, "allowlist"), ({"allowlist": ["relay_A.DO1", "relay_A.DO1"]}, "allowlist"),
                    ({"allowlist": ["relay_A.DI1"]}, "not a configured relay output"),
                    ({"allowlist": ["analog_A.AI1"]}, "not a configured relay output"),
                    ({"allowlist": ["relay_Z.DO1"]}, "not a configured relay output")]:
        with pytest.raises(BenchRefused, match=msg):
            arm(gate, **kw)
    assert gate.active() is None and bus.frames_in == []


def test_at_mb_06_lease_not_armed_when_not_persisted(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock(), fail_persist=True)
    with pytest.raises(BenchRefused, match="durably"):
        arm(gate)
    assert gate.active() is None


@pytest.mark.parametrize("case", ["expired", "revoked", "session", "allowlist", "lease_id", "config", "origin"])
def test_at_mb_06_denials_send_nothing(fake_bus, case):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    clock = Clock()
    events = []
    gate, _, ends = make_gate(cfg, be, clock, events)
    lease = arm(gate)
    cs, _ = bench_cs(cfg, be, gate)
    kw = dict(actor="bench1", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH, reason="t",
              lease_id=lease.lease_id)
    if case == "expired":
        clock.t += 601
    elif case == "revoked":
        gate.revoke("operator done", "bench1")
    elif case == "session":
        gate.session_ended("h1")
    elif case == "allowlist":
        kw["target"] = "relay_A.DO2"
    elif case == "lease_id":
        kw["lease_id"] = "lease_other"
    elif case == "origin":
        kw["origin"] = CommandOrigin.CONTROLLER
    cmd = cs.build(**kw)
    if case == "config":
        from dataclasses import replace
        cmd = replace(cmd, expected_config_version="other")
    rec = cs.submit(cmd)
    assert rec.outcome == Outcome.REJECTED
    assert bus.write_frames() == []
    if case in ("expired", "revoked", "session"):
        assert gate.active() is None and len(ends) == 1
        assert any(k == "BENCH_LEASE_ENDED" and "no automatic output change" in p["note"] for k, p, _ in events)


def test_at_mb_06_session_binding_check(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock())
    lease = arm(gate)
    assert gate.check_session(principal(), lease.lease_id) is None
    assert "another session" in gate.check_session(principal(session="h2"), lease.lease_id)
    assert "another session" in gate.check_session(principal(user="other"), lease.lease_id)
    assert gate.check_session(principal(commissioning=False), lease.lease_id) is not None


def test_at_mb_06_expiry_sends_no_off_sequence(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    clock = Clock()
    gate, *_ = make_gate(cfg, be, clock)
    lease = arm(gate)
    cs, _ = bench_cs(cfg, be, gate)
    cs.submit(cs.build(actor="bench1", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH, reason="t",
                       lease_id=lease.lease_id))
    n = len(bus.write_frames())
    clock.t += 10_000
    assert gate.active() is None
    assert len(bus.write_frames()) == n and relay.coils[0] is True   # stays as it was; nothing sent


@pytest.mark.parametrize("fault,outcome", [
    ("apply_then_timeout", Outcome.OUTCOME_UNKNOWN), ("timeout", Outcome.REJECTED),
    ("exception", Outcome.REJECTED), ("readback_mismatch", Outcome.FAILED),
])
def test_at_mb_06_uncertain_writes_never_retransmitted(fake_bus, fault, outcome):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock())
    lease = arm(gate)
    relay.fault = fault
    cs, _ = bench_cs(cfg, be, gate)
    rec = cs.submit(cs.build(actor="bench1", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH,
                             reason="t", lease_id=lease.lease_id))
    assert rec.outcome == outcome, rec.detail
    # timeout/exception faults also break the preflight read, so no FC05 is sent at all
    assert len(bus.write_frames()) == (1 if fault in ("apply_then_timeout", "readback_mismatch") else 0)


def test_at_mb_06_write_exception_response_is_failed(fake_bus):
    cfg, bus, relay, ai = fake_bus
    be = ModbusBackend(cfg, context=ctx)
    gate, *_ = make_gate(cfg, be, Clock())
    lease = arm(gate)
    orig = bus.respond

    def respond(frame):    # module answers reads normally but refuses the write
        if frame[1] == 0x05:
            pdu = bytes([frame[0], 0x85, 0x04])
            return pdu + struct.pack(">H", FramerRTU.compute_CRC(pdu))
        return orig(frame)
    bus.respond = respond
    cs, _ = bench_cs(cfg, be, gate)
    rec = cs.submit(cs.build(actor="bench1", target="relay_A.DO1", desired=True, origin=CommandOrigin.BENCH,
                             reason="t", lease_id=lease.lease_id))
    assert rec.outcome == Outcome.FAILED and len(bus.write_frames()) == 1


def test_at_mb_06_restart_never_restores_lease(fake_bus, tmp_path):
    from aquacrop.api.hardware import HardwareService
    from aquacrop.historian.writer import Historian
    cfg, bus, relay, ai = fake_bus
    hist = Historian(tmp_path / "h.sqlite3", cfg, "sess-a")
    svc = HardwareService(cfg, hist, "sess-a")
    lease = svc.arm(principal(), confirmation=CONFIRMATION_PHRASE, setup_description="isolated bench test",
                    allowlist=["relay_A.DO1"], duration_s=600)
    assert hist.query("SELECT count(*) n FROM bench_leases WHERE revoked_utc IS NULL")[0]["n"] == 1
    svc.stop()
    hist.close()                                   # crash-like: lease row still open
    hist2 = Historian(tmp_path / "h.sqlite3", cfg, "sess-b")
    svc2 = HardwareService(cfg, hist2, "sess-b")
    assert svc2.gate.active() is None
    row = hist2.query("SELECT revoke_reason FROM bench_leases WHERE lease_id=?", (lease.lease_id,))[0]
    assert "restart" in row["revoke_reason"]
    rec = svc2.bench_command(principal(), target="relay_A.DO1", desired=True, reason="t", lease_id=lease.lease_id)
    assert rec.outcome == Outcome.REJECTED
    svc2.stop()
    hist2.close()
    assert bus.write_frames() == []


def test_at_mb_06_stop_priority_jumps_queue():
    from aquacrop.api.hardware import BusWorker
    from aquacrop.domain.model import OutputCommand, Priority, utcnow
    import threading
    from datetime import timedelta
    order, gate = [], threading.Event()
    w = BusWorker("b", 3600, lambda b: gate.wait(2))
    w.start()                                # first poll blocks until we have queued everything

    def mk(desired, prio, name):
        now = utcnow()
        return OutputCommand(name, "a", "relay_A.DO1", desired, "v", None, now, now + timedelta(seconds=30),
                             CommandOrigin.BENCH, "t", None, prio)
    futs = [w.submit(mk(True, Priority.NORMAL, "n1"), lambda: order.append("n1")),
            w.submit(mk(True, Priority.NORMAL, "n2"), lambda: order.append("n2")),
            w.submit(mk(False, Priority.STOP, "s1"), lambda: order.append("s1"))]
    gate.set()
    for f in futs:
        f.result(timeout=5)
    w.stop(lambda c, why: None)
    assert order == ["s1", "n1", "n2"]


# --------------------------------------------------------------------------- AT-MB-07
def test_at_mb_07_one_failing_device_does_not_invalidate_others(fake_bus):
    cfg, bus, relay, ai = fake_bus
    ai.fault = "timeout"
    relay.di[0] = True
    clock = Clock()
    be = ModbusBackend(cfg, context=ctx, monotonic=clock)
    r = be.read_inputs("relay_A")
    a = be.read_inputs("analog_A")
    assert r.ok and all(s.quality == Quality.GOOD for s in r.samples)
    assert not a.ok and all(s.quality == Quality.UNAVAILABLE and s.value is None for s in a.samples)
    assert be.health("relay_A").online and not be.health("analog_A").online


def test_at_mb_07_backoff_stops_traffic_then_recovers(fake_bus):
    cfg, bus, relay, ai = fake_bus
    ai.fault = "timeout"
    clock = Clock()
    be = ModbusBackend(cfg, context=ctx, monotonic=clock)
    for _ in range(3):
        be.read_inputs("analog_A")
    h = be.health("analog_A")
    assert h.consecutive_failures == 3 and h.backoff_until_monotonic > clock.t
    n = len(bus.frames_in)
    res = be.read_inputs("analog_A")
    assert len(bus.frames_in) == n and "backoff" in res.error         # no frames while backing off
    ai.fault = None
    clock.t = h.backoff_until_monotonic + 0.01
    assert be.read_inputs("analog_A").ok and be.health("analog_A").consecutive_failures == 0


def test_physical_edges_overrun_and_gaps_are_events(fake_bus, tmp_path):
    from aquacrop.api.hardware import HardwareService
    from aquacrop.historian.writer import Historian
    cfg, bus, relay, ai = fake_bus
    hist = Historian(tmp_path / "h.sqlite3", cfg, "sess-e")
    svc = HardwareService(cfg, hist, "sess-e")
    svc.poll_bus("rs485_a")
    relay.di[1] = True
    svc.poll_bus("rs485_a")
    relay.fault = ai.fault = "timeout"
    svc.poll_bus("rs485_a")
    hist.wait_idle()
    ev = {r["kind"]: r["payload_json"] for r in hist.query("SELECT kind, payload_json FROM events")}
    assert "relay_A.DI2" in ev["DI_EDGE"] and "not_before_utc" in ev["DI_EDGE"]
    assert "OBSERVATION_GAP" in ev
    assert "POLL_OVERRUN" in ev           # timeouts with retries exceed the 0.2 s test interval
    # Polls feed the live view, edges and alarms; they write no periodic history themselves.
    assert hist.query("SELECT count(*) FROM cycles")[0][0] == 0
    svc.stop()
    hist.close()
    assert bus.write_frames() == []


def test_bench_on_refused_when_storage_degraded(fake_bus, tmp_path):
    from aquacrop.api.hardware import HardwareService
    from aquacrop.historian.writer import Historian
    cfg, bus, relay, ai = fake_bus
    hist = Historian(tmp_path / "h.sqlite3", cfg, "sess-s")
    svc = HardwareService(cfg, hist, "sess-s")
    svc.start()
    lease = svc.arm(principal(), confirmation=CONFIRMATION_PHRASE, setup_description="isolated bench test",
                    allowlist=["relay_A.DO1"], duration_s=600)
    hist.fail_writes = True                       # every commit now fails (disk error)
    with pytest.raises(Exception):
        hist.flush()
    rec = svc.bench_command(principal(), target="relay_A.DO1", desired=True, reason="t", lease_id=lease.lease_id)
    assert rec.outcome == Outcome.REJECTED and "storage" in rec.detail
    assert bus.write_frames() == []
    hist.fail_writes = False
    svc.stop()
    hist.close()
