"""Packaged fixtures must be identical to the repository's configs/ (single source of truth)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_configs_identical():
    for f in (ROOT / "configs").glob("*.yaml"):
        assert (ROOT / "src" / "aquacrop" / "configs" / f.name).read_bytes() == f.read_bytes(), f.name
