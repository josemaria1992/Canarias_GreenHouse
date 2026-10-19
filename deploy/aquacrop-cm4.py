#!/usr/bin/env python3
"""Guided installation, upgrade and rollback of AquaCrop on the CM4 (Raspberry Pi OS 64-bit).

Run from the unpacked release folder, as root, step by step; every step says what it does and stops at
the first failure:

    sudo python3 deploy/aquacrop-cm4.py check   --release .            # read-only: is this host ready?
    sudo python3 deploy/aquacrop-cm4.py install --release . --config /etc/aquacrop/hardware-read-only.yaml
    sudo python3 deploy/aquacrop-cm4.py tls     --hostname aquacrop.lan       # TLS proxy for LAN and OpenVPN
    sudo python3 deploy/aquacrop-cm4.py upgrade --release /path/to/new/release
    sudo python3 deploy/aquacrop-cm4.py rollback
    sudo python3 deploy/aquacrop-cm4.py status

`--dry-run` prints every command and file change without doing anything. `--root DIR` stages the
installation under DIR (no user, group, permission or systemd changes; those are listed as skipped):
that is how CI exercises this script on an ARM64 runner. This script never partitions or formats a
disk, never edits the boot configuration (UART overlays are verified by hand, docs/installation/cm4.md),
never enables hardware writes (there is no such switch) and never creates an application account with
a default password. Standard library only: it runs before the application's environment exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SUPPORTED_PY = ((3, 11), (3, 12), (3, 13), (3, 14))
SERVICE = "aquacrop-greenhouse.service"
UNITS = ("aquacrop-greenhouse.service", "aquacrop-backup.service", "aquacrop-backup.timer",
         "aquacrop-restore-drill.service", "aquacrop-restore-drill.timer")
MIN_FREE = {"opt": 1 * 2**30, "var": 12 * 2**30}      # a release ~ 0.2 GiB; history budget is 10 GiB + headroom
HEALTH_URL = "http://127.0.0.1:8000/api/health"
SD_MOUNT = "/media/aquacrop-sd"                         # offsite_backup.sd_card.mount_path (offsite-backups.md)


class Fail(Exception):
    pass


class Host:
    """Where and how changes happen: the real system, a dry run, or a staging root."""

    def __init__(self, root: Path | None, dry_run: bool) -> None:
        self.root, self.dry = root, dry_run
        self.staging = root is not None
        self.skipped: list[str] = []

    def p(self, path: str) -> Path:
        return (self.root / path.lstrip("/")) if self.root else Path(path)

    def say(self, msg: str) -> None:
        print(f"  {msg}", flush=True)

    def run(self, cmd: list[str], *, system: bool = False, check: bool = True, capture: bool = False,
            input_text: str | None = None) -> subprocess.CompletedProcess | None:
        """`system` commands change users, ownership or services: skipped in a staging root."""
        line = shlex.join(cmd)
        if self.dry or (system and self.staging):
            self.say(("would run: " if self.dry else "skipped in staging root: ") + line)
            if system and self.staging:
                self.skipped.append(line)
            return None
        self.say("run: " + line)
        r = subprocess.run(cmd, check=False, text=True, capture_output=capture, input=input_text)
        if check and r.returncode != 0:
            raise Fail(f"command failed ({r.returncode}): {line}" + (f"\n{r.stderr.strip()}" if capture and r.stderr else ""))
        return r

    def mkdir(self, path: str, mode: int) -> Path:
        d = self.p(path)
        if self.dry:
            self.say(f"would create {d} (mode {mode:o})")
            return d
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, mode)
        return d

    def install_file(self, src: Path, dest: str, mode: int, *, overwrite: bool = True) -> None:
        d = self.p(dest)
        if d.exists() and not overwrite:
            self.say(f"kept existing {d}")
            return
        if self.dry:
            self.say(f"would install {src} -> {d} (mode {mode:o})")
            return
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d)
        os.chmod(d, mode)
        self.say(f"installed {d}")

    def write_file(self, dest: str, text: str, mode: int) -> None:
        d = self.p(dest)
        if self.dry:
            self.say(f"would write {d} (mode {mode:o}):\n" + "\n".join("      " + x for x in text.splitlines()))
            return
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(text, encoding="utf-8")
        os.chmod(d, mode)
        self.say(f"wrote {d}")

    def symlink(self, target: Path, link: str) -> None:
        lp = self.p(link)
        if self.dry:
            self.say(f"would point {lp} -> {target}")
            return
        tmp = lp.with_name(lp.name + ".new")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(os.path.relpath(target, lp.parent))     # relative: the same inside a staging root
        os.replace(tmp, lp)          # atomic switch
        self.say(f"{lp} -> {target}")


# ------------------------------------------------------------------------------------------ release
class Release:
    def __init__(self, folder: Path) -> None:
        self.dir = folder.resolve()
        wheels = sorted((self.dir / "wheels").glob("aquacrop_greenhouse-*.whl"))
        if len(wheels) != 1:
            raise Fail(f"{self.dir}/wheels must hold exactly one aquacrop_greenhouse wheel (found {len(wheels)})")
        self.wheel = wheels[0]
        self.version = self.wheel.name.split("-")[1]
        self.lock = self.dir / "requirements.lock"
        if not self.lock.is_file():
            raise Fail(f"{self.lock} is missing")
        self.info = json.loads((self.dir / "RELEASE.json").read_text(encoding="utf-8")) \
            if (self.dir / "RELEASE.json").is_file() else {}

    def wheelhouse(self) -> Path | None:
        tag = f"py{sys.version_info[0]}{sys.version_info[1]}"
        mach = platform.machine().lower()
        hits = sorted(self.dir.glob(f"wheelhouse-{mach}-{tag}"))
        return hits[0] if hits else None

    def verify_manifest(self) -> int:
        man = self.dir / "MANIFEST.sha256"
        if not man.is_file():
            raise Fail(f"{man} is missing: the release cannot be verified")
        n = 0
        for line in man.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, rel = line.split(None, 1)
            f = self.dir / rel.strip().lstrip("*")
            if not f.is_file():
                raise Fail(f"manifest lists {rel.strip()} but it is missing")
            h = hashlib.sha256()
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                raise Fail(f"SHA-256 mismatch for {rel.strip()}: the release folder is damaged or was changed")
            n += 1
        listed = {ln.split(None, 1)[1].strip().lstrip("*") for ln in man.read_text(encoding="utf-8").splitlines() if ln.strip()}
        for f in [self.wheel, self.lock, *self.dir.glob("wheelhouse-*/*.whl")]:
            if str(f.relative_to(self.dir)).replace(os.sep, "/") not in listed:
                raise Fail(f"{f.relative_to(self.dir)} is not in MANIFEST.sha256")
        return n


# ------------------------------------------------------------------------------------------ checks
def checks(host: Host, rel: Release | None, *, allow_arch: bool) -> list[str]:
    bad: list[str] = []

    def ok(cond: bool, what: str, fix: str = "") -> None:
        print(f"  [{'ok' if cond else 'FAIL'}] {what}" + ("" if cond or not fix else f"\n         -> {fix}"), flush=True)
        if not cond:
            bad.append(what)

    print("Checking the host (read-only)")
    mach = platform.machine().lower()
    ok(mach == "aarch64" or allow_arch, f"architecture {mach}",
       "the CM4 runs Raspberry Pi OS 64-bit (aarch64); flash the 64-bit image (docs/installation/cm4.md)")
    v = sys.version_info[:2]
    ok(v in SUPPORTED_PY, f"python {platform.python_version()}", "use the OS python3 (Bookworm ships 3.11)")
    try:
        import ensurepip  # noqa: F401
        venv_ok = True
    except ImportError:
        venv_ok = False
    ok(venv_ok, "python3-venv available", "sudo apt install python3-venv")
    osr = Path("/etc/os-release")
    pretty = next((ln.split("=", 1)[1].strip('"') for ln in osr.read_text().splitlines() if ln.startswith("PRETTY_NAME=")),
                  "unknown") if osr.is_file() else "unknown"
    print(f"  [info] OS: {pretty}")
    if not host.staging:
        ok(Path("/run/systemd/system").is_dir(), "systemd is the init system")
        ok(os.geteuid() == 0 or host.dry, "running as root (sudo)", "run with sudo")
    for key, path in (("opt", "/opt"), ("var", "/var/lib")):
        target = host.p(path)
        while not target.exists():
            target = target.parent
        free = shutil.disk_usage(target).free
        line = f"free space on {path}: {free / 2**30:.1f} GiB (need {MIN_FREE[key] / 2**30:.0f} GiB)"
        if host.staging and free < MIN_FREE[key]:
            print(f"  [info] {line}: not checked in a staging root (it is this machine's disk, not the CM4's)")
        else:
            ok(free >= MIN_FREE[key], line, "free space on the eMMC; the history budget is 10 GiB plus headroom (ADR-0008)")
    if rel is not None:
        try:
            n = rel.verify_manifest()
            ok(True, f"release {rel.version}: {n} files match MANIFEST.sha256" +
               (f" (commit {rel.info.get('commit', '?')[:12]})" if rel.info else ""))
        except Fail as e:
            ok(False, f"release verification: {e}")
        wh = rel.wheelhouse()
        ok(wh is not None, f"offline wheelhouse for this python and architecture: {wh.name if wh else 'none'}",
           f"the release needs wheelhouse-{mach}-py{v[0]}{v[1]}; build it with scripts/build_wheelhouse.sh")
    # Read-only observations for the commissioning record (nothing is changed)
    serial = sorted(str(p) for p in Path("/dev").glob("ttyAMA*")) + sorted(str(p) for p in Path("/dev").glob("ttyS0"))
    byid = sorted(str(p) for p in Path("/dev/serial/by-id").glob("*")) if Path("/dev/serial/by-id").is_dir() else []
    print(f"  [info] serial devices: {', '.join(serial + byid) or 'none'} (verify each port by hand: docs/installation/cm4.md §3)")
    sd = [ln for ln in Path("/proc/mounts").read_text().splitlines() if ln.split()[1:2] == [SD_MOUNT]] \
        if Path("/proc/mounts").is_file() else []
    print(f"  [info] backup card mount {SD_MOUNT}: {'mounted' if sd else 'not mounted'} "
          "(backups stay off until it is enrolled; nothing is ever formatted by this script)")
    return bad


# ------------------------------------------------------------------------------------------ steps
def ensure_user(host: Host) -> None:
    try:
        import pwd
        pwd.getpwnam("aquacrop")
        host.say("user aquacrop exists")
    except (KeyError, ImportError):
        host.run(["adduser", "--system", "--group", "--home", "/var/lib/aquacrop", "--no-create-home", "aquacrop"],
                 system=True)
    host.run(["usermod", "-aG", "dialout", "aquacrop"], system=True)


def install_release(host: Host, rel: Release) -> Path:
    dest = host.p(f"/opt/aquacrop/releases/{rel.version}")
    venv = dest / "venv"
    wh = rel.wheelhouse()
    if wh is None:
        raise Fail("no offline wheelhouse for this python and architecture in the release")
    if (venv / "bin" / "python").exists():
        host.say(f"release {rel.version} already installed at {dest}; reinstalling its environment")
        if not host.dry:
            shutil.rmtree(venv)
    host.mkdir(f"/opt/aquacrop/releases/{rel.version}", 0o755)
    py = str(venv / "bin" / "python")
    host.run([sys.executable, "-m", "venv", str(venv)])
    pip = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--find-links", str(wh)]
    host.run(pip + ["--require-hashes", "-r", str(rel.lock)])
    host.run(pip + ["--no-deps", str(rel.wheel)])
    host.run([py, "-m", "pip", "check"])
    for sub in ("docs", "deploy", "configs"):
        if (rel.dir / sub).is_dir() and not host.dry:
            shutil.copytree(rel.dir / sub, dest / sub, dirs_exist_ok=True)
    if not host.dry:
        (dest / "RELEASE.json").write_text(json.dumps(rel.info | {"version": rel.version}, indent=1), encoding="utf-8")
    out = host.run([py, "-m", "aquacrop", "--version"], capture=True)
    if out is not None and out.stdout.strip() != rel.version:
        raise Fail(f"installed version {out.stdout.strip()!r} is not {rel.version!r}")
    return dest


def current_release(host: Host) -> Path | None:
    cur = host.p("/opt/aquacrop/current")
    return cur.resolve() if cur.is_symlink() else None


def install_units(host: Host, rel: Release) -> None:
    for u in UNITS:
        host.install_file(rel.dir / "deploy" / u, f"/etc/systemd/system/{u}", 0o644)
    host.install_file(rel.dir / "deploy" / "journald-aquacrop.conf", "/etc/systemd/journald.conf.d/aquacrop.conf", 0o644)
    host.mkdir("/etc/aquacrop", 0o750)
    host.install_file(rel.dir / "deploy" / "aquacrop.env.example", "/etc/aquacrop/aquacrop.env", 0o640, overwrite=False)
    host.run(["chgrp", "-R", "aquacrop", "/etc/aquacrop"], system=True)
    host.mkdir("/var/lib/aquacrop", 0o700)
    host.run(["chown", "aquacrop:aquacrop", "/var/lib/aquacrop"], system=True)
    host.run(["systemctl", "daemon-reload"], system=True)


def validate_config(host: Host, py: Path, config: str) -> None:
    host.run([str(py), "-m", "aquacrop", "config", "validate", "--config", config], capture=True)
    host.say(f"configuration {config} is valid")


def health(host: Host, timeout_s: float = 90.0) -> bool:
    if host.dry or host.staging:
        host.say(f"{'would check' if host.dry else 'skipped in staging root:'} {HEALTH_URL}")
        return True
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as r:
                body = json.load(r)
                host.say(f"health: {json.dumps(body)}")
                return bool(body.get("ok"))
        except OSError:
            time.sleep(2)
    host.say("health: no answer")
    return False


def record(host: Host, line: str) -> None:
    if host.dry:
        return
    log = host.p("/var/lib/aquacrop-install.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {line}\n")


# ------------------------------------------------------------------------------------------ commands
def cmd_check(a, host: Host) -> int:
    rel = Release(Path(a.release)) if a.release else None
    bad = checks(host, rel, allow_arch=a.allow_arch)
    print("ready" if not bad else f"{len(bad)} check(s) failed")
    return 1 if bad else 0


def cmd_install(a, host: Host) -> int:
    rel = Release(Path(a.release))
    if checks(host, rel, allow_arch=a.allow_arch):
        raise Fail("fix the failed checks first; nothing was changed")
    if current_release(host) is not None and not a.reinstall:
        raise Fail(f"an installation exists ({current_release(host).name}); use upgrade, or --reinstall")
    print(f"Installing AquaCrop {rel.version}")
    ensure_user(host)
    dest = install_release(host, rel)
    install_units(host, rel)
    host.symlink(dest, "/opt/aquacrop/current")
    if a.config:
        validate_config(host, dest / "venv" / "bin" / "python", a.config)
    host.run(["systemctl", "enable", "aquacrop-greenhouse.service", "aquacrop-backup.timer",
              "aquacrop-restore-drill.timer"], system=True)
    record(host, f"install {rel.version}")
    print(f"""
