"""Deterministic causal plant for the confirmed installation (brief §2, §7; ADR-0013).

Units: volume L, flow L/min, time s, pressure bar. One `step()` advances exactly one fixed tick.

    mains --FILL_MAIN--> MAIN --(MP1|MP2 on the shared drive, K1/K2 select)--> header
        --> sector valve S1|S2 --> supply pipe (transit) --> channel inlets (4 adjustable outlets each)
        --> channel water (retained depth kept; only water above it drains) --> drain pipe (transit) --> SUMP
        --> RP1|RP2 --> MAIN
    mains --FILL_A|B|ACID--> stock tank --TP_x + OUT_x--> MAIN
    channel uptake (plants) leaves the system as consumption; overflows are explicit spills.

Requested states (commands), actuator positions and observations are separate. Actuator faults change
the dynamics; sensor faults live in the observation layer (overrides) and never change volumes.
Water carries species (see chemistry.py): every transfer moves them in proportion.

Proposed hardwired protection modelled here (design proposals, unverified, ADR-0013): an electrical
K1/K2 mutual interlock (`contactor_interlock`) and a safety chain that removes motor power. Switching a
contactor while the drive output is energised, or two motors on the drive, trips the synthetic drive.
"""
from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass, field

from .chemistry import SPECIES, Probe, SyntheticChemistry, empty, initial_main, mains_species
from .installation_params import STOCKS, InstallationTwin

PUMP_FAULTS = {None, "NO_RUN", "STUCK_ON", "NO_FLOW"}
VALVE_FAULTS = {None, "STUCK_CLOSED", "STUCK_OPEN"}
DRIVE_FAULTS = {None, "TRIP", "NO_RUN", "STUCK_ON"}
CONTACTOR_FAULTS = {None, "WELDED", "NO_CLOSE"}
MOTOR_FAULTS = {None, "NO_RUN", "NO_FLOW"}
HAZARD_SPEED = 0.05


def first_order(current: float, target: float, dt: float, tau: float) -> float:
    if not (math.isfinite(current) and math.isfinite(target)):
        raise ValueError("non-finite actuator state")
    if tau == 0:
        return target
    return target + (current - target) * math.exp(-dt / tau)


@dataclass
class Parcel:
    volume: float = 0.0
    sp: dict[str, float] = field(default_factory=empty)

    def conc(self) -> dict[str, float]:
        if self.volume <= 1e-12:
            return empty()
        return {k: v / self.volume for k, v in self.sp.items()}

    def take(self, v: float) -> tuple[float, dict[str, float]]:
        """Remove `v` litres (bounded by the content); species leave in proportion."""
        v = max(0.0, min(v, self.volume))
        if v <= 0:
            return 0.0, empty()
        if v >= self.volume - 1e-12:
            out = dict(self.sp)
            self.volume, self.sp = 0.0, empty()
            return v, out
        f = v / self.volume
        out = {k: x * f for k, x in self.sp.items()}
        for k in SPECIES:
            self.sp[k] -= out[k]
        self.volume -= v
        return v, out

    def put(self, v: float, sp: dict[str, float]) -> None:
        self.volume += v
        for k in SPECIES:
            self.sp[k] += sp[k]


@dataclass
class Actuator:
    cmd: bool = False
    position: float = 0.0
    fault: str | None = None


@dataclass
class Contactor:
    cmd: bool = False
    closed: bool = False
    pending_s: float = 0.0          # time left until the commanded state is reached
    fault: str | None = None


@dataclass
class Drive:
    run_cmd: bool = False
    speed: float = 0.0              # output speed fraction 0..1
    fault: str | None = None        # injected fault (TRIP / NO_RUN / STUCK_ON)
    tripped: str | None = None      # synthetic protective trip reason (latched until fault reset in the twin)


@dataclass
class Ledger:
    initial_stored_L: float
    initial_species: dict[str, float]
    external_supply_L: dict[str, float] = field(default_factory=lambda: {"MAIN": 0.0, **{s: 0.0 for s in STOCKS}})
    external_species: dict[str, float] = field(default_factory=empty)
    consumption_L: float = 0.0
    consumed_species: dict[str, float] = field(default_factory=empty)
    spill_L: dict[str, float] = field(default_factory=dict)
    spilled_species: dict[str, float] = field(default_factory=empty)
    degassed_CT: float = 0.0
    unmet_demand_L: dict[str, float] = field(default_factory=dict)
    delivered_by_sector_L: dict[str, float] = field(default_factory=dict)
    delivered_by_channel_L: dict[int, float] = field(default_factory=dict)
    drained_to_sump_L: float = 0.0
    returned_to_main_L: float = 0.0
    transferred_L: dict[str, float] = field(default_factory=lambda: {s: 0.0 for s in STOCKS})


