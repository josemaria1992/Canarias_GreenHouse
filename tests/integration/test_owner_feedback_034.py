"""0.3.4: the reviewer's corrections R33-02 (recipe field errors), R33-03 (reset wording after a fault the
reset checks cannot see) and the diagram's reading request (O-04), through the real app and runtime."""
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from aquacrop.control.recipes import demo_preset, form_schema, integer_paths
from aquacrop.domain.model import ControllerState as CS
from aquacrop.twin.runtime import make_runtime
from tests.integration.test_api_installation import ORIGIN, _app, icfg, login  # noqa: F401  (fixture)


# ------------------------------------------------------------------------------------------ R33-02
def test_r33_02_integer_fields_are_marked_in_the_form():
    ints = integer_paths()
    assert ints == {"acid.max_no_response"}
    f = {x["path"]: x for x in form_schema({"MAIN": 20000, "SUMP": 5000, "A": 5000, "B": 5000, "ACID": 5000})}
    assert f["acid.max_no_response"]["integer"] is True and f["acid.dose_step_L"]["integer"] is False


def _draft(c, h, **kw):
    body = demo_preset(["S1", "S2"]).body_json()
    for path, v in kw.pop("set", {}).items():
        a, b = path.split(".")
        body[a][b] = v
    return c.post("/api/recipes/draft", json={"recipe_id": "site", "name": "Site", "body": body} | kw, headers=h)


def test_r33_02_a_fractional_count_goes_back_to_its_field_and_nothing_is_saved(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "oper1")
        before = c.get("/api/recipes", headers=h).json()["recipes"]
        r = _draft(c, h, set={"acid.max_no_response": 1.5})
        assert r.status_code == 422
        d = r.json()["detail"]
        assert d["fields"] == [{"path": "acid.max_no_response", "message": "Enter a whole number, without decimals.",
                                "type": "int_from_float"}]
        assert "beside its field" in d["message"]
        assert c.get("/api/recipes", headers=h).json()["recipes"] == before          # no draft was saved
        assert _draft(c, h, set={"acid.max_no_response": 2.0}).status_code == 200     # 2.0 is a whole number


def test_r33_02_wrong_kinds_and_a_bad_recipe_id_each_name_their_field(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "oper1")
        r = _draft(c, h, set={"acid.ph_target": "six", "main.low_low_L": None, "refill.endpoint_mode": "SOMETIMES"})
        got = {x["path"]: x["message"] for x in r.json()["detail"]["fields"]}
        assert r.status_code == 422 and got == {"acid.ph_target": "Enter a number.",
                                                "refill.endpoint_mode": "Choose one of the options."}
        r = _draft(c, h, recipe_id="Summer 2027")
        assert r.status_code == 422
        f = r.json()["detail"]["fields"]
        assert [x["path"] for x in f] == ["recipe_id"] and f[0]["message"].startswith("Use 1 to 48 characters")
        r = _draft(c, h, name="")
        assert r.status_code == 422 and r.json()["detail"]["fields"][0]["path"] == "name"
        # other requests keep FastAPI's own answer
        r = c.post("/api/processes/IRRIGATION/state", json={"target": "SOMETIMES"}, headers=h)
        assert r.status_code == 422 and isinstance(r.json()["detail"], list)


def test_r33_02_an_out_of_range_value_is_a_check_problem_at_its_field(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "oper1")
        r = _draft(c, h, set={"acid.max_no_response": 11})
        assert r.status_code == 200
        v = c.post(f"/api/recipes/site/{r.json()['version']}/validate", headers=h).json()
        assert v["status"] == "DRAFT"
        assert [i["path"] for i in v["issues"]] == ["acid.max_no_response"]


# ------------------------------------------------------------------------------------------ R33-03
def _locked_by_stuck_valve(icfg):
    rt = make_runtime(icfg)
    rt.set_actuator_fault("op", "valve", "S1", "STUCK_CLOSED")
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(40)
    irr = rt.sup.by_id["IRRIGATION"]
    assert irr.state == CS.FAULT_LOCKOUT and irr.fault_reason == "VALVE_OPEN_TIMEOUT"
    return rt, irr


def test_r33_03_a_valve_that_did_not_open_is_not_reported_as_repaired(icfg):
    rt, irr = _locked_by_stuck_valve(icfg)
    v = irr.view()
    # the reset checks pass (they cannot see a valve that does not open while everything is stopped) ...
    assert v["fault_present"] is None and v["fault_checkable"] is False
    occ = rt.alarms.get(irr.fault_occurrence_id)
    rt.acknowledge("op", occ.rule_id, occ.target)
    assert irr.view()["reset_blocker"] is None
    # ... and the controller's checks know nothing of the twin's fault injection
    assert not any("STUCK" in str(x) or "twin" in str(x).lower() for x in irr.view()["permissives"].values())
    assert irr.state == CS.FAULT_LOCKOUT                                   # seen is not cleared
    assert rt.process_reset("op", "IRRIGATION")[0] and irr.state == CS.STOPPED   # cleared is not started
    rt.run_for(20)
    assert irr.state == CS.STOPPED and not irr.on
    # the valve is still stuck in the twin: the cause shows itself again only at the deliberate restart
    assert rt.process_request("op", "IRRIGATION", CS.AUTO)[0]
    rt.run_for(40)
    assert irr.state == CS.FAULT_LOCKOUT and irr.fault_reason == "VALVE_OPEN_TIMEOUT"


