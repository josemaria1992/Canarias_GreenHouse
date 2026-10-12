"""Process recipes: draft, validate, activate, version, roll back (brief §3; ADR-0014).

A recipe holds the operating settings of every process: main-tank thresholds and refill endpoint,
nutrient mode and quantities/targets, acid pH target and dosing bounds, irrigation sequence and
timeouts, sump-return thresholds and duty policy, stock-tank filling. Units are part of every field
name. Nothing here is a production value: the only preset is a labelled DEMO preset for simulation.

Lifecycle: a DRAFT can be edited freely and is validated on request; activation requires zero
validation issues and records actor and time; an ACTIVE or superseded version is immutable (editing
creates a new draft version); rollback activates an earlier validated version. Processes take the
active version at the start of their next cycle or batch and record it with every batch, transition
and command, so activation never changes a running operation.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain.model import utcnow


class _R(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, populate_by_name=True)


class MainLevels(_R):
    low_low_L: float | None = None            # irrigation/dosing dry-run guard (software)
    refill_trigger_L: float | None = None     # a refill batch latches when the level falls to this
    rearm_hysteresis_L: float | None = None   # a new batch needs the level back above trigger + this
    fill_stop_L: float | None = None          # every refill water/nutrient path stops at this level
    high_inhibit_L: float | None = None       # every inflow (refill, acid, return) is inhibited at this level
    min_dosing_L: float | None = None         # no nutrient or acid dosing below this main volume


class WaterPath(_R):
    target_L: float | None = None             # FIXED_ADDITIONS endpoint (measured by the mains flow meter)
    max_time_s: float | None = None
    min_flow_L_min: float | None = None
    flow_grace_s: float | None = None


class FixedNutrients(_R):
    a_L: float | None = None
    b_L: float | None = None
    ec_min_mS_cm: float | None = None         # EC validation band after mixing
    ec_max_mS_cm: float | None = None
    validation_settle_s: float | None = None  # mixing/measurement window before the EC check


class EcTarget(_R):
    ec_target_mS_cm: float | None = None
    ec_tolerance_mS_cm: float | None = None
    ratio_a_to_b: float | None = None         # litres of A per litre of B, kept on every step
    step_total_L: float | None = None         # A + B per staged dose
    max_a_L: float | None = None              # per batch
    max_b_L: float | None = None
    settle_s: float | None = None             # mixing/measurement window before EC is evaluated


class Refill(_R):
    trigger_debounce_s: float | None = None
    endpoint_mode: Literal["FIXED_ADDITIONS", "FINAL_LEVEL"] | None = None
    final_level_L: float | None = None        # FINAL_LEVEL endpoint for the mains water path
    water: WaterPath = WaterPath()
    nutrient_mode: Literal["FIXED_VOLUMES", "EC_TARGET"] | None = None
    fixed: FixedNutrients = FixedNutrients()
    ec: EcTarget = EcTarget()
    transfer_max_time_s: float | None = None  # per stock transfer (one path or one staged step)
    transfer_min_flow_L_min: float | None = None
    transfer_flow_grace_s: float | None = None
    stock_reserve_L: float | None = None      # never draw a stock tank below this


class Acid(_R):
    ph_target: float | None = None
    ph_tolerance: float | None = None         # dose only above target + tolerance
    undershoot_margin: float | None = None    # pH below target - margin: fault, operator action (no base dosing)
    dose_step_L: float | None = None
    settle_s: float | None = None
    max_batch_L: float | None = None
    max_day_L: float | None = None
    min_response_ph: float | None = None      # expected pH drop after a settled dose
    max_no_response: int | None = None        # consecutive doses without that drop -> fault
    dose_max_time_s: float | None = None
    min_flow_L_min: float | None = None
    flow_grace_s: float | None = None


class IrrigationStep(_R):
    sector: str
    duration_s: float | None = None


class Irrigation(_R):
    sequence: list[IrrigationStep] = Field(default_factory=list)   # AUTO pump per sector is an installation fact
    min_flow_L_min: float | None = None
    flow_grace_s: float | None = None
    no_flow_sustain_s: float | None = None
    valve_open_timeout_s: float | None = None
    valve_close_timeout_s: float | None = None
    open_lead_s: float | None = None          # owner: valve opens at least 1 s before the pump starts
    drive_start_timeout_s: float | None = None
    drive_stop_timeout_s: float | None = None
    contactor_timeout_s: float | None = None
    stop_settle_s: float | None = None        # dwell after stop is verified, before the valve closes
    max_manual_s: float | None = None


class Return(_R):
    sump_start_L: float | None = None
    sump_stop_L: float | None = None
    main_inhibit_L: float | None = None       # destination full: return holds (never pumps into a full main)
    main_resume_L: float | None = None        # ... and resumes only once the main level is back below this
    duty_policy: Literal["FIXED_RP1", "FIXED_RP2", "ALTERNATE_EACH_START"] | None = None
    max_runtime_s: float | None = None
    min_flow_L_min: float | None = None
    flow_grace_s: float | None = None
    run_feedback_timeout_s: float | None = None


class StockFill(_R):
    target_L: float | None = None
    auto_trigger_L: float | None = None
    high_inhibit_L: float | None = None
    max_time_s: float | None = None
    min_rise_L: float | None = None           # fill verified when the level rises this much ...
    rise_window_s: float | None = None        # ... within this time (no flow meter on stock fills)


class RecipeBody(_R):
    main: MainLevels = MainLevels()
    refill: Refill = Refill()
    acid: Acid = Acid()
    irrigation: Irrigation = Irrigation()
    return_: Return = Field(default=Return(), alias="return")
    stock_fill: dict[str, StockFill] = Field(default_factory=dict)


class Recipe(_R):
    recipe_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,47}$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    provenance: Literal["DEMO_PRESET", "OPERATOR"] = "OPERATOR"
    note: str | None = Field(default=None, max_length=500)
    body: RecipeBody = RecipeBody()

    @property
    def ref(self) -> str:
        return f"{self.recipe_id}@v{self.version}"

    def body_json(self) -> dict:
        return self.body.model_dump(mode="json", by_alias=True)

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body_json(), sort_keys=True).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------
# field table: units, ranges, labels (also served to the UI form)
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldRule:
    path: str
    label: str
    unit: str
    lo: float | None = None           # inclusive minimum
    hi: float | None = None           # inclusive maximum
    gt: float | None = None           # exclusive minimum (for "positive")
    required: str = "always"          # always | <mode condition> | optional
    help: str = ""
    options: tuple[str, ...] = ()     # enumerated (text) fields


def _rules(cap: dict[str, float]) -> list[FieldRule]:
    M, S = cap["MAIN"], cap["SUMP"]
    st = min(cap["A"], cap["B"], cap["ACID"])
    R = FieldRule
    return [
        R("main.low_low_L", "Main dry-run guard", "L", lo=0, hi=M),
        R("main.refill_trigger_L", "Refill trigger level", "L", lo=0, hi=M),
        R("main.rearm_hysteresis_L", "Refill re-arm hysteresis", "L", gt=0, hi=M),
        R("main.fill_stop_L", "Refill stop level", "L", lo=0, hi=M),
        R("main.high_inhibit_L", "High-level inflow inhibit", "L", lo=0, hi=M),
        R("main.min_dosing_L", "Minimum volume for dosing", "L", lo=0, hi=M),
        R("refill.trigger_debounce_s", "Trigger debounce", "s", lo=0, hi=3600),
        R("refill.endpoint_mode", "Refill endpoint", "", help="FIXED_ADDITIONS or FINAL_LEVEL",
          options=("FIXED_ADDITIONS", "FINAL_LEVEL")),
        R("refill.final_level_L", "Final operating level", "L", lo=0, hi=M, required="refill.endpoint_mode=FINAL_LEVEL"),
        R("refill.water.target_L", "Mains water addition", "L", gt=0, hi=M,
          required="refill.endpoint_mode=FIXED_ADDITIONS"),
        R("refill.water.max_time_s", "Mains water maximum time", "s", gt=0, hi=86400),
        R("refill.water.min_flow_L_min", "Mains water minimum flow", "L/min", gt=0, hi=10000),
        R("refill.water.flow_grace_s", "Mains water flow grace", "s", lo=0, hi=600),
        R("refill.nutrient_mode", "Nutrient mode", "", help="FIXED_VOLUMES or EC_TARGET",
          options=("FIXED_VOLUMES", "EC_TARGET")),
        R("refill.fixed.a_L", "Stock A addition", "L", lo=0, hi=st, required="refill.nutrient_mode=FIXED_VOLUMES"),
        R("refill.fixed.b_L", "Stock B addition", "L", lo=0, hi=st, required="refill.nutrient_mode=FIXED_VOLUMES"),
        R("refill.fixed.ec_min_mS_cm", "EC validation minimum", "mS/cm", gt=0, hi=20,
          required="refill.nutrient_mode=FIXED_VOLUMES"),
        R("refill.fixed.ec_max_mS_cm", "EC validation maximum", "mS/cm", gt=0, hi=20,
          required="refill.nutrient_mode=FIXED_VOLUMES"),
        R("refill.fixed.validation_settle_s", "EC validation settling window", "s", gt=0, hi=86400,
          required="refill.nutrient_mode=FIXED_VOLUMES"),
        R("refill.ec.ec_target_mS_cm", "EC target", "mS/cm", gt=0, hi=20, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.ec.ec_tolerance_mS_cm", "EC tolerance", "mS/cm", gt=0, hi=5, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.ec.ratio_a_to_b", "A:B ratio (A per B)", "L/L", gt=0, hi=100, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.ec.step_total_L", "A+B per staged dose", "L", gt=0, hi=st, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.ec.max_a_L", "Stock A maximum per batch", "L", gt=0, hi=st, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.ec.max_b_L", "Stock B maximum per batch", "L", gt=0, hi=st, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.ec.settle_s", "EC settling window", "s", gt=0, hi=86400, required="refill.nutrient_mode=EC_TARGET"),
        R("refill.transfer_max_time_s", "Stock transfer maximum time", "s", gt=0, hi=86400),
        R("refill.transfer_min_flow_L_min", "Stock transfer minimum flow", "L/min", gt=0, hi=1000),
        R("refill.transfer_flow_grace_s", "Stock transfer flow grace", "s", lo=0, hi=600),
        R("refill.stock_reserve_L", "Stock reserve (never drawn)", "L", lo=0, hi=st),
        R("acid.ph_target", "pH target", "pH", lo=3, hi=9, help="owner preference about 6; adjustable"),
        R("acid.ph_tolerance", "pH tolerance (dose above target + this)", "pH", gt=0, hi=2),
        R("acid.undershoot_margin", "pH undershoot margin", "pH", gt=0, hi=3),
        R("acid.dose_step_L", "Acid dose step", "L", gt=0, hi=st),
        R("acid.settle_s", "pH settling window", "s", gt=0, hi=86400),
        R("acid.max_batch_L", "Acid maximum per batch", "L", gt=0, hi=st),
        R("acid.max_day_L", "Acid maximum per 24 h", "L", gt=0, hi=st),
        R("acid.min_response_ph", "Expected pH drop per dose", "pH", gt=0, hi=2),
        R("acid.max_no_response", "Doses without response before fault", "", lo=1, hi=10),
        R("acid.dose_max_time_s", "Acid dose maximum time", "s", gt=0, hi=3600),
        R("acid.min_flow_L_min", "Acid transfer minimum flow", "L/min", gt=0, hi=1000),
        R("acid.flow_grace_s", "Acid transfer flow grace", "s", lo=0, hi=600),
        R("irrigation.min_flow_L_min", "Sector minimum flow", "L/min", gt=0, hi=10000),
        R("irrigation.flow_grace_s", "Flow verification grace", "s", lo=0, hi=600),
        R("irrigation.no_flow_sustain_s", "No-flow confirmation time", "s", lo=0, hi=600),
        R("irrigation.valve_open_timeout_s", "Valve open timeout", "s", gt=0, hi=600),
        R("irrigation.valve_close_timeout_s", "Valve close timeout", "s", gt=0, hi=600),
        R("irrigation.open_lead_s", "Valve lead before pump start", "s", lo=1, hi=600,
          help="owner: the valve opens at least one second before the pump starts"),
        R("irrigation.drive_start_timeout_s", "Drive start timeout", "s", gt=0, hi=600),
        R("irrigation.drive_stop_timeout_s", "Drive stop timeout", "s", gt=0, hi=600),
        R("irrigation.contactor_timeout_s", "Pump selection timeout", "s", gt=0, hi=60),
        R("irrigation.stop_settle_s", "Dwell after verified stop", "s", lo=0, hi=60),
        R("irrigation.max_manual_s", "Maximum MANUAL irrigation", "s", gt=0, hi=86400),
        R("return.sump_start_L", "Return start (sump)", "L", lo=0, hi=S),
        R("return.sump_stop_L", "Return stop (sump)", "L", lo=0, hi=S),
        R("return.main_inhibit_L", "Return destination-full inhibit (main)", "L", lo=0, hi=M),
        R("return.main_resume_L", "Return resume below (main)", "L", lo=0, hi=M),
        R("return.duty_policy", "Return pump duty policy", "", help="FIXED_RP1, FIXED_RP2 or ALTERNATE_EACH_START",
          options=("FIXED_RP1", "FIXED_RP2", "ALTERNATE_EACH_START")),
        R("return.max_runtime_s", "Return maximum run time", "s", gt=0, hi=86400),
        R("return.min_flow_L_min", "Return minimum flow", "L/min", gt=0, hi=10000),
        R("return.flow_grace_s", "Return flow grace", "s", lo=0, hi=600),
        R("return.run_feedback_timeout_s", "Return run feedback timeout", "s", gt=0, hi=600),
    ] + [r for s in ("A", "B", "ACID") for r in (
        R(f"stock_fill.{s}.target_L", f"Stock {s} fill target", "L", gt=0, hi=cap[s]),
        R(f"stock_fill.{s}.auto_trigger_L", f"Stock {s} auto-fill trigger", "L", lo=0, hi=cap[s]),
        R(f"stock_fill.{s}.high_inhibit_L", f"Stock {s} high inhibit", "L", gt=0, hi=cap[s]),
        R(f"stock_fill.{s}.max_time_s", f"Stock {s} fill maximum time", "s", gt=0, hi=86400),
        R(f"stock_fill.{s}.min_rise_L", f"Stock {s} fill verification rise", "L", gt=0, hi=cap[s]),
        R(f"stock_fill.{s}.rise_window_s", f"Stock {s} fill verification window", "s", gt=0, hi=3600))]


def _get(body: dict, path: str) -> Any:
    cur: Any = body
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


@dataclass
class Issue:
    path: str
    message: str

    def to_dict(self) -> dict:
        return {"path": self.path, "message": self.message}


def validate_body(body: RecipeBody, capacities: dict[str, float], sectors: list[str]) -> list[Issue]:
    """Every check a recipe must pass before activation: units/ranges, required fields for the selected
    modes, ordered thresholds, usable capacity, incompatible endpoints."""
    b = body.model_dump(mode="json", by_alias=True)
    out: list[Issue] = []
    for r in _rules(capacities):
        v = _get(b, r.path)
        need = r.required == "always"
        if "=" in r.required:
            k, want = r.required.split("=")
            need = _get(b, k) == want
        if v is None:
            if need:
                out.append(Issue(r.path, f"{r.label} is required"))
            continue
        if isinstance(v, str):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            out.append(Issue(r.path, f"{r.label} must be a finite number ({r.unit})"))
            continue
        if r.lo is not None and v < r.lo:
            out.append(Issue(r.path, f"{r.label} must be at least {r.lo:g} {r.unit}"))
        if r.gt is not None and v <= r.gt:
            out.append(Issue(r.path, f"{r.label} must be greater than {r.gt:g} {r.unit}"))
        if r.hi is not None and v > r.hi:
            out.append(Issue(r.path, f"{r.label} must not exceed {r.hi:g} {r.unit}"))
    if out:
        return out     # ordering checks only make sense on numbers that passed the range checks
    m, rf, ac, ir, rt = body.main, body.refill, body.acid, body.irrigation, body.return_
    M = capacities["MAIN"]

    def order(path: str, names: list[tuple[str, float | None]]) -> None:
        vals = [(n, v) for n, v in names if v is not None]
        for (n1, v1), (n2, v2) in zip(vals, vals[1:]):
            if not v1 < v2:
                out.append(Issue(path, f"thresholds must be ordered: {n1} ({v1:g} L) < {n2} ({v2:g} L)"))

    order("main", [("dry-run guard", m.low_low_L), ("refill trigger", m.refill_trigger_L),
                   ("trigger + re-arm hysteresis", (m.refill_trigger_L or 0) + (m.rearm_hysteresis_L or 0)),
                   ("refill stop", m.fill_stop_L), ("high inhibit", m.high_inhibit_L), ("capacity", M + 1e-9)])
    if m.min_dosing_L is not None and m.min_dosing_L > (m.fill_stop_L or M):
        out.append(Issue("main.min_dosing_L", "minimum dosing volume is above the refill stop level"))
    if rf.endpoint_mode == "FINAL_LEVEL":
        if rf.water.target_L is not None:
            out.append(Issue("refill.water.target_L", "a fixed water addition is incompatible with the FINAL_LEVEL "
                                                      "endpoint (the final level decides the water volume); clear it"))
        lo_ok = (m.refill_trigger_L or 0) + (m.rearm_hysteresis_L or 0)
        if rf.final_level_L is not None and not (lo_ok <= rf.final_level_L <= (m.fill_stop_L or M)):
            out.append(Issue("refill.final_level_L", f"final level must lie between trigger + hysteresis ({lo_ok:g} L) "
                                                     f"and the refill stop level ({m.fill_stop_L:g} L)"))
    if rf.endpoint_mode == "FIXED_ADDITIONS":
        if rf.final_level_L is not None:
            out.append(Issue("refill.final_level_L", "a final level is incompatible with FIXED_ADDITIONS; clear it"))
        adds = (rf.water.target_L or 0) + (_nutrient_max(rf) or 0)
        room = (m.fill_stop_L or M) - (m.refill_trigger_L or 0)
        if adds > room:
            out.append(Issue("refill.water.target_L", f"water + maximum nutrient additions ({adds:g} L) exceed the usable "
                                                      f"headroom between trigger and refill stop ({room:g} L); the "
                                                      "refill-stop guard would end the batch early"))
    if rf.nutrient_mode == "FIXED_VOLUMES":
        f = rf.fixed
        if f.ec_min_mS_cm is not None and f.ec_max_mS_cm is not None and f.ec_min_mS_cm >= f.ec_max_mS_cm:
            out.append(Issue("refill.fixed.ec_max_mS_cm", "EC validation maximum must exceed the minimum"))
    if rf.nutrient_mode == "EC_TARGET":
        e = rf.ec
        if e.ec_tolerance_mS_cm is not None and e.ec_target_mS_cm is not None and e.ec_tolerance_mS_cm >= e.ec_target_mS_cm:
            out.append(Issue("refill.ec.ec_tolerance_mS_cm", "EC tolerance must be smaller than the target"))
        if e.step_total_L is not None and e.max_a_L is not None and e.max_b_L is not None and \
                e.step_total_L > e.max_a_L + e.max_b_L:
            out.append(Issue("refill.ec.step_total_L", "one staged dose exceeds the batch maxima"))
    if ac.dose_step_L is not None and ac.max_batch_L is not None and ac.dose_step_L > ac.max_batch_L:
        out.append(Issue("acid.dose_step_L", "acid dose step exceeds the batch maximum"))
    if ac.max_batch_L is not None and ac.max_day_L is not None and ac.max_batch_L > ac.max_day_L:
        out.append(Issue("acid.max_batch_L", "acid batch maximum exceeds the 24 h maximum"))
    if ac.ph_target is not None and ac.undershoot_margin is not None and ac.ph_tolerance is not None and \
            ac.undershoot_margin <= ac.ph_tolerance:
        out.append(Issue("acid.undershoot_margin", "undershoot margin must be wider than the dosing tolerance"))
    if not ir.sequence:
        out.append(Issue("irrigation.sequence", "the irrigation sequence needs at least one sector step"))
    for i, step in enumerate(ir.sequence):
        if step.sector not in sectors:
            out.append(Issue(f"irrigation.sequence.{i}.sector", f"unknown sector {step.sector}"))
        if step.duration_s is None or not (0 < step.duration_s <= 86400):
            out.append(Issue(f"irrigation.sequence.{i}.duration_s", "sector duration must be 1..86400 s"))
    order("return", [("sump stop", rt.sump_stop_L), ("sump start", rt.sump_start_L)])
    order("return.main_resume_L", [("return resume level", rt.main_resume_L), ("return inhibit level", rt.main_inhibit_L)])
    if rt.main_inhibit_L is not None and m.high_inhibit_L is not None and rt.main_inhibit_L > m.high_inhibit_L:
        out.append(Issue("return.main_inhibit_L", "return may not fill the main tank above the high-level inhibit"))
    for s in ("A", "B", "ACID"):
        sf = body.stock_fill.get(s)
        if sf is None:
            out.append(Issue(f"stock_fill.{s}", f"stock {s} filling settings are required"))
            continue
        if None not in (sf.auto_trigger_L, sf.target_L, sf.high_inhibit_L) and \
                not (sf.auto_trigger_L < sf.target_L < sf.high_inhibit_L):
            out.append(Issue(f"stock_fill.{s}", "stock thresholds must be ordered: auto trigger < target < high inhibit"))
    return out


def _nutrient_max(rf: Refill) -> float | None:
    if rf.nutrient_mode == "FIXED_VOLUMES":
        return (rf.fixed.a_L or 0) + (rf.fixed.b_L or 0)
    if rf.nutrient_mode == "EC_TARGET":
        return (rf.ec.max_a_L or 0) + (rf.ec.max_b_L or 0)
    return None


def form_schema(capacities: dict[str, float]) -> list[dict]:
    return [{"path": r.path, "label": r.label, "unit": r.unit, "min": r.lo if r.lo is not None else r.gt,
             "min_exclusive": r.gt is not None, "max": r.hi, "required": r.required, "help": r.help,
             "options": list(r.options)}
            for r in _rules(capacities)]


def physical_readiness(recipe: "Recipe | None", *, profile_simulated: bool) -> list[str]:
    """Why this recipe cannot drive physical equipment (always non-empty in this release)."""
    reasons = ["live process control is not available in this release (no commissioned profile)"]
    if recipe is None:
        return ["no active recipe"] + reasons
    if recipe.provenance == "DEMO_PRESET":
        reasons.append("DEMO preset: synthetic values for simulation only")
    reasons += ["calibration, physical mapping, mixing method, acid concentration and dosing limits are "
                "unverified commissioning items"]
    return reasons


# ------------------------------------------------------------------------------------------------
def demo_preset(sectors: list[str]) -> Recipe:
    """Labelled DEMO preset for the simulation. Every number is synthetic. It is deliberately not the
    owner's illustrative example (5,000 + 1,000 A + 1,000 B + 12,500 water + 500 acid)."""
    seq = [IrrigationStep(sector=s, duration_s=300.0) for s in sectors]
    sf = StockFill(target_L=4000.0, auto_trigger_L=800.0, high_inhibit_L=4700.0, max_time_s=7200.0,
                   min_rise_L=5.0, rise_window_s=60.0)
    body = RecipeBody(
        main=MainLevels(low_low_L=2500.0, refill_trigger_L=8000.0, rearm_hysteresis_L=1000.0, fill_stop_L=16500.0,
                        high_inhibit_L=18500.0, min_dosing_L=4000.0),
        refill=Refill(trigger_debounce_s=10.0, endpoint_mode="FINAL_LEVEL", final_level_L=15000.0,
                      water=WaterPath(max_time_s=7200.0, min_flow_L_min=20.0, flow_grace_s=20.0),
                      nutrient_mode="EC_TARGET",
                      fixed=FixedNutrients(a_L=60.0, b_L=60.0, ec_min_mS_cm=1.4, ec_max_mS_cm=2.2,
                                           validation_settle_s=600.0),
                      ec=EcTarget(ec_target_mS_cm=1.8, ec_tolerance_mS_cm=0.1, ratio_a_to_b=1.0, step_total_L=20.0,
                                  max_a_L=150.0, max_b_L=150.0, settle_s=600.0),
                      transfer_max_time_s=900.0, transfer_min_flow_L_min=5.0, transfer_flow_grace_s=15.0,
                      stock_reserve_L=300.0),
        acid=Acid(ph_target=6.0, ph_tolerance=0.2, undershoot_margin=0.5, dose_step_L=2.0, settle_s=600.0,
                  max_batch_L=30.0, max_day_L=60.0, min_response_ph=0.02, max_no_response=2, dose_max_time_s=60.0,
                  min_flow_L_min=1.0, flow_grace_s=10.0),
        irrigation=Irrigation(sequence=seq, min_flow_L_min=60.0, flow_grace_s=15.0, no_flow_sustain_s=5.0,
                              valve_open_timeout_s=5.0, valve_close_timeout_s=5.0, open_lead_s=1.0,
                              drive_start_timeout_s=8.0, drive_stop_timeout_s=10.0, contactor_timeout_s=2.0,
                              stop_settle_s=0.5, max_manual_s=1800.0),
        return_=Return(sump_start_L=2500.0, sump_stop_L=800.0, main_inhibit_L=18500.0, main_resume_L=18000.0,
                       duty_policy="ALTERNATE_EACH_START", max_runtime_s=1800.0, min_flow_L_min=30.0,
                       flow_grace_s=10.0, run_feedback_timeout_s=3.0),
        stock_fill={"A": sf, "B": sf, "ACID": sf})
    return Recipe(recipe_id="demo-sim", version=1, name="DEMO preset (synthetic, simulation only)",
                  provenance="DEMO_PRESET", note="Synthetic values to exercise the simulation; not production "
                  "settings and not the owner's illustrative example.", body=body)


