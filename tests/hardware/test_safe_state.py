"""ADR-0016: the hardware service switches digital outputs OFF at the configured moments (in-memory bus, not
hardware). OFF only, each coil that reads ON, FC05 once with an FC01 readback, audited with origin SAFE_STATE."""
import time

import pytest

from aquacrop.api.hardware import BusWorker, HardwareService
from aquacrop.config.schema import InstallationConfig
from aquacrop.control.bench import CONFIRMATION_PHRASE, BenchRefused
from aquacrop.domain.model import CommandOrigin, OutputCommand, Priority, utcnow
from aquacrop.historian.writer import Historian
from aquacrop.io.modbus_backend import safe_state_policy
from aquacrop.security.auth import Principal, Role

OFF = lambda ch: bytes([1, 5, 0, ch - 1, 0, 0])   # noqa: E731  (FC05 OFF request body, without CRC)


def with_safe_state(cfg, *triggers):
    return InstallationConfig.model_validate(cfg.model_dump(mode="json") | {"safe_state": {"outputs_off_on": list(triggers)}})


def bench_user(session="h1"):
    return Principal("bench1", Role.OPERATOR, True, session, False)


def wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture
def service(tmp_path, fake_bus):
    made = []

    def make(*triggers, start=True):
        cfg, bus, relay, ai = fake_bus
        c = with_safe_state(cfg, *triggers)
        hist = Historian(tmp_path / f"h{len(made)}.sqlite3", c, f"s{len(made)}")
        svc = HardwareService(c, hist, f"s{len(made)}")
        made.append((svc, hist))
        if start:
            svc.start()
            assert wait(lambda: svc.workers["rs485_a"].polls >= 2)
        return svc, bus, relay, hist
    yield make
    for svc, hist in made:
        svc.stop()
        hist.close()


def off_frames(bus):
    return [f[:-2] for f in bus.write_frames() if f[1] == 5 and f[4:6] == b"\x00\x00"]


def safe_events(hist):
    hist.wait_idle()
    return [r[0] for r in hist.query("SELECT payload_json FROM events WHERE kind='SAFE_STATE_OFF' ORDER BY id")]


def test_without_triggers_nothing_is_written_at_start_or_stop(service, fake_bus):
    _, bus, relay, _ = fake_bus
    relay.coils[2] = True
    svc, bus, relay, hist = service()
    svc.stop()
    assert bus.write_frames() == [] and relay.coils[2] is True


def test_start_switches_on_outputs_off_and_audits_them(service, fake_bus):
    _, bus, relay, _ = fake_bus
    relay.coils[2] = relay.coils[6] = True
    svc, bus, relay, hist = service("START")
    assert wait(lambda: not any(relay.coils)) and wait(lambda: len(safe_events(hist)) == 1)
    assert off_frames(bus) == [OFF(3), OFF(7)]            # only the two that read ON, each once
    assert not any(f[1] == 5 and f[4:6] == b"\xff\x00" for f in bus.write_frames())   # never ON
    ev = safe_events(hist)
    assert len(ev) == 1 and '"OFF_CONFIRMED"' in ev[0] and '"START"' in ev[0]
    rows = [tuple(r) for r in hist.query("SELECT target, desired, origin, outcome FROM commands ORDER BY intent_utc")]
    assert rows == [("relay_A.DO3", 0, "SAFE_STATE", "READBACK_CONFIRMED"),
                    ("relay_A.DO7", 0, "SAFE_STATE", "READBACK_CONFIRMED")]


def test_a_module_silent_at_start_is_switched_off_at_its_first_answer(service, fake_bus):
    _, bus, relay, _ = fake_bus
    relay.coils[0], relay.fault = True, "timeout"
    svc, bus, relay, hist = service("START")
    assert relay.coils[0] is True and off_frames(bus) == []
    relay.fault = None
    assert wait(lambda: relay.coils[0] is False)
    assert wait(lambda: len(safe_events(hist)) == 2)
    assert off_frames(bus) == [OFF(1)]
    ev = safe_events(hist)
    assert '"NOT_READ"' in ev[0] and "first answer" in ev[0] and '"OFF_CONFIRMED"' in ev[-1]


