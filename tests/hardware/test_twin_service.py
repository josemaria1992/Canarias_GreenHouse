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
