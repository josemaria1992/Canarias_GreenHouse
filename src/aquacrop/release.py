"""Which release is running: the version of the installed package and, when started by a release launcher,
the release folder's RELEASE.json (source commit, build time, wheel SHA-256) checked against the wheel the
launcher installed. Shown by `aquacrop launch` and on Settings, so the owner can confirm the build.

The launchers pass two environment variables: AQUACROP_RELEASE_INFO (path of RELEASE.json next to the
launcher) and AQUACROP_WHEEL_SHA256 (SHA-256 of the wheel they installed into this environment). Without
them (a source checkout, `serve`, the controller) only the package version is known.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import __version__


def release_info(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    out: dict = {"software_version": __version__, "release_file": None, "release_version": None,
                 "commit": None, "built_utc": None, "tree_clean": None, "wheel_sha256": env.get("AQUACROP_WHEEL_SHA256") or None,
                 "status": "NOT_A_RELEASE", "detail": "started from a source checkout or without a release launcher"}
    path = env.get("AQUACROP_RELEASE_INFO") or ""
    if not path:
        return out
    out["release_file"] = path
    try:
        info = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        out |= {"status": "UNREADABLE", "detail": f"RELEASE.json could not be read: {e}"}
        return out
    expected = (info.get("wheel") or {}).get("sha256")
    out |= {"release_version": info.get("version"), "commit": info.get("commit"), "built_utc": info.get("built_utc"),
            "tree_clean": info.get("tree_clean")}
    problems = []
    if info.get("version") != __version__:
        problems.append(f"RELEASE.json is version {info.get('version')} but the installed package is {__version__}")
    if not out["wheel_sha256"]:
        problems.append("the launcher did not report the installed wheel's SHA-256")
    elif expected != out["wheel_sha256"]:
        problems.append("the installed wheel's SHA-256 differs from RELEASE.json")
    if problems:
        out |= {"status": "MISMATCH", "detail": "; ".join(problems)}
    else:
        out |= {"status": "VERIFIED", "detail": "installed wheel matches RELEASE.json (version and SHA-256)"}
    return out


def describe(info: dict) -> str:
    if info["status"] == "NOT_A_RELEASE":
        return f"AquaCrop {info['software_version']} (not started from a release folder)"
    commit = (info.get("commit") or "unknown")[:12]
    return (f"AquaCrop {info['software_version']}, release {info.get('release_version')}, source commit {commit}, "
            f"built {info.get('built_utc')}: {info['status']} ({info['detail']})")
