"""Drawings of the hydraulic graph (0.7.0, ADR-0024): presentation only.

A diagram is one view (the whole installation, or one block): symbols placed for graph nodes and routes drawn for
graph edges. A route is a list of points where every pair of consecutive points differs in exactly one coordinate:
horizontal and vertical segments with 90-degree bends, nothing else (owner request 2026-10-25). Its ends sit on the
anchors of the edge's two ports. Moving a symbol, rerouting or crossing another pipe never changes the graph: the
digest of a diagram's presentation is kept apart from the graph's digest.

Routing is done by the browser editor (`web/router.js`); here routes are checked and made canonical. A route
without points is "not routed yet" (a draft the editor routes when it opens); a diagonal, a detached end, a
non-finite number or a reference to something that does not exist is refused.

0.7.3 (owner corrections): one drawing for Overview, Twin and Blocks, able to carry everything an earlier drawing had.
A symbol may have its own size (`w`, `h`: kept from the earlier drawing, or a tank's size from its capacity,
`tank_size`). `shapes` are drawn boxes, `placeholders` are earlier symbols not mapped to equipment yet (drawn and
marked, never part of the graph), and `sketches` are earlier pipes kept as they were drawn (a reference, never a
connection; diagonal allowed because they are not routes). Empty additions leave a drawing's digest unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .topology.definitions import ID, SYMBOLS, Topology, ports_of

EPS = 0.5                      # canonical resolution of coordinates (document units)
MAX_POINTS = 64
SIDE_VEC = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
ROT_SIDE = {0: "NESW", 90: "ESWN", 180: "SWNE", 270: "WNES"}   # side after rotating clockwise


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PortPos(_B):
    """Where this drawing puts one port of the symbol, when the user moved it (same port, same identity)."""
    side: Literal["N", "E", "S", "W"]
    offset: float = Field(ge=0.05, le=0.95)


class Symbol(_B):
    node: str = Field(pattern=ID)
    x: float = Field(ge=-100, le=10000)          # top-left of the box as drawn (after rotation)
    y: float = Field(ge=-100, le=10000)
    rot: Literal[0, 90, 180, 270] = 0
    label: str = Field(default="", max_length=80)
    ports: dict[str, PortPos] = Field(default_factory=dict)     # moved port anchors, in unrotated terms
    w: float | None = Field(default=None, gt=0, le=2000)        # its own size (unrotated); None: the kind's size
    h: float | None = Field(default=None, gt=0, le=2000)


class Pin(_B):
    """A segment the user placed: kept by automatic rerouting until "Reroute selected" removes it."""
    axis: Literal["x", "y"]                       # x: a vertical segment at this x; y: a horizontal one at this y
    value: float = Field(ge=-100, le=10000)


class Route(_B):
    edge: str = Field(pattern=ID)
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=MAX_POINTS)
    mode: Literal["AUTO", "MANUAL"] = "AUTO"
    pins: list[Pin] = Field(default_factory=list, max_length=16)


class Text(_B):
    id: str = Field(pattern=ID)
    x: float = Field(ge=-100, le=10000)
    y: float = Field(ge=-100, le=10000)
    text: str = Field(max_length=120)


class Shape(_B):
    """A drawn box with a caption (an area, a cabinet): presentation only."""
    id: str = Field(pattern=ID)
    x: float = Field(ge=-100, le=10000)
    y: float = Field(ge=-100, le=10000)
    w: float = Field(gt=0, le=5000)
    h: float = Field(gt=0, le=5000)
    label: str = Field(default="", max_length=80)


class Placeholder(_B):
    """A symbol of an earlier drawing that is not mapped to an equipment item of the graph yet: kept where it was,
    drawn and marked, never a node. `item` is the item the earlier drawing showed with it (for its reading)."""
    id: str = Field(pattern=ID)
    kind: str = Field(max_length=16)
    x: float = Field(ge=-100, le=10000)
    y: float = Field(ge=-100, le=10000)
    w: float = Field(gt=0, le=5000)
    h: float = Field(gt=0, le=5000)
    label: str = Field(default="", max_length=80)
    item: str | None = Field(default=None, max_length=48)
    why: str = Field(default="", max_length=240)


class Sketch(_B):
    """A pipe of an earlier drawing kept exactly as drawn: a reference, never a connection."""
    id: str = Field(pattern=ID)
    points: list[tuple[float, float]] = Field(min_length=2, max_length=MAX_POINTS)
    why: str = Field(default="", max_length=240)
    edge: str | None = Field(default=None, pattern=ID)      # the edge it became, when its route had to be corrected


class Diagram(_B):
    kind: Literal["AQUACROP_DIAGRAM"] = "AQUACROP_DIAGRAM"
    format: Literal[1] = 1
    view: str = Field(default="installation", pattern=ID)
    title: str = Field(default="Installation", max_length=80)
    width: float = Field(default=1200, ge=200, le=10000)
    height: float = Field(default=800, ge=200, le=10000)
    grid: float = Field(default=10, ge=2, le=100)
    symbols: list[Symbol] = Field(default_factory=list, max_length=400)
    routes: list[Route] = Field(default_factory=list, max_length=600)
    texts: list[Text] = Field(default_factory=list, max_length=200)
    shapes: list[Shape] = Field(default_factory=list, max_length=200)
    placeholders: list[Placeholder] = Field(default_factory=list, max_length=400)
    sketches: list[Sketch] = Field(default_factory=list, max_length=600)

    @model_validator(mode="after")
    def _unique(self):
        for what, ids in (("symbol", [s.node for s in self.symbols]), ("route", [r.edge for r in self.routes]),
                          ("text", [t.id for t in self.texts])):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                raise ValueError(f"a view draws each {what} once: {', '.join(dup)} appear twice")
        return self


def parse(doc: dict) -> Diagram:
    return Diagram.model_validate(doc)


# ---------------------------------------------------------------------- geometry shared with the editor
def box(kind: str, s: Symbol) -> tuple[float, float, float, float]:
    """(x, y, w, h) of the symbol as drawn: a quarter turn swaps width and height around the same top-left."""
    w, h = (s.w or SYMBOLS[kind]["w"]), (s.h or SYMBOLS[kind]["h"])
    if kind == "junction":
        w, h = 0, 0
    return (s.x, s.y, h, w) if s.rot in (90, 270) else (s.x, s.y, w, h)


def tank_size(capacity_L: float | None) -> tuple[float, float]:
    """A tank's schematic size from its capacity (shared with web/hydraulic-editor.js): the area grows with the
    volume, so 20 000 L is visibly bigger than 5 000 L. Schematic only: not the tank's physical dimensions."""
    if not capacity_L or capacity_L <= 0:
        return SYMBOLS["tank"]["w"], SYMBOLS["tank"]["h"]
    k = min(2.0, max(0.6, (capacity_L / 5000.0) ** 0.5))
    return round(100 * k / 10) * 10, round(120 * k / 10) * 10