# ------------------------------------------------------------------------------------------------
# store
# ------------------------------------------------------------------------------------------------
STATUSES = ("DRAFT", "VALIDATED", "ACTIVE", "SUPERSEDED")


@dataclass
class RecipeRecord:
    recipe: Recipe
    status: str
    created_utc: str
    created_by: str
    validated_utc: str | None = None
    activated_utc: str | None = None
    activated_by: str | None = None
    issues: list[dict] = field(default_factory=list)

    def view(self) -> dict:
        return {"recipe_id": self.recipe.recipe_id, "version": self.recipe.version, "ref": self.recipe.ref,
                "name": self.recipe.name, "provenance": self.recipe.provenance, "note": self.recipe.note,
                "status": self.status, "created_utc": self.created_utc, "created_by": self.created_by,
                "validated_utc": self.validated_utc, "activated_utc": self.activated_utc,
                "activated_by": self.activated_by, "issues": self.issues, "digest": self.recipe.digest(),
                "body": self.recipe.body_json()}


class RecipeBackend(Protocol):
    def load(self) -> tuple[list[RecipeRecord], list[dict]]: ...

    def save(self, rec: RecipeRecord) -> None: ...

    def activation(self, entry: dict) -> None: ...


class MemoryRecipeBackend:
    def __init__(self) -> None:
        self.records: dict[tuple[str, int], RecipeRecord] = {}
        self.activations: list[dict] = []

    def load(self):
        return list(self.records.values()), list(self.activations)

    def save(self, rec: RecipeRecord) -> None:
        self.records[(rec.recipe.recipe_id, rec.recipe.version)] = rec

    def activation(self, entry: dict) -> None:
        self.activations.append(entry)


