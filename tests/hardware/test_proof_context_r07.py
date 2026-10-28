"""Review R07-01 (0.7.0): a twin result counts on the real system only in the context it was taken in.

The twin and the real system compute one proof context (programs/proof.py). A changed channel, item setting, water
network or model parameter makes an accepted twin result history: activation and every new start are refused until
the version is tested again. Moving a symbol on a drawing with an explicit graph keeps the proof. In-memory bus and
virtual modules only; nothing here is a physical test."""
import pytest
from fastapi.testclient import TestClient

from aquacrop.api.twin_app import create_twin_app
from aquacrop.control.bench import CONFIRMATION_PHRASE
from aquacrop.equipment.definitions import ItemBody
from aquacrop.programs import proof
from aquacrop.programs.engine import ItemView
from aquacrop.topology.definitions import parse as parse_topology
from tests.hardware import test_bench_programs as bp
from tests.unit.test_graph_water import demo

LED_DO2 = {"item_id": "led1", "role": "on", "point": "digital_A.DO2"}


@pytest.fixture
def linked_bench(tmp_path, monkeypatch):
    # Both fake outputs are approved in this throwaway configuration only.
    monkeypatch.setenv("AQUACROP_FEED_DIR", str(tmp_path / "feed"))
    original = bp.config
    monkeypatch.setattr(bp, "config", lambda port, **kw: original(
        port, **(kw | {"allowlist": ["digital_A.DO1", "digital_A.DO2"]})))
    yield from bp.bench.__wrapped__(tmp_path, monkeypatch)


def _twin_pass(tmp_path, program_id="blink"):
    """Run the program in the real twin service on the published snapshot and accept its DONE result."""
    twin_app = create_twin_app(tmp_path / "feed", state_dir=tmp_path / "twin", allowed_hosts=["127.0.0.1"],
                               allowed_origins=["http://127.0.0.1:8001"])
    with TestClient(twin_app, base_url="http://127.0.0.1:8001") as twin:
        origin = "http://127.0.0.1:8001"
        login = twin.post("/api/auth/login", json={"username": "admin1", "password": bp.PW["admin1"]},
                          headers={"Origin": origin})
        assert login.status_code == 200, login.text
        H = {"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"],
             "X-Runtime-Id": twin.get("/api/auth/session").json()["runtime_id"]}
        assert twin.post("/api/twin/programs/activate", json={"program_id": program_id, "version": 1},
                         headers=H).status_code == 200
        twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
        assert twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H).status_code == 200
        assert twin.post("/api/twin/run-now", json={"program_id": program_id}, headers=H).status_code == 200
        twin.post("/api/twin/control", json={"action": "step", "steps": 30}, headers=H)
        r = twin.post("/api/twin/accept", json={"program_id": program_id, "version": 1, "note": "test"}, headers=H)
        assert r.status_code == 200, r.text
        return r.json()


def _rebind_and_verify(app, c, h):
    assert c.post("/api/equipment/bind", json=LED_DO2, headers=h).status_code == 200
    app.state.equipment._bind_write("admin1", "led1", "on", "digital_A.DO2", "VERIFIED", None,
                                     {"check": "TEST_FIXTURE", "observation": "fake wire verification"})


def _sim(c, h):
    return next(p for p in c.get("/api/programs", headers=h).json()["programs"] if p["program_id"] == "blink")["simulation"]


def test_r07_01_twin_proof_from_another_channel_does_not_activate(linked_bench, tmp_path):
    app, live, bus, dio = linked_bench
    h = bp.login(live, "admin1")
    bp.setup_led(app, live, h)
    assert live.post("/api/twin-link/publish", headers=h).status_code == 200
    ev = _twin_pass(tmp_path)
    assert ev["context"] == app.state.proof_context("blink", 1)      # one definition on both sides
    sim = _sim(live, h)                                               # normal import of the twin's evidence
    assert sim["passed"] and sim["current"] is True
    _rebind_and_verify(app, live, h)
    sim = _sim(live, h)
    assert sim["passed"] and sim["current"] is False and "test this version again" in sim["why_not_current"]
    rid = live.get("/api/operation", headers=h).json()["runtime_id"]
    r = live.post("/api/operation/activate", json={"program_id": "blink", "version": 1},
                  headers=h | {"X-Runtime-Id": rid})
    assert r.status_code == 409 and "other equipment, channels" in r.text
    assert bp.on_frames(bus) == []


