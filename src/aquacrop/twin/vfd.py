"""The simulated drive's Modbus interface (ADR-0015). SIMULATION ONLY.

`SimVfd` answers the configured register map the way `drivers/vfd.py` expects a drive to, and moves
the twin's shared drive: the control register accepts the run sequence (in order) and the stop value,
and the status word reports the twin drive's running, fault and ready state. Like a real drive set up
for fieldbus control, it stops and trips when no frame reaches it within `comm_loss_timeout_s` while
it runs, so a controller that dies or loses the bus does not leave the main pump running.

The register map of an installation-48 run is a DEMO map of this simulated drive, not a vendor's; a
real profile refuses it (`VfdModbusMap.provenance`). Injected faults: `NO_RESPONSE` (the drive answers
nothing), `EXCEPTION` (it answers every request with exception code 4) and `IGNORES_WRITES` (it echoes
control values but does not act on them).
"""
from __future__ import annotations

from typing import Callable

from ..config.schema import VfdModbusMap
from ..drivers.rtu_errors import RtuExceptionResponse, RtuNoResponse, RtuRefused

VFD_FAULTS = (None, "NO_RESPONSE", "EXCEPTION", "IGNORES_WRITES")


class SimVfd:
    def __init__(self, m: VfdModbusMap, plant, clock: Callable[[], float]):
        self.m, self.plant, self.clock = m, plant, clock
        self.control = m.stop_value
        self.seq_pos = 0                 # how much of the run sequence has arrived in order
        self.last_frame_s: float | None = None
        self.fault: str | None = None
        self.reads = self.writes = 0

    # ------------------------------------------------------------------ the wire (as seen by the drive)
    def _frame(self, fc: int) -> None:
        if self.fault == "NO_RESPONSE":
            raise RtuNoResponse("simulated drive: no response")
        self.last_frame_s = self.clock()       # any valid frame addressed to the drive resets its timer
        if self.fault == "EXCEPTION":
            raise RtuExceptionResponse(fc, 4)

    def status_word(self) -> int:
        dr, m = self.plant.drive, self.m
        word = 0
        if dr.speed > self.plant.p.dynamics.drive_running_threshold:
            word |= m.running_mask
        if dr.tripped is not None:
            word |= m.fault_mask
        if m.ready_mask is not None and dr.tripped is None and self.plant.safety_chain_ok:
            word |= m.ready_mask
        return word

    def read(self, fc: int, address: int) -> int:
        self._frame(fc)
        self.reads += 1
        m = self.m
        if address == m.status_register and fc == m.status_function:
            return self.status_word()
        for name in ("frequency", "current", "fault_code"):
            r = getattr(m, name)
            if r is not None and r.address == address and r.function == fc:
                return self._diagnostic(name, r)
        if fc == 3 and address == m.control_register:
            return self.control
        raise RtuExceptionResponse(fc, 2)

    def _diagnostic(self, name: str, r) -> int:
        dr = self.plant.drive
        if name == "frequency":
            eng = 50.0 * dr.speed                      # DEMO: 50 Hz at full speed
        elif name == "current":
            eng = 0.0 if dr.speed <= 0 else 2.0 + 8.0 * dr.speed      # DEMO amperes
        else:
            eng = 0.0 if dr.tripped is None else (1.0 if "COMM_LOSS" in dr.tripped else 99.0)
        return max(0, min(0xFFFF, int(round(eng / r.scale))))

    def write(self, address: int, value: int) -> None:
        self._frame(6)
        m = self.m
        if address != m.control_register:
            raise RtuExceptionResponse(6, 2)
        self.writes += 1
        self.control = value
        if self.fault == "IGNORES_WRITES":
            return
        seq = m.run_sequence
        if value == m.stop_value:
            self.seq_pos = 0
            self.plant.drive.run_cmd = False
        elif self.seq_pos < len(seq) and value == seq[self.seq_pos]:
            self.seq_pos += 1
            if self.seq_pos == len(seq):
                self.plant.drive.run_cmd = True
        elif value == seq[0]:
            self.seq_pos = 1
            if len(seq) == 1:
                self.plant.drive.run_cmd = True
        else:                                   # out of order: a real drive state machine would refuse
            self.seq_pos = 0
            raise RtuExceptionResponse(6, 3)

    # ------------------------------------------------------------------ the drive's own timer
    def tick(self) -> None:
        dr = self.plant.drive
        if not dr.run_cmd or self.last_frame_s is None:
            return
        silent = self.clock() - self.last_frame_s
        if silent > self.m.comm_loss_timeout_s + 1e-9:
            dr.run_cmd = False
            self.seq_pos = 0
            self.plant.trip_drive(f"COMM_LOSS: no Modbus frame for {silent:.1f} s "
                                  f"(drive timeout {self.m.comm_loss_timeout_s:g} s)")

    def fingerprint(self) -> tuple:
        return (self.control, self.seq_pos, self.last_frame_s, self.fault)

    def view(self) -> dict:
        return {"control_value": self.control, "run_sequence_received": self.seq_pos, "fault": self.fault,
                "last_frame_sim_s": self.last_frame_s, "reads": self.reads, "writes": self.writes,
                "comm_loss_timeout_s": self.m.comm_loss_timeout_s, "provenance": self.m.provenance}


class SimVfdBus:
    """The bus operations `VfdDevice` uses, served by one `SimVfd` (no framing; the framed path is
    exercised by the tests' in-memory serial bus)."""

    def __init__(self, vfd: SimVfd):
        self.vfd = vfd

    def _unit(self, unit: int) -> None:
        if not 1 <= unit <= 247:
            raise RtuRefused(f"unit address {unit!r} outside 1..247")
        if unit != self.vfd.m.address:
            raise RtuNoResponse(f"no device at address {unit}")

    def read_registers(self, fc: int, unit: int, start: int, count: int) -> list[int]:
        self._unit(unit)
        return [self.vfd.read(fc, start + i) for i in range(count)]

    def write_single_register(self, unit: int, address: int, value: int) -> None:
        self._unit(unit)
        self.vfd.write(address, value)
