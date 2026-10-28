"""UX-05: a built-in type (Tank) is discoverable with its state and made available in one administrator step without
JSON, never reactivating a retired type, replacing an edited draft or touching other types; a tank needs no channel
and keeps its capacity."""
from fastapi.testclient import TestClient

from aquacrop.equipment.definitions import TEMPLATES
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)


def _state(c, h, tid):
    return next(a for a in c.get("/api/equipment", headers=h).json()["availability"] if a["type_id"] == tid)


def test_tank_states_and_make_available(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c, TestClient(app, base_url=ORIGIN) as co:
        h = login(c, "admin1")
        pump = TEMPLATES["pump"]
        assert c.post("/api/equipment/types/draft", json={"type_id": "pump", "name": pump["name"], "body": pump["body"]}, headers=h).status_code == 200
        assert c.post("/api/equipment/types/activate", json={"type_id": "pump", "version": 1}, headers=h).status_code == 200
        pump_digest = next(t for t in c.get("/api/equipment", headers=h).json()["types"] if t["type_id"] == "pump")["digest"]
        assert _state(c, h, "tank")["state"] == "TEMPLATE" and _state(c, h, "tank")["roles"] == 0
        ho = login(co, "oper1")
        r = co.post("/api/equipment/types/make-available", json={"type_id": "tank"}, headers=ho)
        assert r.status_code == 403 and "CSRF" not in r.text
        r = c.post("/api/equipment/types/make-available", json={"type_id": "tank"}, headers=h)
        assert r.status_code == 200 and r.json()["changed"], r.text
        assert _state(c, h, "tank")["state"] == "ACTIVE"
        assert next(t for t in c.get("/api/equipment", headers=h).json()["types"] if t["type_id"] == "pump")["digest"] == pump_digest
        # an operator can now create a tank with its capacity, and no channel is needed
        r = co.post("/api/equipment/items", json={"item_id": "tank-1", "name": "Main tank", "type_id": "tank", "icon": "tank",
                                                 "body": {"properties": {"capacity_l": 20000}}}, headers=ho)
        assert r.status_code == 200, r.text
        assert c.get("/api/equipment", headers=h).json()["readiness"]["tank-1"] is not None
        # a draft seeded from the template is activated; an edited draft is not replaced
        assert c.post("/api/equipment/types/seed", headers=h).status_code == 200
        assert _state(c, h, "sector")["state"] == "DRAFT"
        assert c.post("/api/equipment/types/make-available", json={"type_id": "sector"}, headers=h).json()["state"] == "ACTIVE"
        body = dict(TEMPLATES["valve"]["body"], description="our own valve")
        vd = _state(c, h, "valve")["version"]                         # the seeded draft, edited by the owner
        assert c.post("/api/equipment/types/draft", json={"type_id": "valve", "name": "Valve", "body": body, "version": vd},
                      headers=h).status_code == 200
        r = c.post("/api/equipment/types/make-available", json={"type_id": "valve"}, headers=h)
        assert r.status_code == 409 and "differs" in r.text
        # retired stays retired
        r = c.post("/api/equipment/types/make-available", json={"type_id": "level"}, headers=h)
        assert r.status_code == 200
        assert c.post("/api/equipment/types/level/retire", json={"reason": "not used"}, headers=h).status_code == 200
        r = c.post("/api/equipment/types/make-available", json={"type_id": "level"}, headers=h)
        assert r.status_code == 409 and "retired" in r.text and _state(c, h, "level")["state"] == "RETIRED"
