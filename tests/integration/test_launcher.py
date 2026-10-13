"""DL-01: the desktop demo launcher (`python -m aquacrop launch`), on every CI platform including Windows.

Cold start in a fresh state folder with the first administrator created from stdin, the simulation served
on 127.0.0.1 at a non-default port (the loopback origins follow it), sign-in, a clean stop on the console
interrupt, then a second start that keeps the account and asks nothing. The browser is not opened here
(`--no-browser`); the prerequisite checks are exercised one by one."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import aquacrop
from aquacrop import launcher

PW = "launcher test password"
SRC = str(Path(aquacrop.__file__).resolve().parents[1])      # the package under test, also in the subprocess


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(state, port, stdin_text=None):
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
    args = [sys.executable, "-m", "aquacrop", "launch", "--state-dir", str(state), "--port", str(port), "--no-browser"]
    if stdin_text is not None:
        args.append("--password-stdin")
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env=os.environ | {"PYTHONUTF8": "1", "PYTHONPATH": SRC}, **kw)
    if stdin_text is not None:
        p.stdin.write(stdin_text)
        p.stdin.flush()
    return p


def _wait_health(base, p, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if p.poll() is not None:
            raise AssertionError(f"launcher exited early ({p.returncode}): {p.stdout.read()}")
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                return json.load(r)
        except OSError:
            time.sleep(0.25)
    raise AssertionError("launcher did not become healthy")


def _stop(p):
    p.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    out, _ = p.communicate(timeout=60)
    return p.returncode, out


def _login(base, user, pw):
    req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"username": user, "password": pw}).encode(),
                                 headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def test_cold_start_first_admin_sign_in_clean_stop_and_persistence(tmp_path):
    state, port = tmp_path / "demo state", _free_port()
    base = f"http://127.0.0.1:{port}"
    p = _start(state, port, PW + "\n")
    try:
        assert _wait_health(base, p)["ok"]
        who = _login(base, "admin", PW)
        assert who["username"] == "admin" and who["role"] == "admin"
        with pytest.raises(urllib.error.HTTPError) as e:      # no default or shared credentials
            _login(base, "admin", "admin")
        assert e.value.code == 401
    finally:
        rc, out = _stop(p)
    assert rc == 0, out
    assert "Administrator 'admin' created" in out and "stopped cleanly" in out and "[FAIL]" not in out
    log = (state / "logs" / "aquacrop-launch.log").read_text(encoding="utf-8")
    assert "starting installation-48" in log and "stopped cleanly" in log
    # second start: the account persists and nothing is asked
    p = _start(state, port)
    try:
        _wait_health(base, p)
        assert _login(base, "admin", PW)["username"] == "admin"
    finally:
        rc, out = _stop(p)
    assert rc == 0 and "First start" not in out, out


def test_prerequisite_failures_are_reported_and_start_nothing(tmp_path, monkeypatch, capsys):
    port = _free_port()
    cfg, failed = launcher.prerequisites("installation-48", tmp_path / "s", port)
    assert cfg is not None and failed == []
    monkeypatch.setattr(launcher, "REQUIRED_MODULES", launcher.REQUIRED_MODULES + ("no_such_module_xyz",))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", port))
        busy.listen(1)
        _, failed = launcher.prerequisites("hardware-read-only.example", tmp_path / "s", port)
    out = capsys.readouterr().out
    assert any("missing: no_such_module_xyz" in f for f in failed) and "requirements.lock" in out
    assert any("HARDWARE_READ_ONLY" in f for f in failed) and "only runs simulations" in out
    assert any(f"port {port}" in f for f in failed) and "--port" in out
    blocked = tmp_path / "a file"
    blocked.write_text("x")
    _, failed = launcher.prerequisites("installation-48", blocked / "state", _free_port())
    assert any("state folder" in f for f in failed)
    # the command itself exits 2 and writes nothing when a check fails
    from aquacrop.cli import main
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", port))
        busy.listen(1)
        assert main(["launch", "--state-dir", str(tmp_path / "t"), "--port", str(port), "--no-browser"]) == 2
    assert not (tmp_path / "t" / "installation-48.sqlite3").exists()


def test_origins_follow_a_non_default_port_only_for_loopback_configs():
    from aquacrop.cli import _cfg
    cfg = _cfg("installation-48")
    assert launcher.localize(cfg, 8000) is cfg
    moved = launcher.localize(cfg, 8123)
    assert moved.security.allowed_origins == ["http://127.0.0.1:8123", "http://localhost:8123"]
    remote = cfg.model_copy(update={"security": cfg.security.model_copy(
        update={"allowed_origins": ["https://aquacrop.lan"]})})
    assert launcher.localize(remote, 8123) is remote
