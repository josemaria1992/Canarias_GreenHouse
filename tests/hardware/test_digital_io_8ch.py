"""SKU 26244: exact model, mixed inventory, real RTU framing over an in-memory bus only."""
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.api.hardware import HardwareService
from aquacrop.config import load_config
from aquacrop.config.catalog import CATALOG, ModelId
from aquacrop.config.schema import InstallationConfig
from aquacrop.control.commands import CommandService, MemoryAudit
from aquacrop.domain.model import CommandOrigin, ControllerState, Outcome, SignalSource
from aquacrop.drivers.diagnostics import read_device
from aquacrop.historian.writer import Historian
from aquacrop.io.modbus_backend import ModbusBackend
from aquacrop.io.sampling import AcqContext
from aquacrop.security.auth import Role
from aquacrop.twin.runtime import make_runtime
from tests.hardware.test_modbus import Clock, arm, make_gate

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def hw80(fake_bus):
    old_cfg, bus, relay, analog = fake_bus
    data = load_config(ROOT / 'configs' / 'bench-malmo.example.yaml').model_dump(mode='json')
    data['buses'][0] = old_cfg.buses[0].model_dump(mode='json')
    cfg = InstallationConfig.model_validate(data)
    bus.add('relay', 3)
    bus.add('ai', 4)
    a, b = bus.add('digital_io', 5), bus.add('digital_io', 6)
    return cfg, bus, a, b

def commands(cfg, backend, gate):
    return CommandService(cfg, backend, MemoryAudit(), gate.authorize, lambda: ControllerState.STOPPED)

def test_owner_inventory_and_electrical_types():
    cfg = load_config(ROOT / 'configs' / 'installation-80.yaml')
    assert len(cfg.devices) == 6 and len(cfg.all_points()) == cfg.expected_point_count() == 80
    assert {k: sum(p.kind.value == k for p in cfg.all_points()) for k in ('AI', 'DI', 'DO')} == {
        'AI': 16, 'DI': 32, 'DO': 32}
    assert sum(d.model == ModelId.WAVESHARE_IO_8CH for d in cfg.devices) == 2
    assert CATALOG[ModelId.WAVESHARE_IO_8CH].sku == '26244'
    assert CATALOG[ModelId.WAVESHARE_IO_8CH].output_type == 'OPEN_DRAIN_SINK'
    assert CATALOG[ModelId.WAVESHARE_RELAY_D].output_type == 'RELAY_CONTACT'
    # Additional contacts/equipment are not falsely presented as physically installed.
    assert not any(m.point.startswith('digital_') for m in cfg.virtual_mapping)
    assert cfg.physical_mapping == []
    for name, zone in [('bench-malmo.example', 'Europe/Stockholm'), ('hardware-read-only.example', 'Atlantic/Canary')]:
        physical = load_config(ROOT / 'configs' / (name + '.yaml'))
        assert physical.expected_point_count() == 80 and physical.site.timezone == zone
        assert physical.drive_adapters[0].kind == 'NOT_CONFIGURED'

def test_all_80_virtual_points_independent_and_new_module_loss_isolated():
    rt = make_runtime(load_config(ROOT / 'configs' / 'installation-80.yaml'))
    assert rt.set_source('test', SignalSource.IO_TEST)[0]
    for p in rt.cfg.all_points():
        if p.kind.value == 'DO':
            for desired in (True, False):
                assert rt.operator_command('test', str(p), desired, 'isolated virtual channel').outcome == Outcome.READBACK_CONFIRMED
        elif p.kind.value == 'DI':
            for raw in (True, False):
                rt.inject('test', str(p), raw)
                assert rt.samples[str(p)].value is raw
        else:
            rt.inject('test', str(p), 12000)
            assert rt.samples[str(p)].raw_value == 12000
    rt.set_device_fault('test', 'digital_A', 'TIMEOUT')
    assert all(rt.samples[str(p)].quality.value == 'UNAVAILABLE' for p in rt.cfg.device('digital_A').point_ids())
    assert all(rt.samples[str(p)].quality.value != 'UNAVAILABLE' for p in rt.cfg.device('digital_B').point_ids())

def test_read_only_diagnostics_and_backend_use_digital_protocol(hw80):
    cfg, bus, a, b = hw80
    a.di[7] = True
    a.coils[0] = True
    be = ModbusBackend(cfg, context=lambda: AcqContext('test', 'cycle'))
    try:
        assert be.read_inputs('digital_A').samples[7].value is True
        assert be.read_outputs('digital_A').samples[0].value is True
        diag = read_device(cfg.device('digital_A'), be.devices['digital_A'], metadata=True)
        assert diag['output_type'] == 'OPEN_DRAIN_SINK' and diag['bench_preflight'].startswith('PASS')
        assert diag['discrete_inputs'][7] is True and diag['coils'][0] is True
        cs = CommandService(cfg, be, MemoryAudit(), lambda c: None, lambda: ControllerState.STOPPED)
        cmd = cs.build(actor='test', target='digital_A.DO8', desired=True, origin=CommandOrigin.BENCH, reason='no lease')
        assert cs.submit(cmd).outcome == Outcome.REJECTED
        assert bus.write_frames() == []
    finally:
        be.close()

