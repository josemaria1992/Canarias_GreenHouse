"""0.7.5 review C075-03/04/07: the project export carries the earlier Overview drawing faithfully (JSON and YAML),
an approval covers the installation as previewed, malformed records are refused readably, and imported items and
drawings have a continuation (proposals listed, dismissed, item created on Equipment)."""
import copy
import json

import yaml
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import ready_block


def test_export_keeps_the_earlier_overview_drawing(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        legacy = c.get("/api/layout", headers=h).json()["layout"]
        ov = c.get("/api/overview/diagram", headers=h).json()
        assert legacy["nodes"] and not ov["explicit"]
        for fmt, load in (("json", json.loads), ("yaml", yaml.safe_load)):
            doc = load(c.get(f"/api/project/export?fmt={fmt}", headers=h).text)
            dr = doc["drawing"]
            same = lambda d: {k: d.get(k) for k in ("symbols", "placeholders", "texts", "shapes", "sketches", "routes")}  # noqa: E731
            assert same(dr["diagram"]) == same(ov["legacy_view"]["diagram"])            # every visible element
            assert dr["legacy_layout"]["nodes"] == legacy["nodes"] and dr["source"]["digest"]
            n = len(dr["diagram"].get("symbols") or []) + len(dr["diagram"].get("placeholders") or []) \
                + len(dr["diagram"].get("texts") or []) + len(dr["diagram"].get("shapes") or [])
            assert n == len(legacy["nodes"])
            pv = c.post("/api/project/import/preview", json={"text": json.dumps(doc)}, headers=h)
            assert pv.status_code == 200, pv.text


def test_a_preview_goes_stale_when_the_installation_changes(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        text = c.get("/api/project/export", headers=h).text
        pv = c.post("/api/project/import/preview", json={"text": text}, headers=h).json()
        body = copy.deepcopy(app.state.blocks.get("s1", 1)["body"])
        body["description"] = "a later validated installation edit"
        assert c.post("/api/blocks", json={"block_id": "s1", "name": "Sector 1", "body": body, "expected_version": 1},
                      headers=h).status_code == 200
        assert c.post("/api/blocks/validate", json={"block_id": "s1", "version": 2}, headers=h).status_code == 200
        r = c.post("/api/project/import/apply", json={"text": text, "expected_digest": pv["digest"]}, headers=h)
        assert r.status_code == 409 and "preview it again" in r.text
        assert [b["version"] for b in app.state.blocks.blocks() if b["block_id"] == "s1"] == [1, 2]   # nothing written
        pv2 = c.post("/api/project/import/preview", json={"text": text}, headers=h).json()
        assert next(x for x in pv2["rows"] if x["kind"] == "block")["action"] == "NEW VERSION"   # now shown


def test_malformed_records_are_refused_by_name(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN, raise_server_exceptions=False) as c:
        h = login(c, "admin1")
        base = {"kind": "AQUACROP_PROJECT", "schema": 1}
        for doc, word in ((base | {"equipment": {"types": [1]}}, "equipment.types[0]"),
                          (base | {"equipment": {"items": [{"item_id": "a b"}]}}, "item_id"),
                          (base | {"blocks": [{"block_id": "x", "body": []}]}, "body"),
                          (base | {"programs": [{"program_id": "p"}, {"program_id": "p"}]}, "twice"),
                          (base | {"equipment": []}, "equipment"),
                          (base | {"drawing": {"graph": [1]}}, "drawing.graph")):
            r = c.post("/api/project/import/preview", json={"text": json.dumps(doc)}, headers=h)
            assert r.status_code == 422 and word in r.text, (doc, r.status_code, r.text)


def test_imported_items_and_drawing_have_a_continuation(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        doc = json.loads(c.get("/api/project/export", headers=h).text)
        new = dict(doc["equipment"]["items"][0], item_id="pump-9", name="Spare pump")
        doc["equipment"]["items"].append(new)
        text = json.dumps(doc)
        pv = c.post("/api/project/import/preview", json={"text": text}, headers=h).json()
        r = c.post("/api/project/import/apply", json={"text": text, "expected_digest": pv["digest"]}, headers=h)
        assert r.status_code == 200, r.text
        props = c.get("/api/project/proposals", headers=h).json()
        it = next(x for x in props["items"] if x["item_id"] == "pump-9")
        assert it["name"] == "Spare pump" and it["type_id"] == new["type_id"] and props["drawings"]
        # creating it on Equipment with the proposal's record completes it
        assert c.post("/api/equipment/items", json={"item_id": it["item_id"], "name": it["name"], "type_id": it["type_id"],
                                                    "icon": it["icon"] or "pump", "body": it["body"]}, headers=h).status_code == 200
        assert not [x for x in c.get("/api/project/proposals", headers=h).json()["items"] if x["item_id"] == "pump-9"]
        d = props["drawings"][0]["file"]
        assert c.post("/api/project/proposals/dismiss", json={"file": d}, headers=h).status_code == 200
        assert not c.get("/api/project/proposals", headers=h).json()["drawings"]
        assert c.post("/api/project/proposals/dismiss", json={"file": "../x.json"}, headers=h).status_code == 422


def test_the_bench_drawing_passes_the_document_guards():
    from pathlib import Path
    from aquacrop import exchange as x
    fx = json.loads((Path(__file__).resolve().parents[1] / "fixtures/bench-legacy-drawing.json").read_text(encoding="utf-8"))
    d = {"kind": "AQUACROP_PROJECT", "schema": 1, "drawing": {"legacy_layout": fx["layout"]}}
    for t in (json.dumps(d), x.dumps(d, "yaml")):
        assert x.loads(t)["drawing"]["legacy_layout"] == fx["layout"]
