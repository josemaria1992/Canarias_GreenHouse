"""0.7.1 DT-01, DT-02 (API part), DT-06, DT-07 (owner decisions 26 October 2026): the Digital Twin is the one place the
installation is drawn, in two journeys, and the Overview draws what was exported and applied.

* New design: place equipment made on the Equipment page, connect, save in the twin, export, apply on the main
  dashboard; the Overview then draws it, with planned equipment marked and nothing operated.
* Import from Overview: the approved drawing becomes the twin's design; a twin design not exported is replaced only
  when asked twice; before any graph exists, the earlier drawing comes back as a proposal (nothing saved, ambiguities
  listed). The fake bus sees no write."""
from tests.hardware.test_twin_graph_070 import KINDS, PARTS, graph, drawing  # noqa: F401
from tests.hardware.test_twin_service import ORIGIN, _twin_h, live_and_twin, login  # noqa: F401  (fixture)


def _parts(live, hl):
    for iid, name, tid, icon, props in PARTS:
        assert live.post("/api/equipment/items", json={"item_id": iid, "name": name, "type_id": tid, "icon": icon,
                                                       "body": {"properties": props}}, headers=hl).status_code == 200
    assert live.post("/api/twin-link/publish", headers=hl).status_code == 200


def test_dt01_import_from_overview_without_a_graph_proposes_the_earlier_drawing_and_saves_nothing(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    _parts(live, hl)
    H = _twin_h(twin, "bench1")
    before = twin.get("/api/topology").json()
    r = twin.post("/api/twin/design/import", json={}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "legacy" and "proposal" in d and "ambiguous" in d["proposal"] and "unresolved" in d["proposal"]
    assert twin.get("/api/topology").json()["rev"] == before["rev"]          # a proposal, never saved by looking
    ov = live.get("/api/overview/diagram", headers=hl).json()
    assert ov["explicit"] is False and "Import from Overview" in ov["migration"]   # the Overview says how, no blank


def test_dt02_dt06_dt07_new_design_exported_applied_and_drawn_on_the_overview(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames = len(bus.write_frames())
    _parts(live, hl)
    H = _twin_h(twin, "bench1")
    assert twin.post("/api/twin/design/import", json={}, headers=H).status_code == 200      # equipment arrives
    g = graph()
    r = twin.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g), "expected_topology_rev": 0,
                                                     "expected_diagram_rev": 0, "note": "new design"}, headers=H)
    assert r.status_code == 200, r.text
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert pv["hydraulics"]["graph_changed"] and not pv["missing"]
    r = twin.post("/api/twin/export", json={"note": "first design"}, headers=H)
    assert r.status_code == 200, r.text
    digest = r.json()["digest"]
    # the Overview is untouched until the export is applied
    assert live.get("/api/overview/diagram", headers=hl).json()["explicit"] is False
    hb = login(live, "bench1", ORIGIN)
    assert live.post("/api/twin-changes/approve", json={"digest": digest, "note": "ok"}, headers=hb).status_code == 200
    ov = live.get("/api/overview/diagram", headers=hb).json()
    assert ov["explicit"] and len(ov["topology"]["graph"]["edges"]) == len(g["edges"])
    # planned equipment: no channel, so the Overview marks it unassigned and nothing claims it runs
    assert ov["channels"]["valve-1"]["bound"] == [] and ov["channels"]["valve-1"]["roles"] >= 1
    assert all(r["coil"] in ("NONE", "UNKNOWN", "OFF") for r in ov["readings"].values())
    # applying a drawing activates nothing and writes nothing to a module
    assert len(bus.write_frames()) == frames
    assert app.state.programs.active("LIVE") == {} if hasattr(app.state.programs, "active") else True
    # journey 2: Import from Overview brings the applied drawing back as the twin's design, unchanged
    r = twin.post("/api/twin/design/import", json={}, headers=H)
    assert r.status_code == 200 and r.json()["source"] == "graph", r.text
    assert twin.get("/api/twin/changes").json()["preview"]["hydraulics"] is None   # same as the Overview


def test_dt07_import_from_overview_never_silently_replaces_a_design_not_exported(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    _parts(live, hl)
    # the main dashboard has a drawing (as if applied earlier)
    g = graph()
    assert live.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g), "expected_topology_rev": 0,
                                                        "expected_diagram_rev": 0}, headers=hl).status_code == 200
    assert live.post("/api/twin-link/publish", headers=hl).status_code == 200
    H = _twin_h(twin, "bench1")
    assert twin.post("/api/twin/design/import", json={}, headers=H).json()["source"] == "graph"
    t = twin.get("/api/topology").json()
    d = twin.get("/api/diagram/installation").json()
    # a twin edit (the pump drawn 40 px lower) that is not exported
    r = twin.post("/api/drawing/installation", json={"graph": t["graph"], "diagram": drawing(g, dy=40),
                                                     "expected_topology_rev": t["rev"], "expected_diagram_rev": d["rev"]}, headers=H)
    assert r.status_code == 200, r.text
    r = twin.post("/api/twin/design/import", json={}, headers=H)
    assert r.status_code == 409 and "press it again" in r.text                     # first press: says what it would do
    assert twin.get("/api/twin/changes").json()["preview"]["hydraulics"]["drawing_changed"]
    r = twin.post("/api/twin/design/import", json={"replace": True}, headers=H)
    assert r.status_code == 200 and r.json()["replaced"] is True
    assert twin.get("/api/twin/changes").json()["preview"]["hydraulics"] is None
