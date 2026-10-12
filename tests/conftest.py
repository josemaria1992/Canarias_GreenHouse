import itertools
from pathlib import Path

import pytest

from aquacrop.config import load_config

ROOT = Path(__file__).resolve().parents[1]
_bus_ids = itertools.count(1)


@pytest.fixture
def cfg48():
    return load_config(ROOT / "configs" / "demo-48.yaml")


@pytest.fixture
def cfg24():
    return load_config(ROOT / "configs" / "demo-24.yaml")


@pytest.fixture
def cfg_data(cfg48):
    return cfg48.model_dump(mode="json")


@pytest.fixture
def fake_bus(request):
    """A fresh in-memory RS485 bus with relay_A@1 and analog_A@2, plus a config pointed at it."""
    import serial
    import yaml

    from aquacrop.config.schema import InstallationConfig
    from tests.fakes import protocol_aquafake as pf

    if "tests.fakes" not in serial.protocol_handler_packages:
        serial.protocol_handler_packages.append("tests.fakes")
    name = f"b{next(_bus_ids)}"
    bus = pf.bus(name)
    relay, ai = bus.add("relay", 1), bus.add("ai", 2)
    data = yaml.safe_load((ROOT / "tests" / "fixtures" / "hw-fake.yaml").read_text(encoding="utf-8"))
    data["buses"][0]["port"] = f"aquafake://{name}"
    cfg = InstallationConfig.model_validate(data)
    yield cfg, bus, relay, ai
    pf.BUSES.pop(name, None)


