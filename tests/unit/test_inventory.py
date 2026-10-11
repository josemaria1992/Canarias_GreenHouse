"""AT-INV-01..04: inventory, identities, profiles."""
import copy

import pytest
from pydantic import ValidationError

from aquacrop.config import InstallationConfig, load_config
from aquacrop.domain.model import PointId, PointKind
from tests.conftest import ROOT


def test_demo48_has_48_unique_points(cfg48):
    pts = [str(p) for p in cfg48.all_points()]
    assert len(pts) == 48 == len(set(pts)) == cfg48.expected_point_count()
    assert "relay_B.DI8" in pts and "analog_B.AI5" in pts and "relay_A.DO1" in pts


def test_demo24_has_24_points(cfg24):
    assert len(cfg24.all_points()) == 24 == cfg24.expected_point_count()


def test_arbitrary_module_mix_without_code_change(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["devices"].append({"device_uid": "analog_C", "bus_id": "bus_sim_1", "address": 5,
                         "model": "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH", "sku": "25821"})
    d["devices"].append({"device_uid": "relay_C", "bus_id": "bus_sim_1", "address": 6,
                         "model": "WAVESHARE_MODBUS_RTU_RELAY_D", "sku": "26517"})
    c = InstallationConfig.model_validate(d)
    assert len(c.all_points()) == 8 * 3 + 16 * 3 == c.expected_point_count()


def test_point_id_contract():
    p = PointId.parse("analog_B.AI5")
    assert (p.device_uid, p.kind, p.channel, p.register_address) == ("analog_B", PointKind.AI, 5, 4)
    for bad in ("relay_A.DO9", "relay_A.DO0", "AI1", "relay_A.AO1"):
        with pytest.raises(ValueError):
            PointId.parse(bad)


def test_duplicate_address_same_bus_rejected(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["devices"][1]["address"] = d["devices"][0]["address"]
    with pytest.raises(ValidationError, match="already used"):
        InstallationConfig.model_validate(d)


def test_same_address_different_bus_ok(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["buses"].append({"bus_id": "bus_sim_2", "kind": "virtual"})
    d["devices"][2]["bus_id"] = "bus_sim_2"
    d["devices"][2]["address"] = d["devices"][0]["address"]
    InstallationConfig.model_validate(d)


@pytest.mark.parametrize("addr", [0, 248, 255])
def test_reserved_addresses_rejected(cfg_data, addr):
    d = copy.deepcopy(cfg_data)
    d["devices"][0]["address"] = addr
    with pytest.raises(ValidationError, match="unicast"):
        InstallationConfig.model_validate(d)


def test_sku_must_match_model(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["devices"][0]["sku"] = "25821"
    with pytest.raises(ValidationError, match="SKU"):
        InstallationConfig.model_validate(d)


@pytest.mark.parametrize("profile", ["COMMISSIONED_LIVE_CONTROL", "BENCH_MANUAL_WRITE"])
def test_reserved_or_ephemeral_profiles_refused(cfg_data, profile):
    d = copy.deepcopy(cfg_data)
    d["profile"] = profile
    with pytest.raises(ValidationError):
        InstallationConfig.model_validate(d)


def test_simulated_profile_refuses_rtu_bus(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["buses"][0] = {"bus_id": "bus_sim_1", "kind": "rtu", "port": "/dev/ttyX"}
    with pytest.raises(ValidationError, match="virtual buses only"):
        InstallationConfig.model_validate(d)


def test_demo_values_never_load_into_real_profile(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["profile"] = "HARDWARE_READ_ONLY"
    d["buses"][0] = {"bus_id": "bus_sim_1", "kind": "rtu", "port": "/dev/ttyX"}
    with pytest.raises(ValidationError) as e:
        InstallationConfig.model_validate(d)
    msg = str(e.value)
    assert "DEMO scaling" in msg and "virtual_mapping" in msg and "DEMO twin" in msg


def test_hardware_example_is_read_only_and_unscaled():
    c = load_config(ROOT / "configs" / "hardware-read-only.example.yaml")
    assert c.profile.value == "HARDWARE_READ_ONLY"
    assert not c.virtual_mapping and not c.physical_mapping and not c.scaling_profiles and c.twin is None


def test_physical_mapping_must_stay_empty(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["physical_mapping"] = [{"point": "relay_A.DO1", "signal": "pump.P1.cmd"}]
    with pytest.raises(ValidationError, match="physical_mapping"):
        InstallationConfig.model_validate(d)


def test_two_points_cannot_command_same_actuator(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["virtual_mapping"].append({"point": "relay_B.DO1", "signal": "pump.P1.cmd"})
    with pytest.raises(ValidationError, match="commanded by both"):
        InstallationConfig.model_validate(d)


def test_scaling_unit_must_match_mode(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["devices"][1]["ai"]["1"]["mode"] = "0"  # voltage mV, scaling is uA
    with pytest.raises(ValidationError, match="incompatible"):
        InstallationConfig.model_validate(d)


def test_zero_denominator_scaling_rejected(cfg_data):
    d = copy.deepcopy(cfg_data)
    d["scaling_profiles"][0]["raw_high"] = d["scaling_profiles"][0]["raw_low"]
    with pytest.raises(ValidationError, match="zero denominator"):
        InstallationConfig.model_validate(d)
