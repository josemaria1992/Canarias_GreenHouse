"""0.7.1 MOD-03 (owner decision 26 October 2026): module requests are a tracked workflow on the bench. Requesters see
their own, handlers see all, rejecting is never adding, and ADDED appears only when the running configuration has the
module. Simulation profile and a schema 6 database migrated to 7; no hardware."""
import json
import sqlite3

from fastapi.testclient import TestClient

from aquacrop.historian.schema import MIGRATIONS, migrate
from aquacrop.security.auth import Role
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)
from tests.integration import test_api_installation as T


def test_mod03_requests_are_visible_to_requester_and_handlers_and_never_falsely_added(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    app.state.accounts.create_user("oper2", "operator password 2", Role.OPERATOR)
    app.state.accounts.create_user("bench2", "bench password 222", Role.OPERATOR, commissioning=True)
    T.PW.update({"oper2": "operator password 2", "bench2": "bench password 222"})
    with TestClient(app, base_url=ORIGIN) as c:
        ho = login(c, "oper1")
        r = c.post("/api/modules/requests", json={"model": "Relay (D), SKU 26517", "purpose": "drainage pumps"}, headers=ho)
        assert r.status_code == 200 and r.json()["status"] == "REQUESTED"
        rid = r.json()["request_id"]
        assert "recorded on this bench" in c.get("/api/modules/requests", headers=ho).json()["where"]
        h2 = login(c, "oper2")                                        # another operator: not their request
        assert c.get("/api/modules/requests", headers=h2).json()["requests"] == []
        hb = login(c, "bench2")                                       # a handler sees every request
        rows = c.get("/api/modules/requests", headers=hb).json()["requests"]
        assert [x["request_id"] for x in rows] == [rid] and rows[0]["requester"] == "oper1"
        assert c.post("/api/modules/requests/close", json={"request_id": rid, "outcome": "not needed", "decision": "ADDED"},
                      headers=hb).status_code == 422                  # nobody can declare a module added
        r = c.post("/api/modules/requests/close", json={"request_id": rid, "outcome": "we use the IO 8CH instead",
                                                        "decision": "REJECTED"}, headers=hb)
        assert r.json()["status"] == "REJECTED"
        ho = login(c, "oper1")
        mine = c.get("/api/modules/requests", headers=ho).json()["requests"][0]
        assert mine["status"] == "REJECTED" and mine["reason"] == "we use the IO 8CH instead" and mine["closed"]
        hb = login(c, "bench2")
        assert c.post("/api/modules/requests/close", json={"request_id": rid, "outcome": "again"}, headers=hb).status_code == 409
        ho = login(c, "oper1")
        # a request whose module is in the running configuration is ADDED, and only then
        r2 = c.post("/api/modules/requests", json={"model": "Relay (D), SKU 26517", "purpose": "relay"}, headers=ho).json()
        assert "request_id" in r2, r2
        uid = app.state.cfg.devices[0].device_uid
        app.state.hist.submit(lambda cn: cn.execute("UPDATE module_requests SET status='AWAITING_APPLICATION', device_uid=? "
                                                    "WHERE request_id=?", (uid, r2["request_id"])), wait=True)
        got = next(x for x in c.get("/api/modules/requests", headers=ho).json()["requests"] if x["request_id"] == r2["request_id"])
        assert got["status"] == "ADDED" and "unassigned and unverified" in got["next"]


def test_schema7_migrates_earlier_event_requests_into_the_workflow(tmp_path):
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    for v in range(1, 7):                                            # a 0.7.0 database (schema 6)
        for stmt in [x for x in MIGRATIONS[v][1].split(";") if x.strip()]:
            con.execute(stmt)
    con.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','6')")
    ev = lambda kind, actor, doc: con.execute("INSERT INTO events(utc, kind, actor, payload_json) VALUES(?,?,?,?)",  # noqa: E731
                                              ("2026-10-26T10:00:00+00:00", kind, actor, json.dumps(doc)))
    ev("MODULE_REGISTRATION_REQUESTED", "oper1", {"model": "IO 8CH", "purpose": "valves", "location": "north"})
    ev("MODULE_REGISTRATION_REQUESTED", "oper1", {"model": "AI 8CH", "purpose": "levels"})
    ev("MODULE_REGISTRATION_CLOSED", "admin1", {"request_id": 2, "outcome": "prepared at 7"})
    assert migrate(con, db) == [7]
    rows = [dict(zip([d[0] for d in cur.description], r)) for cur in [con.execute("SELECT * FROM module_requests ORDER BY request_id")]
            for r in cur.fetchall()]
    assert [(r["request_id"], r["status"], r["model"]) for r in rows] == [(1, "REQUESTED", "IO 8CH"), (2, "CLOSED", "AI 8CH")]
    assert rows[1]["reason"] == "prepared at 7" and rows[1]["handler"] == "admin1" and rows[0]["location"] == "north"
    assert (tmp_path / "backups").is_dir()                            # the migration took its safety copy first
