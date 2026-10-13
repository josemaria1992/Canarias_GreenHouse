"""Measure historian growth, rolling-retention steady state and backup cost at the approved 60 s cadence.

Writes a synthetic history through the real write path on a simulated timeline that ends now: complete
snapshots every 60 s, digital edges, operator commands (intent and outcome), alarm raise/ack/clear/close,
a weekly device fault with a chattering input, and the server's hourly bounded retention run. Then it
times a verified compressed backup and a full restore drill (restore to scratch, integrity and
foreign-key checks, application smoke test). Nothing here touches a real installation; it creates its
own scratch database under --out.

    python scripts/measure_storage.py --days 400 --out /tmp/aquacrop-measure            # 48 points
    python scripts/measure_storage.py --days 30 --inventory 24 --out /tmp/m24           # 24 points
    python scripts/measure_storage.py --days 7 --out /tmp/m --skip-drill                # quick, e.g. on the CM4
    python scripts/measure_storage.py --days 400 --process-workload workload.json --out /tmp/mp   # OP-06

`--process-workload` adds the events and controller commands of the process controllers at the
rates measured on the simulated installation by `scripts/measure_process_workload.py` (sample
payloads replayed, so row sizes are real), on top of the workload below.

The report (report.json under --out) records the environment (Python, SQLite, OS, CPU, package
versions, git commit, script checksum, command line and seed), the workload, the persistent database
and WAL sizes separately, the largest WAL seen, and the bytes the process wrote to storage. Bytes
written are a desktop figure, not eMMC endurance evidence (the device's write amplification is
unknown). Values are random walks; real signals may compress differently, so re-run
`aquacrop storage report` on the installation after a few weeks of real data (ADR-0008).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aquacrop
from aquacrop.config.schema import InstallationConfig
from aquacrop.control import alarms as alarms_mod
from aquacrop.control.alarms import AlarmEngine
from aquacrop.domain.model import (CommandOrigin, CommandRecord, Origin, Outcome, OutputCommand, PointKind,
                                   new_id)
from aquacrop.historian import writer as writer_mod
from aquacrop.historian.backup import create_backup, verify_backup
from aquacrop.historian.recording import complete_snapshot
from aquacrop.historian.retention import PRUNE_MAX_TRANSACTIONS_PER_RUN, prune_expired
from aquacrop.io.sampling import AcqContext, make_sample

GIB = 2**30
ROOT = Path(__file__).resolve().parent.parent


def config(inventory: int) -> InstallationConfig:
    """Two (48 points) or one (24 points) of each module: the inventory stays configurable; these are
    measurement values, never production configuration. Identifiers have the length of real ones
    (every sample row stores the configuration id and version), so the size is not understated."""
    ai = {i: {"mode": "3", "scaling": "level"} for i in range(1, 9)}
    dev = lambda uid, addr, model, sku, **kw: {"device_uid": uid, "bus_id": "rs485_a", "address": addr,  # noqa: E731
                                               "model": model, "sku": sku, **kw}
    devices = [dev("relay_A", 1, "WAVESHARE_MODBUS_RTU_RELAY_D", "26517"),
               dev("analog_A", 2, "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH", "25821", hardware_variant="STANDARD",
                   ai=ai)]
    if inventory == 48:
        devices += [dev("relay_B", 3, "WAVESHARE_MODBUS_RTU_RELAY_D", "26517"),
                    dev("analog_B", 4, "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH", "25821",
                        hardware_variant="STANDARD", ai=ai)]
    return InstallationConfig.model_validate({
        "schema_version": 1, "config_id": "canarias-greenhouse", "config_version": "2026-10-12.1",
        "profile": "HARDWARE_READ_ONLY", "label": "measurement only", "site": {"timezone": "Atlantic/Canary"},
        "buses": [{"bus_id": "rs485_a", "kind": "rtu", "port": "/dev/null", "port_evidence": "measurement",
                   "poll_interval_s": 3.0}],
        "devices": devices,
        "scaling_profiles": [{"profile_id": "level", "version": 1, "status": "VERIFIED", "input_unit": "uA",
                              "raw_low": 4000, "raw_high": 20000, "eng_low": 0, "eng_high": 100, "unit": "%",
                              "valid_raw_min": 3800, "valid_raw_max": 20500, "evidence": "measurement"}],
        "historian": {"db_path": "measure.sqlite3", "recording_interval_s": 60}})


# ---------------------------------------------------------------------------------------- environment
def _run(*cmd: str) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _version(name: str) -> str | None:
    from importlib import metadata
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _mem_total() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def _filesystem(path: Path) -> dict | None:
    try:
        best = None
        for line in Path("/proc/mounts").read_text().splitlines():
            dev, mnt, fstype = line.split()[:3]
            if str(path.resolve()).startswith(mnt) and (best is None or len(mnt) > len(best[1])):
                best = (dev, mnt, fstype)
        return {"device": best[0], "mount": best[1], "type": best[2]} if best else None
    except OSError:
        return None


def environment(out: Path, seed: int) -> dict:
    script = Path(sys.argv[0]).resolve()
    status = _run("git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no")
    return {"utc_started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "command": [sys.executable, *sys.argv], "seed": seed,
            "script": {"path": str(script.relative_to(ROOT)) if script.is_relative_to(ROOT) else str(script),
                       "sha256": hashlib.sha256(script.read_bytes()).hexdigest()},
            "git": {"commit": _run("git", "-C", str(ROOT), "rev-parse", "HEAD"),
                    "branch": _run("git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"),
                    "dirty": None if status is None else bool(status)},
            "python": sys.version.split()[0], "python_implementation": platform.python_implementation(),
            "sqlite_library": sqlite3.sqlite_version, "os": platform.platform(), "machine": platform.machine(),
            "cpu_model": _cpu_model(), "cpu_count": os.cpu_count(), "memory_bytes": _mem_total(),
            "filesystem": _filesystem(out),
            "packages": {n: _version(n) for n in ("pydantic", "fastapi", "starlette", "uvicorn", "pymodbus",
                                                  "PyYAML")} | {"aquacrop": aquacrop.__version__}}


def process_io() -> dict | None:
    """Bytes this process sent to storage so far (Linux /proc/self/io), or None elsewhere."""
    try:
        d = dict(line.split(": ") for line in Path("/proc/self/io").read_text().splitlines())
        return {"write_bytes": int(d["write_bytes"]), "wchar": int(d["wchar"])}
    except (OSError, KeyError, ValueError):
        return None


def file_sizes(db: Path) -> dict:
    wal = Path(str(db) + "-wal")
    return {"db_file_bytes": db.stat().st_size, "wal_bytes": wal.stat().st_size if wal.exists() else 0}


# ---------------------------------------------------------------------------------------- workload
class Workload:
    """What the server writes besides snapshots, through the same historian calls: operator commands
    (durable intent, then outcome), alarm raise/ack/clear/close with still-active alarms refreshed each
    minute, and a periodic device fault (its points UNAVAILABLE, a DEVICE_COMM alarm) during which one
    input chatters on every poll."""

    def __init__(self, a, cfg: InstallationConfig, h, rng: random.Random):
        self.a, self.cfg, self.h, self.rng = a, cfg, h, rng
        self.alarms = AlarmEngine(cfg, emit=lambda kind, payload: h.event(kind, payload, actor="system"))
        pts = cfg.all_points()
        self.do = [p for p in pts if p.kind == PointKind.DO]
        self.ai = [p for p in pts if p.kind == PointKind.AI]
        self.chatter_point = next(str(p) for p in pts if p.kind == PointKind.DI)
        self.fault_device = cfg.devices[-1].device_uid
        self.plan: dict[int, list] = {}
        self.faulted = False
        self.counts = {"commands": 0, "alarms_raised": 0, "device_faults": 0, "chatter_edges": 0}
        self.proc = json.loads(Path(a.process_workload).read_text(encoding="utf-8")) if a.process_workload else None
        if self.proc:
            self.counts |= {"process_events": 0, "process_commands": 0}

    def plan_day(self, day: int) -> None:
        a, rng, plan = self.a, self.rng, {}
        for _ in range(int(a.commands_per_day)):
            plan.setdefault(rng.randrange(1440), []).append(("command", rng.choice(self.do), rng.random() < 0.5))
        for _ in range(int(a.alarms_per_day)):
            m, target = rng.randrange(1370), rng.choice(self.ai)
            plan.setdefault(m, []).append(("raise", target))
            plan.setdefault(m + rng.randint(1, 15), []).append(("ack", target))
            plan.setdefault(m + rng.randint(5, 60), []).append(("clear", target))
        if self.proc:
            for kind, k in self.proc["kinds"].items():
                n = int(k["per_day"]) + (rng.random() < k["per_day"] % 1)
                for i in range(n):
                    plan.setdefault(rng.randrange(1440), []).append(("pevent", kind, i))
            n = self.proc["commands_per_day"]
            for _ in range(int(n) + (rng.random() < n % 1)):
                plan.setdefault(rng.randrange(1440), []).append(("pcommand", rng.choice(self.do), rng.random() < 0.5))
        if a.fault_every_days and day % a.fault_every_days == a.fault_every_days - 1:
            m = rng.randrange(1440 - a.fault_minutes - 20)
            plan.setdefault(m, []).append(("fault_start",))
            plan.setdefault(m + a.fault_minutes, []).append(("fault_end",))
            plan.setdefault(m + a.fault_minutes + 5, []).append(("ack_fault",))
            for k in range(a.chatter_minutes):
                plan.setdefault(m + k, []).append(("chatter",))
        self.plan = plan

    def run_minute(self, minute: int, at: datetime, state: dict) -> None:
        for act in self.plan.get(minute, []):
            getattr(self, "_" + act[0])(at, state, *act[1:])
        for occ in list(self.alarms.open.values()):
            if occ.active and occ.rule_id == "MEASURE_HIGH":
                self.alarms.raise_or_update(occ.rule_id, occ.target, severity=occ.severity, message=occ.message,
                                            sim_s=None, value=occ.value, quality=occ.quality, origin=occ.origin)
        if self.faulted:
            self.alarms.evaluate_devices({self.fault_device: (False, "no response (timeout)")}, None)
        self.h.alarms(self.alarms.take_dirty())

    def _command(self, at, state, point, desired):
        rid = new_id("cmd")
        cmd = OutputCommand(rid, "operator1", str(point), desired, self.cfg.config_version, None, at,
                            at + timedelta(seconds=30), CommandOrigin.BENCH, "measurement workload")
        rec = CommandRecord(cmd, Outcome.ACCEPTED)
        self.h.command_intent(rec)
        rec.readback = desired
        rec.advance(Outcome.READBACK_CONFIRMED, "coil readback matches the request (not actuator proof)")
        self.h.command_outcome(rec)
        if state[str(point)] != desired:
            state[str(point)] = desired
            self.h.event("DO_EDGE", {"point": str(point), "from": not desired, "to": desired, "origin": "REAL",
                                     "request_id": rid, "resolution_s": 3.0})
        self.counts["commands"] += 1

    def _pevent(self, at, state, kind, i):
        """A process event as the simulated installation wrote it (its DO/DI edges included)."""
        smp = self.proc["kinds"][kind]["samples"]
        self.h.event(kind, smp[i % len(smp)]["payload"], actor=smp[i % len(smp)]["actor"])
        self.counts["process_events"] += 1

    def _pcommand(self, at, state, point, desired):
        """A controller command: durable intent and outcome (its DO_EDGE comes with the process events)."""
        cmd = OutputCommand(new_id("cmd"), "controller", str(point), desired, self.cfg.config_version, None, at,
                            at + timedelta(seconds=30), CommandOrigin.CONTROLLER, "measurement workload: process")
        rec = CommandRecord(cmd, Outcome.ACCEPTED)
        self.h.command_intent(rec)
        rec.readback = desired
        rec.advance(Outcome.READBACK_CONFIRMED, "coil readback matches the request (not actuator proof)")
        self.h.command_outcome(rec)
        self.counts["process_commands"] += 1

    def _raise(self, at, state, target):
        self.alarms.raise_or_update("MEASURE_HIGH", str(target), severity="WARNING", sim_s=None,
                                    message=f"MEASURE_HIGH on {target}", value=90.0, threshold=85.0,
                                    quality="GOOD", origin="REAL")
        self.counts["alarms_raised"] += 1

    def _ack(self, at, state, target):
        occ = self.alarms.open.get(("MEASURE_HIGH", str(target)))
        if occ is not None:
            self.alarms.acknowledge(occ.occurrence_id, "operator1")

    def _clear(self, at, state, target):
        self.alarms.clear("MEASURE_HIGH", str(target))

    def _fault_start(self, at, state):
        self.faulted = True
        self.counts["device_faults"] += 1

    def _fault_end(self, at, state):
        self.faulted = False
        self.alarms.evaluate_devices({self.fault_device: (True, None)}, None)

    def _ack_fault(self, at, state):
        occ = self.alarms.open.get(("DEVICE_COMM", self.fault_device))
        if occ is not None:
            self.alarms.acknowledge(occ.occurrence_id, "operator1")

    def _chatter(self, at, state):
        """A bouncing input seen on every 3 s poll: 20 edges a minute."""
        pid = self.chatter_point
        for i in range(20):
            state[pid] = not state[pid]
            t = at + timedelta(seconds=3 * i)
            self.h.event("DI_EDGE", {"point": pid, "from": not state[pid], "to": state[pid], "origin": "REAL",
                                     "not_before_utc": (t - timedelta(seconds=3)).isoformat(),
                                     "not_after_utc": t.isoformat(), "resolution_s": 3.0})
        self.counts["chatter_edges"] += 20


# ---------------------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=400)
    ap.add_argument("--inventory", type=int, choices=(24, 48), default=48)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--edges-per-digital-point-per-day", type=float, default=20.0)
    ap.add_argument("--commands-per-day", type=float, default=200.0)
    ap.add_argument("--alarms-per-day", type=float, default=24.0)
    ap.add_argument("--fault-every-days", type=int, default=7, help="0 = no device faults")
    ap.add_argument("--fault-minutes", type=int, default=30)
    ap.add_argument("--chatter-minutes", type=int, default=10)
    ap.add_argument("--process-workload", default=None,
                    help="JSON from scripts/measure_process_workload.py: add the process controllers' events and "
                         "commands at the measured rates")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-drill", action="store_true", help="skip the full restore drill")
    a = ap.parse_args()
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    io0 = process_io()
    cfg = config(a.inventory)
    pts = cfg.all_points()
    n = int(a.days * 1440)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=n)   # ends now
    clock = {"now": t0}
    writer_mod.utc_iso = lambda dt=None: (dt or clock["now"]).isoformat(timespec="microseconds")  # simulated time
    alarms_mod.utcnow = lambda: clock["now"]
    db = out / "measure.sqlite3"
    sid = new_id("sess")                      # same length as a real process session id
    h = writer_mod.Historian(db, cfg, sid, backend_name="MODBUS_RTU")
    rng = random.Random(a.seed)
    work = Workload(a, cfg, h, random.Random(a.seed + 1))
    raw = {str(p): 12000.0 for p in pts}
    state = {str(p): False for p in pts}
    p_edge = a.edges_per_digital_point_per_day / 1440
    report = {"environment": environment(out, a.seed),
              "workload": {"points": len(pts), "inventory": a.inventory, "interval_s": 60, "days": a.days,
                           "timeline_utc": [t0.isoformat(), (t0 + timedelta(minutes=n)).isoformat()],
                           "edges_per_digital_point_per_day": a.edges_per_digital_point_per_day,
                           "commands_per_day": a.commands_per_day, "alarms_per_day": a.alarms_per_day,
                           "process_workload": None if not work.proc else {
                               "file_sha256": hashlib.sha256(Path(a.process_workload).read_bytes()).hexdigest(),
                               "simulated_hours": work.proc["simulated_hours"],
                               "events_per_day": work.proc["events_per_day"],
                               "commands_per_day": work.proc["commands_per_day"],
                               "activity": work.proc["activity"]},
                           "device_fault": {"every_days": a.fault_every_days, "minutes": a.fault_minutes,
                                            "device": work.fault_device,
                                            "chatter_edges_per_fault": 20 * a.chatter_minutes},
                           "retention": {"days": cfg.historian.retention_days, "run": "hourly",
                                         "max_transactions": PRUNE_MAX_TRANSACTIONS_PER_RUN,
                                         "wal_limit_bytes": cfg.historian.wal_warning_bytes}},
              "checkpoints": []}
    wal_max = {"bytes": 0, "day": None}
    deferred = 0
    tic = time.perf_counter()
    for k in range(n):
        at = t0 + timedelta(minutes=k)
        clock["now"] = at
        minute = k % 1440
        if minute == 0:
            work.plan_day(k // 1440)
        ctx = AcqContext(sid, f"{sid}:{k}")
        latest = {}
        for p in pts:
            pid = str(p)
            acq = at - timedelta(seconds=rng.random() * 3)
            if work.faulted and p.device_uid == work.fault_device:
                latest[pid] = make_sample(cfg, p, None, acquired=acq, ctx=ctx, origin=Origin.REAL,
                                          reason=f"{p.device_uid}: no response (timeout)")
                continue
            if p.kind == PointKind.AI:
                raw[pid] = min(19900.0, max(4100.0, raw[pid] + rng.gauss(0, 40)))
                v = int(raw[pid])
            else:
                if p.kind == PointKind.DI and rng.random() < p_edge:
                    state[pid] = not state[pid]
                    h.event("DI_EDGE", {"point": pid, "from": not state[pid], "to": state[pid], "origin": "REAL",
                                        "not_before_utc": (at - timedelta(seconds=3)).isoformat(),
                                        "not_after_utc": at.isoformat(), "resolution_s": 3.0,
                                        "note": "polled observation; actual change time is within the window"})
                v = state[pid]
            latest[pid] = make_sample(cfg, p, v, acquired=acq, ctx=ctx, origin=Origin.REAL)
        work.run_minute(minute, at, state)
        h.snapshot(at, complete_snapshot(cfg, latest, now=at, ctx=ctx, origin=Origin.REAL,
                                         stale_reason=lambda s: None), 60)
        if k % 60 == 59:                       # the server's hourly retention run, with its bounds
            h.wait_idle(120)
            w = file_sizes(db)["wal_bytes"]
            if w > wal_max["bytes"]:
                wal_max = {"bytes": w, "day": round(k / 1440, 2)}
            res = prune_expired(h, now=at, max_chunks=PRUNE_MAX_TRANSACTIONS_PER_RUN,
                                wal_limit_bytes=cfg.historian.wal_warning_bytes)
            deferred += "incomplete" in res
        if k % 1440 == 1439:
            day = (k + 1) // 1440
            if day % 30 == 0 or day in (1, 7, 365) or k == n - 1:
                h.wait_idle(120)
                before = file_sizes(db)
                c = sqlite3.connect(db)
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()
                st = h.storage()
                io = process_io()
                report["checkpoints"].append({
                    "day": day, "db_bytes": st["db_bytes"], "used_bytes": st["db_used_bytes"],
                    "reusable_bytes": st["db_reusable_bytes"], "wal_bytes_before_checkpoint": before["wal_bytes"],
                    **{t: h.query(f"SELECT count(*) FROM {t}")[0][0]
                       for t in ("samples", "events", "commands", "alarm_occurrences")},
                    "process_bytes_written": (io["write_bytes"] - io0["write_bytes"]) if io and io0 else None,
                    "elapsed_s": round(time.perf_counter() - tic, 1)})
                print(json.dumps(report["checkpoints"][-1]), flush=True)
    h.wait_idle(300)
    before_close = file_sizes(db)
    h.close()
    at_rest = file_sizes(db)
    io1 = process_io()
    con = sqlite3.connect(db)
    st = writer_mod.capacity_report(con, db, cfg, detail=True)
    con.close()
    report["workload"]["generated"] = work.counts | {"retention_runs_stopped_early": deferred}
    report["final_storage"] = {k: st.get(k) for k in (
        "db_bytes", "db_file_bytes", "wal_bytes", "db_used_bytes", "db_reusable_bytes", "oldest_sample_utc",
        "newest_sample_utc", "history_span_days", "window_full", "series", "registers", "last_7_days")} | {
        "files_before_close": before_close, "files_after_close": at_rest}
    report["wal"] = {"max_observed_bytes": wal_max["bytes"], "max_observed_day": wal_max["day"],
                     "sampled": "after each hourly batch, before the retention run",
                     "journal_size_limit_bytes": 64 * 2**20}
    report["bytes_written"] = {
        "process_write_bytes": (io1["write_bytes"] - io0["write_bytes"]) if io1 and io0 else None,
        "process_wchar": (io1["wchar"] - io0["wchar"]) if io1 and io0 else None,
        "persistent_db_file_bytes": at_rest["db_file_bytes"], "persistent_wal_bytes": at_rest["wal_bytes"],
        "note": "bytes the measurement process sent to storage over the whole run (WAL, checkpoints, page "
                "rewrites, retention deletes); a desktop figure, not eMMC endurance evidence"}
    tic = time.perf_counter()
    man = create_backup(db, out / "backups", config_id=cfg.config_id, config_version=cfg.config_version, keep=7)
    report["backup"] = {"bytes": man["bytes"], "seconds": round(time.perf_counter() - tic, 1),
                        "ratio": round(at_rest["db_file_bytes"] / man["bytes"], 1),
                        "verified_before_publish": man.get("verification", {}).get("ok"),
                        "estimate_bytes": man["estimate"]["estimate_bytes"]}
    tic = time.perf_counter()
    v = verify_backup(man["path"])
    report["backup"] |= {"quick_verify_ok": v["ok"], "quick_verify_seconds": round(time.perf_counter() - tic, 1)}
    if not a.skip_drill:
        tic = time.perf_counter()
        d = verify_backup(man["path"], full=True, scratch_dir=out / "drill-scratch", cfg=cfg,
                          report_dir=out / "restore-drills")
        f = d.get("full", {})
        report["restore_drill"] = {"ok": d["ok"], "integrity": d["integrity"],
                                   "seconds": round(time.perf_counter() - tic, 1),
                                   "restore_seconds": f.get("restore_seconds"),
                                   "restored_bytes": f.get("restored_bytes"), "needed_bytes": f.get("needed_bytes"),
                                   "facts": f.get("facts"), "smoke": f.get("smoke")}
        shutil.rmtree(out / "drill-scratch", ignore_errors=True)
    report["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = json.dumps(report, indent=2, default=str)
    print(text)
    (out / "report.json").write_text(text)


if __name__ == "__main__":
    main()
