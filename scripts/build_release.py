"""Build the release ZIP (DL-05): wheel, launchers, CM4 installer, configs, guides, locked requirements,
optional bundled Windows Python (runtime/), optional offline wheelhouses, RELEASE.json and a SHA-256 manifest of every file.

    python scripts/build_release.py --out dist-release                                   # no wheelhouses
    python scripts/build_release.py --out dist-release --windows-runtime download --wheelhouse aarch64:3.11

The owner tour ships as docs/operator/walkthrough.md, owner-checklist.csv (every result NOT RUN) and
owner-feedback-template.md. With --owner-tour (the output folder of scripts/ui_check/run_tour_rehearsal.py run
against this release) the illustrated offline guide owner-tour/ is added too. The CI release job builds twice:
once to rehearse the tour on the extracted release, then again with the same wheel, --built-utc and
--owner-tour, so the screenshots in the guide are of the program the ZIP carries.

Wheelhouses are downloaded with pip for the named platform and Python (needs the package index), with
the hashes in requirements.lock enforced. Run it from the repository root of a clean checkout: the
commit and whether the tree was clean are recorded in RELEASE.json. Nothing here signs anything or claims
hardware evidence; the release carries the acceptance and readiness reports as they stand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Never shipped, whatever is lying around in the folders that are copied.
FORBIDDEN_SUFFIXES = (".key", ".pem", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".pyc")
FORBIDDEN_NAMES = ("rclone.conf", "secret.key", ".env", "aquacrop.env")
LINUX_PLATFORMS = {"aarch64": ("manylinux_2_17_aarch64", "manylinux2014_aarch64", "manylinux_2_28_aarch64"),
                   "x86_64": ("manylinux_2_17_x86_64", "manylinux2014_x86_64", "manylinux_2_28_x86_64")}
# The environment markers of each wheelhouse target: `pip download --platform` selects wheels for the target
# but evaluates markers on the build machine, which would leave out e.g. tzdata (`sys_platform == 'win32'`).
MARKER_ENVS = {
    "win_amd64": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"},
    "aarch64": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "aarch64"},
    "x86_64": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "x86_64"},
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def copy_tree(src: Path, dest: Path) -> None:
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        if f.is_dir() or "__pycache__" in rel.parts:
            continue
        if f.suffix in FORBIDDEN_SUFFIXES or f.name in FORBIDDEN_NAMES:
            raise SystemExit(f"refusing to ship {f}: private files never go into a release")
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest / rel)


# The Python bundled for Windows (F-01): python.org's own CPython build for 64-bit Windows, as the Python Software
# Foundation publishes it with every release in the NuGet package "python" (the same build as the python.org
# installer, without an installer). Updating it is a change of these three values, then a rebuild.
WINDOWS_RUNTIME = {"python": "3.14.8",
                   "url": "https://api.nuget.org/v3-flatcontainer/python/3.14.8/python.3.14.8.nupkg",
                   "sha256": "ce85f674d9a63029f709cbff7a3da1c6bc5bfcfaefdd9999f98fa0290470c454"}
RUNTIME_SKIP = ("include/", "libs/")          # C headers and import libraries: only for building extensions


GATES = tuple(f"{p}-{i:02d}" for p, n in (("PH", 8), ("PC", 13), ("UI", 6), ("OP", 6), ("BK", 7), ("DL", 5))
              for i in range(1, n + 1))
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT RUN", "NOT CONFIGURED")
_ROW = re.compile(r"^\| ((?:PH|PC|UI|OP|BK|DL)-\d\d) (.+?) \| (.+?) \| (.+) \|$")


def acceptance_manifest(text: str) -> dict:
    """The owner's 45 pre-hardware gates with status and evidence, taken from docs/ACCEPTANCE.md (the one
    source); every gate exactly once, statuses from the status key only. A status like
    "PASS (desktop) / NOT RUN (CM4)" keeps both parts: the software part passed, the physical part did not
    run. The gate's own `status` is PASS only when every part passed, otherwise the first part that did not."""
    gates = {}
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        gid, summary, status, evidence = m.groups()
        if gid in gates:
            raise ValueError(f"{gid} appears twice in ACCEPTANCE.md")
        parts = []
        for part in status.split(" / "):
            word = next((w for w in STATUSES if part.startswith(w)), None)
            if word is None:
                raise ValueError(f"{gid}: unknown status {part!r}")
            scope = part[len(word):].strip(" ()") or None
            parts.append({"status": word.replace(" ", "_"), "scope": scope})
        status = next((p["status"] for p in parts if p["status"] != "PASS"), "PASS")
        gates[gid] = {"id": gid, "requirement_summary": summary, "status": status, "parts": parts,
                      "evidence": evidence, "physical_verification": False}
    missing = [g for g in GATES if g not in gates]
    if missing:
        raise ValueError(f"gates missing from ACCEPTANCE.md: {missing}")
    counts: dict = {}
    for g in gates.values():
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    return {"type": "pre-hardware acceptance report, generated from docs/ACCEPTANCE.md",
            "gate_count": len(GATES), "status_counts": counts, "gates": [gates[g] for g in GATES],
            "physical_and_live_cloud_evidence": "NOT_RUN: hardware, CM4, SD card and OneDrive steps are listed in "
                                                "docs/RELEASE_READINESS.md"}