def anchor(kind: str, s: Symbol, port: str) -> tuple[tuple[float, float], str]:
    """Where a port sits on the drawn symbol, and the side it faces after rotation."""
    p = ports_of(kind)[port]
    moved = s.ports.get(port)
    side0, off = (moved.side, moved.offset) if moved else (p.side, p.offset)
    side = ROT_SIDE[s.rot]["NESW".index(side0)]
    x, y, w, h = box(kind, s)                    # the offset runs clockwise along its side, so a turn keeps it
    if kind == "junction":
        return (snap(x), snap(y)), side
    if side == "N":
        pt = (x + off * w, y)
    elif side == "E":
        pt = (x + w, y + off * h)
    elif side == "S":
        pt = (x + (1 - off) * w, y + h)
    else:
        pt = (x, y + (1 - off) * h)
    return (snap(pt[0]), snap(pt[1])), side


def snap(v: float) -> float:
    return round(v / EPS) * EPS


def canonical_points(points) -> list[tuple[float, float]]:
    """Snap to the canonical resolution, drop repeated points, merge collinear runs. A diagonal stays a diagonal
    (and is refused by `route_issues`): canonicalising never hides one."""
    pts: list[tuple[float, float]] = []
    for x, y in points:
        p = (snap(float(x)), snap(float(y)))
        if not pts or pts[-1] != p:
            pts.append(p)
    i = 1
    while i < len(pts) - 1:
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        # merge a straight run only; a run that doubles back stays (and is refused by `route_issues`)
        if (a[0] == b[0] == c[0] and min(a[1], c[1]) <= b[1] <= max(a[1], c[1])) or                 (a[1] == b[1] == c[1] and min(a[0], c[0]) <= b[0] <= max(a[0], c[0])):
            del pts[i]
        else:
            i += 1
    return pts


