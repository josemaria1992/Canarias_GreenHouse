"""pyserial URL handler `aquafake://<bus>`: an in-memory RS485 bus with simulated Waveshare devices.

It plugs in *below* pymodbus (at the pyserial layer), so the real ModbusSerialClient, RTU framer,
CRC handling and transaction manager are exercised. Every frame written is logged so tests can
prove that read-only paths emit zero write frames. This is a test double, not hardware evidence.
"""
from __future__ import annotations

import struct
import threading

from serial.serialutil import SerialBase

from pymodbus.framer import FramerRTU

BUSES: dict[str, "FakeBus"] = {}


def crc(data: bytes) -> bytes:
    return struct.pack("<H", FramerRTU.compute_CRC(data))


class FakeDevice:
    def __init__(self, model: str, address: int):
        self.model, self.address = model, address
        self.coils = [False] * 8
        self.di = [False] * 8
        self.modes = [0] * 8          # relay control modes or AI data types
        self.ai = [0] * 8
        self.meta = {0x2000: 0x0003, 0x4000: address, 0x8000: 0x0100}  # unverified V2 metadata stand-ins
        self.fault: str | None = None  # see FakeBus.respond
        self.writes_applied = 0


class FakeBus:
    def __init__(self, name: str):
        self.name = name
        self.devices: dict[int, FakeDevice] = {}
        self.frames_in: list[bytes] = []
        self.lock = threading.Lock()

    def add(self, model: str, address: int) -> FakeDevice:
        d = FakeDevice(model, address)
        self.devices[address] = d
        return d

    def write_frames(self) -> list[bytes]:
        return [f for f in self.frames_in if len(f) > 1 and f[1] in (0x05, 0x06, 0x0F, 0x10)]

    # ------------------------------------------------------------------ device behaviour
    def respond(self, frame: bytes) -> bytes:
        if len(frame) < 4 or crc(frame[:-2]) != frame[-2:]:
            return b""
        addr, fc = frame[0], frame[1]
        if addr == 0:
            return b""  # broadcast never answered (and never sent by this software)
        d = self.devices.get(addr)
        if d is None:
            return b""
        f = d.fault
        if f == "timeout":
            return b""
        body = frame[2:-2]

        def exc(code: int) -> bytes:
            pdu = bytes([addr, fc | 0x80, code])
            return pdu + crc(pdu)

        if f == "exception":
            return exc(4)
        pdu: bytes
        if fc in (0x01, 0x02):
            start, count = struct.unpack(">HH", body)
            src = d.coils if fc == 0x01 else d.di
            if d.model != "relay" or start + count > 8 or count == 0:
                return exc(2)
            bits = src[start:start + count]
            nbytes = (count + 7) // 8
            val = sum(1 << i for i, b in enumerate(bits) if b)
            data = val.to_bytes(nbytes, "little")
            pdu = bytes([addr, fc, nbytes]) + data
        elif fc in (0x03, 0x04):
            start, count = struct.unpack(">HH", body)
            if fc == 0x04:
                if d.model != "ai" or start + count > 8:
                    return exc(2)
                regs = d.ai[start:start + count]
            elif 0x1000 <= start and start + count <= 0x1008:
                regs = d.modes[start - 0x1000:start - 0x1000 + count]
            elif start in d.meta and count == 1:
                regs = [d.meta[start]]
            else:
                return exc(2)
            if f == "short_modes" and fc == 0x03:
                regs = regs[:-1]
            if f == "empty_modes" and fc == 0x03:
                regs = []
            pdu = bytes([addr, fc, 2 * len(regs)]) + b"".join(struct.pack(">H", r) for r in regs)
        elif fc == 0x05:
            a, v = struct.unpack(">HH", body)
            if d.model != "relay" or a >= 8 or v not in (0x0000, 0xFF00):
                return exc(3)
            if f != "readback_mismatch":
                d.coils[a] = v == 0xFF00
                d.writes_applied += 1
            if f == "apply_then_timeout":
                return b""
            pdu = bytes([addr, fc]) + body
        elif fc == 0x0F:
            a, n, nb = struct.unpack(">HHB", body[:5])
            if d.model != "relay" or a + n > 8:
                return exc(2)
            val = int.from_bytes(body[5:5 + nb], "little")
            for i in range(n):
                d.coils[a + i] = bool(val >> i & 1)
            d.writes_applied += 1
            pdu = bytes([addr, fc]) + struct.pack(">HH", a, n)
        else:
            return exc(1)
        if f == "wrong_unit":
            pdu = bytes([(addr % 247) + 1]) + pdu[1:]
        if f == "wrong_fc":
            pdu = bytes([pdu[0], 0x01 if fc != 0x01 else 0x02]) + pdu[2:]
        out = pdu + crc(pdu)
        if f == "bad_crc":
            out = out[:-1] + bytes([out[-1] ^ 0xFF])
        if f == "short":
            out = out[:-3]
        return out


def bus(name: str) -> FakeBus:
    if name not in BUSES:
        BUSES[name] = FakeBus(name)
    return BUSES[name]


class Serial(SerialBase):
    """pyserial-compatible port backed by a FakeBus."""

    def open(self):
        name = self.portstr.split("://", 1)[1]
        self._bus = bus(name)
        self._rx = bytearray()
        self.is_open = True

    def close(self):
        self.is_open = False

    def _reconfigure_port(self, *a, **k):
        pass

    def from_url(self, url):
        return url

    @property
    def in_waiting(self):
        return len(self._rx)

    def read(self, size=1):
        out = bytes(self._rx[:size])
        del self._rx[:size]
        return out

    def write(self, data):
        data = bytes(data)
        with self._bus.lock:
            self._bus.frames_in.append(data)
            self._rx += self._bus.respond(data)
        return len(data)

    def reset_input_buffer(self):
        self._rx.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass
