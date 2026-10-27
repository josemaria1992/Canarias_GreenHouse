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
            assert saved["body"]["properties"][-1]["label"] == "Installer note"
