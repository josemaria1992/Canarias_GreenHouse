"""The installation starter (0.6.2, owner request): every part of the greenhouse as equipment, in one step.

Before 0.5.6 the Overview drawing was hard-wired to the installation's parts. Since the drawing became a layout
of equipment items, a new controller starts with nothing linked. The starter creates, in the twin's change
set, one equipment item per part of the standard installation (the pumps, the valves and the transmitters of
the standard drawing) and links every symbol and pipe of the twin's layout to them. No channel is assigned:
channels are chosen afterwards, one at a time, and verified at the bench on the real system.

Everything lands in the twin first. It reaches the real system only through the usual export (equipment) and
layout proposal (drawing), each approved there. Items that already exist are left as they are, so the starter
can be pressed again after adding or removing parts.
"""
from __future__ import annotations

from ..layout import Layout
from .installation_params import InstallationFacts

# (item id, name, type, icon, symbols that show it (symbol id, role or None))
_STOCKS = (("a", "A", "Fertiliser A"), ("b", "B", "Fertiliser B"), ("acid", "ACID", "Acid"))


def parts() -> list[dict]:
    out = []

    def add(iid, name, type_id, icon, symbols):
        out.append({"item_id": iid, "name": name, "type_id": type_id, "icon": icon, "symbols": symbols})
    for s, _, name in _STOCKS:
        tp = "TP acid" if s == "acid" else f"TP {s.upper()}"
        add(f"v-fill-{s}", f"{name} water in", "valve", "valve", [(f"v-fill-{s}", None)])
        add(f"lvl-{s}", f"{name} level", "level", "level", [(f"lvl-{s}", "level"), (f"tank-{s}", "level")])
        add(f"v-out-{s}", f"{name} outlet", "valve", "valve", [(f"v-out-{s}", None)])
        add(f"tp-{s}", tp, "pump", "pump", [(f"tp-{s}", None)])
        add(f"flow-tp-{s}", f"{tp} flow", "flow", "flow", [(f"flow-tp-{s}", "flow")])
    add("v-fill-main", "Main tank water in", "valve", "valve", [("v-fill-main", None)])
    add("flow-fill-main", "Main tank fill flow", "flow", "flow", [("flow-fill-main", "flow")])
    add("lvl-main", "Main tank level", "level", "level", [("lvl-main", "level"), ("tank-main", "level")])
    add("ph", "Main tank pH", "ph", "drop", [("ph", "ph")])
    add("ec", "Main tank EC", "ec", "gauge", [("ec", "ec")])
    add("temp", "Main tank temperature", "temperature", "temperature", [("temp", "temperature")])
    add("mp1", "Main pump 1", "pump", "pump", [("mp1", None)])
    add("mp2", "Main pump 2", "pump", "pump", [("mp2", None)])
    for j in (1, 2):
        add(f"v-s{j}", f"Sector {j} valve", "valve", "valve", [(f"v-s{j}", None), (f"sector-{j}", None)])
        add(f"flow-s{j}", f"Sector {j} flow", "flow", "flow", [(f"flow-s{j}", "flow")])
        add(f"pres-s{j}", f"Sector {j} pressure", "pressure", "pressure", [(f"pres-s{j}", "pressure")])
    add("lvl-sump", "Drainage tank level", "level", "level", [("lvl-sump", "level"), ("tank-sump", "level")])
    add("rp1", "Return pump 1", "pump", "pump", [("rp1", None)])
    add("rp2", "Return pump 2", "pump", "pump", [("rp2", None)])
    add("flow-return", "Return flow", "flow", "flow", [("flow-return", "flow")])
    return out


