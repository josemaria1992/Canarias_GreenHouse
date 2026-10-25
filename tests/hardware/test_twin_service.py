"""0.5.0 the twin beside the live service: a live hardware profile (in-memory bus) publishes its snapshot; the
twin service imports it, runs a program on virtual modules and writes evidence that the live service records.
Nothing reaches the live bus from the twin."""
import time

import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.api.twin_app import COOKIE as TWIN_COOKIE, create_twin_app
from aquacrop.equipment.definitions import TEMPLATES
from aquacrop.security.auth import Role

ORIGIN = "http://127.0.0.1:8000"
TWIN_ORIGIN = "http://127.0.0.1:8001"
PW = {"admin1": "correct horse battery", "bench1": "bench password 123", "view1": "viewer password 12"}


def login(c, user, origin):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": origin})
    assert r.status_code == 200, r.text
    return {"Origin": origin, "X-CSRF-Token": r.json()["csrf_token"]}


@pytest.fixture
def live_and_twin(tmp_path, fake_bus, monkeypatch):
    cfg, bus, relay, ai = fake_bus
    feed = tmp_path / "feed"
    monkeypatch.setenv("AQUACROP_FEED_DIR", str(feed))
    monkeypatch.setenv("AQUACROP_TWIN_URL", TWIN_ORIGIN + "/")
    app = create_app(cfg, state_dir=tmp_path / "live", bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("admin1", PW["admin1"], Role.ADMIN, commissioning=True)
    acc.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    acc.create_user("view1", PW["view1"], Role.VIEWER)
    with TestClient(app, base_url=ORIGIN) as live:
        h = login(live, "admin1", ORIGIN)
        live.post("/api/equipment/types/seed", headers=h)
        for t in live.get("/api/equipment", headers=h).json()["types"]:
            live.post("/api/equipment/types/activate", json={"type_id": t["type_id"], "version": t["version"]}, headers=h)
        assert live.post("/api/equipment/items", json={"item_id": "p1", "name": "Pump 1", "type_id": "pump",
                                                       "icon": "pump", "body": {}}, headers=h).status_code == 200
        assert live.post("/api/equipment/bind", json={"item_id": "p1", "role": "start", "point": "relay_A.DO1"},
                         headers=h).status_code == 200
        body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "p1", "duration_s": 20}}
        r = live.post("/api/programs/draft", json={"program_id": "fill", "name": "Fill test", "body": body}, headers=h)
        assert r.status_code == 200, r.text
        r = live.post("/api/programs/validate", json={"program_id": "fill", "version": 1}, headers=h).json()
        assert r["status"] == "VALIDATED" and any("not ready" in x for x in r["physical_needs"])
        assert live.post("/api/twin-link/publish", headers=h).status_code == 200
        twin_app = create_twin_app(feed, state_dir=tmp_path / "twin", allowed_hosts=["127.0.0.1", "testserver"],
                                   allowed_origins=[TWIN_ORIGIN])
        with TestClient(twin_app, base_url=TWIN_ORIGIN) as twin:
            yield app, live, twin_app, twin, bus, h


def test_twin_config_has_only_virtual_buses_and_its_own_database(live_and_twin):
    app, live, twin_app, twin, bus, _ = live_and_twin
    cfg = twin_app.state.cfg
    assert cfg.profile.value == "SIMULATED" and {b.kind for b in cfg.buses} == {"virtual"}
    assert all(b.port is None for b in cfg.buses) and cfg.safe_state.outputs_off_on == []
    assert twin_app.state.hist.db_path != app.state.hist.db_path and cfg.config_id.endswith("-twin")


def test_every_login_works_in_the_twin_with_the_same_password(live_and_twin):
    twin = live_and_twin[3]
    for u in ("admin1", "bench1", "view1"):
        login(twin, u, TWIN_ORIGIN)
    h = login(twin, "view1", TWIN_ORIGIN)
    rid = twin.get("/api/auth/session").json()["runtime_id"]
    assert rid.startswith("TWIN:")
    r = twin.post("/api/twin/control", json={"action": "run"}, headers=h | {"X-Runtime-Id": rid})
    assert r.status_code == 403                               # viewers watch, operators run


