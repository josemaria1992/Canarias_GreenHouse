"""Claude's browser rehearsal of the owner's acceptance tour (docs/operator/walkthrough.md, UAT-01 to UAT-16).

Follows the tour's own steps in headless Chromium against the delivered build: it starts AquaCrop through the
release launcher (--launcher, e.g. the extracted ZIP's aquacrop-demo.sh) or `python -m aquacrop launch`, with a
throwaway state folder and a random first administrator, runs tour_rehearsal.cjs phase "main", stops and
restarts the launcher, runs phase "after" (persistence), then stops the server while the page is open for the
loss-of-connection case. Screenshots, the exported CSV and rehearsal.json go to --out.

On Linux and macOS the first start is answered in a terminal (a pseudo-terminal, as a person would type): a
login name with an accent, a refused password and the accepted one. Its console text, with the throwaway
folder shown as <data folder>, is saved as first-start.txt for the guide's "Starting AquaCrop" section.

This is Claude's rehearsal, not the owner's acceptance: every owner result stays NOT RUN until the owner runs
the tour. Nothing here touches hardware.

    python scripts/ui_check/run_tour_rehearsal.py --out tour-evidence
    python scripts/ui_check/run_tour_rehearsal.py --out tour-evidence --launcher /path/to/aquacrop-0.3.4/aquacrop-demo.sh
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_ui_check import port_free, wait_healthy  # noqa: E402


def start(args, state: Path, port: int, log: Path, password: str | None) -> subprocess.Popen:
    cmd = (["sh", str(args.launcher)] if args.launcher else [args.python, "-m", "aquacrop", "launch"])
    cmd += ["--state-dir", str(state), "--port", str(port), "--no-browser"]
    if password is not None:
        cmd += ["--username", "owner", "--password-stdin"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log.open("ab"), stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    p.stdin.write((password or "") + "\n")
    p.stdin.close()
    return p


def start_in_terminal(args, state: Path, port: int, log: Path, password: str, transcript: Path) -> subprocess.Popen:
    """The first start answered at the launcher's own prompts in a pseudo-terminal: "José" is refused with a
    suggestion, then "owner"; a too-short password is refused, then the real one is typed twice. The password is
    never echoed, so the console text is safe to show."""
    import fcntl
    import pty
    import termios
    cmd = (["sh", str(args.launcher)] if args.launcher else [args.python, "-m", "aquacrop", "launch"])
    cmd += ["--state-dir", str(state), "--port", str(port), "--no-browser", "--username", "owner"]
    master, slave = pty.openpty()

    def own_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    p = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, preexec_fn=own_terminal, close_fds=True)
    os.close(slave)
    seen = bytearray()
    answers = [(b"Login name [", "Jos\u00e9"), (b"Login name [", "owner"), (b"Password: ", "short"),
               (b"Password: ", password), (b"Repeat the password: ", password)]
    lock = threading.Lock()

    def pump():
        with log.open("ab") as f:
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                f.write(chunk)
                f.flush()
                with lock:
                    seen.extend(chunk)
    threading.Thread(target=pump, daemon=True).start()
    pos = 0
    for prompt, answer in answers:
        end = time.monotonic() + 120
        while True:
            with lock:
                i = seen.find(prompt, pos)
            if i >= 0:
                pos = i + len(prompt)
                break
            if time.monotonic() > end or p.poll() is not None:
                raise SystemExit(f"the launcher did not ask {prompt!r}; see {log}")
            time.sleep(0.1)
        time.sleep(0.2)
        os.write(master, (answer + "\n").encode("utf-8"))
    end = time.monotonic() + 60
    while time.monotonic() < end:
        with lock:
            if b"AquaCrop is running" in seen:
                break
        time.sleep(0.2)
    with lock:
        text = seen.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    shown = text.split("AquaCrop is running")[0] + "AquaCrop is running" + text.split("AquaCrop is running", 1)[1].split("\n", 2)[0] \
        if "AquaCrop is running" in text else text
    for folder in {str(state), str(state.resolve())}:
        shown = shown.replace(folder, "<data folder>")
    transcript.write_text(shown.rstrip() + "\n", encoding="utf-8")
    return p


def stop(p: subprocess.Popen) -> int:
    """The console interrupt, as Ctrl+C in the launcher window."""
    p.send_signal(signal.SIGINT)
    try:
        return p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        return p.wait()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("tour-evidence"))
    ap.add_argument("--launcher", type=Path, help="release launcher script (aquacrop-demo.sh) to start AquaCrop with")
    ap.add_argument("--python", default=sys.executable, help="interpreter for `-m aquacrop launch` without --launcher")
    ap.add_argument("--node", default=shutil.which("node") or "node")
    ap.add_argument("--only", default="", help="comma-separated case ids to run (default: all)")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in (*out.glob("*.png"), *out.glob("*.jpg"), *out.glob("rehearsal*.json"), out / "launcher.log", out / "first-start.txt"):
        old.unlink(missing_ok=True)
    state = Path(tempfile.mkdtemp(prefix="aquacrop-tour-"))
    password = secrets.token_urlsafe(18)          # throwaway first administrator in a throwaway state folder
    port = 8000
    if not port_free(port):
        raise SystemExit("port 8000 on 127.0.0.1 is in use; stop that program first")
    base = f"http://127.0.0.1:{port}"
    flag = out / "stop-server.flag"
    env = os.environ | {"AQ_BASE": base, "AQ_USER": "owner", "AQ_PASS": password, "AQ_OUT": str(out),
                        "AQ_ONLY": args.only, "AQ_STOP_FLAG": str(flag)}
    log = out / "launcher.log"
    results: list[dict] = []
    codes = []
    try:
        for phase in ("main", "after"):
            flag.unlink(missing_ok=True)
            if phase == "main" and os.name == "posix":
                proc = start_in_terminal(args, state, port, log, password, out / "first-start.txt")
            else:
                proc = start(args, state, port, log, password if phase == "main" else None)
            try:
                wait_healthy(base, proc, timeout=300)
                node = subprocess.Popen([args.node, str(HERE / "tour_rehearsal.cjs"), phase], env=env)
                while node.poll() is None:
                    if flag.exists() and proc.poll() is None:
                        codes.append(stop(proc))          # the page stays open: loss of connection (UAT-16)
                    time.sleep(0.5)
            finally:
                if proc.poll() is None:
                    codes.append(stop(proc))
            f = out / f"rehearsal-{phase}.json"
            results += json.loads(f.read_text()) if f.exists() else [
                {"id": f"phase {phase}", "ok": False, "detail": "no results written"}]
        results.append({"id": "launcher stops cleanly on Ctrl+C", "ok": all(c == 0 for c in codes),
                        "detail": f"exit codes {codes}"})
        fs = out / "first-start.txt"
        if fs.exists():
            t = fs.read_text(encoding="utf-8")
            want = ['"José" cannot be used', 'Try "jose"', "That password cannot be used: password must be at least 12",
                    "Administrator 'owner' created", "AquaCrop is running"]
            results.append({"id": "first start in a terminal: accented name and short password explained, asked again",
                            "ok": all(w in t for w in want) and password not in t,
                            "detail": "; ".join(w for w in want if w not in t) or "all prompts answered; password never shown"})
    finally:
        shutil.rmtree(state, ignore_errors=True)
        flag.unlink(missing_ok=True)
    summary = {"record": "Claude's browser rehearsal of the owner tour (headless Chromium), not the owner's acceptance",
               "owner_results": "NOT RUN", "profile": "SIMULATED", "config": "installation-80",
               "launcher": str(args.launcher) if args.launcher else f"{args.python} -m aquacrop launch",
               "physical_verification": False,
               "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results), "checks": results}
    (out / "rehearsal.json").write_text(json.dumps(summary, indent=1))
    print(f"{summary['passed']} passed, {summary['failed']} failed; evidence in {out}")
    return 0 if summary["failed"] == 0 and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
