"""0.7.0 PROG-01 through the API: block -> program form -> preview -> save -> validate, with no JSON written by
the operator; a viewer sees summaries only."""
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_blocks_070 import SYMS, drawing, graph, setup_equipment


def ready_block(c, h):
    setup_equipment(c, h)
    for iid, role, point in (("pump-1", "start", "relay_B.DO7"), ("valve-1", "open", "relay_B.DO8")):
        r = c.post("/api/equipment/bind", json={"item_id": iid, "role": role, "point": point}, headers=h)
        assert r.status_code == 200, r.text
    g = graph()
    assert c.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g, SYMS), "expected_topology_rev": 0,
                                                     "expected_diagram_rev": 0}, headers=h).status_code == 200
    tp = c.post("/api/blocks/template-plan", json={"template": "IRRIGATION_SECTOR", "supply": "tank-1",
                                                   "destination": "sector-1", "travel_s": 3}, headers=h).json()
    tp["plan"]["startup"][-1]["verify"] = False                    # the test pump has no running feedback
    body = {"template": "IRRIGATION_SECTOR", "members": tp["members"], "supply": "tank-1", "destination": "sector-1",
            "meter": tp["meter"], "plan": tp["plan"]}
    assert c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body}, headers=h).status_code == 200
    assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 1}, headers=h).status_code == 200


FORM = {"block_id": "s1", "block_version": 1, "endpoint": {"kind": "TIME", "minutes": 10, "max_minutes": 15},
        "schedule": {"kind": "WEEKLY", "days": ["MON", "WED", "FRI"], "times": ["06:00", "18:30"]}}


def test_prog01_timed_weekly_program_from_the_form(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        h = login(c, "oper1")
        pv = c.post("/api/programs/preview", json={"form": FORM}, headers=h)
        assert pv.status_code == 200, pv.text
        pv = pv.json()
        assert "water for 10 min" in pv["summary"]["text"] and len(pv["summary"]["next"]) == 3
        assert pv["steps"][0].startswith("switch on Sector valve") and "keep watering for 10 min" in pv["steps"]
        assert pv["resources"] == ["Main pump", "Sector valve"]
        r = c.post("/api/programs/form", json={"program_id": "sector-1-am", "name": "Sector 1 mornings", "form": FORM},
                   headers=h)
        assert r.status_code == 200, r.text
        v = c.post("/api/programs/validate", json={"program_id": "sector-1-am", "version": 1}, headers=h).json()
        assert v["status"] == "VALIDATED", v
        listed = {x["program_id"]: x for x in c.get("/api/programs", headers=h).json()["programs"]}
        assert listed["sector-1-am"]["form"]["block_id"] == "s1" and "Sector 1" in listed["sector-1-am"]["summary"]["text"]
        # a volume through a block without a meter, or a wrong value: the field is named
        bad = dict(FORM, endpoint={"kind": "VOLUME", "litres": 100, "max_minutes": 30}, block_id="s1")
        bad["permissives"] = [{"item": "pump-1", "role": "start", "op": "IS_ON"}]
        r = c.post("/api/programs/preview", json={"form": bad}, headers=h)
        assert r.status_code == 409 and r.json()["detail"]["field"]
        h = login(c, "view1")
        listed = c.get("/api/programs", headers=h).json()
        row = listed["programs"][0]
        assert "body" not in row and row["summary"] and listed["readings"] == [] and listed["blocks"] == []
        assert c.post("/api/programs/form", json={"program_id": "x", "name": "x", "form": FORM}, headers=h).status_code == 403