# pipe id -> (pump or valve that moves water through it, flow meter on it)
PIPES: dict[str, tuple[str | None, str | None]] = {
    "fill-main": ("v-fill-main", "flow-fill-main"),
    "fill-a": ("v-fill-a", None), "fill-b": ("v-fill-b", None), "fill-acid": ("v-fill-acid", None),
    "dose-a": ("tp-a", "flow-tp-a"), "dose-b": ("tp-b", "flow-tp-b"), "dose-acid": ("tp-acid", "flow-tp-acid"),
    "to-mp1": ("mp1", None), "to-mp2": ("mp2", None),
    "mp1-s1": ("mp1", None), "mp1-s2": ("mp1", None), "mp2-s1": ("mp2", None), "mp2-s2": ("mp2", None),
    "s1": ("v-s1", "flow-s1"), "s2": ("v-s2", "flow-s2"),
    "return": ("rp1", "flow-return"),
}
_TANK = {"tank-main": "MAIN", "tank-sump": "SUMP", "tank-a": "A", "tank-b": "B", "tank-acid": "ACID"}
TYPES_NEEDED = sorted({p["type_id"] for p in parts()})


def missing_types(snap: dict) -> list[str]:
    active = {t["type_id"] for t in snap["equipment"]["types"] if t["status"] == "ACTIVE"}
    return [t for t in TYPES_NEEDED if t not in active]


SNAP = 24
_BOXES = ("tank", "sector", "box")


def _near(nodes: list[dict], x: float, y: float) -> str | None:
    """The symbol a pipe end at (x, y) touches, the same rule as the editor's: a pump, valve or sensor by its
    centre, a tank, sector or box by its edge."""
    best = None
    for n in nodes:
        if n["kind"] == "label":
            continue
        px, py = n["x"], n["y"]
        if n["kind"] in _BOXES:
            w, hh = n.get("w") or 100, n.get("h") or 100
            px, py = max(n["x"], min(n["x"] + w, x)), max(n["y"], min(n["y"] + hh, y))
            if n["x"] < px < n["x"] + w and n["y"] < py < n["y"] + hh:
                continue                                   # a corner inside a box is not an end on its edge
        d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
        if d <= SNAP and (best is None or d < best[0]):
            best = (d, n["id"])
    return best[1] if best else None


def link_layout(lay: Layout, have: set[str]) -> tuple[Layout, int]:
    """Link the standard drawing's symbols and pipes to the starter's items that exist (by id). Symbols the
    owner removed or renamed are left alone; links already set are kept."""
    doc = lay.model_dump(mode="json")
    ids = {n["id"] for n in doc["nodes"]}
    if "mains" not in ids and any(p["id"] == "mains" for p in doc["pipes"]):      # the twin's water needs a source
        start = next(p for p in doc["pipes"] if p["id"] == "mains")["points"][0]
        doc["nodes"].append({"id": "mains", "kind": "source", "x": start[0], "y": start[1], "label": "Mains",
                             "flow_L_min": 60})
    for p in doc["pipes"]:                     # 0.5.6 drew the dosing pipes from below the tank's edge
        if p["id"] in ("dose-a", "dose-b", "dose-acid") and not p.get("from_node") and p["points"][0][1] == 262:
            p["points"][0] = [p["points"][0][0], 225]
    by_symbol = {sym: (p["item_id"], role) for p in parts() for sym, role in p["symbols"]}
    facts = InstallationFacts()
    n = 0
    for node in doc["nodes"]:
        hit = by_symbol.get(node["id"])
        if hit and hit[0] in have and not node.get("item"):
            node["item"], node["role"] = hit
            node["planned"] = False
            n += 1
        if node["id"] in _TANK and node.get("capacity_L") in (None, 100, 200, 1000):   # 0.5.6 placeholders
            node["capacity_L"] = facts.tank_capacity(_TANK[node["id"]])
    for pipe in doc["pipes"]:                       # ends touching a symbol are attached: they move with it
        if not pipe.get("from_node"):
            pipe["from_node"] = _near(doc["nodes"], *pipe["points"][0])
        if not pipe.get("to_node"):
            pipe["to_node"] = _near(doc["nodes"], *pipe["points"][-1])
        hit = PIPES.get(pipe["id"])
        if hit is None:
            continue
        item, meter = hit
        if item in have and not pipe.get("item"):
            pipe["item"] = item
            n += 1
        if meter and meter in have and not pipe.get("flow_item"):
            pipe["flow_item"], pipe["flow_role"] = meter, "flow"
    return Layout.model_validate(doc), n
