"""0.7.0 MOD-01 and the graph's way back: a simulated module exists in the twin only (its channels can be used there,
never exported); a twin edit of the installation graph and a block travel with the export, apply on approval
(drafts) and are undone. The fake bus sees no write."""
from tests.hardware.test_twin_graph_070 import KINDS, POS, graph, drawing  # noqa: F401
from tests.hardware.test_twin_service import ORIGIN, _twin_h, live_and_twin, login  # noqa: F401  (fixture)


def test_mod01_a_simulated_module_adds_twin_channels_that_stay_in_the_twin(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    H = _twin_h(twin, "bench1")
    r = twin.post("/api/twin/modules", json={"device_uid": "sim_io", "model": "WAVESHARE_MODBUS_RTU_IO_8CH",
                                             "label": "spare IO"}, headers=H)
    assert r.status_code == 200, r.text
    H["X-Runtime-Id"] = r.json()["runtime_id"]                    # a new twin run
    assert "sim_io" in {d.device_uid for d in twin_app.state.svc.cfg.devices}
    assert twin.post("/api/twin/modules", json={"device_uid": "relay_A", "model": "WAVESHARE_MODBUS_RTU_IO_8CH"},
                     headers=H).status_code == 409                # the real modules' names are taken
    ch = twin.get("/api/twin/changes").json()
    pump_t = next(t for t in ch["types"] if t["type_id"] == "pump")
    assert twin.post("/api/twin/equipment/save", json={"item_id": "p9", "name": "Sim pump", "type_id": "pump",
                                                       "type_version": pump_t["version"], "icon": "pump", "body": {}},
                     headers=H).status_code == 200
    r = twin.post("/api/twin/equipment/bind", json={"item_id": "p9", "role": "start", "point": "sim_io.DO1"}, headers=H)
    assert r.status_code == 200, r.text                          # the twin's channel lists follow its configuration
    pv = twin.get("/api/twin/changes").json()["preview"]
    # 0.7.1 (owner decision, DT-06): a simulated module's channel stays in the twin; it never travels with an export
    # and does not stop one (the equipment goes as planned, without that channel)
    assert any("sim_io.DO1" in m and "stays in the twin" in m for m in pv["twin_only"])
    assert not any(b["point"] == "sim_io.DO1" for b in pv["bindings"]) and not pv["missing"]
    r = twin.post("/api/twin/export", json={"note": ""}, headers=H)
    assert r.status_code == 200 and r.json()["bindings"] == 0, r.text
    from aquacrop.twin.changes import read_exports
    import os
    pkg = next(x for x in read_exports(os.environ["AQUACROP_FEED_DIR"]) if x["digest"] == r.json()["digest"])
    assert not any((b.get("point") or "").startswith("sim_io.") for b in pkg["bindings"])
    assert "sim_io" not in {d.device_uid for d in app.state.cfg.devices}     # the real system never saw it
    r = twin.post("/api/twin/modules/remove", json={"device_uid": "sim_io"}, headers=H)
    assert r.status_code == 200 and "sim_io" not in {d.device_uid for d in twin_app.state.svc.cfg.devices}


def test_a_twin_graph_edit_travels_with_the_export_applies_as_drafts_and_is_undone(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames = len(bus.write_frames())
    from tests.hardware.test_twin_graph_070 import PARTS
    for iid, name, tid, icon, props in PARTS:
        assert live.post("/api/equipment/items", json={"item_id": iid, "name": name, "type_id": tid, "icon": icon,
                                                       "body": {"properties": props}}, headers=hl).status_code == 200
    assert live.post("/api/twin-link/publish", headers=hl).status_code == 200
    H = _twin_h(twin, "bench1")
    assert twin.post("/api/twin/control", json={"action": "import"}, headers=H).status_code == 200
    g = graph()
    r = twin.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g), "expected_topology_rev": 0,
                                                     "expected_diagram_rev": 0}, headers=H)
    assert r.status_code == 200, r.text
    body = {"template": "IRRIGATION_SECTOR", "members": list(KINDS), "supply": "tank-1", "destination": "sector-1",
            "meter": "meter-1", "plan": {"startup": [{"do": "OPEN", "node": "valve-1"}, {"do": "START", "node": "pump-1"}],
                                          "normal_stop": [{"do": "STOP", "node": "pump-1"}, {"do": "CLOSE", "node": "valve-1"}]}}
    assert twin.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body}, headers=H).status_code == 200
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert pv["hydraulics"] and pv["hydraulics"]["graph_changed"] and pv["hydraulics"]["blocks"][0]["block_id"] == "s1"
    r = twin.post("/api/twin/export", json={"note": "the sector line"}, headers=H)
    assert r.status_code == 200, r.text
    digest = r.json()["digest"]
    pkg = live.get("/api/twin-changes", headers=hl).json()["pending"][0]
    assert pkg["hydraulics"]["edges"] == 4 and pkg["conflicts"] == []
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": digest, "note": "ok"}, headers=hb)
    assert r.status_code == 200, r.text
    blocks = app.state.blocks
    assert len(blocks.graph().edges) == 4 and blocks.latest()["s1"]["status"] == "DRAFT"     # validated on live later
    assert live.post("/api/twin-changes/undo", json={"digest": digest}, headers=hb).json()["problems"] == []
    assert len(blocks.graph().edges) == 0 and blocks.latest()["s1"]["status"] == "RETIRED"
    assert len(bus.write_frames()) == frames
