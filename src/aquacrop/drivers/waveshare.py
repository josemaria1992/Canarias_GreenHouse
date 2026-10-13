"""Fixed Waveshare V2 operations (brief §6). Register facts: docs/vendor_protocol.md.

This module is the only place that knows register addresses. It offers no way to write IDs, baud
rates, modes, all-on/all-off, toggle or flash registers; those operations do not exist here.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config.catalog import AIMode, ModelId, RelayMode
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

    # relay (D)
    def read_discrete_inputs(self) -> list[bool]:
        self._need(ModelId.WAVESHARE_RELAY_D)
        return self.bus.read_bits(FC_READ_DISCRETE_INPUTS, self.address, 0, CHANNELS)

    def read_coils(self) -> list[bool]:
        self._need(ModelId.WAVESHARE_RELAY_D)
        return self.bus.read_bits(FC_READ_COILS, self.address, 0, CHANNELS)

    def read_relay_modes(self) -> RelayModes:
        self._need(ModelId.WAVESHARE_RELAY_D)
        return RelayModes(tuple(self.bus.read_registers(FC_READ_HOLDING_REGISTERS, self.address,
                                                        MODE_REGISTER_START, CHANNELS)))

    def write_coil(self, channel: int, value: bool, *, before_write=None) -> None:
        self._need(ModelId.WAVESHARE_RELAY_D)
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

    def _need(self, model: ModelId) -> None:
        if self.model != model:
            raise RtuRefused(f"operation not supported by {self.model.value}")
