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


def test_plant_view_binds_every_signal_with_units_quality_and_age(tmp_path, icfg):
    """UI-02 data: the installation page draws from one view; each logical signal is bound to a point,
    the drive adapter or nothing (UNBOUND), and carries unit, quality and age. Requested (command
    readback) and observed (feedback) are separate signals; no installed-probe or mixer claim."""
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        login(c, "view1")
        p = c.get("/api/plant").json()
        sig = p["signals"]
        by = {}
        for name, s in sig.items():
            by.setdefault(s["binding"], []).append(name)
        assert len(by["POINT"]) == 48 and by["DRIVE_ADAPTER"] == ["drive.MAIN.run"]
        assert sorted(by["UNBOUND"]) == sorted(
            ["main.temperature", "tank.A.low", "tank.B.low", "tank.ACID.low", "valve.S1.closed_limit",
             "valve.S2.closed_limit"] + [f"valve.{v}.open_limit" for v in
                                         ("FILL_MAIN", "FILL_A", "FILL_B", "FILL_ACID", "OUT_A", "OUT_B", "OUT_ACID")])
        assert len({s["point"] for s in sig.values() if s["point"]}) == 48       # no point bound twice
        for name in by["POINT"]:
            s = sig[name]["sample"]
            assert s is not None and s["quality"] and s["age_s"] >= 0 and sig[name]["label"], name
        assert sig["tank.MAIN.level"]["sample"]["unit"] == "L" and sig["main.ph"]["sample"]["unit"] == "pH"
        assert sig["pressure.S1"]["sample"]["unit"] == "bar" and sig["flow.S2"]["sample"]["unit"] == "L/min"
        # requested vs observed: the contactor command and its auxiliary contact are different points
        assert sig["contactor.MP1.cmd"]["kind"] == "actuator" and sig["contactor.MP1.closed"]["kind"] == "digital"
        assert sig["contactor.MP1.cmd"]["point"] != sig["contactor.MP1.closed"]["point"]
        assert sig["drive.MAIN.run"]["adapter"]["kind"] and "requested" in sig["drive.MAIN.run"]["adapter"]
        lay = p["layout"]
        assert lay["tanks_L"] == {"MAIN": 20000.0, "SUMP": 5000.0, "A": 5000.0, "B": 5000.0, "ACID": 5000.0}
        assert [len(s["channels"]) for s in lay["sectors"]] == [6, 7] and lay["agitators"] == 0
        assert lay["outlets_per_channel"] * 13 == 52 and lay["shared_drive"] == "MAIN"
        assert lay["channel"]["retained_L"] == pytest.approx(85 * 0.30 * 0.05 * 1000)
        assert p["read_only"] is False and "not wiring" in p["mapping"]
        assert set(p["processes"]["processes"]) == {"IRRIGATION", "RETURN", "REFILL", "ACID", "STOCK_FILL_A",
                                                    "STOCK_FILL_B", "STOCK_FILL_ACID"}
        assert "truth" not in str(p).lower()                                     # twin truth stays out of the view


def test_plant_view_needs_the_installation_topology(tmp_path, cfg48):
    with TestClient(_app(tmp_path, cfg48), base_url=ORIGIN) as c:
        login(c, "view1")
        assert c.get("/api/plant").status_code == 409


def test_recipe_form_offers_choices_for_enumerated_fields(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        login(c, "view1")
        form = c.get("/api/recipes").json()["form"]
        opts = {r["path"]: r["options"] for r in form if r["options"]}
        assert set(opts) == {"refill.endpoint_mode", "refill.nutrient_mode", "return.duty_policy"}, opts
        assert all(len(v) >= 2 for v in opts.values())


# (path, body, lowest role allowed). Forged and direct requests: the gate checks the session, origin
# and CSRF token before the body; the role check follows. UI-04.
MUTATIONS = [
    ("/api/processes/stop-all", {"reason": "t"}, Role.OPERATOR),
    ("/api/processes/ACID/state", {"target": "STOPPED"}, Role.OPERATOR),
    ("/api/processes/ACID/manual", {"op": {"volume_L": 1}}, Role.OPERATOR),
    ("/api/processes/ACID/reset", None, Role.OPERATOR),
    ("/api/stocks/A/acknowledge", {"note": "x"}, Role.OPERATOR),
    ("/api/stocks/A/invalidate", {"reason": "x"}, Role.OPERATOR),
    ("/api/twin/stock/A/prepare", None, Role.OPERATOR),
    ("/api/twin/outlet", {"channel": 1, "outlet": 1, "opening": 0.5}, Role.OPERATOR),
    ("/api/recipes/draft", {"recipe_id": "t", "name": "t", "body": demo_preset(["S1", "S2"]).body_json()},
     Role.OPERATOR),
    ("/api/recipes/demo-sim/1/validate", None, Role.OPERATOR),
    ("/api/recipes/demo-sim/1/activate", {"reason": "x"}, Role.ADMIN),
    ("/api/recipes/rollback", {"reason": "x"}, Role.ADMIN),
]
RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


def test_role_table_is_enforced_on_every_process_and_recipe_mutation(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        for user, role in (("view1", Role.VIEWER), ("oper1", Role.OPERATOR), ("admin1", Role.ADMIN)):
            c.cookies.clear()
            h = login(c, user)
            for path, body, least in MUTATIONS:
                r = c.post(path, json=body, headers=h)
                if RANK[role] < RANK[least]:
                    assert r.status_code == 403, (user, path, r.status_code)
                else:
                    assert r.status_code not in (401, 403), (user, path, r.status_code, r.text)
            for path, body, _ in MUTATIONS:                                       # forged / direct requests
                assert c.post(path, json=body, headers={"Origin": ORIGIN}).status_code == 403, path
                assert c.post(path, json=body, headers=h | {"Origin": "http://evil.example"}).status_code == 403
                assert c.post(path, json=body, headers=h | {"X-CSRF-Token": "forged"}).status_code == 403
        c.cookies.clear()
        for path, body, _ in MUTATIONS:
            assert c.post(path, json=body, headers={"Origin": ORIGIN}).status_code == 401, path
