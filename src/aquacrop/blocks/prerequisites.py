"""What a block's choices are waiting for (0.7.6, owner UX-01).

A block chooses its water source, its destination and its flow meter from the approved installation drawing (the
graph the Overview shows), never from Equipment directly: an item that exists but is not drawn, or is drawn only in a
Digital Twin design, is not part of the real installation yet. When a choice is empty this says, per role, which stage
is missing and for which named equipment, in the order the owner works: created on Equipment, placed and connected in
the Digital Twin, exported, approved here. A meter that is drawn also says whether its measurement can be used
(channel chosen, verified, scaled). Nothing here changes anything.
"""
from __future__ import annotations

ROLES = {"source": ("Water source", ("source", "tank"), "a water source or a tank"),
         "destination": ("Watering destination", ("sector", "tank"), "an irrigation sector (or a tank to fill)"),
         "meter": ("Flow meter", ("meter",), "a flow meter")}


def check(items: dict[str, dict], graph_nodes: list[dict], exported_nodes: set[str],
          unlinked_symbols: list[dict], readiness=None) -> dict:
    """`items`: equipment item id -> {kind, name} (kind None: not a water part); `graph_nodes`: the approved graph's
    nodes; `exported_nodes`: item ids in a twin export not applied yet; `unlinked_symbols`: earlier-drawing symbols
    not linked to equipment ({kind, label}); `readiness`: item id -> the Equipment checklist (meters)."""
    placed = {n.get("item") or n["id"]: n for n in graph_nodes}
    out = {}
    for role, (title, kinds, words) in ROLES.items():
        created = {iid: it for iid, it in items.items() if it.get("kind") in kinds}
        drawn = [iid for iid in created if iid in placed]
        waiting = [iid for iid in created if iid not in placed and iid in exported_nodes]
        unplaced = [iid for iid in created if iid not in placed and iid not in exported_nodes]
        unlinked = [s for s in unlinked_symbols if s.get("kind") in kinds]
        nm = lambda ids: ", ".join(created[i]["name"] for i in ids)       # noqa: E731
        steps = []
        if not drawn:
            if waiting:
                steps.append(f"{nm(waiting)}: exported from the Digital Twin, waiting for approval on the Digital Twin "
                             "page here")
            if unplaced:
                it_ = "them" if len(unplaced) > 1 else "it"
                steps.append(f"{nm(unplaced)}: on Equipment but not in the approved drawing. Place and connect {it_} in "
                             "the Digital Twin, export the drawing, then approve it here")
            if unlinked:
                steps.append(f"The earlier drawing shows {', '.join(s['label'] for s in unlinked)} without an equipment "
                             f"item: create {words} with that name on Equipment, then Import from Overview in the "
                             "Digital Twin links it")
            if not created and not unlinked:
                steps.append(f"No {words} exists yet. Create one on Equipment, place and connect it in the Digital "
                             "Twin, export the drawing and approve it here")
        state = "READY" if drawn else "WAITING_APPROVAL" if waiting else "NOT_PLACED" if unplaced else \
            "UNLINKED_SYMBOL" if unlinked else "NOT_CREATED"
        entry = {"title": title, "state": state, "choices": drawn, "next": steps,
                 "not_placed": unplaced, "waiting_approval": waiting,
                 "unlinked_symbols": [s["label"] for s in unlinked]}
        if role == "meter" and readiness is not None:
            m = {}
            for iid in drawn:
                r = readiness(iid) or {}
                m[iid] = ("ready" if r.get("ready") else
                          "no channel chosen for its flow reading" if r.get("missing_required") else
                          "its channel is not verified" if r.get("unverified") else
                          "its reading has no scaling" if r.get("unscaled") else "not ready")
            entry["measurement"] = m
        out[role] = entry
    return {"roles": out, "order": "Equipment → place and connect in the Digital Twin → export → approve here → "
                                   "Blocks → Programs and Twin testing"}
