"""The context a twin result proves (0.7.0, F01; review R07-01): one definition for the twin and the real system.

A twin result tests a program version together with, for every item it names, the item's type, its settings
(scaling, properties, simulation) and its channels, plus the water model and the network it runs on. Names and
icons are not part of it. With an explicit graph only its connections and the numbers the model reads count (a
moved symbol keeps the proof); without one the drawing is the network, so a moved pipe is a new context.

The twin computes this digest when a run starts and ends and writes it into accepted evidence; the real system
computes it again from its own records at evidence import, activation and every new start. A result from another
context stays visible as history and never counts.
"""
from __future__ import annotations

import hashlib
import json

WATER_MODEL = "drawing-water-0.7.0"   # part of every proof context: a changed model is a new test context


def context(version: dict, views: dict, graph=None, layout_digest: str | None = None) -> dict:
    """`version`: the program version row (body, digest); `views`: item id -> ItemView with every channel binding;
    `graph`: the explicit topology when it has nodes, else None; `layout_digest`: the drawing's digest otherwise."""
    from ..twin.changes import program_items
    names = sorted(program_items(version["body"]))
    items = {}
    for iid in names:
        it = views.get(iid)
        items[iid] = None if it is None else {
            "type": it.type.model_dump(mode="json"), "body": it.body.model_dump(mode="json"),
            "points": dict(sorted(it.points.items()))}
    water, model = layout_digest, WATER_MODEL
    if graph is not None:
        from ..topology.definitions import digest as topo_digest
        from ..twin.graph_water import MODEL as GRAPH_MODEL, params_from
        water, model = {"topology": topo_digest(graph), "params": params_from(graph, views).digest_parts()}, GRAPH_MODEL
    doc = {"program": version["digest"], "items": items, "water_network": water, "water_model": model}
    d = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {"digest": d, "items": names, "water_network": water, "water_model": model}


def refusal(simulation: dict | None, version_digest: str, current: str | None) -> str | None:
    """Why this version's recorded twin result does not count now; None when it does."""
    if not simulation or not simulation.get("passed") or simulation.get("digest") != version_digest:
        return "this version has no passing twin run; test it in the digital twin first"
    if current is None or simulation.get("context") != current:
        return ("its twin result was for other equipment, channels, settings or water network than the real system "
                "has now; it stays as history: test this version again in the digital twin")
    return None
