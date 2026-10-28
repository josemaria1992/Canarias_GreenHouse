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
from .definitions import SYMBOLS, parse, ports_of

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


def propose(lay: legacy.Layout, item_kinds: dict[str, str | None], names: dict[str, str] | None = None,
            answers: dict[str, str] | None = None) -> dict:
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
    from .reconcile import pipes                     # C077-08: explicit links kept, geometry asked once
    graph, view, extra = pipes(lay, nodes, symbols, by_legacy, measures, names, item_kinds, answers or {}, taken,
                               texts, shapes, placeholders, to_create, unresolved, ambiguous)
    return {"kind": "AQUACROP_MIGRATION_PROPOSAL", "source": {"kind": "AQUACROP_LAYOUT", "digest": legacy.digest(lay)},
            "topology": graph, "diagram": view.model_dump(mode="json"),
            "ambiguous": ambiguous, "unresolved": unresolved, "matched": matched, "to_create": to_create} | extra | {
            "meaning": "the earlier drawing kept as drawn; its explicit links became connections, and what only its "
                       "geometry suggests became a connection only where a person answered yes"}