def test_a_program_runs_in_the_twin_and_its_evidence_reaches_the_live_version(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames_before = len(bus.write_frames())
    h = login(twin, "bench1", TWIN_ORIGIN)
    rid = twin.get("/api/auth/session").json()["runtime_id"]
    H = h | {"X-Runtime-Id": rid}
    assert twin.post("/api/twin/control", json={"action": "import"}, headers={**h}).status_code == 409   # no runtime id
    v = twin.get("/api/twin").json()
    assert v["engine"]["mode"] == "RECOVERY" and [i["item_id"] for i in v["items"]] == ["p1"]
    assert twin.post("/api/twin/programs/activate", json={"program_id": "fill", "version": 1}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    r = twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H)
    assert r.status_code == 200, r.text
    assert twin.post("/api/twin/run-now", json={"program_id": "fill"}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 60}, headers=H)
    v = twin.get("/api/twin").json()
    assert v["results"] and v["results"][-1]["result"] == "DONE"
    r = twin.post("/api/twin/accept", json={"program_id": "fill", "version": 1, "note": ""}, headers=H)
    assert r.status_code == 200 and "not physical verification" in r.json()["meaning"]
    progs = live.get("/api/programs", headers=hl).json()["programs"]
    sim = next(p for p in progs if p["program_id"] == "fill")["simulation"]
    assert sim["passed"] and sim["twin_run_id"] == v["run_id"]
    assert len(bus.write_frames()) == frames_before          # the twin never reached the live bus
    live_events = app.state.hist.query("SELECT count(*) AS n FROM events WHERE kind LIKE 'PROGRAM_OP_%'")
    assert live_events[0]["n"] == 0                          # no twin operation in the live history


