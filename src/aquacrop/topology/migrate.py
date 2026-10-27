"""From a 0.5.6-0.6.x drawing (`aquacrop.layout`) to a reviewed graph draft and an orthogonal drawing (0.7.0).

The old drawing is kept as it is, read-only, with its digest. What it says explicitly becomes a proposal:
* a symbol linked to an equipment item whose type has a water kind becomes that item's node;
* a pipe whose two ends name symbols (`from_node`, `to_node`) becomes an edge between the default ports of
  those kinds, unless the old water model also saw equipment drawn on it or another pipe joined it.
Everything the old model inferred from geometry (equipment lying on a pipe, a pipe end touching another pipe) is
listed as ambiguous with what the drawing suggests: a person decides whether it was a connection. Nothing
ambiguous becomes an edge, and the draft cannot be validated while an ambiguity is open. Old pipe points are not
reused: every route of the new drawing is made by the orthogonal router when the editor opens it.

0.7.1: drawings made before tank, sector and water source items existed show a tank by its level sensor and a sector
by its valve (or by nothing). Such a symbol becomes the tank, sector or source item **with the same name**, listed as
matched for review; its level sensor is proposed as measuring that tank (an association, never a pipe). When no item
of that kind has the name, the proposal lists it under `to_create` (create it on Equipment, then import again).
"""
from __future__ import annotations

import re

from .. import diagram as dg
from .. import layout as legacy
from .definitions import SYMBOLS, parse

DEFAULT_OUT = {"source": "out", "tank": "out", "pump": "out", "valve": "out", "meter": "out", "sector": "drain"}
DEFAULT_IN = {"tank": "in", "pump": "in", "valve": "in", "meter": "in", "sector": "in"}


def _edge_id(old: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]", "-", old.lower()).strip("-_")[:40] or "pipe"
    if not base[0].isalnum():
        base = "p" + base
    eid, n = base, 1
    while eid in taken:
        n += 1
        eid = f"{base}-{n}"
    return eid


def _kind_for(item_kinds: dict, node) -> str | None:
    k = item_kinds.get(node.item) if node.item else None
    if node.kind == "sensor" and node.tag == "F" and k == "meter":
        return "meter"
    return k


def _safe_id(old: str, taken: set[str]) -> str:
    sid = _edge_id(old, taken)
    taken.add(sid)
    return sid


SMALL = {"pump": 40, "valve": 40, "sensor": 30, "source": 40, "meter": 40}


