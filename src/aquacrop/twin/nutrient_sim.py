"""Simulated nutrient preparation (0.7.3, PROG-10): what the synthetic chemistry fixture predicts when stock A, stock B
and acid are added to a tank of mains water, step by step, with the probe lagging the bulk.

LABEL: every result is SIMULATED with the synthetic fixture `synthetic-carbonate-ec-v1` (twin/chemistry.py). It is
not a recipe, it prescribes no physical amount, and none of its constants is an owner or supplier fact. Real dosing
stays blocked: a program over a NUTRIENT_PREPARATION block never starts on the real system (programs/engine.py).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .chemistry import ChemistryParams, Probe, SyntheticChemistry, mains_species

LABEL = ("SIMULATED: synthetic chemistry fixture synthetic-carbonate-ec-v1, DEMO constants. Not a recipe and not a "
         "dosing instruction; real dosing stays blocked.")


class Addition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_s: float = Field(ge=0, le=86400)
    what: Literal["A", "B", "ACID"]
    litres: float = Field(gt=0, le=1000)


class Preparation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volume_L: float = Field(gt=0, le=100000)          # mains water in the preparation tank
    additions: list[Addition] = Field(min_length=1, max_length=40)
    duration_s: float = Field(default=3600, gt=0, le=86400)
    circulating: bool = True                          # flow through the tank: the probe follows faster
    step_s: float = Field(default=30, ge=5, le=600)


def simulate(prep: Preparation, params: ChemistryParams | None = None) -> dict:
    p = params or ChemistryParams()
    chem = SyntheticChemistry(p)
    bulk = mains_species(p, prep.volume_L)
    vol = prep.volume_L
    probe = Probe({k: v / vol for k, v in bulk.items()})
    tau = p.probe_tau_circulating_s if prep.circulating else p.probe_tau_still_s
    todo = sorted(prep.additions, key=lambda a: a.at_s)
    out, t, i = [], 0.0, 0
    while t <= prep.duration_s + 1e-9:
        while i < len(todo) and todo[i].at_s <= t:
            a = todo[i]
            if a.what == "ACID":
                meq = a.litres * p.acid_stock_meq_L
                bulk["ALK"] -= meq
                bulk["ANION"] += meq
            else:
                bulk[a.what] += a.litres * p.stock_strength[a.what]
            vol += a.litres
            i += 1
        conc = {k: v / vol for k, v in bulk.items()}
        probe.follow(conc, prep.step_s if t else 0.0, tau)
        out.append({"t_s": round(t, 1), "ec_probe": round(chem.ec(probe.conc), 3), "ph_probe": round(chem.ph(probe.conc), 2),
                    "ec_bulk": round(chem.ec(conc), 3), "ph_bulk": round(chem.ph(conc), 2), "volume_L": round(vol, 2)})
        t += prep.step_s
    last = out[-1]
    return {"label": LABEL, "fixture": chem.fixture_id, "provenance": p.provenance, "samples": out,
            "final": {k: last[k] for k in ("ec_bulk", "ph_bulk", "ec_probe", "ph_probe", "volume_L")},
            "notes": ["The probe lags the bulk (mixing stand-in); a reading taken too early under-reads the addition.",
                      "Pressure, temperature, precipitation and real stock compositions are NOT MODELLED."]}