def test_stop_switches_outputs_off_before_the_port_closes(service):
    svc, bus, relay, hist = service("STOP")
    relay.coils[4] = True                    # e.g. left ON by a bench test
    svc.stop()
    assert relay.coils[4] is False and off_frames(bus) == [OFF(5)]


def test_lease_end_switches_off_the_leased_outputs_only(service):
    svc, bus, relay, hist = service("LEASE_END")
    p = bench_user()
    lease = svc.arm(p, confirmation=CONFIRMATION_PHRASE, setup_description="isolated bench, lamps only",
                    allowlist=["relay_A.DO1", "relay_A.DO2"], duration_s=120)
    for t in ("relay_A.DO1", "relay_A.DO2"):
        assert svc.bench_command(p, target=t, desired=True, reason="t", lease_id=lease.lease_id).outcome.value == \
            "READBACK_CONFIRMED"
    relay.coils[7] = True                    # not in the lease: not ours to switch
    assert svc.revoke("test done", "bench1")
    assert wait(lambda: relay.coils[0] is False and relay.coils[1] is False)
    assert relay.coils[7] is True and off_frames(bus) == [OFF(1), OFF(2)]


def test_an_expired_lease_is_noticed_by_the_poller_without_a_page_open(service):
    svc, bus, relay, hist = service("LEASE_END")
    p = bench_user()
    lease = svc.arm(p, confirmation=CONFIRMATION_PHRASE, setup_description="isolated bench, lamps only",
                    allowlist=["relay_A.DO1"], duration_s=60)
    svc.bench_command(p, target="relay_A.DO1", desired=True, reason="t", lease_id=lease.lease_id)
    lease.expires_monotonic = time.monotonic()          # as if 60 s had passed
    assert wait(lambda: relay.coils[0] is False)
    assert svc.gate.active() is None


def test_a_module_that_comes_back_has_its_outputs_switched_off(service):
    svc, bus, relay, hist = service("MODULE_RETURN")
    relay.fault = "timeout"
    assert wait(lambda: svc.backend.health("relay_A").online is False)
    relay.coils[3] = True                    # the module kept its relay while the link was down
    relay.fault = None
    assert wait(lambda: relay.coils[3] is False, timeout=10)
    assert wait(lambda: any('"MODULE_RETURN"' in e for e in safe_events(hist)))
    assert off_frames(bus) == [OFF(4)]


def test_a_module_not_in_normal_mode_is_not_written_and_the_result_says_so(service, fake_bus):
    _, bus, relay, _ = fake_bus
    relay.coils[0], relay.modes[0] = True, 1      # channel 1 in linkage mode
    svc, bus, relay, hist = service("START")
    assert wait(lambda: len(safe_events(hist)) == 1)
    ev = safe_events(hist)[0]
    assert '"INCOMPLETE"' in ev and "not all NORMAL" in ev
    assert bus.write_frames() == [] and relay.coils[0] is True


def test_all_outputs_off_on_request_needs_no_lease(service):
    svc, bus, relay, hist = service()
    relay.coils[1] = relay.coils[5] = True
    res = svc.all_off("oper1")
    assert [r["result"] for r in res] == ["OFF_CONFIRMED"] and not any(relay.coils)
    assert off_frames(bus) == [OFF(2), OFF(6)]


def _cmd(target="relay_A.DO1", desired=False, origin=CommandOrigin.SAFE_STATE):
    now = utcnow()
    return OutputCommand("r1", "t", target, desired, "test-1", None, now, now, origin, "t", None, Priority.STOP)


def test_the_safe_state_policy_allows_off_to_a_digital_output_and_nothing_else():
    assert safe_state_policy(_cmd()) is None
    assert "OFF" in safe_state_policy(_cmd(desired=True))
    assert "drives" in safe_state_policy(_cmd(target="drive:MAIN"))
    assert "digital outputs" in safe_state_policy(_cmd(target="relay_A.DI1"))
    assert safe_state_policy(_cmd(origin=CommandOrigin.BENCH)) is not None


