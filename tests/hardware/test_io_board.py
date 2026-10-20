"""ADR-0017: the I/O board and the I/O assignment records on a HARDWARE_READ_ONLY profile, over the
in-memory RS485 bus (not hardware). Assignments are records: they never bind a signal or change control."""
import time

import yaml

from tests.hardware.test_api_hardware import arm_body, hw, login  # noqa: F401  (hw is a fixture)


def wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def point(board, pid):
    return next(p for m in board["modules"] for p in m["points"] if p["id"] == pid)


def bench(c, h, lease, target, desired):
    from aquacrop.domain.model import new_id
    st = c.get("/api/status", headers=h).json()
    r = c.post("/api/bench/command", json={"backend": "REAL_BENCH", "config_version": st["config_version"],
                                           "lease_id": lease["lease_id"], "target": target, "desired": desired,
                                           "request_id": new_id("t"), "reason": "board test"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_board_shows_modules_asked_and_reported_and_milliamps(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    ai.modes[0], ai.ai[0] = 3, 12000                        # channel 1 in 4-20 mA mode, 12000 µA
    relay.di[2] = True
    assert wait(lambda: point(c.get("/api/board", headers=h).json(), "analog_A.AI1")["electrical"] is not None
                and point(c.get("/api/board", headers=h).json(), "analog_A.AI1")["electrical"]["value"] == 12.0)
    b = c.get("/api/board", headers=h).json()
    assert b["mode"] == "BENCH" and [m["device_uid"] for m in b["modules"]] == ["relay_A", "analog_A"]
    assert b["can"] == {"command": True, "assign": True, "all_off": True}
    assert point(b, "analog_A.AI1")["electrical"] == {"value": 12.0, "unit": "mA"}
    assert point(b, "relay_A.DI3")["sample"]["value"] is True
    assert point(b, "relay_A.DO1")["asked"] is None and b["lease"] is None
    assert "rs485_a" in b["poll_s"] and b["workers"]["rs485_a"]["state"] == "RUNNING"
    lease = c.post("/api/bench/lease", json=arm_body(), headers=h).json()
    bench(c, h, lease, "relay_A.DO1", True)
    b = c.get("/api/board", headers=h).json()
    assert point(b, "relay_A.DO1")["asked"]["desired"] is True
    assert point(b, "relay_A.DO1")["asked"]["outcome"] == "READBACK_CONFIRMED"
    assert wait(lambda: point(c.get("/api/board", headers=h).json(), "relay_A.DO1")["sample"]["value"] is True)


def test_assigning_needs_commissioning_and_matching_kinds(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "oper1")
    assert c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "valve.S1.cmd"}, headers=h).status_code == 403
    c.post("/api/auth/logout", headers=h)
    h = login(c, "bench1")
    r = c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "tank.MAIN.level"}, headers=h)
    assert r.status_code == 409 and "analog signal" in r.json()["detail"]
    r = c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "nonsense.cmd"}, headers=h)
    assert r.status_code == 409 and "not a signal" in r.json()["detail"]
    r = c.post("/api/assignments", json={"point": "relay_A.DO9", "signal": "valve.S1.cmd"}, headers=h)
    assert r.status_code in (409, 422)
    r = c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "valve.S1.cmd", "note": "lamp L1"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "PROPOSED"
    r = c.post("/api/assignments", json={"point": "relay_A.DO2", "signal": "valve.S1.cmd"}, headers=h)
    assert r.status_code == 409 and "already on relay_A.DO1" in r.json()["detail"]
    opts = c.get("/api/assignments", headers=h).json()["signals"]
    assert "valve.S1.cmd" in opts["DO"] and "tank.MAIN.level" in opts["AI"] and "safety.chain_ok" in opts["DI"]