def orthogonal(points) -> bool:
    return all((a[0] == b[0]) != (a[1] == b[1]) for a, b in zip(points, points[1:]))


def doubles_back(points) -> bool:
    return any((a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]) for a, b, c in zip(points, points[1:], points[2:]))


def _segments(points):
    return list(zip(points, points[1:]))


def _overlap(s1, s2) -> bool:
    """Two collinear segments that share more than a point."""
    (a, b), (c, d) = s1, s2
    if a[1] == b[1] == c[1] == d[1]:
        lo1, hi1 = sorted((a[0], b[0]))
        lo2, hi2 = sorted((c[0], d[0]))
        return min(hi1, hi2) - max(lo1, lo2) > EPS
    if a[0] == b[0] == c[0] == d[0]:
        lo1, hi1 = sorted((a[1], b[1]))
        lo2, hi2 = sorted((c[1], d[1]))
        return min(hi1, hi2) - max(lo1, lo2) > EPS
    return False


def crossings(points_a, points_b) -> list[tuple[float, float]]:
    """Points where a horizontal segment of one route crosses a vertical segment of the other, strictly inside
    both: drawn as a gap (no connection). Touching ends are not crossings."""
    out = []
    for s in _segments(points_a):
        for t in _segments(points_b):
            for h, v in ((s, t), (t, s)):
                (h1, h2), (v1, v2) = h, v
                if h1[1] != h2[1] or v1[0] != v2[0]:
                    continue
                x, y = v1[0], h1[1]
                if min(h1[0], h2[0]) < x < max(h1[0], h2[0]) and min(v1[1], v2[1]) < y < max(v1[1], v2[1]):
                    out.append((x, y))
    return sorted(set(out))


