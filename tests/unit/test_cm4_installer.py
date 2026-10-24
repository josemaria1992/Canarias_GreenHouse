"""DL-02: the guided CM4 installer (`deploy/aquacrop-cm4.py`) in a staging root.

Install, upgrade, rollback and the TLS step run against fake release folders under a temporary root;
pip and venv are replaced by a recorder, and user, ownership and systemd steps are skipped by the
script itself in a staging root (they are listed). CI also runs the real thing, still staged, on an
ARM64 runner from the built release ZIP (job `release`). Nothing here touches a CM4."""
import hashlib
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the installer targets Linux (symlinks, systemd)")


@pytest.fixture
def cm4(monkeypatch):
    spec = importlib.util.spec_from_file_location("aquacrop_cm4", ROOT / "deploy" / "aquacrop-cm4.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    def fake_run(cmd, check=False, text=True, capture_output=False, input=None):
        calls.append(cmd)
        if "venv" in cmd and cmd[cmd.index("venv") - 1] == "-m":
            (Path(cmd[-1]) / "bin").mkdir(parents=True, exist_ok=True)
            (Path(cmd[-1]) / "bin" / "python").write_text("")
        out = ""
        if cmd[-1] == "--version":
            out = Path(cmd[0]).parents[2].name + "\n"      # releases/<version>/venv/bin/python
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.calls = calls
    return mod


def release(base: Path, version: str) -> Path:
    """A fake release folder; its service unit names its version, so the installed units show which release
    they came from."""
    r = base / f"rel-{version}"
    (r / "wheels").mkdir(parents=True)
    (r / "wheels" / f"aquacrop_greenhouse-{version}-py3-none-any.whl").write_bytes(b"wheel " + version.encode())
    wh = r / f"wheelhouse-{platform.machine().lower()}-py{sys.version_info[0]}{sys.version_info[1]}"
    wh.mkdir()
    (wh / "dep-1.0-py3-none-any.whl").write_bytes(b"dep")
    shutil.copy2(ROOT / "requirements.lock", r / "requirements.lock")
    shutil.copytree(ROOT / "deploy", r / "deploy")
    unit = r / "deploy" / "aquacrop-greenhouse.service"
    unit.write_bytes(unit.read_bytes() + f"# release {version}\n".encode())
    shutil.copytree(ROOT / "configs", r / "configs")
    (r / "RELEASE.json").write_text(json.dumps({"version": version, "commit": "0" * 40}))
    files = sorted(p for p in r.rglob("*") if p.is_file())
    (r / "MANIFEST.sha256").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(r).as_posix()}\n" for p in files))
    return r


def test_install_upgrade_rollback_in_a_staging_root(cm4, tmp_path, capsys):
    stage = tmp_path / "root"
    r1, r2 = release(tmp_path, "9.9.0"), release(tmp_path, "9.9.1")
    args = ["--root", str(stage), "--allow-arch"]
    assert cm4.main(args + ["check", "--release", str(r1)]) == 0
    assert cm4.main(args + ["install", "--release", str(r1), "--config", str(r1 / "configs" / "installation-48.yaml")]) == 0
    cur = stage / "opt" / "aquacrop" / "current"
    assert cur.is_symlink() and Path(cur.readlink()).as_posix() == "releases/9.9.0" and cur.resolve().name == "9.9.0"
    units = stage / "etc" / "systemd" / "system"
    for u in cm4.UNITS:
        assert (units / u).read_bytes() == (r1 / "deploy" / u).read_bytes()
    env = stage / "etc" / "aquacrop" / "aquacrop.env"
    assert env.stat().st_mode & 0o777 == 0o640 and (stage / "var" / "lib" / "aquacrop").stat().st_mode & 0o777 == 0o700
    out = capsys.readouterr().out
    assert "skipped in staging root: chown aquacrop:aquacrop /var/lib/aquacrop" in out
    assert "skipped in staging root: systemctl enable" in out and "--generate" in out
    pips = [c for c in cm4.calls if "pip" in c and "install" in c]
    assert all("--no-index" in c for c in pips) and any("--require-hashes" in c for c in pips)
    assert cm4.main(args + ["install", "--release", str(r1)]) == 2           # an installation exists
    # upgrade: config kept, previous recorded, atomic switch
    env.write_text(f"AQUACROP_CONFIG={r2 / 'configs' / 'installation-48.yaml'}\n")
    assert cm4.main(args + ["upgrade", "--release", str(r2)]) == 0
    assert cur.resolve().name == "9.9.1" and (stage / "opt" / "aquacrop" / "previous").read_text().strip() == "9.9.0"
    assert (units / "aquacrop-greenhouse.service").read_text().endswith("# release 9.9.1\n")
    out = capsys.readouterr().out
    assert "backup create" in out and "--destination /var/lib/aquacrop/backups/pre-upgrade" in out   # before the switch
    assert out.index("backup create") < out.index("systemctl stop aquacrop-greenhouse.service")
    assert env.read_text().startswith("AQUACROP_CONFIG=")                      # never overwritten
    assert cm4.main(args + ["upgrade", "--release", str(r2)]) == 2           # already current
    assert cm4.main(["--root", str(stage), "rollback"]) == 0
    assert cur.resolve().name == "9.9.0" and (stage / "opt" / "aquacrop" / "previous").read_text().strip() == "9.9.1"
    assert (units / "aquacrop-greenhouse.service").read_text().endswith("# release 9.9.0\n")   # its own units again
    assert cm4.main(["--root", str(stage), "rollback", "--to", "1.0.0"]) == 2
    log = (stage / "var" / "lib" / "aquacrop-install.log").read_text()
    assert [ln.split(" ", 1)[1] for ln in log.splitlines()] == ["install 9.9.0", "upgrade 9.9.0 -> 9.9.1",
                                                                "rollback 9.9.1 -> 9.9.0"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rel-9.9.0", "rel-9.9.1", "root"]   # nothing outside


def test_damaged_release_and_dry_run_change_nothing(cm4, tmp_path, capsys):
    r = release(tmp_path, "9.9.0")
    (r / "deploy" / "aquacrop-greenhouse.service").write_text("tampered")
    stage = tmp_path / "root"
    assert cm4.main(["--root", str(stage), "--allow-arch", "install", "--release", str(r)]) == 2
    assert "SHA-256 mismatch for deploy/aquacrop-greenhouse.service" in capsys.readouterr().out
    assert not (stage / "opt").exists()
    r2 = release(tmp_path / "b", "9.9.0")
    dry = tmp_path / "dry"
    assert cm4.main(["--dry-run", "--root", str(dry), "--allow-arch", "install", "--release", str(r2)]) == 0
    assert not dry.exists() and cm4.calls == []
    out = capsys.readouterr().out
    assert "would run:" in out and "would install" in out and "would point" in out


def test_tls_step_writes_the_proxy_and_dropin_only(cm4, tmp_path, capsys):
    stage = tmp_path / "root"
    assert cm4.main(["--root", str(stage), "tls", "--hostname", "aquacrop.lan"]) == 0
    caddy = (stage / "etc" / "caddy" / "Caddyfile").read_text()
    assert "aquacrop.lan {" in caddy and "tls internal" in caddy and "reverse_proxy 127.0.0.1:8000" in caddy
    drop = (stage / "etc" / "systemd" / "system" / "aquacrop-greenhouse.service.d" / "tls-proxy.conf").read_text()
    assert "--host 127.0.0.1" in drop and "--behind-tls-proxy" in drop and "0.0.0.0" not in drop
    assert "netcheck --url https://aquacrop.lan/" in capsys.readouterr().out
    assert cm4.main(["--root", str(stage), "tls", "--hostname", "http://x/"]) == 2


def test_the_installer_never_formats_partitions_or_touches_boot_config():
    src = (ROOT / "deploy" / "aquacrop-cm4.py").read_text()
    for word in ("mkfs", "parted", "fdisk", "wipefs", "sfdisk", "dd if=", "config.txt\"", "/boot/firmware"):
        assert word not in src, word
    assert "HYDRO_BENCH_WRITES" not in src and "bench" not in src.lower().replace("no bench", "")


def test_upgrade_and_rollback_move_an_installed_twin_with_the_live_service(cm4, tmp_path, capsys):
    stage = tmp_path / "root"
    r1, r2 = release(tmp_path, "9.9.0"), release(tmp_path, "9.9.1")
    for r, v in ((r1, "9.9.0"), (r2, "9.9.1")):
        twin = r / "deploy" / "aquacrop-twin.service"
        twin.write_bytes(twin.read_bytes() + f"# release {v}\n".encode())
        (r / "MANIFEST.sha256").write_text("".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(r).as_posix()}\n"
            for p in sorted(p for p in r.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")))
    args = ["--root", str(stage), "--allow-arch"]
    assert cm4.main(args + ["install", "--release", str(r1)]) == 0
    units = stage / "etc" / "systemd" / "system"
    shutil.copy2(r1 / "deploy" / "aquacrop-twin.service", units / "aquacrop-twin.service")   # `twin` was run
    capsys.readouterr()
    assert cm4.main(args + ["upgrade", "--release", str(r2)]) == 0
    assert (units / "aquacrop-twin.service").read_text().endswith("# release 9.9.1\n")
    out = capsys.readouterr().out
    assert out.index("systemctl stop aquacrop-twin.service") < out.index("systemctl stop aquacrop-greenhouse.service")
    assert "systemctl start aquacrop-twin.service" in out
    assert cm4.main(["--root", str(stage), "rollback"]) == 0
    assert (units / "aquacrop-twin.service").read_text().endswith("# release 9.9.0\n")