def propose(lay: legacy.Layout, item_kinds: dict[str, str | None], names: dict[str, str] | None = None) -> dict:
    """The earlier drawing as one drawing of the new kind, faithfully (0.7.3): every symbol, label, box and pipe is
    kept where and as large as it was drawn. A symbol mapped to an equipment item with a water kind becomes its graph
    node; every other symbol stays as a marked placeholder. A pipe whose two ends name mapped symbols becomes an edge
    and keeps its drawn route when that route is horizontal/vertical and meets the ports; otherwise the edge is
    rerouted and the drawn route stays beside it as a reference sketch. Every other pipe stays as a sketch: nothing
    ambiguous becomes a connection, and touching geometry never joins anything."""
    names = names or {}
    nodes, symbols, unresolved, ambiguous = {}, [], [], []
    matched, to_create, measures = [], [], []
    texts, shapes, placeholders, sketches = [], [], [], []
    taken: set[str] = set()
    by_legacy: dict[str, str] = {}
    by_name: dict[tuple, str | None] = {}            # (name, kind) -> the one item with that name and kind
    for iid, k in item_kinds.items():
        key = ((names.get(iid) or "").strip().casefold(), k)
        by_name[key] = None if key in by_name else iid

    def placeholder(n, why):
        size = SMALL.get(n.kind, 40)
        w, h = (n.w or 100, n.h or 100) if n.kind in ("tank", "sector", "box") else (size, size)
        x, y = (n.x, n.y) if n.kind in ("tank", "sector", "box") else (n.x - w / 2, n.y - h / 2)
        placeholders.append(dg.Placeholder(id=_safe_id(n.id, taken), kind=n.kind, x=dg.snap(max(-100, x)),
                                           y=dg.snap(max(-100, y)), w=w, h=h, label=(n.label or n.id)[:80],
                                           item=n.item, why=why[:240]))

    for n in lay.nodes:
        if n.kind == "label":
            texts.append(dg.Text(id=_safe_id(n.id, taken), x=n.x, y=n.y, text=(n.label or n.sub or "")[:120]))
            continue
        if n.kind == "box":
            shapes.append(dg.Shape(id=_safe_id(n.id, taken), x=n.x, y=n.y, w=n.w or 100, h=n.h or 60, label=n.label[:80]))
            continue
        k = _kind_for(item_kinds, n)
        want = n.kind if n.kind in ("tank", "sector", "source") else None
        if want and k != want:                       # drawn as a tank/sector/source, linked to its sensor, valve or nothing
            cand = by_name.get(((n.label or "").strip().casefold(), want))
            if cand is None:
                why = (f"there is no {want} item called {n.label or n.id}"
                       + (f" (the drawing showed it with {names.get(n.item, n.item)})" if n.item else ""))
                to_create.append({"kind": want, "name": n.label or n.id, "legacy": n.id, "shown_by": n.item})
                unresolved.append({"legacy": n.id, "label": n.label or n.id, "kind": n.kind, "why": why,
                                   "fix": f"create a {want} named {n.label or n.id} on Equipment, then place it on this placeholder"})
                placeholder(n, why)
                continue
            matched.append({"legacy": n.id, "label": n.label, "item": cand, "kind": want,
                            "why": f"the {want} item with the same name" + (f"; the drawing showed it with {names.get(n.item, n.item)}"
                                                                            if n.item else "")})
            if n.item and k == "sensor":
                measures.append({"sensor": n.item, "node": cand, "mode": "TAP"})
            n = n.model_copy(update={"item": cand})
            k = want
        if not n.item or k is None or n.item in nodes:
            why = ("not linked to an equipment item" if not n.item else
                   f"{names.get(n.item, n.item)} is drawn twice" if n.item in nodes else
                   f"{names.get(n.item, n.item)} has no water kind (or does not exist)")
            (ambiguous if n.item in nodes else unresolved).append(
                {"legacy": n.id, "label": n.label or n.id, "kind": "SYMBOL_TWICE" if n.item in nodes else n.kind, "why": why,
                 "fix": "keep one symbol for it" if n.item in nodes else "map it to an equipment item, or leave it as a placeholder"})
            placeholder(n, why)
            continue
        nodes[n.item] = {"id": n.item, "kind": k, "item": n.item}
        by_legacy[n.id] = n.item
        big = n.kind in ("tank", "sector", "box")
        w, h = (n.w, n.h) if big and n.w and n.h else (None, None)
        sw, sh = w or SYMBOLS[k]["w"], h or SYMBOLS[k]["h"]
        x, y = (n.x, n.y) if big else (n.x - sw / 2, n.y - sh / 2)   # the old drawing placed small symbols by their centre
        symbols.append(dg.Symbol(node=n.item, x=dg.snap(max(-100, x)), y=dg.snap(max(-100, y)), label=n.label[:80], w=w, h=h))
    # what the old water model inferred from geometry: evidence for the reviewer, never a connection
    from ..twin.water import Network
    try:
        net = Network(legacy.canonical(lay))
    except Exception:                               # an unreadable old drawing: everything stays for review
        net = None
    edges, routes = [], []
    syms = {s.node: s for s in symbols}
    for p in lay.pipes:
        a, b = by_legacy.get(p.from_node or ""), by_legacy.get(p.to_node or "")
        info = net.pipes.get(p.id) if net else None
        inline = [by_legacy.get(x, x) for _, x in (info.inline if info else [])]
        joined = bool(info and (info.branches or info.end_join))
        why = None
        if not (a and b):
            why = "a pipe end is not attached to a symbol by name" + (f" (the drawing suggests {', '.join(inline)} on it)" if inline else "")
            ambiguous.append({"legacy": p.id, "kind": "PIPE_END", "why": why, "fix": "connect its ports explicitly in the editor"})
        elif inline or joined:
            why = (f"the old drawing had {', '.join(names.get(x, x) for x in inline)} drawn on this pipe" if inline
                   else "another pipe touched this one in the old drawing")
            ambiguous.append({"legacy": p.id, "kind": "INLINE" if inline else "JOIN", "why": why,
                              "suggests": {"from": a, "to": b, "through": inline},
                              "fix": "decide the connection: through that equipment, through a junction, or not connected"})
        elif nodes[a]["kind"] not in DEFAULT_OUT or nodes[b]["kind"] not in DEFAULT_IN:
            why = f"no default ports from a {nodes[a]['kind']} to a {nodes[b]['kind']}"
            ambiguous.append({"legacy": p.id, "kind": "PORTS", "why": why, "fix": "connect it in the editor"})
        if why:
            sketches.append(dg.Sketch(id=_safe_id(p.id, taken), points=[tuple(pt) for pt in p.points], why=why[:240]))
            continue
        eid = _edge_id(p.id, {e["id"] for e in edges})
        ka, kb = nodes[a]["kind"], nodes[b]["kind"]
        edges.append({"id": eid, "src": {"node": a, "port": DEFAULT_OUT[ka]},
                      "dst": {"node": b, "port": DEFAULT_IN[kb]}, "provenance": "UNKNOWN"})
        (pa, _), (pb, _) = dg.anchor(ka, syms[a], DEFAULT_OUT[ka]), dg.anchor(kb, syms[b], DEFAULT_IN[kb])
        drawn = dg.canonical_points([pa, *[tuple(x) for x in p.points[1:-1]], pb])
        routes.append({"edge": eid, "points": drawn, "mode": "MANUAL", "legacy": p.id, "original": p.points})
    graph = {"nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": edges,
             "measures": [m for m in measures if m["sensor"] in nodes and m["node"] in nodes]}
    topo = parse(graph)                              # raises if the proposal is malformed: a bug, never silent
    view = dg.Diagram(view="installation", title=lay.title, width=max(lay.width, 400), height=max(lay.height, 300),
                      symbols=symbols, routes=[dg.Route(edge=r["edge"], points=r["points"], mode="MANUAL") for r in routes],
                      texts=texts, shapes=shapes, placeholders=placeholders)
    # a drawn route that is not horizontal/vertical, or does not meet its ports, is rerouted; the original stays visible
    bad = {i["object"] for i in dg.route_issues(view, topo) if i["severity"] == "ERROR" and i["rule"].startswith("ROUTE_")}
    kept = []
    for r in routes:
        if r["edge"] in bad:
            kept.append(dg.Route(edge=r["edge"], mode="AUTO"))
            sketches.append(dg.Sketch(id=_safe_id(r["legacy"] + "-drawn", taken), points=[tuple(x) for x in r["original"]],
                                      edge=r["edge"], why="the pipe as it was drawn (not horizontal/vertical or off its ports): "
                                                          "shown for reference; the connection is rerouted"))
        else:
            kept.append(dg.Route(edge=r["edge"], points=r["points"], mode="MANUAL"))
    view = view.model_copy(update={"routes": kept, "sketches": sketches})
    return {"kind": "AQUACROP_MIGRATION_PROPOSAL", "source": {"kind": "AQUACROP_LAYOUT", "digest": legacy.digest(lay)},
            "topology": topo.model_dump(mode="json"), "diagram": view.model_dump(mode="json"),
            "ambiguous": ambiguous, "unresolved": unresolved, "matched": matched, "to_create": to_create,
            "kept": {"symbols": len(symbols), "placeholders": len(placeholders), "texts": len(texts), "shapes": len(shapes),
                     "routes_kept": sum(1 for r in kept if r.points), "routes_corrected": len(bad), "sketches": len(sketches)},
            "meaning": "the earlier drawing kept as drawn; explicit links became connections, nothing ambiguous did"}
