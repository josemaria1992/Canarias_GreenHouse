"""The drive's Modbus interface (ADR-0015): fixed operations over a configured register map.

The register map comes from the drive's manual and is entered in the configuration (`VfdModbusMap`);
nothing here knows a vendor's addresses. Two operations exist:

* `read_status`: the status word (FC03 or FC04) and the optional diagnostic registers. Reads only.
* `command(run)`: the run sequence or the stop value, written to the control register with FC06,
  one frame per value, in order, never retried. A frame without a valid echo stops the sequence; the
  caller reports how far it got. An echo proves the drive received the value, not that the motor
  turns: the status word, and on the installation the hardwired running input, prove that.

No speed reference, parameter, fault reset or broadcast is ever written. The same class runs over a
real `RtuBus` and over the simulated drive's register bus (`twin/vfd.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config.schema import VfdModbusMap, VfdRegister
from .rtu_errors import RtuError


@dataclass(frozen=True)
class VfdStatus:
    word: int
    running: bool
    fault: bool
    ready: bool | None
    diagnostics: dict[str, float | None] = field(default_factory=dict)
    diagnostic_errors: dict[str, str] = field(default_factory=dict)

    def view(self) -> dict:
        return {"status_word": f"0x{self.word:04X}", "running": self.running, "fault": self.fault,
                "ready": self.ready, **self.diagnostics,
                **({"diagnostic_errors": self.diagnostic_errors} if self.diagnostic_errors else {})}


class VfdCommandError(Exception):
    """A run or stop sequence that did not complete. `sent` frames got a valid echo before `cause`."""

    def __init__(self, sent: int, total: int, cause: RtuError):
        super().__init__(f"{sent} of {total} control value(s) echoed, then {cause.kind}: {cause}")
        self.sent, self.total, self.cause = sent, total, cause


def _value(raw: int, r: VfdRegister) -> float:
    if r.signed and raw >= 0x8000:
        raw -= 0x10000
    return raw * r.scale


class VfdDevice:
    def __init__(self, bus, m: VfdModbusMap):
        self.bus, self.m = bus, m

    def read_status(self) -> VfdStatus:
        m = self.m
        word = self.bus.read_registers(m.status_function, m.address, m.status_register, 1)[0]
        diag: dict[str, float | None] = {}
        errs: dict[str, str] = {}
        for name in ("frequency", "current", "fault_code"):
            r: VfdRegister | None = getattr(m, name)
            if r is None:
                continue
            try:     # a diagnostic that does not answer never hides the status word
                diag[name] = _value(self.bus.read_registers(r.function, m.address, r.address, 1)[0], r)
            except RtuError as e:
                diag[name], errs[name] = None, f"{e.kind}: {e}"
        return VfdStatus(word, bool(word & m.running_mask), bool(word & m.fault_mask),
                         None if m.ready_mask is None else bool(word & m.ready_mask), diag, errs)

    def values(self, run: bool) -> list[int]:
        return list(self.m.run_sequence) if run else [self.m.stop_value]

    def command(self, run: bool, *, before_write=None) -> int:
        """Write the run sequence or the stop value; returns the number of frames echoed."""
        vals = self.values(run)
        for i, v in enumerate(vals):
            try:
                self.bus.write_single_register(self.m.address, self.m.control_register, v,
                                               before_write=before_write)
            except RtuError as e:
                raise VfdCommandError(i, len(vals), e) from e
        return len(vals)
