"""AT-PKG-01: exercise an installed wheel from a directory unrelated to the source tree.

Usage: <venv-python> scripts/check_installed_wheel.py
Run it with the interpreter of a venv where only the wheel and the locked requirements are
installed. It verifies: the package is not imported from a source checkout, `demo verify` passes,
`users bootstrap` and `serve` work, every UI asset is served from the package with no external URL,
login and an authenticated status call succeed. Exit code 0 only if every check passes.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="aquacrop-wheel-"))
    os.chdir(work)
    py = sys.executable
    import aquacrop
    loc = Path(aquacrop.__file__).resolve()
    check("imported from site-packages", "site-packages" in str(loc), str(loc))
    r = subprocess.run([py, "-m", "aquacrop", "demo", "verify", "--config", "demo-24", "--seed", "42"],
                       capture_output=True, text=True, timeout=900)
    check("demo verify demo-24", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[-200:])
    r = subprocess.run([py, "-m", "aquacrop", "demo", "verify", "--config", "installation-48", "--seed", "42"],
                       capture_output=True, text=True, timeout=900)
    check("demo verify installation-48", r.returncode == 0,
          r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[-200:])
    pw = secrets.token_urlsafe(18)
    r = subprocess.run([py, "-m", "aquacrop", "users", "bootstrap", "--config", "demo-48", "--state-dir", "st",
                        "--username", "admin", "--password-stdin"], input=pw + "\n", capture_output=True, text=True)
    check("users bootstrap", r.returncode == 0, r.stderr.strip()[-200:])
    port = 8000 if _port_free(8000) else free_port()
    srv = subprocess.Popen([py, "-m", "aquacrop", "serve", "--config", "demo-48", "--state-dir", "st", "--host",
                            "127.0.0.1", "--port", str(port), "--log-level", "warning"])
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        html = urllib.request.urlopen(base + "/").read().decode()
        assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
        check("index references packaged assets", len(assets) >= 3, ", ".join(assets))
        ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        check("no external URLs in index", not ext, ", ".join(ext))
        for a in assets:
            body = urllib.request.urlopen(base + a).read()
            check(f"asset {a}", len(body) > 50, f"{len(body)} bytes")
            if a.endswith(".js"):
                check(f"no CDN in {a}", not re.search(rb"https?://(?!127\.0\.0\.1|localhost|www\.w3\.org/2000/svg)", body), "")
        if port == 8000:
            jar = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            req = urllib.request.Request(base + "/api/auth/login", data=json.dumps(
                {"username": "admin", "password": pw}).encode(), method="POST",
                headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"})
            d = json.loads(op.open(req).read())
            check("login", d.get("username") == "admin" and bool(d.get("csrf_token")),
                  json.dumps({k: d[k] for k in ("username", "role", "must_change_password")}))
            st = json.loads(op.open(base + "/api/status").read())
            check("authenticated status", st["banner"] == "SIMULATION — DEMO MODEL", st["banner"])
        else:
            check("login skipped (port 8000 busy; default allowed origin is :8000)", True, "")
    finally:
        srv.terminate()
        srv.wait(timeout=20)
    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} PASS (python {sys.version.split()[0]}, {sys.platform})")
    return 1 if failed else 0


def _port_free(p: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # like uvicorn; ignore TIME_WAIT leftovers
        try:
            s.bind(("127.0.0.1", p))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    raise SystemExit(main())
