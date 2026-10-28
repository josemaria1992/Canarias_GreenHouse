"""The hydraulic graph: nodes with named ports, explicit edges between ports, junctions and sensor associations.

* A node is one equipment item (a pump, a valve, a tank...) or a junction. One item is one node, however many
  drawings show it; its kind comes from the item's type (`TypeBody.hydraulic`), never from an icon or a name.
* Ports come from the kind's template (`SYMBOLS`): a stable id, the direction water may take through it (in, out
  or both) and whether it takes several edges. The side and offset are where a drawing anchors it; they are
  presentation and have no meaning here.
* An edge joins one port to another, from a port water may leave by to a port it may enter by. Two pipes that
  cross in a drawing are two edges and nothing more; a connection exists only as an edge or a junction.
* A sensor measures something by association: the level of a tank, the pressure in an edge (a tap). An inline
  flow meter is a node on the path itself, with an inlet and an outlet. A tap carries no water.

The digest covers only semantics, so it does not change when a drawing is moved or rerouted.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = r"^[a-z0-9][a-z0-9_\-]{0,47}$"
NodeKind = Literal["source", "tank", "pump", "valve", "meter", "sensor", "sector", "junction"]
Flow = Literal["in", "out", "both"]
Side = Literal["N", "E", "S", "W"]
MAX_NODES, MAX_EDGES = 400, 600


class PortDef:
    __slots__ = ("id", "flow", "side", "offset", "many", "label")

    def __init__(self, id: str, flow: Flow, side: Side, offset: float, label: str, many: bool = False):
        self.id, self.flow, self.side, self.offset, self.label, self.many = id, flow, side, offset, label, many

    def public(self) -> dict:
        return {"id": self.id, "flow": self.flow, "side": self.side, "offset": self.offset, "label": self.label,
                "many": self.many}


# Symbol size (document units at rotation 0) and ports per kind. A port's offset runs clockwise along its side
# (N left to right, E top to bottom, S right to left, W bottom to top), so a quarter turn keeps it. Shared with the browser editor through the API,
# so the editor and the validation use the same anchors.
SYMBOLS: dict[str, dict] = {
    "source": {"w": 40, "h": 40, "ports": [PortDef("out", "out", "E", 0.5, "Outlet", many=True)]},
    "tank": {"w": 100, "h": 120, "ports": [PortDef("in", "in", "N", 0.3, "Inlet", many=True),
                                           PortDef("return", "in", "N", 0.7, "Return inlet", many=True),
                                           PortDef("out", "out", "E", 0.85, "Outlet", many=True),
                                           PortDef("overflow", "out", "W", 0.85, "Overflow")]},
    "pump": {"w": 40, "h": 40, "ports": [PortDef("in", "in", "W", 0.5, "Suction"), PortDef("out", "out", "E", 0.5, "Discharge")]},
    "valve": {"w": 40, "h": 40, "ports": [PortDef("in", "both", "W", 0.5, "Port A"), PortDef("out", "both", "E", 0.5, "Port B")]},
    "meter": {"w": 40, "h": 40, "ports": [PortDef("in", "in", "W", 0.5, "Inlet"), PortDef("out", "out", "E", 0.5, "Outlet")]},
    "sensor": {"w": 30, "h": 30, "ports": []},
    "sector": {"w": 120, "h": 60, "ports": [PortDef("in", "in", "W", 0.5, "Inlet", many=True),
                                            PortDef("drain", "out", "S", 0.5, "Drain")]},
    "junction": {"w": 0, "h": 0, "ports": [PortDef(s.lower(), "both", s, 0.5, f"{s} branch") for s in "NESW"]},
}


def ports_of(kind: str) -> dict[str, PortDef]:
    return {p.id: p for p in SYMBOLS[kind]["ports"]}


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PortRef(_B):
    node: str = Field(pattern=ID)
    port: str = Field(pattern=r"^[a-z]{1,16}$")

    def key(self) -> str:
        return f"{self.node}.{self.port}"


class Node(_B):
    id: str = Field(pattern=ID)
    kind: NodeKind
    item: str | None = Field(default=None, pattern=ID)      # the equipment item; None only for a junction

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "junction" and self.item is not None:
            raise ValueError(f"{self.id}: a junction is not an equipment item")
        if self.kind != "junction" and self.item is None:
            raise ValueError(f"{self.id}: a {self.kind} refers to its equipment item")
        return self


class Edge(_B):
    id: str = Field(pattern=ID)
    src: PortRef                      # water may leave by it
    dst: PortRef                      # water may enter by it
    medium: Literal["WATER", "NUTRIENT", "DRAIN"] = "WATER"
    length_m: float | None = Field(default=None, gt=0, le=10000)     # optional engineering data, never a pixel length
    diameter_mm: float | None = Field(default=None, gt=0, le=2000)
    provenance: Literal["MEASURED", "DATASHEET", "OWNER_CONFIGURED", "DEMO", "UNKNOWN"] = "UNKNOWN"
    lineage: str | None = Field(default=None, max_length=48)         # the edge it was split from, if any


class Measure(_B):
    sensor: str = Field(pattern=ID)                     # a sensor node
    node: str | None = Field(default=None, pattern=ID)  # what it measures: a tank's level ...
    edge: str | None = Field(default=None, pattern=ID)  # ... or a tap on an edge (pressure, EC)
    mode: Literal["TAP"] = "TAP"

    @model_validator(mode="after")
    def _check(self):
        if (self.node is None) == (self.edge is None):
            raise ValueError(f"{self.sensor}: measures either a node or an edge")
        return self


class Topology(_B):
    kind: Literal["AQUACROP_TOPOLOGY"] = "AQUACROP_TOPOLOGY"
    format: Literal[1] = 1
    nodes: tuple[Node, ...] = Field(default=(), max_length=MAX_NODES)
    edges: tuple[Edge, ...] = Field(default=(), max_length=MAX_EDGES)
    measures: tuple[Measure, ...] = Field(default=(), max_length=MAX_NODES)

    @model_validator(mode="after")
    def _unique(self):
        for what, ids in (("node", [n.id for n in self.nodes]), ("edge", [e.id for e in self.edges])):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                raise ValueError(f"duplicate {what} id: {', '.join(dup)}")
        items = [n.item for n in self.nodes if n.item]
        dup = sorted({i for i in items if items.count(i) > 1})
        if dup:
            raise ValueError(f"one equipment item is one node: {', '.join(dup)} appear twice")
        return self

    def node(self, nid: str) -> Node | None:
        return next((n for n in self.nodes if n.id == nid), None)


def canonical(t: Topology) -> dict:
    d = t.model_dump(mode="json")
    d["nodes"] = sorted(d["nodes"], key=lambda n: n["id"])
    d["edges"] = sorted(d["edges"], key=lambda e: e["id"])
    d["measures"] = sorted(d["measures"], key=lambda m: (m["sensor"], m["node"] or "", m["edge"] or ""))
    return d


def digest(t: Topology) -> str:
    """Semantic digest: nodes, edges, ports and associations, sorted. No coordinate is part of it."""
    return hashlib.sha256(json.dumps(canonical(t), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse(doc: dict) -> Topology:
    return Topology.model_validate(doc)


def symbols_public() -> dict:
    return {k: {"w": v["w"], "h": v["h"], "ports": [p.public() for p in v["ports"]]} for k, v in SYMBOLS.items()}
