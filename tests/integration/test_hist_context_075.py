"""0.7.5 review C075-05: a run's configuration and chart use the context the engine held at its start, so a rebind
recorded at the same instant (or after a restart) never replaces the channel it actually ran with."""
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import FORM, ready_block


def test_tied_timestamp_rebind_does_not_rewrite_the_run(tmp_path, icfg, monkeypatch):
    import aquacrop.equipment.store as eqstore
    import aquacrop.historian.writer as writer
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        assert c.post("/api/programs/form", json={"program_id": "am", "name": "Morning", "form": FORM}, headers=h).status_code == 200
        it = next(i for i in app.state.equipment.items() if i["item_id"] == "pump-1")
        from aquacrop.equipment.definitions import ItemBody, digest
        held = {"items": {"pump-1": {"body": digest(ItemBody.model_validate(it["body"]).model_dump(mode="json")),
                                     "points": {"start": "relay_B.DO7"}}}}
        fixed = "2030-01-01T00:00:00.000000+00:00"
        monkeypatch.setattr(writer, "utc_iso", lambda: fixed)
        monkeypatch.setattr(eqstore, "_now", lambda: fixed)
        app.state.hist.event("PROGRAM_OP_STARTED", {"op_id": "op_tied", "program": "am", "version": 1, "origin": "MANUAL",
                                                    "context": held, "lead": None})
        app.state.hist.flush()
        assert c.post("/api/equipment/bind", json={"item_id": "pump-1", "role": "start", "point": "relay_B.DO6"},
                      headers=h).status_code == 200
        # read back from the recorded start event (what a restarted service reads too)
        d = c.get("/api/history/runs/op_tied", headers=h).json()
        run_b = next(b for b in d["configuration"]["bindings"] if b["item_id"] == "pump-1" and b["role"] == "start")
        assert run_b["point"] == "relay_B.DO7"
        assert any("relay_B.DO6" in x for x in d["configuration"]["changed_since"])
        assert "relay_B.DO7" in {p["point"] for p in d["chart"]["points"]}
        assert "relay_B.DO6" not in {p["point"] for p in d["chart"]["points"]}


def test_the_engine_holds_the_run_context_with_both_duty_pumps():
    from tests.unit.test_prog0910_073 import Rig, _body, _pumps
    r = Rig(_pumps(), {"drain": (1, _body(duty=("pump-a", "pump-b")))})
    r.twin.set_input("digital_A.DI4", False)
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    r.e.run_now("t", "drain")
    p = next(e for k, e in r.events if k == "PROGRAM_OP_STARTED")
    assert set(p["context"]["items"]) == {"pump-a", "pump-b"} and p["lead"] == "pump-a"
    assert p["context"]["items"]["pump-b"]["points"] == {"fault": "digital_A.DI4", "start": "digital_A.DO3"}
