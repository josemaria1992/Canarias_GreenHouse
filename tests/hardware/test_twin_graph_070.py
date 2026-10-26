"""0.7.0 TWIN-02/05/08 end to end: equipment, drawing, block and program made on the real dashboard; the twin imports
them with the snapshot, runs the program over the explicit graph and its proof follows the graph's connections, not
the drawing. Nothing reaches the fake bus."""
from aquacrop import diagram as dg
from aquacrop.topology.definitions import parse
from tests.hardware.test_twin_service import _twin_h, live_and_twin  # noqa: F401  (fixture)
from tests.integration.test_blocks_070 import straight_route

PARTS = [("tank-1", "Main tank", "tank", "tank", {"capacity_l": 200, "sim_initial_l": 100, "provenance": "DEMO"}),
         ("pump-1", "Main pump", "pump", "pump", {"rated_flow_l_min": 20}),
         ("meter-1", "Flow meter", "flow", "flow", {}),
         ("valve-1", "Sector valve", "valve", "valve", {}),
         ("sector-1", "Sector 1", "sector", "drop", {"provenance": "DEMO"})]
SCALING = {"flow": {"raw_min": 4, "raw_max": 20, "raw_unit": "mA", "eng_min": 0, "eng_max": 100, "unit": "L/min",
                    "provenance": "DEMO"}}
KINDS = {"tank-1": "tank", "pump-1": "pump", "meter-1": "meter", "valve-1": "valve", "sector-1": "sector"}
POS = {"tank-1": (40, 100), "pump-1": (240, 200), "meter-1": (360, 200), "valve-1": (480, 200), "sector-1": (620, 190)}


def graph(with_last=True):
    e = lambda i, a, b: {"id": i, "src": dict(zip(("node", "port"), a.split("."))),  # noqa: E731
                         "dst": dict(zip(("node", "port"), b.split(".")))}
    edges = [e("e2", "tank-1.out", "pump-1.in"), e("e3", "pump-1.out", "meter-1.in"),
             e("e4", "meter-1.out", "valve-1.in")] + ([e("e5", "valve-1.out", "sector-1.in")] if with_last else [])
    return {"nodes": [{"id": k, "kind": v, "item": k} for k, v in KINDS.items()], "edges": edges}


def drawing(g, dy=0):
    t = parse(g)
    syms = {k: dg.Symbol(node=k, x=x, y=y + (dy if k == "pump-1" else 0)) for k, (x, y) in POS.items()}
    return {"symbols": [s.model_dump() for s in syms.values()],
            "routes": [{"edge": ed.id, "points": straight_route(KINDS, syms, ed)} for ed in t.edges]}


