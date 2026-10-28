"""0.7.0 Blocks through the real app: equipment from templates (passive parts without channels), the explicit graph,
an orthogonal drawing saved with it, a block validated against it, revision conflicts and the role checks.
EQ-01, EQ-04, TOPO-01/05, RTE-04/07 (server side), DEP-03 (revision checks)."""
from fastapi.testclient import TestClient

from aquacrop import diagram as dg
from aquacrop.topology.definitions import parse
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)

PARTS = [("mains", "Mains", "water_source", "drop", {"supply_l_min": 60, "provenance": "DEMO"}),
         ("tank-1", "Main tank", "tank", "tank", {"capacity_l": 200, "sim_initial_l": 100, "level_full": 200, "provenance": "DEMO"}),
         ("pump-1", "Main pump", "pump", "pump", {"rated_flow_l_min": 20}),
         ("meter-1", "Flow meter", "flow", "flow", {}),
         ("valve-1", "Sector valve", "valve", "valve", {}),
         ("sector-1", "Sector 1", "sector", "drop", {"provenance": "DEMO"})]


def setup_equipment(c, h):
    assert c.post("/api/equipment/types/seed", headers=h).status_code == 200
    for t in c.get("/api/equipment", headers=h).json()["types"]:
        if t["status"] == "DRAFT":
            assert c.post("/api/equipment/types/activate", json={"type_id": t["type_id"], "version": t["version"]},
                          headers=h).status_code == 200, t["type_id"]
    for iid, name, tid, icon, props in PARTS:
        r = c.post("/api/equipment/items", json={"item_id": iid, "name": name, "type_id": tid, "icon": icon,
                                                 "body": {"properties": props}}, headers=h)
        assert r.status_code == 200, r.text


def graph():
    kinds = {"mains": "source", "tank-1": "tank", "pump-1": "pump", "meter-1": "meter", "valve-1": "valve",
             "sector-1": "sector"}
    edge = lambda i, a, b: {"id": i, "src": dict(zip(("node", "port"), a.split("."))),  # noqa: E731
                            "dst": dict(zip(("node", "port"), b.split(".")))}
    return {"nodes": [{"id": k, "kind": v, "item": k} for k, v in kinds.items()],
            "edges": [edge("e1", "mains.out", "tank-1.in"), edge("e2", "tank-1.out", "pump-1.in"),
                      edge("e3", "pump-1.out", "meter-1.in"), edge("e4", "meter-1.out", "valve-1.in"),
                      edge("e5", "valve-1.out", "sector-1.in")]}


def straight_route(kinds, syms, e):
    """A simple route that leaves and enters each port straight: stub, elbow, stub (test layouts only)."""
    a, sa = dg.anchor(kinds[e.src.node], syms[e.src.node], e.src.port)
    b, sb = dg.anchor(kinds[e.dst.node], syms[e.dst.node], e.dst.port)
    va, vb = dg.SIDE_VEC[sa], dg.SIDE_VEC[sb]
    s1 = (a[0] + 20 * va[0], a[1] + 20 * va[1])
    e1 = (b[0] + 20 * vb[0], b[1] + 20 * vb[1])
    elbow = (s1[0], e1[1]) if va[0] else (e1[0], s1[1])
    return dg.canonical_points([a, s1, elbow, e1, b])


def drawing(g, symbols):
    t = parse(g)
    syms = {s.node: s for s in symbols}
    kinds = {n.id: n.kind for n in t.nodes}
    routes = []
    for e in t.edges:
        routes.append({"edge": e.id, "points": straight_route(kinds, syms, e)})
    return {"symbols": [s.model_dump() for s in symbols], "routes": routes}


SYMS = [dg.Symbol(node="mains", x=20, y=60), dg.Symbol(node="tank-1", x=120, y=20), dg.Symbol(node="pump-1", x=300, y=100),
        dg.Symbol(node="meter-1", x=400, y=100), dg.Symbol(node="valve-1", x=500, y=100), dg.Symbol(node="sector-1", x=620, y=90)]


