"""Water in the digital twin (0.6.4, owner request): water moves along the drawing's pipes.

The network is the twin's layout: every pipe is a directed line (from its first point to its last), its ends
attached to symbols or to another pipe, and pumps, valves and flow meters drawn on it sit inline. Each step:

* a water source (mains) pushes water through every pipe it reaches, as long as every valve on the way is open
  and no stopped pump is in the way;
* a running pump draws from the tank its pipe starts at and pushes to whatever the pipe reaches (another tank, a
  sector); a pump on an empty tank delivers nothing;
* a sector sends a share of what it receives back by gravity along the pipes that start at it (drainage);
* a tank rises with what flows in and falls with what flows out; a full tank takes nothing more.

Rates are demo numbers, labelled as such: a source gives its `flow_L_min` (default 60 L/min), shared between
the places it reaches; a pump gives its item's rated flow, else its type's demo flow, else 20 L/min, shared between the places
it reaches. Valves and pumps count as open or running from their twin outputs, after their travel or start time.
Nothing here is physical evidence: it is a picture of what the drawing and the program imply.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MAINS_L_MIN = 60.0
PUMP_L_MIN = 20.0
DRAIN_SHARE = 0.3            # of what a sector receives, back by gravity
ON_PIPE = 8.0                # a symbol this close to a pipe sits on it
JOIN = 6.0                   # a pipe end this close to another pipe joins it
SNAP = 24.0
START_PCT = 20.0
_BOXES = ("tank", "sector", "box")


def _seglen(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _project(points, x, y):
    """(distance along the pipe, distance from the pipe) of the nearest point of the pipe to (x, y)."""
    best, along = (math.inf, 0.0), 0.0
    for a, b in zip(points, points[1:]):
        L = _seglen(a, b)
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - a[0]) * (b[0] - a[0]) + (y - a[1]) * (b[1] - a[1])) / (L * L)))
        px, py = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        d = math.hypot(px - x, py - y)
        if d < best[0]:
            best = (d, along + t * L)
        along += L
    return best[1], best[0]


def _length(points):
    return sum(_seglen(a, b) for a, b in zip(points, points[1:]))


def _near(nodes, x, y):
    best = None
    for n in nodes:
        if n["kind"] == "label":
            continue
        px, py = n["x"], n["y"]
        if n["kind"] in _BOXES:
            w, h = n.get("w") or 100, n.get("h") or 100
            px, py = max(n["x"], min(n["x"] + w, x)), max(n["y"], min(n["y"] + h, y))
        d = math.hypot(px - x, py - y)
        if d <= SNAP and (best is None or d < best[0]):
            best = (d, n["id"])
    return best[1] if best else None


@dataclass
class Pipe:
    id: str
    length: float
    start_node: str | None
    end_node: str | None
    end_join: tuple[str, float] | None             # (pipe, position) the end flows into
    inline: list[tuple[float, str]] = field(default_factory=list)      # (position, node) of valves/pumps/sensors
    branches: list[tuple[float, str]] = field(default_factory=list)    # (position, pipe) that start on this pipe
    flow_item: str | None = None


class Network:
    """The drawing as a water network (built once per layout)."""

    def __init__(self, layout: dict):
        self.nodes = {n["id"]: n for n in layout["nodes"]}
        nodes = list(self.nodes.values())
        raw = layout["pipes"]
        self.pipes: dict[str, Pipe] = {}
        for p in raw:
            pts = [tuple(q) for q in p["points"]]
            start = p.get("from_node") or _near(nodes, *pts[0])
            end = p.get("to_node") or _near(nodes, *pts[-1])
            pipe = Pipe(p["id"], _length(pts), start, end, None, flow_item=p.get("flow_item"))
            for n in nodes:
                if n["kind"] not in ("valve", "pump", "sensor") or n["id"] in (start, end):
                    continue
                t, d = _project(pts, n["x"], n["y"])
                if d <= ON_PIPE and 2.0 < t < pipe.length - 2.0:
                    pipe.inline.append((t, n["id"]))
            pipe.inline.sort()
            self.pipes[p["id"]] = pipe
        for p in raw:                               # ends lying on another pipe join it
            me, pts = self.pipes[p["id"]], [tuple(q) for q in p["points"]]
            for q in raw:
                if q["id"] == p["id"]:
                    continue
                qpts = [tuple(x) for x in q["points"]]
                if me.start_node is None:
                    t, d = _project(qpts, *pts[0])
                    if d <= JOIN:
                        self.pipes[q["id"]].branches.append((t, p["id"]))
                if me.end_node is None and me.end_join is None:
                    t, d = _project(qpts, *pts[-1])
                    if d <= JOIN and t > 0.5:          # not onto the very start of a pipe it feeds
                        me.end_join = (q["id"], t)
        for pipe in self.pipes.values():
            pipe.branches.sort()
        self.starting_at: dict[str, list[str]] = {}
        for pipe in self.pipes.values():
            if pipe.start_node:
                self.starting_at.setdefault(pipe.start_node, []).append(pipe.id)

    # ------------------------------------------------------------------ where water from one origin can go
    def reach(self, pipe_id: str, s: float, gate, drivers: frozenset, seen: frozenset, out: list, used: tuple):
        """Follow water entering `pipe_id` at position `s`. `gate(node)` says "open", "closed", or "pump" with
        whether it runs. Each place reached is appended to `out` as (node, drivers, pipes used)."""
        if (pipe_id, round(s)) in seen or len(seen) > 200:
            return
        seen = seen | {(pipe_id, round(s))}
        pipe = self.pipes[pipe_id]
        used = used + (pipe_id,)
        # pumps drawn on the same pipe are alternatives (duty and standby): water passes if one of them runs
        pumps = [gate(n) for t, n in pipe.inline if t >= s and isinstance(gate(n), tuple)]
        running = {g[2] for g in pumps if g[1]}
        events = sorted([(t, 0, n) for t, n in pipe.inline if t >= s] + [(t, 1, b) for t, b in pipe.branches if t >= s])
        for t, kind, ref in events:
            if kind == 1:
                self.reach(ref, 0.0, gate, drivers, seen, out, used)
                continue
            g = gate(ref)
            if g == "closed":
                return
            if isinstance(g, tuple):               # ("pump", running, item)
                if not running:
                    return
                drivers = drivers | running
        if pipe.end_join:
            self.reach(pipe.end_join[0], pipe.end_join[1], gate, drivers, seen, out, used)
            return
        end = pipe.end_node
        if end is None:
            return
        kind = self.nodes[end]["kind"]
        if kind in ("tank", "sector"):
            out.append((end, drivers, used))
            return
        g = gate(end)
        if g == "closed":
            return
        if isinstance(g, tuple):
            if not g[1]:
                return
            drivers = drivers | {g[2]}
        for nxt in self.starting_at.get(end, []):
            self.reach(nxt, 0.0, gate, drivers, seen, out, used)


class Water:
    """Tank levels and flows of the twin, stepped with the simulation."""

    def __init__(self):
        self.levels: dict[str, float] = {}          # tank node -> litres
        self.caps: dict[str, float] = {}            # tank node -> the capacity its level was kept for
        self.state: dict = {"tanks": {}, "pipes": {}, "sensors": {}, "sectors": {}, "sources": {}, "notes": []}
        self._net: tuple[str, Network] | None = None
        self._flows: tuple | None = None            # (key, inflow, outflow, pipes, sources): reused while nothing changes

    def reset(self) -> None:
        self._flows = None
        self.levels.clear()
        self.caps.clear()
        self.state = {"tanks": {}, "pipes": {}, "sensors": {}, "sectors": {}, "sources": {}, "notes": []}

    def set_level(self, node: str, pct: float, layout: dict) -> None:
        n = next((x for x in layout["nodes"] if x["id"] == node and x["kind"] == "tank"), None)
        if n is None:
            raise ValueError(f"{node} is not a tank of the drawing")
        cap = n.get("capacity_L") or 1000.0
        self.levels[node] = max(0.0, min(100.0, pct)) / 100.0 * cap

    def network(self, digest: str, layout: dict) -> Network:
        if self._net is None or self._net[0] != digest:
            self._net = (digest, Network(layout))
        return self._net[1]

    def step(self, dt: float, digest: str, layout: dict, item_on, item_rate, publish: bool = True) -> None:
        """`item_on(item)` is True/False/None for a pump or valve item; `item_rate(item)` a pump's L/min."""
        net = self.network(digest, layout)
        tanks = {i: n for i, n in net.nodes.items() if n["kind"] == "tank"}
        for i, n in tanks.items():
            cap = n.get("capacity_L") or 1000.0
            self.levels.setdefault(i, START_PCT / 100.0 * cap)
            if self.caps.get(i, cap) != cap:          # a tank resized in the drawing keeps its percentage
                self.levels[i] *= cap / self.caps[i]
            self.caps[i] = cap

        def gate(node_id):
            n = net.nodes[node_id]
            if n["kind"] == "valve":
                return "open" if n.get("item") and item_on(n["item"]) else "closed"
            if n["kind"] == "pump":
                return ("pump", bool(n.get("item") and item_on(n["item"])), n.get("item") or node_id)
            return "open"

        notes = []
        # the flows change only when a valve or pump changes or a tank becomes empty or full: trace the network
        # then, and in between only integrate the levels (the twin steps water every half second of its time)
        key = (digest, tuple(sorted((i, bool(n.get("item") and item_on(n["item"]))) for i, n in net.nodes.items()
                                    if n["kind"] in ("valve", "pump"))),
               tuple(sorted((i, self.levels.get(i, 0.0) <= 1e-6,
                             self.levels.get(i, 0.0) >= (n.get("capacity_L") or 1000.0) - 1e-6)
                            for i, n in tanks.items())))
        if self._flows is not None and self._flows[0] == key:
            _, inflow, outflow, pipes, sources = self._flows
            self._integrate(dt, tanks, inflow, outflow, notes)
            self._pending = (net, tanks, inflow, outflow, pipes, sources, notes)
            if publish:
                self.publish()
            return
        inflow: dict[str, float] = {}               # tank or sector -> L/min
        outflow: dict[str, float] = {}              # tank -> L/min
        pipes: dict[str, float] = {}

        def full(node):
            n = net.nodes[node]
            return n["kind"] == "tank" and self.levels.get(node, 0.0) >= (n.get("capacity_L") or 1000.0) - 1e-6

        def deliver(paths, rate_of, source_tank=None) -> float:
            given = 0.0
            groups: dict = {}
            for node, drivers, used in paths:
                if full(node) or node == source_tank:
                    continue
                key = frozenset(drivers) if drivers else None
                groups.setdefault(key, []).append((node, used))
            for key, dests in groups.items():
                total = rate_of(key)
                if total <= 0 or not dests:
                    continue
                share = total / len(dests)
                for node, used in dests:
                    given += share
                    inflow[node] = inflow.get(node, 0.0) + share
                    if source_tank:
                        outflow[source_tank] = outflow.get(source_tank, 0.0) + share
                    for pid in used:
                        pipes[pid] = pipes.get(pid, 0.0) + share
            return given

        def pump_rate(key):
            return min((item_rate(i) for i in key), default=0.0) if key else 0.0

        # 1. sources (mains): pressure of their own, a pump on the way only adds its own limit
        sources: dict[str, float] = {}
        for sid, n in net.nodes.items():
            if n["kind"] != "source":
                continue
            paths = []
            for pid in net.starting_at.get(sid, []):
                net.reach(pid, 0.0, gate, frozenset(), frozenset(), paths, ())
            src = n.get("flow_L_min") or MAINS_L_MIN
            sources[sid] = round(deliver(paths, lambda key, src=src: min(src, pump_rate(key)) if key else src), 2)
        # 2. tanks: only through a running pump, and only while there is water
        for tid in tanks:
            if self.levels.get(tid, 0.0) <= 1e-6:
                continue
            paths = []
            for pid in net.starting_at.get(tid, []):
                net.reach(pid, 0.0, gate, frozenset(), frozenset(), paths, ())
            deliver([p for p in paths if p[1]], pump_rate, source_tank=tid)
        # 3. sectors drain back by gravity
        for sid, n in net.nodes.items():
            if n["kind"] != "sector" or not inflow.get(sid):
                continue
            paths = []
            for pid in net.starting_at.get(sid, []):
                net.reach(pid, 0.0, gate, frozenset(), frozenset(), paths, ())
            back = inflow[sid] * DRAIN_SHARE
            deliver([(node, frozenset(), used) for node, _, used in paths if node != sid], lambda key, b=back: b)
        self._flows = (key, inflow, outflow, pipes, sources)
        self._integrate(dt, tanks, inflow, outflow, notes)
        self._pending = (net, tanks, inflow, outflow, pipes, sources, notes)
        if publish:
            self.publish()

    def publish(self) -> None:
        """Build what the page and the sensors see (once per batch of steps, not every step)."""
        if getattr(self, "_pending", None) is not None:
            self._publish(*self._pending)
            self._pending = None

    def _integrate(self, dt, tanks, inflow, outflow, notes) -> None:
        for tid, n in tanks.items():
            cap = n.get("capacity_L") or 1000.0
            lv = self.levels[tid] + (inflow.get(tid, 0.0) - outflow.get(tid, 0.0)) * dt / 60.0
            if lv <= 0 and outflow.get(tid):
                notes.append(f"{n.get('label') or tid} is empty: its pumps run dry")
            self.levels[tid] = max(0.0, min(cap, lv))

    def _publish(self, net, tanks, inflow, outflow, pipes, sources, notes) -> None:
        sensors = {}
        for nid, n in net.nodes.items():
            if n["kind"] != "sensor" or not n.get("item"):
                continue
            if n.get("tag") == "F":
                on = [p for p in net.pipes.values() if p.flow_item == n["item"]] or \
                     [p for p in net.pipes.values() if any(x == nid for _, x in p.inline)]
                sensors[nid] = {"value": round(sum(pipes.get(p.id, 0.0) for p in on), 2), "unit": "L/min",
                                "item": n["item"]}
            elif n.get("tag") == "L":
                tank = next((t for t in tanks.values() if t.get("item") == n["item"]), None)
                if tank:
                    sensors[nid] = dict(self._level_reading(tank), item=n["item"])
        self.state = {
            "tanks": {tid: {"L": round(self.levels[tid], 1), "capacity_L": n.get("capacity_L") or 1000.0,
                            "pct": round(100.0 * self.levels[tid] / (n.get("capacity_L") or 1000.0), 1),
                            "in_L_min": round(inflow.get(tid, 0.0), 2), "out_L_min": round(outflow.get(tid, 0.0), 2)}
                      for tid, n in tanks.items()},
            "pipes": {k: round(v, 2) for k, v in pipes.items() if v > 0},
            "sectors": {k: round(v, 2) for k, v in inflow.items() if net.nodes[k]["kind"] == "sector"},
            "sensors": sensors, "sources": sources, "notes": notes}

    def _level_reading(self, tank: dict) -> dict:
        cap = tank.get("capacity_L") or 1000.0
        frac = self.levels.get(tank["id"], 0.0) / cap
        if tank.get("full_at"):
            return {"value": round(frac * tank["full_at"], 1), "unit": "cm"}
        return {"value": round(100 * frac, 1), "unit": "%"}

    def item_values(self) -> dict[str, float]:
        """What the twin's sensor items read (engineering units), for those with a channel and a scaling."""
        return {s["item"]: s["value"] for s in self.state["sensors"].values() if "item" in s}
