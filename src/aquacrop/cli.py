"""Command-line interface: `python -m aquacrop <command>`."""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path

from . import __version__
from .domain.model import new_id


BUILTIN_CONFIGS = ("demo-48", "demo-24", "hardware-read-only.example")


def resolve_config_path(path: str) -> Path:
    """A filesystem path, or the name of a packaged fixture (works from an installed wheel)."""
    p = Path(path)
    if p.exists():
        return p
    if path in BUILTIN_CONFIGS:
        from importlib import resources
        return Path(str(resources.files("aquacrop") / "configs" / f"{path}.yaml"))
    raise FileNotFoundError(f"config {path} not found (built-in names: {', '.join(BUILTIN_CONFIGS)})")


def _cfg(path: str):
    from .config import load_config
    return load_config(resolve_config_path(path))


def _state_dir(args) -> Path:
    return Path(args.state_dir).resolve()


def _db_path(cfg, state_dir: Path) -> Path:
    p = Path(cfg.historian.db_path)
    return p if p.is_absolute() else state_dir / p.name


# ------------------------------------------------------------------------------------------ config
def cmd_config_validate(args) -> int:
    try:
        cfg = _cfg(args.config)
    except Exception as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"valid": True, "config_id": cfg.config_id, "config_version": cfg.config_version,
                      "profile": cfg.profile.value, "devices": len(cfg.devices), "points": len(cfg.all_points()),
                      "expected_points_formula": "8 x AI_modules + 16 x RelayD_modules",
                      "expected_points": cfg.expected_point_count()}, indent=2))
    return 0


# ------------------------------------------------------------------------------------------ users
def cmd_users_bootstrap(args) -> int:
    from .historian.writer import Historian
    from .security.auth import Accounts, Role, load_or_create_secret
    cfg = _cfg(args.config)
    sd = _state_dir(args)
    h = Historian(_db_path(cfg, sd), cfg, new_id("cli_bootstrap"))
    try:
        acc = Accounts(h, load_or_create_secret(sd / "secret.key"), idle_minutes=30, absolute_hours=12)
        if acc.count_users() and not args.additional:
            print("users already exist; bootstrap refused (use the Settings view, or --additional)", file=sys.stderr)
            return 3
        if args.password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
            generated = False
        elif sys.stdin.isatty() and not args.generate:
            pw = getpass.getpass("New admin password (12+ chars): ")
            if pw != getpass.getpass("Repeat: "):
                print("passwords differ", file=sys.stderr)
                return 2
            generated = False
        else:
            pw = secrets.token_urlsafe(18)
            generated = True
        acc.create_user(args.username, pw, Role(args.role), commissioning=False, must_change=generated)
        h.event("USER_BOOTSTRAPPED", {"username": args.username, "role": args.role}, actor="cli")
        print(f"created {args.role} '{args.username}'")
        if generated:
            print(f"one-time password (shown once, must be changed at first login): {pw}")
        return 0
    finally:
        h.close()


# ------------------------------------------------------------------------------------------ serve
def cmd_serve(args) -> int:
    import ipaddress

    import uvicorn

    from .api.app import create_app
    from .ownership import OwnershipError, acquire_installation
    cfg = _cfg(args.config)
    sd = _state_dir(args)
    try:
        loop = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loop = args.host == "localhost"
    if not loop and not args.behind_tls_proxy:
        print("refusing to bind a non-loopback interface over plain HTTP. Bind 127.0.0.1 and publish through the "
              "documented TLS reverse proxy (or Tailscale Serve), then pass --behind-tls-proxy.", file=sys.stderr)
        return 2
    if cfg.profile.value not in ("SIMULATED", "HARDWARE_READ_ONLY"):
        print(f"profile {cfg.profile.value} is not served by this release", file=sys.stderr)
        return 2
    if cfg.profile.value == "HARDWARE_READ_ONLY":
        print("REAL — READ ONLY: polling only; bench writes need a commissioning user to arm a lease in the UI",
              file=sys.stderr)
    try:
        locks = acquire_installation(sd, cfg)
    except OwnershipError as e:
        print(str(e), file=sys.stderr)
        return 4
    try:
        try:
            app = create_app(cfg, state_dir=sd, bind_host=args.host if not args.behind_tls_proxy else "0.0.0.0")
        except Exception as e:   # e.g. wrong pymodbus version, database of another profile
            print(f"cannot start: {e}", file=sys.stderr)
            return 3
        uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level=args.log_level,
                    proxy_headers=args.behind_tls_proxy, forwarded_allow_ips="127.0.0.1" if args.behind_tls_proxy else None)
    finally:
        for lk in locks:
            lk.release()
    return 0


