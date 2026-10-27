"""0.7.3 owner correction 1: Preview and Save say what to complete, in words, every problem at once, with field paths;
never a validator pattern; an unfinished safety condition is never ignored; a newer validated block is offered."""
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import ready_block

FORM2 = {"form_version": 2, "block_id": "", "block_version": 1, "endpoint": {"kind": "TIME", "minutes": None},
         "schedule": {"kind": "WEEKLY", "days": ["MON"], "windows": [], "rest_minutes": None},
         "permissives": [{"item": "", "role": "", "op": "ABOVE", "value": None, "unit": None, "persist_s": 5}],
         "guards": [], "description": ""}


def test_preview_and_save_list_every_missing_input_in_words(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        for path, body in (("/api/programs/preview", {"form": FORM2}),
                           ("/api/programs/form", {"program_id": "Morning_1", "name": "Morning 1", "form": FORM2})):
            r = c.post(path, json=body, headers=h)
            assert r.status_code == 422, r.text
            d = r.json()["detail"]
            fields = [x["field"] for x in d["problems"]]
            assert fields == ["block_id", "endpoint.minutes", "schedule.windows", "schedule.rest_minutes", "permissives.0"]
            assert "Choose a validated block" in d["problems"][0]["message"]
            assert d["problems"][-1]["message"] == "Start condition 1: choose a reading (or remove the row)"
            assert "pattern" not in r.text and "^[" not in r.text
        # a finished form without the block's runtime: the actionable sentence
        ok = dict(FORM2, block_id="s1", endpoint={"kind": "TIME", "minutes": 5}, permissives=[],
                  schedule={"kind": "WEEKLY", "days": ["MON"], "windows": [{"start": "06:00", "finish": "07:00"}], "rest_minutes": 30})
        r = c.post("/api/programs/preview", json={"form": ok}, headers=h)
        assert r.status_code == 409 and "Set the block's maximum runtime on Blocks" in r.json()["detail"]["message"]


def test_the_program_list_offers_the_latest_validated_block_version(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        b = c.get("/api/blocks", headers=h).json()["blocks"][0]
        body = dict(b["body"], safety={"max_runtime_s": 900})
        assert c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body, "expected_version": 1}, headers=h).status_code == 200
        bl = {x["block_id"]: x for x in c.get("/api/programs", headers=h).json()["blocks"]}["s1"]
        assert bl["version"] == 1 and bl["latest_version"] == 2 and bl["latest_status"] == "DRAFT"   # the draft is not used
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 2}, headers=h).status_code == 200
        bl = {x["block_id"]: x for x in c.get("/api/programs", headers=h).json()["blocks"]}["s1"]
        assert bl["version"] == 2 and bl["body"]["safety"]["max_runtime_s"] == 900


def test_a_form_level_rule_names_its_field(tmp_path, icfg):
    from fastapi.testclient import TestClient
    from tests.integration.test_api_installation import ORIGIN, _app, login
    from tests.integration.test_program_forms_070 import FORM, ready_block
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        r = c.post("/api/programs/preview", json={"form": dict(FORM, endpoint={"kind": "TIME", "minutes": 5})}, headers=h)
        assert r.status_code == 422 and r.json()["detail"]["field"] == "endpoint.max_minutes", r.text
