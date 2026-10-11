"""Deterministic causal plant model (DEMO topology, brief §7).

Units: volume L, flow L/min, time s. One call to `step()` advances exactly one fixed tick.

Topology (synthetic demo, configurable counts):

    FILL valve (external supply) --> reservoir --> pump header (P1..Pn, any running gives head)
        --> sector valves S1..Sk --> channels (equal share within sector)
        --> return_fraction to transit delay line --> return tank --(transfer)--> reservoir
        --> (1 - return_fraction) to cumulative external consumption
    Any tank overflow goes to a cumulative spill ledger. Nutrient A/B/acid tanks have no edges.

Requested outputs (commands) are separate from actuator state; actuator faults change dynamics,
sensor faults are applied later by the observation layer and never change volumes.
"""
from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass, field

from .params import TwinParams

PUMP_FAULTS = {None, "NO_RUN", "STUCK_ON", "NO_FLOW"}  # NO_FLOW: spins (feedback) but delivers nothing
VALVE_FAULTS = {None, "STUCK_CLOSED", "STUCK_OPEN"}


def first_order(current: float, target: float, dt: float, tau: float) -> float:
    if not (math.isfinite(current) and math.isfinite(target)):
        raise ValueError("non-finite actuator state")
    if tau == 0:
        return target
    return target + (current - target) * math.exp(-dt / tau)


@dataclass
class Actuator:
    cmd: bool = False
    position: float = 0.0          # pump speed fraction or valve opening fraction, 0..1
    fault: str | None = None


@dataclass
class Ledger:
    initial_accounted_L: float
    external_supply_L: float = 0.0
    consumption_L: float = 0.0
    spill_L: dict[str, float] = field(default_factory=lambda: {"reservoir": 0.0, "return_tank": 0.0})
    delivered_by_sector_L: dict[str, float] = field(default_factory=dict)
    delivered_by_channel_L: dict[int, float] = field(default_factory=dict)
    returned_to_tank_L: float = 0.0
    transferred_to_reservoir_L: float = 0.0
    unmet_demand_L: float = 0.0


