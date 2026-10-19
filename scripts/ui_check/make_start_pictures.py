"""The four pictures of the in-app Getting started page (src/aquacrop/web/start-1.png .. start-4.png).

Starts `python -m aquacrop serve --config installation-80` on 127.0.0.1:8000 with a throwaway state directory
and administrator, follows the four Getting started tasks in headless Chromium (start_pictures.cjs) and saves
a cropped picture of each. Simulation only; the pictures show the Digital Twin. Run from the repository root
of a checkout with the same Node/Playwright setup as run_ui_check.py.

    python scripts/ui_check/make_start_pictures.py
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_ui_check import port_free, serve, stop, wait_healthy  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE.parents[1] / "src" / "aquacrop" / "web")
    ap.add_argument("--node", default=shutil.which("node") or "node")
    args = ap.parse_args()
    state = Path(tempfile.mkdtemp(prefix="aquacrop-start-"))
    password = secrets.token_urlsafe(18)
    subprocess.run([sys.executable, "-m", "aquacrop", "users", "bootstrap", "--config", "installation-80",
                    "--state-dir", str(state), "--username", "admin", "--password-stdin"],
                   input=password + "\n", text=True, check=True, capture_output=True)
    if not port_free(8000):
        raise SystemExit("port 8000 on 127.0.0.1 is in use; stop that server first")
    base = "http://127.0.0.1:8000"
    env = os.environ | {"AQ_BASE": base, "AQ_USER": "admin", "AQ_PASS": password, "AQ_OUT": str(args.out.resolve())}
    proc = serve(state, 8000, state / "server.log")
    try:
        wait_healthy(base, proc)
        r = subprocess.run([args.node, str(HERE / "start_pictures.cjs")], env=env, timeout=600)
    finally:
        stop(proc)
        shutil.rmtree(state, ignore_errors=True)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
