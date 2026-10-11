"""Sensor observation model: plant truth -> virtual signal values (before overrides).

Analog signals are emitted as raw AI 8CH register values in the configured mode's unit
(4-20 mA mode => microamps), so the same scaling path is used for virtual and real data.
"""
from __future__ import annotations

import random

from .plant import Plant

UA_LOW, UA_HIGH = 4000.0, 20000.0
UA_SATURATION = 20500.0  # demo transmitter saturation; real AI board clamping behaviour is unverified


def _ua(value: float, span: float) -> float:
    return min(UA_SATURATION, max(UA_LOW, UA_LOW + (UA_HIGH - UA_LOW) * value / span))


def observe(plant: Plant, rng: random.Random | None = None) -> dict[str, bool | int]:
    p = plant.p
    out: dict[str, bool | int] = {}
    for pid, a in plant.pumps.items():
        out[f"pump.{pid}.running"] = a.position > p.run_feedback_threshold
    for sid in plant.sectors:
        out[f"valve.{sid}.open_limit"] = plant.valves[sid].position >= p.valve_open_threshold
    res, ret = plant.volume["reservoir"], plant.volume["return_tank"]
    rcap, tcap = plant.capacity["reservoir"], plant.capacity["return_tank"]
    out["reservoir.high_level"] = p.reservoir.high_level_fraction is not None and res / rcap >= p.reservoir.high_level_fraction
    out["reservoir.low_level"] = p.reservoir.low_level_fraction is not None and res / rcap <= p.reservoir.low_level_fraction
    out["return_tank.high_level"] = (p.return_tank.high_level_fraction is not None
                                     and ret / tcap >= p.return_tank.high_level_fraction)
    analog = {
        "reservoir.level_transmitter": _ua(res, p.level_transmitter_span["reservoir"]),
        "return_tank.level_transmitter": _ua(ret, p.level_transmitter_span["return_tank"]),
    }
    for sid in plant.sectors:
        analog[f"sector.{sid}.flow_transmitter"] = _ua(plant.flow_actual[sid], p.flow_transmitter_span_L_min)
    for k in sorted(analog):
        v = analog[k]
        if p.noise_std_fraction and rng is not None:
            v += rng.gauss(0.0, p.noise_std_fraction * (UA_HIGH - UA_LOW))
        out[k] = int(round(min(65535.0, max(0.0, v))))
    return out
