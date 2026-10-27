"""Baseline observations for the owner's block/editor feedback; fake installation only."""
import json
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient
from aquacrop import layout
from aquacrop.topology.migrate import propose
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login
from tests.integration.test_blocks_070 import setup_equipment, graph, drawing, SYMS


def choices(g):
    ns = g["nodes"]
    return {"supply": sum(n["kind"] in ("source", "tank") for n in ns),
            "destination": sum(n["kind"] in ("sector", "tank") for n in ns),
            "flow_meter": sum(n["kind"] == "meter" for n in ns)}


def test_equipment_creation_alone_does_not_populate_block_selectors(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        setup_equipment(c, h)
        equipment = c.get("/api/equipment", headers=h).json()
        before = c.get("/api/topology", headers=h).json()
        assert len(equipment["items"]) >= 6
        assert choices(before["graph"]) == {"supply": 0, "destination": 0, "flow_meter": 0}
        g = graph()
        r = c.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g, SYMS),
                   "expected_topology_rev": 0, "expected_diagram_rev": 0}, headers=h)
        assert r.status_code == 200, r.text
        after = c.get("/api/topology", headers=h).json()
        assert choices(after["graph"]) == {"supply": 2, "destination": 2, "flow_meter": 1}
        print("BLOCK_OPTIONS", json.dumps({"equipment_items": len(equipment["items"]),
              "before_drawing": choices(before["graph"]), "after_drawing": choices(after["graph"])}))


def test_legacy_reference_elements_are_not_hydraulic_connections():
    fx = json.loads(Path("tests/fixtures/bench-legacy-drawing.json").read_text(encoding="utf-8"))
    p = propose(layout.parse(fx["layout"]), {k:v["kind"] for k,v in fx["items"].items()},
                {k:v["name"] for k,v in fx["items"].items()})
    assert p["kept"] == fx["expected_kept"]
    assert len(p["diagram"]["sketches"]) == 15
    assert len(p["diagram"]["placeholders"]) == 8
    print("LEGACY_STATES", json.dumps({"graph_kinds": dict(Counter(n["kind"] for n in p["topology"]["nodes"])),
          "unmapped_kinds": dict(Counter(n["kind"] for n in p["diagram"]["placeholders"])), "kept": p["kept"]}))
