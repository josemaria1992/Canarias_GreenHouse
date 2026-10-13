"""DL-06: the running build can be confirmed, and an older environment cannot pass for the new release.

The launchers key their private environment by the wheel's SHA-256 and tell AquaCrop which wheel they
installed; `release_info` checks that against RELEASE.json next to the launcher. A provisional 0.3.1
environment started from a 0.3.2 folder (or the reverse) is a MISMATCH and the launcher's prerequisite
check stops before anything starts. Nothing here deletes an older environment or any state folder.
"""
import json
import re

from aquacrop import __version__, launcher
from aquacrop.release import describe, release_info
from tests.conftest import ROOT

SHA = "a" * 64


def _release(tmp_path, version=__version__, sha=SHA):
    f = tmp_path / "RELEASE.json"
    f.write_text(json.dumps({"version": version, "commit": "0123456789abcdef0123", "built_utc": "2026-10-13T20:00:00Z",
                             "tree_clean": True, "wheel": {"file": "wheels/x.whl", "sha256": sha}}), encoding="utf-8")
    return str(f)


def test_without_a_release_launcher_only_the_package_version_is_known():
    info = release_info({})
    assert info["status"] == "NOT_A_RELEASE" and info["software_version"] == __version__
    assert "not started from a release folder" in describe(info)


def test_the_installed_wheel_matching_release_json_is_verified(tmp_path):
    info = release_info({"AQUACROP_RELEASE_INFO": _release(tmp_path), "AQUACROP_WHEEL_SHA256": SHA})
    assert info["status"] == "VERIFIED" and info["commit"].startswith("0123456789ab")
    assert "source commit 0123456789ab" in describe(info) and "VERIFIED" in describe(info)


def test_an_older_environment_or_another_wheel_is_a_mismatch(tmp_path):
    old = release_info({"AQUACROP_RELEASE_INFO": _release(tmp_path, version="0.3.1"), "AQUACROP_WHEEL_SHA256": SHA})
    assert old["status"] == "MISMATCH" and "0.3.1" in old["detail"]
    other = release_info({"AQUACROP_RELEASE_INFO": _release(tmp_path), "AQUACROP_WHEEL_SHA256": "b" * 64})
    assert other["status"] == "MISMATCH" and "differs" in other["detail"]
    unreported = release_info({"AQUACROP_RELEASE_INFO": _release(tmp_path)})
    assert unreported["status"] == "MISMATCH" and "did not report" in unreported["detail"]
    broken = tmp_path / "bad.json"
    broken.write_text("{", encoding="utf-8")
    assert release_info({"AQUACROP_RELEASE_INFO": str(broken), "AQUACROP_WHEEL_SHA256": SHA})["status"] == "UNREADABLE"


def test_a_mismatch_stops_the_launcher_before_anything_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("AQUACROP_RELEASE_INFO", _release(tmp_path, version="0.3.1"))
    monkeypatch.setenv("AQUACROP_WHEEL_SHA256", SHA)
    _, failed = launcher.prerequisites("installation-80", tmp_path / "state", 0)
    assert any("MISMATCH" in f for f in failed)
    assert not list((tmp_path / "state").glob("*.sqlite3"))


def test_both_launchers_key_the_environment_by_the_wheel_sha256_and_report_it():
    cmd = (ROOT / "launchers" / "AquaCrop-Demo.cmd").read_text(encoding="utf-8")
    sh = (ROOT / "launchers" / "aquacrop-demo.sh").read_text(encoding="utf-8")
    assert 'set "VENV=%LOCAL%\\venv-!TAG!-!WSHA:~0,12!"' in cmd
    assert 'VENV="$LOCAL/venv-$TAG-$(printf %s "$WSHA" | cut -c1-12)"' in sh
    for text in (cmd, sh):
        assert "aquacrop-wheel.sha256" in text and "AQUACROP_WHEEL_SHA256" in text and "AQUACROP_RELEASE_INFO" in text
    # an environment is only removed when its install never finished (no marker); state folders are never touched
    for line in cmd.splitlines() + sh.splitlines():
        if re.search(r"\b(rmdir|rm -rf|del)\b", line):
            assert "VENV" in line and "AquaCrop\\demo" not in line and "state" not in line.lower(), line


def test_a_release_folder_without_its_release_json_stops_the_launcher(tmp_path, monkeypatch):
    # the launchers always pass RELEASE.json for a release wheel; a missing file is not "not a release"
    info = release_info({"AQUACROP_RELEASE_INFO": str(tmp_path / "RELEASE.json"), "AQUACROP_WHEEL_SHA256": SHA})
    assert info["status"] == "MISSING" and "extract the whole ZIP" in info["detail"]
    monkeypatch.setenv("AQUACROP_RELEASE_INFO", str(tmp_path / "RELEASE.json"))
    monkeypatch.setenv("AQUACROP_WHEEL_SHA256", SHA)
    _, failed = launcher.prerequisites("installation-80", tmp_path / "state", 0)
    assert any("MISSING" in f for f in failed)
    cmd = (ROOT / "launchers" / "AquaCrop-Demo.cmd").read_text(encoding="utf-8")
    sh = (ROOT / "launchers" / "aquacrop-demo.sh").read_text(encoding="utf-8")
    assert 'if defined WSHA set "AQUACROP_RELEASE_INFO=%HERE%RELEASE.json"' in cmd
    assert 'if [ -n "$WSHA" ]; then export AQUACROP_RELEASE_INFO="$HERE/RELEASE.json"' in sh
