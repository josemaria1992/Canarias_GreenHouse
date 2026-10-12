"""Installation topology through the real app: processes, recipes, stock readiness, persistence, roles."""
import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.config import load_config
from aquacrop.control.recipes import demo_preset
from aquacrop.security.auth import Role
from tests.conftest import ROOT

ORIGIN = "http://127.0.0.1:8000"
PW = {"admin1": "correct horse battery", "oper1": "operator password 1", "view1": "viewer password 12"}


@pytest.fixture
def icfg():
    return load_config(ROOT / "configs" / "installation-48.yaml")


def _app(tmp_path, cfg, users=True):
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    if users:
        acc = app.state.accounts
        acc.create_user("admin1", PW["admin1"], Role.ADMIN)
        acc.create_user("oper1", PW["oper1"], Role.OPERATOR)
        acc.create_user("view1", PW["view1"], Role.VIEWER)
    return app


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


def test_status_and_processes(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "view1")
        st = c.get("/api/status", headers=h).json()
        assert st["topology"] == "installation"
        assert st["controller"]["recipe"] == "demo-sim@v1"
        assert set(st["controller"]["processes"]) == {"IRRIGATION", "RETURN", "REFILL", "ACID", "STOCK_FILL_A",
                                                      "STOCK_FILL_B", "STOCK_FILL_ACID"}
        assert all(v["state"] == "STOPPED" for v in st["controller"]["processes"].values())
        pr = c.get("/api/processes", headers=h).json()
        assert pr["recipe"]["physical_readiness"] and pr["stocks"]["A"]["status"] == "NOT_PREPARED"
        assert c.post("/api/processes/IRRIGATION/state", json={"target": "AUTO"}, headers=h).status_code == 403
        names = {s["name"] for s in c.get("/api/scenarios", headers=h).json()}
        assert "irrigation_alternation" in names


def test_operator_runs_processes_and_raw_commands_are_refused(tmp_path, icfg):
    with TestClient(app := _app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "oper1")
        r = c.post("/api/processes/IRRIGATION/state", json={"target": "AUTO"}, headers=h)
        assert r.status_code == 200, r.text
        r = c.post("/api/processes/IRRIGATION/state", json={"target": "MANUAL"}, headers=h)
        assert r.status_code == 409 and "STOPPED" in r.json()["detail"]
        r = c.post("/api/processes/NOPE/state", json={"target": "AUTO"}, headers=h)
        assert r.status_code == 404
        r = c.post("/api/commands", json={"backend": "SIMULATED", "config_version": icfg.config_version,
                                          "target": "relay_A.DO1", "desired": True, "request_id": "req-0001-abcd",
                                          "reason": "raw"}, headers=h)
        assert r.status_code == 200 and r.json()["outcome"] == "REJECTED"
        assert "process controllers" in r.json()["detail"]
        r = c.post("/api/stocks/A/acknowledge", json={"note": "batch 7"}, headers=h)
        assert r.status_code == 200
        r = c.post("/api/processes/stop-all", json={"reason": "test"}, headers=h)
        assert r.status_code == 200 and len(r.json()["results"]) == 7


def test_recipe_roles_validation_and_persistence(tmp_path, icfg):
    body = demo_preset(["S1", "S2"]).body_json()
    body["acid"]["ph_target"] = 6.1
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        ho = login(c, "oper1")
        r = c.post("/api/recipes/draft", json={"recipe_id": "site", "name": "Site", "body": body}, headers=ho)
        assert r.status_code == 200 and r.json()["status"] == "DRAFT", r.text
        bad = dict(body, acid=dict(body["acid"], ph_target="six"))
        assert c.post("/api/recipes/draft", json={"recipe_id": "bad", "name": "x", "body": bad},
                      headers=ho).status_code == 422
        r = c.post("/api/recipes/site/1/validate", headers=ho)
        assert r.json()["status"] == "VALIDATED"
        assert c.post("/api/recipes/site/1/activate", json={"reason": "x"}, headers=ho).status_code == 403
        ha = login(c, "admin1")
        r = c.post("/api/recipes/site/1/activate", json={"reason": "season start"}, headers=ha)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        assert c.post("/api/recipes/site/1/activate", json={}, headers=ha).status_code == 409
        assert c.post("/api/stocks/B/acknowledge", json={"note": "prepared"}, headers=ha).status_code == 200
    # restart: recipes, activation log and stock readiness persist; every process starts STOPPED
    with TestClient(_app(tmp_path, icfg, users=False), base_url=ORIGIN) as c:
        h = login(c, "view1")
        rec = c.get("/api/recipes", headers=h).json()
        assert rec["active"] == "site@v1"
        assert [a["action"] for a in rec["activations"]] == ["ACTIVATE", "ACTIVATE"]
        assert {r["ref"] for r in rec["recipes"]} == {"demo-sim@v1", "site@v1"}
        pr = c.get("/api/processes", headers=h).json()
        assert pr["stocks"]["B"]["status"] == "PREPARED" and pr["stocks"]["A"]["status"] == "NOT_PREPARED"
        assert all(v["state"] == "STOPPED" for v in pr["processes"].values())
        assert pr["active_recipe_body"]["acid"]["ph_target"] == 6.1
