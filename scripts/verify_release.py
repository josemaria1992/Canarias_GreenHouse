"""DL-08: check an extracted release folder before it is handed over, with the Python that will run it.

    python scripts/verify_release.py --release unpacked/aquacrop-0.3.3 [--zip dist/aquacrop-0.3.3.zip] \
        [--python <venv python with the release wheel installed>] [--json out.json]

Checks: the ZIP's SHA-256 file and every MANIFEST.sha256 entry; RELEASE.json (version, wheel SHA-256, targets,
wheelhouses present); the bundled Windows Python is the pinned python.org build with AquaCrop and every locked
package installed in it; the configs in the folder are byte-identical to the copies inside the wheel; the normal
demo installation-80 has six modules (2 Relay (D), 2 AI 8CH, 2 IO 8CH) with 16 AI + 32 DI + 32 DO, 60 s
snapshots, 365 days, the 17 GiB budget, and no plant signal bound to the added IO 8CH channels; the owner
checklist and guide carry every case NOT RUN; nothing forbidden (databases, keys, rclone.conf) is shipped.
Software checks only; nothing here is physical verification.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

PROBE = r"""
import json, sys
from importlib import resources
from aquacrop.config import load_config
from aquacrop import __version__
d = resources.files("aquacrop") / "configs"
out = {"version": __version__, "configs": {p.name: p.read_bytes().hex() for p in d.iterdir() if p.name.endswith(".yaml")}}
c = load_config(d / "installation-80.yaml")
pts = c.all_points()
out["i80"] = {"config": f"{c.config_id}@{c.config_version}", "profile": c.profile.value,
  "modules": sorted((x.device_uid, x.model.value) for x in c.devices),
  "kinds": {k: sum(1 for p in pts if p.kind.value == k) for k in ("AI", "DI", "DO")},
  "recording_interval_s": c.historian.recording_interval_s, "retention_days": c.historian.retention_days,
  "db_budget_bytes": c.historian.db_budget_bytes,
  "bound_on_added": sorted(m.point for m in c.virtual_mapping if m.point.split(".")[0] in ("digital_A", "digital_B")),
  "bindings": len(c.virtual_mapping)}
