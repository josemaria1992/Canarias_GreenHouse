from pathlib import Path

import pytest

from aquacrop.config import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg48():
    return load_config(ROOT / "configs" / "demo-48.yaml")


@pytest.fixture
def cfg24():
    return load_config(ROOT / "configs" / "demo-24.yaml")


@pytest.fixture
def cfg_data(cfg48):
    return cfg48.model_dump(mode="json")
