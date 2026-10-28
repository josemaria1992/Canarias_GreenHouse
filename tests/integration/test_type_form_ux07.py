"""UX-07: the guided type form gets the built-in definitions and the diagram symbols from /api/equipment, and the
definitions it writes (through the pure model in web/type-form.js) are accepted by the existing draft endpoint, for
every kind it offers."""
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquacrop.equipment.definitions import TEMPLATES
from aquacrop.topology.definitions import symbols_public
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "src/aquacrop/web/type-form.js"


def _bodies(templates: dict) -> dict:
    """What the form saves for each kind: started from its built-in definition, the optional feedback asked and
    answered (contact polarity chosen), a detail added; the custom kind gets a reading and a status signal."""
    js = f"""
    const M = require({json.dumps(str(FORM))});
    const T = {json.dumps(templates)};
    const out = {{}};
    for (const k of M.KINDS) {{
      const f = M.startFrom(k.id, T);
      f.name = "UX07 " + k.word;
      for (const p of k.asks) {{ M.addStatus(f, p); f.roles[f.roles.length - 1].pol = "open"; }}
      if (k.id === "custom") {{ M.addReading(f, "Temperature", "temperature", "°C", "CURRENT_4_20"); M.addStatus(f, "STATE", "Door open"); f.roles[1].pol = "closed"; }}
      for (const r of f.roles) if (r.pol === null) r.pol = "open";        // every polarity is answered (R402-04)
      M.addProperty(f, {{ label: "Installer note", kind: "TEXT" }});
      if (f.roles.some(r => r.purpose === "COMMAND") && !f.limits.max_runtime_s) f.limits.max_runtime_s = 600;
      out[k.id] = {{ problems: M.problems(f), body: M.toBody(f), name: f.name }};
    }}
    console.log(JSON.stringify(out));
    """
    try:
        return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60, check=True).stdout)
    except FileNotFoundError:
        pytest.skip("node is not installed")


def test_the_form_gets_definitions_and_symbols_and_its_drafts_are_accepted(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        E = c.get("/api/equipment", headers=h).json()
        assert set(E["template_bodies"]) == set(TEMPLATES)
        assert E["template_bodies"]["pump"]["body"] == TEMPLATES["pump"]["body"]
        assert E["symbols"] == json.loads(json.dumps(symbols_public()))
        assert {p["id"] for p in E["symbols"]["pump"]["ports"]} >= {"in", "out"}
        for kind, r in _bodies(E["template_bodies"]).items():
            assert r["problems"] == [], (kind, r["problems"])
            tid = "ux07_" + kind
            resp = c.post("/api/equipment/types/draft", json={"type_id": tid, "name": r["name"], "body": r["body"]}, headers=h)
            assert resp.status_code == 200, (kind, resp.text)
            saved = next(t for t in c.get("/api/equipment", headers=h).json()["types"] if t["type_id"] == tid)
            # polarity is what was answered: an optional feedback answered "opens" is stored inverted
            for role in saved["body"]["roles"]:
                if role["purpose"] in ("RUNNING", "FAULT", "OPEN", "CLOSED"):
                    assert role["inverted"] is True and role["required"] is False, (kind, role)
                if role["direction"] == "INPUT" and role["signal"] == "DIGITAL" and kind == "button":
                    assert role["inverted"] is True, role                        # as answered, never assumed
            assert saved["body"]["properties"][-1]["label"] == "Installer note"


def test_r402_05_a_changed_measurement_goes_through_the_draft_endpoint_and_reopens_the_same(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        T = c.get("/api/equipment", headers=h).json()["template_bodies"]
        js = f"""
        const M = require({json.dumps(str(FORM))});
        const f = M.startFrom("flow", {json.dumps(T)});
        f.name = "Line pressure"; M.setMeasure(f, f.roles[0], "pressure");
        console.log(JSON.stringify({{ problems: M.problems(f), body: M.toBody(f) }}));
        """
        try:
            r = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60, check=True).stdout)
        except FileNotFoundError:
            pytest.skip("node is not installed")
        assert r["problems"] == []
        assert c.post("/api/equipment/types/draft", json={"type_id": "line_pressure", "name": "Line pressure", "body": r["body"]},
                      headers=h).status_code == 200
        saved = next(t for t in c.get("/api/equipment", headers=h).json()["types"] if t["type_id"] == "line_pressure")
        js2 = f"""
        const M = require({json.dumps(str(FORM))});
        const f = M.fromBody({json.dumps(saved["body"])}, {{ name: "Line pressure", type_id: "line_pressure", version: {saved["version"]} }});
        console.log(JSON.stringify(f.roles.map(r => [r.role, r.measure, r.label, r.unit])));
        """
        back = json.loads(subprocess.run(["node", "-e", js2], capture_output=True, text=True, timeout=60, check=True).stdout)
        assert back == [["pressure", "pressure", "Pressure", "bar"]]


def test_r913_03_two_readings_of_one_quantity_go_through_the_draft_endpoint_and_reopen_the_same(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        js = f"""
        const M = require({json.dumps(str(FORM))});
        const f = M.startFrom("custom", {{}});
        f.name = "Line monitor";
        M.addReading(f, "Pressure", "pressure", "bar");
        M.addReading(f, "Something", "other", "x");
        M.setMeasure(f, f.roles[1], "pressure"); f.roles[1].label = "Discharge pressure";
        console.log(JSON.stringify({{ problems: M.problems(f), body: M.toBody(f) }}));
        """
        try:
            r = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60, check=True).stdout)
        except FileNotFoundError:
            pytest.skip("node is not installed")
        assert r["problems"] == []
        assert c.post("/api/equipment/types/draft", json={"type_id": "line_monitor", "name": "Line monitor", "body": r["body"]},
                      headers=h).status_code == 200
        saved = next(t for t in c.get("/api/equipment", headers=h).json()["types"] if t["type_id"] == "line_monitor")
        js2 = f"""
        const M = require({json.dumps(str(FORM))});
        const f = M.fromBody({json.dumps(saved["body"])}, {{ name: "Line monitor", type_id: "line_monitor", version: {saved["version"]} }});
        console.log(JSON.stringify(f.roles.map(r => [r.role, r.measure, r.unit])));
        """
        back = json.loads(subprocess.run(["node", "-e", js2], capture_output=True, text=True, timeout=60, check=True).stdout)
        assert back == [["pressure", "pressure", "bar"], ["pressure_2", "pressure", "bar"]]