def test_bench_lease_supports_new_outputs_and_channel_isolation(hw80):
    cfg, bus, a, b = hw80
    be = ModbusBackend(cfg, context=lambda: AcqContext('test', 'cycle'))
    try:
        gate, *_ = make_gate(cfg, be, Clock())
        lease = arm(gate, allowlist=['digital_A.DO8'])
        cs = commands(cfg, be, gate)
        def command(target, desired):
            return cs.submit(cs.build(actor='bench1', target=target, desired=desired,
                origin=CommandOrigin.BENCH, reason='isolated digital output', lease_id=lease.lease_id))
        assert command('digital_A.DO8', True).outcome == Outcome.READBACK_CONFIRMED
        assert a.coils == [False] * 7 + [True] and b.coils == [False] * 8
        count = len(bus.write_frames())
        assert command('digital_B.DO8', True).outcome == Outcome.REJECTED
        assert command('digital_A.DI8', True).outcome == Outcome.REJECTED
        assert len(bus.write_frames()) == count
        assert command('digital_A.DO8', False).outcome == Outcome.READBACK_CONFIRMED
        assert all(f[1] == 5 and f[0] == 5 and f[2:4] == bytes([0, 7]) for f in bus.write_frames())
    finally:
        be.close()

@pytest.mark.parametrize('fault', ['LINKAGE', 'short_modes', 'empty_modes', 'timeout'])
def test_new_output_refuses_unsafe_or_unreadable_modes(hw80, fault):
    cfg, bus, a, b = hw80
    be = ModbusBackend(cfg, context=lambda: AcqContext('test', 'cycle'))
    try:
        gate, *_ = make_gate(cfg, be, Clock())
        lease = arm(gate, allowlist=['digital_A.DO1'])
        if fault == 'LINKAGE':
            a.modes[7] = 1
        else:
            a.fault = fault
        cs = commands(cfg, be, gate)
        cmd = cs.build(actor='bench1', target='digital_A.DO1', desired=True, origin=CommandOrigin.BENCH,
                       reason='mode guard', lease_id=lease.lease_id)
        assert cs.submit(cmd).outcome == Outcome.REJECTED
        assert bus.write_frames() == [] and a.writes_applied == 0
    finally:
        be.close()

def test_expiry_during_new_module_preflight_sends_no_write(hw80):
    cfg, bus, a, b = hw80
    clock = Clock()
    be = ModbusBackend(cfg, context=lambda: AcqContext('test', 'cycle'))
    try:
        gate, *_ = make_gate(cfg, be, clock)
        lease = arm(gate, allowlist=['digital_A.DO1'], duration_s=60)
        read = be.devices['digital_A'].read_relay_modes
        def expire_during_read():
            result = read()
            clock.t = lease.expires_monotonic + 0.01
            return result
        be.devices['digital_A'].read_relay_modes = expire_during_read
        cs = commands(cfg, be, gate)
        cmd = cs.build(actor='bench1', target='digital_A.DO1', desired=True, origin=CommandOrigin.BENCH,
                       reason='expires in preflight', lease_id=lease.lease_id)
        assert cs.submit(cmd).outcome == Outcome.REJECTED
        assert bus.write_frames() == [] and a.writes_applied == 0
    finally:
        be.close()

def test_hardware_recorder_persists_all_80_without_writes(hw80, tmp_path):
    cfg, bus, a, b = hw80
    hist = Historian(tmp_path / 'history.sqlite3', cfg, 'test')
    svc = HardwareService(cfg, hist, 'test', recording_interval_s=0.2)
    try:
        svc.start()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            rows = hist.query('SELECT count(*) FROM cycles')
            if rows[0][0] >= 2:
                break
            time.sleep(0.02)
        svc.stop()
        hist.wait_idle()
        counts = hist.query('SELECT count(*), count(DISTINCT point_id) FROM samples GROUP BY cycle_pk')
        assert len(counts) >= 2 and all(tuple(r) == (80, 80) for r in counts)
        assert bus.write_frames() == []
    finally:
        svc.stop()
        hist.close()

def test_api_reports_six_modules_and_transistor_outputs(tmp_path):
    cfg = load_config(ROOT / 'configs' / 'installation-80.yaml')
    app = create_app(cfg, state_dir=tmp_path, bind_host='127.0.0.1')
    app.state.accounts.create_user('viewer1', 'viewer test password', Role.VIEWER)
    app.state.accounts.create_user('oper9', 'operator test password', Role.OPERATOR)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        assert client.post('/api/auth/login', json={'username': 'viewer1', 'password': 'viewer test password'},
                           headers={'Origin': 'http://127.0.0.1:8000'}).status_code == 200
        assert client.get('/api/points').status_code == 403        # 0.7.0: channels are not a viewer's page
        assert client.post('/api/auth/login', json={'username': 'oper9', 'password': 'operator test password'},
                           headers={'Origin': 'http://127.0.0.1:8000'}).status_code == 200
        data = client.get('/api/points').json()
        assert len(data['devices']) == 6 and sum(len(d['points']) for d in data['devices']) == 80
        io = next(d for d in data['devices'] if d['device_uid'] == 'digital_A')
        assert io['sku'] == '26244' and io['output_type'] == 'OPEN_DRAIN_SINK'
        assert io['output_current_limit_ma'] == 500
