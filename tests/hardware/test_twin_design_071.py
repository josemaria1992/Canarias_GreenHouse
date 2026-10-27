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


def test_dt01_import_from_overview_without_a_graph_keeps_the_earlier_drawing_as_the_twins_design(live_and_twin):
    """0.7.3: the earlier drawing becomes the twin's saved design as drawn: placeholders for what is not mapped,
    nothing connected by guesswork; the Overview draws the same conversion."""
    app, live, twin_app, twin, bus, hl = live_and_twin
    _parts(live, hl)
    H = _twin_h(twin, "bench1")
    r = twin.post("/api/twin/design/import", json={}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "legacy" and "ambiguous" in d["proposal"] and d["proposal"]["kept"]
    dia = twin.get("/api/diagram/installation").json()["diagram"]
    ov = live.get("/api/overview/diagram", headers=hl).json()
    assert ov["explicit"] is False and "Import from Overview" in ov["migration"]   # the Overview says how, no blank
    same = lambda d: {k: d.get(k) for k in ("symbols", "placeholders", "texts", "shapes", "sketches")}   # noqa: E731
    from aquacrop import diagram as dg
    assert same(dg.canonical(dg.parse(dia))) == same(dg.canonical(dg.parse(ov["legacy_view"]["diagram"])))
    ref = twin.get("/api/twin/design/reference").json()
    assert ref["source"] == "legacy" and ref["kept"] == ov["legacy_view"]["kept"]


def test_dt02_dt06_dt07_new_design_exported_applied_and_drawn_on_the_overview(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames = len(bus.write_frames())
    _parts(live, hl)
    H = _twin_h(twin, "bench1")
    assert twin.post("/api/twin/design/import", json={}, headers=H).status_code == 200      # equipment arrives
    g = graph()
    t0, d0 = twin.get("/api/topology").json(), twin.get("/api/diagram/installation").json()
    r = twin.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g), "expected_topology_rev": t0["rev"],
                                                     "expected_diagram_rev": d0["rev"], "note": "new design"}, headers=H)
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


def test_prog10_the_twin_simulates_a_nutrient_preparation_labelled(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    H = _twin_h(twin, "bench1")
    r = twin.post("/api/twin/nutrient/simulate", json={"volume_L": 1000, "additions": [{"what": "A", "litres": 5, "at_s": 0}]},
                  headers=H)
    assert r.status_code == 200, r.text
    assert "SIMULATED" in r.json()["label"] and r.json()["final"]["ec_bulk"] > 0.35


def test_c077_06_an_empty_design_clears_only_the_twins_drawing_and_keeps_equipment_to_place(live_and_twin):
    """Codex 0.7.7 C077-06 (owner 27 Oct 2026): after the earlier drawing was imported (sketches, placeholders, labels),
    a new empty design saved in the twin is really empty on reload; the equipment is still offered to place; the
    Overview and the equipment on the main dashboard are untouched."""
    app, live, twin_app, twin, bus, hl = live_and_twin
    _parts(live, hl)
    H = _twin_h(twin, "bench1")
    assert twin.post("/api/twin/design/import", json={}, headers=H).status_code == 200
    t, d = twin.get("/api/topology").json(), twin.get("/api/diagram/installation").json()
    old = d["diagram"]
    assert old["sketches"] or old["placeholders"]                         # the earlier drawing's content is there
    blank = {k: old[k] for k in ("kind", "format", "view", "title", "width", "height", "grid")} | {
        "symbols": [], "routes": [], "sketches": [], "placeholders": [], "shapes": [], "texts": []}
    ov_before = live.get("/api/overview/diagram", headers=hl).json()
    items_before = live.get("/api/equipment/items", headers=hl).json()
    r = twin.post("/api/drawing/installation", json={"graph": {"nodes": [], "edges": [], "measures": []}, "diagram": blank,
                                                     "expected_topology_rev": t["rev"], "expected_diagram_rev": d["rev"],
                                                     "note": "new design"}, headers=H)
    assert r.status_code == 200, r.text
    t2, d2 = twin.get("/api/topology").json(), twin.get("/api/diagram/installation").json()
    assert t2["graph"]["nodes"] == [] and t2["graph"]["edges"] == [] and t2["rev"] > 0 and d2["rev"] > 0
    for k in ("symbols", "routes", "sketches", "placeholders", "shapes", "texts"):
        assert d2["diagram"].get(k, []) == [], k                         # empty lists are left out when stored
    assert {i for i in t2["items"]} >= {p[0] for p in PARTS}              # still listed beside the drawing to place
    assert live.get("/api/overview/diagram", headers=hl).json() == ov_before
    assert live.get("/api/equipment/items", headers=hl).json() == items_before


def test_c077_08_answers_are_kept_per_drawing_and_a_later_import_is_complete_without_asking(live_and_twin):
    """Codex 0.7.7 C077-08: the reconciliation is answered once in the twin; the answers are kept for that earlier
    drawing, so the next Import from Overview applies them without asking, and nothing is called ready while a
    blocker is left."""
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames = len(bus.write_frames())
    _parts(live, hl)
    H = _twin_h(twin, "bench1")
    first = twin.post("/api/twin/design/import", json={}, headers=H).json()["proposal"]
    qs = first["questions"]
    assert qs and all(q["answer"] is None for q in qs) and first["ready"] is False
    t0 = twin.get("/api/topology").json()
    view = twin.get("/api/twin/design/reconcile").json()
    assert view["drawing"] == "legacy" and [q["id"] for q in view["questions"]] == [q["id"] for q in qs]
    assert view["ready"] == (view["blockers"] == [])
    r = twin.post("/api/twin/design/reconcile", json={"answers": {q["id"]: "yes" for q in qs}}, headers=H)
    assert r.status_code == 200, r.text                  # the design is still the import's: no second press needed
    after = r.json()["proposal"]
    assert all(q["answer"] == "yes" for q in after["questions"]) and after["ready"] == (after["blockers"] == [])
    assert not any(b["id"].startswith("answer:") for b in after["blockers"])
    t1, d1 = twin.get("/api/topology").json(), twin.get("/api/diagram/installation").json()
    assert t1["rev"] >= t0["rev"] and len(t1["graph"]["edges"]) >= len(t0["graph"]["edges"])   # an unchanged save adds no revision
    assert d1["diagram"]["symbols"]
    again = twin.post("/api/twin/design/import", json={}, headers=H)
    assert again.status_code == 200 and again.json()["replaced"] is False, again.text
    assert all(q["answer"] == "yes" for q in again.json()["proposal"]["questions"])
    t2 = twin.get("/api/topology").json()
    assert t2["graph"] == t1["graph"]                    # repeatable: the same complete graph, nothing asked again
    bad = twin.post("/api/twin/design/reconcile", json={"answers": {"x": "maybe"}}, headers=H)
    assert bad.status_code == 422
    assert len(bus.write_frames()) == frames             # nothing reaches a module
