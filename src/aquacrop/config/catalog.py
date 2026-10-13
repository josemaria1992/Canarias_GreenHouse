"""Catalogue of exactly-identified device models (brief §6).

Only models listed here can appear in an inventory. Register facts come from the indexed vendor V2
tables (see docs/vendor_protocol.md); they are not a physically validated firmware map.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class ModelId(str, enum.Enum):
    WAVESHARE_RELAY_D = "WAVESHARE_MODBUS_RTU_RELAY_D"
    WAVESHARE_AI_8CH = "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH"
    WAVESHARE_IO_8CH = "WAVESHARE_MODBUS_RTU_IO_8CH"


class Capability(str, enum.Enum):
    READ_COILS = "FC01_READ_COILS"
    READ_DISCRETE_INPUTS = "FC02_READ_DISCRETE_INPUTS"
    READ_MODE_REGISTERS = "FC03_READ_MODE_REGISTERS"
    READ_INPUT_REGISTERS = "FC04_READ_INPUT_REGISTERS"
    WRITE_SINGLE_COIL = "FC05_WRITE_SINGLE_COIL"
    WRITE_MULTIPLE_COILS = "FC0F_WRITE_MULTIPLE_COILS"


@dataclass(frozen=True)
class ModelSpec:
    model: ModelId
    sku: str
    vendor_name: str
    ai: int
    di: int
    do: int
    capabilities: frozenset[Capability]
    mode_register_start: int = 0x1000
    output_type: str | None = None
    output_current_limit_ma: int | None = None


CATALOG: dict[ModelId, ModelSpec] = {
    ModelId.WAVESHARE_RELAY_D: ModelSpec(
        ModelId.WAVESHARE_RELAY_D, "26517", "Waveshare Modbus RTU Relay (D)", ai=0, di=8, do=8,
        capabilities=frozenset({Capability.READ_COILS, Capability.READ_DISCRETE_INPUTS,
                                Capability.READ_MODE_REGISTERS, Capability.WRITE_SINGLE_COIL,
                                Capability.WRITE_MULTIPLE_COILS}), output_type="RELAY_CONTACT"),
    ModelId.WAVESHARE_AI_8CH: ModelSpec(
        ModelId.WAVESHARE_AI_8CH, "25821", "Waveshare Modbus RTU Analog Input 8CH", ai=8, di=0, do=0,
        capabilities=frozenset({Capability.READ_INPUT_REGISTERS, Capability.READ_MODE_REGISTERS})),
    ModelId.WAVESHARE_IO_8CH: ModelSpec(
        ModelId.WAVESHARE_IO_8CH, "26244", "Waveshare Modbus RTU IO 8CH (open-drain outputs)", ai=0, di=8, do=8,
        capabilities=frozenset({Capability.READ_COILS, Capability.READ_DISCRETE_INPUTS,
                                Capability.READ_MODE_REGISTERS, Capability.WRITE_SINGLE_COIL,
                                Capability.WRITE_MULTIPLE_COILS}), output_type="OPEN_DRAIN_SINK",
        output_current_limit_ma=500),
}

# Identical indexed V2 digital-channel protocol, different electrical output interfaces.
DIGITAL_IO_MODELS = frozenset({ModelId.WAVESHARE_RELAY_D, ModelId.WAVESHARE_IO_8CH})


class RelayMode(enum.IntEnum):
    """Relay (D) / IO 8CH output control modes (FC03 @0x1000)."""
    NORMAL = 0
    LINKAGE = 1
    TOGGLE = 2
    EDGE = 3


class AIMode(str, enum.Enum):
    """AI 8CH data type register (FC03 @0x1000). Physical range depends on the hardware variant."""
    VOLTAGE_FULL = "0"      # 0-5 V (standard) / 0-10 V (B variant), value in mV
    VOLTAGE_OFFSET = "1"    # 1-5 V / 2-10 V, mV
    CURRENT_0_20 = "2"      # 0-20 mA, value in uA
    CURRENT_4_20 = "3"      # 4-20 mA, value in uA
    RAW_CODE = "4"          # raw ADC code
    UNKNOWN = "UNKNOWN"     # not observed / not verified: unscaled and write-locked

    @property
    def raw_unit(self) -> str | None:
        return {"0": "mV", "1": "mV", "2": "uA", "3": "uA", "4": "code"}.get(self.value)

    @classmethod
    def from_register(cls, value: int) -> "AIMode":
        return cls(str(value)) if 0 <= value <= 4 else cls.UNKNOWN


class AIVariant(str, enum.Enum):
    STANDARD = "STANDARD"   # 0-5 V / 1-5 V
    B = "B"                 # 0-10 V / 2-10 V
    UNKNOWN = "UNKNOWN"