class Plant:
    def __init__(self, params: TwinParams):
        self.p = params
        self.tick = 0
        self.volume = {"reservoir": params.reservoir.initial_L, "return_tank": params.return_tank.initial_L}
        self.capacity = {"reservoir": params.reservoir.capacity_L, "return_tank": params.return_tank.capacity_L}
        self.nutrients = dict(params.nutrient_tanks_L)  # logical only, no active edges
        self.pumps = {pid: Actuator() for pid in params.pumps}
        self.valves = {s.sector_id: Actuator() for s in params.sectors}
        self.valves["FILL"] = Actuator()
        self.sectors = {s.sector_id: s for s in params.sectors}
        n_delay = int(round(params.return_transit_s / params.dt_s))
        self.transit: deque[float] = deque([0.0] * n_delay, maxlen=n_delay) if n_delay else deque()
        self.n_delay = n_delay
        self.flow_actual = {s: 0.0 for s in self.sectors}   # L/min
        self.flow_demand = {s: 0.0 for s in self.sectors}
        self.channel_flow = {c: 0.0 for s in params.sectors for c in s.channels}
        self.fill_flow = 0.0
        self.transfer_flow = 0.0
        self.deadhead = False
        self.ledger = Ledger(initial_accounted_L=self._stored())
        self.ledger.delivered_by_sector_L = {s: 0.0 for s in self.sectors}
        self.ledger.delivered_by_channel_L = {c: 0.0 for c in self.channel_flow}

    # ----------------------------------------------------------------------------- commands
    def command(self, signal: str, on: bool) -> None:
        """Apply a requested state from a virtual DO. signal: pump.<id>.cmd / valve.<id>.cmd."""
        kind, ident, suffix = signal.split(".")
        if suffix != "cmd":
            raise KeyError(signal)
        table = self.pumps if kind == "pump" else self.valves if kind == "valve" else None
        if table is None or ident not in table:
            raise KeyError(signal)
        table[ident].cmd = bool(on)

    def set_fault(self, kind: str, ident: str, fault: str | None) -> None:
        if kind == "pump":
            if fault not in PUMP_FAULTS:
                raise ValueError(fault)
            self.pumps[ident].fault = fault
        elif kind == "valve":
            if fault not in VALVE_FAULTS:
                raise ValueError(fault)
            self.valves[ident].fault = fault
        else:
            raise ValueError(kind)

    # ----------------------------------------------------------------------------- dynamics
    def step(self) -> None:
        p, dt = self.p, self.p.dt_s
        to_L = dt / 60.0
        for a in self.pumps.values():
            target = 1.0 if (a.cmd and a.fault != "NO_RUN") or a.fault == "STUCK_ON" else 0.0
            a.position = first_order(a.position, target, dt, p.pump_tau_s)
        for a in self.valves.values():
            target = 1.0 if a.cmd else 0.0
            if a.fault == "STUCK_CLOSED":
                target = 0.0
            elif a.fault == "STUCK_OPEN":
                target = 1.0
            a.position = first_order(a.position, target, dt, p.valve_tau_s)

        drive = min(1.0, sum(a.position for a in self.pumps.values() if a.fault != "NO_FLOW"))
        demand = {sid: s.rated_flow_L_min * self.valves[sid].position * drive for sid, s in self.sectors.items()}
        self.deadhead = drive > p.run_feedback_threshold and all(self.valves[s].position < p.valve_closed_threshold
                                                                 for s in self.sectors)
        demand_vol = sum(demand.values()) * to_L
        avail = self.volume["reservoir"]
        # Allocation rule: when the source cannot meet demand, every outlet is scaled by the same factor.
        factor = 1.0 if demand_vol <= avail or demand_vol == 0 else avail / demand_vol
        actual = {sid: q * factor for sid, q in demand.items()}
        extracted = sum(actual.values()) * to_L
        if extracted > avail:  # floating-point guard: never extract more than present
            extracted = avail
        self.ledger.unmet_demand_L += demand_vol - extracted
        self.volume["reservoir"] -= extracted
        self.flow_demand, self.flow_actual = demand, actual
        for sid, s in self.sectors.items():
            v = actual[sid] * to_L
            self.ledger.delivered_by_sector_L[sid] += v
            share = actual[sid] / len(s.channels)
            for c in s.channels:
                self.channel_flow[c] = share
                self.ledger.delivered_by_channel_L[c] += v / len(s.channels)

        to_return = extracted * p.return_fraction
        self.ledger.consumption_L += extracted - to_return
        if self.n_delay:
            arriving = self.transit[0]
            self.transit.append(to_return)  # maxlen drops the oldest (== arriving)
        else:
            arriving = to_return
        self.ledger.returned_to_tank_L += arriving
        self._add("return_tank", arriving)

        if p.return_transfer_enabled:
            v = min(p.return_transfer_L_min * to_L, self.volume["return_tank"])
            self.volume["return_tank"] -= v
            self.transfer_flow = v / to_L
            self.ledger.transferred_to_reservoir_L += v
            self._add("reservoir", v)
        else:
            self.transfer_flow = 0.0

        fill = p.fill_flow_L_min * self.valves["FILL"].position * to_L
        self.fill_flow = fill / to_L
        self.ledger.external_supply_L += fill
        self._add("reservoir", fill)
        self.tick += 1

    def _add(self, tank: str, v: float) -> None:
        room = self.capacity[tank] - self.volume[tank]
        if v > room:
            self.ledger.spill_L[tank] += v - room
            self.volume[tank] = self.capacity[tank]
        else:
            self.volume[tank] += v

    # ----------------------------------------------------------------------------- accounting
    def _stored(self) -> float:
        return sum(self.volume.values()) + sum(self.transit) + sum(self.nutrients.values())

    def ledger_residual_L(self) -> float:
        lg = self.ledger
        accounted = (self._stored() + lg.consumption_L + sum(lg.spill_L.values()) - lg.external_supply_L)
        return accounted - lg.initial_accounted_L

    @property
    def sim_time_s(self) -> float:
        return self.tick * self.p.dt_s

    def state_hash(self) -> str:
        h = hashlib.sha256()
        parts = [self.tick, sorted(self.volume.items()), list(self.transit),
                 sorted((k, a.cmd, a.position, a.fault) for k, a in self.pumps.items()),
                 sorted((k, a.cmd, a.position, a.fault) for k, a in self.valves.items()),
                 self.ledger.consumption_L, sorted(self.ledger.spill_L.items()), self.ledger.external_supply_L]
        h.update(repr(parts).encode())
        return h.hexdigest()

    def snapshot(self) -> dict:
        lg = self.ledger
        return {
            "tick": self.tick, "sim_time_s": round(self.sim_time_s, 6),
            "tanks": {k: {"volume_L": self.volume[k], "capacity_L": self.capacity[k]} for k in self.volume},
            "nutrient_tanks_L": dict(self.nutrients), "nutrient_dosing": "INACTIVE (no chemistry modelled)",
            "pumps": {k: {"requested": a.cmd, "speed": a.position, "fault": a.fault} for k, a in self.pumps.items()},
            "valves": {k: {"requested": a.cmd, "position": a.position, "fault": a.fault} for k, a in self.valves.items()},
            "sector_flow_L_min": dict(self.flow_actual), "sector_demand_L_min": dict(self.flow_demand),
            "channel_flow_L_min": {str(k): v for k, v in self.channel_flow.items()},
            "fill_flow_L_min": self.fill_flow, "return_transfer_L_min": self.transfer_flow,
            "transit_L": sum(self.transit), "deadhead": self.deadhead,
            "ledger": {"initial_accounted_L": lg.initial_accounted_L, "external_supply_L": lg.external_supply_L,
                       "consumption_L": lg.consumption_L, "spill_L": dict(lg.spill_L),
                       "delivered_by_sector_L": dict(lg.delivered_by_sector_L),
                       "delivered_by_channel_L": {str(k): v for k, v in lg.delivered_by_channel_L.items()},
                       "returned_to_tank_L": lg.returned_to_tank_L,
                       "transferred_to_reservoir_L": lg.transferred_to_reservoir_L,
                       "unmet_demand_L": lg.unmet_demand_L, "residual_L": self.ledger_residual_L()},
        }
