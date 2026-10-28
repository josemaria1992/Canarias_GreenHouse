"""0.7.0 role matrix (UI_UX_SPECIFICATION, ADR-0023): exact navigation per role and backend enforcement.
RBAC-01/02/03 through the real app; hidden navigation is never the only protection.

0.7.1 (owner decisions 26 October 2026, ADR-0023 amended): Equipment is a page of its own for operators; the module
address wizard is for administrators and operators with commissioning (MOD-01); other operators send requests."""
import re

import pytest
from fastapi.testclient import TestClient

from aquacrop.security.auth import NAVIGATION, Perm, Principal, Role
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)

VIEWER_READS = {"/api/auth/session", "/api/status", "/api/layout", "/api/plant", "/api/plant/reading", "/api/twin",
                "/api/controller", "/api/processes", "/api/alarms", "/api/operation", "/api/programs", "/api/health",
                "/api/version", "/api/auth/logout", "/api/auth/login", "/api/auth/password", "/api/auth/ticket",
                "/api/overview/diagram"}


def test_rbac01_navigation_is_exactly_the_specified_pages(tmp_path, icfg):
    # owner, 28 Oct 2026: operators open Equipment types too (drafting and requests); activation stays administrator
    assert NAVIGATION["operator"] == ["overview", "equipment", "equipment-types", "blocks", "twin", "hw-add", "programs",
                                      "history", "alarms"]
    assert NAVIGATION["viewer"] == ["overview", "programs", "alarms"]
    assert set(NAVIGATION["operator"]) < set(NAVIGATION["admin"])
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        for user, role in (("view1", "viewer"), ("oper1", "operator"), ("admin1", "admin")):
            h = login(c, user)
            assert c.get("/api/auth/session", headers=h).json()["navigation"] == NAVIGATION[role]


def test_rbac02_a_viewer_reads_only_overview_programs_and_alarms(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "view1")
        checked = 0
        for r in app.routes:
            path = getattr(r, "path", "")
            if not path.startswith("/api/") or "GET" not in getattr(r, "methods", set()):
                continue
            url = re.sub(r"\{[^}]+\}", "x", path)
            got = c.get(url, headers=h).status_code
            if path in VIEWER_READS:
                assert got != 403, path
            else:
                assert got in (401, 403, 404, 422), (path, got)
                if got == 422:          # parameter validation ran first: ask again with a valid-looking request
                    assert c.get(url + "?points=x&point=x&signal=x", headers=h).status_code in (403, 404, 422), path
            checked += 1
        assert checked > 30
        for path in ("/api/events", "/api/export.csv", "/api/trends?points=x", "/api/equipment", "/api/settings",
                     "/api/twin-link", "/api/commissioning", "/api/discovery", "/api/bench", "/api/twin-changes"):
            assert c.get(path, headers=h).status_code == 403, path
        assert c.post("/api/twin-link/ticket", headers=h).status_code == 403
        assert c.post("/api/processes/IRRIGATION/state", json={"target": "MANUAL"}, headers=h).status_code == 403
        assert c.post("/api/alarms/acknowledge", json={"rule_id": "x", "target": "y"}, headers=h).status_code in (403, 404, 405)
        for ok in ("/api/status", "/api/alarms", "/api/programs", "/api/processes"):
            assert c.get(ok, headers=h).status_code == 200, ok


def test_rbac02_operator_and_admin_reads(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "oper1")
        for ok in ("/api/events", "/api/equipment", "/api/twin-changes", "/api/commissioning", "/api/discovery"):
            assert c.get(ok, headers=h).status_code == 200, ok
        for admin_only in ("/api/settings", "/api/backup", "/api/retention", "/api/users/roles"):
            assert c.get(admin_only, headers=h).status_code == 403, admin_only
        h = login(c, "admin1")
        for ok in ("/api/settings", "/api/backup", "/api/users/roles"):
            assert c.get(ok, headers=h).status_code == 200, ok


