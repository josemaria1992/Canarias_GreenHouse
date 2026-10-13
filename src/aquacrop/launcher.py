"""Desktop demo launcher: `python -m aquacrop launch` (pre-hardware release brief §12, DL-01).

One command for the owner's PC: checks the prerequisites, keeps the state in a per-user folder so it
persists between runs, guides the first administrator (no default or shared credentials), serves the
full-installation simulation on 127.0.0.1 only, opens the browser, writes a log file, and stops cleanly
on Ctrl+C. It is a convenience around `users bootstrap` and `serve`; both keep working as before.
Only SIMULATED configurations are launched: a hardware profile is served with `serve` on the controller.
"""
from __future__ import annotations

import getpass
import logging
import os
import platform
import shutil
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__

SUPPORTED = ((3, 11), (3, 12), (3, 13))      # the tested range (CI matrix)
MIN_FREE_BYTES = 2 * 2**30                   # a first year of demo history is far smaller; this is headroom
REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "yaml", "argon2", "starlette")


def default_state_dir() -> Path:
    """Per-user, persistent: %LOCALAPPDATA%\\AquaCrop\\demo, ~/Library/Application Support/AquaCrop/demo
    or ~/.local/share/aquacrop/demo."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AquaCrop"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AquaCrop"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "aquacrop"
    return base / "demo"


class Check:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def __call__(self, ok: bool, what: str, fix: str = "") -> bool:
        print(f"  [{'ok' if ok else 'FAIL'}] {what}" + ("" if ok or not fix else f"\n         -> {fix}"))
        if not ok:
            self.failed.append(what)
        return ok


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def prerequisites(cfg_name: str, state_dir: Path, port: int):
    """Returns (config or None, failures). Prints one line per check with the fix for each failure."""
    from .cli import _cfg
    chk = Check()
    print(f"AquaCrop {__version__}: checking prerequisites")
    v = sys.version_info[:2]
    chk(v in SUPPORTED, f"Python {platform.python_version()} ({sys.executable})",
        "install Python 3.11, 3.12 or 3.13 from python.org and run the launcher again")
    missing = []
    for m in REQUIRED_MODULES:
        try:
            __import__(m)
        except Exception:
            missing.append(m)
    chk(not missing, "required packages installed" + (f" (missing: {', '.join(missing)})" if missing else ""),
        "install the locked requirements: python -m pip install --require-hashes -r requirements.lock, "
        "or the offline wheelhouse in the release ZIP")
    from importlib import resources
    web = resources.files("aquacrop") / "web"
    assets = [n for n in ("index.html", "app.js", "plant.js", "chart.js", "app.css") if not (web / n).is_file()]
    chk(not assets, "browser pages packaged" + (f" (missing: {', '.join(assets)})" if assets else ""),
        "reinstall the AquaCrop wheel; do not run from a partial copy")
    cfg = None
    try:
        cfg = _cfg(cfg_name)
        chk(cfg.profile.value == "SIMULATED", f"configuration {cfg.config_id} ({cfg.profile.value})",
            "the launcher only runs simulations; serve a hardware profile with `aquacrop serve` on the controller")
    except Exception as e:
        chk(False, f"configuration {cfg_name}: {e}", "use a packaged name (installation-48, demo-48) or a valid file")
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok = True
    except OSError as e:
        ok, why = False, str(e)
    chk(ok, f"state folder writable: {state_dir}", "" if ok else f"{why}; choose another folder with --state-dir")
    if ok:
        free = shutil.disk_usage(state_dir).free
        chk(free >= MIN_FREE_BYTES, f"free disk space {free / 2**30:.1f} GiB",
            f"free at least {MIN_FREE_BYTES / 2**30:.0f} GiB on that drive or use --state-dir elsewhere")
    chk(port_free(port), f"port {port} on 127.0.0.1 is free",
        f"another program (or AquaCrop already running) uses it: open http://127.0.0.1:{port}/ or stop it, "
        "or start with --port <another>")
    return cfg, chk.failed


def localize(cfg, port: int):
    """The loopback origins follow the chosen port (the packaged configs allow 127.0.0.1:8000 only)."""
    sec = cfg.security
    loop = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    if port == 8000 or not all(o.startswith(("http://127.0.0.1:", "http://localhost:")) for o in sec.allowed_origins):
        return cfg
    return cfg.model_copy(update={"security": sec.model_copy(update={"allowed_origins": sorted(loop)})})


def first_admin(cfg, state_dir: Path, *, username: str, password_stdin: bool) -> bool:
    """Guided creation of the first administrator when none exists. Returns False when cancelled."""
    from .cli import _db_path
    from .domain.model import new_id
    from .historian.writer import Historian
    from .security.auth import Accounts, Role, load_or_create_secret
    h = Historian(_db_path(cfg, state_dir), cfg, new_id("launch_setup"))
    try:
        acc = Accounts(h, load_or_create_secret(state_dir / "secret.key"), idle_minutes=30, absolute_hours=12)
        if acc.count_users():
            return True
        print("\nFirst start: create the administrator account (there is no default password).")
        if password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            if not sys.stdin.isatty():
                print("no terminal to ask for a password: run in a console or pass --password-stdin", file=sys.stderr)
                return False
            entered = input(f"Administrator user name [{username}]: ").strip()
            username = entered or username
            while True:
                pw = getpass.getpass("Password (at least 12 characters): ")
                if pw == getpass.getpass("Repeat the password: "):
                    break
                print("The passwords differ; try again.")
        try:
            acc.create_user(username, pw, Role.ADMIN, commissioning=False, must_change=False)
        except ValueError as e:
            print(f"cannot create the account: {e}", file=sys.stderr)
            return False
        h.event("USER_BOOTSTRAPPED", {"username": username, "role": "admin", "via": "launcher"}, actor="launcher")
        print(f"Administrator '{username}' created. Add operators and viewers under Settings, Users.")
        return True
    finally:
        h.close()


def _open_browser_when_ready(url: str, stop: threading.Event, log: logging.Logger, open_browser: bool) -> None:
    end = time.monotonic() + 120
    while not stop.is_set() and time.monotonic() < end:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as r:
                if r.status == 200:
                    log.info("ready: %s", url)
                    print(f"\nAquaCrop is running: {url}\nStop it with Ctrl+C in this window (or close the window).")
                    if open_browser:
                        webbrowser.open(url)
                    return
        except OSError:
            pass
        time.sleep(0.5)


def cmd_launch(args) -> int:
    import uvicorn

    from .api.app import create_app
    from .ownership import OwnershipError, acquire_installation
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir()
    cfg, failed = prerequisites(args.config, state_dir, args.port)
    if failed:
        print(f"\n{len(failed)} prerequisite(s) failed; nothing was started.", file=sys.stderr)
        return 2
    cfg = localize(cfg, args.port)
    logs = state_dir / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / "aquacrop-launch.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("aquacrop", "uvicorn", "uvicorn.error"):
        logging.getLogger(name).addHandler(handler)
    log = logging.getLogger("aquacrop.launch")
    log.setLevel(logging.INFO)
    print(f"  state folder: {state_dir}\n  log file:     {log_path}")
    if not first_admin(cfg, state_dir, username=args.username, password_stdin=args.password_stdin):
        return 3
    try:
        locks = acquire_installation(state_dir, cfg)
    except OwnershipError as e:
        print(str(e), file=sys.stderr)
        return 4
    url = f"http://127.0.0.1:{args.port}/"
    stop = threading.Event()
    try:
        try:
            app = create_app(cfg, state_dir=state_dir, bind_host="127.0.0.1")
        except Exception as e:
            log.exception("cannot start")
            print(f"cannot start: {e} (details in {log_path})", file=sys.stderr)
            return 3
        log.info("starting %s %s on %s, state %s", cfg.config_id, __version__, url, state_dir)
        threading.Thread(target=_open_browser_when_ready, args=(url, stop, log, not args.no_browser),
                         daemon=True).start()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1,
                                               log_level="warning"))
        # uvicorn shuts down gracefully on Ctrl+C, Ctrl+Break and SIGTERM, then re-raises the signal to the
        # handler that was installed before it: record it instead of dying, so the locks and log close
        got: list[int] = []
        for sig in (signal.SIGINT, signal.SIGTERM) + ((signal.SIGBREAK,) if sys.platform == "win32" else ()):
            signal.signal(sig, lambda n, _f: got.append(n))
        server.run()
        log.info("shutdown after signal %s", got[0] if got else "none")
    finally:
        stop.set()
        for lk in locks:
            lk.release()
    log.info("stopped cleanly")
    print("AquaCrop stopped cleanly; the history and accounts are kept in the state folder.")
    return 0


def add_parser(sub) -> None:
    la = sub.add_parser("launch", help="desktop demo: checks, first administrator, serve on 127.0.0.1, open browser")
    la.add_argument("--config", default="installation-48", help="a SIMULATED configuration (default installation-48)")
    la.add_argument("--state-dir", default=None, help=f"default: {default_state_dir()}")
    la.add_argument("--port", type=int, default=8000)
    la.add_argument("--username", default="admin", help="first administrator's name (asked when interactive)")
    la.add_argument("--password-stdin", action="store_true", help="first administrator's password on stdin")
    la.add_argument("--no-browser", action="store_true")
    la.add_argument("--check-only", action="store_true", help="run the prerequisite checks and exit")
    la.set_defaults(fn=cmd_launch_or_check)


def cmd_launch_or_check(args) -> int:
    if args.check_only:
        state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir()
        _, failed = prerequisites(args.config, state_dir, args.port)
        return 2 if failed else 0
    return cmd_launch(args)