class InstallationPlant:
    def __init__(self, params: InstallationTwin):
        self.p = params
        f, d = params.facts, params.dynamics
        self.tick = 0
        self.chem = SyntheticChemistry(params.chemistry)
        cp = params.chemistry
        self.capacity = {t: f.tank_capacity(t) for t in ("MAIN", "SUMP", *STOCKS)}
        self.tanks: dict[str, Parcel] = {}
        self.tanks["MAIN"] = Parcel(d.initial_L["MAIN"], initial_main(cp, d.initial_L["MAIN"]))
        self.tanks["SUMP"] = Parcel(d.initial_L["SUMP"], initial_main(cp, d.initial_L["SUMP"]))
        for s in STOCKS:
            v = d.initial_L[s]
            self.tanks[s] = Parcel(v, mains_species(cp, v))
            self._prepare(s, record=False)   # initial synthetic stock solution (twin truth only)
        self.sectors = {s.sector_id: s for s in f.sectors}
        self.sector_of = {c: s.sector_id for s in f.sectors for c in s.channels}
        geo = f.channel
        self.retained_L, self.channel_capacity_L = geo.retained_L, geo.capacity_L
        v0 = self.retained_L * d.initial_channel_fraction_of_retained
        if v0 > self.channel_capacity_L:
            raise ValueError("initial channel inventory exceeds the channel capacity")
        self.channels = {c: Parcel(v0, {k: x * v0 / max(d.initial_L["MAIN"], 1e-9)
                                        for k, x in self.tanks["MAIN"].sp.items()}) for c in self.sector_of}
        self.outlets = {c: [1.0] * f.outlets_per_channel for c in self.sector_of}
        n_sup = int(round(d.supply_transit_s / params.dt_s))
        self.n_sup = n_sup
        self.supply = {s: deque([Parcel() for _ in range(n_sup)], maxlen=n_sup) if n_sup else deque()
                       for s in self.sectors}
        n_dr = int(round(d.drain_transit_s / params.dt_s))
        self.n_dr = n_dr
        self.drain = deque([Parcel() for _ in range(n_dr)], maxlen=n_dr) if n_dr else deque()
        self.drive = Drive()
        self.contactors = {p: Contactor() for p in f.main_pumps}
        self.motor_fault: dict[str, str | None] = {p: None for p in f.main_pumps}
        self.motor_speed = {p: 0.0 for p in f.main_pumps}
        self.valves: dict[str, Actuator] = {s: Actuator() for s in self.sectors}
        for s in STOCKS:
            self.valves[f"OUT_{s}"] = Actuator()
        for t in ("MAIN", *STOCKS):
            self.valves[f"FILL_{t}"] = Actuator()
        self.pumps: dict[str, Actuator] = {p: Actuator() for p in f.return_pumps}
        for s in STOCKS:
            self.pumps[f"TP_{s}"] = Actuator()
        self.mains_available = True
        self.safety_chain_ok = True
        self.temperature_C = 20.0
        self.probe = Probe(dict(self.tanks["MAIN"].conc()))
        # flows of the last tick (L/min), for observation and display
        self.flow = {"FILL_MAIN": 0.0, "RETURN": 0.0, **{s: 0.0 for s in self.sectors},
                     **{f"TP_{s}": 0.0 for s in STOCKS}, **{f"FILL_{s}": 0.0 for s in STOCKS}}
        self.pressure = {s: 0.0 for s in self.sectors}
        self.channel_in = {c: 0.0 for c in self.channels}
        self.channel_out = {c: 0.0 for c in self.channels}
        self.circulating = False
        self.hazards: list[dict] = []          # synthetic protective trips and interlock blocks (bounded)
        self.ledger = Ledger(initial_stored_L=self.stored_L(), initial_species=self.stored_species())
        self.ledger.spill_L = {t: 0.0 for t in self.tanks} | {f"channel_{c}": 0.0 for c in self.channels}
        self.ledger.unmet_demand_L = {"MAIN_PUMPS": 0.0, "RETURN": 0.0, **{s: 0.0 for s in STOCKS}}
        self.ledger.delivered_by_sector_L = {s: 0.0 for s in self.sectors}
        self.ledger.delivered_by_channel_L = {c: 0.0 for c in self.channels}

    # ----------------------------------------------------------------------------- commands
    def command(self, signal: str, on: bool) -> None:
        """Apply a requested state: drive.<id>.run / contactor.<p>.cmd / valve.<v>.cmd / pump.<p>.cmd."""
        parts = signal.split(".")
        if len(parts) != 3:
            raise KeyError(signal)
        kind, ident, suffix = parts
        on = bool(on)
        if kind == "drive" and suffix == "run" and ident == self.p.facts.shared_drive:
            self.drive.run_cmd = on
        elif kind == "contactor" and suffix == "cmd" and ident in self.contactors:
            self.contactors[ident].cmd = on
        elif kind == "valve" and suffix == "cmd" and ident in self.valves:
            self.valves[ident].cmd = on
        elif kind == "pump" and suffix == "cmd" and ident in self.pumps:
            self.pumps[ident].cmd = on
        else:
            raise KeyError(signal)

    def set_fault(self, kind: str, ident: str, fault: str | None) -> None:
        if kind == "pump":
            if ident in self.motor_fault:
                if fault not in MOTOR_FAULTS:
                    raise ValueError(f"main pump faults: {sorted(x for x in MOTOR_FAULTS if x)}")
                self.motor_fault[ident] = fault
                return
            if fault not in PUMP_FAULTS:
                raise ValueError(fault)
            self.pumps[ident].fault = fault
        elif kind == "valve":
            if fault not in VALVE_FAULTS:
                raise ValueError(fault)
            self.valves[ident].fault = fault
        elif kind == "drive":
            if ident != self.p.facts.shared_drive or fault not in DRIVE_FAULTS:
                raise ValueError(f"drive {ident}: {fault}")
            self.drive.fault = fault
            if fault is None:
                self.drive.tripped = None       # fault reset at the drive (twin stand-in for a local reset)
        elif kind == "contactor":
            if fault not in CONTACTOR_FAULTS:
                raise ValueError(fault)
            self.contactors[ident].fault = fault
        elif kind == "mains":
            if fault not in (None, "NO_SUPPLY"):
                raise ValueError(fault)
            self.mains_available = fault is None
        elif kind == "safety":
            if fault not in (None, "CHAIN_OPEN"):
                raise ValueError(fault)
            self.safety_chain_ok = fault is None
        else:
            raise ValueError(kind)

    def set_outlet(self, channel: int, outlet: int, opening: float) -> None:
        """Manual mechanical adjustment of one channel outlet (0..1). Not an electric valve."""
        if channel not in self.outlets or not 1 <= outlet <= len(self.outlets[channel]):
            raise KeyError(f"channel {channel} outlet {outlet}")
        if not 0.0 <= opening <= 1.0 or not math.isfinite(opening):
            raise ValueError("outlet opening must be 0..1")
        self.outlets[channel][outlet - 1] = float(opening)

    def prepare_stock(self, stock: str) -> dict:
        """SIMULATION ONLY: the operator's manual stock preparation, as synthetic species added to the
        twin tank (a real stock concentration is never inferred from it)."""
        if stock not in STOCKS:
            raise KeyError(stock)
        return self._prepare(stock, record=True)

    def _prepare(self, stock: str, *, record: bool) -> dict:
        t, cp = self.tanks[stock], self.p.chemistry
        add = empty()
        if stock == "ACID":
            delta = -cp.acid_stock_meq_L * t.volume - t.sp["ALK"]     # net acidity of a fresh stock
            if delta < 0:
                add["ALK"], add["ANION"] = delta, -delta
        else:
            want = cp.stock_strength[stock] * t.volume
            add[stock] = max(0.0, want - t.sp[stock])
        for k in SPECIES:
            t.sp[k] += add[k]
            if record:
                self.ledger.external_species[k] += add[k]
        return {"stock": stock, "added_species": add, "volume_L": t.volume, "synthetic": True}

    # ----------------------------------------------------------------------------- dynamics
    def step(self) -> None:
        p, d, dt = self.p, self.p.dynamics, self.p.dt_s
        to_L = dt / 60.0
        powered = self.safety_chain_ok
        # --- valves
        for a in self.valves.values():
            target = 1.0 if a.cmd else 0.0
            if a.fault == "STUCK_CLOSED":
                target = 0.0
            elif a.fault == "STUCK_OPEN":
                target = 1.0
            a.position = first_order(a.position, target, dt, d.valve_tau_s)
        # --- direct-on-line pumps
        for a in self.pumps.values():
            on = (a.cmd and a.fault != "NO_RUN") or a.fault == "STUCK_ON"
            a.position = first_order(a.position, 1.0 if (on and powered) else 0.0, dt, d.pump_tau_s)
        # --- shared drive (updated before the contactors: a contactor that changes state while the
        # output is energised trips it)
        dr = self.drive
        run = (dr.run_cmd and dr.fault not in ("NO_RUN", "TRIP") and dr.tripped is None) or dr.fault == "STUCK_ON"
        if dr.fault == "TRIP" and dr.tripped is None:
            self._trip("TRIP (injected drive fault)")
        if not powered:
            dr.speed = 0.0          # safety chain: output removed, motors coast
        elif dr.tripped is not None and dr.fault != "STUCK_ON":
            dr.speed = 0.0          # a tripped drive removes its output immediately (coast)
        elif run:
            dr.speed = min(1.0, dr.speed + (dt / d.drive_accel_s if d.drive_accel_s else 1.0))
        else:
            dr.speed = max(0.0, dr.speed - (dt / d.drive_decel_s if d.drive_decel_s else 1.0))
        # --- contactors (K1/K2) with the proposed hardwired interlock
        energised = dr.speed > HAZARD_SPEED
        for pid, k in self.contactors.items():
            want = k.cmd and powered
            if k.fault == "WELDED":
                want = True if k.closed else want
            if k.fault == "NO_CLOSE":
                want = False if not k.closed else want
            if want and d.contactor_interlock and not k.closed:
                others = [o for o, c in self.contactors.items() if o != pid and c.closed]
                if others:
                    want = False
                    self._hazard("INTERLOCK_BLOCKED", f"{pid} close blocked by hardwired interlock ({others} closed)")
            if want != k.closed:
                k.pending_s += dt
                if k.pending_s + 1e-9 >= d.contactor_s:
                    k.closed, k.pending_s = want, 0.0
                    if energised:
                        self._trip(f"OUTPUT_SWITCHED_UNDER_LOAD: {pid} {'closed' if want else 'opened'} at speed "
                                   f"{self.drive.speed:.2f}")
            else:
                k.pending_s = 0.0
        closed = [pid for pid, k in self.contactors.items() if k.closed]
        if len(closed) > 1 and self.drive.speed > HAZARD_SPEED:
            self._trip(f"TWO_MOTORS_ON_DRIVE: {closed}")
        for pid in self.motor_speed:
            ok = self.contactors[pid].closed and self.motor_fault[pid] != "NO_RUN" and powered
            self.motor_speed[pid] = dr.speed if ok else 0.0

        # --- main pumps -> sectors
        pumping = sum(s for pid, s in self.motor_speed.items() if self.motor_fault[pid] != "NO_FLOW")
        cap = d.main_pump_rated_L_min * min(1.0, pumping)
        mean_open = {sid: sum(self.outlets[c][i] for c in s.channels for i in range(len(self.outlets[c])))
                     / (len(s.channels) * p.facts.outlets_per_channel) for sid, s in self.sectors.items()}
        demand = {sid: d.sector_rated_L_min[sid] * self.valves[sid].position * mean_open[sid] for sid in self.sectors}
        dem_total = sum(demand.values())
        q_total = min(cap, dem_total) if dem_total > 0 else 0.0
        want_vol = q_total * to_L
        main = self.tanks["MAIN"]
        got, sp = main.take(want_vol)
        self.ledger.unmet_demand_L["MAIN_PUMPS"] += want_vol - got
        for sid in self.sectors:
            share = demand[sid] / dem_total if dem_total > 0 else 0.0
            v = got * share
            part = {k: x * share for k, x in sp.items()}
            self.flow[sid] = v / to_L
            self.ledger.delivered_by_sector_L[sid] += v
            if self.n_sup:
                arriving = self.supply[sid][0]
                self.supply[sid].append(Parcel(v, part))
            else:
                arriving = Parcel(v, part)
            self._to_channels(sid, arriving)
        speed_eff = max([s for pid, s in self.motor_speed.items() if self.motor_fault[pid] != "NO_FLOW"] or [0.0])
        load = (q_total / d.main_pump_rated_L_min) if d.main_pump_rated_L_min else 0.0
        for sid in self.sectors:
            v_open = self.valves[sid].position > d.valve_closed_threshold
            self.pressure[sid] = (d.shutoff_pressure_bar * speed_eff ** 2 * max(0.0, 1 - 0.5 * load ** 2)
                                  if v_open else 0.0)

        # --- channels: uptake, drain above the retained depth, capacity overflow
        drained = Parcel()
        k_dr = 1.0 - math.exp(-dt / d.channel_drain_tau_s)
        cp = p.chemistry
        for c, ch in self.channels.items():
            up = min(ch.volume, d.channel_uptake_L_min * to_L)
            if up > 0:
                conc = ch.conc()
                ch.volume -= up
                taken = empty()
                for s in ("A", "B"):
                    taken[s] = min(ch.sp[s], conc[s] * up * cp.uptake_solute_ratio)
                    ch.sp[s] -= taken[s]
                self.ledger.consumption_L += up
                for k in SPECIES:
                    self.ledger.consumed_species[k] += taken[k]
            excess = ch.volume - self.retained_L
            v_out = excess * k_dr if excess > 0 else 0.0
            v, spx = ch.take(v_out)
            self.channel_out[c] = v / to_L
            drained.put(v, spx)
            if ch.volume > self.channel_capacity_L:
                v, spx = ch.take(ch.volume - self.channel_capacity_L)
                self._spill(f"channel_{c}", v, spx)
        if self.n_dr:
            arriving = self.drain[0]
            self.drain.append(drained)
        else:
            arriving = drained
        self.ledger.drained_to_sump_L += arriving.volume
        self._add("SUMP", arriving.volume, arriving.sp)

        # --- return pumps: SUMP -> MAIN
        q_ret = sum(d.return_pump_rated_L_min * self.pumps[r].position for r in p.facts.return_pumps
                    if self.pumps[r].fault != "NO_FLOW")
        want_vol = q_ret * to_L
        v, spx = self.tanks["SUMP"].take(want_vol)
        self.ledger.unmet_demand_L["RETURN"] += want_vol - v
        self.flow["RETURN"] = v / to_L
        self.ledger.returned_to_main_L += v
        self._add("MAIN", v, spx)

        # --- stock transfers: stock -> MAIN (pump AND outlet valve)
        for s in STOCKS:
            pump, valve = self.pumps[f"TP_{s}"], self.valves[f"OUT_{s}"]
            q = d.transfer_rated_L_min[s] * pump.position * valve.position if pump.fault != "NO_FLOW" else 0.0
            want_vol = q * to_L
            v, spx = self.tanks[s].take(want_vol)
            self.ledger.unmet_demand_L[s] += want_vol - v
            self.flow[f"TP_{s}"] = v / to_L
            self.ledger.transferred_L[s] += v
            self._add("MAIN", v, spx)

        # --- mains fills (external supply)
        for t in ("MAIN", *STOCKS):
            q = d.fill_rated_L_min[t] * self.valves[f"FILL_{t}"].position if self.mains_available else 0.0
            v = q * to_L
            self.flow[f"FILL_{t}"] = q
            if v > 0:
                spx = mains_species(cp, v)
                self.ledger.external_supply_L[t] += v
                for k in SPECIES:
                    self.ledger.external_species[k] += spx[k]
                self._add(t, v, spx)

        # --- chemistry: CO2 degassing in MAIN, probe lag (circulation speeds it up)
        if main.volume > 1e-9:
            conc = main.conc()
            h = 10 ** -self.chem.ph(conc)
            co2 = conc["CT"] * h / (h + 10 ** -6.35)
            excess = co2 - 0.0136   # mmol/L above air-equilibrium dissolved CO2
            if excess > 0:
                dct = excess * (1 - math.exp(-dt / cp.degas_tau_s)) * main.volume
                main.sp["CT"] -= dct
                self.ledger.degassed_CT += dct
        flows_main = (sum(self.flow[s] for s in self.sectors) + self.flow["RETURN"] + self.flow["FILL_MAIN"]
                      + sum(self.flow[f"TP_{s}"] for s in STOCKS))
        self.circulating = flows_main > 1e-6
        tau = cp.probe_tau_circulating_s if self.circulating else cp.probe_tau_still_s
        if main.volume > 1e-9:
            self.probe.follow(main.conc(), dt, tau)
        self.tick += 1

    def _to_channels(self, sid: str, parcel: Parcel) -> None:
        chans = self.sectors[sid].channels
        w = {c: sum(self.outlets[c]) for c in chans}
        tot = sum(w.values())
        dt = self.p.dt_s
        for c in chans:
            share = w[c] / tot if tot > 0 else 1.0 / len(chans)
            v = parcel.volume * share
            self.channels[c].put(v, {k: x * share for k, x in parcel.sp.items()})
            self.channel_in[c] = v / (dt / 60.0)
            self.ledger.delivered_by_channel_L[c] += v

    def _add(self, tank: str, v: float, sp: dict[str, float]) -> None:
        t = self.tanks[tank]
        t.put(v, sp)
        over = t.volume - self.capacity[tank]
        if over > 0:
            vv, spx = t.take(over)
            self._spill(tank, vv, spx)

    def _spill(self, where: str, v: float, sp: dict[str, float]) -> None:
        self.ledger.spill_L[where] = self.ledger.spill_L.get(where, 0.0) + v
        for k in SPECIES:
            self.ledger.spilled_species[k] += sp[k]

    def _trip(self, reason: str) -> None:
        if self.drive.tripped is None:
            self.drive.tripped = reason
            self._hazard("DRIVE_TRIP", reason)

    def _hazard(self, kind: str, detail: str) -> None:
        if self.hazards and self.hazards[-1]["kind"] == kind and self.hazards[-1]["detail"] == detail:
            self.hazards[-1]["count"] += 1
            return
        self.hazards.append({"tick": self.tick, "kind": kind, "detail": detail, "count": 1})
        del self.hazards[:-50]

    # ----------------------------------------------------------------------------- accounting
    def stored_L(self) -> float:
        return (sum(t.volume for t in self.tanks.values()) + sum(c.volume for c in self.channels.values())
                + sum(x.volume for q in self.supply.values() for x in q) + sum(x.volume for x in self.drain))

    def stored_species(self) -> dict[str, float]:
        out = empty()
        parcels = (list(self.tanks.values()) + list(self.channels.values())
                   + [x for q in self.supply.values() for x in q] + list(self.drain))
        for pc in parcels:
            for k in SPECIES:
                out[k] += pc.sp[k]
        return out

    def ledger_residual_L(self) -> float:
        lg = self.ledger
        return (self.stored_L() + lg.consumption_L + sum(lg.spill_L.values())
                - sum(lg.external_supply_L.values()) - lg.initial_stored_L)

    def species_residual(self) -> dict[str, float]:
        lg, now = self.ledger, self.stored_species()
        out = {}
        for k in SPECIES:
            out[k] = (now[k] + lg.consumed_species[k] + lg.spilled_species[k] - lg.external_species[k]
                      - lg.initial_species[k] + (lg.degassed_CT if k == "CT" else 0.0))
        return out

    @property
    def sim_time_s(self) -> float:
        return self.tick * self.p.dt_s

    # ----------------------------------------------------------------------------- views
    def bulk_ph_ec(self) -> tuple[float | None, float | None]:
        m = self.tanks["MAIN"]
        if m.volume <= 1e-9:
            return None, None
        c = m.conc()
        return self.chem.ph(c), self.chem.ec(c)

    def probe_ph_ec(self) -> tuple[float, float]:
        return self.chem.ph(self.probe.conc), self.chem.ec(self.probe.conc)

    def stock_strength(self, stock: str) -> float:
        """Synthetic strength of a stock tank relative to freshly prepared (1.0). Twin truth only."""
        t, cp = self.tanks[stock], self.p.chemistry
        if t.volume <= 1e-9:
            return 0.0
        if stock == "ACID":
            return max(0.0, (-t.sp["ALK"] / t.volume) / cp.acid_stock_meq_L)
        return (t.sp[stock] / t.volume) / cp.stock_strength[stock]

    def state_hash(self) -> str:
        h = hashlib.sha256()
        parts = [self.tick, sorted((k, t.volume, sorted(t.sp.items())) for k, t in self.tanks.items()),
                 sorted((c, ch.volume) for c, ch in self.channels.items()),
                 [[x.volume for x in q] for q in self.supply.values()], [x.volume for x in self.drain],
                 (self.drive.run_cmd, self.drive.speed, self.drive.fault, self.drive.tripped),
                 sorted((k, c.cmd, c.closed, c.fault) for k, c in self.contactors.items()),
                 sorted((k, a.cmd, a.position, a.fault) for k, a in self.valves.items()),
                 sorted((k, a.cmd, a.position, a.fault) for k, a in self.pumps.items()),
                 sorted(self.probe.conc.items()), self.mains_available, self.safety_chain_ok,
                 sorted((c, tuple(o)) for c, o in self.outlets.items()), self.ledger.consumption_L,
                 sorted(self.ledger.spill_L.items())]
        h.update(repr(parts).encode())
        return h.hexdigest()

    def snapshot(self) -> dict:
        lg, d = self.ledger, self.p.dynamics
        bph, bec = self.bulk_ph_ec()
        pph, pec = self.probe_ph_ec()
        return {
            "topology": "installation", "tick": self.tick, "sim_time_s": round(self.sim_time_s, 6),
            "provenance": {"facts": "OWNER_CONFIRMED (approximate where stated)", "dynamics": d.provenance,
                           "chemistry": f"SYNTHETIC FIXTURE {self.chem.fixture_id} (not a calibrated model)"},
            "tanks": {k: {"volume_L": t.volume, "capacity_L": self.capacity[k],
                          "fraction": t.volume / self.capacity[k]} for k, t in self.tanks.items()},
            "stock_strength_synthetic": {s: self.stock_strength(s) for s in STOCKS},
            "channels": {str(c): {"volume_L": ch.volume, "retained_L": self.retained_L,
                                  "capacity_L": self.channel_capacity_L, "inflow_L_min": self.channel_in[c],
                                  "drain_L_min": self.channel_out[c], "sector": self.sector_of[c],
                                  "outlets": list(self.outlets[c])} for c, ch in self.channels.items()},
            "drive": {"run_requested": self.drive.run_cmd, "speed": self.drive.speed, "fault": self.drive.fault,
                      "tripped": self.drive.tripped},
            "contactors": {k: {"requested": c.cmd, "closed": c.closed, "fault": c.fault}
                           for k, c in self.contactors.items()},
            "main_pumps": {k: {"motor_speed": v, "fault": self.motor_fault[k]} for k, v in self.motor_speed.items()},
            "pumps": {k: {"requested": a.cmd, "speed": a.position, "fault": a.fault} for k, a in self.pumps.items()},
            "valves": {k: {"requested": a.cmd, "position": a.position, "fault": a.fault}
                       for k, a in self.valves.items()},
            "flows_L_min": dict(self.flow), "pressures_bar": dict(self.pressure),
            "supply_transit_L": {s: sum(x.volume for x in q) for s, q in self.supply.items()},
            "drain_transit_L": sum(x.volume for x in self.drain),
            "mains_available": self.mains_available, "safety_chain_ok": self.safety_chain_ok,
            "circulating": self.circulating,
            "chemistry": {"bulk_ph": bph, "bulk_ec_mS_cm": bec, "probe_ph": pph, "probe_ec_mS_cm": pec,
                          "label": "SYNTHETIC: qualitative controller test fixture only"},
            "hazards": list(self.hazards[-10:]),
            "ledger": {"initial_stored_L": lg.initial_stored_L, "stored_L": self.stored_L(),
                       "external_supply_L": dict(lg.external_supply_L), "consumption_L": lg.consumption_L,
                       "spill_L": {k: v for k, v in lg.spill_L.items() if v}, "spill_total_L": sum(lg.spill_L.values()),
                       "unmet_demand_L": dict(lg.unmet_demand_L),
                       "delivered_by_sector_L": dict(lg.delivered_by_sector_L),
                       "delivered_by_channel_L": {str(k): v for k, v in lg.delivered_by_channel_L.items()},
                       "drained_to_sump_L": lg.drained_to_sump_L, "returned_to_main_L": lg.returned_to_main_L,
                       "transferred_L": dict(lg.transferred_L), "degassed_CT_mmol": lg.degassed_CT,
                       "residual_L": self.ledger_residual_L(), "species_residual": self.species_residual()},
        }
