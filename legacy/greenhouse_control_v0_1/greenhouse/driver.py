"""Waveshare drivers. No hardware writes on initialization, polling, or shutdown."""
import inspect
import math
import threading
import time
from dataclasses import dataclass


@dataclass
class IOSnapshot:
    analog: list[int]
    analog_types: list[int]
    digital_inputs: list[bool]
    relays: list[bool]


class SimulationDriver:
    """Eight AI (simulated 4-20mA, raw microamps), eight DI, eight relay DO."""
    def __init__(self):
        self.lock = threading.RLock()
        self.analog = [12000] * 8
        self.inputs = [False] * 8
        self.relays = [False] * 8
        self.analog_types = [3] * 8

    def read(self):
        with self.lock:
            return IOSnapshot(self.analog.copy(), self.analog_types.copy(),
                              self.inputs.copy(), self.relays.copy())

    def write_relay(self, index: int, value: bool):
        with self.lock:
            self.relays[index] = value

    def set_analog(self, index: int, value: int):
        with self.lock:
            self.analog[index] = value

    def set_input(self, index: int, value: bool):
        with self.lock:
            self.inputs[index] = value

    def close(self):
        pass


class WaveshareModbusDriver:
    """Single serial bus, explicit addresses; compatible with PyModbus 3.x.

    Waveshare Relay D: FC01 coils 0..7; FC02 inputs 0..7;
      control-mode FC03 holding registers 0x1000..0x1007.
    Waveshare Analog Input 8CH: FC04 input registers 0..7;
      channel-mode FC03 holding registers 0x1000..0x1007.
    """
    def __init__(self, cfg):
        from pymodbus.client import ModbusSerialClient
        self.cfg = cfg
        self.lock = threading.RLock()
        self.client = ModbusSerialClient(port=cfg.serial_port, baudrate=cfg.baudrate,
                                         parity=cfg.parity, stopbits=cfg.stopbits,
                                         bytesize=8, timeout=1.0, retries=1)
        # PyModbus changed `slave` to `device_id` in newer 3.x versions.
        params = inspect.signature(self.client.read_coils).parameters
        self.id_key = "device_id" if "device_id" in params else "slave"

    def _request(self, method, address, count_or_value, unit):
        kw = {self.id_key: unit}
        if method == "write_coil":
            response = self.client.write_coil(address, bool(count_or_value), **kw)
        else:
            response = getattr(self.client, method)(address, count=int(count_or_value), **kw)
        if response is None or response.isError():
            raise IOError(f"Modbus {method}, device {unit}, address {address}: {response}")
        return response

    def read(self):
        with self.lock:
            if not self.client.connect():
                raise ConnectionError(f"Cannot open serial port {self.cfg.serial_port}")
            a = self._request("read_input_registers", 0, 8, self.cfg.analog_id)
            types = self._request("read_holding_registers", 0x1000, 8, self.cfg.analog_id)
            coils = self._request("read_coils", 0, 8, self.cfg.relay_id)
            inputs = self._request("read_discrete_inputs", 0, 8, self.cfg.relay_id)
            if any(int(n) not in (0, 1, 2, 3, 4) for n in types.registers[:8]):
                raise IOError("Unrecognized Waveshare analog channel data type")
            return IOSnapshot([int(x) for x in a.registers[:8]],
                              [int(x) for x in types.registers[:8]],
                              [bool(x) for x in inputs.bits[:8]],
                              [bool(x) for x in coils.bits[:8]])

    def write_relay(self, index: int, value: bool):
        if not self.cfg.bench_writes:
            raise PermissionError("Real hardware writes are disabled")
        with self.lock:
            if not self.client.connect():
                raise ConnectionError("RS485 disconnected")
            # Reject writes when hardware is configured for linkage, toggle or edge mode.
            modes = self._request("read_holding_registers", 0x1000, 8, self.cfg.relay_id)
            if any(int(m) != 0 for m in modes.registers[:8]):
                raise PermissionError("Relay module is not in all-normal control mode")
            self._request("write_coil", index, value, self.cfg.relay_id)
            confirmed = self._request("read_coils", 0, 8, self.cfg.relay_id)
            if bool(confirmed.bits[index]) != bool(value):
                raise IOError("Relay feedback mismatch")

    def close(self):
        with self.lock:
            self.client.close()