Installed {rel.version}. Next, by hand (each is a deliberate step):
  1. Put the validated configuration at the path in /etc/aquacrop/aquacrop.env (AQUACROP_CONFIG).
     Start from {dest}/configs/hardware-read-only.example.yaml; fill in verified facts only.
  2. Create the first administrator (prints a one-time password to change at first sign-in):
     sudo -u aquacrop {dest}/venv/bin/python -m aquacrop users bootstrap --config <config> \\
          --state-dir /var/lib/aquacrop --username <name> --generate
  3. sudo systemctl start aquacrop-greenhouse.service   then   curl -fsS {HEALTH_URL}
  4. For LAN and OpenVPN access: sudo python3 {Path(__file__).name} tls --hostname <name> (docs/security/remote-access.md)""")
    if host.skipped:
        print(f"\n{len(host.skipped)} system step(s) skipped in the staging root.")
    return 0


def cmd_upgrade(a, host: Host) -> int:
    rel = Release(Path(a.release))
    old = current_release(host)
    if old is None:
        raise Fail("no installation found (/opt/aquacrop/current); use install")
    if old.name == rel.version and not a.reinstall:
        raise Fail(f"{rel.version} is already the current release")
    if checks(host, rel, allow_arch=a.allow_arch):
        raise Fail("fix the failed checks first; nothing was changed")
    env = host.p("/etc/aquacrop/aquacrop.env")
    config = next((ln.split("=", 1)[1].strip() for ln in env.read_text().splitlines()
                   if ln.startswith("AQUACROP_CONFIG=")), None) if env.is_file() else None
    old_py = old / "venv" / "bin" / "python"
    print(f"Upgrading {old.name} -> {rel.version}")
    if config:
        # a verified backup with the running version before anything changes (service.md, Upgrade); the
        # backup is consistent while the service runs (online SQLite backup, ADR-0008)
        dest_dir = "/var/lib/aquacrop/backups/pre-upgrade"
        r = host.run(["sudo", "-u", "aquacrop", str(old_py), "-m", "aquacrop", "backup", "create", "--config", config,
                      "--state-dir", "/var/lib/aquacrop", "--destination", dest_dir, "--keep", "3"],
                     system=True, capture=True)
        if r is not None:
            made = f"{dest_dir}/{json.loads(r.stdout)['file']}"     # the manifest names the file only
            host.run(["sudo", "-u", "aquacrop", str(old_py), "-m", "aquacrop", "backup", "verify", "--backup", made],
                     system=True, capture=True)
            host.say(f"pre-upgrade backup verified: {made}")
    else:
        host.say("no AQUACROP_CONFIG in /etc/aquacrop/aquacrop.env: no database to back up")
    dest = install_release(host, rel)
    if config:
        validate_config(host, dest / "venv" / "bin" / "python", config)
    for u in UNITS:
        host.install_file(rel.dir / "deploy" / u, f"/etc/systemd/system/{u}", 0o644)
    host.run(["systemctl", "daemon-reload"], system=True)
    host.run(["systemctl", "stop", SERVICE], system=True)
    host.write_file("/opt/aquacrop/previous", old.name + "\n", 0o644)
    host.symlink(dest, "/opt/aquacrop/current")
    host.run(["systemctl", "start", SERVICE], system=True)
    record(host, f"upgrade {old.name} -> {rel.version}")
    if not health(host):
        print(f"\nThe new release did not report healthy. Roll back with:\n  sudo python3 {Path(__file__).name} rollback\n"
              "If it already migrated the database, the old release refuses it: restore the pre-upgrade backup "
              "(docs/operations/service.md, Upgrade and rollback).", file=sys.stderr)
        return 1
    print(f"Upgraded to {rel.version}; {old.name} stays installed for rollback.")
    return 0


def cmd_rollback(a, host: Host) -> int:
    cur = current_release(host)
    if cur is None:
        raise Fail("no installation found")
    prev_file = host.p("/opt/aquacrop/previous")
    target = a.to or (prev_file.read_text().strip() if prev_file.is_file() else None)
    if not target:
        raise Fail("no previous release recorded; name one with --to <version>")
    dest = host.p(f"/opt/aquacrop/releases/{target}")
    if not (dest / "venv" / "bin" / "python").exists():
        raise Fail(f"release {target} is not installed under {dest.parent}")
    print(f"Rolling back {cur.name} -> {target}")
    host.run(["systemctl", "stop", SERVICE], system=True)
    # the target's own service units: the newer release may have changed a command line or a hardening
    # setting that the older one does not know (install_release keeps each release's deploy folder)
    for u in UNITS:
        if (dest / "deploy" / u).is_file():
            host.install_file(dest / "deploy" / u, f"/etc/systemd/system/{u}", 0o644)
    host.run(["systemctl", "daemon-reload"], system=True)
    host.write_file("/opt/aquacrop/previous", cur.name + "\n", 0o644)
    host.symlink(dest, "/opt/aquacrop/current")
    host.run(["systemctl", "start", SERVICE], system=True)
    record(host, f"rollback {cur.name} -> {target}")
    if not health(host):
        print("The previous release did not report healthy. If the newer release migrated the database, the "
              "older one refuses to open it by design: restore the pre-upgrade backup with "
              "`backup restore --maintenance` (docs/operations/service.md).", file=sys.stderr)
        return 1
    return 0


CADDY = """# AquaCrop behind TLS (written by aquacrop-cm4.py tls). LAN and OpenVPN clients only:
# the host firewall restricts port 443 to those networks (docs/security/remote-access.md).
{hostname} {{
    tls internal
    reverse_proxy 127.0.0.1:8000
}}
"""
DROPIN = """# Written by aquacrop-cm4.py tls: the app stays on 127.0.0.1 and trusts forwarded headers from the
# local TLS proxy only.
[Service]
ExecStart=
ExecStart=/opt/aquacrop/current/venv/bin/python -m aquacrop serve --config ${AQUACROP_CONFIG} --state-dir /var/lib/aquacrop --host 127.0.0.1 --port 8000 --log-level warning --behind-tls-proxy
"""


def cmd_tls(a, host: Host) -> int:
    name = a.hostname.strip().lower()
    if not name or "/" in name or " " in name:
        raise Fail("--hostname is a DNS name such as aquacrop.lan")
    print(f"TLS endpoint for https://{name}/")
    if shutil.which("caddy") is None and not host.staging:
        print("  Caddy is not installed. Install it once (needs internet), then run this step again:\n"
              "    sudo apt install caddy", file=sys.stderr)
        return 1
    host.write_file("/etc/caddy/Caddyfile", CADDY.format(hostname=name), 0o644)
    host.write_file(f"/etc/systemd/system/{SERVICE}.d/tls-proxy.conf", DROPIN, 0o644)
    host.run(["systemctl", "daemon-reload"], system=True)
    host.run(["systemctl", "restart", "caddy"], system=True)
    print(f"""
