"""ADR-0016, CP-01: `aquacrop hw scan / setup / make-config` over the in-memory RS485 bus (not hardware)."""
import itertools
import json

import pytest
import yaml

from aquacrop.cli import main
from aquacrop.config.schema import InstallationConfig
from tests.fakes import protocol_aquafake as pf

_n = itertools.count()


@pytest.fixture
def wire(tmp_path, monkeypatch):
    import serial
    if "tests.fakes" not in serial.protocol_handler_packages:
        serial.protocol_handler_packages.append("tests.fakes")
    monkeypatch.setenv("AQUACROP_LOCK_DIR", str(tmp_path / "locks"))
    name = f"setup{next(_n)}"
    bus = pf.bus(name)
    yield bus, f"aquafake://{name}"
    pf.BUSES.pop(name, None)


def run(tmp_path, *argv):
    return main(["hw", *argv, "--state-dir", str(tmp_path / "var")])


def setup(tmp_path, port, *extra, name="relay_A", address=3, model="relay"):
    rec = tmp_path / f"setup-{name}.json"
    code = run(tmp_path, "setup", "--port", port, "--model", model, "--address", str(address), "--name", name,
               "--record", str(rec), "--yes", "--last", "8", *extra)
    return code, json.loads(rec.read_text())


def test_scan_reads_only_and_lists_what_answers(tmp_path, wire, capsys):
    bus, port = wire
    bus.add("relay", 1)
    assert run(tmp_path, "scan", "--port", port, "--last", "4") == 0
    out = json.loads(capsys.readouterr().out)
    assert [(f["address"], f["kind"]) for f in out["found"]] == [(1, "digital")] and out["writes_sent"] == 0
    assert bus.write_frames() == []


def test_setup_gives_a_module_its_address_and_normal_modes(tmp_path, wire):
    bus, port = wire
    relay = bus.add("relay", 1)
    relay.modes[2] = 1                                   # channel 3 in linkage mode, as it might ship
    code, rec = setup(tmp_path, port)
    assert code == 0 and rec["result"] == "DONE"
    assert relay.address == 3 and relay.modes == [0] * 8
    assert rec["before"]["address"] == 1 and rec["after"]["address"] == 3 and rec["after"]["modes"] == [0] * 8
    writes = bus.write_frames()
    assert [w[:6].hex() for w in writes] == ["010610020000", "010640000003"]   # mode, then address; unit 1 only
    assert all(w[0] != 0 for w in bus.frames_in)                                 # never broadcast


def test_setup_refuses_when_two_modules_answer(tmp_path, wire):
    bus, port = wire
    bus.add("relay", 1)
    bus.add("ai", 2)
    code, rec = setup(tmp_path, port)
    assert code == 2 and rec["result"] == "STOPPED" and "connect only the module" in rec["reason"]
    assert bus.write_frames() == []


def test_setup_refuses_the_wrong_model(tmp_path, wire):
    bus, port = wire
    bus.add("ai", 1)
    code, rec = setup(tmp_path, port, model="relay")
    assert code == 2 and "looks analog" in rec["reason"] and bus.write_frames() == []


def test_setup_sets_analog_ranges_and_reminds_about_the_jumpers(tmp_path, wire):
    bus, port = wire
    ai = bus.add("ai", 1)
    code, rec = setup(tmp_path, port, "--ai-range", "all=4-20mA", "--ai-range", "8=0-5V", "--ai-variant", "A",
                      name="analog_A", address=2, model="ai")
    assert code == 0 and ai.modes == [3] * 7 + [0] and ai.address == 2
    assert rec["ai_variant"] == "A" and "jumper" in rec["note"]


def test_a_voltage_range_needs_the_module_version(tmp_path, wire):
    bus, port = wire
    bus.add("ai", 1)
    assert run(tmp_path, "setup", "--port", port, "--model", "ai", "--address", "2", "--name", "analog_A",
               "--ai-range", "1=0-10V", "--yes") == 2
    assert bus.write_frames() == []


def test_firmware_that_answers_from_the_new_address_is_still_verified(tmp_path, wire):
    bus, port = wire
    d = bus.add("digital_io", 1)
    d.answer_address_change_from_new = True
    code, rec = setup(tmp_path, port, name="digital_A", address=5, model="io")
    assert code == 0 and d.address == 5
    addr = [w for w in rec["writes"] if w["register"] == "0x4000"][0]
    assert addr["ok"] and addr["readback_at_new"] == 5 and not addr["answers_at_old"]


def test_nothing_is_written_when_the_module_is_already_set(tmp_path, wire):
    bus, port = wire
    bus.add("relay", 3)
    code, rec = setup(tmp_path, port)
    assert code == 0 and rec["writes"] == [] and bus.write_frames() == []


def test_setup_cannot_run_while_the_port_is_owned(tmp_path, wire):
    from aquacrop.ownership import OwnerLock, lock_name, port_key
    bus, port = wire
    bus.add("relay", 1)
    lk = OwnerLock(tmp_path / "locks" / lock_name("port", port_key(port))).acquire()
    try:
        assert run(tmp_path, "scan", "--port", port) == 4
    finally:
        lk.release()
    assert bus.frames_in == []


def test_make_config_from_the_records(tmp_path, wire):
    bus, port = wire
    recs = []
    for model, kind, name, addr in (("relay", "relay", "relay_A", 1), ("ai", "ai", "analog_A", 2),
                                    ("io", "digital_io", "digital_A", 5)):
        dev = bus.add(kind, 1)
        extra = ("--ai-range", "all=4-20mA") if model == "ai" else ()
        code, _ = setup(tmp_path, port, *extra, name=name, address=addr, model=model)
        assert code == 0
        del bus.devices[addr]                      # take it off the wire before the next one
        recs.append(str(tmp_path / f"setup-{name}.json"))
        assert dev.address == addr
    out = tmp_path / "bench.yaml"
    assert main(["hw", "make-config", *recs, "--digital-port", "/dev/ttyAMA1", "--analog-port", "/dev/ttyAMA2",
                 "--host", "aquacrop-bench.local", "--out", str(out)]) == 0
    cfg = InstallationConfig.model_validate(yaml.safe_load(out.read_text()))
    assert cfg.profile.value == "HARDWARE_READ_ONLY" and [b.port for b in cfg.buses] == ["/dev/ttyAMA1", "/dev/ttyAMA2"]
    assert {d.device_uid: (d.bus_id, d.address) for d in cfg.devices} == {
        "relay_A": ("rs485_a", 1), "analog_A": ("rs485_b", 2), "digital_A": ("rs485_a", 5)}
    assert cfg.device("analog_A").ai[1].mode.value == "3"
    assert cfg.safe_state.outputs_off_on == ["START", "STOP", "LEASE_END", "MODULE_RETURN"]
    assert "https://aquacrop-bench.local" in cfg.security.allowed_origins and cfg.physical_mapping == []


def test_make_config_refuses_a_stopped_record(tmp_path):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"name": "relay_A", "result": "STOPPED"}))
    assert main(["hw", "make-config", str(bad), "--digital-port", "/dev/ttyAMA1", "--out", str(tmp_path / "o.yaml")]) == 2
    assert not (tmp_path / "o.yaml").exists()
