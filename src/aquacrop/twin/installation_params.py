"""Parameters of the full-installation twin (brief §2, §7; ADR-0013).

Two kinds of values live here and are kept apart:

* `InstallationFacts` — what the owner confirmed on 2026-10-12 (tank sizes, equipment counts, channel
  layout). Approximate where the owner said "approximately". Provenance OWNER_CONFIRMED.
* `InstallationDynamics`, `ChemistryParams` — synthetic DEMO dynamics (flow rates, delays, switch
  levels, transmitter spans, chemistry). None of them is an engineering fact; real-hardware profiles
  refuse a twin marked DEMO.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .chemistry import ChemistryParams

STOCKS = ("A", "B", "ACID")
TANKS = ("MAIN", "SUMP") + STOCKS


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChannelGeometry(_Strict):
    """Owner-stated approximate geometry (about 85 x 0.30 x 0.15 m, 0.05 m retained). Unverified: a
    transparent model parameter, not a measurement."""
    length_m: float = Field(default=85.0, gt=0)
    width_m: float = Field(default=0.30, gt=0)
    height_m: float = Field(default=0.15, gt=0)
    retained_depth_m: float = Field(default=0.05, ge=0)

    @model_validator(mode="after")
    def _depth(self) -> "ChannelGeometry":
        if self.retained_depth_m >= self.height_m:
            raise ValueError("retained_depth_m must be below the channel height")
        return self

    @property
    def retained_L(self) -> float:
        return self.length_m * self.width_m * self.retained_depth_m * 1000.0

    @property
    def capacity_L(self) -> float:
        return self.length_m * self.width_m * self.height_m * 1000.0


class SectorFacts(_Strict):
    sector_id: str = Field(pattern=r"^S[0-9]{1,2}$")
    channels: list[int] = Field(min_length=1)
    auto_pump: str          # main pump that serves this sector in AUTO (owner: P1->S1, P2->S2)


class InstallationFacts(_Strict):
    provenance: Literal["OWNER_CONFIRMED"] = "OWNER_CONFIRMED"
    confirmed: str = "owner answers through 2026-10-12"
    main_tank_L: float = Field(default=20000.0, gt=0)
    stock_tanks_L: dict[str, float] = Field(default_factory=lambda: {"A": 5000.0, "B": 5000.0, "ACID": 5000.0})
    sump_L: float = Field(default=5000.0, gt=0)          # "approximately 5 m3"
    channel: ChannelGeometry = ChannelGeometry()
    outlets_per_channel: int = Field(default=4, ge=1, le=16)
    sectors: list[SectorFacts] = Field(default_factory=lambda: [
        SectorFacts(sector_id="S1", channels=[1, 2, 3, 4, 5, 6], auto_pump="MP1"),
        SectorFacts(sector_id="S2", channels=[7, 8, 9, 10, 11, 12, 13], auto_pump="MP2")])
    main_pumps: list[str] = Field(default_factory=lambda: ["MP1", "MP2"], min_length=1)
    shared_drive: str = "MAIN"                            # one VFD, mutually exclusive motor selection
    return_pumps: list[str] = Field(default_factory=lambda: ["RP1", "RP2"], min_length=1)
    agitators: int = Field(default=0, ge=0, le=0)         # none confirmed; the diagram's agitators are removed

    @model_validator(mode="after")
    def _check(self) -> "InstallationFacts":
        if set(self.stock_tanks_L) != set(STOCKS):
            raise ValueError(f"stock_tanks_L must name exactly {STOCKS}")
        chans = [c for s in self.sectors for c in s.channels]
        if len(chans) != len(set(chans)):
            raise ValueError("a channel belongs to more than one sector")
        if sorted(chans) != list(range(1, len(chans) + 1)):
            raise ValueError("channels must be numbered 1..N without gaps")
        if len({s.sector_id for s in self.sectors}) != len(self.sectors):
            raise ValueError("duplicate sector_id")
        if len(set(self.main_pumps)) != len(self.main_pumps) or len(set(self.return_pumps)) != len(self.return_pumps):
            raise ValueError("duplicate pump id")
        for s in self.sectors:
            if s.auto_pump not in self.main_pumps:
                raise ValueError(f"sector {s.sector_id}: auto_pump {s.auto_pump} is not a main pump")
        return self

    @property
    def channel_count(self) -> int:
        return sum(len(s.channels) for s in self.sectors)

    def tank_capacity(self, tank: str) -> float:
        if tank == "MAIN":
            return self.main_tank_L
        if tank == "SUMP":
            return self.sump_L
        return self.stock_tanks_L[tank]


class LevelSwitches(_Strict):
    """DEMO switch points (L). The real switch heights are commissioning values."""
    main_high_high_L: float = 19500.0
    main_low_low_L: float = 1500.0
    sump_high_L: float = 4500.0
    sump_low_L: float = 400.0
    stock_low_L: float = 250.0            # per stock tank (switch not bound in the DEMO proposal)


class InstallationDynamics(_Strict):
    """Synthetic DEMO dynamics. Pump curves, delays and rates are placeholders for qualitative testing."""
    provenance: Literal["DEMO", "PROPOSED", "VERIFIED"] = "DEMO"
    initial_L: dict[str, float] = Field(default_factory=lambda: {
        "MAIN": 12000.0, "SUMP": 1200.0, "A": 3000.0, "B": 3000.0, "ACID": 2000.0})
    # initial channel inventory as a fraction of the retained volume (explicit: not empty, not full)
    initial_channel_fraction_of_retained: float = Field(default=1.0, ge=0, le=3)
    main_pump_rated_L_min: float = Field(default=150.0, gt=0)          # per pump at full drive speed
    sector_rated_L_min: dict[str, float] = Field(default_factory=lambda: {"S1": 150.0, "S2": 150.0})
    shutoff_pressure_bar: float = Field(default=3.0, gt=0)
    drive_accel_s: float = Field(default=3.0, ge=0)
    drive_decel_s: float = Field(default=3.0, ge=0)
    drive_running_threshold: float = Field(default=0.01, gt=0, lt=1)   # output speed reported as "running"
    contactor_s: float = Field(default=0.1, ge=0)                     # contactor close/open time
    contactor_interlock: bool = True     # PROPOSED hardwired K1/K2 mutual interlock (unverified design)
    valve_tau_s: float = Field(default=1.0, ge=0)
    valve_open_threshold: float = Field(default=0.9, gt=0, le=1)
    valve_closed_threshold: float = Field(default=0.1, ge=0, lt=1)
    pump_tau_s: float = Field(default=1.0, ge=0)
    pump_running_threshold: float = Field(default=0.5, gt=0, lt=1)
    supply_transit_s: float = Field(default=10.0, ge=0)               # header to channel inlets
    drain_transit_s: float = Field(default=60.0, ge=0)                # channel outlets to sump (gravity)
    channel_drain_tau_s: float = Field(default=120.0, gt=0)           # water above retained depth drains
    channel_uptake_L_min: float = Field(default=0.25, ge=0)           # per channel (plants), synthetic
    return_pump_rated_L_min: float = Field(default=250.0, gt=0)
    transfer_rated_L_min: dict[str, float] = Field(default_factory=lambda: {"A": 20.0, "B": 20.0, "ACID": 8.0})
    fill_rated_L_min: dict[str, float] = Field(default_factory=lambda: {
        "MAIN": 100.0, "A": 40.0, "B": 40.0, "ACID": 40.0})
    switches: LevelSwitches = LevelSwitches()
    # transmitter spans used by the DEMO observation model (raw 4-20 mA <-> engineering value)
    flow_span_L_min: dict[str, float] = Field(default_factory=lambda: {
        "FILL_MAIN": 150.0, "S1": 200.0, "S2": 200.0, "RETURN": 300.0, "TP_A": 40.0, "TP_B": 40.0,
        "TP_ACID": 16.0})
    pressure_span_bar: float = Field(default=6.0, gt=0)
    ph_span: tuple[float, float] = (0.0, 14.0)
    ec_span_mS_cm: float = Field(default=5.0, gt=0)
    noise_std_fraction: float = Field(default=0.0, ge=0, le=0.05)

    @model_validator(mode="after")
    def _check(self) -> "InstallationDynamics":
        missing = set(("MAIN", "SUMP") + STOCKS) - set(self.initial_L)
        if missing:
            raise ValueError(f"initial_L misses {sorted(missing)}")
        if set(self.transfer_rated_L_min) != set(STOCKS):
            raise ValueError(f"transfer_rated_L_min must name {STOCKS}")
        if set(self.fill_rated_L_min) != {"MAIN", *STOCKS}:
            raise ValueError("fill_rated_L_min must name MAIN, A, B and ACID")
        need = {"FILL_MAIN", "RETURN", *(f"TP_{s}" for s in STOCKS)}
        if not need <= set(self.flow_span_L_min):
            raise ValueError(f"flow_span_L_min must name {sorted(need)} and every sector")
        return self


class InstallationTwin(_Strict):
    """Twin section for `topology: installation`."""
    topology: Literal["installation"] = "installation"
    provenance: Literal["DEMO", "PROPOSED", "VERIFIED"] = "DEMO"
    dt_s: float = Field(default=0.1, gt=0, le=1.0)
    control_period_s: float = Field(default=0.5, gt=0, le=5.0)   # acquisition + controller scan (simulated time)
    seed: int = 42
    facts: InstallationFacts = InstallationFacts()
    dynamics: InstallationDynamics = InstallationDynamics()
    chemistry: ChemistryParams = ChemistryParams()

    @model_validator(mode="after")
    def _check(self) -> "InstallationTwin":
        ratio = self.control_period_s / self.dt_s
        if abs(ratio - round(ratio)) > 1e-6 or round(ratio) < 1:
            raise ValueError("control_period_s must be a whole multiple of dt_s")
        f, d = self.facts, self.dynamics
        for t in ("MAIN", "SUMP", *STOCKS):
            if d.initial_L[t] > f.tank_capacity(t):
                raise ValueError(f"initial_L[{t}] exceeds the tank capacity")
        if set(d.sector_rated_L_min) != {s.sector_id for s in f.sectors}:
            raise ValueError("sector_rated_L_min must name every sector")
        if not {s.sector_id for s in f.sectors} <= set(d.flow_span_L_min):
            raise ValueError("flow_span_L_min must name every sector")
        sw = d.switches
        if not (0 < sw.main_low_low_L < sw.main_high_high_L <= f.main_tank_L):
            raise ValueError("main switch levels must satisfy 0 < low_low < high_high <= capacity")
        if not (0 < sw.sump_low_L < sw.sump_high_L <= f.sump_L):
            raise ValueError("sump switch levels must satisfy 0 < low < high <= capacity")
        return self

    @property
    def control_stride(self) -> int:
        return int(round(self.control_period_s / self.dt_s))

    # ------------------------------------------------------------------ logical signal catalogue
    def actuator_signals(self) -> set[str]:
        f = self.facts
        sig = {f"drive.{f.shared_drive}.run"}
        sig |= {f"contactor.{p}.cmd" for p in f.main_pumps}
        sig |= {f"valve.{s.sector_id}.cmd" for s in f.sectors}
        sig |= {f"pump.{p}.cmd" for p in f.return_pumps}
        sig |= {f"pump.TP_{s}.cmd" for s in STOCKS}
        sig |= {f"valve.OUT_{s}.cmd" for s in STOCKS}
        sig |= {f"valve.FILL_{t}.cmd" for t in ("MAIN", *STOCKS)}
        return sig

    def digital_observations(self) -> set[str]:
        f = self.facts
        sig = {f"drive.{f.shared_drive}.running", f"drive.{f.shared_drive}.fault", "safety.chain_ok"}
        sig |= {f"contactor.{p}.closed" for p in f.main_pumps}
        sig |= {f"valve.{s.sector_id}.open_limit" for s in f.sectors}
        sig |= {f"valve.{s.sector_id}.closed_limit" for s in f.sectors}
        sig |= {f"valve.{v}.open_limit" for v in [f"OUT_{s}" for s in STOCKS] + [f"FILL_{t}" for t in ("MAIN", *STOCKS)]}
        sig |= {f"pump.{p}.running" for p in f.return_pumps}
        sig |= {f"pump.TP_{s}.running" for s in STOCKS}
        sig |= {"tank.MAIN.high_high", "tank.MAIN.low_low", "tank.SUMP.high", "tank.SUMP.low"}
        sig |= {f"tank.{s}.low" for s in STOCKS}
        return sig

    def analog_observations(self) -> set[str]:
        sig = {f"tank.{t}.level" for t in TANKS}
        sig |= {"main.ph", "main.ec", "main.temperature"}
        sig |= {f"flow.{k}" for k in self.dynamics.flow_span_L_min}
        sig |= {f"pressure.{s.sector_id}" for s in self.facts.sectors}
        return sig
