"""Desktop demo launcher: `python -m aquacrop launch` (pre-hardware release brief §12, DL-01).

One command for the owner's PC: checks the prerequisites, keeps the state in a per-user folder so it
persists between runs, guides the first administrator (no default or shared credentials), serves the
full-installation simulation on 127.0.0.1 only, opens the browser, writes a log file, and stops cleanly
on Ctrl+C. It is a convenience around `users bootstrap` and `serve`; both keep working as before.
Only SIMULATED configurations are launched: a hardware profile is served with `serve` on the controller.
"""
from __future__ import annotations

import getpass
import json
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

SUPPORTED = ((3, 11), (3, 12), (3, 13), (3, 14))   # the tested range (CI matrix)
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
    from .release import describe, release_info
    rel = release_info()
    chk(rel["status"] in ("VERIFIED", "NOT_A_RELEASE"), describe(rel),
        "this environment does not hold the release next to the launcher: unpack the whole release ZIP into a "
        "new folder and start AquaCrop-Demo.cmd from there")
    v = sys.version_info[:2]
    chk(v in SUPPORTED, f"Python {platform.python_version()} ({sys.executable})",
        "use the Python bundled in the release folder (runtime), or install Python 3.11 to 3.14 from python.org")
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
    assets = [n for n in ("index.html", "words.js", "app.js", "plant.js", "chart.js", "app.css") if not (web / n).is_file()]
    chk(not assets, "browser pages packaged" + (f" (missing: {', '.join(assets)})" if assets else ""),
        "reinstall the AquaCrop wheel; do not run from a partial copy")
    cfg = None
    try:
        cfg = _cfg(cfg_name)
        chk(cfg.profile.value == "SIMULATED", f"configuration {cfg.config_id} ({cfg.profile.value})",
            "the launcher only runs simulations; serve a hardware profile with `aquacrop serve` on the controller")
    except Exception as e:
        chk(False, f"configuration {cfg_name}: {e}",
            "use a packaged name (installation-80, the normal demo; legacy installation-48, demo-48, demo-24) "
            "or a valid file")
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok = True
    except OSError as e:
        ok, why = False, str(e)
    chk(ok, f"data folder writable: {state_dir}", "" if ok else f"{why}; choose another folder with --state-dir")
    if ok:
        free = shutil.disk_usage(state_dir).free
        chk(free >= MIN_FREE_BYTES, f"free disk space {free / 2**30:.1f} GiB",
            f"free at least {MIN_FREE_BYTES / 2**30:.0f} GiB on that drive or use --state-dir elsewhere")
    chk(port_free(port), f"port {port} on 127.0.0.1 is free",
        f"another program (or AquaCrop already running) uses it: open http://127.0.0.1:{port}/ or stop it, "
        "or start with --port <another>")
    return cfg, chk.failed


def localize(cfg, port: int):
    """The loopback origins follow the chosen port (the packaged configs allow 127.0.0.1:8000 only); any other
    origin a custom configuration allows is kept as it is."""
    sec = cfg.security
    if port == 8000:
        return cfg
    keep = [o for o in sec.allowed_origins if not o.startswith(("http://127.0.0.1:", "http://localhost:"))]
    loop = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    return cfg.model_copy(update={"security": sec.model_copy(update={"allowed_origins": keep + loop})})


