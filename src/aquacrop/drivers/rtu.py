"""Pinned pymodbus adapter for one RS485 bus (brief §6).

* Exactly one pymodbus version is supported (`PINNED_PYMODBUS`); any other version is refused at
  construction instead of guessing call signatures at runtime.
* The library's own retry loop is disabled (`retries=0`). Reads use a bounded driver-level retry
  count from the bus configuration. Writes are never retried: a write that gets no valid answer is
  reported as OUTCOME_UNKNOWN by the caller, because the module may have applied it.
* There is no generic "read/write register" method. The adapter exposes the fixed set of Waveshare
  operations the platform needs, each with a fixed address range (see `waveshare.py`), and one
  single-register write (FC06) for the control register of a drive whose register map is configured
  (ADR-0015). A bus refuses FC06 to any unit and register it was not constructed with
  (`register_writes`), before anything is sent; a bus built without them cannot write a register.
* Responses are validated beyond what pymodbus checks: function code, unit address, exact data
  length and zero padding bits. pymodbus 3.11.3 accepts a short or empty register reply and a reply
  carrying a different read function code, so those checks live here.
* Unit address 0 (broadcast) and addresses outside 1..247 are refused before anything is sent.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pymodbus
from pymodbus import FramerType
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

from ..config.schema import BusConfig
from .rtu_errors import RtuError, RtuExceptionResponse, RtuInvalidResponse, RtuNoResponse, RtuRefused  # noqa: F401

PINNED_PYMODBUS = "3.11.3"

FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06


@dataclass
class BusStats:
    requests: int = 0
    write_requests: int = 0
    failures: int = 0
    last_error: str | None = None
    by_kind: dict[str, int] = field(default_factory=dict)


def check_version() -> None:
    if pymodbus.__version__ != PINNED_PYMODBUS:
        raise RtuRefused(f"pymodbus {pymodbus.__version__} is not the pinned {PINNED_PYMODBUS}; "
                         "install the locked requirements")


class RtuBus:
    """One serial port, one client, one lock. All traffic on a bus is serialized here."""

    def __init__(self, bus: BusConfig, *, client_factory=None,
                 register_writes: frozenset[tuple[int, int]] = frozenset()):
        check_version()
        self.register_writes = frozenset(register_writes)     # (unit, register) pairs FC06 may address
        if bus.kind != "rtu" or not bus.port:
            raise RtuRefused(f"bus {bus.bus_id} is not an rtu bus with a port")
        self.bus = bus
        s = bus.serial
        factory = client_factory or ModbusSerialClient
        self._client = factory(port=bus.port, framer=FramerType.RTU, baudrate=s.baudrate, bytesize=s.bytesize,
                               parity=s.parity, stopbits=s.stopbits, timeout=s.timeout_s, retries=0)
        self._lock = threading.Lock()
        self.stats = BusStats()
        self._closed = False

    # ------------------------------------------------------------------ plumbing
    def _ensure_open(self) -> None:
        if self._closed:
            raise RtuRefused("adapter closed")
        if not self._client.connected and not self._client.connect():
            raise RtuNoResponse(f"cannot open serial port {self.bus.port}")

    @staticmethod
    def _check_unit(unit: int) -> None:
        if isinstance(unit, bool) or not isinstance(unit, int) or not 1 <= unit <= 247:
            raise RtuRefused(f"unit address {unit!r} outside 1..247 (broadcast is never used)")

    def _call(self, fc: int, unit: int, fn, *, write: bool):
        self._check_unit(unit)
        with self._lock:
            self._ensure_open()
            self.stats.requests += 1
            if write:
                self.stats.write_requests += 1
            try:
                rsp = fn()
            except ModbusException as e:   # ModbusIOException: no (valid) frame within the timeout
                raise RtuNoResponse(str(e)) from e
            except OSError as e:           # port vanished: drop the handle so the next call reopens
                self._client.close()
                raise RtuNoResponse(f"serial error: {e}") from e
        if rsp is None:
            raise RtuNoResponse("no response object")
        if rsp.isError():
            code = getattr(rsp, "exception_code", None)
            if code is not None:
                raise RtuExceptionResponse(fc, int(code))
            raise RtuInvalidResponse(f"error response {rsp!r}")
        if getattr(rsp, "function_code", None) != fc:
            raise RtuInvalidResponse(f"function code 0x{getattr(rsp, 'function_code', 0):02X} != 0x{fc:02X}")
        if getattr(rsp, "dev_id", unit) != unit:
            raise RtuInvalidResponse(f"unit {rsp.dev_id} != {unit}")
        return rsp

    def _with_read_retries(self, op):
        attempts = 1 + self.bus.serial.read_retries
        last: RtuError | None = None
        for i in range(attempts):
            try:
                return op()
            except (RtuNoResponse, RtuInvalidResponse) as e:
                last = e
                self._note(e)
                if i + 1 < attempts:
                    time.sleep(min(0.05, self.bus.serial.timeout_s))
            except RtuError as e:          # exception responses and refusals are not retried
                self._note(e)
                raise
        assert last is not None
        raise last

    def _note(self, e: RtuError) -> None:
        self.stats.failures += 1
        self.stats.last_error = f"{e.kind}: {e}"
        self.stats.by_kind[e.kind] = self.stats.by_kind.get(e.kind, 0) + 1

    # ------------------------------------------------------------------ validated primitives
    def read_bits(self, fc: int, unit: int, start: int, count: int) -> list[bool]:
        if fc not in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS) or not 1 <= count <= 8:
            raise RtuRefused("unsupported bit read")
        method = self._client.read_coils if fc == FC_READ_COILS else self._client.read_discrete_inputs

        def op():
            rsp = self._call(fc, unit, lambda: method(start, count=count, device_id=unit), write=False)
            bits = list(rsp.bits)
            nbytes = (count + 7) // 8
            if len(bits) != 8 * nbytes:
                raise RtuInvalidResponse(f"{len(bits) // 8} data bytes, expected {nbytes}")
            if any(bits[count:]):
                raise RtuInvalidResponse("non-zero padding bits")
            return bits[:count]
        return self._with_read_retries(op)

    def read_registers(self, fc: int, unit: int, start: int, count: int) -> list[int]:
        if fc not in (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS) or not 1 <= count <= 8:
            raise RtuRefused("unsupported register read")
        method = (self._client.read_holding_registers if fc == FC_READ_HOLDING_REGISTERS
                  else self._client.read_input_registers)

        def op():
            rsp = self._call(fc, unit, lambda: method(start, count=count, device_id=unit), write=False)
            regs = list(rsp.registers)
            if len(regs) != count:
                raise RtuInvalidResponse(f"{len(regs)} registers, expected {count}")
            return regs
        return self._with_read_retries(op)

    def write_single_coil(self, unit: int, address: int, value: bool) -> None:
        """FC05, sent exactly once. Raises on anything but a matching echo; the caller decides
        FAILED (exception response) versus OUTCOME_UNKNOWN (no/invalid answer)."""
        if not isinstance(value, bool) or not 0 <= address <= 7:
            raise RtuRefused("coil write outside channel range 0..7 or non-boolean value")
        try:
            rsp = self._call(FC_WRITE_SINGLE_COIL, unit,
                             lambda: self._client.write_coil(address, value, device_id=unit), write=True)
        except RtuError as e:
            self._note(e)
            raise
        echo_addr = getattr(rsp, "address", None)
        echo_bits = list(getattr(rsp, "bits", []) or [])
        if echo_addr != address or not echo_bits or bool(echo_bits[0]) != value:
            e = RtuInvalidResponse(f"echo mismatch (address {echo_addr}, bits {echo_bits[:1]})")
            self._note(e)
            raise e

    def write_single_register(self, unit: int, address: int, value: int) -> None:
        """FC06, sent exactly once. Raises on anything but a matching echo (address and value)."""
        for x in (address, value):
            if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 0xFFFF:
                raise RtuRefused("register write needs an address and a value of 0..65535")
        if (unit, address) not in self.register_writes:
            raise RtuRefused(f"register {address} of unit {unit} is not a configured drive control register")
        try:
            rsp = self._call(FC_WRITE_SINGLE_REGISTER, unit,
                             lambda: self._client.write_register(address, value, device_id=unit), write=True)
        except RtuError as e:
            self._note(e)
            raise
        echo = list(getattr(rsp, "registers", []) or [])
        if getattr(rsp, "address", None) != address or echo[:1] != [value]:
            e = RtuInvalidResponse(f"echo mismatch (address {getattr(rsp, 'address', None)}, value {echo[:1]})")
            self._note(e)
            raise e

    def close(self) -> None:
        """Release the port. Sends nothing: no OFF sequence, no flush of outputs."""
        with self._lock:
            self._closed = True
            self._client.close()
