"""Browser journey for 0.7.0 (operator, viewer, tablet width, twin), simulation only.

Starts a throwaway live service (installation-48, simulated) and a twin on 127.0.0.1:8020/8021 with a fresh state
directory, creates an administrator, an operator and a viewer with random passwords, runs journey_070.cjs in headless
Chromium (Playwright) and writes checks.json and the screenshots to --out. Nothing here touches hardware, and passing
it is not physical verification. Run it from the repository root of a checkout.

Needs Node.js with the `playwright` package and a Chromium build (PLAYWRIGHT_MODULE, CHROMIUM_PATH as for
run_ui_check.py).

    python scripts/ui_check/run_journey_070.py --out docs/operator/walkthrough-0.7
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from run_ui_check import annotate_failures, port_free, stop, wait_healthy

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LIVE, TWIN = 8020, 8021


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("ui-evidence-0.7"))
    ap.add_argument("--node", default=shutil.which("node") or "node")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for port in (LIVE, TWIN):
        if not port_free(port):
            raise SystemExit(f"port {port} on 127.0.0.1 is in use; stop that server first")
    tmp = Path(tempfile.mkdtemp(prefix="aquacrop-journey-"))
    live_state, twin_state, feed = tmp / "live", tmp / "twin", tmp / "feed"
    for d in (live_state, twin_state, feed):
        d.mkdir()
    base, twin = f"http://127.0.0.1:{LIVE}", f"http://127.0.0.1:{TWIN}"
    cfg = tmp / "installation-48.yaml"             # the simulated installation, served on the journey's port
    cfg.write_text((REPO / "configs" / "installation-48.yaml").read_text(encoding="utf-8") +
                   f'\nsecurity:\n  allowed_hosts: ["127.0.0.1"]\n  allowed_origins: ["{base}"]\n', encoding="utf-8")
    accounts = {}
    for i, (user, role) in enumerate((("admin", "admin"), ("oper", "operator"), ("view", "viewer"))):
        pw = secrets.token_urlsafe(18)             # throwaway accounts in a throwaway state directory
        subprocess.run([sys.executable, "-m", "aquacrop", "users", "bootstrap", "--config", str(cfg),
                        "--state-dir", str(live_state), "--username", user, "--role", role, "--password-stdin",
                        *(["--additional"] if i else [])], input=pw + "\n", text=True, check=True, capture_output=True)
        accounts[user] = f"{user}:{pw}"
    env = os.environ | {"AQUACROP_FEED_DIR": str(feed), "AQUACROP_TWIN_URL": twin + "/", "AQ_BASE": base,
                        "AQ_TWIN": twin, "AQ_ADMIN": accounts["admin"], "AQ_OPER": accounts["oper"],
                        "AQ_VIEW": accounts["view"], "AQ_OUT": str(out)}
    log = out / "server-journey.log"
    for stale in (log, out / "checks-journey.json", out / "checks.json"):
        stale.unlink(missing_ok=True)
    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "-m", "aquacrop", "serve", "--config", str(cfg), "--state-dir",
                                       str(live_state), "--host", "127.0.0.1", "--port", str(LIVE)],
                                      stdout=log.open("ab"), stderr=subprocess.STDOUT, env=env))
        wait_healthy(base, procs[0])                # the twin starts from the snapshot the live service publishes
        procs.append(subprocess.Popen([sys.executable, "-m", "aquacrop", "twin-serve", "--feed-dir", str(feed),
                                       "--state-dir", str(twin_state), "--port", str(TWIN), "--allowed-host",
                                       "127.0.0.1", "--allowed-origin", twin],
                                      stdout=log.open("ab"), stderr=subprocess.STDOUT, env=env))
        wait_healthy(twin, procs[1])
        subprocess.run([args.node, str(HERE / "journey_070.cjs")], env=env, timeout=900)
    finally:
        for p in procs:
            stop(p)
        shutil.rmtree(tmp, ignore_errors=True)
    f = out / "checks-journey.json"
    results = json.loads(f.read_text()) if f.exists() else [{"id": "journey", "ok": False, "detail": "no results"}]
    summary = {"profile": "SIMULATED", "config": "installation-48", "physical_verification": False,
               "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results), "checks": results}
    (out / "checks.json").write_text(json.dumps(summary, indent=1))
    f.unlink(missing_ok=True)
    annotate_failures(results, "browser journey failure")
    print(f"{summary['passed']} passed, {summary['failed']} failed; evidence in {out}")
    return 0 if summary["failed"] == 0 and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
