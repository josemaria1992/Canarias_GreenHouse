"""DL-05: the requirement-to-test manifest shipped in the release ZIP comes from docs/ACCEPTANCE.md and
covers the owner's 45 pre-hardware gates exactly once, with honest statuses."""
import importlib.util

import pytest

from tests.conftest import ROOT


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_gate_once_with_a_known_status_and_evidence(build):
    rep = build.acceptance_manifest((ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8"))
    ids = [g["id"] for g in rep["gates"]]
    assert rep["gate_count"] == len(ids) == len(set(ids)) == 45
    assert ids[0] == "PH-01" and ids[-1] == "DL-05"
    for g in rep["gates"]:
        assert g["status"] in ("PASS", "BLOCKED", "NOT_RUN", "NOT_CONFIGURED"), g    # no FAIL in this release
        assert g["evidence"].strip() and g["physical_verification"] is False
    by = {g["id"]: g for g in rep["gates"]}
    # the parts that need the hardware, the account or the real keys are never reported as passed
    for gid, word in (("UI-06", "NOT_RUN"), ("BK-04", "NOT_CONFIGURED"), ("BK-07", "NOT_RUN"), ("DL-02", "NOT_RUN")):
        assert word in [p["status"] for p in by[gid]["parts"]], by[gid]["parts"]
    assert "NOT demonstrated" in by["BK-07"]["evidence"]


def test_a_duplicate_or_missing_gate_or_an_unknown_status_is_refused(build):
    text = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith("| PH-01 "))
    with pytest.raises(ValueError, match="twice"):
        build.acceptance_manifest(text + "\n" + row + "\n")
    with pytest.raises(ValueError, match="missing"):
        build.acceptance_manifest(text.replace(row, ""))
    with pytest.raises(ValueError, match="unknown status"):
        build.acceptance_manifest(text.replace(row, row.replace("| PASS |", "| MOSTLY |", 1)))
