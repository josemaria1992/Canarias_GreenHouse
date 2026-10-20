"""Fixed Waveshare V2 operations (brief §6). Register facts: docs/vendor_protocol.md.

This module holds the register addresses the service uses. It offers no way to write IDs, baud rates,
modes, all-on/all-off, toggle or flash registers; those operations do not exist here. Only the
one-module-at-a-time setup command (`setup.py`, ADR-0016) writes a module's address and mode registers.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config.catalog import AIMode, DIGITAL_IO_MODELS, ModelId, RelayMode
from .rtu import (FC_READ_COILS, FC_READ_DISCRETE_INPUTS, FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS,
                  RtuBus, RtuRefused)

MODE_REGISTER_START = 0x1000
CHANNELS = 8

#: Read-only identification registers used by diagnostics only. Values are reported raw, unverified.
METADATA_REGISTERS = {"software_version": 0x8000, "device_address": 0x4000, "uart_parameters": 0x2000}


@dataclass(frozen=True)
class RelayModes:
    raw: tuple[int, ...]

    @property
    def all_normal(self) -> bool:
        return len(self.raw) == CHANNELS and all(v == RelayMode.NORMAL for v in self.raw)

    def describe(self) -> list[str]:
        out = []
        for v in self.raw:
            try:
                out.append(RelayMode(v).name)
            except ValueError:
                out.append(f"UNKNOWN({v})")
        return out


class WaveshareDevice:
    def __init__(self, bus: RtuBus, model: ModelId, address: int):
        self.bus, self.model, self.address = bus, model, address

    # Relay (D) and IO 8CH share V2 addresses, not their electrical load interfaces.
    def read_discrete_inputs(self) -> list[bool]:
        self._need(*DIGITAL_IO_MODELS)
        return self.bus.read_bits(FC_READ_DISCRETE_INPUTS, self.address, 0, CHANNELS)

    def read_coils(self) -> list[bool]:
        self._need(*DIGITAL_IO_MODELS)
        return self.bus.read_bits(FC_READ_COILS, self.address, 0, CHANNELS)

    def read_relay_modes(self) -> RelayModes:
        """Compatibility name; IO 8CH modes govern transistor outputs."""
        return self.read_output_modes()

    def read_output_modes(self) -> RelayModes:
        self._need(*DIGITAL_IO_MODELS)
        return RelayModes(tuple(self.bus.read_registers(FC_READ_HOLDING_REGISTERS, self.address,
                                                        MODE_REGISTER_START, CHANNELS)))

    def write_coil(self, channel: int, value: bool, *, before_write=None) -> None:
        self._need(*DIGITAL_IO_MODELS)
        if not 1 <= channel <= CHANNELS:
            raise ValueError("channel must be 1..8")
        self.bus.write_single_coil(self.address, channel - 1, value, before_write=before_write)

    # analog input 8CH
    def read_analog(self) -> list[int]:
        self._need(ModelId.WAVESHARE_AI_8CH)
        return self.bus.read_registers(FC_READ_INPUT_REGISTERS, self.address, 0, CHANNELS)

    def read_ai_modes(self) -> list[AIMode]:
        self._need(ModelId.WAVESHARE_AI_8CH)
        regs = self.bus.read_registers(FC_READ_HOLDING_REGISTERS, self.address, MODE_REGISTER_START, CHANNELS)
        return [AIMode.from_register(v) for v in regs]

    # identification (diagnostics only)
    def read_metadata(self) -> dict[str, int | str]:
        out: dict[str, int | str] = {}
        for name, reg in METADATA_REGISTERS.items():
            try:
                out[name] = self.bus.read_registers(FC_READ_HOLDING_REGISTERS, self.address, reg, 1)[0]
            except Exception as e:  # optional: many firmware revisions may not answer
                out[name] = f"unavailable ({getattr(e, 'kind', type(e).__name__)})"
        return out

    def _need(self, *models: ModelId) -> None:
        if self.model not in models:
            raise RtuRefused(f"operation not supported by {self.model.value}")
