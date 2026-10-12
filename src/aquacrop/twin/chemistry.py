"""Synthetic pH/EC response fixture for the installation twin (brief §5, §7; ADR-0013).

LABEL: SYNTHETIC FIXTURE `synthetic-carbonate-ec-v1`. It exists to exercise controllers and guards
qualitatively (staged dosing, settling, undershoot, no response). It is not a calibrated or predictive
chemical model, it prescribes no physical amounts, and none of its constants is an owner or supplier fact.
A calibrated model can replace it through `ChemistryModel` once measured data exist.

Water is tracked as *parcels*: a volume plus amounts of a few species. Moving water moves species in
proportion, so mixing is additive in amounts and never averages pH linearly:

    A, B   litres of prepared stock A / B (strength 1 = one "prepared litre")
    ALK    alkalinity, meq (acid adds negative alkalinity)
    CT     dissolved inorganic carbon, mmol
    EC0    background conductivity, (mS/cm)·L  (from mains water)
    ANION  acid anion (e.g. nitrate), meq — contributes to EC only

pH comes from a single carbonate equilibrium (pKa1 6.35) solved from alkalinity and total carbon; EC is
a linear sum of species concentrations. Both are evaluated on the probe's *local* composition, which lags
the bulk tank composition (mixing/settling), not on the bulk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

FIXTURE_ID = "synthetic-carbonate-ec-v1"
SPECIES = ("A", "B", "ALK", "CT", "EC0", "ANION")
PKA1 = 6.35
KA1 = 10 ** -PKA1


class ChemistryParams(BaseModel):
    """Every value is a labelled DEMO constant of the synthetic fixture."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    fixture: str = FIXTURE_ID
    provenance: str = "DEMO"
    mains_alkalinity_meq_L: float = Field(default=3.0, ge=0, le=20)
    mains_ph: float = Field(default=7.8, ge=4, le=9.5)
    mains_ec_mS_cm: float = Field(default=0.35, ge=0, le=5)
    stock_strength: dict[str, float] = Field(default_factory=lambda: {"A": 1.0, "B": 1.0})
    acid_stock_meq_L: float = Field(default=1000.0, gt=0, le=20000)   # synthetic "1 N" acid stock
    ec_per_stock_fraction: dict[str, float] = Field(default_factory=lambda: {"A": 100.0, "B": 100.0})  # mS/cm per L/L
    ec_per_anion_meq_L: float = Field(default=0.1, ge=0)
    probe_tau_circulating_s: float = Field(default=180.0, gt=0)   # probe composition lag with flow through the tank
    probe_tau_still_s: float = Field(default=1800.0, gt=0)        # ... with no flow in or out of the tank
    degas_tau_s: float = Field(default=6 * 3600.0, gt=0)          # excess CO2 leaves slowly: pH drifts up
    uptake_solute_ratio: float = Field(default=0.7, ge=0, le=2)   # plants take A/B at 70 % of water concentration
    initial_main_ec_mS_cm: float = Field(default=1.6, ge=0, le=10)
    initial_main_ph: float = Field(default=6.1, ge=3, le=9)


def empty() -> dict[str, float]:
    return {k: 0.0 for k in SPECIES}


def mains_species(p: ChemistryParams, volume_L: float) -> dict[str, float]:
    """Species carried by `volume_L` of mains water (synthetic)."""
    alk = p.mains_alkalinity_meq_L
    h = 10 ** -p.mains_ph
    # C_T from the same charge balance used by ph(): Alk = C_T*Ka/(h+Ka) - h  (mol/L)
    ct = (alk / 1000 + h) * (h + KA1) / KA1 * 1000
    return {"A": 0.0, "B": 0.0, "ALK": alk * volume_L, "CT": ct * volume_L, "EC0": p.mains_ec_mS_cm * volume_L,
            "ANION": 0.0}


def ph_from(alk_meq_L: float, ct_mmol_L: float) -> float:
    """pH of a carbonate solution from alkalinity and total inorganic carbon (single equilibrium,
    carbonate ion and hydroxide neglected; adequate below about pH 8.3, the region this fixture is for)."""
    alk, ct = alk_meq_L / 1000.0, max(ct_mmol_L, 0.0) / 1000.0
    b = KA1 + alk
    disc = b * b - 4 * KA1 * (alk - ct)
    h = (-b + math.sqrt(max(disc, 0.0))) / 2
    if h <= 0:          # numerically alkaline beyond the fixture's range
        h = 1e-10
    return -math.log10(h)


def ec_from(p: ChemistryParams, conc: dict[str, float]) -> float:
    return (conc["EC0"] + sum(p.ec_per_stock_fraction.get(k, 0.0) * conc[k] for k in ("A", "B"))
            + p.ec_per_anion_meq_L * conc["ANION"])


def initial_main(p: ChemistryParams, volume_L: float) -> dict[str, float]:
    """A plausible starting solution: mains water acidified to initial_main_ph, with A and B (1:1)
    added to reach initial_main_ec. Synthetic, deterministic."""
    sp = mains_species(p, volume_L)
    if volume_L <= 0:
        return sp
    alk0, ct0 = sp["ALK"] / volume_L, sp["CT"] / volume_L
    # acid needed so that ph_from(alk0 - x, ct0) == initial_main_ph (CO2 kept: closed system)
    h = 10 ** -p.initial_main_ph
    alk_target = (ct0 / 1000 * KA1 / (h + KA1) - h) * 1000
    acid = max(0.0, alk0 - alk_target)
    sp["ALK"] -= acid * volume_L
    sp["ANION"] += acid * volume_L
    ec_now = ec_from(p, {k: v / volume_L for k, v in sp.items()})
    need = max(0.0, p.initial_main_ec_mS_cm - ec_now)
    per_frac = p.ec_per_stock_fraction["A"] * p.stock_strength["A"] + p.ec_per_stock_fraction["B"] * p.stock_strength["B"]
    frac = need / per_frac if per_frac else 0.0     # litres of each stock per litre of solution
    sp["A"] += frac * volume_L * p.stock_strength["A"]
    sp["B"] += frac * volume_L * p.stock_strength["B"]
    return sp


class ChemistryModel(Protocol):
    """Interface a later calibrated model implements (fixture id, pH and EC from a composition)."""
    fixture_id: str

    def ph(self, conc: dict[str, float]) -> float: ...

    def ec(self, conc: dict[str, float]) -> float: ...


@dataclass
class SyntheticChemistry:
    p: ChemistryParams
    fixture_id: str = FIXTURE_ID

    def ph(self, conc: dict[str, float]) -> float:
        return ph_from(conc["ALK"], conc["CT"])

    def ec(self, conc: dict[str, float]) -> float:
        return ec_from(self.p, conc)


@dataclass
class Probe:
    """The pH/EC probe's local composition (concentrations per litre), lagging the bulk tank.
    `tau` depends on whether water is flowing through the tank (circulation) — the fixture's stand-in
    for mixing. No mixing actuator exists or is implied."""
    conc: dict[str, float] = field(default_factory=empty)

    def follow(self, bulk: dict[str, float], dt: float, tau: float) -> None:
        k = 1.0 - math.exp(-dt / tau)
        for s in SPECIES:
            self.conc[s] += (bulk[s] - self.conc[s]) * k
