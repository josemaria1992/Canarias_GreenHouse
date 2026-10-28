"""0.7.0 F01 / FB-01: twin proof counts only from the current twin run and in the tested context (program body,
equipment settings, channels, water network and model). Reset or a changed channel needs a new run. The live
system is read through the fake bus: the twin writes nothing to it."""
from tests.hardware.test_twin_service import _twin_h, live_and_twin  # noqa: F401  (fixture)


def _run_program(twin, H, program_id, steps=40):
    twin.post("/api/twin/control", json={"action": "step", "steps": 2}, headers=H)
    mode = twin.get("/api/twin").json()["engine"]["mode"]
    if mode == "RECOVERY":
        assert twin.post("/api/twin/recovery/approve", json={"target": "MANUAL"}, headers=H).status_code == 200
    r = twin.post("/api/twin/run-now", json={"program_id": program_id}, headers=H)
    assert r.status_code == 200, r.text
    twin.post("/api/twin/control", json={"action": "step", "steps": steps}, headers=H)


def test_fb01_reset_and_rebinding_invalidate_the_proof_until_a_new_run(live_and_twin):
    app, live, twin_app, twin, bus, hl = live_and_twin
    frames_before = len(bus.write_frames())
    H = _twin_h(twin, "bench1")
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "p1", "duration_s": 10}}
    r = twin.post("/api/twin/programs/draft", json={"program_id": "proof-test", "name": "Proof test", "body": body},
                  headers=H)
    assert r.status_code == 200, r.text
    assert twin.post("/api/twin/programs/activate", json={"program_id": "proof-test", "version": 1},
                     headers=H).status_code == 200
    _run_program(twin, H, "proof-test")
    svc = twin_app.state.svc
    assert svc.results[-1]["result"] == "DONE" and svc.results[-1]["context"]
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert pv["programs"][0]["passed"] and not pv["missing"]

    # reset: a new twin run; the DONE of the previous run is history only
    assert twin.post("/api/twin/control", json={"action": "reset"}, headers=H).status_code == 200
    H["X-Runtime-Id"] = twin.get("/api/auth/session").json()["runtime_id"]
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert not pv["programs"][0]["passed"] and pv["programs"][0]["history_runs"] == 1
    r = twin.post("/api/twin/export", json={"note": "after reset"}, headers=H)
    assert r.status_code == 409 and "earlier runs are history only" in r.json()["detail"]
    assert twin.post("/api/twin/accept", json={"program_id": "proof-test", "version": 1, "note": ""},
                     headers=H).status_code in (400, 409)

    # a run in the new twin run counts again
    _run_program(twin, H, "proof-test")
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert pv["programs"][0]["passed"]

    # the pump's output moves to another channel: the tested context changed, so the pass no longer counts
    r = twin.post("/api/twin/equipment/bind", json={"item_id": "p1", "role": "start", "point": "relay_A.DO2"}, headers=H)
    assert r.status_code == 200, r.text
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert not pv["programs"][0]["passed"]
    r = twin.post("/api/twin/export", json={"note": "after rebinding"}, headers=H)
    assert r.status_code == 409

    # a run with the new channel is proof for the new context; the export then carries it
    _run_program(twin, H, "proof-test")
    pv = twin.get("/api/twin/changes").json()["preview"]
    assert pv["programs"][0]["passed"] and pv["bindings"] == [
        {"item_id": "p1", "role": "start", "point": "relay_A.DO2", "base_point": "relay_A.DO1"}]
    r = twin.post("/api/twin/export", json={"note": "proved with the new channel"}, headers=H)
    assert r.status_code == 200, r.text
    assert len(bus.write_frames()) == frames_before          # nothing reached the real bus
