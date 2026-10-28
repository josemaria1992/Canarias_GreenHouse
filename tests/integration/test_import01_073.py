"""0.7.3 IMPORT-01: project exchange. Export leaves secrets, leases and bindings out; the import preview names every
conflict and writes nothing; applying writes DRAFTs only, never replaces a draft, keeps the drawing as a proposal;
malformed, oversized, secret-carrying or path-escaping documents are refused; a background image is no connection."""
import json

import yaml
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import FORM, ready_block


def test_project_round_trip_preview_and_drafts(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        assert c.post("/api/programs/form", json={"program_id": "am", "name": "Mornings", "form": FORM}, headers=h).status_code == 200
        y = c.get("/api/project/export?fmt=yaml", headers=h)
        assert y.status_code == 200
        text = y.text
        doc = yaml.safe_load(text)
        low = json.dumps({k: v for k, v in doc.items() if k != "excluded"}).lower()   # the list of what is left out
        assert not any(w in low for w in ("password", "lease", "session", "secret", "relay_b.do7")), low[:300]
        assert doc["kind"] == "AQUACROP_PROJECT" and doc["blocks"] and doc["programs"] and doc["drawing"]["graph"]["nodes"]
        # the same project again: everything SAME, nothing to write
        pv = c.post("/api/project/import/preview", json={"text": text}, headers=h)
        assert pv.status_code == 200, pv.text
        pv = pv.json()
        assert set(pv["counts"]) <= {"SAME", "PROPOSAL"}, pv["rows"]
        # a changed block and a new program arrive as drafts; the program's own draft is never replaced
        doc["blocks"][0]["body"]["description"] = "from the other greenhouse"
        doc["programs"].append(dict(doc["programs"][0], program_id="pm", name="Evenings"))
        doc["programs"][0] = dict(doc["programs"][0], body=dict(doc["programs"][0]["body"], description="changed there"))
        doc["drawing"]["diagram"]["background"] = {"file": "greenhouse.png", "opacity": 0.4}
        text2 = json.dumps(doc)
        pv = c.post("/api/project/import/preview", json={"text": text2}, headers=h).json()
        acts = {(r["kind"], r["id"]): r["action"] for r in pv["rows"]}
        assert acts[("block", "s1")] == "NEW VERSION" and acts[("program", "pm")] == "NEW"
        assert acts[("program", "am")] == "CONFLICT"                       # "am" v1 is still a draft here
        assert "creates no connection" in next(r["why"] for r in pv["rows"] if r["kind"] == "drawing")
        edges = len(c.get("/api/topology", headers=h).json()["graph"]["edges"])
        assert c.post("/api/project/import/apply", json={"text": text2}, headers=h).status_code == 409   # preview first
        r = c.post("/api/project/import/apply", json={"text": text2, "expected_digest": pv["digest"]}, headers=h)
        assert r.status_code == 200, r.text
        out = r.json()
        assert {(w["kind"], w["id"]) for w in out["written"]} == {("block", "s1"), ("program", "pm")}
        assert all(w["status"] == "DRAFT" for w in out["written"]) and out["drawing_proposal"]
        assert len(c.get("/api/topology", headers=h).json()["graph"]["edges"]) == edges   # the Overview is unchanged
        assert app.state.blocks.get("s1", 2)["status"] == "DRAFT" and app.state.blocks.get("s1", 1)["status"] == "VALIDATED"
        # operators cannot import a project
        ho = login(c, "oper1")
        assert c.post("/api/project/import/preview", json={"text": text}, headers=ho).status_code == 403


def test_bad_documents_are_refused(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "admin1")
        base = {"kind": "AQUACROP_PROJECT", "schema": 1}
        bad = [
            "{not json",
            "a: [unclosed",
            json.dumps(base | {"users": [{"username": "x"}]}),                                # unknown section
            json.dumps(base | {"equipment": {"types": [], "items": [{"item_id": "x", "password": "p"}]}}),
            json.dumps(base | {"drawing": {"diagram": {"background": {"file": "../../etc/passwd"}}}}),
            json.dumps(base | {"drawing": {"diagram": {"background": {"file": "C:\\x\\y.png"}}}}),
            json.dumps(base | {"note": "x" * (2 * 1024 * 1024)}),                             # oversized
            json.dumps({"kind": "SOMETHING_ELSE", "schema": 1}),
            "x: &a [1]\n" + "".join(f"y{i}: &b{i} [*a, *a]\n" for i in range(3)) + "z: " + "[" * 60 + "]" * 60,
        ]
        for t in bad:
            r = c.post("/api/project/import/preview", json={"text": t}, headers=h)
            assert r.status_code in (413, 422), (t[:60], r.status_code, r.text[:200])   # 413: the server's own limit
