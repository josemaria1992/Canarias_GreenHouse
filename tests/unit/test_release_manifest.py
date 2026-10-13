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
    # and a gate whose physical part did not run is not a PASS, although its software part passed
    for g in rep["gates"]:
        assert (g["status"] == "PASS") == all(p["status"] == "PASS" for p in g["parts"]), g
    assert by["OP-06"]["status"] == "NOT_RUN" and by["OP-06"]["parts"][0] == {"status": "PASS", "scope": "desktop"}
    assert sum(rep["status_counts"].values()) == 45
    text = (ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    assert f"({rep['status_counts']['PASS']} of 45 in this release)" in " ".join(text.split())
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


def test_wheelhouse_lock_decides_markers_for_the_target_platform(build):
    """`pip download --platform` evaluates markers on the build machine; the wheelhouse lock decides them for
    the target, so the Windows wheelhouse carries tzdata (sys_platform == 'win32') and the CM4 one does not."""
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    win, cm4 = build.lock_for(text, "win_amd64", "3.12"), build.lock_for(text, "aarch64", "3.11")
    assert "\ntzdata==" in win and "tzdata" not in cm4.replace("# via", "")
    assert ";" not in "".join(ln for ln in win.splitlines() if not ln.lstrip().startswith("#"))
    entry = win[win.index("\ntzdata=="):].split("\n")
    assert entry[1].endswith(" \\") and entry[2].lstrip().startswith("--hash=sha256:")     # pin and hashes kept
    for out in (win, cm4):
        pins = [ln.split("==")[0] for ln in out.splitlines() if ln and not ln[0].isspace() and not ln.startswith("#")]
        assert "fastapi" in pins and "pycparser" in pins and len(pins) == len(set(pins))