Next, by hand:
  1. In the installation config add  {name}  to security.allowed_hosts and  https://{name}  to
     security.allowed_origins, validate it, then: sudo systemctl restart {SERVICE}
  2. Copy Caddy's root certificate to each operator PC and trust it once:
     /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
  3. Restrict port 443 to the greenhouse LAN and the OpenVPN client network (deploy/nftables-aquacrop.conf.example).
  4. From a PC on the LAN, and from home with OpenVPN connected:
     python -m aquacrop netcheck --url https://{name}/ --ca-file root.crt""")
    return 0


def cmd_status(a, host: Host) -> int:
    cur = current_release(host)
    rels = sorted(p.name for p in host.p("/opt/aquacrop/releases").glob("*")) if host.p("/opt/aquacrop/releases").is_dir() else []
    print(json.dumps({"current": cur.name if cur else None, "installed": rels,
                      "previous": host.p("/opt/aquacrop/previous").read_text().strip()
                      if host.p("/opt/aquacrop/previous").is_file() else None}, indent=1))
    if not (host.dry or host.staging):
        host.run(["systemctl", "--no-pager", "status", SERVICE], check=False)
        health(host, timeout_s=5)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print every change, make none")
    ap.add_argument("--root", type=Path, help="stage under this folder (no user, ownership or systemd changes)")
    ap.add_argument("--allow-arch", action="store_true", help="accept a non-aarch64 host (CI staging only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--release")
    i = sub.add_parser("install")
    i.add_argument("--release", required=True)
    i.add_argument("--config", help="validate this installation config with the new release")
    i.add_argument("--reinstall", action="store_true")
    u = sub.add_parser("upgrade")
    u.add_argument("--release", required=True)
    u.add_argument("--reinstall", action="store_true")
    r = sub.add_parser("rollback")
    r.add_argument("--to", help="release version to switch back to (default: the previous one)")
    t = sub.add_parser("tls")
    t.add_argument("--hostname", required=True)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    host = Host(a.root.resolve() if a.root else None, a.dry_run)
    if host.staging and not host.dry:
        host.root.mkdir(parents=True, exist_ok=True)
    try:
        return {"check": cmd_check, "install": cmd_install, "upgrade": cmd_upgrade, "rollback": cmd_rollback,
                "tls": cmd_tls, "status": cmd_status}[a.cmd](a, host)
    except Fail as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
