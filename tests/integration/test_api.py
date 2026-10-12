"""AT-SEC-01/02, AT-UI-01, API behaviour. Uses the real app with its lifespan (driver task running)."""
import re
import time

import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import _escape_csv, create_app
from aquacrop.security.auth import Role

ORIGIN = "http://127.0.0.1:8000"


def _app(tmp_path, cfg):
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("admin1", "correct horse battery", Role.ADMIN)
    acc.create_user("oper1", "operator password 1", Role.OPERATOR)
    acc.create_user("view1", "viewer password 12", Role.VIEWER)
    return app


@pytest.fixture
def env(tmp_path, cfg48):
    with TestClient(app := _app(tmp_path, cfg48), base_url=ORIGIN) as c:
        yield app, c


@pytest.fixture
def env_dense(tmp_path, cfg48):
    """Simulation-only 1 s snapshot stride (simulated time) so export/trend tests get rows quickly."""
    cfg = cfg48.model_copy(update={"historian": cfg48.historian.model_copy(update={"sim_history_stride_s": 1.0})})
    with TestClient(app := _app(tmp_path, cfg), base_url=ORIGIN) as c:
        yield app, c


def login(c, user, pw):
    r = c.post("/api/auth/login", json={"username": user, "password": pw}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


PW = {"admin1": "correct horse battery", "oper1": "operator password 1", "view1": "viewer password 12"}


def test_health_is_minimal_and_status_needs_auth(env):
    app, c = env
    h = c.get("/api/health").json()
    assert set(h) == {"ok", "storage_ok", "acquisition_ok"}
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/openapi.json").status_code == 401
    assert c.get("/docs").status_code == 404


def test_login_failures_and_rate_limit(env):
    app, c = env
    assert c.post("/api/auth/login", json={"username": "oper1", "password": "wrong password!!"},
                  headers={"Origin": ORIGIN}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "nobody", "password": "wrong password!!"},
                  headers={"Origin": ORIGIN}).status_code == 401
    codes = [c.post("/api/auth/login", json={"username": "view1", "password": "bad bad bad bad"},
                    headers={"Origin": ORIGIN}).status_code for _ in range(12)]
    assert 429 in codes
    ev = app.state.hist
    ev.wait_idle()
    assert ev.query("SELECT count(*) FROM events WHERE kind='REQUEST_REJECTED'")[0][0] >= 3
    rows = ev.query("SELECT payload_json FROM events WHERE kind='REQUEST_REJECTED'")
    assert not any("bad bad" in r[0] or "wrong password" in r[0] for r in rows)  # credentials never stored


def test_every_mutation_requires_session_origin_and_csrf(env):
    app, c = env
    posts = sorted({r.path for r in app.routes if "POST" in getattr(r, "methods", set())} - {"/api/auth/login"})
    for path in posts:
        path = path.replace("{username}", "oper1")
        assert c.post(path, json={}).status_code == 401, path
    hdr = login(c, "admin1", PW["admin1"])
    for path in posts:
        path = path.replace("{username}", "oper1")
        r = c.post(path, json={}, headers={"Origin": ORIGIN})
        assert r.status_code == 403 and "CSRF" in r.text, (path, r.text)
        r = c.post(path, json={}, headers={"Origin": "http://evil.example", "X-CSRF-Token": hdr["X-CSRF-Token"]})
        assert r.status_code == 403 and "origin" in r.text, path
        r = c.post(path, json={}, headers={"X-CSRF-Token": hdr["X-CSRF-Token"]})  # no Origin, no Referer
        assert r.status_code == 403, path


def test_roles_enforced_server_side(env):
    app, c = env
    hv = login(c, "view1", PW["view1"])
    assert c.get("/api/status").status_code == 200
    r = c.post("/api/controller/state", json={"target": "MANUAL"}, headers=hv)
    assert r.status_code == 403
    assert c.get("/api/export/audit.csv").status_code == 403
    c.post("/api/auth/logout", headers=hv)
    ho = login(c, "oper1", PW["oper1"])
    assert c.post("/api/controller/state", json={"target": "MANUAL"}, headers=ho).status_code == 200
    assert c.post("/api/users", json={"username": "x1", "password": "p" * 14, "role": "viewer"}, headers=ho).status_code == 403
    assert c.post("/api/backup", headers=ho).status_code == 403
    assert c.post("/api/bench/lease", headers=ho).status_code == 403  # no commissioning flag


