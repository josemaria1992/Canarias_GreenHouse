"""0.7.0 FB-07: applying a twin export is journaled before each change. A failure at any step is undone and
reported truthfully; a kill leaves a record that the next start settles against what the real system has; two
approvals of one package never both apply. Fake bus only: nothing physical."""
import threading

import pytest
from fastapi.testclient import TestClient

from aquacrop.api.app import create_app
from tests.hardware.test_twin_service import ORIGIN, _twin_h, live_and_twin, login  # noqa: F401  (fixture)


class Crash(BaseException):
    """Stands for the process being killed: no except clause of the application catches it."""


def _export(twin):
    H = _twin_h(twin, "bench1")
    ch = twin.get("/api/twin/changes").json()
    pump_t = next(t for t in ch["types"] if t["type_id"] == "pump")
    assert twin.post("/api/twin/equipment/save", json={"item_id": "p2", "name": "Pump 2", "type_id": "pump",
                                                       "type_version": pump_t["version"], "icon": "pump", "body": {}},
                     headers=H).status_code == 200
    assert twin.post("/api/twin/equipment/bind", json={"item_id": "p2", "role": "start", "point": "relay_A.DO2"},
                     headers=H).status_code == 200
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "p2", "duration_s": 10}}
    assert twin.post("/api/twin/programs/draft", json={"program_id": "p2-test", "name": "Pump 2 test", "body": body},
                     headers=H).status_code == 200
    assert twin.post("/api/twin/programs/activate", json={"program_id": "p2-test", "version": 1},
                     headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    assert twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H).status_code == 200
    assert twin.post("/api/twin/run-now", json={"program_id": "p2-test"}, headers=H).status_code == 200
    twin.post("/api/twin/control", json={"action": "step", "steps": 40}, headers=H)
    r = twin.post("/api/twin/export", json={"note": "second pump"}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["digest"]


def _live_state(app):
    items = {i["item_id"]: i for i in app.state.equipment.items()}
    p2 = items.get("p2")
    point = app.state.equipment.bindings("p2").get("start") if p2 else None
    progs = [v for v in app.state.programs.versions() if v["program_id"] == "p2-test"]
    return {"p2": p2 and p2["status"], "point": point and point["point"],
            "programs": [(v["version"], v["status"]) for v in progs]}


STEPS = [("intent", 0), ("before_done", 0), ("intent", 1), ("before_done", 1), ("intent", 2), ("before_done", 2)]


@pytest.mark.parametrize("where,n", STEPS)
def test_fb07_a_failure_at_each_step_is_undone_and_reported(live_and_twin, where, n):
    app, live, twin_app, twin, bus, hl = live_and_twin
    digest = _export(twin)
    before = _live_state(app)
    seen = []

    def fail(w, step):
        seen.append(w)
        if w == where and sum(1 for x in seen if x == where) == n + 1:
            raise OSError(f"injected failure at {w} of step {n + 1}")
    app.state.changes_fail_point = fail
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": digest, "note": "ok"}, headers=hb)
    assert r.status_code == 409 and r.json()["detail"].startswith("nothing was changed"), r.text
    after = _live_state(app)
    # the item may exist as retired and the program as a retired version: history stays, nothing is in effect
    assert after["p2"] in (None, "RETIRED") and after["point"] in (None, before["point"])
    assert all(st == "RETIRED" for _, st in after["programs"])
    v = live.get("/api/twin-changes", headers=hl).json()
    assert v["applied"][0]["state"] == "FAILED_NOTHING_CHANGED" and v["undo_digest"] is None
    app.state.changes_fail_point = None
    # the same package can be applied after a failure that changed nothing, if the base still matches
    r = live.post("/api/twin-changes/approve", json={"digest": digest, "note": "again"}, headers=hb)
    assert r.status_code in (200, 409)
    if r.status_code == 409:
        assert "changed on the real system" in r.json()["detail"] or "now exists" in r.json()["detail"]


@pytest.mark.parametrize("where,n", STEPS)
def test_fb07_a_kill_mid_apply_is_settled_at_the_next_start(live_and_twin, tmp_path, where, n):
    app, live, twin_app, twin, bus, hl = live_and_twin
    digest = _export(twin)
    seen = []

    def kill(w, step):
        seen.append(w)
        if w == where and sum(1 for x in seen if x == where) == n + 1:
            raise Crash()
    app.state.changes_fail_point = kill
    doomed = TestClient(app, base_url=ORIGIN)               # its own portal: the "killed" request ends only it
    hb = login(doomed, "bench1", ORIGIN)
    with pytest.raises(BaseException):
        doomed.post("/api/twin-changes/approve", json={"digest": digest, "note": "ok"}, headers=hb)
    app.state.changes_fail_point = None
    state = _live_state(app)
    # restart: a second app on the same state directory recovers the journal (no lifespan needed for that)
    app2 = create_app(app.state.cfg, state_dir=tmp_path / "live", bind_host="127.0.0.1")
    c2 = TestClient(app2, base_url=ORIGIN)
    h2 = login(c2, "bench1", ORIGIN)
    v = c2.get("/api/twin-changes", headers=h2).json()
    rec = v["applied"][0]
    assert rec["state"] == "INTERRUPTED" and v["attention"][0]["digest"] == digest
    done = rec["interrupted"]["done"]
    # what the record says was done is exactly what the real system has
    assert ("item_created p2" in done) == (state["p2"] == "ACTIVE")
    assert ("bound p2" in done) == (state["point"] == "relay_A.DO2")
    assert ("program_added p2-test" in done) == any(st == "DRAFT" for _, st in state["programs"])
    # it is never applied a second time
    r = c2.post("/api/twin-changes/approve", json={"digest": digest}, headers=h2)
    assert r.status_code == 409 and "INTERRUPTED" in r.json()["detail"]
    # undo removes what was done
    r = c2.post("/api/twin-changes/undo", json={"digest": digest, "note": "after the interruption"}, headers=h2)
    assert r.status_code == 200 and r.json()["problems"] == [], r.text
    after = _live_state(app2)
    assert after["p2"] in (None, "RETIRED") and all(st == "RETIRED" for _, st in after["programs"])


def test_fb07_two_approvals_at_once_apply_the_package_once(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    digest = _export(twin)
    gate, go = threading.Event(), threading.Event()

    def hold(w, step):
        if w == "intent" and not gate.is_set():
            gate.set()
            go.wait(5)                                  # the first approval waits inside the apply
    app.state.changes_fail_point = hold
    c1, c2 = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)   # two browsers
    hb, ha = login(c1, "bench1", ORIGIN), login(c2, "admin1", ORIGIN)
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__(
        "first", c1.post("/api/twin-changes/approve", json={"digest": digest, "note": "a"}, headers=hb)))
    t.start()
    assert gate.wait(10)
    second = c2.post("/api/twin-changes/approve", json={"digest": digest, "note": "b"}, headers=ha)
    go.set()
    t.join(10)
    assert out["first"].status_code == 200, out["first"].text
    assert second.status_code == 409
    progs = [v for v in app.state.programs.versions() if v["program_id"] == "p2-test"]
    assert len(progs) == 1                                  # one version: applied once
