"""ADR-0017: the I/O board and I/O assignment on the simulation (installation-80, six virtual modules),
so the bring-up can be practised before the hardware arrives."""
import time

import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.config import load_config
from aquacrop.security.auth import Role
from tests.conftest import ROOT

ORIGIN = "http://127.0.0.1:8000"
PW = {"bench1": "bench password 123", "view1": "viewer password 12", "oper1": "operator password 1"}


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


def wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def point(board, pid):
    return next(p for m in board["modules"] for p in m["points"] if p["id"] == pid)


@pytest.fixture
def sim(tmp_path):
    cfg = load_config(ROOT / "configs" / "installation-80.yaml")
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    acc.create_user("oper1", PW["oper1"], Role.OPERATOR)
    acc.create_user("view1", PW["view1"], Role.VIEWER)
    with TestClient(app, base_url=ORIGIN) as c:
        yield app, c


def cmd(c, h, target, desired, n=[0]):
    n[0] += 1
    v = c.get("/api/status", headers=h).json()["config_version"]
    r = c.post("/api/commands", json={"backend": "SIMULATED", "config_version": v, "target": target, "desired": desired,
                                      "request_id": f"ui_board{n[0]:08d}", "reason": "I/O board"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_board_lists_the_six_virtual_modules(sim):
    app, c = sim
    h = login(c, "view1")
    b = c.get("/api/board", headers=h).json()
    assert b["mode"] == "SIMULATION" and b["can"] == {"command": False, "assign": False, "all_off": False}
    assert len(b["modules"]) == 6 and sum(len(m["points"]) for m in b["modules"]) == 80
    ai = point(b, "analog_A.AI1")
    assert ai["electrical"]["unit"] == "mA" and ai["model_signal"] == "tank.MAIN.level" and ai["assignment"] is None
    assert "lease" not in b                         # no bench on the simulation


def test_practise_assigning_and_verifying_on_the_simulation(sim):
    app, c = sim
    h = login(c, "oper1")
    assert c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "contactor.MP1.cmd"}, headers=h).status_code == 403
    c.post("/api/auth/logout", headers=h)
    h = login(c, "bench1")
    assert c.post("/api/io/source", json={"source": "IO_TEST"}, headers=h).status_code == 200
    # an output: ON and OFF with readback, then the observation
    assert c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "contactor.MP1.cmd"}, headers=h).status_code == 200
    assert cmd(c, h, "relay_A.DO1", True)["outcome"] == "READBACK_CONFIRMED"
    assert point(c.get("/api/board", headers=h).json(), "relay_A.DO1")["asked"]["desired"] is True
    cmd(c, h, "relay_A.DO1", False)
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DO1", "observation": "virtual LED went on and off"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED"
    # an input: injected both ways
    c.post("/api/assignments", json={"point": "digital_A.DI1", "signal": "safety.chain_ok"}, headers=h)
    c.post("/api/io/inject", json={"point": "digital_A.DI1", "raw": True}, headers=h)
    assert wait(lambda: point(c.get("/api/board", headers=h).json(), "digital_A.DI1")["changes"])
    c.post("/api/io/inject", json={"point": "digital_A.DI1", "raw": False}, headers=h)
    assert wait(lambda: len(point(c.get("/api/board", headers=h).json(), "digital_A.DI1")["changes"]) == 2)
    r = c.post("/api/assignments/verify", json={"point": "digital_A.DI1", "observation": "injected 1 then 0"}, headers=h)
    assert r.status_code == 200, r.text
    # an analog input: an injected 8 mA against the reference
    c.post("/api/assignments", json={"point": "analog_B.AI3", "signal": "pressure.S1"}, headers=h)
    c.post("/api/io/inject", json={"point": "analog_B.AI3", "raw": 8000}, headers=h)
    assert wait(lambda: (point(c.get("/api/board", headers=h).json(), "analog_B.AI3")["electrical"] or {}).get("value") == 8.0)
    r = c.post("/api/assignments/verify", json={"point": "analog_B.AI3", "observation": "injected 8 mA",
                                                "reference": 8.0, "tolerance": 0.05}, headers=h)
    assert r.status_code == 200, r.text
    b = c.get("/api/board", headers=h).json()
    assert b["assigned"] == {"PROPOSED": 0, "VERIFIED": 3}
    doc = c.get("/api/assignments/export", params={"fmt": "json"}, headers=h).json()
    assert doc["counts"] == {"VERIFIED": 3, "PROPOSED": 0, "UNASSIGNED": 77} and doc["profile"] == "SIMULATED"


def test_an_input_change_right_after_the_switch_to_io_test_is_seen(sim):
    """Regression (CI, 0.4.0): the change was missed when the injection came before the edge watcher's next
    tick had seen the I/O test baseline. Reads and injections now observe, so no waiting is needed."""
    app, c = sim
    h = login(c, "bench1")
    assert c.post("/api/io/source", json={"source": "IO_TEST"}, headers=h).status_code == 200
    c.post("/api/io/inject", json={"point": "digital_B.DI4", "raw": True}, headers=h)
    c.post("/api/io/inject", json={"point": "digital_B.DI4", "raw": False}, headers=h)
    changes = point(c.get("/api/board", headers=h).json(), "digital_B.DI4")["changes"]
    assert [v for _, v in changes] == [True, False]


def test_on_and_off_in_the_same_clock_tick_still_verify(sim, monkeypatch):
    """Regression (CI, Windows, Python 3.12): its clock can give the ON and the OFF the same time stamp. The
    order of the commands is kept by their insertion order, so the OFF still counts as after the ON."""
    import aquacrop.historian.writer as writer
    from aquacrop.historian.schema import utc_iso
    app, c = sim
    h = login(c, "bench1")
    assert c.post("/api/io/source", json={"source": "IO_TEST"}, headers=h).status_code == 200
    assert c.post("/api/assignments", json={"point": "relay_B.DO2", "signal": "contactor.MP2.cmd"}, headers=h).status_code == 200
    tick = utc_iso()
    monkeypatch.setattr(writer, "utc_iso", lambda dt=None: tick)
    cmd(c, h, "relay_B.DO2", True)
    cmd(c, h, "relay_B.DO2", False)
    assert point(c.get("/api/board", headers=h).json(), "relay_B.DO2")["asked"]["desired"] is False
    r = c.post("/api/assignments/verify", json={"point": "relay_B.DO2", "observation": "virtual LED on and off"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED", r.text
