"""The build identity of the running program (0.7.1): one value that changes whenever the code people run changes.

A version string alone could not tell builds apart: several different 0.7.0 builds ran side by side, and browsers kept
a year-long cached copy of scripts keyed only by "0.7.0" while the files changed underneath. The build identity is

    <version>+<source commit, 10 characters>[.dirty].w<digest of the shipped web files, 8 characters>

* The commit comes from `_build.json`, written into the package by `scripts/build_release.py` (a release), or from git
  when running from a source checkout. A checkout with uncommitted changes is marked `.dirty`.
* The web digest covers every file the browser loads, so it is the cache key of the pages' scripts and styles: a page
  can never run scripts from another build.
* It is computed once, when the process starts. If the web files change on disk afterwards (a source tree being edited
  under a running server), `web_drift()` says so instead of serving the new files as if they were this build.

The main dashboard and the twin each write their identity into the shared feed folder; each reads the other's and
reports a mismatch (`partner`). Nothing here is secret.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path

from . import __version__

WEB = resources.files("aquacrop") / "web"


def web_digest() -> str:
    h = hashlib.sha256()
    for f in sorted((p for p in WEB.iterdir() if p.is_file()), key=lambda p: p.name):
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def _git(*a: str) -> str | None:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return None
    try:
        return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


@lru_cache(maxsize=1)
def identity() -> dict:
    info = {"version": __version__, "commit": None, "tree_clean": None, "built_utc": None, "source": "unknown"}
    try:
        b = json.loads((resources.files("aquacrop") / "_build.json").read_text(encoding="utf-8"))
        info |= {"commit": b.get("commit"), "tree_clean": b.get("tree_clean"), "built_utc": b.get("built_utc"),
                 "source": "release build"}
    except (OSError, ValueError):
        commit = _git("rev-parse", "HEAD")
        if commit:
            dirty = bool(_git("status", "--porcelain", "--", "src"))
            info |= {"commit": commit, "tree_clean": not dirty, "source": "source checkout"}
    web = web_digest()
    build = (f"{__version__}+{(info['commit'] or 'nocommit')[:10]}{'.dirty' if info['tree_clean'] is False else ''}"
             f".w{web[:8]}")
    return info | {"web": web, "build": build, "asset_key": build.replace("+", "-"),   # safe in a query string
                   "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def web_drift() -> str | None:
    """Why the files on disk are not the build this process serves, or None."""
    try:
        now = web_digest()
    except OSError as e:
        return f"the web files cannot be read: {e}"
    if now != identity()["web"]:
        return ("the web files changed on disk after this process started: restart it to serve one build "
                f"(started as {identity()['build']}, files now w{now[:8]})")
    return None


IDENTITY_FILE = "identity-{runtime}.json"


def _identity_path(feed_dir: Path, runtime: str) -> Path:
    """The main service owns the feed folder; the twin may write only in its `evidence` folder (on the controller the
    feed root is not writable by the twin's account), so each identity lives where its writer can write it."""
    base = Path(feed_dir) / ("evidence" if runtime.upper() == "TWIN" else "")
    return base / IDENTITY_FILE.format(runtime=runtime.lower())


def publish(feed_dir: Path | None, runtime: str, extra: dict | None = None) -> None:
    """Write this process's identity for its partner (main <-> twin) to read. Best effort: never stops a start."""
    if feed_dir is None:
        return
    try:
        p = _identity_path(feed_dir, runtime)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(identity() | {"runtime": runtime} | (extra or {}), indent=1), encoding="utf-8")
        os.chmod(tmp, 0o640)                  # readable by the partner's group, as the feed's other files (0.7.3)
        os.replace(tmp, p)
    except OSError:
        pass


def partner(feed_dir: Path | None, runtime: str) -> dict:
    """The other runtime's identity as it last wrote it, and whether it is this build."""
    if feed_dir is None:
        return {"runtime": runtime, "known": False, "match": None, "detail": "no shared feed folder here"}
    p = _identity_path(feed_dir, runtime)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if d is None and runtime.upper() == "MAIN":       # the main's build is also in the snapshot it publishes
        try:
            snap = json.loads((Path(feed_dir) / "live-snapshot.json").read_text(encoding="utf-8"))
            d = {"build": snap.get("build"), "version": snap.get("software_version"), "started_utc": None} if snap.get("build") else None
        except (OSError, ValueError):
            d = None
    if d is None:
        return {"runtime": runtime, "known": False, "match": None, "detail": f"the {runtime.lower()} has not reported yet"}
    me = identity()["build"]
    ok = d.get("build") == me
    return {"runtime": runtime, "known": True, "build": d.get("build"), "version": d.get("version"),
            "started_utc": d.get("started_utc"), "match": ok,
            "detail": "same build" if ok else f"the {runtime.lower()} runs {d.get('build')}, this runs {me}"}
