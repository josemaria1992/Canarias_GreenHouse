"""Owner decision 27 Oct 2026: a program compiled over a block version whose safety settings changed in a newer
VALIDATED block version cannot start (real start paths, bench activation) until it is reviewed with the new
version, validated and twin-tested. A block draft changes nothing; a change that is not a safety setting does not
block; the check reads the stores every time (a restart changes nothing)."""
import pytest
from fastapi.testclient import TestClient

from aquacrop.programs.authoring import superseded_safety
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import FORM, ready_block


def _block(c, h, app, version, **change):
    body = dict(app.state.blocks.get("s1", version - 1)["body"])
    body.update(change)
    r = c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body, "expected_version": version - 1},
               headers=h)
    assert r.status_code == 200, r.text


def test_changed_block_safety_blocks_older_programs_until_reviewed(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        assert c.post("/api/programs/form", json={"program_id": "am", "name": "Morning", "form": FORM}, headers=h).status_code == 200
        body = app.state.programs.get("am", 1)["body"]
        assert superseded_safety(body, app.state.blocks) is None
        # a block draft with a new maximum runtime changes nothing
        _block(c, h, app, 2, safety={"max_runtime_s": 600})
        assert superseded_safety(body, app.state.blocks) is None
        # validated: the older program is refused, with the versions and what changed
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 2}, headers=h).status_code == 200
        why = superseded_safety(body, app.state.blocks)
        assert why and "maximum runtime" in why and "v2" in why and "v1" in why
        row = next(p for p in c.get("/api/programs", headers=h).json()["programs"] if p["program_id"] == "am")
        assert row["safety_refusal"] == why
        # the program reviewed with v2 is no longer refused by this rule (its own twin proof gate still applies)
        r = c.post("/api/programs/form", json={"program_id": "am2", "name": "Morning v2",
                                               "form": dict(FORM, block_version=2, endpoint={"kind": "TIME", "minutes": 5, "max_minutes": 8})}, headers=h)
        assert r.status_code == 200, r.text
        assert superseded_safety(app.state.programs.get("am2", 1)["body"], app.state.blocks) is None


def test_a_change_that_is_not_a_safety_setting_does_not_block(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        assert c.post("/api/programs/form", json={"program_id": "am", "name": "Morning", "form": FORM}, headers=h).status_code == 200
        _block(c, h, app, 2, description="renamed purpose")
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 2}, headers=h).status_code == 200
        assert superseded_safety(app.state.programs.get("am", 1)["body"], app.state.blocks) is None


def test_the_engine_refuses_manual_and_scheduled_starts_through_its_refusal_hook():
    from aquacrop.programs.engine import Refused
    from tests.unit.test_prog0910_073 import Rig, _body, _pumps
    r = Rig(_pumps(), {"p": (1, _body())})
    r.e.io.program_refusal = lambda pid, ver: "the block changed its safety settings"
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    with pytest.raises(Refused, match="safety settings"):
        r.e.run_now("t", "p")