def test_twin_mode_and_faults_never_change_the_live_side(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    h = login(twin, "admin1", TWIN_ORIGIN)
    H = h | {"X-Runtime-Id": twin.get("/api/auth/session").json()["runtime_id"]}
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    twin.post("/api/twin/recovery/approve", json={"target": "AUTO"}, headers=H)
    twin.post("/api/twin/fault", json={"target": "relay_A", "fault": "MODULE_OFFLINE"}, headers=H)
    assert twin.get("/api/twin").json()["engine"]["mode"] == "AUTO"
    assert app.state.svc.workers["rs485_a"].health()["state"] == "RUNNING"   # the live poller is untouched
    assert not [r for r in app.state.hist.query("SELECT kind FROM events WHERE kind LIKE 'TWIN_%'")
                if r["kind"] != "TWIN_SNAPSHOT_PUBLISHED"]


def test_the_real_dashboard_hands_its_login_to_the_twin_once(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    r = live.post("/api/twin-link/ticket", headers=hl)
    assert r.status_code == 200 and r.json()["url"] == TWIN_ORIGIN + "/"
    t = r.json()["ticket"]
    twin.cookies.clear()
    r = twin.post("/api/auth/ticket", json={"ticket": t}, headers={"Origin": TWIN_ORIGIN})
    assert r.status_code == 200 and r.json()["username"] == "admin1"
    assert twin.get("/api/auth/session").json()["username"] == "admin1"
    twin.cookies.clear()
    assert twin.post("/api/auth/ticket", json={"ticket": t}, headers={"Origin": TWIN_ORIGIN}).status_code == 401
    forged = t[:-1] + ("0" if t[-1] != "0" else "1")
    assert twin.post("/api/auth/ticket", json={"ticket": forged}, headers={"Origin": TWIN_ORIGIN}).status_code == 401
    assert twin.post("/api/auth/ticket", json={"ticket": t}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_a_ticket_expires_after_a_minute(tmp_path):
    from aquacrop.twin import feed
    key = feed.write_handoff_key(tmp_path)
    now = [1000.0]
    chk = feed.TicketCheck(tmp_path, clock=lambda: now[0])
    t = feed.issue_ticket(key, "x", now=1000.0)
    now[0] = 1000.0 + feed.TICKET_S + 1
    with pytest.raises(ValueError):
        chk.username(t)
    feed.write_handoff_key(tmp_path)                  # a live restart makes earlier tickets worthless
    now[0] = 1000.0
    with pytest.raises(ValueError):
        chk.username(feed.issue_ticket(key, "x", now=1000.0))


def test_the_default_layout_is_the_0_5_5_drawing_and_validates():
    from aquacrop import layout
    lay = layout.default_layout()
    assert {"tank-main", "mp1", "mp2", "v-s1", "sector-2", "tank-sump"} <= {n.id for n in lay.nodes}
    assert layout.digest(lay) == layout.digest(layout.parse(layout.canonical(lay)))
    assert not any(layout.summary(lay, lay).values())
    with pytest.raises(ValueError):
        layout.parse(layout.canonical(lay) | {"nodes": [layout.canonical(lay)["nodes"][0]] * 2})


def test_layout_is_edited_in_the_twin_and_accepted_on_the_real_system(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames_before = len(bus.write_frames())
    h = login(twin, "bench1", TWIN_ORIGIN)
    h["X-Runtime-Id"] = twin.get("/api/auth/session").json()["runtime_id"]
    d = twin.get("/api/twin/diagram").json()
    assert d["digest"] == d["live_digest"] and "p1" in d["readings"]
    lay = d["layout"]
    mp1 = next(n for n in lay["nodes"] if n["id"] == "mp1")
    mp1.update(x=mp1["x"] + 40, item="p1")
    lay["nodes"].append({"id": "new-valve", "kind": "valve", "x": 300, "y": 600, "label": "New valve", "planned": True})
    r = twin.put("/api/twin/layout", json={"layout": lay}, headers=h)
    assert r.status_code == 200 and r.json()["issues"] == []
    bad = dict(lay, nodes=lay["nodes"] + [{"id": "x", "kind": "rocket", "x": 1, "y": 1}])
    assert twin.put("/api/twin/layout", json={"layout": bad}, headers=h).status_code == 422
    d = twin.get("/api/twin/diagram").json()
    assert d["changes"]["added"] == ["New valve"] and d["changes"]["moved"] == ["Main pump 1"]
    # the timeline records pictures of every item while the twin steps
    twin.post("/api/twin/control", json={"action": "step", "steps": 4}, headers=h)
    fr = twin.get("/api/twin/frames").json()
    assert fr and "p1" in fr[-1]["items"] and twin.get(f"/api/twin/frames?after={fr[-1]['sim_s']}").json() == []
    assert twin.post("/api/twin/layout/send", json={"note": "moved pump 1"}, headers=h).status_code == 200

    lv = live.get("/api/layout").json()
    assert lv["meta"]["source"] == "default" and len(lv["proposals"]) == 1
    prop = lv["proposals"][0]
    assert prop["note"] == "moved pump 1" and prop["changes"]["added"] == ["New valve"]
    # only administrators and operators with commissioning accept
    app.state.accounts.create_user("op1", "operator password 1", Role.OPERATOR)
    PW["op1"] = "operator password 1"
    for u in ("view1", "op1"):
        hu = login(live, u, ORIGIN)
        assert live.post("/api/layout/accept", json={"digest": prop["digest"]}, headers=hu).status_code == 403
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/layout/accept", json={"digest": prop["digest"], "note": "ok"}, headers=hb)
    assert r.status_code == 200, r.text
    lv = live.get("/api/layout").json()
    assert lv["meta"]["accepted_by"] == "bench1" and lv["proposals"] == [] and lv["previous"]
    assert next(n for n in lv["layout"]["nodes"] if n["id"] == "mp1")["item"] == "p1"
    assert "p1" in lv["readings"]
    # the twin sees the accepted layout as the real system's
    app.state.publish_snapshot()
    d = twin.get("/api/twin/diagram").json()
    assert d["digest"] == d["live_digest"]
    # and the previous one can be restored
    assert live.post("/api/layout/restore", json={"digest": lv["previous"]}, headers=hb).status_code == 200
    assert live.get("/api/layout").json()["meta"]["source"] == "restored"
    assert len(bus.write_frames()) == frames_before     # the diagram never writes to the real modules


# ---- review findings (2026-10-24)

def test_a_live_password_change_ends_the_users_twin_sessions(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    login(twin, "view1", TWIN_ORIGIN)
    assert twin.get("/api/auth/session").status_code == 200
    app.state.accounts.reset_password("view1", "a brand new password 99")
    assert live.post("/api/twin-link/publish", headers=hl).status_code == 200
    twin_app.state.svc.sync_users(twin_app.state.accounts)
    assert twin.get("/api/auth/session").status_code == 401


def test_evidence_comes_only_from_the_current_twin_run_and_a_reset_reruns_the_same_windows(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    svc = twin_app.state.svc
    h = login(twin, "bench1", TWIN_ORIGIN)
    H = h | {"X-Runtime-Id": twin.get("/api/auth/session").json()["runtime_id"]}
    twin.post("/api/twin/programs/activate", json={"program_id": "fill", "version": 1}, headers=H)
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H)
    twin.post("/api/twin/run-now", json={"program_id": "fill"}, headers=H)
    twin.post("/api/twin/control", json={"action": "step", "steps": 60}, headers=H)
    assert svc.results[-1]["result"] == "DONE"
    svc.reset("bench1")
    with pytest.raises(ValueError, match="ended DONE in this twin run"):
        svc.accept("bench1", "fill", 1, "")
    assert svc.store.prefix.startswith(svc.run_id)          # occurrences are namespaced per run


def test_twin_inputs_must_be_real_channels_with_finite_values(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    h = login(twin, "admin1", TWIN_ORIGIN)
    H = h | {"X-Runtime-Id": twin.get("/api/auth/session").json()["runtime_id"]}
    assert twin.post("/api/twin/input", content='{"point": "analog_A.AI1", "value": NaN}',
                     headers=H | {"Content-Type": "application/json"}).status_code == 422
    with pytest.raises(ValueError, match="finite"):
        twin_app.state.svc.twin.set_input("analog_A.AI1", float("inf"))
    with pytest.raises(ValueError, match="not a channel"):
        twin_app.state.svc.twin.set_input("nowhere.AI1", 1.0)


def test_the_twin_starts_from_a_configuration_with_bench_tests_enabled(fake_bus):
    from aquacrop.twin.equipment_service import twin_config
    cfg = fake_bus[0]
    live = cfg.model_dump(mode="json", exclude_none=True) | {"bench_tests": {"enabled": True}}
    twin = twin_config(live, allowed_hosts=["127.0.0.1"], allowed_origins=[TWIN_ORIGIN])
    assert twin.profile.value == "SIMULATED" and not twin.bench_tests.enabled


def _twin_h(twin, user):
    h = login(twin, user, TWIN_ORIGIN)
    return h | {"X-Runtime-Id": twin.get("/api/auth/session").json()["runtime_id"]}


def test_equipment_and_programs_made_in_the_twin_are_exported_approved_and_undone(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames_before = len(bus.write_frames())
    H = _twin_h(twin, "bench1")
    ch = twin.get("/api/twin/changes").json()
    pump_t = next(t for t in ch["types"] if t["type_id"] == "pump")
    assert ch["preview"]["empty"]
    # a new pump in the twin, on a free channel, and a program that runs it
    r = twin.post("/api/twin/equipment/save", json={"item_id": "p2", "name": "Pump 2", "type_id": "pump",
                                                    "type_version": pump_t["version"], "icon": "pump", "body": {}}, headers=H)
    assert r.status_code == 200, r.text
    assert twin.post("/api/twin/equipment/bind", json={"item_id": "p2", "role": "start", "point": "relay_A.DO1"},
                     headers=H).status_code == 409                      # p1 already uses it
    assert twin.post("/api/twin/equipment/bind", json={"item_id": "p2", "role": "start", "point": "relay_A.DO2"},
                     headers=H).status_code == 200
    assert "p2" in [i["item_id"] for i in twin.get("/api/twin").json()["items"]]
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "p2", "duration_s": 10}}
    r = twin.post("/api/twin/programs/draft", json={"program_id": "p2-test", "name": "Pump 2 test", "body": body}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "VALIDATED", r.text
    # no proof yet: the export is refused
    r = twin.post("/api/twin/export", json={"note": ""}, headers=H)
    assert r.status_code == 409 and "no run that ended DONE" in r.json()["detail"]
    assert twin.post("/api/twin/programs/activate", json={"program_id": "p2-test", "version": 1}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    assert twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H).status_code == 200
    assert twin.post("/api/twin/run-now", json={"program_id": "p2-test"}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 40}, headers=H)
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert pv["missing"] == [] and pv["programs"][0]["passed"] and pv["items"][0]["exercised_by"] == ["p2-test v1"]
    r = twin.post("/api/twin/export", json={"note": "second pump"}, headers=H)
    assert r.status_code == 200, r.text
    digest = r.json()["digest"]

    # the real system lists it with its differences
    v = live.get("/api/twin-changes", headers=hl).json()
    pkg = v["pending"][0]
    assert pkg["digest"] == digest and pkg["conflicts"] == [] and pkg["items"][0]["op"] == "create"
    assert pkg["bindings"] == [{"item_id": "p2", "role": "start", "point": "relay_A.DO2", "base_point": None}]
    app.state.accounts.create_user("op2", "operator password 2", Role.OPERATOR)
    PW["op2"] = "operator password 2"
    for u in ("view1", "op2"):
        assert live.post("/api/twin-changes/approve", json={"digest": digest}, headers=login(live, u, ORIGIN)).status_code == 403
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": digest, "note": "ok"}, headers=hb)
    assert r.status_code == 200, r.text
    items = {i["item_id"]: i for i in live.get("/api/equipment", headers=hl).json()["items"]}
    assert items["p2"]["status"] == "ACTIVE"
    progs = {p["program_id"]: p for p in live.get("/api/programs", headers=hl).json()["programs"]}
    assert progs["p2-test"]["status"] == "DRAFT" and progs["p2-test"]["simulation"]["passed"]
    assert progs["p2-test"]["simulation"]["export"] == digest
    assert live.post("/api/twin-changes/approve", json={"digest": digest}, headers=hb).status_code == 409   # once
    v = live.get("/api/twin-changes", headers=hl).json()
    assert v["pending"] == [] and v["undo_digest"] == digest
    # undo: the item is retired again, the channel cleared, the program version retired
    r = live.post("/api/twin-changes/undo", json={"digest": digest}, headers=hb)
    assert r.status_code == 200 and r.json()["problems"] == [], r.text
    items = {i["item_id"]: i for i in live.get("/api/equipment", headers=hl).json()["items"]}
    assert items["p2"]["status"] == "RETIRED"
    assert app.state.programs.get("p2-test", 1)["status"] == "RETIRED"
    assert len(bus.write_frames()) == frames_before          # nothing reached the real modules


def test_an_export_is_refused_when_the_real_system_changed_meanwhile(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    H = _twin_h(twin, "bench1")
    ch = twin.get("/api/twin/changes").json()
    p1 = next(i for i in ch["items"] if i["item_id"] == "p1")
    assert twin.post("/api/twin/equipment/save", json={"item_id": "p1", "name": "Main pump", "type_id": p1["type_id"],
                                                       "type_version": p1["type_version"], "icon": "pump", "body": p1["body"]},
                     headers=H).status_code == 200
    digest = twin.post("/api/twin/export", json={"note": ""}, headers=H).json()["digest"]
    it = live.get("/api/equipment", headers=hl).json()["items"][0]
    r = live.post("/api/equipment/items", json={"item_id": "p1", "name": "Pump one", "type_id": "pump", "icon": "pump",
                                                "body": {}, "expected_version": it["version"]}, headers=hl)
    assert r.status_code == 200, r.text
    pkg = live.get("/api/twin-changes", headers=hl).json()["pending"][0]
    assert pkg["conflicts"] and pkg["items"][0]["diff"][0]["field"] == "name"
    r = live.post("/api/twin-changes/approve", json={"digest": digest}, headers=login(live, "admin1", ORIGIN))
    assert r.status_code == 409 and "Import in the twin" in r.json()["detail"]


def test_the_twin_offers_only_channels_that_suit_each_role(live_and_twin):
    twin = live_and_twin[3]
    login(twin, "bench1", TWIN_ORIGIN)
    ch = twin.get("/api/twin/changes").json()
    pump = next(t for t in ch["types"] if t["type_id"] == "pump")
    start = next(r for r in pump["roles"] if r["role"] == "start")
    assert start["points"] and all(".DO" in p for p in start["points"])          # an output role: outputs only
    assert set(start["points"]) < set(ch["points"])


def test_the_starter_creates_the_installation_and_links_the_drawing(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    H = _twin_h(twin, "bench1")
    r = twin.post("/api/twin/starter", headers=H)
    assert r.status_code == 200, r.text
    from aquacrop.twin.starter import parts
    assert len(r.json()["created"]) == len(parts()) and r.json()["links"] > 40
    d = twin.get("/api/twin/diagram").json()
    nodes = {n["id"]: n for n in d["layout"]["nodes"]}
    assert nodes["mp1"]["item"] == "mp1" and nodes["tank-main"]["item"] == "lvl-main" and nodes["tank-main"]["role"] == "level"
    assert nodes["sector-1"]["item"] == "v-s1" and d["issues"] == []
    pipes = {p["id"]: p for p in d["layout"]["pipes"]}
    assert pipes["s1"]["item"] == "v-s1" and pipes["s1"]["flow_item"] == "flow-s1"
    assert (pipes["s1"]["from_node"], pipes["s1"]["to_node"]) == ("v-s1", "sector-1")       # ends attached
    assert (pipes["to-mp1"]["from_node"], pipes["to-mp1"]["to_node"]) == ("tank-main", "mp1")
    assert any(i["item_id"] == "mp1" for i in twin.get("/api/twin").json()["items"])      # the twin runs it
    again = twin.post("/api/twin/starter", headers=H).json()
    assert again["created"] == [] and again["skipped"] == len(parts())
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert len([i for i in pv["items"] if i["op"] == "create"]) == len(parts())


def test_the_starter_names_the_types_to_activate_first():
    from aquacrop.twin.starter import missing_types
    snap = {"equipment": {"types": [{"type_id": "pump", "status": "ACTIVE"}, {"type_id": "valve", "status": "DRAFT"}]}}
    assert missing_types(snap) == ["ec", "flow", "level", "ph", "pressure", "temperature", "valve"]


def test_the_starter_creates_nothing_when_a_name_is_taken(live_and_twin):
    twin = live_and_twin[3]
    H = _twin_h(twin, "bench1")
    lamp = next(t for t in twin.get("/api/twin/changes").json()["types"] if t["type_id"] == "test_lamp")
    assert twin.post("/api/twin/equipment/save", json={"item_id": "demo", "name": "Main pump 1", "type_id": "test_lamp",
                                                       "type_version": lamp["version"], "icon": "lamp", "body": {}},
                     headers=H).status_code == 200
    r = twin.post("/api/twin/starter", headers=H)
    assert r.status_code == 409 and "Main pump 1 (mp1): the name is used by demo" in r.json()["detail"]
    ids = {i["item_id"] for i in twin.get("/api/twin/changes").json()["items"]}
    assert ids == {"p1", "demo"}                                                  # nothing half-made


def test_twin_water_fills_tanks_through_open_valves_and_pumps_move_it():
    from aquacrop.layout import default_layout
    from aquacrop.twin.starter import link_layout, parts
    from aquacrop.twin.water import Water
    doc = link_layout(default_layout(), {p["item_id"] for p in parts()})[0].model_dump(mode="json")
    w = Water()
    on = {"v-fill-a", "v-fill-main"}
    w.step(60.0, "d", doc, lambda it: it in on, lambda it: 20.0)
    t = w.state["tanks"]
    assert t["tank-a"]["in_L_min"] == 30 and t["tank-main"]["in_L_min"] == 30 and t["tank-b"]["in_L_min"] == 0
    assert w.state["sensors"]["flow-fill-main"]["value"] == 30 and "mains" in w.state["pipes"]
    on = {"mp1", "v-s1"}                                   # main pump 1 to sector 1; the sector drains back
    w.step(60.0, "d", doc, lambda it: it in on, lambda it: 20.0)
    assert t is not w.state["tanks"] and w.state["tanks"]["tank-main"]["out_L_min"] == 20
    assert w.state["sectors"]["sector-1"] == 20 and w.state["tanks"]["tank-sump"]["in_L_min"] == 6
    on = {"v-s1"}                                          # no pump: nothing leaves the main tank
    w.step(60.0, "d", doc, lambda it: it in on, lambda it: 20.0)
    assert w.state["tanks"]["tank-main"]["out_L_min"] == 0 and not w.state["sectors"]
    doc["nodes"] = [dict(n, capacity_L=n["capacity_L"] * 2) if n["id"] == "tank-b" else n for n in doc["nodes"]]
    before = w.state["tanks"]["tank-b"]["pct"]
    w.step(1.0, "d2", doc, lambda it: False, lambda it: 20.0)
    assert w.state["tanks"]["tank-b"]["pct"] == before          # resized: same percentage


def test_a_program_made_in_the_twin_can_be_removed_so_it_is_not_sent(live_and_twin):
    twin = live_and_twin[3]
    H = _twin_h(twin, "bench1")
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "p1", "duration_s": 10}}
    assert twin.post("/api/twin/programs/draft", json={"program_id": "tryout", "name": "Try", "body": body},
                     headers=H).status_code == 200
    assert twin.post("/api/twin/programs/activate", json={"program_id": "tryout", "version": 1}, headers=H).status_code == 200
    assert [p["program_id"] for p in twin.get("/api/twin/changes").json()["preview"]["programs"]] == ["tryout"]
    r = twin.post("/api/twin/programs/remove", json={"program_id": "tryout"}, headers=H)
    assert r.status_code == 200 and r.json()["removed"] == [1]
    assert twin.get("/api/twin/changes").json()["preview"]["programs"] == []
    assert "tryout" not in [p["program_id"] for p in twin.get("/api/twin").json()["programs"]]
    # the real system's program (imported) cannot be removed here
    assert twin.post("/api/twin/programs/remove", json={"program_id": "fill"}, headers=H).status_code == 409


def test_an_export_that_can_no_longer_be_applied_can_be_dismissed(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    H = _twin_h(twin, "bench1")
    lamp = next(t for t in twin.get("/api/twin/changes").json()["types"] if t["type_id"] == "test_lamp")
    assert twin.post("/api/twin/equipment/save", json={"item_id": "lamp2", "name": "Lamp 2", "type_id": "test_lamp",
                                                       "type_version": lamp["version"], "icon": "lamp", "body": {}},
                     headers=H).status_code == 200
    first = twin.post("/api/twin/export", json={"note": "one"}, headers=H).json()["digest"]
    second = twin.post("/api/twin/export", json={"note": "two"}, headers=H).json()["digest"]   # sent twice
    hb = login(live, "bench1", ORIGIN)
    assert live.post("/api/twin-changes/dismiss", json={"digest": second}, headers=hb).status_code == 409  # still applicable
    assert live.post("/api/twin-changes/approve", json={"digest": first}, headers=hb).status_code == 200
    v = live.get("/api/twin-changes", headers=hl).json()
    assert [x["digest"] for x in v["pending"]] == [second] and v["pending"][0]["conflicts"]
    assert live.post("/api/twin-changes/dismiss", json={"digest": second}, headers=login(live, "view1", ORIGIN)).status_code == 403
    hb = login(live, "bench1", ORIGIN)                       # the viewer's login replaced the session cookie
    assert live.post("/api/twin-changes/dismiss", json={"digest": second, "note": "duplicate"}, headers=hb).status_code == 200
    assert live.get("/api/twin-changes", headers=hl).json()["pending"] == []
