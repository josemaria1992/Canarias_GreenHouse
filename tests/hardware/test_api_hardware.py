"""The web app serving a HARDWARE_READ_ONLY profile over the in-memory serial bus (not hardware)."""
import time

import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.control.bench import CONFIRMATION_PHRASE
from aquacrop.security.auth import Role

ORIGIN = "http://127.0.0.1:8000"
PW = {"admin1": "correct horse battery", "bench1": "bench password 123", "oper1": "operator password 1"}


@pytest.fixture
def hw(tmp_path, fake_bus):
    cfg, bus, relay, ai = fake_bus
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("admin1", PW["admin1"], Role.ADMIN)                       # admin, no commissioning
    acc.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    acc.create_user("oper1", PW["oper1"], Role.OPERATOR)
    with TestClient(app, base_url=ORIGIN) as c:
        deadline = time.monotonic() + 5
        while app.state.svc.workers["rs485_a"].polls < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        yield app, c, bus, relay, ai
    assert bus.write_frames() == [] or relay.writes_applied > 0   # writes only where a test armed a lease


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


def arm_body(**kw):
    b = {"confirmation": CONFIRMATION_PHRASE, "setup_description": "isolated bench, lamp on DO1 only",
         "allowlist": ["relay_A.DO1"], "duration_s": 300}
    return b | kw


def test_read_only_status_points_and_banner(hw):
    app, c, bus, relay, ai = hw
    login(c, "oper1")
    st = c.get("/api/status").json()
    assert st["banner"] == "REAL — READ ONLY" and st["profile"] == "HARDWARE_READ_ONLY"
    assert st["bench_lease"] is None and st["relay_modes"]["relay_A"] == ["NORMAL"] * 8
    assert st["physical_writes"].startswith("NONE")
    pts = c.get("/api/points").json()
    assert pts["source"] == "REAL"
    s = {p["id"]: p["sample"] for g in pts["devices"] for p in g["points"]}
    assert s["relay_A.DI1"]["origin"] == "REAL" and s["analog_A.AI2"]["quality"] == "UNSCALED"
    assert bus.write_frames() == []


@pytest.mark.parametrize("path", ["/api/commands", "/api/io/inject", "/api/twin/control", "/api/controller/state"])
def test_simulation_routes_are_unavailable(hw, path):
    app, c, *_ = hw
    h = login(c, "oper1")
    assert c.post(path, json={}, headers=h).status_code == 409
    assert c.get("/api/twin").status_code == 409
    for shared in ("/api/commands/history", "/api/alarms", "/api/events", "/api/trends?points=relay_A.DI1", "/api/settings", "/api/bench"):
        assert c.get(shared).status_code == 200, shared


def test_admin_without_commissioning_cannot_arm(hw):
    app, c, bus, *_ = hw
    h = login(c, "admin1")
    assert c.post("/api/bench/lease", json=arm_body(), headers=h).status_code == 403
    assert bus.write_frames() == []


