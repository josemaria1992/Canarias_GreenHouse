"""Browser evidence for the installation pages (UI-01 to UI-05), simulation only.

Starts `python -m aquacrop serve --config installation-80` on 127.0.0.1 with a throwaway state directory
and a random administrator password, runs ui_check.cjs in headless Chromium (Playwright), restarts the
server, runs the after-restart phase, then serves the HARDWARE_READ_ONLY profile over the in-memory fake
bus (tests/fakes) for the read-only pages, and writes screenshots, the exported CSV and checks.json to
--out. Nothing here touches hardware, and passing it is not physical verification. Run it from the
repository root of a checkout.

Needs Node.js with the `playwright` package and a Chromium build. Set PLAYWRIGHT_MODULE to the package
path and CHROMIUM_PATH to the browser when Node would not find them.

    python scripts/ui_check/run_ui_check.py --out ui-evidence
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as the server binds; TIME_WAIT is fine
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def wait_healthy(base: str, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise SystemExit(f"server exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                if json.load(r).get("ok"):
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise SystemExit("server did not become healthy")


def serve(state: Path, port: int, log: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "aquacrop", "serve", "--config", "installation-80",
                             "--state-dir", str(state), "--host", "127.0.0.1", "--port", str(port)],
                            stdout=log.open("ab"), stderr=subprocess.STDOUT)


def serve_fake_hardware(state: Path, port: int, log: Path, env: dict) -> subprocess.Popen:
    """HARDWARE_READ_ONLY over the in-memory fake bus (test infrastructure from tests/fakes)."""
    return subprocess.Popen([sys.executable, str(HERE / "serve_fake_hardware.py"), "--state-dir", str(state),
                             "--port", str(port)], stdout=log.open("ab"), stderr=subprocess.STDOUT, env=env,
                            cwd=HERE.parents[1])


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("ui-evidence"))
    ap.add_argument("--node", default=shutil.which("node") or "node")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix="aquacrop-ui-"))
    password = secrets.token_urlsafe(18)          # throwaway account in a throwaway state directory
    subprocess.run([sys.executable, "-m", "aquacrop", "users", "bootstrap", "--config", "installation-80",
                    "--state-dir", str(state), "--username", "admin", "--password-stdin"],
                   input=password + "\n", text=True, check=True, capture_output=True)
    port = 8000                                    # the default allowed origin of the installation profile
    if not port_free(port):
        raise SystemExit("port 8000 on 127.0.0.1 is in use; stop that server first")
    base = f"http://127.0.0.1:{port}"
    env = os.environ | {"AQ_BASE": base, "AQ_USER": "admin", "AQ_PASS": password, "AQ_OUT": str(out)}
    log, hw_log = out / "server.log", out / "server-hardware.log"
    for stale in (log, hw_log, out / "checks.json", *out.glob("checks-*.json")):
        stale.unlink(missing_ok=True)       # a rerun into the same folder never reads an earlier run's results
    hw_state = Path(tempfile.mkdtemp(prefix="aquacrop-ui-hw-"))
    results = []
    try:
        for phase in ("main", "after", "hardware"):
            # the after phase starts a fresh server on the same state; the hardware phase serves the
            # read-only profile over the fake bus from its own state directory
            proc = serve_fake_hardware(hw_state, port, hw_log, env) if phase == "hardware" else serve(state, port, log)
            try:
                wait_healthy(base, proc)
                subprocess.run([args.node, str(HERE / "ui_check.cjs"), phase], env=env, timeout=900)
            finally:
                stop(proc)
            f = out / f"checks-{phase}.json"
            results += json.loads(f.read_text()) if f.exists() else [
                {"id": f"phase {phase}", "ok": False, "detail": "no results written"}]
        frames = [ln for ln in hw_log.read_text(errors="replace").splitlines() if ln.startswith("fake bus write frames:")]
        results.append({"id": "DL-03 no write from the browser on the read-only profile",
                        "ok": frames == ["fake bus write frames: 0"], "detail": frames[-1] if frames else "no count printed"})
    finally:
        shutil.rmtree(state, ignore_errors=True)
        shutil.rmtree(hw_state, ignore_errors=True)
    summary = {"profile": "SIMULATED", "config": "installation-80", "physical_verification": False,
               "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
               "checks": results}
    (out / "checks.json").write_text(json.dumps(summary, indent=1))
    annotate_failures(results, "browser check failure")
    print(f"{summary['passed']} passed, {summary['failed']} failed; evidence in {out}")
    return 0 if summary["failed"] == 0 and results else 1


def annotate_failures(results: list[dict], title: str) -> None:
    """On GitHub Actions, each failed check also becomes an annotation on the check run (readable through the
    checks API when the job log cannot be downloaded)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for r in results:
        if not r.get("ok"):
            msg = f"{r.get('id')}: {r.get('detail')}"[-1500:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
            print(f"::error title={title}::{msg}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