# ------------------------------------------------------------------------------------------ demo verify
def cmd_demo_verify(args) -> int:
    """Headless deterministic acceptance walkthrough. No serial access. Nonzero on any failed invariant."""
    from .twin.runtime import SimulationRuntime, replay
    from .twin.scenarios import presets, run_scenario
    cfg = _cfg(args.config)
    if cfg.profile.value != "SIMULATED":
        print("demo verify requires a SIMULATED configuration", file=sys.stderr)
        return 2
    results = []

    def record(name, errs, extra=""):
        results.append((name, "PASS" if not errs else "FAIL", errs, extra))

    for name, sc in presets().items():
        try:
            rt, errs = run_scenario(cfg, sc, seed=args.seed)
            errs += [f"ledger residual {f.residual_L}" for f in rt.invariant_failures[:1]]
            record(f"scenario:{name}", errs, f"max|residual|={rt.max_abs_residual_L:.2e} L")
        except KeyError as e:
            record(f"scenario:{name}", [], f"NOT APPLICABLE to this inventory ({e})")
    # determinism + replay
    def scripted():
        rt = SimulationRuntime(cfg, seed=args.seed)
        rt.schedule(0, "STATE_REQUEST", "verify", {"target": "AUTO"})
        rt.schedule(45, "OVERRIDE_SET", "verify", {"point": cfg.point_for_signal("reservoir.level_transmitter"),
                                                   "raw": 9000, "reason": "verify", "duration_s": 5})
        rt.schedule(90, "STATE_REQUEST", "verify", {"target": "STOPPED"})
        rt.run_for(120)
        return rt
    a, b = scripted(), scripted()
    record("determinism:same-seed-same-hash", [] if a.state_hash() == b.state_hash() else ["hash differs"],
           a.state_hash()[:16])
    r = replay(cfg, a.seed, a.journal, a.tick)
    record("replay:journal-reproduces-hash", [] if r.state_hash() == a.state_hash() else ["replay hash differs"])
    # isolation: simulation must not have imported serial libraries
    bad = [m for m in sys.modules if m.split(".")[0] in ("serial", "pymodbus")]
    record("isolation:no-serial-imports", [f"imported {bad}"] if bad else [])
    # inventory
    n = len(cfg.all_points())
    record("inventory:point-count", [] if n == cfg.expected_point_count() else [f"{n} != formula"], f"{n} points")
    w = max(len(x[0]) for x in results)
    for name, st, errs, extra in results:
        print(f"{st:5} {name:<{w}} {extra}")
        for e in errs:
            print(f"      - {e}")
    failed = [x for x in results if x[1] != "PASS"]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS · config {cfg.config_id}@{cfg.config_version} · "
          f"seed {args.seed} · software {__version__}")
    return 1 if failed else 0


# ------------------------------------------------------------------------------------------ backup
def cmd_backup_create(args) -> int:
    from .historian.backup import create_backup
    cfg = _cfg(args.config)
    man = create_backup(_db_path(cfg, _state_dir(args)), args.destination, config_id=cfg.config_id,
                        config_version=cfg.config_version)
    print(json.dumps(man, indent=2))
    return 0


def cmd_backup_verify(args) -> int:
    from .historian.backup import verify_backup
    v = verify_backup(args.backup)
    print(json.dumps(v, indent=2, default=str))
    return 0 if v["ok"] else 1


def cmd_backup_restore(args) -> int:
    from .historian.backup import restore_backup
    from .ownership import OwnershipError, OwnerLock, lock_name
    target = Path(args.target)
    if target.exists():
        if not args.maintenance or not args.config:
            print("target exists. Restore to a new instance first; overwriting requires --maintenance and --config "
                  "(to prove the server is stopped).", file=sys.stderr)
            return 2
        if args.config:
            cfg = _cfg(args.config)
            try:
                lk = OwnerLock(_state_dir(args) / lock_name("config", cfg.config_id)).acquire()
                lk.release()
            except OwnershipError:
                print("the installation is running; stop it before a maintenance restore", file=sys.stderr)
                return 4
    res = restore_backup(args.backup, target, allow_existing=args.maintenance)
    print(json.dumps(res, indent=2, default=str))
    return 0


# ------------------------------------------------------------------------------------------ archive / retention
def _maintenance_historian(args, actor: str):
    """Open the installation's database only while no server owns it."""
    from .historian.writer import Historian
    from .ownership import OwnershipError, OwnerLock, lock_name
    cfg = _cfg(args.config)
    sd = _state_dir(args)
    try:
        lk = OwnerLock(sd / lock_name("config", cfg.config_id)).acquire()
    except OwnershipError:
        raise SystemExit("stop the running server first (archive/retention are maintenance operations)")
    return Historian(_db_path(cfg, sd), cfg, new_id(actor)), lk


