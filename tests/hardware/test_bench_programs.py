"""0.5.0 physical stage on the in-memory bus (not hardware): programs on allowlisted IO 8CH outputs, only inside an
armed bench lease, only for a version that passed in the twin, starting in RECOVERY. The frames on the fake wire are
the evidence; nothing here is an owner-observed physical test."""
import itertools
import json
import time
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from aquacrop.config.schema import InstallationConfig
from aquacrop.control.bench import CONFIRMATION_PHRASE
from aquacrop.security.auth import Role
from tests.conftest import ROOT
from tests.fakes import protocol_aquafake as pf

ORIGIN = "http://127.0.0.1:8000"
PW = {"admin1": "correct horse battery", "bench1": "bench password 123", "oper1": "operator password 1"}
_n = itertools.count()


def config(port, **bench):
    d = yaml.safe_load((ROOT / "tests" / "fixtures" / "hw-fake.yaml").read_text(encoding="utf-8"))
    d["buses"][0]["port"] = port
    d["devices"].append({"device_uid": "digital_A", "bus_id": "rs485_a", "address": 5,
                         "model": "WAVESHARE_MODBUS_RTU_IO_8CH", "sku": "26244"})
    d["safe_state"] = {"outputs_off_on": ["START", "STOP", "LEASE_END", "MODULE_RETURN"]}
    if bench:
        d["bench_tests"] = bench
    return InstallationConfig.model_validate(d)


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": PW[user]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200, r.text
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf_token"]}


def wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.fixture
def bench(tmp_path, monkeypatch):
    import serial
    if "tests.fakes" not in serial.protocol_handler_packages:
        serial.protocol_handler_packages.append("tests.fakes")
    monkeypatch.setenv("AQUACROP_LOCK_DIR", str(tmp_path / "locks"))
    name = f"bp{next(_n)}"
    bus = pf.bus(name)
    bus.add("relay", 1)
    bus.add("ai", 2)
    dio = bus.add("digital_io", 5)
    cfg = config(f"aquafake://{name}", enabled=True, allowlist=["digital_A.DO1"], approved_by="vicente",
                 loads="LED with 2.2 kOhm resistor from 24 V to DO1 (test)")
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("admin1", PW["admin1"], Role.ADMIN, commissioning=True)
    acc.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    acc.create_user("oper1", PW["oper1"], Role.OPERATOR)
    with TestClient(app, base_url=ORIGIN) as c:
        wait(lambda: app.state.svc.workers["rs485_a"].polls >= 2)
        yield app, c, bus, dio
    pf.BUSES.pop(name, None)


def setup_led(app, c, h):
    """An LED on digital_A.DO1 whose channel is VERIFIED, and a program version with a passing twin result."""
    c.post("/api/equipment/types/seed", headers=h)
    for t in c.get("/api/equipment", headers=h).json()["types"]:
        c.post("/api/equipment/types/activate", json={"type_id": t["type_id"], "version": t["version"]}, headers=h)
    assert c.post("/api/equipment/items", json={"item_id": "led1", "name": "LED 1", "type_id": "test_lamp",
                                                "icon": "lamp", "body": {}}, headers=h).status_code == 200
    c.post("/api/equipment/bind", json={"item_id": "led1", "role": "on", "point": "digital_A.DO1"}, headers=h)
    eq = app.state.equipment
    eq._bind_write("admin1", "led1", "on", "digital_A.DO1", "VERIFIED", None,
                   {"check": "TEST_FIXTURE", "observation": "test double"})
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "led1", "duration_s": 3}}
    c.post("/api/programs/draft", json={"program_id": "blink", "name": "Blink", "body": body}, headers=h)
    v = c.post("/api/programs/validate", json={"program_id": "blink", "version": 1}, headers=h).json()
    assert v["status"] == "VALIDATED" and v["physical_needs"] == []
    return v


def on_frames(bus):
    return [f for f in bus.write_frames() if f[1] == 0x05 and f[4:6] == b"\xff\x00"]


def test_read_only_profile_has_no_program_engine(tmp_path, fake_bus):
    cfg, bus, relay, ai = fake_bus
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    app.state.accounts.create_user("admin1", PW["admin1"], Role.ADMIN, commissioning=True)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        v = c.get("/api/operation", headers=h).json()
        assert v["available"] is False and "not enabled" in v["reason"]
        assert c.post("/api/operation/mode", json={"target": "AUTO"}, headers=h).status_code == 409
    assert bus.write_frames() == []


def test_bench_tests_config_refuses_relays_and_needs_lease_end(tmp_path):
    with pytest.raises(Exception, match="IO 8CH"):
        config("aquafake://x", enabled=True, allowlist=["relay_A.DO1"], approved_by="v", loads="LED")
    d = yaml.safe_load((ROOT / "tests" / "fixtures" / "hw-fake.yaml").read_text(encoding="utf-8"))
    d["devices"].append({"device_uid": "digital_A", "bus_id": "rs485_a", "address": 5,
                         "model": "WAVESHARE_MODBUS_RTU_IO_8CH", "sku": "26244"})
    d["bench_tests"] = {"enabled": True, "allowlist": ["digital_A.DO1"], "approved_by": "v", "loads": "LED"}
    with pytest.raises(Exception, match="LEASE_END"):
        InstallationConfig.model_validate(d)


