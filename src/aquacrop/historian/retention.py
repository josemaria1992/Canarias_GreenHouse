"""Archive and retention (brief §8).

* `create_archive` exports a UTC range of samples and events to gzip CSV files with a manifest
  (checksums, row counts, configuration/calibration references, schema and software version) and
  registers it. It never deletes anything.
* `verify_archive` re-checks the checksums and row counts.
* `apply_retention` deletes sample rows only when ALL hold: the retention policy is APPROVED (the
  shipped value is a DEMO proposal, so by default nothing is ever deleted), the rows are older than
  the retention window, and every deleted row lies inside a registered archive whose files verify.
  Events, commands, alarms, users and leases are audit records and are never pruned here.
* `storage_pressure` reports budget/reserve pressure so callers can raise a visible alarm.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import __version__
from .backup import _sha256
from .schema import utc_iso

SAMPLE_COLS = ["id", "point_id", "acquired_utc", "persisted_utc", "sequence", "quality", "reason", "raw_value",
               "raw_type", "raw_unit", "value", "unit", "calibration_ref", "origin", "source_profile",
               "config_version", "device_uid", "bus_id", "session_id", "sim_run_id", "sim_s", "override_json"]
EVENT_COLS = ["id", "utc", "kind", "actor", "session_id", "run_id", "sim_s", "payload_json"]


def _parse(ts: str) -> str:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def create_archive(hist, start: str, end: str, destination: str | Path) -> dict:
    start, end = _parse(start), _parse(end)
    if start >= end:
        raise ValueError("start must be before end")
    stamp = f"{start[:19]}_{end[:19]}".replace(":", "")
    out = Path(destination) / f"archive-{stamp}"
    if out.exists():
        raise FileExistsError(f"{out} exists; archives are never overwritten")
    out.mkdir(parents=True)
    files = {}
    for name, cols, sql in (
            ("samples.csv.gz", SAMPLE_COLS,
             f"SELECT {', '.join(SAMPLE_COLS)} FROM samples WHERE acquired_utc>=? AND acquired_utc<? ORDER BY id"),
            ("events.csv.gz", EVENT_COLS,
             f"SELECT {', '.join(EVENT_COLS)} FROM events WHERE utc>=? AND utc<? ORDER BY id")):
        rows = hist.query(sql, (start, end))
        p = out / name
        with gzip.open(p, "wt", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([r[c] for c in cols])
        files[name] = {"sha256": _sha256(p), "rows": len(rows), "bytes": p.stat().st_size}
    meta = hist.query("SELECT DISTINCT source_profile, config_version, calibration_ref, origin FROM samples "
                      "WHERE acquired_utc>=? AND acquired_utc<?", (start, end))
    manifest = {"start_utc": start, "end_utc": end, "created_utc": utc_iso(), "software_version": __version__,
                "files": files, "configurations": sorted({f"{r[0]}@{r[1]}" for r in meta}),
                "calibration_refs": sorted({r[2] for r in meta if r[2]}), "origins": sorted({r[3] for r in meta}),
                "note": "UTC half-open range [start, end). Audit tables (events also kept in DB) are never pruned."}
    mp = out / "manifest.json"
    mp.write_text(json.dumps(manifest, indent=2))
    msha = _sha256(mp)
    aid = "arc_" + hashlib.sha256(f"{start}{end}{msha}".encode()).hexdigest()[:16]
    hist.submit(lambda c: c.execute(
        "INSERT INTO archives(archive_id,start_utc,end_utc,path,manifest_sha256,samples,events,created_utc)"
        " VALUES(?,?,?,?,?,?,?,?)", (aid, start, end, str(out.resolve()), msha, files["samples.csv.gz"]["rows"],
                                     files["events.csv.gz"]["rows"], utc_iso())), wait=True)
    hist.event("ARCHIVE_CREATED", {"archive_id": aid, "path": str(out), "start_utc": start, "end_utc": end,
                                   "samples": files["samples.csv.gz"]["rows"]}, actor="system")
    return {"archive_id": aid, "path": str(out), "manifest_sha256": msha} | manifest


def verify_archive(path: str | Path, expected_manifest_sha256: str | None = None) -> dict:
    path = Path(path)
    mp = path / "manifest.json"
    problems = []
    if not mp.exists():
        return {"ok": False, "problems": ["manifest.json missing"]}
    if expected_manifest_sha256 and _sha256(mp) != expected_manifest_sha256:
        problems.append("manifest checksum differs from the register")
    man = json.loads(mp.read_text())
    for name, info in man["files"].items():
        p = path / name
        if not p.exists():
            problems.append(f"{name} missing")
            continue
        if _sha256(p) != info["sha256"]:
            problems.append(f"{name} checksum mismatch")
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            n = sum(1 for _ in csv.reader(f)) - 1
        if n != info["rows"]:
            problems.append(f"{name} has {n} rows, manifest says {info['rows']}")
    return {"ok": not problems, "problems": problems, "manifest": man}


def apply_retention(hist, *, now: datetime | None = None, dry_run: bool = False) -> dict:
    h = hist.cfg.historian
    if h.retention_provenance != "APPROVED" or h.retention_days is None:
        return {"deleted": 0, "reason": f"retention policy is {h.retention_provenance}; nothing is deleted until an "
                                        "owner-approved policy is configured"}
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=h.retention_days)).isoformat(
        timespec="microseconds")
    archives = hist.query("SELECT archive_id, start_utc, end_utc, path, manifest_sha256 FROM archives "
                          "WHERE start_utc < ? ORDER BY start_utc", (cutoff,))
    deletable: list[tuple[str, str, str]] = []
    skipped = []
    for a in archives:
        v = verify_archive(a["path"], a["manifest_sha256"])
        if not v["ok"]:
            skipped.append({"archive_id": a["archive_id"], "problems": v["problems"]})
            continue
        deletable.append((a["archive_id"], a["start_utc"], min(a["end_utc"], cutoff)))
    total = 0
    for aid, s, e in deletable:
        n = hist.query("SELECT count(*) FROM samples WHERE acquired_utc>=? AND acquired_utc<?", (s, e))[0][0]
        total += n
        if dry_run or not n:
            continue

        def op(c, s=s, e=e, aid=aid):
            c.execute("DELETE FROM samples WHERE acquired_utc>=? AND acquired_utc<?", (s, e))
            c.execute("DELETE FROM cycles WHERE cycle_pk NOT IN (SELECT DISTINCT cycle_pk FROM samples)")
            c.execute("UPDATE archives SET verified_utc=? WHERE archive_id=?", (utc_iso(), aid))
        hist.submit(op, wait=True, timeout=600)
    result = {"deleted": 0 if dry_run else total, "would_delete": total if dry_run else None, "cutoff_utc": cutoff,
              "archives_used": [d[0] for d in deletable], "archives_skipped": skipped,
              "unarchived_older_rows": hist.query(
                  "SELECT count(*) FROM samples WHERE acquired_utc<? AND NOT EXISTS (SELECT 1 FROM archives a "
                  "WHERE samples.acquired_utc>=a.start_utc AND samples.acquired_utc<a.end_utc)", (cutoff,))[0][0]}
    if not dry_run:
        hist.event("RETENTION_APPLIED", result, actor="system")
    return result


def storage_pressure(hist) -> tuple[str | None, str]:
    """(severity or None, message)."""
    st = hist.storage()
    if st["below_reserve"]:
        return "CRITICAL", (f"free disk {st['free_bytes'] / 2**30:.2f} GiB below reserve "
                            f"{st['reserve_bytes'] / 2**30:.2f} GiB: new starts inhibited")
    if st["over_budget"]:
        return "WARNING", (f"database {st['db_bytes'] / 2**30:.2f} GiB over budget "
                           f"{st['budget_bytes'] / 2**30:.2f} GiB; archive and apply an approved retention policy")
    return None, "ok"


def evaluate_storage_alarm(hist, alarms, sim_s: float | None = None) -> None:
    """Raise/clear STORAGE_PRESSURE and STORAGE_WRITE_FAILED on the given AlarmEngine."""
    sev, msg = storage_pressure(hist)
    if sev:
        alarms.raise_or_update("STORAGE_PRESSURE", "historian", severity=sev, message=msg, sim_s=sim_s)
    else:
        alarms.clear("STORAGE_PRESSURE", "historian")
    if not hist.health["ok"]:
        alarms.raise_or_update("STORAGE_WRITE_FAILED", "historian", severity="CRITICAL", sim_s=sim_s,
                               message=f"historian degraded: {hist.health.get('degraded_reason')}; starts inhibited")
    else:
        alarms.clear("STORAGE_WRITE_FAILED", "historian")
