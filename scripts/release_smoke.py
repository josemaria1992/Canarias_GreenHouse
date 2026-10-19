"""DL-07: one session of the extracted release through its own launcher, as the owner would run it.

Starts the release launcher (AquaCrop-Demo.cmd on Windows, aquacrop-demo.sh elsewhere) with a private
%LOCALAPPDATA% (--local), creates the first administrator on a cold start (--first), checks sign-in, the
browser assets, the version, the release check (RELEASE.json against the installed wheel), the 80-point
inventory and the configuration, optionally adds data (--seed: a viewer account, a recipe draft, a local
backup and two history snapshots), records what the data folder holds (--record), compares it with an
earlier session (--compare: nothing lost, every process STOPPED) and stops the launcher with the console
break, as Ctrl+C in its window.

Used by CI for the cold start, the restart and the upgrade over the earlier demos (0.3.1, then 0.3.2). With
--hide-python the launcher sees no Python on the PC, as on an owner's PC without one. The password
is random and kept in --secret (a temporary file, never uploaded). Simulation only; no hardware.

    python scripts/release_smoke.py --launcher unpacked/aquacrop-0.3.3/AquaCrop-Demo.cmd --local T/cold \
        --secret T/pw --first --record T/cold1.json --expect-version 0.3.3 --expect-release VERIFIED
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8000"
WIN = sys.platform == "win32"


class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""

    def call(self, method, path, body=None, raw=False):
        h = {"Origin": BASE}
        if method != "GET":
            h["X-CSRF-Token"] = self.csrf
        data = None
        if body is not None:
            h["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
        with self.op.open(req, timeout=30) as r:
            payload = r.read()
            return payload if raw else json.loads(payload or b"null")


def wait_health(proc, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise SystemExit(f"the launcher exited early with {proc.returncode}")
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if json.load(r).get("ok"):
                    return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit("AquaCrop did not report healthy")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--launcher", type=Path, required=True)
    ap.add_argument("--local", type=Path, required=True, help="used as %%LOCALAPPDATA%% (XDG_DATA_HOME elsewhere)")
    ap.add_argument("--secret", type=Path, required=True)
    ap.add_argument("--first", action="store_true", help="cold start: create the first administrator")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--record", type=Path)
    ap.add_argument("--compare", type=Path)
    ap.add_argument("--expect-version", required=True)
    ap.add_argument("--expect-release", choices=["VERIFIED", "NOT_A_RELEASE", "ANY"], default="VERIFIED")
    ap.add_argument("--log", type=Path, help="launcher console output (default next to --record)")
    ap.add_argument("--hide-python", action="store_true",
                    help="Windows: start the launcher as on a PC without Python: every Python folder is taken off PATH "
                         "and py, python and python3 are replaced by stand-ins that fail and say so")
    a = ap.parse_args()
    a.local.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)))
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

    if a.first:
        a.secret.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    password = a.secret.read_text(encoding="utf-8").strip()
    env = os.environ | {"LOCALAPPDATA": str(a.local), "XDG_DATA_HOME": str(a.local), "AQUACROP_NO_PAUSE": "1",
                        "PYTHONUTF8": "1"}
    env.pop("PYTHONPATH", None)
    shim = "AQUACROP-SMOKE: a Python on the PC was called"
    if a.hide_python:
        stand_in = a.local / "no-python-here"
        stand_in.mkdir(exist_ok=True)
        for name in ("py", "python", "python3"):
            (stand_in / f"{name}.cmd").write_text(f"@echo {shim} ({name}) & exit /b 9\r\n", encoding="ascii")
        keep = [d for d in env.get("PATH", "").split(os.pathsep) if d and "python" not in d.lower()]
        env["PATH"] = os.pathsep.join([str(stand_in)] + keep)
    launcher = str(a.launcher.resolve())
    cmd = (["cmd", "/c", launcher] if WIN else ["sh", launcher]) + ["--no-browser"]
    if a.first:
        cmd += ["--username", "owner", "--password-stdin"]
    log = a.log or (a.record or a.local / "session").with_suffix(".console.log")
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WIN else {"start_new_session": True}
    t0 = time.monotonic()
    offset = log.stat().st_size if log.exists() else 0
    with log.open("ab") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, env=env, **kw)
        proc.stdin.write(((password + "\n") if a.first else "\n").encode())
        proc.stdin.close()
        try:
            wait_health(proc, 900)
            check("started and healthy", True, f"{time.monotonic() - t0:.0f} s")
            c = Client()
            who = c.call("POST", "/api/auth/login", {"username": "owner", "password": password})
            c.csrf = who["csrf_token"]
            check("sign-in as the administrator created on the first start", who["role"] == "admin", who["username"])
            try:
                c.call("POST", "/api/auth/login", {"username": "admin", "password": "admin"})
                check("no default credentials", False, "admin/admin accepted")
            except urllib.error.HTTPError as e:
                check("no default credentials", e.code == 401, f"admin/admin -> HTTP {e.code}")
            page = c.call("GET", "/", raw=True).decode()
            assets = {n: len(c.call("GET", f"/static/{n}", raw=True)) for n in ("words.js", "app.js", "plant.js", "chart.js", "app.css")}
            check("browser page and assets served", "AquaCrop" in page and all(assets.values()), assets)
            st = c.call("GET", "/api/status")
            check("version", st.get("software_version") == a.expect_version, st.get("software_version"))
            check("simulation profile and installation-80", st.get("profile") == "SIMULATED" and
                  str(st.get("config_id", "")).startswith("installation-80"), f"{st.get('profile')} {st.get('config_id')}")
            s = c.call("GET", "/api/settings")
            rel = s.get("release") or {}
            if a.expect_release != "ANY":
                check("release check", rel.get("status") == a.expect_release,
                      f"{rel.get('status')}: {rel.get('detail')} (commit {str(rel.get('commit'))[:12]})")
                inv = s.get("inventory") or {}
                check("inventory 6 modules, 16 AI + 32 DI + 32 DO", (inv.get("modules"), inv.get("AI"), inv.get("DI"), inv.get("DO")) == (6, 16, 32, 32), inv)
                check("data folder under the private local folder", str(s.get("state_dir", "")).startswith(str(a.local.resolve())), s.get("state_dir"))
            if a.seed:
                users = {u["username"] for u in s["users"]}
                if "smoke-viewer" not in users:
                    c.call("POST", "/api/users", {"username": "smoke-viewer", "password": secrets.token_urlsafe(16), "role": "viewer"})
                rec = c.call("GET", "/api/recipes")
                body = next(r["body"] for r in rec["recipes"] if r["ref"] == rec["active"])
                c.call("POST", "/api/recipes/draft", {"recipe_id": "smoke", "name": "smoke test draft (synthetic)", "body": body})
                c.call("POST", "/api/backup")
                end = time.monotonic() + 300
                while time.monotonic() < end and not (c.call("GET", "/api/backup").get("local_count", 0) >= 1):
                    time.sleep(2)
                check("local backup created", c.call("GET", "/api/backup").get("local_count", 0) >= 1, "")

            def samples():
                e = datetime.now(timezone.utc)
                q = f"points=analog_A.AI1&start={(e - timedelta(days=2)).isoformat()}&end={e.isoformat()}".replace("+", "%2B")
                return len(c.call("GET", "/api/trends?" + q)["series"].get("analog_A.AI1", []))
            if a.seed:                       # history is one snapshot per 60 s of wall time
                end = time.monotonic() + 200
                while time.monotonic() < end and samples() < 2:
                    time.sleep(5)
            now = {"version": st.get("software_version"),
                   "users": sorted(u["username"] for u in c.call("GET", "/api/settings")["users"]),
                   "recipes": sorted(r["ref"] for r in c.call("GET", "/api/recipes")["recipes"]),
                   "samples": samples(),
                   "processes": {k: v["state"] for k, v in c.call("GET", "/api/processes")["processes"].items()},
                   # 0.3.1 did not report its data folder: it is the launcher's default
                   "state_dir": s.get("state_dir") or str(a.local.resolve() / ("AquaCrop" if WIN else "aquacrop") / "demo"),
                   "environments": sorted(p.name for p in (a.local / "AquaCrop").glob("venv-*")) if WIN else
                   sorted(p.name for p in (a.local / "aquacrop").glob("venv-*")),
                   "backups": []}
            now["backups"] = sorted(p.name for p in Path(now["state_dir"]).joinpath("backups").glob("*") if p.is_file())
            if a.seed:
                check("seeded data present", "smoke-viewer" in now["users"] and "smoke@v1" in now["recipes"] and now["samples"] >= 2,
                      f"users {now['users']}, recipes {now['recipes']}, {now['samples']} snapshots")
            if a.compare:
                before = json.loads(a.compare.read_text(encoding="utf-8"))
                check("accounts kept", set(before["users"]) <= set(now["users"]), now["users"])
                check("recipes kept", set(before["recipes"]) <= set(now["recipes"]), now["recipes"])
                check("history kept", now["samples"] >= before["samples"], f"{before['samples']} -> {now['samples']} snapshots of analog_A.AI1")
                check("every process starts STOPPED", all(v == "STOPPED" for v in now["processes"].values()), now["processes"])
                check("same data folder", before["state_dir"] == now["state_dir"], now["state_dir"])
                check("earlier environments left in place", set(before["environments"]) <= set(now["environments"]), now["environments"])
                check("earlier backups left in place", set(before["backups"]) <= set(now["backups"]), now["backups"])
            if a.record:
                a.record.write_text(json.dumps(now, indent=1), encoding="utf-8")
        except Exception as e:                     # noqa: BLE001 - reported as a failed check, then stopped
            check("session", False, repr(e))
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.CTRL_BREAK_EVENT if WIN else signal.SIGINT)
                try:
                    proc.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    check("launcher stopped by the console break", False, "had to be killed")
    with log.open("rb") as f:
        f.seek(offset)
        text = f.read().decode("utf-8", errors="replace")
    if a.launcher.suffix.lower() == ".cmd" and (a.launcher.parent / "runtime" / "python.exe").is_file():
        check("started with the Python bundled in the release, no Python of the PC used",
              "Using the Python bundled with this release" in text and shim not in text,
              next((ln for ln in text.splitlines() if "Using the Python" in ln or shim in ln), "no line naming the Python"))
    check("stopped cleanly on the console break", "AquaCrop stopped cleanly" in text,
          next((ln for ln in text.splitlines() if "stopped cleanly" in ln), "no clean-stop line in the launcher output"))
    failed = [n for n, ok, _ in checks if not ok]
    print(json.dumps({"passed": len(checks) - len(failed), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
