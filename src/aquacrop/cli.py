"""Command-line interface: `python -m aquacrop <command>`."""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .domain.model import new_id


BUILTIN_CONFIGS = ("installation-80", "installation-48", "demo-48", "demo-24", "hardware-read-only.example", "bench-malmo.example")


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
                      "expected_points_formula": "sum of catalog AI + DI + DO channels for every device",
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
              "documented TLS reverse proxy (docs/security/remote-access.md), then pass --behind-tls-proxy.", file=sys.stderr)
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

    if cfg.topology == "installation":
        _verify_installation(cfg, args, record)
        return _verify_report(cfg, args, results)
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
    return _verify_report(cfg, args, results)


def _verify_installation(cfg, args, record) -> None:
    """Installation topology: process scenarios, determinism and replay, the 48-point I/O round trip."""
    from .domain.model import SignalSource
    from .twin.installation_scenarios import presets, run_installation_scenario
    from .twin.runtime import make_runtime, replay
    for name, sc in presets().items():
        rt, errs = run_installation_scenario(cfg, sc, seed=args.seed)
        errs += [f"ledger residual {f.residual_L}" for f in rt.invariant_failures[:1]]
        record(f"scenario:{name}", errs, f"{sc.duration_s:g} s simulated")

    def scripted():
        rt = make_runtime(cfg, seed=args.seed)
        for s in ("A", "B", "ACID"):
            rt.schedule(0, "STOCK_ACK", "verify", {"stock": s, "note": "verify"})
        for pid in ("IRRIGATION", "RETURN", "REFILL", "ACID"):
            rt.schedule(0, "PROCESS_REQUEST", "verify", {"process": pid, "target": "AUTO"})
        rt.schedule(200, "OVERRIDE_SET", "verify", {"point": cfg.point_for_signal("main.ph"), "raw": 12000,
                                                     "reason": "verify", "duration_s": 20})
        rt.schedule(400, "STOP_ALL", "verify", {"reason": "verify"})
        rt.run_for(450)
        return rt
    a, b = scripted(), scripted()
    record("determinism:same-seed-same-hash", [] if a.state_hash() == b.state_hash() else ["hash differs"],
           a.state_hash()[:16])
    r = replay(cfg, a.seed, a.journal, a.tick, **a.replay_kwargs())
    record("replay:journal-reproduces-hash", [] if r.state_hash() == a.state_hash() else ["replay hash differs"])
    rt = make_runtime(cfg, seed=args.seed)
    ok, msg = rt.set_source("verify", SignalSource.IO_TEST)
    errs = [] if ok else [msg]
    for pid in cfg.all_points():
        p = str(pid)
        if pid.kind.value == "DO":
            for v in (True, False):
                rec = rt.operator_command("verify", p, v, "verify: I/O round trip")
                if rec.outcome.value != "READBACK_CONFIRMED":
                    errs.append(f"{p}={v}: {rec.outcome.value} {rec.detail}")
        else:
            rt.inject("verify", p, True if pid.kind.value == "DI" else 12000)
            if rt.samples[p].quality.value != "GOOD":
                errs.append(f"{p}: {rt.samples[p].quality.value}")
    record(f"io:all-{len(cfg.all_points())}-points-round-trip", errs, f"{len(cfg.all_points())} points on the I/O test source")
    bad = [m for m in sys.modules if m.split(".")[0] in ("serial", "pymodbus")]
    record("isolation:no-serial-imports", [f"imported {bad}"] if bad else [])
    n = len(cfg.all_points())
    record("inventory:point-count", [] if n == cfg.expected_point_count() else [f"{n} != formula"], f"{n} points")


