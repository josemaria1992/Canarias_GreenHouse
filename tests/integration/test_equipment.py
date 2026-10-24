"""0.5.0 equipment types, items and channel bindings on the simulation (installation-80, virtual modules)."""
import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.config import load_config
from aquacrop.equipment.definitions import TEMPLATES, TypeBody
from aquacrop.security.auth import Role
from tests.conftest import ROOT

ORIGIN = "http://127.0.0.1:8000"
PW = {"bench1": "bench password 123", "view1": "viewer password 12", "oper1": "operator password 1",
      "admin1": "admin password 123"}


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


@pytest.fixture
def sim(tmp_path):
    cfg = load_config(ROOT / "configs" / "installation-80.yaml")
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    acc.create_user("oper1", PW["oper1"], Role.OPERATOR)
    acc.create_user("view1", PW["view1"], Role.VIEWER)
    acc.create_user("admin1", PW["admin1"], Role.ADMIN)
    with TestClient(app, base_url=ORIGIN) as c:
        yield app, c, tmp_path


def activate_templates(c):
    h = login(c, "admin1")
    assert set(c.post("/api/equipment/types/seed", headers=h).json()["created"]) == set(TEMPLATES)
    for t in c.get("/api/equipment", headers=h).json()["types"]:
        if t["status"] == "DRAFT":
            assert c.post("/api/equipment/types/activate", json={"type_id": t["type_id"], "version": t["version"]},
                          headers=h).status_code == 200
    c.post("/api/auth/logout", headers=h)


def test_every_template_is_a_valid_type():
    for tid, t in TEMPLATES.items():
        TypeBody.model_validate(t["body"])


def test_type_validation_refuses_unsafe_or_inconsistent_definitions(sim):
    app, c, _ = sim
    h = login(c, "oper1")
    base = {"category": "X", "roles": [{"role": "start", "label": "Start", "direction": "OUTPUT", "signal": "DIGITAL",
                                        "purpose": "COMMAND"}], "operations": ["RUN_FOR"]}
    r = c.post("/api/equipment/types/draft", json={"type_id": "x", "name": "X", "body": base}, headers=h)
    assert r.status_code == 409 and "maximum runtime" in r.text
    bad = dict(base, limits={"max_runtime_s": 60}, roles=[dict(base["roles"][0], signal="ANALOG")])
    assert "analog output" in c.post("/api/equipment/types/draft", json={"type_id": "x", "name": "X", "body": bad}, headers=h).text
    bad = dict(base, limits={"max_runtime_s": 60}, default_icon="<script>")
    assert "built-in icon" in c.post("/api/equipment/types/draft", json={"type_id": "x", "name": "X", "body": bad}, headers=h).text
    ok = dict(base, limits={"max_runtime_s": 60})
    assert c.post("/api/equipment/types/draft", json={"type_id": "x", "name": "X", "body": ok}, headers=h).status_code == 200
    # operators save drafts; activation is an administrator's
    assert c.post("/api/equipment/types/activate", json={"type_id": "x", "version": 1}, headers=h).status_code == 403
    h2 = login(c, "view1")
    assert c.post("/api/equipment/types/draft", json={"type_id": "y", "name": "Y", "body": ok}, headers=h2).status_code == 403