def route_issues(d: Diagram, t: Topology) -> list[dict]:
    """What is wrong with the drawing against the graph. ERROR: refused on save. WARNING: shown for correction."""
    out = []

    def issue(rule, obj, message, fix, severity="ERROR"):
        out.append({"id": f"{rule}:{obj}", "severity": severity, "object": obj, "field": None, "rule": rule,
                    "message": message, "fix": fix})
    nodes = {n.id: n for n in t.nodes}
    edges = {e.id: e for e in t.edges}
    syms = {s.node: s for s in d.symbols}
    for s in d.symbols:
        if s.node not in nodes:
            issue("SYMBOL_NODE", s.node, f"symbol {s.node} draws nothing of the graph", "remove the symbol")
    for r in d.routes:
        e = edges.get(r.edge)
        if e is None:
            issue("ROUTE_EDGE", r.edge, f"a pipe is drawn for {r.edge}, which is not a connection of the graph",
                  "remove the drawn pipe or connect the ports")
            continue
        if not r.points:
            issue("ROUTE_UNROUTED", r.edge, f"pipe {r.edge} is not routed yet", "open the editor: it routes it",
                  "WARNING")
            continue
        if any(not (math.isfinite(x) and math.isfinite(y)) for x, y in r.points):
            issue("ROUTE_NUMBER", r.edge, f"pipe {r.edge} has a point that is not a number", "reroute it")
            continue
        pts = canonical_points(r.points)
        if len(pts) < 2:
            issue("ROUTE_SHORT", r.edge, f"pipe {r.edge} has no length", "reroute it")
            continue
        if not orthogonal(pts):
            issue("ROUTE_DIAGONAL", r.edge, f"pipe {r.edge} has a segment that is not horizontal or vertical",
                  "reroute it: pipes are drawn with horizontal and vertical segments only")
            continue
        if doubles_back(pts):
            issue("ROUTE_BACKTRACK", r.edge, f"pipe {r.edge} runs back over itself", "reroute it")
            continue
        for end, ref, pt, nxt in (("start", e.src, pts[0], pts[1]), ("end", e.dst, pts[-1], pts[-2])):
            s = syms.get(ref.node)
            n = nodes.get(ref.node)
            if s is None or n is None:
                issue("ROUTE_NO_SYMBOL", r.edge, f"pipe {r.edge}: {ref.node} is not drawn in this view",
                      "place its symbol, or draw the pipe in the view that shows both ends")
                continue
            if ref.port not in ports_of(n.kind):
                continue                         # the graph's own check reports it
            a, side = anchor(n.kind, s, ref.port)
            if pt != a:
                issue("ROUTE_DETACHED", r.edge, f"pipe {r.edge}: its {end} is not on {ref.node}'s {ref.port} port",
                      "reroute it; a pipe end stays on its port")
                continue
            vx, vy = SIDE_VEC[side]                # the pipe runs out of the port along the side it faces
            dx, dy = nxt[0] - pt[0], nxt[1] - pt[1]
            if (dx > 0) - (dx < 0) != vx or (dy > 0) - (dy < 0) != vy or abs(dx) + abs(dy) < 5:
                issue("ROUTE_PORT_SIDE", r.edge, f"pipe {r.edge}: its {end} does not run straight out of "
                      f"{ref.node}'s {ref.port} port", "reroute it; a pipe leaves and enters a port straight")
        if any(not (0 - 100 <= x <= d.width + 100 and -100 <= y <= d.height + 100) for x, y in pts):
            issue("ROUTE_BOUNDS", r.edge, f"pipe {r.edge} leaves the drawing", "reroute it", "WARNING")
    # unrelated pipes drawn on top of each other look connected: warn, never merge
    routed = [(r.edge, canonical_points(r.points)) for r in d.routes if len(r.points) >= 2]
    for i, (ea, pa) in enumerate(routed):
        for eb, pb in routed[i + 1:]:
            if any(_overlap(s1, s2) for s1 in _segments(pa) for s2 in _segments(pb)):
                issue("ROUTE_OVERLAP", f"{ea}+{eb}", f"pipes {ea} and {eb} run along the same line; they look joined "
                      "but are not", "move one of them apart", "WARNING")
    return out


def canonical(d: Diagram) -> dict:
    out = d.model_dump(mode="json")
    for r in out["routes"]:
        r["points"] = [list(p) for p in canonical_points(r["points"])]
    out["symbols"] = sorted(out["symbols"], key=lambda s: s["node"])
    out["routes"] = sorted(out["routes"], key=lambda r: r["edge"])
    out["texts"] = sorted(out["texts"], key=lambda x: x["id"])
    for s in out["symbols"]:                     # 0.7.3 additions leave earlier drawings' digests as they were
        for k in ("w", "h"):
            if s.get(k) is None:
                s.pop(k, None)
    for k in ("shapes", "placeholders", "sketches"):
        if out.get(k):
            out[k] = sorted(out[k], key=lambda x: x["id"])
        else:
            out.pop(k, None)
    return out


def presentation_digest(d: Diagram) -> str:
    return hashlib.sha256(json.dumps(canonical(d), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
