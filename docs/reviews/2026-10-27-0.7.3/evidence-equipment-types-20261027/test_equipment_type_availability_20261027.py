"""Equipment type lifecycle observation; fake installation only."""
from fastapi.testclient import TestClient
from aquacrop.equipment.definitions import TEMPLATES
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login


def test_tank_is_built_in_but_seeding_does_not_make_it_an_active_choice(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        pump = TEMPLATES["pump"]
        r = c.post("/api/equipment/types/draft", json={"type_id": "pump", "name": pump["name"], "body": pump["body"]}, headers=h)
        assert r.status_code == 200, r.text
        assert c.post("/api/equipment/types/activate", json={"type_id": "pump", "version": 1}, headers=h).status_code == 200
        before = c.get("/api/equipment", headers=h).json()
        old_pump = next(t for t in before["types"] if t["type_id"] == "pump")
        assert before["templates"]["tank"] == "Tank"
        assert not any(t["type_id"] == "tank" for t in before["types"])
        assert "tank" in c.post("/api/equipment/types/seed", headers=h).json()["created"]
        seeded = c.get("/api/equipment", headers=h).json()
        tank = next(t for t in seeded["types"] if t["type_id"] == "tank")
        assert tank["status"] == "DRAFT" and tank["body"]["hydraulic"] == "tank" and not tank["body"]["roles"]
        assert not any(t["type_id"] == "tank" and t["status"] == "ACTIVE" for t in seeded["types"])
        assert next(t for t in seeded["types"] if t["type_id"] == "pump")["digest"] == old_pump["digest"]
        assert c.post("/api/equipment/types/activate", json={"type_id": "tank", "version": tank["version"]}, headers=h).status_code == 200
        active = c.get("/api/equipment", headers=h).json()
        assert any(t["type_id"] == "tank" and t["status"] == "ACTIVE" for t in active["types"])
        print("TANK_TYPE built-in template present; initially absent; seeded as DRAFT (not selectable); ACTIVE after explicit activation; existing pump unchanged")