def test_command_flow_and_audit(env):
    app, c = env
    ho = login(c, "oper1", PW["oper1"])
    cv = app.state.cfg.config_version
    body = {"backend": "SIMULATED", "config_version": cv, "target": "relay_A.DO1", "desired": True,
            "request_id": "req_test_0001"}
    r = c.post("/api/commands", json=body, headers=ho).json()
    assert r["outcome"] == "REJECTED"  # STOPPED
    c.post("/api/controller/state", json={"target": "MANUAL"}, headers=ho)
    r = c.post("/api/commands", json=body | {"request_id": "req_test_0002"}, headers=ho).json()
    assert r["outcome"] == "READBACK_CONFIRMED"
    assert c.post("/api/commands", json=body | {"request_id": "req_test_0003", "config_version": "old"},
                  headers=ho).status_code == 409
    assert c.post("/api/commands", json=body | {"backend": "HARDWARE"}, headers=ho).status_code == 422
    assert c.post("/api/commands", json={k: v for k, v in body.items() if k != "desired"} | {"request_id": "r4xxxxxx"},
                  headers=ho).status_code == 422  # no toggle: explicit desired state required
    hist = c.get("/api/commands/history").json()
    assert {h["outcome"] for h in hist} >= {"REJECTED", "READBACK_CONFIRMED"}
    assert all(h["actor"] == "oper1" for h in hist if h["origin"] == "OPERATOR")


def test_get_routes_do_not_change_state(env):
    app, c = env
    hdr = login(c, "oper1", PW["oper1"])
    c.post("/api/twin/control", json={"action": "pause"}, headers=hdr)
    rt = app.state.svc.runtime
    before = (rt.state_hash(), len(rt.journal))
    for path in ["/api/status", "/api/points", "/api/twin", "/api/scenarios", "/api/controller", "/api/alarms",
                 "/api/events", "/api/commands/history", "/api/trends?points=analog_A.AI1", "/api/export.csv",
                 "/api/analytics", "/api/settings", "/api/auth/session"]:
        assert c.get(path).status_code == 200, path
    assert (rt.state_hash(), len(rt.journal)) == before


def test_password_change_revokes_sessions_and_logout(env):
    app, c = env
    hdr = login(c, "oper1", PW["oper1"])
    c2 = TestClient(app, base_url=ORIGIN)  # second browser; no second lifespan
    login(c2, "oper1", PW["oper1"])
    assert c2.get("/api/status").status_code == 200
    r = c.post("/api/auth/password", json={"old_password": PW["oper1"], "new_password": "brand new password 2"},
               headers=hdr)
    assert r.status_code == 200
    assert c2.get("/api/status").status_code == 401
    assert c.get("/api/status").status_code == 401
    hdr = login(c, "oper1", "brand new password 2")
    c.post("/api/auth/logout", headers=hdr)
    assert c.get("/api/status").status_code == 401


def test_must_change_password_blocks_operations(env):
    app, c = env
    app.state.accounts.create_user("newop", "temporary password 9", Role.OPERATOR, must_change=True)
    hdr = login(c, "newop", "temporary password 9")
    assert c.get("/api/status").status_code == 200
    assert c.post("/api/controller/state", json={"target": "MANUAL"}, headers=hdr).status_code == 403


def test_idle_expiry(env):
    app, c = env
    login(c, "view1", PW["view1"])
    acc = app.state.accounts
    from datetime import timedelta
    acc.idle = timedelta(seconds=0)
    assert c.get("/api/status").status_code == 401


def test_request_size_and_host_limits(env):
    app, c = env
    hdr = login(c, "oper1", PW["oper1"])
    r = c.post("/api/io/override", content=b"{" + b" " * 70000 + b"}", headers=hdr | {"Content-Type": "application/json"})
    assert r.status_code == 413
    assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_static_ui_is_packaged_and_offline(env):
    app, c = env
    idx = c.get("/")
    assert idx.status_code == 200 and "AquaCrop" in idx.text
    assert "content-security-policy" in idx.headers
    for name in ["app.js", "app.css", "chart.js", "icon.svg"]:
        r = c.get(f"/static/{name}")
        assert r.status_code == 200, name
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", r.text), f"external URL in {name}"
    assert not re.search(r"https?://", idx.text)
    assert c.get("/static/../cli.py").status_code == 404
    assert "innerHTML" not in c.get("/static/app.js").text


