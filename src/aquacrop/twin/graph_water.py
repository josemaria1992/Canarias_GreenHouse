"""Water over the explicit hydraulic graph (0.7.0, ADR-0026): a bounded causal water balance, not a hydraulic solver.

Order of one fixed step (the same at every speed and batch size):
1. the actuator states committed before this step (valves open, pumps running, after their travel/start delay);
2. the paths water can take: the graph's explicit paths from each water source or tank to each tank or sector
   (`topology.validation.paths_from`, computed once per graph digest); a path is active when every valve on it is
   open and every pump on it runs; water leaves a tank only through a running pump, a source pushes by itself (its
   configured supply), and a sector's drain and a tank's overflow run by gravity;
3. allocation: one rate per driver (the pumps of a path in series give their smallest rate; a source its supply),
   shared once between its active paths; a source's supply is shared once between everything it feeds; a tank
   cannot give more than it holds in the step; a full tank takes no more, and what still arrives goes to its
   overflow path or is counted as spilled;
4. integration over the same step: tanks, the drain in transit (a sector returns its drain share after its
   delay), litres delivered to sectors, and every meter's flow;
5. the ledger: initial stored + supplied = stored + in transit + used by sectors + drained away + spilled. Its
   residual must stay within the tolerance, else the model stops with an error (the run is not valid evidence).

Parameters come from the equipment items' properties, each with its provenance. A missing one is never replaced
by a convenient default: the pump without a rated flow moves no water and is listed NOT_MODELLED, and so is a tank
without a capacity or a source without a supply rate. Pressure, chemistry and suction are NOT_MODELLED here.
Coordinates never enter: the drawing is not read at all.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..topology.definitions import Topology, digest as topo_digest
from ..topology.validation import paths_from

MODEL = "graph-water-1"
TOLERANCE_L = 1e-6


class ModelError(RuntimeError):
    pass


@dataclass
class Params:
    kind: dict[str, str]                       # node -> kind
    item: dict[str, str | None]
    rate: dict[str, float | None]              # pump -> L/min (rated)
    supply: dict[str, float | None]            # source -> L/min
    capacity: dict[str, float | None]          # tank -> L
    initial: dict[str, float]                  # tank -> L at the start of a run
    drain_fraction: dict[str, float]           # sector -> share returned by its drain
    drain_delay_s: dict[str, float]
    level_full: dict[str, float | None]        # tank -> the level reading meaning full
    provenance: dict[str, str] = field(default_factory=dict)

    def digest_parts(self) -> dict:
        return {k: getattr(self, k) for k in ("rate", "supply", "capacity", "initial", "drain_fraction",
                                              "drain_delay_s", "level_full")}


def params_from(t: Topology, views: dict) -> Params:
    """Read every number from the items' properties; unknown stays None."""
    p = Params({}, {}, {}, {}, {}, {}, {}, {}, {})
    for n in t.nodes:
        p.kind[n.id], p.item[n.id] = n.kind, n.item
        v = views.get(n.item) if n.item else None
        props = dict(v.body.properties) if v is not None else {}
        p.provenance[n.id] = str(props.get("provenance") or ("DEMO" if v is not None and v.body.properties.get("demo") else "UNKNOWN"))
        if n.kind == "pump":
            r = props.get("rated_flow_l_min")
            p.rate[n.id] = float(r) if isinstance(r, (int, float)) and r > 0 else None
        elif n.kind == "source":
            r = props.get("supply_l_min")
            p.supply[n.id] = float(r) if isinstance(r, (int, float)) and r > 0 else None
        elif n.kind == "tank":
            c = props.get("capacity_l")
            p.capacity[n.id] = float(c) if isinstance(c, (int, float)) and c > 0 else None
            i = props.get("sim_initial_l")
            p.initial[n.id] = min(float(i), p.capacity[n.id] or float(i)) if isinstance(i, (int, float)) and i >= 0 else 0.0
            lf = props.get("level_full")
            p.level_full[n.id] = float(lf) if isinstance(lf, (int, float)) and lf > 0 else None
        elif n.kind == "sector":
            df = props.get("drain_fraction")
            p.drain_fraction[n.id] = float(df) if isinstance(df, (int, float)) and 0 <= df <= 1 else 0.0
            dd = props.get("drain_delay_s")
            p.drain_delay_s[n.id] = float(dd) if isinstance(dd, (int, float)) and dd >= 0 else 0.0
    return p


