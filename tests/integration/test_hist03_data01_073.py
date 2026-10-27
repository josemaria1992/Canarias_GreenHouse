"""0.7.3 HIST-03 and DATA-01: a run's view rebuilds the configuration it executed with from the versioned records,
lists what changed since without applying it, and names the channels its chart draws (as bound then)."""
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration.test_program_forms_070 import FORM, ready_block


def test_run_view_keeps_the_configuration_it_ran_with(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "admin1")
        ready_block(c, h)
        assert c.post("/api/programs/form", json={"program_id": "am", "name": "Mornings", "form": FORM},
                      headers=h).status_code == 200
        hist = app.state.hist
        hist.event("PROGRAM_OP_STARTED", {"op_id": "op_hist03", "program": "am", "version": 1, "origin": "MANUAL"},
                   actor="oper1")
        hist.event("PROGRAM_OP_ENDED", {"op_id": "op_hist03", "result": "DONE"}, actor="system")
        hist.flush()
        # later edits: the pump moves to another channel
        r = c.post("/api/equipment/bind", json={"item_id": "pump-1", "role": "start", "point": "relay_B.DO6"}, headers=h)
        assert r.status_code == 200, r.text
        d = c.get("/api/history/runs/op_hist03", headers=h).json()
        conf = d["configuration"]
        then = {(b["item_id"], b["role"]): b["point"] for b in conf["bindings"]}
        assert then[("pump-1", "start")] == "relay_B.DO7"                       # as it was when the run started
        assert any("pump" in x.lower() and "relay_B.DO6" in x for x in conf["changed_since"]), conf["changed_since"]
        assert conf["block"]["block_id"] == "s1" and conf["block"]["version"] == 1
        assert {p["point"] for p in d["chart"]["points"]} >= {"relay_B.DO7", "relay_B.DO8"}
        assert "never filled" in d["chart"]["meaning"]
