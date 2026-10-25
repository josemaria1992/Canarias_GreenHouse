"""Users and roles (owner feedback 2026-10-24): an administrator has every right, can change, disable, delete
users and reset their passwords; the last administrator is protected; role texts are served for the form."""
import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.config import load_config
from aquacrop.security.auth import Perm, Role
from tests.conftest import ROOT

ORIGIN = "http://127.0.0.1:8000"
PW = {"boss": "chief password 12345", "op1": "operator password 1", "view1": "viewer password 12"}


def login(c, user, pw=None):
    r = c.post("/api/auth/login", json={"username": user, "password": pw or PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


@pytest.fixture
def app_c(tmp_path):
    app = create_app(load_config(ROOT / "configs" / "installation-80.yaml"), state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("boss", PW["boss"], Role.ADMIN)                    # no commissioning flag set
    acc.create_user("op1", PW["op1"], Role.OPERATOR)
    acc.create_user("view1", PW["view1"], Role.VIEWER)
    with TestClient(app, base_url=ORIGIN) as c:
        yield app, c


def test_an_admin_has_every_permission_including_commissioning(app_c):
    app, c = app_c
    login(c, "boss")
    s = c.get("/api/auth/session").json()
    assert s["commissioning"] is True and set(s["permissions"]) == {p.value for p in Perm}
    roles = c.get("/api/users/roles").json()
    assert set(roles["roles"]) == {"viewer", "operator", "admin"} and "Commissioning" in roles["commissioning"]


def test_admin_changes_resets_and_deletes_users(app_c):
    app, c = app_c
    h = login(c, "boss")
    assert c.post("/api/users/op1", json={"commissioning": True}, headers=h).status_code == 200
    assert c.post("/api/users/view1", json={"role": "operator"}, headers=h).status_code == 200
    r = c.post("/api/users/op1/password", json={"new_password": "temporary pass 777"}, headers=h)
    assert r.status_code == 200
    assert c.post("/api/users/op1/password", json={"new_password": "short"}, headers=h).status_code == 422
    assert c.delete("/api/users/view1", headers=h).status_code == 200
    assert "view1" not in [u["username"] for u in app.state.accounts.list_users()]
    assert c.delete("/api/users/boss", headers=h).status_code == 409          # not yourself
    c.post("/api/auth/logout", headers=h)
    h = login(c, "op1", "temporary pass 777")
    s = c.get("/api/auth/session").json()
    assert s["must_change_password"] is True                                  # must choose a new one first
    assert c.delete("/api/users/boss", headers=h).status_code == 403          # operators manage no users


def test_the_last_admin_is_protected(app_c):
    app, c = app_c
    app.state.accounts.create_user("boss2", "second admin pass 1x", Role.ADMIN)
    h = login(c, "boss")
    assert c.delete("/api/users/boss2", headers=h).status_code == 200
    assert c.post("/api/users/boss", json={"disabled": True}, headers=h).status_code == 409