def lock_for(text: str, plat: str, pyv: str) -> str:
    """requirements.lock with its environment markers decided for the target: an entry whose marker holds
    there keeps its pin and hashes without the marker, any other entry is left out."""
    try:
        from packaging.markers import Marker
    except ImportError:                                   # pip's own copy, when packaging is not installed
        from pip._vendor.packaging.markers import Marker
    env = {"python_version": pyv, "python_full_version": pyv + ".0", "implementation_name": "cpython",
           "platform_python_implementation": "CPython", **MARKER_ENVS.get(plat, {"sys_platform": plat})}
    out, keep = [], True
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            req, sep, rest = line.partition(";")
            keep = True
            if sep:
                marker = rest.rstrip().removesuffix("\\").strip()
                keep = Marker(marker).evaluate(env)
                line = req.rstrip() + " \\"
        if keep:
            out.append(line)
    return "\n".join(out) + "\n"


def wheelhouse(stage: Path, spec: str) -> Path:
    plat, pyv = spec.split(":")
    tag = "py" + pyv.replace(".", "")
    out = stage / f"wheelhouse-{plat}-{tag}"
    plats = LINUX_PLATFORMS.get(plat, (plat,))
    lock = stage / f"requirements-{plat}-{tag}.lock"
    lock.write_text(lock_for((ROOT / "requirements.lock").read_text(encoding="utf-8"), plat, pyv), encoding="utf-8")
    cmd = [sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--require-hashes", "-r",
           str(lock), "--only-binary=:all:", "--python-version", pyv, "-d", str(out)]
    for p in plats + ("any",):
        cmd += ["--platform", p]
    try:
        subprocess.run(cmd, check=True)
    finally:
        lock.unlink(missing_ok=True)
    (out / "TARGET.txt").write_text(f"python {pyv} platform {plat}\n", encoding="utf-8")
    return out


