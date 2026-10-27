"""0.7.3 owner correction 5: a block's own start permissives and stop conditions apply to every program over it,
alongside the program's own; earlier block digests do not change."""
from fastapi.testclient import TestClient

from aquacrop.blocks.definitions import BlockBody, Safety, digest
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import FORM, ready_block


def test_block_conditions_join_every_program_and_old_digests_stay():
    assert digest(BlockBody(safety=Safety(max_runtime_s=600))) == digest(BlockBody.model_validate(
        {"safety": {"max_runtime_s": 600, "permissives": [], "guards": []}}))
    import hashlib, json  # noqa: E401
    d = BlockBody(safety=Safety(max_runtime_s=600)).model_dump(mode="json")
    d["safety"] = {"max_runtime_s": 600.0}                         # how 0.7.1/0.7.2 wrote it
    d.pop("duty", None)
    assert digest(BlockBody(safety=Safety(max_runtime_s=600))) == hashlib.sha256(
        json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_block_conditions_in_the_compiled_program(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        b = next(x for x in c.get("/api/blocks", headers=h).json()["blocks"] if x["block_id"] == "s1")
        body = dict(b["body"])
        body["safety"] = {"max_runtime_s": 1200, "permissives": [{"item": "pump-1", "role": "start", "op": "IS_ON"}],
                          "guards": []}
        r = c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body, "expected_version": 1}, headers=h)
        assert r.status_code == 200, r.text
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 2}, headers=h).status_code == 200
        form = dict(FORM, block_version=2)
        pv = c.post("/api/programs/preview", json={"form": form}, headers=h)
        # a command output is not evidence: the block's condition is checked like a program's, and named as the block's
        assert pv.status_code == 409 and "the block's start condition 1" in pv.text, pv.text
        body["safety"]["permissives"] = [{"item": "meter-1", "role": "flow", "op": "BELOW", "value": 1, "unit": "L/min"}]
        r = c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body, "expected_version": 2}, headers=h)
        assert r.status_code == 200, r.text
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 3}, headers=h).status_code == 200
        form = dict(FORM, block_version=3, permissives=[])
        pv = c.post("/api/programs/preview", json={"form": form}, headers=h)
        assert pv.status_code == 200, pv.text
        pv = pv.json()
        assert pv["body"]["permissives"][0]["item"] == "meter-1" and pv["summary"]["block_start_conditions"]
