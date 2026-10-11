"""Synthetic DEMO twin parameters (brief §7).

Every value here is a labelled demonstration proposal. None is a confirmed engineering fact, and
real-hardware profiles refuse to load a twin section marked DEMO as production configuration.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TankParams(_Strict):
    capacity_L: float = Field(gt=0)
    initial_L: float = Field(ge=0)
    high_level_fraction: float | None = Field(default=None, gt=0, le=1)
    low_level_fraction: float | None = Field(default=None, ge=0, lt=1)

    @model_validator(mode="after")
    def _initial_fits(self) -> "TankParams":
        if self.initial_L > self.capacity_L:
            raise ValueError("initial_L exceeds capacity_L")
        return self


class SectorParams(_Strict):
    sector_id: str
    channels: list[int] = Field(min_length=1)
    rated_flow_L_min: float = Field(gt=0)


class AutoParams(_Strict):
    irrigate_s: float = Field(default=15.0, gt=0)
    pause_s: float = Field(default=10.0, ge=0)
    flow_grace_s: float = Field(default=3.0, ge=0)
    min_flow_L_min: float = Field(default=6.0, gt=0)
    no_flow_sustain_s: float = Field(default=2.0, ge=0)
    valve_feedback_timeout_s: float = Field(default=5.0, gt=0)
    pump_feedback_timeout_s: float = Field(default=3.0, gt=0)
    stop_valve_delay_s: float = Field(default=1.0, ge=0)
    min_reservoir_L: float = Field(default=50.0, ge=0)
    selected_pump: str = "P1"
    sector_order: list[str] = Field(default_factory=lambda: ["S1", "S2"])


class TwinParams(_Strict):
    provenance: Literal["DEMO", "PROPOSED", "VERIFIED"] = "DEMO"
    dt_s: float = Field(default=0.1, gt=0, le=1.0)
    seed: int = 42
    noise_std_fraction: float = Field(default=0.0, ge=0, le=0.05)
    reservoir: TankParams = TankParams(capacity_L=1000, initial_L=600,
                                       high_level_fraction=0.95, low_level_fraction=0.10)
    return_tank: TankParams = TankParams(capacity_L=200, initial_L=0, high_level_fraction=0.9)
    nutrient_tanks_L: dict[str, float] = Field(default_factory=lambda: {"A": 20.0, "B": 20.0, "ACID": 20.0})
    pumps: list[str] = Field(default_factory=lambda: ["P1", "P2", "P3"], min_length=1)
    sectors: list[SectorParams] = Field(default_factory=lambda: [
        SectorParams(sector_id="S1", channels=[1, 2, 3, 4, 5, 6], rated_flow_L_min=12.0),
        SectorParams(sector_id="S2", channels=[7, 8, 9, 10, 11, 12, 13], rated_flow_L_min=12.0),
    ])
    pump_tau_s: float = Field(default=1.0, ge=0)
    valve_tau_s: float = Field(default=1.0, ge=0)
    return_transit_s: float = Field(default=10.0, ge=0)
    return_fraction: float = Field(default=0.8, ge=0, le=1)
    return_transfer_enabled: bool = True
    return_transfer_L_min: float = Field(default=12.0, ge=0)
    fill_flow_L_min: float = Field(default=30.0, ge=0)
    run_feedback_threshold: float = Field(default=0.5, gt=0, lt=1)
    valve_open_threshold: float = Field(default=0.9, gt=0, le=1)
    valve_closed_threshold: float = Field(default=0.1, ge=0, lt=1)
    level_transmitter_span: dict[str, float] = Field(
        default_factory=lambda: {"reservoir": 1000.0, "return_tank": 200.0})
    flow_transmitter_span_L_min: float = Field(default=30.0, gt=0)
    auto: AutoParams = AutoParams()

    @model_validator(mode="after")
    def _check(self) -> "TwinParams":
        chans = [c for s in self.sectors for c in s.channels]
        if len(chans) != len(set(chans)):
            raise ValueError("a channel belongs to more than one sector")
        ids = [s.sector_id for s in self.sectors]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate sector_id")
        if len(set(self.pumps)) != len(self.pumps):
            raise ValueError("duplicate pump id")
        if self.auto.selected_pump not in self.pumps:
            raise ValueError("auto.selected_pump is not a configured pump")
        if any(s not in ids for s in self.auto.sector_order):
            raise ValueError("auto.sector_order names an unknown sector")
        return self

    # Signals the twin can drive (DO) or observe (AI/DI). Used to validate virtual mappings.
    def actuator_signals(self) -> set[str]:
        sig = {f"pump.{p}.cmd" for p in self.pumps}
        sig |= {f"valve.{s.sector_id}.cmd" for s in self.sectors}
        sig.add("valve.FILL.cmd")
        return sig

    def digital_observations(self) -> set[str]:
        sig = {f"pump.{p}.running" for p in self.pumps}
        sig |= {f"valve.{s.sector_id}.open_limit" for s in self.sectors}
        sig |= {"reservoir.high_level", "reservoir.low_level", "return_tank.high_level"}
        return sig

    def analog_observations(self) -> set[str]:
        sig = {"reservoir.level_transmitter", "return_tank.level_transmitter"}
        sig |= {f"sector.{s.sector_id}.flow_transmitter" for s in self.sectors}
        return sig