def test_a_safe_state_command_cannot_switch_on_through_the_backend(service):
    svc, bus, relay, hist = service()
    r = svc.safe_commands.submit(svc.safe_commands.build(actor="x", target="relay_A.DO1", desired=True,
                                                         origin=CommandOrigin.SAFE_STATE, reason="t"))
    assert r.outcome.value == "REJECTED" and bus.write_frames() == [] and relay.coils[0] is False


def test_safe_state_is_refused_on_the_simulation(cfg24):
    with pytest.raises(ValueError, match="HARDWARE_READ_ONLY profile only"):
        with_safe_state(cfg24, "START")
    with pytest.raises(ValueError, match="once"):
        InstallationConfig.model_validate(cfg24.model_dump(mode="json") | {"safe_state": {"outputs_off_on": ["STOP", "STOP"]}})


def test_the_lease_holder_can_renew_from_the_same_session_only(service):
    svc, bus, relay, hist = service()
    p = bench_user()
    lease = svc.arm(p, confirmation=CONFIRMATION_PHRASE, setup_description="isolated bench, lamps only",
                    allowlist=["relay_A.DO1"], duration_s=60)
    before = lease.expires_monotonic
    assert svc.renew(p, lease.lease_id, 1800).expires_monotonic > before + 1000
    with pytest.raises(BenchRefused, match="another session"):
        svc.renew(bench_user(session="h2"), lease.lease_id, 600)
    with pytest.raises(BenchRefused, match="duration"):
        svc.renew(p, lease.lease_id, 3600)
    hist.wait_idle()
    assert hist.query("SELECT count(*) FROM events WHERE kind='BENCH_LEASE_RENEWED'")[0][0] == 1


def test_a_failed_bus_worker_tries_again_after_a_pause():
    n = {"i": 0}

    def poll(bus):
        n["i"] += 1
        if n["i"] <= 6:
            raise RuntimeError("bus exploded")
    w = BusWorker("b3", 0.01, poll, restart_after_s=0.2)
    w.start()
    assert wait(lambda: w.state == "FAILED")
    assert wait(lambda: w.state == "RUNNING" and w.polls >= 2)
    assert w.health()["restarts"] == 1
    w.stop(lambda c, why: None)


def test_api_all_off_and_renew(tmp_path, fake_bus):
    from fastapi.testclient import TestClient

    from aquacrop.api.app import create_app
    from tests.hardware.test_api_hardware import ORIGIN, PW, arm_body, login
    cfg, bus, relay, ai = fake_bus
    app = create_app(cfg, state_dir=tmp_path, bind_host="127.0.0.1")
    acc = app.state.accounts
    acc.create_user("bench1", PW["bench1"], Role.OPERATOR, commissioning=True)
    acc.create_user("oper1", PW["oper1"], Role.OPERATOR)
    with TestClient(app, base_url=ORIGIN) as c:
        assert wait(lambda: app.state.svc.workers["rs485_a"].polls >= 2)
        h = login(c, "bench1")
        lease = c.post("/api/bench/lease", json=arm_body(duration_s=60), headers=h).json()
        r = c.post("/api/bench/lease/renew", json={"lease_id": lease["lease_id"], "duration_s": 1200}, headers=h)
        assert r.status_code == 200 and r.json()["remaining_s"] > 1000
        relay.coils[2] = True
        c.post("/api/auth/logout", headers=h)
        h2 = login(c, "oper1")                      # an operator without commissioning may switch all OFF
        assert c.post("/api/bench/lease/renew", json={"lease_id": lease["lease_id"], "duration_s": 600},
                      headers=h2).status_code == 403
        r = c.post("/api/bench/all-off", headers=h2)
        assert r.status_code == 200 and r.json()["results"][0]["result"] == "OFF_CONFIRMED" and relay.coils[2] is False
        b = c.get("/api/bench").json()
        assert b["safe_state"]["outputs_off_on"] == [] and b["safe_state"]["last"]["trigger"] == "OPERATOR"
        assert b["poll_s"]["rs485_a"] > 0