def test_r33_03_a_drive_fault_is_one_the_checks_can_see(icfg):
    rt = make_runtime(icfg)
    irr = rt.sup.by_id["IRRIGATION"]
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(30)
    rt.set_actuator_fault("op", "drive", "MAIN", "TRIP")
    rt.run_for(15)
    v = irr.view()
    assert v["fault_reason"] == "DRIVE_FAULT" and v["fault_checkable"] is True
    assert "drive fault input still active" in v["fault_present"]
    rt.set_actuator_fault("op", "drive", "MAIN", None)
    rt.run_for(10)
    v = irr.view()
    assert v["fault_present"] is None and v["fault_checkable"] is True    # the program no longer sees it
    assert make_runtime(icfg).sup.by_id["IRRIGATION"].view()["fault_checkable"] is None   # nothing locked out


@pytest.mark.parametrize("device, checkable", [("analog_B", False), ("analog_A", True)])
def test_r33_03_a_lost_reading_is_checkable_only_if_the_reset_checks_read_it(icfg, device, checkable):
    # analog_B carries sector 1's flow, read only while sector 1 runs; analog_A the main tank level, always read
    rt = make_runtime(icfg)
    irr = rt.sup.by_id["IRRIGATION"]
    rt.process_request("op", "IRRIGATION", CS.AUTO)
    rt.run_for(40)
    rt.set_device_fault("op", device, "TIMEOUT")
    rt.run_for(20)
    rt.set_device_fault("op", device, None)
    rt.run_for(10)
    v = irr.view()
    assert v["fault_reason"] == "OBSERVATION_UNAVAILABLE" and v["fault_present"] is None
    assert v["fault_checkable"] is checkable


# ------------------------------------------------------------------------------------------ O-04 readings
def test_o04_a_requested_reading_is_the_next_acquisition_with_its_own_time(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "view1")
        c.get("/api/plant", headers=h)
        time.sleep(1.5)
        r = c.get("/api/plant/reading", params={"signal": "tank.MAIN.level"}, headers=h).json()
        assert r["bound"] and r["fresh"] is True and r["point"]
        asked, got = datetime.fromisoformat(r["requested_utc"]), datetime.fromisoformat(r["sample"]["acquired_utc"])
        assert got > asked                                    # acquired after the request, not a cached reading
        assert r["sample"]["unit"] == "L" and r["sample"]["quality"] in ("GOOD", "OVERRIDDEN")
        # the time shown is the sample's own: the plant view reports the same sample with the same time
        pv = c.get("/api/plant", headers=h).json()["signals"]["tank.MAIN.level"]["sample"]
        assert datetime.fromisoformat(pv["acquired_utc"]) >= got


def test_o04_without_a_new_acquisition_the_newest_reading_comes_with_its_real_age(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        ha = login(c, "admin1")
        time.sleep(1.5)
        assert c.post("/api/twin/control", json={"action": "acq_stall"}, headers=ha).status_code == 200
        time.sleep(1.0)
        r = c.get("/api/plant/reading", params={"signal": "main.ph", "wait_s": 1.5}, headers=ha).json()
        assert r["fresh"] is False and r["sample"] is not None
        asked, got = datetime.fromisoformat(r["requested_utc"]), datetime.fromisoformat(r["sample"]["acquired_utc"])
        assert got <= asked and r["sample"]["age_s"] >= 1.5
        c.post("/api/twin/control", json={"action": "acq_resume"}, headers=ha)


def test_o04_reading_requests_for_unknown_or_unmeasured_signals(tmp_path, icfg):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "view1")
        assert c.get("/api/plant/reading", params={"signal": "nope.x"}, headers=h).status_code == 404
        sig = c.get("/api/plant", headers=h).json()["signals"]
        unbound = next(k for k, v in sig.items() if v["binding"] == "UNBOUND")
        r = c.get("/api/plant/reading", params={"signal": unbound, "wait_s": 0}, headers=h).json()
        assert r["bound"] is False and r["sample"] is None and r["why"] == "UNBOUND"
        c.cookies.clear()
        assert c.get("/api/plant/reading", params={"signal": "main.ph"}).status_code == 401   # signed-in users only


@pytest.mark.parametrize("wait", [-1, 6])
def test_o04_reading_wait_is_bounded(tmp_path, icfg, wait):
    with TestClient(_app(tmp_path, icfg), base_url=ORIGIN) as c:
        h = login(c, "view1")
        assert c.get("/api/plant/reading", params={"signal": "main.ph", "wait_s": wait}, headers=h).status_code == 422