def test_mod01_module_setup_is_for_administrators_and_commissioning_operators():
    op = Principal("bench", Role.OPERATOR, True, "x", False)
    admin = Principal("vicente", Role.ADMIN, False, "y", False)
    assert op.can(Perm.MODULE_SETUP) and admin.can(Perm.MODULE_SETUP)
    assert op.can(Perm.MODULE_REGISTER) and op.can(Perm.IO_ASSIGN) and op.can(Perm.BENCH_ARM)
    plain = Principal("op", Role.OPERATOR, False, "z", False)
    assert plain.can(Perm.MODULE_REGISTER) and not plain.can(Perm.MODULE_SETUP)
    assert not plain.can(Perm.IO_ASSIGN) and not plain.can(Perm.BENCH_ARM)
    viewer = Principal("v", Role.VIEWER, True, "w", False)            # a flag on a viewer gives nothing
    assert not viewer.can(Perm.MODULE_SETUP) and not viewer.can(Perm.MODULE_REGISTER)


def test_mod01_wizard_routes_are_enforced_on_the_server_by_role_and_flag(tmp_path, icfg):
    """A commissioning operator passes the permission (the simulation then says it has no USB adapter); an
    operator without the flag is refused 403 before anything else; a flag removed later counts at once."""
    app = _app(tmp_path, icfg)
    app.state.accounts.create_user("bench9", "bench password 999", Role.OPERATOR, commissioning=True)
    from tests.integration import test_api_installation as t
    t.PW["bench9"] = "bench password 999"
    body = {"adapter": "/dev/ttyUSB0", "bus_id": "rs485_a", "read": True}
    with TestClient(app, base_url=ORIGIN) as c:
        h = login(c, "bench9")
        r = c.post("/api/commissioning/open", json=body, headers=h)
        assert r.status_code == 409 and "hardware profile" in r.text, r.text
        app.state.accounts.set_role("bench9", Role.OPERATOR, False)    # commissioning revoked: that session ends
        assert c.post("/api/commissioning/open", json=body, headers=h).status_code in (401, 403)   # session ended
        h2 = login(c, "oper1")
        for path, b in (("/api/commissioning/open", body), ("/api/commissioning/x/reserve", {"address": 9}),
                        ("/api/commissioning/x/commit", {"confirm": True})):
            assert c.post(path, json=b, headers=h2).status_code == 403, path


def test_mod02_an_operator_request_is_a_record_never_configuration(tmp_path, icfg):
    app = _app(tmp_path, icfg)
    devices = [d.model_dump() for d in app.state.cfg.devices]
    with TestClient(app, base_url=ORIGIN) as c:
        hv = login(c, "view1")
        req = {"model": "WAVESHARE_MODBUS_RTU_IO_8CH", "purpose": "two more sector valves", "location": "north bench"}
        assert c.post("/api/modules/requests", json=req, headers=hv).status_code == 403
        ho = login(c, "oper1")
        r = c.post("/api/modules/requests", json=req, headers=ho)
        assert r.status_code == 200 and "Nothing changed yet" in r.json()["next"]
        listed = c.get("/api/modules/requests", headers=ho).json()["requests"]
        assert len(listed) == 1 and listed[0]["by"] == "oper1" and not listed[0]["closed"]
        assert listed[0]["purpose"] == "two more sector valves"
        rid = listed[0]["request_id"]
        assert c.post("/api/modules/requests/close", json={"request_id": rid, "outcome": "prepared"},
                      headers=ho).status_code == 403             # preparing a module is an administrator's task
        ha = login(c, "admin1")
        assert c.post("/api/modules/requests/close", json={"request_id": rid, "outcome": "prepared at address 7"},
                      headers=ha).status_code == 200
        assert c.get("/api/modules/requests", headers=ho).json()["requests"][0]["closed"]
    assert [d.model_dump() for d in app.state.cfg.devices] == devices   # the configuration never changed
