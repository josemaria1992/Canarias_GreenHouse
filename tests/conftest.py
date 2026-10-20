import itertools
import os
import sys
from pathlib import Path

import pytest

from aquacrop.config import load_config

ROOT = Path(__file__).resolve().parents[1]
_bus_ids = itertools.count(1)


def pytest_runtest_logreport(report):
    """On GitHub Actions, each failure also becomes an annotation on the check run, so it can be read from
    the PR (and through the checks API) without downloading the job log."""
    if report.failed and os.environ.get("GITHUB_ACTIONS") == "true":
        path, line, _ = report.location
        text = str(report.longreprtext)[-1500:]
        msg = f"{report.nodeid} ({report.when})\n{text}".replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        sys.__stdout__.write(f"\n::error file={path},line={(line or 0) + 1},title=pytest failure::{msg}\n")
        sys.__stdout__.flush()


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


