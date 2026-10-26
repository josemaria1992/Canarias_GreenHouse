"""0.7.1 ID-01..ID-03 through the API (owner decisions 26 October 2026): an ID typed in any case is kept as typed,
IDs stay editable after creation and unique (ignoring case, under concurrent edits), and a rename keeps every
reference: channels, blocks, programs, validated versions and their proof, history. Simulation and fake data only."""
import threading

from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import FORM, ready_block


def _item(c, h, iid):
    return next(i for i in c.get("/api/equipment", headers=h).json()["items"] if i["item_id"] == iid)


def test_id01_typed_case_is_kept_and_the_key_is_stable(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        h = login(c, "oper1")
        r = c.post("/api/equipment/items", json={"item_id": "Valve_1", "name": "Valve 1", "type_id": "valve", "icon": "valve",
                                                 "body": {}}, headers=h)
        assert r.status_code == 200, r.text
        it = r.json()
        assert it["public_id"] == "Valve_1" and it["item_id"] == "valve_1"
        assert _item(c, h, "valve_1")["public_id"] == "Valve_1"            # read back as typed
        assert c.get("/api/ids/suggest?name=Irrigation%20Pump%202", headers=h).json()["id"] == "IrrigationPump_2"
        # the same ID in another case is the same ID: refused inline, nothing renumbered
        r = c.post("/api/equipment/items", json={"item_id": "valve_1", "name": "Other valve", "type_id": "valve",
                                                 "icon": "valve", "body": {}}, headers=h)
        assert r.status_code == 409 and "already used" in r.text
        assert c.post("/api/equipment/items", json={"item_id": "bad id", "name": "Bad", "type_id": "valve", "icon": "valve",
                                                    "body": {}}, headers=h).status_code == 409
        # existing lower-case IDs are untouched
        assert _item(c, h, "pump-1")["public_id"] == "pump-1"
        # an existing item can be addressed by its public ID too (a save by ID edits it, never creates a copy)
        r = c.post("/api/equipment/items", json={"item_id": "VALVE_1", "name": "Valve 1 (north)", "type_id": "valve",
                                                 "icon": "valve", "body": {}, "expected_version": 1}, headers=h)
        assert r.status_code == 200 and r.json()["item_id"] == "valve_1" and r.json()["version"] == 2


def test_id02_id03_rename_after_creation_keeps_every_reference(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        h = login(c, "oper1")
        r = c.post("/api/programs/form", json={"program_id": "Morning_1", "name": "Morning 1", "form": FORM}, headers=h)
        assert r.status_code == 200, r.text
        key = r.json()["program_id"]
        assert key == "morning_1"
        v = c.post("/api/programs/validate", json={"program_id": key, "version": 1}, headers=h).json()
        assert v["status"] == "VALIDATED"
        before_prog = {p["program_id"]: p for p in c.get("/api/programs", headers=h).json()["programs"]}[key]
        before_bind = {(b["item_id"], b["role"]): b for b in c.get("/api/equipment", headers=h).json()["bindings"]}
        before_block = c.get("/api/blocks", headers=h).json()["blocks"][0]
        pump = _item(c, h, "pump-1")
        # rename the pump and the program after creation
        r = c.post("/api/equipment/items/pump-1/id", json={"public_id": "MainPump_1", "expected_rev": pump["id_rev"]}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["public_id"] == "MainPump_1" and r.json()["item_id"] == "pump-1"
        assert r.json()["id_history"][-1] == r.json()["id_history"][-1] | {"old_id": "pump-1", "new_id": "MainPump_1"}
        stale = c.post("/api/equipment/items/pump-1/id", json={"public_id": "Pump_X", "expected_rev": 0}, headers=h)
        assert stale.status_code == 409 and "meanwhile" in stale.text          # revision check
        dup = c.post("/api/equipment/items/valve-1/id", json={"public_id": "mainpump_1", "expected_rev": 0}, headers=h)
        assert dup.status_code == 409 and "already used" in dup.text           # unique ignoring case
        r = c.post("/api/programs/rename", json={"program_id": "Morning_1", "public_id": "MorningWatering_1",
                                                 "expected_rev": before_prog["id_rev"]}, headers=h)
        assert r.status_code == 200, r.text
        # nothing that names the keys changed: channels, block, program version, its digest and status
        after_bind = {(b["item_id"], b["role"]): b for b in c.get("/api/equipment", headers=h).json()["bindings"]}
        assert after_bind == before_bind
        after_block = c.get("/api/blocks", headers=h).json()["blocks"][0]
        assert after_block["digest"] == before_block["digest"] and after_block["status"] == before_block["status"]
        after_prog = {p["program_id"]: p for p in c.get("/api/programs", headers=h).json()["programs"]}[key]
        assert after_prog["public_id"] == "MorningWatering_1"
        assert (after_prog["version"], after_prog["digest"], after_prog["status"]) == \
               (before_prog["version"], before_prog["digest"], before_prog["status"])
        # the item is still found by its key and by its new ID; history keeps both IDs
        assert _item(c, h, "pump-1")["public_id"] == "MainPump_1"
        ev = c.get("/api/events?kind=PUBLIC_ID_CHANGED", headers=h)
        assert ev.status_code == 200
        rows = [e for e in app.state.hist.query("SELECT payload_json FROM events WHERE kind='PUBLIC_ID_CHANGED'")]
        renames = [r["payload_json"] for r in rows if '"from": null' not in r["payload_json"]]
        assert len(renames) == 2 and '"from": "pump-1"' in renames[0] and '"from": "Morning_1"' in renames[1]


def test_id02_concurrent_renames_to_one_id_one_wins(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        eq = app.state.equipment if hasattr(app.state, "equipment") else None
        store = eq or app.state.equipment_store
        results = []
        barrier = threading.Barrier(2)

        def go(iid):
            barrier.wait()
            try:
                store.rename_item("admin1", iid, "Shared_1", 0)
                results.append(("ok", iid))
            except Exception as e:                                    # noqa: BLE001
                results.append(("refused", str(e)))
        ts = [threading.Thread(target=go, args=(i,)) for i in ("pump-1", "valve-1")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert sorted(r[0] for r in results) == ["ok", "refused"], results
        ids = [i["public_id"] for i in store.items()]
        assert ids.count("Shared_1") == 1