def test_twin08_program_runs_over_the_graph_and_proof_follows_connections_not_drawing(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames_before = len(bus.write_frames())
    for iid, name, tid, icon, props in PARTS:
        body = {"properties": props} | ({"scaling": SCALING} if iid == "meter-1" else {})
        r = live.post("/api/equipment/items", json={"item_id": iid, "name": name, "type_id": tid, "icon": icon, "body": body},
                      headers=hl)
        assert r.status_code == 200, r.text
    for iid, role, pt in (("pump-1", "start", "relay_A.DO2"), ("valve-1", "open", "relay_A.DO3"), ("meter-1", "flow", "analog_A.AI1")):
        assert live.post("/api/equipment/bind", json={"item_id": iid, "role": role, "point": pt}, headers=hl).status_code == 200
    g = graph()
    r = live.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g), "expected_topology_rev": 0,
                                                     "expected_diagram_rev": 0}, headers=hl)
    assert r.status_code == 200, r.text
    tp = live.post("/api/blocks/template-plan", json={"template": "IRRIGATION_SECTOR", "supply": "tank-1",
                                                      "destination": "sector-1", "travel_s": 3}, headers=hl).json()
    tp["plan"]["startup"][-1]["verify"] = False
    body = {"template": "IRRIGATION_SECTOR", "members": tp["members"], "supply": "tank-1", "destination": "sector-1",
            "meter": tp["meter"], "plan": tp["plan"]}
    assert live.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body}, headers=hl).status_code == 200
    assert live.post("/api/blocks/validate", json={"block_id": "s1", "version": 1}, headers=hl).status_code == 200
    form = {"block_id": "s1", "block_version": 1, "endpoint": {"kind": "VOLUME", "litres": 10, "max_minutes": 5},
            "schedule": {"kind": "NOW_ONLY"}}
    r = live.post("/api/programs/form", json={"program_id": "s1-10l", "name": "Sector 1 10 L", "form": form}, headers=hl)
    assert r.status_code == 200, r.text
    assert live.post("/api/programs/validate", json={"program_id": "s1-10l", "version": 1}, headers=hl).json()["status"] == "VALIDATED"
    assert live.post("/api/twin-link/publish", headers=hl).status_code == 200

    H = _twin_h(twin, "bench1")
    assert twin.post("/api/twin/control", json={"action": "import"}, headers=H).status_code == 200
    svc = twin_app.state.svc
    assert svc.graph() is not None and len(svc.graph().edges) == 4          # the twin follows the real graph
    assert twin.post("/api/twin/programs/activate", json={"program_id": "s1-10l", "version": 1}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    assert twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H).status_code == 200
    assert twin.post("/api/twin/run-now", json={"program_id": "s1-10l"}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 200}, headers=H)
    v = twin.get("/api/twin").json()
    res = v["results"][-1]
    assert res["result"] == "DONE" and 10 <= res["measured_L"] <= 10 + 20 * 0.5 / 60 + 1e-6, res
    gw = v["graph_water"]
    assert gw["model"] == "graph-water-1" and abs(gw["ledger"]["residual_L"]) < 1e-6
    assert gw["sectors"]["sector-1"]["delivered_L"] >= 10 and gw["tanks"]["tank-1"]["L"] < 100
    assert svc.current_proof("s1-10l", 1)

    # HIST: the run is in Operation History with its timeline (engine events and audited commands) and CSV
    runs = twin.get("/api/history/runs").json()["runs"]
    assert runs[0]["program_id"] == "s1-10l" and runs[0]["result"] == "DONE" and runs[0]["measured_L"] == res["measured_L"]
    d = twin.get(f"/api/history/runs/{runs[0]['op_id']}").json()
    kinds = [e["kind"] for e in d["timeline"]]
    assert kinds[0] == "PROGRAM_OP_STARTED" and "COMMAND" in kinds and kinds[-1] in ("PROGRAM_OP_ENDED", "COMMAND")
    assert d["program_body"]["authoring"]["block_id"] == "s1"
    csv_text = twin.get("/api/history/runs.csv").text
    assert csv_text.splitlines()[0].startswith("op_id,program_id") and "s1-10l" in csv_text

    # TWIN-08: moving a symbol in the twin is a drawing-only change: the proof stays
    cur_t, cur_d = twin.get("/api/topology").json(), twin.get("/api/diagram/installation").json()
    r = twin.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g, dy=60), "expected_topology_rev": cur_t["rev"],
                                                     "expected_diagram_rev": cur_d["rev"]}, headers=H)
    assert r.status_code == 200 and not r.json()["semantic_change"], r.text
    assert svc.current_proof("s1-10l", 1)
    # ... a connection change is a new test context: the proof is history until a new run
    g2 = graph(with_last=False)
    r = twin.post("/api/drawing/installation", json={"graph": g2, "diagram": drawing(g2, dy=60), "expected_topology_rev": cur_t["rev"],
                                                     "expected_diagram_rev": cur_d["rev"] + 1}, headers=H)
    assert r.status_code == 200 and r.json()["semantic_change"], r.text
    assert svc.current_proof("s1-10l", 1) == []
    assert len(bus.write_frames()) == frames_before