def test_an_output_is_verified_after_on_and_off_with_readback_and_an_observation(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    c.post("/api/assignments", json={"point": "relay_A.DO1", "signal": "valve.S1.cmd"}, headers=h)
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DO1", "observation": "lamp L1 lit"}, headers=h)
    assert r.status_code == 409 and "ON and then OFF" in r.json()["detail"]
    lease = c.post("/api/bench/lease", json=arm_body(), headers=h).json()
    bench(c, h, lease, "relay_A.DO1", True)
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DO1", "observation": "lamp L1 lit"}, headers=h)
    assert r.status_code == 409                                  # ON alone is not enough
    bench(c, h, lease, "relay_A.DO1", False)
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DO1", "observation": "x"}, headers=h)
    assert r.status_code == 409 and "describe what you saw" in r.json()["detail"]
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DO1", "observation": "lamp L1 lit, then off"}, headers=h)
    assert r.status_code == 200, r.text
    ev = r.json()["evidence"]
    assert r.json()["status"] == "VERIFIED" and ev["check"] == "DO_ON_OFF" and ev["on"] and ev["off"]
    assert ev["observation"] == "lamp L1 lit, then off" and "observation" in ev["proof"]
    # a record only: the configuration still binds nothing, and the profile is unchanged
    assert app.state.cfg.physical_mapping == [] and c.get("/api/status", headers=h).json()["profile"] == "HARDWARE_READ_ONLY"
    plant = c.get("/api/plant", headers=h).json()
    assert plant["signals"]["valve.S1.cmd"]["binding"] == "UNBOUND"


def test_an_input_is_verified_after_it_changes_both_ways(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    c.post("/api/assignments", json={"point": "relay_A.DI4", "signal": "tank.SUMP.low"}, headers=h)
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DI4", "observation": "float lifted by hand"}, headers=h)
    assert r.status_code == 409 and "both ways" in r.json()["detail"]
    relay.di[3] = True
    assert wait(lambda: point(c.get("/api/board", headers=h).json(), "relay_A.DI4")["changes"])
    relay.di[3] = False
    assert wait(lambda: len(point(c.get("/api/board", headers=h).json(), "relay_A.DI4")["changes"]) == 2)
    r = c.post("/api/assignments/verify", json={"point": "relay_A.DI4", "observation": "float lifted by hand"}, headers=h)
    assert r.status_code == 200 and r.json()["evidence"]["check"] == "DI_BOTH_WAYS"


def test_an_analog_input_is_verified_against_the_applied_reference(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    c.post("/api/assignments", json={"point": "analog_A.AI1", "signal": "tank.MAIN.level"}, headers=h)
    ai.modes[0], ai.ai[0] = 3, 12030
    assert wait(lambda: (point(c.get("/api/board", headers=h).json(), "analog_A.AI1")["electrical"] or {}).get("value") == 12.03)
    body = {"point": "analog_A.AI1", "observation": "calibrator 12.00 mA on AI1"}
    r = c.post("/api/assignments/verify", json=body, headers=h)
    assert r.status_code == 409 and "reference" in r.json()["detail"]
    r = c.post("/api/assignments/verify", json=body | {"reference": 4.0, "tolerance": 0.1}, headers=h)
    assert r.status_code == 409 and "reads 12.030 mA" in r.json()["detail"]
    r = c.post("/api/assignments/verify", json=body | {"reference": 12.0, "tolerance": 0.1}, headers=h)
    assert r.status_code == 200
    ev = r.json()["evidence"]
    assert ev["check"] == "AI_REFERENCE" and ev["reading"] == 12.03 and ev["unit"] == "mA" and ev["raw"] == 12030


def test_changing_a_signal_starts_again_and_the_export_lists_every_channel(hw):
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    c.post("/api/assignments", json={"point": "relay_A.DI4", "signal": "tank.SUMP.low"}, headers=h)
    c.post("/api/assignments", json={"point": "relay_A.DI4", "signal": "tank.SUMP.high"}, headers=h)
    c.post("/api/assignments", json={"point": "relay_A.DI5", "signal": "tank.SUMP.low"}, headers=h)   # free again
    r = c.post("/api/assignments", json={"point": "relay_A.DI5", "signal": None}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "CLEARED"
    cur = c.get("/api/assignments", headers=h).json()["assignments"]
    assert set(cur) == {"relay_A.DI4"} and cur["relay_A.DI4"]["signal"] == "tank.SUMP.high"
    hist = c.get("/api/assignments/history", params={"point": "relay_A.DI4"}, headers=h).json()
    assert [x["signal"] for x in hist] == ["tank.SUMP.low", "tank.SUMP.high"]
    r = c.get("/api/assignments/export", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    doc = yaml.safe_load(r.text)
    assert doc["kind"] == "AQUACROP_IO_ASSIGNMENT_PROPOSAL" and len(doc["points"]) == 24   # relay 8 DI + 8 DO, analog 8 AI
    assert doc["counts"] == {"VERIFIED": 0, "PROPOSED": 1, "UNASSIGNED": 23} and "not a mapping" in doc["note"]
    j = c.get("/api/assignments/export", params={"fmt": "json"}, headers=h).json()
    di4 = next(x for x in j["points"] if x["point"] == "relay_A.DI4")
    assert (di4["signal"], di4["status"], di4["by"], di4["address"]) == ("tank.SUMP.high", "PROPOSED", "bench1", 1)


def test_guided_check_results_carry_what_the_server_saw(hw):
    from datetime import datetime, timedelta, timezone
    app, c, bus, relay, ai = hw
    h = login(c, "bench1")
    start = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    step = {"run_id": "chk_test1", "started_utc": start}
    r = c.post("/api/iochecks", json=step | {"point": "relay_A.DO2", "result": "PASS"}, headers=h)
    assert r.status_code == 409 and "not confirmed ON" in r.json()["detail"]
    lease = c.post("/api/bench/lease", json=arm_body(allowlist=["relay_A.DO2"]), headers=h).json()
    bench(c, h, lease, "relay_A.DO2", True)
    r = c.post("/api/iochecks", json=step | {"point": "relay_A.DO2", "result": "PASS", "note": "LED 2 lit"}, headers=h)
    assert r.status_code == 200 and r.json()["evidence"]["check"] == "DO_ON"
    bench(c, h, lease, "relay_A.DO2", False)
    assert c.post("/api/iochecks", json=step | {"point": "relay_A.DO3", "result": "FAIL", "note": "no LED"}, headers=h).status_code == 200
    r = c.post("/api/iochecks", json=step | {"point": "relay_A.DI1", "result": "PASS"}, headers=h)
    assert r.status_code == 409 and "no change" in r.json()["detail"]
    relay.di[0] = True
    assert wait(lambda: point(c.get("/api/board", headers=h).json(), "relay_A.DI1")["changes"])
    assert c.post("/api/iochecks", json=step | {"point": "relay_A.DI1", "result": "PASS"}, headers=h).status_code == 200
    ai.modes[1], ai.ai[1] = 3, 4010
    assert wait(lambda: (point(c.get("/api/board", headers=h).json(), "analog_A.AI2")["electrical"] or {}).get("value") == 4.01)
    r = c.post("/api/iochecks", json=step | {"point": "analog_A.AI2", "result": "PASS", "reference": 20, "tolerance": 0.1}, headers=h)
    assert r.status_code == 409 and "outside 20" in r.json()["detail"]
    r = c.post("/api/iochecks", json=step | {"point": "analog_A.AI2", "result": "PASS", "reference": 4, "tolerance": 0.05}, headers=h)
    assert r.status_code == 200 and r.json()["evidence"]["reading"] == 4.01
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    assert c.post("/api/iochecks", json={"run_id": "x", "started_utc": old, "point": "relay_A.DO4", "result": "FAIL"}, headers=h).status_code == 409
    b = c.get("/api/board", headers=h).json()
    assert b["checked"] == {"PASS": 3, "FAIL": 1, "SKIPPED": 0}
    assert point(b, "relay_A.DO3")["check"]["note"] == "no LED"
    doc = yaml.safe_load(c.get("/api/assignments/export", headers=h).text)
    assert next(x for x in doc["points"] if x["point"] == "relay_A.DO2")["check"]["result"] == "PASS"
    c.post("/api/auth/logout", headers=h)
    h2 = login(c, "oper1")
    assert c.post("/api/iochecks", json=step | {"point": "relay_A.DO3", "result": "FAIL"}, headers=h2).status_code == 403
