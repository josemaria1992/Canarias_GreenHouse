"""Independent observations against f91e34d; fake services only. No source edits."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login
from tests.integration.test_program_forms_070 import FORM, ready_block
from tests.hardware.test_twin_service import _twin_h, live_and_twin
from tests.hardware.test_twin_graph_070 import graph, drawing
from tests.hardware.test_twin_design_071 import _parts
from tests.unit.test_migration_071 import _lay
from tests.conftest import fake_bus
from aquacrop.topology.migrate import propose


@pytest.mark.parametrize("case,field", [("block", "block_id"), ("condition", "permissives.0.item"), ("role", "permissives.0.role")])
def test_observe_raw_preview_error(tmp_path, icfg, case, field):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        f = copy.deepcopy(FORM)
        assert c.post("/api/programs/preview", json={"form": f}, headers=h).status_code == 200
        if case == "block":
            f["block_id"] = ""
        else:
            f["permissives"] = [{"item": "" if case == "condition" else "pump-1", "role": "", "op": "ABOVE", "value": None}]
        r = c.post("/api/programs/preview", json={"form": f}, headers=h)
        print("PREVIEW", case, r.status_code, json.dumps(r.json()))
        assert r.status_code == 422
        assert r.json()["detail"]["field"] == field
        assert r.json()["detail"]["message"] == "String should match pattern '^[a-z0-9][a-z0-9_\\-]{0,47}$'"


def test_observe_legacy_geometry_loss():
    lay = _lay([{"id": "p2", "points": [[340, 230], [420, 230]], "from_node": "mp1", "to_node": "v-s1"}])
    p = propose(lay, {"lvl-main": "sensor", "mp1": "pump", "v-s1": "valve"},
                {"lvl-main": "Main tank level", "mp1": "Main pump 1", "v-s1": "Sector 1 valve"})
    print("LEGACY", json.dumps({"before_nodes": len(lay.nodes), "after_nodes": len(p["topology"]["nodes"]),
                               "missing": p["to_create"], "routes": p["diagram"]["routes"]}))
    assert any(x["kind"] == "tank" for x in p["to_create"])
    assert all(n["kind"] != "tank" for n in p["topology"]["nodes"])
    assert p["diagram"]["routes"] and all(not r["points"] for r in p["diagram"]["routes"])


def test_explicit_import_preserves_geometry(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    _parts(live, hl)
    before_frames = len(bus.write_frames())
    g, d = graph(), drawing(graph())
    d.update(title="Owner geometry", grid=5, width=1600, height=900,
             texts=[{"id": "note", "x": 100, "y": 40, "text": "Keep this annotation"}])
    d["symbols"][0]["label"] = "Owner tank label"
    d["routes"][0].update(mode="MANUAL", pins=[{"axis": "y", "value": d["routes"][0]["points"][1][1]}])
    r = live.post("/api/drawing/installation", json={"graph": g, "diagram": d, "expected_topology_rev": 0,
                                                     "expected_diagram_rev": 0}, headers=hl)
    assert r.status_code == 200, r.text
    assert live.post("/api/twin-link/publish", headers=hl).status_code == 200
    h = _twin_h(twin, "bench1")
    r = twin.post("/api/twin/design/import", json={}, headers=h)
    assert r.status_code == 200 and r.json()["source"] == "graph", r.text
    ld = live.get("/api/diagram/installation").json()
    td = twin.get("/api/diagram/installation").json()
    assert td["diagram"] == ld["diagram"]
    assert td["digest"] == ld["digest"]
    assert twin.get("/api/topology").json()["graph"] == live.get("/api/topology").json()["graph"]
    assert len(bus.write_frames()) == before_frames
    print("EXPLICIT_IMPORT exact canonical graph/diagram equality; zero hardware writes")