def windows_runtime(stage: Path, source: str, cache: Path, wheel: Path) -> dict:
    """runtime/: the pinned CPython for Windows with AquaCrop and its locked packages already installed, so
    AquaCrop-Demo.cmd needs no Python on the PC, installs nothing and downloads nothing. The NuGet file is
    checked against its pinned SHA-256 before anything is taken from it."""
    if source == "download":
        cache.mkdir(parents=True, exist_ok=True)
        nupkg = cache / WINDOWS_RUNTIME["url"].rsplit("/", 1)[1]
        if not nupkg.is_file() or sha256(nupkg) != WINDOWS_RUNTIME["sha256"]:
            import urllib.request
            with urllib.request.urlopen(WINDOWS_RUNTIME["url"], timeout=120) as r, nupkg.open("wb") as f:
                shutil.copyfileobj(r, f)
    else:
        nupkg = Path(source)
    got = sha256(nupkg)
    if got != WINDOWS_RUNTIME["sha256"]:
        raise SystemExit(f"{nupkg}: SHA-256 {got} is not the pinned {WINDOWS_RUNTIME['sha256']}; not used")
    rt = stage / "runtime"
    with zipfile.ZipFile(nupkg) as z:
        for n in z.namelist():
            if not n.startswith("tools/") or n.endswith("/"):
                continue
            rel = n[len("tools/"):]
            if rel.startswith(RUNTIME_SKIP) or "__pycache__/" in rel or rel.endswith(".pyc"):
                continue
            dest = rt / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(n))
    if not (rt / "python.exe").is_file():
        raise SystemExit(f"{nupkg} has no tools/python.exe")
    pyv = ".".join(WINDOWS_RUNTIME["python"].split(".")[:2])
    cache.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(cache / f"wheelhouse-win_amd64-py{pyv.replace('.', '')}", ignore_errors=True)
    house = wheelhouse(cache, f"win_amd64:{pyv}")
    lock = cache / "requirements-runtime.lock"
    lock.write_text(lock_for((ROOT / "requirements.lock").read_text(encoding="utf-8"), "win_amd64", pyv), encoding="utf-8")
    site = rt / "Lib" / "site-packages"
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile", "--no-index",
            "--find-links", str(house), "--target", str(site), "--platform", "win_amd64", "--python-version", pyv,
            "--implementation", "cp", "--only-binary=:all:", "--no-deps"]
    subprocess.run(base + ["--require-hashes", "-r", str(lock)], check=True)
    shutil.rmtree(site / "bin", ignore_errors=True)        # console scripts made for the build machine
    subprocess.run(base + [str(wheel)], check=True)
    shutil.rmtree(site / "bin", ignore_errors=True)        # console scripts made for the build machine
    # nothing is installed on the PC later: pip leaves the runtime, and with it its certificate bundle
    for p in [site / "pip", *site.glob("pip-*.dist-info")]:
        shutil.rmtree(p)
    for p in list(site.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)
    # read by AquaCrop-Demo.cmd and checked against RELEASE.json at every start (no line end: `set /p`)
    (rt / "aquacrop-wheel.sha256").write_text(sha256(wheel), encoding="ascii")
    return {"python": WINDOWS_RUNTIME["python"], "folder": "runtime", "nupkg_sha256": got,
            "source": f"{WINDOWS_RUNTIME['url']} (CPython for 64-bit Windows, published by the Python Software "
                      "Foundation)", "packages": "AquaCrop and requirements.lock installed in runtime/Lib/site-packages"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "dist-release")
    ap.add_argument("--wheelhouse", action="append", default=[], metavar="PLATFORM:PYTHON",
                    help="aarch64:3.11 (CM4, Bookworm), win_amd64:3.14, x86_64:3.12, macosx_11_0_arm64:3.12")
    ap.add_argument("--wheel", type=Path, help="use this built wheel instead of building one")
    ap.add_argument("--owner-tour", type=Path, help="evidence folder of run_tour_rehearsal.py: adds owner-tour/")
    ap.add_argument("--windows-runtime", metavar="NUPKG|download",
                    help=f"bundle CPython {WINDOWS_RUNTIME['python']} for Windows in runtime/ (the pinned NuGet file, "
                         "or 'download' to fetch it); AquaCrop-Demo.cmd then needs no Python on the PC")
    ap.add_argument("--built-utc", help="build time to record (the first pass's, when rebuilding with the guide)")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "build"
    shutil.rmtree(work, ignore_errors=True)
    wheels = work / "wheels"
    wheels.mkdir(parents=True)
    if args.wheel:
        shutil.copy2(args.wheel, wheels / args.wheel.name)
    else:
        subprocess.run([sys.executable, "-m", "build", "--wheel", "-o", str(wheels), str(ROOT)], check=True,
                       capture_output=True)
    built = sorted(wheels.glob("aquacrop_greenhouse-*.whl"))
    if len(built) != 1:
        raise SystemExit(f"expected one aquacrop wheel, got {[w.name for w in built]}")
    version = built[0].name.split("-")[1]
    name = f"aquacrop-{version}"
    stage = work / name
    stage.mkdir()
    shutil.move(str(wheels), stage / "wheels")
    for sub in ("deploy", "configs", "docs"):
        copy_tree(ROOT / sub, stage / sub)
    for f in ("requirements.lock", "CHANGELOG.md", "README.md"):
        shutil.copy2(ROOT / f, stage / f)
    shutil.copy2(ROOT / "scripts" / "check_installed_wheel.py", stage / "check_installed_wheel.py")
    # launchers at the top, where people look; the batch file keeps Windows line endings
    cmd = (ROOT / "launchers" / "AquaCrop-Demo.cmd").read_text(encoding="utf-8").replace("\r\n", "\n")
    (stage / "AquaCrop-Demo.cmd").write_bytes(cmd.replace("\n", "\r\n").encode("ascii"))
    shutil.copy2(ROOT / "launchers" / "aquacrop-demo.sh", stage / "aquacrop-demo.sh")
    shutil.copy2(ROOT / "launchers" / "README-FIRST.md", stage / "README-FIRST.md")
    tour = ROOT / "docs" / "operator" / "owner-tour"
    shutil.copy2(tour / "owner-checklist.csv", stage / "owner-checklist.csv")
    shutil.copy2(tour / "feedback-template.md", stage / "owner-feedback-template.md")
    if args.owner_tour:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "ui_check" / "make_tour_guide.py"), "--evidence",
                        str(args.owner_tour), "--out", str(stage / "owner-tour")], check=True)
    houses = [wheelhouse(stage, spec) for spec in args.wheelhouse]
    runtime = (windows_runtime(stage, args.windows_runtime, out / "runtime-cache", stage / "wheels" / built[0].name)
               if args.windows_runtime else None)
    commit, dirty = git("rev-parse", "HEAD"), bool(git("status", "--porcelain"))
    report = acceptance_manifest((ROOT / "docs" / "ACCEPTANCE.md").read_text(encoding="utf-8"))
    report |= {"version": version, "commit": commit, "tree_clean": not dirty}
    (stage / "acceptance-manifest.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n",
                                                    encoding="utf-8")
    info = {"name": "aquacrop-greenhouse", "version": version, "commit": commit, "tree_clean": not dirty,
            "built_utc": args.built_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "builder": f"python {sys.version.split()[0]} on {sys.platform}",
            "python_supported": ["3.11", "3.12", "3.13", "3.14"],
            "wheel": {"file": f"wheels/{built[0].name}", "sha256": sha256(stage / "wheels" / built[0].name)},
            "requirements_lock_sha256": sha256(stage / "requirements.lock"),
            "wheelhouses": [h.name for h in houses],
            "windows_runtime": runtime,
            "targets": {"windows": (f"win_amd64: CPython {runtime['python']} bundled in runtime/ with AquaCrop "
                                    "installed; nothing to install on the PC") if runtime else
                                   "win_amd64: a 64-bit CPython 3.11 to 3.14 installed on the PC",
                        "cm4": "aarch64, CPython 3.11 (Raspberry Pi OS Bookworm 64-bit; confirm OS and Python at "
                               "commissioning)"},
            "normal_demo": "installation-80",
            "owner_tour": {"guide": "owner-tour/index.html" if args.owner_tour else None,
                           "checklist": "owner-checklist.csv", "owner_results": "NOT RUN"},
            "evidence": "software and simulation only; no hardware, CM4 or cloud account was tested "
                        "(docs/RELEASE_READINESS.md)",
            "physical_verification": False}
    (stage / "RELEASE.json").write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    files = sorted(p for p in stage.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    lines = [f"{sha256(p)}  {p.relative_to(stage).as_posix()}" for p in files]
    (stage / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    zpath = out / f"{name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                zi = zipfile.ZipInfo.from_file(p, f"{name}/{p.relative_to(stage).as_posix()}")
                if p.suffix == ".sh" or p.name == "aquacrop-cm4.py":
                    zi.external_attr = 0o100755 << 16
                with p.open("rb") as fh:
                    z.writestr(zi, fh.read(), compress_type=zipfile.ZIP_DEFLATED)
    digest = sha256(zpath)
    (out / f"{name}.zip.sha256").write_text(f"{digest}  {zpath.name}\n", encoding="utf-8")
    print(json.dumps({"zip": str(zpath), "sha256": digest, "files": len(files) + 1, "version": version,
                      "commit": commit, "tree_clean": not dirty, "wheelhouses": [h.name for h in houses],
                      "windows_runtime": runtime and runtime["python"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
