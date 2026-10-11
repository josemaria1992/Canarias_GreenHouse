"""`aquacrop diagnostics read` is intrinsically read-only (fake serial bus, not hardware)."""
import inspect
import json

import yaml

from aquacrop import cli
from aquacrop.drivers import diagnostics


def test_diagnostics_read_sends_no_writes(fake_bus, tmp_path, capsys):
    cfg, bus, relay, ai = fake_bus
    relay.modes[3] = 2
    p = tmp_path / "hw.yaml"
    p.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    rc = cli.main(["diagnostics", "read", "--config", str(p), "--state-dir", str(tmp_path), "--cycles", "2",
                   "--interval", "0.1", "--metadata"])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 0
    relay_rec = next(x for x in lines if x.get("device_uid") == "relay_A")
    assert relay_rec["relay_modes"][3] == "TOGGLE" and relay_rec["bench_preflight"].startswith("BLOCKED")
    assert "metadata_unverified" in relay_rec
    assert lines[-1]["summary"]["writes_sent"] == 0
    assert bus.write_frames() == [] and len(bus.frames_in) > 0


def test_diagnostics_reports_failures_per_device(fake_bus, tmp_path, capsys):
    cfg, bus, relay, ai = fake_bus
    ai.fault = "timeout"
    p = tmp_path / "hw.yaml"
    p.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    rc = cli.main(["diagnostics", "read", "--config", str(p), "--state-dir", str(tmp_path)])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 1
    assert next(x for x in lines if x.get("device_uid") == "relay_A")["ok"] is True
    assert next(x for x in lines if x.get("device_uid") == "analog_A")["ok"] is False


def test_diagnostics_refuses_simulation_config(capsys):
    assert cli.main(["diagnostics", "read", "--config", "demo-48"]) == 2


def test_diagnostics_source_has_no_write_call():
    src = inspect.getsource(diagnostics)
    assert "write_coil" not in src and "write_single_coil" not in src and "write_register" not in src