print(json.dumps(out))
"""


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--release", type=Path, required=True)
    ap.add_argument("--zip", type=Path)
    ap.add_argument("--python", default=sys.executable, help="an interpreter with the release wheel installed")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rel = a.release.resolve()
    res: list[dict] = []

    def check(name, ok, detail=""):
        res.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

    if a.zip:
        want = (a.zip.parent / (a.zip.name + ".sha256")).read_text(encoding="utf-8").split()[0]
        check("ZIP SHA-256 matches its .sha256 file", sha(a.zip) == want, want)
        with zipfile.ZipFile(a.zip) as z:
            names = z.namelist()
        check("ZIP holds one top folder", len({n.split("/")[0] for n in names}) == 1, names[0].split("/")[0])
    bad = []
    lines = (rel / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    for ln in lines:
        digest, name = ln.split("  ", 1)
        if not (rel / name).is_file() or sha(rel / name) != digest:
            bad.append(name)
    listed = {ln.split("  ", 1)[1] for ln in lines}
    extra = [p.relative_to(rel).as_posix() for p in rel.rglob("*") if p.is_file()
             and p.relative_to(rel).as_posix() not in listed and p.name != "MANIFEST.sha256"]
    check("MANIFEST.sha256: every file present and matching, nothing unlisted", not bad and not extra,
          f"{len(lines)} files" + (f"; bad {bad[:5]}" if bad else "") + (f"; unlisted {extra[:5]}" if extra else ""))
    forbidden = [p.name for p in rel.rglob("*") if p.suffix in (".sqlite3", ".db", ".key", ".pem") or
                 p.name in ("rclone.conf", ".env", "aquacrop.env", "secret.key")]
    check("no databases, keys or credentials shipped", not forbidden, forbidden)
    info = json.loads((rel / "RELEASE.json").read_text(encoding="utf-8"))
    wheel = rel / info["wheel"]["file"]
    check("RELEASE.json wheel SHA-256", wheel.is_file() and sha(wheel) == info["wheel"]["sha256"], info["wheel"]["file"])
    check("RELEASE.json says no physical verification", info.get("physical_verification") is False, info.get("evidence"))
    houses = sorted(p.name for p in rel.glob("wheelhouse-*") if p.is_dir())
    check("offline wheelhouse for the CM4 (aarch64, Python 3.11)", "wheelhouse-aarch64-py311" in houses
          and houses == sorted(info.get("wheelhouses", [])), houses)
    # F-01: the Windows demo runs on the Python bundled in runtime/, with AquaCrop and the locked packages in it
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_release import WINDOWS_RUNTIME, lock_for
    rt, wr = rel / "runtime", info.get("windows_runtime") or {}
    check(f"bundled Windows Python {WINDOWS_RUNTIME['python']} (pinned python.org build)",
          (rt / "python.exe").is_file() and (rt / "python314.dll").is_file() and wr.get("python") == WINDOWS_RUNTIME["python"]
          and wr.get("nupkg_sha256") == WINDOWS_RUNTIME["sha256"], f"{wr.get('python')} from {str(wr.get('source'))[:60]}…")
    site = rt / "Lib" / "site-packages"
    marker = (rt / "aquacrop-wheel.sha256").read_text(encoding="ascii").strip() if (rt / "aquacrop-wheel.sha256").is_file() else ""
    dists = [d.name.removesuffix(".dist-info").rsplit("-", 1) for d in site.glob("*.dist-info")] if site.is_dir() else []
    have = {n.lower().replace("_", "-") + "==" + v for n, v in dists}
    pyv = ".".join(WINDOWS_RUNTIME["python"].split(".")[:2])
    want = {ln.split(" ")[0].lower().replace("_", "-") for ln in lock_for((rel / "requirements.lock").read_text(encoding="utf-8"),
            "win_amd64", pyv).splitlines() if "==" in ln and not ln[0].isspace() and not ln.startswith("#")}
    want.add(f"aquacrop-greenhouse=={info['version']}")
    check("AquaCrop and every locked package installed in the bundled Python", want <= have and marker == info["wheel"]["sha256"],
          f"{len(want & have)} of {len(want)} packages; wheel marker {'matches' if marker == info['wheel']['sha256'] else 'DIFFERS'}"
          + (f"; missing {sorted(want - have)[:4]}" if want - have else ""))
    for f in ("AquaCrop-Demo.cmd", "README-FIRST.md", "acceptance-manifest.json", "requirements.lock", "owner-checklist.csv",
              "owner-feedback-template.md", "docs/operator/walkthrough.md", "deploy/aquacrop-cm4.py", "check_installed_wheel.py"):
        check(f"ships {f}", (rel / f).is_file())
    check("the batch file keeps Windows line endings", b"\r\n" in (rel / "AquaCrop-Demo.cmd").read_bytes()
          and b"\n" not in (rel / "AquaCrop-Demo.cmd").read_bytes().replace(b"\r\n", b""))
    with (rel / "owner-checklist.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    col = next(k for k in rows[0] if k.startswith("Owner result"))
    check("owner checklist: UAT-01 to UAT-16, all NOT RUN", [r["Case ID"] for r in rows] == [f"UAT-{i:02d}" for i in range(1, 17)]
          and all(r[col] == "NOT RUN" for r in rows), f"{len(rows)} cases")
    guide = rel / "owner-tour" / "index.html"
    if info.get("owner_tour", {}).get("guide"):
        g = guide.read_text(encoding="utf-8") if guide.is_file() else ""
        imgs = list((rel / "owner-tour" / "img").glob("*")) if guide.is_file() else []
        reh = json.loads((rel / "owner-tour" / "claude-rehearsal.json").read_text(encoding="utf-8")) if guide.is_file() else {}
        check("illustrated guide: 16 cases, screenshots, offline", g.count('<h3 id="uat-') == 16 and len(imgs) >= 30
              and "http://" not in g.replace("http://127.0.0.1", "") and "<script" not in g, f"{len(imgs)} images")
        check("guide's rehearsal record is Claude's, owner results NOT RUN", reh.get("owner_results") == "NOT RUN" and reh.get("failed") == 0,
              f"{reh.get('passed')} passed, {reh.get('failed')} failed")
    probe = json.loads(subprocess.run([a.python, "-I", "-c", PROBE], check=True, capture_output=True, text=True, cwd=rel.parent).stdout)
    check("installed package is the release version", probe["version"] == info["version"], probe["version"])
    diff = [n for n, hx in probe["configs"].items() if not (rel / "configs" / n).is_file() or (rel / "configs" / n).read_bytes().hex() != hx]
    check("configs in the folder are identical to the packaged copies", not diff and len(probe["configs"]) >= 6, diff or sorted(probe["configs"]))
    i = probe["i80"]
    check("installation-80 is SIMULATED", i["profile"] == "SIMULATED", i["config"])
    models = sorted(m for _, m in i["modules"])
    check("six modules: 2 Relay (D), 2 AI 8CH, 2 IO 8CH", models == sorted(
        ["WAVESHARE_MODBUS_RTU_RELAY_D"] * 2 + ["WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH"] * 2 + ["WAVESHARE_MODBUS_RTU_IO_8CH"] * 2),
        ", ".join(f"{u} {m}" for u, m in i["modules"]))
    check("16 AI + 32 DI + 32 DO = 80 points", i["kinds"] == {"AI": 16, "DI": 32, "DO": 32}, i["kinds"])
    check("60 s snapshots, 365 days", (i["recording_interval_s"], i["retention_days"]) == (60, 365), (i["recording_interval_s"], i["retention_days"]))
    check("17 GiB budget", i["db_budget_bytes"] == 18253611008, i["db_budget_bytes"])
    check("no plant signal bound to the added IO 8CH channels", not i["bound_on_added"], f"{i['bindings']} bindings; on digital_A/B: {i['bound_on_added']}")
    failed = [r["check"] for r in res if not r["ok"]]
    if a.json:
        a.json.write_text(json.dumps({"release": info["version"], "commit": info["commit"], "checks": res,
                                      "passed": len(res) - len(failed), "failed": failed}, indent=1), encoding="utf-8")
    print(json.dumps({"passed": len(res) - len(failed), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
