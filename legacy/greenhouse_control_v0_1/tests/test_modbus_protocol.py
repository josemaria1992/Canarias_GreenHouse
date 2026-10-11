"""Mocked register protocol tests. No assertion of physical hardware compatibility."""
import threading
from types import SimpleNamespace

import pytest
from greenhouse.driver import WaveshareModbusDriver


class Reply:
    def __init__(self, kind, data):
        setattr(self, kind, data)
    def isError(self):
        return False


class FakeClient:
    def __init__(self):
        self.calls = []
        self.coils = [False] * 8
        self.modes = [0] * 8
    def connect(self): return True
    def close(self): pass
    def read_input_registers(self, address, count, device_id):
        self.calls.append(('FC04', address, count, device_id))
        return Reply('registers', [12500] * 8)
    def read_holding_registers(self, address, count, device_id):
        self.calls.append(('FC03', address, count, device_id))
        return Reply('registers', ([3]*8 if device_id == 2 else self.modes))
    def read_coils(self, address, count, device_id):
        self.calls.append(('FC01', address, count, device_id))
        return Reply('bits', self.coils)
    def read_discrete_inputs(self, address, count, device_id):
        self.calls.append(('FC02', address, count, device_id))
        return Reply('bits', [True]+[False]*7)
    def write_coil(self, address, value, device_id):
        self.calls.append(('FC05', address, value, device_id))
        self.coils[address] = value
        return Reply('bits', [])


def mock_driver(allow=True):
    d = object.__new__(WaveshareModbusDriver)
    d.client = FakeClient()
    d.cfg = SimpleNamespace(serial_port='fake', analog_id=2, relay_id=1,
                            bench_writes=allow)
    d.id_key = 'device_id'
    d.lock = threading.RLock()
    return d


def test_reads_all_24_points_from_expected_registers():
    d = mock_driver()
    snap = d.read()
    assert snap.analog == [12500] * 8
    assert snap.analog_types == [3] * 8
    assert snap.digital_inputs == [True] + [False] * 7
    assert snap.relays == [False] * 8
    assert d.client.calls == [
        ('FC04', 0, 8, 2), ('FC03', 4096, 8, 2),
        ('FC01', 0, 8, 1), ('FC02', 0, 8, 1)
    ]


def test_relay_write_verifies_normal_mode_and_readback():
    d = mock_driver()
    d.write_relay(3, True)
    assert ('FC05', 3, True, 1) in d.client.calls
    assert d.client.coils[3]
    d.client.modes[0] = 1
    with pytest.raises(PermissionError):
        d.write_relay(2, True)
    assert not d.client.coils[2]


def test_bench_write_flag_required():
    d = mock_driver(allow=False)
    with pytest.raises(PermissionError):
        d.write_relay(0, True)
    assert d.client.calls == []
