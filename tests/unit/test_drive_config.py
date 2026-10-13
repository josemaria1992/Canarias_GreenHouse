"""Configuration rules for the drive's Modbus interface (ADR-0015) and the site time zone."""
import pytest
from pydantic import ValidationError

from aquacrop.config import load_config
from aquacrop.config.schema import InstallationConfig
from tests.conftest import ROOT
from tests.hardware.test_vfd_modbus import MAP


def hw_data(drive=None, **top):
    import yaml
    data = yaml.safe_load((ROOT / "tests" / "fixtures" / "hw-fake.yaml").read_text(encoding="utf-8"))
    if drive is not None:
        data["drive_adapters"] = [{"adapter_id": "MAIN", "kind": "MODBUS_RTU", "signals": ["drive.MAIN.run"],
                                   "modbus": drive}]
    return data | top


def errors(data) -> str:
    with pytest.raises(ValidationError) as e:
        InstallationConfig.model_validate(data)
    return str(e.value)


def test_proposed_map_is_accepted_on_a_real_profile():
    cfg = InstallationConfig.model_validate(hw_data(MAP))
    assert cfg.drive_target("drive:MAIN").modbus.provenance == "PROPOSED"
    assert cfg.drive_of_adapter("MAIN") == "MAIN" and cfg.drive_target("drive:OTHER") is None


@pytest.mark.parametrize("change, message", [
    ({"provenance": "DEMO"}, "DEMO"),
    ({"provenance": "VERIFIED"}, "evidence"),
    ({"bus_id": "rs485_z"}, "rs485_z"),
    ({"address": 1}, "address"),
    ({"stop_value": 3}, "stop_value"),
    ({"running_mask": 12}, "share a bit"),
    ({"run_sequence": []}, "run_sequence"),
    ({"address": 0}, "address"),
    ({"comm_loss_timeout_s": 0}, "comm_loss_timeout_s"),
    ({"comm_loss_timeout_s": 0.3}, "twice the poll interval"),
])
def test_bad_maps_are_refused(change, message):
    assert message in errors(hw_data(MAP | change))


def test_verified_map_needs_evidence_and_then_loads():
    cfg = InstallationConfig.model_validate(hw_data(MAP | {"provenance": "VERIFIED",
                                                           "evidence": "CP-06 record 2026-10-20, bench"}))
    assert cfg.drive_adapters[0].modbus.provenance == "VERIFIED"


def test_modbus_kind_and_map_go_together():
    d = hw_data()
    d["drive_adapters"] = [{"adapter_id": "MAIN", "kind": "MODBUS_RTU", "signals": ["drive.MAIN.run"]}]
    assert "register map" in errors(d)
    d["drive_adapters"] = [{"adapter_id": "MAIN", "kind": "NOT_CONFIGURED", "signals": ["drive.MAIN.run"],
                            "modbus": MAP}]
    assert "register map" in errors(d)
    d["drive_adapters"] = [{"adapter_id": "MAIN", "kind": "SIMULATED", "signals": ["drive.MAIN.run"]}]
    assert "SIMULATED" in errors(d)


def test_real_profile_needs_a_known_site_time_zone():
    d = hw_data()
    d.pop("site")
    assert "timezone" in errors(d)
    assert "Mars/Olympus" in errors(hw_data(site={"timezone": "Mars/Olympus"}))


@pytest.mark.parametrize("name, zone, kind", [
    ("installation-48", "Atlantic/Canary", "MODBUS_RTU"),
    ("demo-48", "Atlantic/Canary", None),
    ("demo-24", "Atlantic/Canary", None),
    ("hardware-read-only.example", "Atlantic/Canary", "NOT_CONFIGURED"),
    ("bench-malmo.example", "Europe/Stockholm", "NOT_CONFIGURED"),
])
def test_shipped_configs_carry_their_site_zone(name, zone, kind):
    cfg = load_config(ROOT / "configs" / f"{name}.yaml")
    assert cfg.site.timezone == zone
    main = next((a for a in cfg.drive_adapters if a.adapter_id == "MAIN"), None)
    assert (main.kind if main else None) == kind
    if kind == "MODBUS_RTU":
        assert main.modbus.provenance == "DEMO" and cfg.profile.value == "SIMULATED"