class GraphWater:
    def __init__(self):
        self.reset()
        self._paths_key = None
        self._paths: list[dict] = []

    def reset(self) -> None:
        self.t = 0.0
        self.vol: dict[str, float] = {}
        self.transit: dict[str, deque] = {}       # sector -> deque of (release_at, litres)
        self.delivered: dict[str, float] = {}
        self.used: dict[str, float] = {}
        self.drained_away = 0.0
        self.spilled = 0.0
        self.supplied = 0.0
        self.initial_total: float | None = None
        self.adjustments = 0.0                    # explicit level resets/resizes (scenario events)
        self.flows: dict[str, float] = {}         # edge -> L/min this step
        self.node_flow: dict[str, float] = {}     # pass-through node (meter, pump, valve) -> L/min
        self.sector_rate: dict[str, float] = {}
        self.source_rate: dict[str, float] = {}
        self.notes: list[str] = []
        self.not_modelled: dict[str, str] = {}
        self.events: list[dict] = []

    # ------------------------------------------------------------------ scenario inputs
    def set_level(self, tank: str, litres: float, p: Params, why: str) -> None:
        """An explicit mass adjustment (a scenario's initial level, a reset): recorded, never hidden."""
        cap = p.capacity.get(tank)
        if cap is None:
            raise ValueError(f"{tank} has no capacity: it is not modelled")
        self._init(p)
        new = max(0.0, min(cap, float(litres)))
        old = self.vol.get(tank, p.initial.get(tank, 0.0))
        self.vol[tank] = new
        self.adjustments += new - old
        self.events.append({"t": self.t, "kind": "MASS_ADJUSTMENT", "tank": tank, "litres": new - old, "why": why})

    # ------------------------------------------------------------------ one step
    def paths(self, t: Topology) -> list[dict]:
        key = topo_digest(t)
        if key != self._paths_key:
            out = []
            for n in t.nodes:
                if n.kind in ("source", "tank"):
                    for path in paths_from(t, n.id, "out"):
                        out.append(dict(path, frm=n.id))
                elif n.kind == "sector":
                    for path in paths_from(t, n.id, "drain"):
                        out.append(dict(path, frm=n.id, gravity=True))
                if n.kind == "tank":
                    for path in paths_from(t, n.id, "overflow"):
                        out.append(dict(path, frm=n.id, overflow=True))
            seen, uniq = set(), []
            for path in out:
                k = (path["frm"], tuple(path["edges"]), path.get("overflow", False), path.get("gravity", False))
                if k not in seen:
                    seen.add(k)
                    uniq.append(path)
            self._paths, self._paths_key = uniq, key
        return self._paths

    def _init(self, p: Params) -> None:
        """Tanks start at their configured initial water; the ledger's starting total is taken once."""
        for n, k in p.kind.items():
            if k == "tank" and p.capacity[n] is not None:
                self.vol.setdefault(n, p.initial.get(n, 0.0))
        if self.initial_total is None:
            self.initial_total = sum(self.vol.values())

    def step(self, dt: float, t: Topology, p: Params, is_on) -> None:
        """`is_on(node)`: True when a valve is open or a pump runs this step (after its delay and faults)."""
        if dt < 0:
            raise ValueError("a step is not negative")
        self.notes, self.not_modelled = [], {}
        for n, k in p.kind.items():
            if k == "tank" and p.capacity[n] is None:
                self.not_modelled[n] = "capacity unknown: the tank holds no modelled water"
            elif k == "pump" and p.rate[n] is None:
                self.not_modelled[n] = "rated flow unknown: this pump moves no modelled water"
            elif k == "source" and p.supply[n] is None:
                self.not_modelled[n] = "supply rate unknown: this source gives no modelled water"
        self._init(p)
        flows: dict[tuple, float] = {}            # path index -> L/min
        paths = self.paths(t)
        groups: dict[tuple, list[int]] = {}
        for i, path in enumerate(paths):
            frm, kinds = path["frm"], p.kind
            if kinds[frm] == "tank" and (p.capacity.get(frm) is None or self.vol.get(frm, 0.0) <= 1e-12):
                continue
            if kinds[path["to"]] == "tank" and p.capacity.get(path["to"]) is None:
                continue
            ok, pumps = True, []
            for n in path["nodes"]:
                if kinds[n] == "valve" and not is_on(n):
                    ok = False
                    break
                if kinds[n] == "pump":
                    if not is_on(n):
                        ok = False
                        break
                    pumps.append(n)
            if not ok:
                continue
            gravity = path.get("gravity") or path.get("overflow")
            if kinds[frm] == "tank" and not pumps and not gravity:
                continue                          # a tank's outlet moves water only through a running pump
            if kinds[frm] == "sector":
                continue                          # drains move what arrived earlier, below
            if path.get("overflow"):
                continue                          # overflow carries only the excess, below
            if any(p.rate.get(x) is None for x in pumps):
                continue
            if kinds[frm] == "source" and p.supply.get(frm) is None:
                continue
            key = (frm, frozenset(pumps)) if pumps else (frm,)
            groups.setdefault(key, []).append(i)
        # 3. one rate per driver group, shared once between its active paths
        for key, idx in groups.items():
            frm = key[0]
            rate = min(p.rate[x] for x in key[1]) if len(key) > 1 else p.supply[frm]
            for i in idx:
                flows[i] = rate / len(idx)
        # a source's supply is shared once between everything it feeds
        for s in [n for n, k in p.kind.items() if k == "source"]:
            mine = [i for i in flows if paths[i]["frm"] == s]
            total = sum(flows[i] for i in mine)
            if p.supply.get(s) is not None and total > p.supply[s] + 1e-12:
                f = p.supply[s] / total
                for i in mine:
                    flows[i] *= f
        # a tank cannot give more than it holds in this step
        for tk in [n for n, k in p.kind.items() if k == "tank"]:
            mine = [i for i in flows if paths[i]["frm"] == tk]
            out_L = sum(flows[i] for i in mine) * dt / 60.0
            have = self.vol.get(tk, 0.0)
            if out_L > have + 1e-12 and out_L > 0:
                f = have / out_L
                for i in mine:
                    flows[i] *= f
                self.notes.append(f"{tk} ran empty: its pumps run dry")
        # 4. integrate, destinations first in a stable order; a full tank's excess goes to its overflow or is spilled
        self.flows, self.node_flow, self.sector_rate, self.source_rate = {}, {}, {}, {}
        incoming: dict[str, float] = {}
        for i, q in flows.items():
            incoming[paths[i]["to"]] = incoming.get(paths[i]["to"], 0.0) + q * dt / 60.0
        for i, q in sorted(flows.items()):
            path = paths[i]
            frm = path["frm"]
            if p.kind[frm] == "tank":
                self.vol[frm] -= q * dt / 60.0
            else:
                self.supplied += q * dt / 60.0
                self.source_rate[frm] = self.source_rate.get(frm, 0.0) + q
            self._mark(path, q)
        for dest in sorted(incoming):
            litres = incoming[dest]
            if p.kind[dest] == "tank":
                room = p.capacity[dest] - self.vol.get(dest, 0.0)
                take = min(litres, max(0.0, room))
                self.vol[dest] = self.vol.get(dest, 0.0) + take
                excess = litres - take
                if excess > 1e-12:
                    self._overflow(dest, excess, dt, p, is_on, paths)
            else:                                 # a sector: used, or drained after its delay
                self.delivered[dest] = self.delivered.get(dest, 0.0) + litres
                self.sector_rate[dest] = litres * 60.0 / dt if dt > 0 else 0.0
                df = p.drain_fraction.get(dest, 0.0)
                self.used[dest] = self.used.get(dest, 0.0) + litres * (1 - df)
                if df > 0:
                    self.transit.setdefault(dest, deque()).append((self.t + p.drain_delay_s.get(dest, 0.0), litres * df))
        self.t += dt
        # the drain in transit arrives by gravity along the sector's drain paths, or leaves the system
        for sec, q in self.transit.items():
            due = 0.0
            while q and q[0][0] <= self.t + 1e-9:
                due += q.popleft()[1]
            if due <= 0:
                continue
            routes = [x for x in paths if x["frm"] == sec and x.get("gravity") and all(
                p.kind[n] != "valve" or is_on(n) for n in x["nodes"]) and not any(p.kind[n] == "pump" for n in x["nodes"])]
            if not routes:
                self.drained_away += due
                continue
            share = due / len(routes)
            for x in routes:
                to = x["to"]
                if p.kind[to] == "tank" and p.capacity.get(to) is not None:
                    room = p.capacity[to] - self.vol.get(to, 0.0)
                    take = min(share, max(0.0, room))
                    self.vol[to] = self.vol.get(to, 0.0) + take
                    if share - take > 1e-12:
                        self.spilled += share - take
                        self.notes.append(f"{to} is full: drained water spilled")
                else:
                    self.drained_away += share
                self._mark(x, share * 60.0 / dt if dt > 0 else 0.0)
        self._ledger()

    def _overflow(self, tank, excess, dt, p, is_on, paths) -> None:
        routes = [x for x in paths if x["frm"] == tank and x.get("overflow")
                  and all(p.kind[n] != "valve" or is_on(n) for n in x["nodes"])]
        for x in routes:
            to = x["to"]
            if p.kind[to] == "tank" and p.capacity.get(to) is not None:
                room = p.capacity[to] - self.vol.get(to, 0.0)
                take = min(excess, max(0.0, room))
                self.vol[to] = self.vol.get(to, 0.0) + take
                excess -= take
                self._mark(x, take * 60.0 / dt if dt > 0 else 0.0)
            elif p.kind[to] == "sector":
                self.delivered[to] = self.delivered.get(to, 0.0) + excess
                self.used[to] = self.used.get(to, 0.0) + excess
                self._mark(x, excess * 60.0 / dt if dt > 0 else 0.0)
                excess = 0.0
            if excess <= 1e-12:
                return
        if excess > 1e-12:
            self.spilled += excess
            self.notes.append(f"{tank} is full: {excess:.2f} L spilled")

    def _mark(self, path, q) -> None:
        for e in path["edges"]:
            self.flows[e] = self.flows.get(e, 0.0) + q
        for n in path["nodes"]:
            self.node_flow[n] = self.node_flow.get(n, 0.0) + q

    def stored(self) -> float:
        return sum(self.vol.values())

    def in_transit(self) -> float:
        return sum(l for q in self.transit.values() for _, l in q)

    def ledger(self) -> dict:
        start = (self.initial_total or 0.0) + self.adjustments
        now = self.stored() + self.in_transit() + sum(self.used.values()) + self.drained_away + self.spilled
        return {"initial_L": round(self.initial_total or 0.0, 6), "adjustments_L": round(self.adjustments, 6),
                "supplied_L": round(self.supplied, 6), "stored_L": round(self.stored(), 6),
                "in_transit_L": round(self.in_transit(), 6), "used_by_sectors_L": round(sum(self.used.values()), 6),
                "drained_away_L": round(self.drained_away, 6), "spilled_L": round(self.spilled, 6),
                "residual_L": start + self.supplied - now}

    def _ledger(self) -> None:
        r = self.ledger()["residual_L"]
        scale = max(1.0, (self.initial_total or 0.0) + self.supplied)
        if abs(r) > TOLERANCE_L * scale:
            raise ModelError(f"water ledger does not balance: residual {r:.6f} L (tolerance {TOLERANCE_L * scale:.2e} L)")

    # ------------------------------------------------------------------ what sensors read
    def readings(self, t: Topology, p: Params) -> dict[str, float | None]:
        """Engineering values per sensor/meter node; None is NOT MODELLED (never a plausible number)."""
        out: dict[str, float | None] = {}
        for n in t.nodes:
            if n.kind == "meter":
                out[n.id] = round(self.node_flow.get(n.id, 0.0), 6)
        for m in t.measures:
            if m.node is not None and p.kind.get(m.node) == "tank":
                cap, lf = p.capacity.get(m.node), p.level_full.get(m.node)
                out[m.sensor] = None if cap is None or lf is None else self.vol.get(m.node, 0.0) / cap * lf
            else:
                out[m.sensor] = None              # pressure, EC, pH and temperature taps: not modelled
        return out

    def view(self, t: Topology, p: Params) -> dict:
        return {"model": MODEL, "t_s": round(self.t, 3),
                "tanks": {n: {"L": round(self.vol.get(n, 0.0), 3), "capacity_L": p.capacity.get(n),
                              "pct": round(100 * self.vol.get(n, 0.0) / p.capacity[n], 1) if p.capacity.get(n) else None}
                          for n, k in p.kind.items() if k == "tank"},
                "edges": {e: round(q, 3) for e, q in self.flows.items() if q > 0},
                "nodes": {n: round(q, 3) for n, q in self.node_flow.items() if q > 0},
                "sectors": {s: {"rate_L_min": round(self.sector_rate.get(s, 0.0), 3), "delivered_L": round(self.delivered.get(s, 0.0), 3)}
                            for s, k in p.kind.items() if k == "sector"},
                "sources": {s: round(q, 3) for s, q in self.source_rate.items()},
                "ledger": self.ledger(), "notes": list(self.notes), "not_modelled": dict(self.not_modelled),
                "provenance": dict(p.provenance), "events": self.events[-20:]}