def test_r07_01_an_active_program_does_not_start_after_its_channel_changed(linked_bench, tmp_path):
    app, live, bus, dio = linked_bench
    h = bp.login(live, "admin1")
    bp.setup_led(app, live, h)
    assert live.post("/api/twin-link/publish", headers=h).status_code == 200
    _twin_pass(tmp_path)
    _sim(live, h)                                                     # import
    rid = live.get("/api/operation", headers=h).json()["runtime_id"]
    H = h | {"X-Runtime-Id": rid}
    assert live.post("/api/operation/activate", json={"program_id": "blink", "version": 1}, headers=H).status_code == 200
    bp.wait(lambda: all(x["ok"] for x in live.get("/api/operation", headers=h).json()["checklist"]))
    assert live.post("/api/operation/recovery/approve", json={"target": "MANUAL"}, headers=H).status_code == 200
    assert live.post("/api/bench/lease", json={"confirmation": CONFIRMATION_PHRASE, "setup_description": "LEDs on DO1/DO2",
                                               "allowlist": ["digital_A.DO1", "digital_A.DO2"], "duration_s": 300},
                     headers=h).status_code == 200
    _rebind_and_verify(app, live, h)                                  # after activation: still active, never starts
    r = live.post("/api/operation/run-now", json={"program_id": "blink"}, headers=H)
    assert r.status_code == 409 and "test this version again" in r.text
    assert bp.on_frames(bus) == []


# ---------------------------------------------------------------------- which changes are a new context
def _views(points=None, stale_s=None, **pump_props):
    t, views = demo()
    if points is not None or stale_s is not None or pump_props:
        p = views["pump"]
        body = p.body.model_dump(mode="json")
        body["properties"] |= pump_props
        if stale_s is not None:
            body["stale_after_s"] = stale_s                      # any item setting other than name and icon
        views["pump"] = ItemView("pump", p.name, p.type, ItemBody.model_validate(body),
                                 points if points is not None else p.points, {})
    return t, views


VERSION = {"digest": "d" * 64, "body": {"trigger": {"type": "MANUAL"},
                                        "action": {"type": "RUN_FOR", "item": "pump", "duration_s": 60}}}


def _ctx(t, views, lay=None):
    return proof.context(VERSION, views, t, lay)["digest"]


def test_r07_01_channel_settings_topology_and_model_numbers_are_part_of_the_context():
    t, v = _views()
    base = _ctx(t, v)
    assert _ctx(*_views(points={"run": "digital_A.DO2"})) != base               # binding
    assert _ctx(*_views(stale_s=7)) != base                                  # item settings
    assert _ctx(*_views(rated_flow_l_min=25)) != base                           # model parameter
    g = t.model_dump(mode="json")
    g["edges"] = [e for e in g["edges"] if e["id"] != "d"]                     # a connection removed
    assert _ctx(parse_topology(g), v) != base
    renamed = dict(v)
    p = v["pump"]
    renamed["pump"] = ItemView("pump", "Main pump (renamed)", p.type, p.body, p.points, {})
    assert _ctx(t, renamed) == base                                             # names are not part of it


def test_r07_01_a_moved_symbol_keeps_the_proof_but_a_changed_drawing_without_a_graph_does_not():
    t, v = _views()
    assert _ctx(t, v, "layout-a") == _ctx(t, v, "layout-b")      # explicit graph: coordinates are not part of it
    assert _ctx(None, v, "layout-a") != _ctx(None, v, "layout-b")  # drawing model: the drawing is the network


def test_r07_01_refusal_needs_the_same_context():
    sim = {"passed": True, "digest": "x", "context": "c1"}
    assert proof.refusal(sim, "x", "c1") is None
    assert "again" in proof.refusal(sim, "x", "c2")
    assert "again" in proof.refusal(sim | {"context": None}, "x", "c1")       # older evidence without context
    assert "no passing twin run" in proof.refusal(sim, "y", "c1")
