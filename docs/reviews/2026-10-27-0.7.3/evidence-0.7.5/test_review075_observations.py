"""Independent observations at 7a70ee6. Expected-defect assertions, not acceptance tests."""
import copy
import json

from fastapi.testclient import TestClient
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login
from tests.integration.test_program_forms_070 import FORM, ready_block
from tests.unit.test_prog0910_073 import _body, _pumps
from aquacrop.programs.proof import context
from aquacrop.topology.definitions import parse as topology


def test_standby_rebinding_is_missing_from_proof_context():
    views = _pumps()
    body = _body(duty=("pump-a", "pump-b")).model_dump(mode="json")
    ver = {"body": body, "digest": "same-program"}
    graph = topology({"nodes": [{"id": i, "item": i, "kind": "pump"} for i in views], "edges": []})
    before = context(ver, views, graph)
    views["pump-b"].points["start"] = "digital_A.DO5"
    after = context(ver, views, graph)
    print("PROOF", before["items"], "unchanged_after_standby_rebind", before["digest"] == after["digest"])
    assert before["items"] == ["pump-a"]
    assert before["digest"] == after["digest"]


def test_legacy_overview_not_in_project_export(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        current = c.get("/api/layout", headers=h).json()["layout"]
        overview = c.get("/api/overview/diagram", headers=h).json()
        doc = c.get("/api/project/export", headers=h).json()
        print("EXPORT", "legacy_nodes", len(current["nodes"]), "explicit", overview["explicit"],
              "exported_symbols", len(doc["drawing"]["diagram"]["symbols"]))
        assert current["nodes"] and not overview["explicit"]
        assert doc["drawing"]["graph"]["nodes"] == []
        assert doc["drawing"]["diagram"]["symbols"] == []
        assert not doc["drawing"]["diagram"].get("placeholders")


def test_import_applies_a_change_not_shown_in_preview(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        text = c.get("/api/project/export", headers=h).text
        pv = c.post("/api/project/import/preview", json={"text": text}, headers=h).json()
        assert next(r for r in pv["rows"] if r["kind"] == "block")["action"] == "SAME"
        body = copy.deepcopy(app.state.blocks.get("s1", 1)["body"])
        body["description"] = "a later validated installation edit"
        r = c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body, "expected_version": 1}, headers=h)
        assert r.status_code == 200, r.text
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 2}, headers=h).status_code == 200
        r = c.post("/api/project/import/apply", json={"text": text, "expected_digest": pv["digest"]}, headers=h)
        print("IMPORT_STALE_PREVIEW", r.status_code, r.json().get("written"))
        assert r.status_code == 200
        assert {"kind": "block", "id": "s1", "status": "DRAFT"} in r.json()["written"]
        assert app.state.blocks.get("s1", 3)["body"]["description"] != body["description"]


def test_malformed_record_is_a_500(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN, raise_server_exceptions=False) as c:
        h = login(c, "admin1")
        text = json.dumps({"kind": "AQUACROP_PROJECT", "schema": 1, "equipment": {"types": [1]}})
        r = c.post("/api/project/import/preview", json={"text": text}, headers=h)
        print("IMPORT_MALFORMED", r.status_code)
        assert r.status_code == 500


def test_history_tied_timestamp_selects_later_binding(tmp_path, icfg, monkeypatch):
    import aquacrop.historian.writer as writer
    import aquacrop.equipment.store as eqstore
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        assert c.post("/api/programs/form", json={"program_id": "am", "name": "Morning", "form": FORM}, headers=h).status_code == 200
        fixed = "2030-01-01T00:00:00.000000+00:00"
        monkeypatch.setattr(writer, "utc_iso", lambda: fixed)
        monkeypatch.setattr(eqstore, "_now", lambda: fixed)
        app.state.hist.event("PROGRAM_OP_STARTED", {"op_id": "op_tied", "program": "am", "version": 1, "origin": "MANUAL"})
        app.state.hist.flush()
        r = c.post("/api/equipment/bind", json={"item_id": "pump-1", "role": "start", "point": "relay_B.DO6"}, headers=h)
        assert r.status_code == 200, r.text
        d = c.get("/api/history/runs/op_tied", headers=h).json()
        historical = next(b for b in d["configuration"]["bindings"] if b["item_id"] == "pump-1" and b["role"] == "start")
        print("HISTORY", historical["point"], d["configuration"]["changed_since"])
        assert historical["point"] == "relay_B.DO6"  # actually bound after the run started
        assert not any("DO6" in x for x in d["configuration"]["changed_since"])