def cmd_archive_create(args) -> int:
    from .historian.retention import create_archive
    h, lk = _maintenance_historian(args, "cli_archive")
    try:
        res = create_archive(h, args.start, args.end, args.destination)
        print(json.dumps(res, indent=2))
        return 0
    except (ValueError, FileExistsError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    finally:
        h.close()
        lk.release()


def cmd_archive_verify(args) -> int:
    from .historian.retention import verify_archive
    v = verify_archive(args.archive)
    print(json.dumps({k: v[k] for k in ("ok", "problems")}, indent=2))
    return 0 if v["ok"] else 1


def cmd_retention_apply(args) -> int:
    from .historian.retention import apply_retention
    h, lk = _maintenance_historian(args, "cli_retention")
    try:
        print(json.dumps(apply_retention(h, dry_run=args.dry_run), indent=2))
        return 0
    finally:
        h.close()
        lk.release()


# ------------------------------------------------------------------------------------------ v0.1 migration
def cmd_migrate_v01(args) -> int:
    from .historian.legacy_v01 import import_legacy
    from .historian.writer import Historian
    from .ownership import OwnershipError, OwnerLock, lock_name
    cfg = _cfg(args.config)
    sd = _state_dir(args)
    try:
        lk = OwnerLock(sd / lock_name("config", cfg.config_id)).acquire()
    except OwnershipError:
        print("stop the running server before migrating", file=sys.stderr)
        return 4
    h = Historian(_db_path(cfg, sd), cfg, new_id("cli_migrate"))
    try:
        res = import_legacy(args.legacy_db, h, sd / "backups")
        h.event("LEGACY_V01_IMPORTED", {k: v for k, v in res.items() if k != "settings_recorded_not_applied"},
                actor="cli")
        print(json.dumps(res, indent=2))
        return 0
    except (ValueError, FileExistsError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    finally:
        h.close()
        lk.release()


# ------------------------------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aquacrop", description=f"AquaCrop greenhouse platform {__version__}")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, config=True):
        if config:
            sp.add_argument("--config", required=True)
        sp.add_argument("--state-dir", default="var", help="private state: database, secret key, locks, backups")

    c = sub.add_parser("config").add_subparsers(dest="sub", required=True)
    v = c.add_parser("validate")
    v.add_argument("--config", required=True)
    v.set_defaults(fn=cmd_config_validate)
    # (`--config` everywhere accepts a path or a packaged fixture name: demo-48, demo-24)

    u = sub.add_parser("users").add_subparsers(dest="sub", required=True)
    b = u.add_parser("bootstrap", help="create the first admin; no default credentials exist")
    common(b)
    b.add_argument("--username", default="admin")
    b.add_argument("--role", default="admin", choices=["viewer", "operator", "admin"])
    b.add_argument("--password-stdin", action="store_true")
    b.add_argument("--generate", action="store_true", help="generate a one-time password (must be changed)")
    b.add_argument("--additional", action="store_true")
    b.set_defaults(fn=cmd_users_bootstrap)

    s = sub.add_parser("serve")
    common(s)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--behind-tls-proxy", action="store_true")
    s.add_argument("--log-level", default="info")
    s.set_defaults(fn=cmd_serve)

    d = sub.add_parser("demo").add_subparsers(dest="sub", required=True)
    dv = d.add_parser("verify")
    dv.add_argument("--config", required=True)
    dv.add_argument("--seed", type=int, default=42)
    dv.set_defaults(fn=cmd_demo_verify)

    bk = sub.add_parser("backup").add_subparsers(dest="sub", required=True)
    bc = bk.add_parser("create")
    common(bc)
    bc.add_argument("--destination", required=True)
    bc.set_defaults(fn=cmd_backup_create)
    bv = bk.add_parser("verify")
    bv.add_argument("--backup", required=True)
    bv.set_defaults(fn=cmd_backup_verify)
    br = bk.add_parser("restore")
    br.add_argument("--backup", required=True)
    br.add_argument("--target", required=True)
    br.add_argument("--maintenance", action="store_true")
    br.add_argument("--config")
    br.add_argument("--state-dir", default="var")
    br.set_defaults(fn=cmd_backup_restore)

    ar = sub.add_parser("archive").add_subparsers(dest="sub", required=True)
    ac = ar.add_parser("create", help="export a UTC range of samples+events with checksummed manifest")
    common(ac)
    ac.add_argument("--start", required=True, help="ISO time with offset, inclusive")
    ac.add_argument("--end", required=True, help="ISO time with offset, exclusive")
    ac.add_argument("--destination", required=True)
    ac.set_defaults(fn=cmd_archive_create)
    av = ar.add_parser("verify")
    av.add_argument("--archive", required=True)
    av.set_defaults(fn=cmd_archive_verify)
    rt = sub.add_parser("retention").add_subparsers(dest="sub", required=True)
    ra = rt.add_parser("apply", help="prune archived samples older than an APPROVED retention window")
    common(ra)
    ra.add_argument("--dry-run", action="store_true")
    ra.set_defaults(fn=cmd_retention_apply)

    mg = sub.add_parser("migrate-v01", help="backup-first import of a v0.1 greenhouse.sqlite3")
    common(mg)
    mg.add_argument("--legacy-db", required=True)
    mg.set_defaults(fn=cmd_migrate_v01)

    from .drivers.diagnostics import add_parser as add_diag   # light: pymodbus loads only when run
    add_diag(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