def test_create_item_bind_and_verify_an_output_with_readback(sim):
    app, c, _ = sim
    activate_templates(c)
    h = login(c, "oper1")
    r = c.post("/api/equipment/items", json={"item_id": "led-1", "name": "LED 1", "type_id": "test_lamp",
                                             "icon": "lamp", "body": {}}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1
    # an operator without the commissioning flag cannot choose channels
    assert c.post("/api/equipment/bind", json={"item_id": "led-1", "role": "on", "point": "digital_A.DO1"},
                  headers=h).status_code == 403
    c.post("/api/auth/logout", headers=h)
    h = login(c, "bench1")
    # the template accepts only an IO 8CH transistor output, not a relay contact
    r = c.post("/api/equipment/bind", json={"item_id": "led-1", "role": "on", "point": "relay_A.DO1"}, headers=h)
    assert r.status_code == 409 and "OPEN_DRAIN_SINK" in r.text
    r = c.post("/api/equipment/bind", json={"item_id": "led-1", "role": "on", "point": "digital_A.DI1"}, headers=h)
    assert r.status_code == 409 and "needs a DO channel" in r.text
    assert c.post("/api/equipment/bind", json={"item_id": "led-1", "role": "on", "point": "digital_A.DO1"},
                  headers=h).json()["status"] == "PROPOSED"
    ready = c.get("/api/equipment", headers=h).json()["readiness"]["led-1"]
    assert not ready["ready"] and ready["unverified"] == ["On"]
    r = c.post("/api/equipment/bind/verify", json={"item_id": "led-1", "role": "on", "observation": "LED lit"}, headers=h)
    assert r.status_code == 409 and "ON and then OFF" in r.text
    assert c.post("/api/io/source", json={"source": "IO_TEST"}, headers=h).status_code == 200
    v = c.get("/api/status", headers=h).json()["config_version"]
    for i, d in enumerate((True, False)):
        r = c.post("/api/commands", json={"backend": "SIMULATED", "config_version": v, "target": "digital_A.DO1",
                                          "desired": d, "request_id": f"ui_eqtest{i:08d}", "reason": "check"}, headers=h)
        assert r.json()["outcome"] == "READBACK_CONFIRMED"
    r = c.post("/api/equipment/bind/verify", json={"item_id": "led-1", "role": "on", "observation": "virtual LED on, off"},
               headers=h)
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED" and r.json()["evidence"]["check"] == "DO_ON_OFF"
    assert c.get("/api/equipment", headers=h).json()["readiness"]["led-1"]["ready"]
    # one channel carries one role of one item
    c.post("/api/auth/logout", headers=h)
    h = login(c, "oper1")
    c.post("/api/equipment/items", json={"item_id": "led-2", "name": "LED 2", "type_id": "test_lamp", "icon": "lamp",
                                         "body": {}}, headers=h)
    c.post("/api/auth/logout", headers=h)
    h = login(c, "bench1")
    r = c.post("/api/equipment/bind", json={"item_id": "led-2", "role": "on", "point": "digital_A.DO1"}, headers=h)
    assert r.status_code == 409 and "already carries led-1.on" in r.text


def test_new_type_version_changes_no_item_until_upgraded(sim):
    app, c, _ = sim
    activate_templates(c)
    h = login(c, "oper1")
    c.post("/api/equipment/items", json={"item_id": "p1", "name": "Pump 1", "type_id": "pump", "icon": "pump",
                                         "body": {"properties": {"rated_flow_l_min": 30}}}, headers=h)
    body = dict(TEMPLATES["pump"]["body"])
    body["roles"] = [r for r in body["roles"] if r["role"] != "fault"]
    assert c.post("/api/equipment/types/draft", json={"type_id": "pump", "name": "Pump", "body": body},
                  headers=h).json()["version"] == 2
    ha = login(c, "admin1")
    assert c.post("/api/equipment/types/activate", json={"type_id": "pump", "version": 2}, headers=ha).status_code == 200
    h = ha                                      # one browser session per client: the admin's from here on
    E = c.get("/api/equipment", headers=h).json()
    assert next(i for i in E["items"] if i["item_id"] == "p1")["type_version"] == 1
    assert {(t["version"], t["status"]) for t in E["types"] if t["type_id"] == "pump"} == {(1, "SUPERSEDED"), (2, "ACTIVE")}
    prev = c.get("/api/equipment/items/p1/upgrade?to_version=2", headers=h).json()
    assert prev["roles_removed"] == ["fault"] and prev["from"] == 1 and prev["to"] == 2
    # a stale edit is refused
    assert c.post("/api/equipment/items/p1/upgrade", json={"to_version": 2, "expected_version": 9}, headers=h).status_code == 409
    r = c.post("/api/equipment/items/p1/upgrade", json={"to_version": 2, "expected_version": 1}, headers=h)
    assert r.status_code == 200 and r.json()["type_version"] == 2 and r.json()["version"] == 2


def test_item_properties_are_validated_against_the_type(sim):
    app, c, _ = sim
    activate_templates(c)
    h = login(c, "oper1")
    r = c.post("/api/equipment/items", json={"item_id": "p1", "name": "Pump 1", "type_id": "pump", "icon": "pump",
                                             "body": {"properties": {"rated_flow_l_min": -5}}}, headers=h)
    assert r.status_code == 409 and "below the minimum" in r.text
    r = c.post("/api/equipment/items", json={"item_id": "p1", "name": "Pump 1", "type_id": "pump", "icon": "pump",
                                             "body": {"properties": {"colour": "red"}}}, headers=h)
    assert r.status_code == 409 and "not defined by the type" in r.text
    r = c.post("/api/equipment/items", json={"item_id": "lv", "name": "Level", "type_id": "level", "icon": "level",
                                             "body": {"scaling": {"level": {"raw_min": 4, "raw_max": 20, "raw_unit": "mA",
                                                                            "eng_min": 0, "eng_max": 100, "unit": "cm",
                                                                            "provenance": "CALIBRATED"}}}}, headers=h)
    assert r.status_code == 409 and "calibration record" in r.text


def test_equipment_survives_restart_and_retire_keeps_history(sim):
    app, c, state = sim
    activate_templates(c)
    h = login(c, "oper1")
    c.post("/api/equipment/items", json={"item_id": "b1", "name": "Button 1", "type_id": "button", "icon": "button",
                                         "body": {}}, headers=h)
    c.close()
    cfg = load_config(ROOT / "configs" / "installation-80.yaml")
    app2 = create_app(cfg, state_dir=state, bind_host="127.0.0.1")
    with TestClient(app2, base_url=ORIGIN) as c2:
        h = login(c2, "admin1")
        items = c2.get("/api/equipment", headers=h).json()["items"]
        assert [i["item_id"] for i in items] == ["b1"]
        r = c2.post("/api/equipment/items/b1/retire", json={"reason": "bench rebuilt"}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "RETIRED"
        rows = app2.state.equipment.h.query("SELECT status FROM equipment_items WHERE item_id='b1'")
        assert [r["status"] for r in rows] == ["RETIRED"]          # kept, not deleted
        assert c2.post("/api/equipment/items", json={"item_id": "b1", "name": "Button 1", "type_id": "button",
                                                     "icon": "button", "body": {}, "expected_version": 1},
                       headers=h).status_code == 409


def test_export_and_import_create_drafts_only(sim):
    app, c, _ = sim
    activate_templates(c)
    h = login(c, "oper1")
    doc = c.get("/api/equipment/export", headers=h).json()
    assert doc["kind"] == "AQUACROP_EQUIPMENT_EXPORT"
    doc["types"] = [dict(t, type_id="copy-" + t["type_id"]) for t in doc["types"]]
    r = c.post("/api/equipment/import", json={"doc": doc}, headers=h)
    assert r.status_code == 200 and len(r.json()["types"]) == len(TEMPLATES)
    assert all(t["status"] == "DRAFT" for t in c.get("/api/equipment", headers=h).json()["types"]
               if t["type_id"].startswith("copy-"))