def suggest_username(name: str) -> str:
    """A login name the account rules accept, made from what someone typed: accents dropped, spaces and other
    characters removed, lower case ("José María" -> "josemaria"). Empty when nothing usable is left."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    out = "".join(c for c in plain.lower() if c.isalnum() or c in "._-")[:32]
    return out if len(out) >= 2 else ""


USERNAME_HELP = ("Login name: the name you type each time you sign in. Use 2 to 32 characters: letters, digits, "
                 "and . _ -\n  No spaces or accents. Example: jose")
PASSWORD_HELP = ("Password: at least 12 characters, and it must not contain the login name. Three or more "
                 "unrelated words\n  joined by dashes make a good password. Nothing appears on the screen while you "
                 "type it; that is normal.")


def _ask_username(default: str, ask=input) -> str:
    """Asks until the name passes the account rules; Enter takes the suggestion shown in brackets."""
    from .security.auth import validate_username
    print("  " + USERNAME_HELP)
    while True:
        entered = ask(f"Login name [{default}]: ").strip()
        name = entered or default
        try:
            validate_username(name)
            return name
        except ValueError:
            better = suggest_username(name)
            why = ("it contains a space, an accent or another character that is not allowed" if len(name) >= 2
                   else "it is too short")
            print(f'  "{name}" cannot be used: {why}.' + (f' Try "{better}" (press Enter to use it).' if better else ""))
            default = better or default


def _ask_password(username: str, ask=getpass.getpass) -> str:
    """Asks until the password passes the policy and is typed the same twice; the login name is kept."""
    from .security.auth import validate_password
    print("  " + PASSWORD_HELP)
    while True:
        pw = ask("Password: ")
        try:
            validate_password(pw, username)
        except ValueError as e:
            print(f"  That password cannot be used: {e}. Please type another one.")
            continue
        if pw == ask("Repeat the password: "):
            return pw
        print("  The two passwords were different. Please type the password again.")


def first_admin(cfg, state_dir: Path, *, username: str, password_stdin: bool, ask=input,
                ask_secret=getpass.getpass) -> bool:
    """Guided creation of the first administrator when none exists. Returns False when cancelled. Interactive
    answers that break the account rules are explained and asked again, keeping what was already accepted; the
    rules themselves (security.auth) are the same as for every other account."""
    from .cli import _db_path
    from .domain.model import new_id
    from .historian.writer import Historian
    from .security.auth import Accounts, Role, load_or_create_secret, validate_password, validate_username
    h = Historian(_db_path(cfg, state_dir), cfg, new_id("launch_setup"))
    try:
        acc = Accounts(h, load_or_create_secret(state_dir / "secret.key"), idle_minutes=30, absolute_hours=12)
        if acc.count_users():
            return True
        print("\nFirst start: create the administrator account. There is no default password.")
        if password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
            try:
                validate_username(username)
                validate_password(pw, username)
            except ValueError as e:
                print(f"cannot create the account: {e}. {USERNAME_HELP.splitlines()[0]}", file=sys.stderr)
                return False
        else:
            if not sys.stdin.isatty() and ask is input:
                print("no terminal to ask for a password: run in a console or pass --password-stdin", file=sys.stderr)
                return False
            try:
                username = _ask_username(suggest_username(username) or "admin", ask)
                pw = _ask_password(username, ask_secret)
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled; no account was created.", file=sys.stderr)
                return False
        try:
            acc.create_user(username, pw, Role.ADMIN, commissioning=False, must_change=False)
        except ValueError as e:
            print(f"cannot create the account: {e}", file=sys.stderr)
            return False
        h.event("USER_BOOTSTRAPPED", {"username": username, "role": "admin", "via": "launcher"}, actor="launcher")
        print(f"Administrator '{username}' created. Sign in with this name in the browser. Add operators and viewers "
              "under Settings, Users.")
        return True
    finally:
        h.close()


def _open_browser_when_ready(url: str, stop: threading.Event, log: logging.Logger, open_browser: bool) -> None:
    """Ready once /api/health says the simulation is running and storage accepts starts (not merely that the
    server answers: the first answers can come before the first simulation tick)."""
    end, last = time.monotonic() + 120, None
    while not stop.is_set() and time.monotonic() < end:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as r:
                last = json.load(r)
            if last.get("ok"):
                log.info("ready: %s", url)
                print(f"\nAquaCrop is running: {url}\nStop it with Ctrl+C in this window (or close the window).")
                if open_browser:
                    webbrowser.open(url)
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    if not stop.is_set():
        log.warning("not healthy after 120 s: %s", last)
        print(f"\nAquaCrop did not report healthy within 120 s ({last or 'no answer'}); see the log file.",
              file=sys.stderr)


def cmd_launch(args) -> int:
    """Starts the demo; on any failure the last lines name the data folder and log file actually in use (a
    --state-dir trial folder, not the default)."""
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_state_dir()
    rc = _launch(args, state_dir)
    if rc:
        log = state_dir / "logs" / "aquacrop-launch.log"
        print(f"\nAquaCrop did not start.\n  Data folder: {state_dir}\n  Log file:    "
              + (str(log) if log.is_file() else "none written (the checks above stopped the start before it)"),
              file=sys.stderr)
    return rc


def _launch(args, state_dir: Path) -> int:
    import uvicorn

    from .api.app import create_app
    from .ownership import OwnershipError, acquire_installation
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
    print(f"  data folder: {state_dir}\n  log file:    {log_path}")
    if not first_admin(cfg, state_dir, username=args.username, password_stdin=args.password_stdin):
        log.warning("first administrator not created; nothing started")
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
        from .release import describe, release_info
        log.info("starting %s %s on %s, state %s; %s", cfg.config_id, __version__, url, state_dir,
                 describe(release_info()))
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
    print("AquaCrop stopped cleanly; the history and accounts are kept in the data folder.")
    return 0


def add_parser(sub) -> None:
    la = sub.add_parser("launch", help="desktop demo: checks, first administrator, serve on 127.0.0.1, open browser")
    la.add_argument("--config", default="installation-80", help="a SIMULATED configuration (default installation-80)")
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