def _verify_report(cfg, args, results) -> int:
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
    from .historian.backup import BackupAborted, BackupFailed, BackupRefused, create_backup
    cfg = _cfg(args.config)
    h = cfg.historian
    try:
        man = create_backup(_db_path(cfg, _state_dir(args)), args.destination, config_id=cfg.config_id,
                            config_version=cfg.config_version, keep=args.keep,
                            reserve_bytes=h.free_space_reserve_bytes, wal_abort_bytes=h.wal_reader_abort_bytes,
                            max_seconds=h.backup_max_seconds)
    except BackupRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    except (BackupAborted, BackupFailed) as e:
        print(f"FAILED (nothing published; earlier backups kept): {e}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in man.items() if k != "fingerprints"}, indent=2))
    return 0


def cmd_backup_verify(args) -> int:
    """Without --full: checksum, stream and row counts. With --full: the restore drill (restore into
    --scratch-dir, integrity/foreign keys/schema/counts/fingerprints, application smoke test)."""
    from .config.schema import HistorianSettings
    from .historian.backup import verify_backup
    cfg = _cfg(args.config) if args.config else None
    reserve = (cfg.historian if cfg else HistorianSettings()).free_space_reserve_bytes
    v = verify_backup(args.backup, full=args.full, scratch_dir=args.scratch_dir, reserve_bytes=reserve, cfg=cfg,
                      report_dir=args.report_dir)
    if not args.verbose and isinstance(v.get("manifest"), dict):
        v["manifest"] = {k: x for k, x in v["manifest"].items() if k != "fingerprints"}
    print(json.dumps(v, indent=2, default=str))
    return 0 if v["ok"] else 1


def cmd_storage_report(args) -> int:
    """Measured capacity of an installation's database (read-only; safe while the server runs)."""
    import sqlite3

    from .historian.writer import capacity_report
    cfg = _cfg(args.config)
    db = _db_path(cfg, _state_dir(args))
    if not db.exists():
        print(f"no database at {db}", file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rep = capacity_report(con, db, cfg, detail=True)
    finally:
        con.close()
    print(json.dumps(rep, indent=2))
    return 0


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
    from .config.schema import HistorianSettings
    from .historian.backup import _same_device
    # The free-space reserve protects the live database's disk; a restore elsewhere only needs room.
    reserve = (_cfg(args.config).historian if args.config else HistorianSettings()).free_space_reserve_bytes
    target.parent.mkdir(parents=True, exist_ok=True)
    if _state_dir(args).exists() and not _same_device(target.parent, _state_dir(args)):
        reserve = 0
    res = restore_backup(args.backup, target, allow_existing=args.maintenance, reserve_bytes=reserve)
    print(json.dumps(res, indent=2, default=str))
    return 0


# ------------------------------------------------------------------------------------------ offsite (ADR-0012)
def _offsite(args):
    from .historian.offsite import Offsite
    cfg = _cfg(args.config)
    sd = _state_dir(args)
    return Offsite(cfg, sd, _db_path(cfg, sd))


def _offsite_run(args, op) -> int:
    """Exit codes: 0 done, 1 failed (recorded), 2 not activated, 75 another offsite job is running."""
    from .historian.offsite import Busy, NotActivated
    off = _offsite(args)
    try:
        with off:
            res = op(off)
    except NotActivated as e:
        print(f"NOT ACTIVATED: {e}", file=sys.stderr)
        return 2
    except Busy as e:
        print(f"BUSY: {e}", file=sys.stderr)
        return 75
    print(json.dumps(res, indent=2, default=str))
    bad = (isinstance(res, dict) and (res.get("ok") is False or (res.get("backup") or {}).get("ok") is False
                                      or bool(res.get("failed"))))
    return 1 if bad else 0


def cmd_offsite_backup(args) -> int:
    return _offsite_run(args, lambda o: o.backup(actor="timer" if args.timer else "cli"))


def cmd_offsite_upload(args) -> int:
    return _offsite_run(args, lambda o: o.upload(force=args.now))


def cmd_offsite_drill(args) -> int:
    return _offsite_run(args, lambda o: o.drill(actor="timer" if args.timer else "cli"))


def cmd_offsite_plan(args) -> int:
    """What the copy policy would delete now, and whether it is confirmed. Deletes nothing."""
    from .historian.sdcard import VolumeError

    def op(o):
        try:
            card = o.card()
        except VolumeError:
            card = None
        listing = None
        if o.rclone is not None and args.list_cloud:
            listing = o.rclone.listing()
        return o.plan(card, listing)
    return _offsite_run(args, op)


def cmd_offsite_status(args) -> int:
    from .historian.offsite import OffsiteMonitor
    cfg = _cfg(args.config)

    class _NoHist:
        def query(self, *a):
            return []
    mon = OffsiteMonitor(cfg, _state_dir(args), _NoHist())
    print(json.dumps(mon.view(), indent=2, default=str))
    return 0


def cmd_offsite_check(args) -> int:
    """Verify the card's identity and that the cloud remote is an rclone crypt remote. Writes nothing
    to the card or the cloud."""
    from .historian.rclone import RcloneError
    from .historian.sdcard import VolumeError
    off = _offsite(args)
    out, rc = {}, 0
    try:
        out["sd_card"] = {"ok": True} | off.card().view()
    except VolumeError as e:
        out["sd_card"], rc = {"ok": False, "code": e.code, "error": str(e)}, 1
    if off.rclone is None:
        out["cloud"] = {"ok": False, "error": "cloud.remote is not configured (TBD)"}
        rc = 1
    else:
        try:
            off.rclone.check_crypt()
            out["cloud"] = {"ok": True, "remote": off.s.cloud.remote, "copies_listed": len(off.rclone.listing())}
        except RcloneError as e:
            out["cloud"], rc = {"ok": False, "code": e.code, "error": str(e)}, 1
    out["activated"] = off.s.enabled
    print(json.dumps(out, indent=2))
    return rc


def cmd_offsite_enroll(args) -> int:
    """Mark the card the owner chose (it must be mounted at --mount and have filesystem --uuid) and print
    the values to put into the configuration's offsite_backup.sd_card section."""
    from .historian.sdcard import VolumeError, enroll_card
    cfg = _cfg(args.config)
    sd = _state_dir(args)
    try:
        res = enroll_card(args.mount, args.uuid, live_paths=[_db_path(cfg, sd).parent, sd], note=args.note or "")
    except VolumeError as e:
        print(f"REFUSED ({e.code}): {e}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
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
    """Deletes nothing unless --confirm-cutoff echoes the cutoff this command proposes for the current
    clock: the operator checks the date and the counts first (the same rule as the server's hold)."""
    import time as _time

    from .historian.retention import ProposalMismatch, confirm_cutoff, prune_expired, retention_proposal
    h, lk = _maintenance_historian(args, "cli_retention")
    try:
        prop = retention_proposal(h, wall_s=_time.time())
        if args.dry_run or not args.confirm_cutoff or prop["cutoff_utc"] is None:
            print(json.dumps(prop, indent=2))
            if args.dry_run or prop["cutoff_utc"] is None:
                return 0
            print(f"\nNothing deleted. Check the date and time, then run again with --confirm-cutoff "
                  f"{prop['cutoff_utc']}", file=sys.stderr)
            return 3
        try:
            prop = confirm_cutoff(h, args.confirm_cutoff, wall_s=_time.time(), trusted_s=None,
                                  actor=f"cli:{getpass.getuser()}", hold=None)
        except ProposalMismatch as e:
            print(json.dumps(e.proposal, indent=2))
            print(f"REFUSED: {e}", file=sys.stderr)
            return 4
        res = prune_expired(h, now=datetime.fromisoformat(prop["cutoff_utc"]) + timedelta(days=h.cfg.historian.retention_days))
        print(json.dumps(res, indent=2))
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
    bc.add_argument("--keep", type=int, default=None,
                    help="after the new backup verifies, keep only this many compressed backups")
    bc.set_defaults(fn=cmd_backup_create)
    bv = bk.add_parser("verify")
    bv.add_argument("--backup", required=True)
    bv.add_argument("--full", action="store_true",
                    help="restore drill: restore into a scratch DB, check integrity, foreign keys, schema, counts, "
                         "fingerprints and run the application smoke test")
    bv.add_argument("--scratch-dir", default=None,
                    help="where --full restores (needs room for one database plus 20 %%; on the live database's "
                         "disk the free-space reserve must also remain)")
    bv.add_argument("--config", default=None, help="also start the historian on the restored copy with this config")
    bv.add_argument("--report-dir", default=None, help="write a restore-drill report (JSON) here")
    bv.add_argument("--verbose", action="store_true", help="include the per-table fingerprints in the output")
    bv.set_defaults(fn=cmd_backup_verify)
    br = bk.add_parser("restore")
    br.add_argument("--backup", required=True)
    br.add_argument("--target", required=True)
    br.add_argument("--maintenance", action="store_true")
    br.add_argument("--config")
    br.add_argument("--state-dir", default="var")
    br.set_defaults(fn=cmd_backup_restore)

    of = sub.add_parser("offsite", help="SD-card backups and encrypted cloud copies (ADR-0012)").add_subparsers(
        dest="sub", required=True)
    for name, fn, hlp in (("backup", cmd_offsite_backup, "daily: SD-card backup, queue, upload, prune if confirmed"),
                          ("upload", cmd_offsite_upload, "upload queued backups that are due"),
                          ("drill", cmd_offsite_drill, "restore drill from the newest verified cloud copy"),
                          ("plan", cmd_offsite_plan, "show what the copy policy would delete (deletes nothing)"),
                          ("status", cmd_offsite_status, "backup, upload, drill and policy state"),
                          ("check", cmd_offsite_check, "verify the card identity and the crypt remote")):
        x = of.add_parser(name, help=hlp)
        common(x)
        x.set_defaults(fn=fn)
        if name in ("backup", "drill"):
            x.add_argument("--timer", action="store_true", help="started by the systemd timer (recorded as actor)")
        if name == "upload":
            x.add_argument("--now", action="store_true", help="ignore the retry backoff")
        if name == "plan":
            x.add_argument("--list-cloud", action="store_true", help="also list the cloud folder")
    en = of.add_parser("enroll-card", help="write the enrolment marker on the chosen SD card")
    common(en)
    en.add_argument("--mount", required=True)
    en.add_argument("--uuid", required=True, help="filesystem UUID of the card (blkid)")
    en.add_argument("--note", default=None)
    en.set_defaults(fn=cmd_offsite_enroll)

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
    ra = rt.add_parser("apply", help="delete history older than the APPROVED rolling window (server stopped)")
    common(ra)
    ra.add_argument("--dry-run", action="store_true", help="show the proposed cutoff and counts only")
    ra.add_argument("--confirm-cutoff", default=None,
                    help="the cutoff_utc shown by a run without this option, after checking the date and time")
    ra.set_defaults(fn=cmd_retention_apply)
    sr = sub.add_parser("storage").add_subparsers(dest="sub", required=True)
    srr = sr.add_parser("report", help="measured database growth, projection and free-space thresholds")
    common(srr)
    srr.set_defaults(fn=cmd_storage_report)

    mg = sub.add_parser("migrate-v01", help="backup-first import of a v0.1 greenhouse.sqlite3")
    common(mg)
    mg.add_argument("--legacy-db", required=True)
    mg.set_defaults(fn=cmd_migrate_v01)

    from .drivers.diagnostics import add_parser as add_diag   # light: pymodbus loads only when run
    add_diag(sub)
    from .launcher import add_parser as add_launch
    add_launch(sub)
    from .netcheck import add_parser as add_netcheck
    add_netcheck(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
