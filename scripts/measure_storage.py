"""Measure historian growth, rolling-retention steady state and backup cost for 48 points at 60 s.

Writes a synthetic but realistic history through the real write path (complete snapshots, events,
hourly-equivalent pruning) on a simulated timeline, then times a compressed backup, its verification
and a full restore. Nothing here touches a real installation; it creates its own scratch database.

    python scripts/measure_storage.py --days 400 --out /tmp/aquacrop-measure     # desktop, about 15 min
    python scripts/measure_storage.py --days 7 --out /tmp/m                       # quick, e.g. on the CM4

Values are random walks; real signals may compress differently, so re-run `aquacrop storage report`
on the installation after a few weeks of real data (ADR-0008).
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aquacrop.config.schema import InstallationConfig
from aquacrop.domain.model import Origin, PointKind, new_id
from aquacrop.historian import writer as writer_mod
from aquacrop.historian.backup import create_backup, restore_backup, verify_backup
from aquacrop.historian.recording import complete_snapshot
from aquacrop.historian.retention import prune_expired
from aquacrop.io.sampling import AcqContext, make_sample

GIB = 2**30


def config() -> InstallationConfig:
    ai = {i: {"mode": "3", "scaling": "level"} for i in range(1, 9)}
    dev = lambda uid, addr, model, sku, **kw: {"device_uid": uid, "bus_id": "rs485_a", "address": addr,  # noqa: E731
                                               "model": model, "sku": sku, **kw}
    return InstallationConfig.model_validate({
        "schema_version": 1, "config_id": "canarias-greenhouse", "config_version": "2026-10-12.1", "profile": "HARDWARE_READ_ONLY",
        "label": "measurement only", "buses": [{"bus_id": "rs485_a", "kind": "rtu", "port": "/dev/null",
                                                "port_evidence": "measurement", "poll_interval_s": 3.0}],
        "devices": [dev("relay_A", 1, "WAVESHARE_MODBUS_RTU_RELAY_D", "26517"),
                    dev("relay_B", 3, "WAVESHARE_MODBUS_RTU_RELAY_D", "26517"),
                    dev("analog_A", 2, "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH", "25821", hardware_variant="STANDARD",
                        ai=ai),
                    dev("analog_B", 4, "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH", "25821", hardware_variant="STANDARD",
                        ai=ai)],
        "scaling_profiles": [{"profile_id": "level", "version": 1, "status": "VERIFIED", "input_unit": "uA",
                              "raw_low": 4000, "raw_high": 20000, "eng_low": 0, "eng_high": 100, "unit": "%",
                              "valid_raw_min": 3800, "valid_raw_max": 20500, "evidence": "measurement"}],
        "historian": {"db_path": "measure.sqlite3", "recording_interval_s": 60}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=400)
    ap.add_argument("--edges-per-digital-point-per-day", type=float, default=20.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-restore", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cfg = config()
    pts = cfg.all_points()
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    clock = {"now": t0}
    writer_mod.utc_iso = lambda dt=None: (dt or clock["now"]).isoformat(timespec="microseconds")  # simulated time
    db = out / "measure.sqlite3"
    sid = new_id("sess")                      # same length as a real process session id
    h = writer_mod.Historian(db, cfg, sid, backend_name="MODBUS_RTU")
    rng = random.Random(1)
    raw = {str(p): 12000.0 for p in pts}
    state = {str(p): False for p in pts}
    p_edge = a.edges_per_digital_point_per_day / 1440
    report = {"points": len(pts), "interval_s": 60, "days": a.days, "checkpoints": []}
    tic = time.perf_counter()
    n = int(a.days * 1440)
    for k in range(n):
        at = t0 + timedelta(minutes=k)
        clock["now"] = at
        ctx = AcqContext(sid, f"{sid}:{k}")
        latest = {}
        for p in pts:
            pid = str(p)
            if p.kind == PointKind.AI:
                raw[pid] = min(19900.0, max(4100.0, raw[pid] + rng.gauss(0, 40)))
                v = int(raw[pid])
            else:
                if rng.random() < p_edge:
                    state[pid] = not state[pid]
                    h.event("DI_EDGE" if p.kind == PointKind.DI else "DO_EDGE",
                            {"point": pid, "from": not state[pid], "to": state[pid], "origin": "REAL",
                             "not_before_utc": (at - timedelta(seconds=3)).isoformat(),
                             "not_after_utc": at.isoformat(), "resolution_s": 3.0,
                             "note": "polled observation; actual change time is within the window"})
                v = state[pid]
            latest[pid] = make_sample(cfg, p, v, acquired=at - timedelta(seconds=rng.random() * 3), ctx=ctx,
                                      origin=Origin.REAL)
        h.snapshot(at, complete_snapshot(cfg, latest, now=at, ctx=ctx, origin=Origin.REAL,
                                         stale_reason=lambda s: None), 60)
        if k % 60 == 59:                       # the server prunes hourly
            h.wait_idle(120)
            prune_expired(h, now=at)
        if k % 1440 == 1439:
            day = (k + 1) // 1440
            if day % 30 == 0 or day in (1, 7, 365) or k == n - 1:
                h.wait_idle(120)
                c = sqlite3.connect(db)
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()
                st = h.storage()
                report["checkpoints"].append({"day": day, "db_bytes": st["db_bytes"], "used_bytes": st["db_used_bytes"],
                                              "reusable_bytes": st["db_reusable_bytes"],
                                              "samples": h.query("SELECT count(*) FROM samples")[0][0],
                                              "events": h.query("SELECT count(*) FROM events")[0][0],
                                              "elapsed_s": round(time.perf_counter() - tic, 1)})
                print(json.dumps(report["checkpoints"][-1]), flush=True)
    h.wait_idle(300)
    st = h.storage()
    h.close()
    report["final_storage"] = {k: st[k] for k in ("db_bytes", "db_used_bytes", "db_reusable_bytes",
                                                  "oldest_sample_utc", "newest_sample_utc", "history_span_days")}
    tic = time.perf_counter()
    man = create_backup(db, out / "backups", verify=False)
    report["backup"] = {"bytes": man["bytes"], "seconds": round(time.perf_counter() - tic, 1),
                        "ratio": round(st["db_bytes"] / man["bytes"], 1)}
    tic = time.perf_counter()
    v = verify_backup(man["path"])
    report["backup"]["verify_ok"] = v["ok"]
    report["backup"]["verify_seconds"] = round(time.perf_counter() - tic, 1)
    if not a.skip_restore:
        tic = time.perf_counter()
        r = restore_backup(man["path"], out / "restored.sqlite3")
        report["restore"] = {"seconds": round(time.perf_counter() - tic, 1),
                             "bytes": Path(r["target"]).stat().st_size, "checks": "integrity_check ok, counts match"}
        Path(r["target"]).unlink()
    print(json.dumps(report, indent=2))
    (out / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
