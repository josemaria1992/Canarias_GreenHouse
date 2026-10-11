"""Turn raw channel values into fully traceable Sample records (shared by virtual and Modbus)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import count
from typing import Any

from ..config.catalog import AIMode
from ..config.schema import InstallationConfig
from ..domain.model import Origin, PointId, PointKind, Quality, Sample

_seq = count(1)


def next_sequence() -> int:
    return next(_seq)


@dataclass(frozen=True)
class AcqContext:
    session_id: str
    cycle_id: str
    sim_run_id: str | None = None
    sim_time_s: float | None = None


def make_sample(cfg: InstallationConfig, pid: PointId, raw: Any, *, acquired: datetime, ctx: AcqContext,
                origin: Origin, override: dict | None = None, reason: str | None = None,
                observed_mode: AIMode | None = None) -> Sample:
    dev = cfg.device(pid.device_uid)
    common = dict(point_id=str(pid), acquired_utc=acquired, cycle_id=ctx.cycle_id, sequence=next_sequence(),
                  origin=origin, source_profile=cfg.config_id, config_version=cfg.config_version,
                  device_uid=dev.device_uid, bus_id=dev.bus_id, session_id=ctx.session_id,
                  sim_run_id=ctx.sim_run_id, sim_time_s=ctx.sim_time_s, override=override)
    base_quality = Quality.OVERRIDDEN if override else Quality.GOOD
    if raw is None:
        return Sample(quality=Quality.UNAVAILABLE, reason=reason or "no value", raw_value=None, raw_unit=None,
                      value=None, unit=None, calibration_ref=None, **common)
    if pid.kind in (PointKind.DI, PointKind.DO):
        b = bool(raw)
        return Sample(quality=base_quality, reason=reason, raw_value=b, raw_unit="bool", value=b, unit=None,
                      calibration_ref=None, **common)
    ch = dev.ai_channel(pid.channel)
    mode = ch.mode
    if observed_mode is not None and observed_mode != mode:
        return Sample(quality=Quality.UNSCALED, reason=f"observed mode {observed_mode.name} != configured {mode.name}",
                      raw_value=raw, raw_unit=observed_mode.raw_unit, value=None, unit=None, calibration_ref=None,
                      **common)
    if mode == AIMode.UNKNOWN:
        return Sample(quality=Quality.UNSCALED, reason="channel mode unknown/unverified", raw_value=raw,
                      raw_unit=None, value=None, unit=None, calibration_ref=None, **common)
    prof = cfg.scaling(ch.scaling)
    if prof is None:
        return Sample(quality=Quality.UNSCALED, reason="no scaling profile", raw_value=raw, raw_unit=mode.raw_unit,
                      value=None, unit=None, calibration_ref=None, **common)
    eng = prof.scale(float(raw))
    if not prof.raw_valid(float(raw)):
        return Sample(quality=Quality.INVALID, reason=f"raw {raw} {prof.input_unit} outside valid range",
                      raw_value=raw, raw_unit=mode.raw_unit, value=eng, unit=prof.unit, calibration_ref=prof.ref,
                      **common)
    return Sample(quality=base_quality, reason=reason, raw_value=raw, raw_unit=mode.raw_unit, value=eng,
                  unit=prof.unit, calibration_ref=prof.ref, **common)