def test_a_program_switches_an_allowlisted_output_only_inside_a_lease(bench):
    app, c, bus, dio = bench
    h = login(c, "admin1")
    setup_led(app, c, h)
    rid = c.get("/api/operation", headers=h).json()["runtime_id"]
    H = h | {"X-Runtime-Id": rid}
    assert on_frames(bus) == []                                            # startup sent no ON
    r = c.post("/api/operation/activate", json={"program_id": "blink", "version": 1}, headers=H)
    assert r.status_code == 409 and "twin" in r.text                       # no twin pass yet
    app.state.programs.record_simulation("blink", 1, {"digest": app.state.programs.get("blink", 1)["digest"],
                                                      "passed": True, "accepted_by": "admin1",
                                                      "accepted_utc": datetime.now(timezone.utc).isoformat(),
                                                      "context": app.state.proof_context("blink", 1)})
    assert c.post("/api/operation/activate", json={"program_id": "blink", "version": 1}, headers=H).status_code == 200
    assert c.post("/api/operation/run-now", json={"program_id": "blink"}, headers=H).status_code == 409   # RECOVERY
    wait(lambda: all(x["ok"] for x in c.get("/api/operation", headers=h).json()["checklist"]))
    r = c.post("/api/operation/recovery/approve", json={"target": "MANUAL"}, headers=H)
    assert r.status_code == 200, r.text
    r = c.post("/api/operation/run-now", json={"program_id": "blink"}, headers=H)
    assert r.status_code == 409 and "no armed bench test session" in r.text
    assert on_frames(bus) == []
    lease = c.post("/api/bench/lease", json={"confirmation": CONFIRMATION_PHRASE, "setup_description": "LED board on DO1 only",
                                              "allowlist": ["digital_A.DO1"], "duration_s": 300}, headers=h)
    assert lease.status_code == 200, lease.text
    assert c.post("/api/operation/run-now", json={"program_id": "blink"}, headers=H).status_code == 200
    assert wait(lambda: dio.coils[0])
    assert wait(lambda: not dio.coils[0] and not c.get("/api/operation", headers=h).json()["ops"])
    assert len(on_frames(bus)) == 1 and all(f[0] == 5 for f in on_frames(bus))   # unit 5, coil 1 only
    fin = c.get("/api/operation", headers=h).json()["finished"][-1]
    assert fin["result"] == "DONE"


def test_lease_end_stops_a_running_program_and_the_wrong_runtime_is_refused(bench):
    app, c, bus, dio = bench
    h = login(c, "admin1")
    setup_led(app, c, h)
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "led1", "duration_s": 600}}
    c.post("/api/programs/draft", json={"program_id": "long", "name": "Long", "body": body}, headers=h)
    c.post("/api/programs/validate", json={"program_id": "long", "version": 1}, headers=h)
    app.state.programs.record_simulation("long", 1, {"digest": app.state.programs.get("long", 1)["digest"], "passed": True,
                                                     "accepted_by": "admin1", "accepted_utc": "x",
                                                     "context": app.state.proof_context("long", 1)})
    rid = c.get("/api/operation", headers=h).json()["runtime_id"]
    assert c.post("/api/operation/mode", json={"target": "AUTO"}, headers=h | {"X-Runtime-Id": "TWIN:x"}).status_code == 409
    H = h | {"X-Runtime-Id": rid}
    c.post("/api/operation/activate", json={"program_id": "long", "version": 1}, headers=H)
    wait(lambda: all(x["ok"] for x in c.get("/api/operation", headers=h).json()["checklist"]))
    c.post("/api/operation/recovery/approve", json={"target": "MANUAL"}, headers=H)
    c.post("/api/bench/lease", json={"confirmation": CONFIRMATION_PHRASE, "setup_description": "LED board on DO1 only",
                                     "allowlist": ["digital_A.DO1"], "duration_s": 300}, headers=h)
    c.post("/api/operation/run-now", json={"program_id": "long"}, headers=H)
    assert wait(lambda: dio.coils[0])
    assert c.post("/api/bench/lease/revoke", json={}, headers=h).status_code == 200
    assert wait(lambda: not dio.coils[0])
    assert wait(lambda: not c.get("/api/operation", headers=h).json()["ops"])
    fin = c.get("/api/operation", headers=h).json()["finished"][-1]
    assert "session ended" in fin["reason"]
    n = len(on_frames(bus))
    time.sleep(1.0)
    assert len(on_frames(bus)) == n                                        # nothing restarts by itself


def test_operators_without_commissioning_cannot_activate(bench):
    app, c, bus, dio = bench
    h = login(c, "admin1")
    setup_led(app, c, h)
    c.post("/api/auth/logout", headers=h)
    h = login(c, "oper1")
    rid = c.get("/api/operation", headers=h).json()["runtime_id"]
    assert c.post("/api/operation/activate", json={"program_id": "blink", "version": 1},
                  headers=h | {"X-Runtime-Id": rid}).status_code == 403