def test_csv_export_escapes_and_discloses_completeness(env_dense):
    app, c = env_dense
    hdr = login(c, "admin1", PW["admin1"])
    c.post("/api/io/override", json={"point": "analog_A.AI1", "raw": 9000, "reason": "=HYPERLINK(\"x\")"}, headers=hdr)
    c.post("/api/twin/control", json={"action": "speed", "speed": 10}, headers=hdr)
    time.sleep(2.2)
    app.state.hist.wait_idle()
    r = c.get("/api/export.csv?points=analog_A.AI1")
    assert r.status_code == 200 and r.text.strip().splitlines()[-1].startswith("# COMPLETE")
    r2 = c.get("/api/export.csv?points=analog_A.AI1&limit=1")
    assert r2.headers["X-Truncated"] == "true" and "TRUNCATED" in r2.text
    a = c.get("/api/export/audit.csv").text
    import csv as _csv
    import io as _io
    cells = [x for row in _csv.reader(_io.StringIO(a)) for x in row]
    assert cells and not [x for x in cells if x[:1] in ("=", "+", "@")]
    assert _escape_csv("=1+1") == "'=1+1" and _escape_csv("-x") == "'-x" and _escape_csv("ok") == "ok"


def test_scenario_and_twin_controls(env):
    app, c = env
    hdr = login(c, "oper1", PW["oper1"])
    old = app.state.svc.runtime.run_id
    r = c.post("/api/twin/scenario", json={"name": "valve_stuck_closed"}, headers=hdr).json()
    assert r["run_id"] != old
    assert c.post("/api/twin/control", json={"action": "speed", "speed": 7}, headers=hdr).status_code == 422
    assert c.post("/api/twin/control", json={"action": "step"}, headers=hdr).status_code == 409  # not paused
    c.post("/api/twin/control", json={"action": "pause"}, headers=hdr)
    t = app.state.svc.runtime.tick
    c.post("/api/twin/control", json={"action": "step", "steps": 5}, headers=hdr)
    assert app.state.svc.runtime.tick == t + 5


def test_default_snapshot_interval_is_60_s_of_simulated_time(env):
    app, c = env
    hdr = login(c, "oper1", PW["oper1"])
    assert c.get("/api/status").json()["twin"]["history_stride_sim_s"] == 60.0
    c.post("/api/twin/control", json={"action": "speed", "speed": 60}, headers=hdr)
    assert c.get("/api/status").json()["twin"]["history_stride_sim_s"] == 60.0  # max(60 s, 60 x 0.5 s)


def test_history_cadence_bounded_and_labelled_at_speed(env_dense):
    app, c = env_dense
    hdr = login(c, "oper1", PW["oper1"])
    c.post("/api/twin/control", json={"action": "speed", "speed": 60}, headers=hdr)
    st = c.get("/api/status").json()["twin"]
    assert st["history_stride_sim_s"] == 30.0  # max(1 s, 60 x 0.5 s)
    ev = [e for e in app.state.svc.runtime.events if e["kind"] == "HISTORY_STRIDE_CHANGED"]
    assert ev and "speed 60x" in ev[-1]["payload"]["reason"]
    c.post("/api/twin/control", json={"action": "speed", "speed": 1}, headers=hdr)
    assert c.get("/api/status").json()["twin"]["history_stride_sim_s"] == 1.0


def test_trend_gap_flags_from_recorded_cadence(env_dense):
    app, c = env_dense
    hdr = login(c, "oper1", PW["oper1"])
    c.post("/api/twin/control", json={"action": "speed", "speed": 10}, headers=hdr)
    time.sleep(1.6)
    c.post("/api/twin/control", json={"action": "acq_stall"}, headers=hdr)  # no new acquisitions
    rt = app.state.svc.runtime
    app.state.hist.wait_idle()
    tr = c.get("/api/trends?points=analog_A.AI1").json()["series"]["analog_A.AI1"]
    assert len(tr) >= 3 and not any(r[8] for r in tr[1:])  # continuous cadence => no gaps
    # simulate a missing cycle by deleting one row: the next row must be flagged as a gap
    h = app.state.hist
    mid = h.query("SELECT id FROM samples WHERE point_id='analog_A.AI1' ORDER BY id")[1][0]
    h.submit(lambda con: con.execute("DELETE FROM samples WHERE id=?", (mid,)), wait=True)
    tr = c.get("/api/trends?points=analog_A.AI1").json()["series"]["analog_A.AI1"]
    assert tr[1][8] is True