def test_bench_flow_arm_write_revoke(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    assert c.post("/api/bench/lease", json=arm_body(confirmation="ok"), headers=h).status_code == 409
    r = c.post("/api/bench/lease", json=arm_body(), headers=h)
    assert r.status_code == 200, r.text
    lease = r.json()
    assert c.get("/api/status").json()["banner"].startswith("REAL — BENCH WRITES ARMED")
    cmd = {"backend": "REAL_BENCH", "config_version": "test-1", "lease_id": lease["lease_id"],
           "target": "relay_A.DO1", "desired": True, "request_id": "req-00000001"}
    out = c.post("/api/bench/command", json=cmd, headers=h).json()
    assert out["outcome"] == "READBACK_CONFIRMED" and relay.coils[0] is True
    out = c.post("/api/bench/command", json=cmd | {"target": "relay_A.DO2", "request_id": "req-00000002"},
                 headers=h).json()
    assert out["outcome"] == "REJECTED" and "allowlist" in out["detail"]
    assert c.post("/api/bench/lease/revoke", json={}, headers=h).json() == {"revoked": True}
    out = c.post("/api/bench/command", json=cmd | {"request_id": "req-00000003", "desired": False},
                 headers=h).json()
    assert out["outcome"] == "REJECTED"
    assert len(bus.write_frames()) == 1 and relay.coils[0] is True   # revocation sent nothing
    hist = app.state.hist
    hist.wait_idle()
    kinds = [r[0] for r in hist.query("SELECT kind FROM events WHERE kind LIKE 'BENCH%' ORDER BY id")]
    assert kinds[:2] == ["BENCH_ARM_REFUSED", "BENCH_LEASE_ARMED"] and "BENCH_LEASE_ENDED" in kinds
    rows = hist.query("SELECT outcome FROM commands ORDER BY rowid")
    assert [r[0] for r in rows].count("READBACK_CONFIRMED") == 1


def test_lease_is_bound_to_session(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    lease = c.post("/api/bench/lease", json=arm_body(), headers=h).json()
    c2 = TestClient(app, base_url=ORIGIN)
    h2 = login(c2, "bench1")                         # same user, new browser session
    cmd = {"backend": "REAL_BENCH", "config_version": "test-1", "lease_id": lease["lease_id"],
           "target": "relay_A.DO1", "desired": True, "request_id": "req-00000010"}
    out = c2.post("/api/bench/command", json=cmd, headers=h2).json()
    assert out["outcome"] == "REJECTED" and "another session" in out["detail"]
    c.post("/api/auth/logout", json={}, headers=h)   # logout ends the lease
    assert app.state.svc.gate.active() is None
    assert bus.write_frames() == []


def test_database_of_another_profile_is_refused(tmp_path, fake_bus, cfg48):
    cfg = fake_bus[0]
    app = create_app(cfg48, state_dir=tmp_path)
    app.state.hist.close()
    with pytest.raises(ValueError, match="belongs to profile"):
        create_app(cfg.model_copy(update={"historian": cfg48.historian}), state_dir=tmp_path)


def test_plant_view_is_read_only_and_unbound_on_a_hardware_profile(hw):
    """The installation diagram on a real profile: every logical signal UNBOUND until a physical mapping
    is approved, no process summary, and the process/recipe/stock mutations stay simulation-only."""
    app, c, bus, *_ = hw
    h = login(c, "admin1")
    p = c.get("/api/plant").json()
    assert p["read_only"] is True and p["processes"] is None and "UNBOUND" in p["mapping"]
    assert p["signals"] and {s["binding"] for s in p["signals"].values()} == {"UNBOUND"}
    assert all(s["point"] is None for s in p["signals"].values())
    for path in ("/api/processes/IRRIGATION/state", "/api/recipes/rollback", "/api/stocks/A/acknowledge"):
        assert c.post(path, json={"target": "AUTO", "reason": "x"}, headers=h).status_code == 409
    assert bus.write_frames() == []


def test_plant_view_leaves_a_not_configured_drive_adapter_unbound(tmp_path, fake_bus):
    """A hardware profile may declare the shared drive's adapter as NOT_CONFIGURED (ADR-0013); the
    diagram still shows its run signal UNBOUND, not as a bound adapter."""
    from aquacrop.config.schema import InstallationConfig
    data = fake_bus[0].model_dump(mode="json") | {"drive_adapters": [
        {"adapter_id": "MAIN", "kind": "NOT_CONFIGURED", "signals": ["drive.MAIN.run"]}]}
    app = create_app(InstallationConfig.model_validate(data), state_dir=tmp_path, bind_host="127.0.0.1")
    app.state.accounts.create_user("admin1", PW["admin1"], Role.ADMIN)
    with TestClient(app, base_url=ORIGIN) as c:
        login(c, "admin1")
        signals = c.get("/api/plant").json()["signals"]
    assert signals["drive.MAIN.run"]["binding"] == "UNBOUND" and "adapter" not in signals["drive.MAIN.run"]
    assert {s["binding"] for s in signals.values()} == {"UNBOUND"}
