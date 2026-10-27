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


def propose(lay: legacy.Layout, item_kinds: dict[str, str | None], names: dict[str, str] | None = None) -> dict:
    names = names or {}
    nodes, symbols, unresolved, ambiguous = {}, [], [], []
    matched, to_create, measures = [], [], []
    by_legacy: dict[str, str] = {}
    by_name: dict[tuple, str | None] = {}            # (name, kind) -> the one item with that name and kind
    for iid, k in item_kinds.items():
        key = ((names.get(iid) or "").strip().casefold(), k)
        by_name[key] = None if key in by_name else iid
    for n in lay.nodes:
        if n.kind in ("label", "box"):
            continue
        k = _kind_for(item_kinds, n)
        want = n.kind if n.kind in ("tank", "sector", "source") else None
        if want and k != want:                       # drawn as a tank/sector/source, linked to its sensor, valve or nothing
            cand = by_name.get(((n.label or "").strip().casefold(), want))
            if cand is None:
                to_create.append({"kind": want, "name": n.label or n.id, "legacy": n.id, "shown_by": n.item})
                unresolved.append({"legacy": n.id, "label": n.label or n.id, "kind": n.kind,
                                   "why": f"there is no {want} item called {n.label or n.id}"
                                          + (f" (the drawing showed it with {names.get(n.item, n.item)})" if n.item else ""),
                                   "fix": f"create a {want} named {n.label or n.id} on Equipment, then Import from Overview again"})
                continue
            matched.append({"legacy": n.id, "label": n.label, "item": cand, "kind": want,
                            "why": f"the {want} item with the same name" + (f"; the drawing showed it with {names.get(n.item, n.item)}"
                                                                            if n.item else "")})
            if n.item and k == "sensor":
                measures.append({"sensor": n.item, "node": cand, "mode": "TAP"})
            n = n.model_copy(update={"item": cand})
            k = want
        if not n.item:
            unresolved.append({"legacy": n.id, "label": n.label or n.id, "kind": n.kind,
                               "why": "not linked to an equipment item",
                               "fix": f"create or choose the {n.kind} item it shows, then add it in the editor"})
            continue
        if k is None:
            unresolved.append({"legacy": n.id, "label": n.label or n.id, "kind": n.kind,
                               "why": f"{names.get(n.item, n.item)} has no water kind (or does not exist)",
                               "fix": "give its type a water kind, or leave it out of the graph"})
            continue
        if n.item in nodes:
            ambiguous.append({"legacy": n.id, "kind": "SYMBOL_TWICE", "why": f"{names.get(n.item, n.item)} is drawn twice",
                              "fix": "keep one symbol for it in this view"})
            continue
        nodes[n.item] = {"id": n.item, "kind": k, "item": n.item}
        by_legacy[n.id] = n.item
        w, h = SYMBOLS[k]["w"], SYMBOLS[k]["h"]
        if n.kind in ("tank", "sector", "box"):
            x, y = n.x, n.y
        else:                                       # the old drawing placed small symbols by their centre
            x, y = n.x - w / 2, n.y - h / 2
        symbols.append(dg.Symbol(node=n.item, x=dg.snap(max(-100, x)), y=dg.snap(max(-100, y)), label=n.label[:80]))
    # what the old water model inferred from geometry: evidence for the reviewer, never a connection
    from ..twin.water import Network
    try:
        net = Network(legacy.canonical(lay))
    except Exception:                               # an unreadable old drawing: everything stays for review
        net = None
    edges = []
    for p in lay.pipes:
        a, b = by_legacy.get(p.from_node or ""), by_legacy.get(p.to_node or "")
        info = net.pipes.get(p.id) if net else None
        inline = [by_legacy.get(x, x) for _, x in (info.inline if info else [])]
        joined = bool(info and (info.branches or info.end_join))
        if not (a and b):
            ambiguous.append({"legacy": p.id, "kind": "PIPE_END", "why": "a pipe end is not attached to a symbol by name"
                              + (f" (the drawing suggests {', '.join(inline)} on it)" if inline else ""),
                              "fix": "connect its ports explicitly in the editor"})
            continue
        if inline or joined:
            ambiguous.append({"legacy": p.id, "kind": "INLINE" if inline else "JOIN",
                              "why": (f"the old drawing had {', '.join(names.get(x, x) for x in inline)} drawn on this pipe"
                                      if inline else "another pipe touched this one in the old drawing"),
                              "suggests": {"from": a, "to": b, "through": inline},
                              "fix": "decide the connection: through that equipment, through a junction, or not connected"})
            continue
        ka, kb = nodes[a]["kind"], nodes[b]["kind"]
        if ka not in DEFAULT_OUT or kb not in DEFAULT_IN:
            ambiguous.append({"legacy": p.id, "kind": "PORTS", "why": f"no default ports from a {ka} to a {kb}",
                              "fix": "connect it in the editor"})
            continue
        edges.append({"id": _edge_id(p.id, {e["id"] for e in edges}), "src": {"node": a, "port": DEFAULT_OUT[ka]},
                      "dst": {"node": b, "port": DEFAULT_IN[kb]}, "provenance": "UNKNOWN"})
    measures = [m for m in measures if m["sensor"] in nodes and m["node"] in nodes]
    graph = {"nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": edges, "measures": measures}
    topo = parse(graph)                              # raises if the proposal is malformed: a bug, never silent
    view = dg.Diagram(view="installation", title=lay.title, width=max(lay.width, 400), height=max(lay.height, 300),
                      symbols=symbols, routes=[dg.Route(edge=e["id"]) for e in edges])
    return {"kind": "AQUACROP_MIGRATION_PROPOSAL", "source": {"kind": "AQUACROP_LAYOUT", "digest": legacy.digest(lay)},
            "topology": topo.model_dump(mode="json"), "diagram": view.model_dump(mode="json"),
            "ambiguous": ambiguous, "unresolved": unresolved, "matched": matched, "to_create": to_create,
            "meaning": "a draft for review: explicit links only; nothing ambiguous was connected"}
