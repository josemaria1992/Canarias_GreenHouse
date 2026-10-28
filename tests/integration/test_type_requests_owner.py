"""Owner decisions, 28 October 2026: a need the equipment type form cannot meet is sent to the operators, who review it
and approve or reject it; operators open the Equipment types page. A request is an audited record: approving it changes
no type, item, program or output, and activation stays with administrators."""
from fastapi.testclient import TestClient

from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)


def test_a_request_goes_to_the_operators_who_approve_or_reject_it_and_nothing_else_changes(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as ca, TestClient(app, base_url=ORIGIN) as co, \
            TestClient(app, base_url=ORIGIN) as cv:
        ha, ho, hv = login(ca, "admin1"), login(co, "oper1"), login(cv, "view1")
        assert "equipment-types" in co.get("/api/auth/session", headers=ho).json()["navigation"]
        before = ca.get("/api/equipment", headers=ha).json()
        assert cv.post("/api/equipment/type-requests", json={"need": "reverse the pump"}, headers=hv).status_code == 403
        r = co.post("/api/equipment/type-requests", headers=ho, json={
            "need": "A second output to reverse the pump for back-flushing the filter.", "what": "output",
            "type_name": "Irrigation pump"})
        assert r.status_code == 200, r.text
        rid = r.json()["request_id"]
        assert "Sent to the operators" in r.json()["next"]
        second = co.post("/api/equipment/type-requests", json={"need": "A dew point reading"}, headers=ho).json()["request_id"]
        # every operator and administrator sees the requests and may review them
        for c, h in ((co, ho), (ca, ha)):
            v = c.get("/api/equipment/type-requests", headers=h).json()
            assert v["can_review"] is True and "changes no equipment type" in v["meaning"]
            assert [x["request_id"] for x in v["requests"]][:2] == [second, rid]
            assert {x["status"] for x in v["requests"]} == {"OPEN"}
        assert cv.post("/api/equipment/type-requests/review", headers=hv,
                       json={"request_id": rid, "decision": "APPROVED"}).status_code == 403
        assert co.post("/api/equipment/type-requests/review", headers=ho,
                       json={"request_id": second, "decision": "REJECTED", "note": ""}).status_code == 422  # say why
        ok = co.post("/api/equipment/type-requests/review", headers=ho,
                     json={"request_id": rid, "decision": "APPROVED", "note": "needed for back-flushing"})
        assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
        again = ca.post("/api/equipment/type-requests/review", headers=ha, json={"request_id": rid, "decision": "REJECTED",
                                                                                   "note": "changed my mind"})
        assert again.status_code == 409                                  # one decision per request
        assert ca.post("/api/equipment/type-requests/review", headers=ha,
                       json={"request_id": 999999, "decision": "APPROVED"}).status_code == 404
        x = next(x for x in co.get("/api/equipment/type-requests", headers=ho).json()["requests"] if x["request_id"] == rid)
        assert x["status"] == "APPROVED" and x["reviewer"] == "oper1" and x["note"] == "needed for back-flushing"
        assert x["by"] == "oper1" and x["type_name"] == "Irrigation pump"
        # nothing about the equipment changed, and an operator still cannot activate a type
        after = ca.get("/api/equipment", headers=ha).json()
        assert after["types"] == before["types"] and after["items"] == before["items"]
        assert co.post("/api/equipment/types/activate", json={"type_id": "pump", "version": 1}, headers=ho).status_code == 403
