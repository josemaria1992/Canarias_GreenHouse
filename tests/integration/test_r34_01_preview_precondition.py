"""0.7.0 R34-01 / FB-06: a request made from a preview carries the state that preview showed. When another session
changes the process in between, the server refuses and sends nothing; the other session's operation is left alone."""
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)


def _state(c, h, pid="IRRIGATION"):
    return c.get("/api/processes", headers=h).json()["processes"][pid]["state"]


def test_fb06_takeover_after_another_session_started_auto_needs_a_new_confirmation(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as a:
        b = TestClient(app, base_url=ORIGIN)
        ha, hb = login(a, "oper1"), login(b, "admin1")
        shown = _state(a, ha)                                   # what session A's preview showed
        assert shown == "STOPPED"
        r = b.post("/api/processes/IRRIGATION/state", json={"target": "AUTO"}, headers=hb)
        assert r.status_code == 200, r.text
        assert _state(b, hb) == "AUTO"
        # A confirms what it saw ("switch to Manual and run"): refused, AUTO keeps running
        r = a.post("/api/processes/IRRIGATION/state", json={"target": "MANUAL", "expect": shown}, headers=ha)
        assert r.status_code == 409 and "since your preview" in r.json()["detail"]
        r = a.post("/api/processes/IRRIGATION/state", json={"target": "STOPPED", "expect": "MANUAL"}, headers=ha)
        assert r.status_code == 409
        r = a.post("/api/processes/IRRIGATION/manual", json={"op": {"kind": "x"}, "expect": "MANUAL"}, headers=ha)
        assert r.status_code == 409 and "nothing was sent" in r.json()["detail"]
        assert _state(a, ha) == "AUTO"
        rows = app.state.hist.query("SELECT kind FROM events WHERE kind='PROCESS_REQUEST_STALE_PREVIEW'")
        assert len(rows) == 3                                   # each refusal is recorded
        # with a fresh preview of AUTO, the same takeover is accepted
        r = a.post("/api/processes/IRRIGATION/state", json={"target": "STOPPED", "expect": "AUTO"}, headers=ha)
        assert r.status_code == 200, r.text

