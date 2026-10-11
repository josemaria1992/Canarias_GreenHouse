"""Backup-first import of a v0.1 `greenhouse.sqlite3` database.

* The legacy file is first copied with the SQLite backup API and checksummed; the original is opened
  read-only and never modified.
* Legacy samples carry no module, origin or calibration evidence. They are imported as origin
  `LEGACY_UNKNOWN` under the pseudo-device `legacy_v01` (points `legacy_v01.AI1..8/DI1..8/DO1..8`).
  The backend is NOT inferred from the current configuration, and no engineering value is derived.
* Legacy `settings` (thresholds, demo AUTO timings) are recorded as an event for reference and are
  never applied to any configuration namespace.
* An import is recorded by checksum; importing the same file twice is refused.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .backup import _sha256, backup_to
from .schema import utc_iso
from .writer import Historian

LEGACY_DEVICE = "legacy_v01"
LEGACY_BUS = "legacy_unknown_bus"
TYPE_UNIT = {0: "mV", 1: "mV", 2: "uA", 3: "uA", 4: "code"}
QUALITY = {"OK": "GOOD"}


def inspect_legacy(path: str | Path) -> dict:
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"samples", "events", "settings"} <= tables or "schema_meta" in tables:
            raise ValueError("not a v0.1 greenhouse database")
        cols = {r[1] for r in con.execute("PRAGMA table_info(samples)")}
        need = {"ts", "analog_json", "analog_types_json", "inputs_json", "relays_json", "mode", "sector",
                "auto_phase", "quality"}
        if not need <= cols:
            raise ValueError(f"v0.1 samples table missing columns {need - cols}")
        return {"samples": con.execute("SELECT count(*) FROM samples").fetchone()[0],
                "events": con.execute("SELECT count(*) FROM events").fetchone()[0],
                "settings": dict(con.execute("SELECT key, value_json FROM settings").fetchall())}
    finally:
        con.close()


def import_legacy(legacy_db: str | Path, hist: Historian, backup_dir: str | Path) -> dict:
    legacy_db = Path(legacy_db)
    info = inspect_legacy(legacy_db)
    src = sqlite3.connect(f"file:{legacy_db}?mode=ro", uri=True)
    try:
        bk = backup_to(src, Path(backup_dir) / f"legacy-v0_1-{utc_iso().replace(':', '')}.sqlite3")
    finally:
        src.close()
    digest = _sha256(bk)
    key = f"legacy_v01_import:{digest}"
    if hist.query("SELECT value FROM schema_meta WHERE key=?", (key,)):
        raise FileExistsError("this legacy database was already imported")
    con = sqlite3.connect(f"file:{bk}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    imported = 0
    try:
        rows = con.execute("SELECT * FROM samples ORDER BY id").fetchall()
        events = con.execute("SELECT * FROM events ORDER BY id").fetchall()
    finally:
        con.close()
    session = f"legacy_v01_{digest[:12]}"

    def op(c: sqlite3.Connection):
        nonlocal imported
        persisted = utc_iso()
        seq = 0
        for r in rows:
            analog = json.loads(r["analog_json"])
            types = json.loads(r["analog_types_json"])
            di = json.loads(r["inputs_json"])
            do = json.loads(r["relays_json"])
            ok = r["quality"] == "OK" and analog is not None
            quality = "GOOD" if ok else "UNAVAILABLE"
            reason = f"v0.1 quality={r['quality']}; mode={r['mode']} sector={r['sector']} phase={r['auto_phase']}"
            cur = c.execute("INSERT INTO cycles(cycle_id,session_id,run_id,sim_s,persisted_utc,n_points,cadence_s,"
                            "cadence_basis) VALUES(?,?,NULL,NULL,?,24,NULL,'LEGACY_UNKNOWN')",
                            (f"{session}:{r['id']}", session, persisted))
            pk = cur.lastrowid
            vals = []
            for kind, arr in (("AI", analog), ("DI", di), ("DO", do)):
                for ch in range(1, 9):
                    v = arr[ch - 1] if ok and arr is not None and len(arr) >= ch else None
                    unit = None
                    if kind == "AI":
                        t = types[ch - 1] if types and len(types) >= ch else None
                        unit = TYPE_UNIT.get(t) if t is not None else None
                        rtype = "int"
                    else:
                        unit, rtype = "bool", "bool"
                    seq += 1
                    q = quality if v is not None else "UNAVAILABLE"
                    vals.append((pk, f"{LEGACY_DEVICE}.{kind}{ch}", r["ts"], persisted, seq, q, reason,
                                 None if v is None else float(v), rtype if v is not None else None, unit, None,
                                 None, None, "LEGACY_UNKNOWN", "v0.1", "v0.1", LEGACY_DEVICE, LEGACY_BUS, session,
                                 None, None, json.dumps({"legacy_sample_id": r["id"]}), None, None))
            c.executemany("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,reason,"
                          "raw_value,raw_type,raw_unit,value,unit,calibration_ref,origin,source_profile,config_version,"
                          "device_uid,bus_id,session_id,sim_run_id,sim_s,override_json,last_good_value,last_good_utc)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
            imported += 1
        c.executemany("INSERT INTO events(utc,kind,actor,session_id,run_id,sim_s,payload_json) VALUES(?,?,?,?,NULL,NULL,?)",
                      [(e["ts"], f"LEGACY_{e['type']}", e["actor"], session,
                        json.dumps({"detail": e["detail"], "legacy_event_id": e["id"], "origin": "LEGACY_UNKNOWN"}))
                       for e in events])
        c.execute("INSERT INTO events(utc,kind,actor,session_id,run_id,sim_s,payload_json) VALUES(?,?,?,?,NULL,NULL,?)",
                  (utc_iso(), "LEGACY_SETTINGS_NOT_APPLIED", "migration", session,
                   json.dumps({"settings": info["settings"], "note": "v0.1 thresholds/AUTO timings are recorded for "
                               "reference only and are not applied to any profile"})))
        c.execute("INSERT INTO schema_meta(key,value) VALUES(?,?)",
                  (key, json.dumps({"source": str(legacy_db), "backup": str(bk), "imported_utc": utc_iso(),
                                    "samples": len(rows), "events": len(events)})))
    hist.submit(op, wait=True, timeout=600)
    return {"legacy_db": str(legacy_db), "backup": str(bk), "sha256": digest, "samples_imported": imported,
            "events_imported": len(events), "points_per_sample": 24, "origin": "LEGACY_UNKNOWN",
            "settings_recorded_not_applied": info["settings"]}
