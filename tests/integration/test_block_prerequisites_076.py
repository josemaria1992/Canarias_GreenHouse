"""UX-01: an empty block choice is explained by its missing stage with the equipment's names: not created, created but
not in the approved drawing, exported and awaiting approval, an unlinked earlier-drawing symbol; a drawn meter says
whether its reading can measure litres."""
from fastapi.testclient import TestClient

from aquacrop.blocks.prerequisites import check
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_blocks_070 import SYMS, drawing, graph, setup_equipment


def test_each_stage_is_named():
    items = {"t": {"kind": "tank", "name": "Main tank"}, "s": {"kind": "sector", "name": "Sector 1"},
             "m": {"kind": "meter", "name": "Flow 1"}}
    r = check({}, [], set(), [])["roles"]
    assert r["source"]["state"] == "NOT_CREATED" and "Create one on Equipment" in r["source"]["next"][0]
    r = check(items, [], set(), [])["roles"]
    assert r["source"]["state"] == "NOT_PLACED" and "Main tank" in r["source"]["next"][0]
    r = check(items, [], {"t"}, [])["roles"]
    assert r["source"]["state"] == "WAITING_APPROVAL" and "waiting for approval" in r["source"]["next"][0]
    r = check({}, [], set(), [{"kind": "tank", "label": "Fertiliser A"}])["roles"]
    assert r["source"]["state"] == "UNLINKED_SYMBOL" and "Fertiliser A" in r["source"]["next"][0]
    r = check(items, [{"id": "t", "item": "t", "kind": "tank"}, {"id": "m", "item": "m", "kind": "meter"}], set(), [],
              readiness=lambda i: {"ready": False, "unverified": ["Flow"]})["roles"]
    assert r["source"]["state"] == "READY" and not r["source"]["next"]
    assert r["meter"]["measurement"] == {"m": "its channel is not verified"}


def test_api_before_and_after_the_drawing(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        setup_equipment(c, h)
        r = c.get("/api/blocks/prerequisites", headers=h).json()["roles"]
        assert r["source"]["state"] == "NOT_PLACED" and "Main tank" in " ".join(r["source"]["next"])
        assert r["meter"]["state"] == "NOT_PLACED"
        g = graph()
        assert c.post("/api/drawing/installation", json={"graph": g, "diagram": drawing(g, SYMS), "expected_topology_rev": 0,
                                                         "expected_diagram_rev": 0}, headers=h).status_code == 200
        r = c.get("/api/blocks/prerequisites", headers=h).json()["roles"]
        assert r["source"]["state"] == r["destination"]["state"] == r["meter"]["state"] == "READY"
        assert set(r["meter"]["measurement"].values()) <= {"ready", "no channel chosen for its flow reading",
                                                           "its channel is not verified", "its reading has no scaling",
                                                           "not ready"}