def test_eq01_passive_parts_need_no_channel_and_appear_with_their_water_kind(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        setup_equipment(c, h)
        topo = c.get("/api/topology", headers=h).json()
        kinds = {k: v["kind"] for k, v in topo["items"].items()}
        assert {"tank-1": "tank", "mains": "source", "pump-1": "pump", "meter-1": "meter", "sector-1": "sector"}.items() \
            <= kinds.items()
        assert topo["rev"] == 0 and topo["symbols"]["pump"]["ports"][0]["id"] == "in"


def test_drawing_save_validate_block_and_revision_conflicts(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        setup_equipment(c, h)
        g = graph()
        d = drawing(g, SYMS)
        r = c.post("/api/drawing/installation", json={"graph": g, "diagram": d, "expected_topology_rev": 0,
                                                      "expected_diagram_rev": 0}, headers=h)
        assert r.status_code == 200, r.text
        first = r.json()
        assert first["semantic_change"] and first["topology"]["rev"] == 1 and first["diagram"]["rev"] == 1
        # a stale editor is refused, nothing overwritten (DEP-03)
        r = c.post("/api/drawing/installation", json={"graph": g, "diagram": d, "expected_topology_rev": 0,
                                                      "expected_diagram_rev": 0}, headers=h)
        assert r.status_code == 409 and "meanwhile" in r.json()["detail"]
        # moving symbols is a drawing-only change: the graph revision and digest stay (TOPO-05, RTE-04)
        moved = [s.model_copy(update={"y": s.y + 40}) for s in SYMS]
        r = c.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g, moved), "expected_topology_rev": 1,
                                                      "expected_diagram_rev": 1}, headers=h)
        assert r.status_code == 200 and not r.json()["semantic_change"] and r.json()["topology"] == first["topology"]
        # a diagonal pipe is refused by the server whatever the browser sent (RTE-07)
        bad = drawing(g, moved)
        p0 = bad["routes"][0]["points"]
        bad["routes"][0]["points"] = [p0[0], (p0[0][0] + 20, p0[0][1] + 20), p0[-1]]
        r = c.post("/api/drawing/installation", json={"graph": g, "diagram": bad, "expected_topology_rev": 1,
                                                      "expected_diagram_rev": 2}, headers=h)
        assert r.status_code == 409 and any(i["rule"] == "ROUTE_DIAGONAL" for i in r.json()["issues"])
        # a block over the path: template plan, save, validate
        tp = c.post("/api/blocks/template-plan", json={"template": "IRRIGATION_SECTOR", "supply": "tank-1",
                                                       "destination": "sector-1", "travel_s": 3}, headers=h).json()
        assert tp["path"]["nodes"] == ["pump-1", "meter-1", "valve-1"] and tp["meter"] == "meter-1"
        assert [s["do"] for s in tp["plan"]["startup"]] == ["OPEN", "WAIT", "START"]
        body = {"template": "IRRIGATION_SECTOR", "members": tp["members"], "supply": "tank-1",
                "destination": "sector-1", "meter": tp["meter"], "plan": tp["plan"]}
        r = c.post("/api/blocks", json={"block_id": "sector-1", "name": "Sector 1 watering", "body": body}, headers=h)
        assert r.status_code == 200, r.text
        assert [i for i in r.json()["issues"] if i["severity"] == "ERROR"] == []
        r = c.post("/api/blocks/validate", json={"block_id": "sector-1", "version": 1}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "VALIDATED"
        # a connection change makes the validated block stale (it is revalidated before use)
        g2 = graph()
        g2["edges"] = [e for e in g2["edges"] if e["id"] != "e1"]
        d2 = drawing(g2, moved)
        assert c.post("/api/drawing/installation", json={"graph": g2, "diagram": d2, "expected_topology_rev": 1,
                                                         "expected_diagram_rev": 2}, headers=h).json()["semantic_change"]
        b = c.get("/api/blocks", headers=h).json()["blocks"][0]
        assert b["stale"] is True
        # the meter must be on the path; a valve cannot be started (precise issues)
        body2 = dict(body, meter="pump-1", plan=dict(tp["plan"], startup=[{"do": "START", "node": "valve-1"}]))
        r = c.post("/api/blocks", json={"block_id": "other", "name": "Other", "body": body2}, headers=h)
        rules = {i["rule"] for i in r.json()["issues"]}
        assert {"METER_KIND", "STEP_KIND"} <= rules


def test_a_viewer_cannot_read_or_change_blocks_and_an_operator_cannot_retire(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "view1")
        for path in ("/api/topology", "/api/blocks", "/api/diagram/installation"):
            assert c.get(path, headers=h).status_code == 403, path
        assert c.post("/api/blocks", json={"block_id": "x", "name": "X", "body": {}}, headers=h).status_code == 403
        h = login(c, "oper1")
        assert c.get("/api/blocks", headers=h).status_code == 200
        assert c.post("/api/blocks/retire", json={"block_id": "x", "version": 1}, headers=h).status_code == 403