class RecipeError(ValueError):
    pass


class RecipeStore:
    """Versioned recipe store. All mutations are audited through `emit(kind, payload, actor)`."""

    def __init__(self, backend: RecipeBackend, capacities: dict[str, float], sectors: list[str], *,
                 emit: Callable[[str, dict, str | None], None] | None = None, simulated: bool = True):
        self.backend, self.capacities, self.sectors = backend, capacities, sectors
        self.emit = emit or (lambda k, p, a: None)
        self.simulated = simulated
        recs, acts = backend.load()
        self.records: dict[tuple[str, int], RecipeRecord] = {(r.recipe.recipe_id, r.recipe.version): r for r in recs}
        self.activations: list[dict] = sorted(acts, key=lambda a: a["seq"])
        self.listeners: list[Callable[[Recipe | None], None]] = []

    # ------------------------------------------------------------------ queries
    def active(self) -> Recipe | None:
        for r in self.records.values():
            if r.status == "ACTIVE":
                return r.recipe
        return None

    def get(self, recipe_id: str, version: int) -> RecipeRecord:
        try:
            return self.records[(recipe_id, version)]
        except KeyError:
            raise KeyError(f"no recipe {recipe_id}@v{version}") from None

    def list(self) -> list[dict]:
        return [r.view() for r in sorted(self.records.values(), key=lambda r: (r.recipe.recipe_id, -r.recipe.version))]

    def _next_version(self, recipe_id: str) -> int:
        vs = [v for (rid, v) in self.records if rid == recipe_id]
        return max(vs, default=0) + 1

    # ------------------------------------------------------------------ mutations
    def ensure_demo(self, actor: str = "system") -> Recipe | None:
        """SIMULATED profiles only: seed and activate the DEMO preset when no recipe exists at all."""
        if not self.simulated or self.records:
            return self.active()
        rec = RecipeRecord(demo_preset(self.sectors), "DRAFT", utcnow().isoformat(), actor)
        self.records[(rec.recipe.recipe_id, 1)] = rec
        self.backend.save(rec)
        self.validate(rec.recipe.recipe_id, 1, actor)
        self.activate(rec.recipe.recipe_id, 1, actor, reason="DEMO preset seeded for the simulation")
        return self.active()

    def save_draft(self, actor: str, recipe_id: str, name: str, body: dict, *, version: int | None = None,
                   note: str | None = None) -> RecipeRecord:
        """Create a new draft (version=None), or replace an existing DRAFT. Validated/active versions are
        immutable: editing them creates the next version as a draft."""
        parsed = RecipeBody.model_validate(body)
        if version is not None:
            cur = self.get(recipe_id, version)
            if cur.status != "DRAFT":
                raise RecipeError(f"{cur.recipe.ref} is {cur.status} and immutable; save a new draft version")
            v = version
        else:
            v = self._next_version(recipe_id)
        recipe = Recipe(recipe_id=recipe_id, version=v, name=name, provenance="OPERATOR", note=note, body=parsed)
        rec = RecipeRecord(recipe, "DRAFT", utcnow().isoformat(), actor)
        self.records[(recipe_id, v)] = rec
        self.backend.save(rec)
        self.emit("RECIPE_DRAFT_SAVED", {"recipe": recipe.ref, "digest": recipe.digest(), "name": name}, actor)
        return rec

    def validate(self, recipe_id: str, version: int, actor: str) -> RecipeRecord:
        rec = self.get(recipe_id, version)
        issues = validate_body(rec.recipe.body, self.capacities, self.sectors)
        rec.issues = [i.to_dict() for i in issues]
        if not issues and rec.status == "DRAFT":
            rec.status, rec.validated_utc = "VALIDATED", utcnow().isoformat()
        self.backend.save(rec)
        self.emit("RECIPE_VALIDATED" if not issues else "RECIPE_VALIDATION_FAILED",
                  {"recipe": rec.recipe.ref, "digest": rec.recipe.digest(), "issues": rec.issues}, actor)
        return rec

    def activate(self, recipe_id: str, version: int, actor: str, *, reason: str = "", rollback: bool = False) -> RecipeRecord:
        rec = self.get(recipe_id, version)
        if rec.status == "ACTIVE":
            raise RecipeError(f"{rec.recipe.ref} is already active")
        if rec.status == "DRAFT":
            raise RecipeError(f"{rec.recipe.ref} is a draft: validate it first")
        issues = validate_body(rec.recipe.body, self.capacities, self.sectors)    # re-checked at activation
        if issues:
            rec.issues = [i.to_dict() for i in issues]
            self.backend.save(rec)
            raise RecipeError(f"{rec.recipe.ref} no longer validates: {[i.message for i in issues]}")
        if rec.recipe.provenance == "DEMO_PRESET" and not self.simulated:
            raise RecipeError("a DEMO preset can only be activated in a SIMULATED profile")
        prev = None
        for r in self.records.values():
            if r.status == "ACTIVE":
                prev = r
                r.status = "SUPERSEDED"
                self.backend.save(r)
        rec.status, rec.activated_utc, rec.activated_by = "ACTIVE", utcnow().isoformat(), actor
        self.backend.save(rec)
        entry = {"seq": len(self.activations) + 1, "utc": rec.activated_utc, "actor": actor,
                 "recipe": rec.recipe.ref, "digest": rec.recipe.digest(), "action": "ROLLBACK" if rollback else "ACTIVATE",
                 "previous": prev.recipe.ref if prev else None, "reason": reason}
        self.activations.append(entry)
        self.backend.activation(entry)
        self.emit("RECIPE_ROLLED_BACK" if rollback else "RECIPE_ACTIVATED", entry | {"applies": "next cycle or batch "
                  "of each process; running operations keep their version"}, actor)
        for cb in self.listeners:
            cb(rec.recipe)
        return rec

    def rollback(self, actor: str, reason: str) -> RecipeRecord:
        """Activate the version that was active before the current one."""
        cur = self.active()
        if cur is None:
            raise RecipeError("no active recipe")
        prev = [a for a in self.activations if a["recipe"] != cur.ref]
        if not prev:
            raise RecipeError("no earlier activated version to roll back to")
        rid, v = prev[-1]["recipe"].rsplit("@v", 1)
        return self.activate(rid, int(v), actor, reason=reason or "rollback", rollback=True)
