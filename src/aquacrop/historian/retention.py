"""Archive, rolling retention and capacity alarms (owner requirements of 2026-10-12, ADR-0008).

* Rolling retention keeps a 365-day window. Under an APPROVED policy, rows older than the window are
  deleted in small batches through the single writer, every `retention_check_interval_s` while the
  server runs (and on demand with `retention apply`). Each run that deletes anything is recorded as a
  RETENTION_PRUNED event. Nothing inside the window is ever deleted, for a size limit or any other
  reason; open alarms, users and the schema/migration/archive registers are never pruned.
* Retention trusts the wall clock only after sanity checks. If the clock steps forward by more than
  an hour while running, or at start is more than 30 days past the newest stored record (or behind
  it), retention holds and raises RETENTION_HELD until someone checks the date and acknowledges it.
* Capacity alarms: STORAGE_CAPACITY (WARNING: low free disk, database over budget, or the 365-day
  projection over budget), STORAGE_PRESSURE (CRITICAL: free disk below the reserve, new starts and ON
  refused), HISTORY_SNAPSHOTS_PAUSED (CRITICAL: below the snapshot floor, periodic snapshots pause
  while events, commands and alarms are still written), STORAGE_WRITE_FAILED (CRITICAL).
* `create_archive` / `verify_archive` export and check a UTC range as gzip CSV with a manifest. An
  archive is optional: it is how history is kept beyond the window, off the device.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import __version__
from .backup import _sha256
from .schema import utc_iso

SAMPLE_COLS = ["id", "point_id", "acquired_utc", "persisted_utc", "sequence", "quality", "reason", "raw_value",
               "raw_type", "raw_unit", "value", "unit", "calibration_ref", "origin", "source_profile",
               "config_version", "device_uid", "bus_id", "session_id", "sim_run_id", "sim_s", "override_json",
               "last_good_value", "last_good_utc"]   # a STALE row's value is null; its last good reading is here
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
                "note": "UTC half-open range [start, end). Rolling retention later removes these rows from the "
                        "database once they leave the window; this archive is the copy that outlives it."}
    mp = out / "manifest.json"
    mp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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
    man = json.loads(mp.read_text(encoding="utf-8"))
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


PRUNE_CHUNK_ROWS = 5000
HOLD_FORWARD_STEP_S = 3600.0
HOLD_START_GAP_DAYS = 30.0


def _chunks(hist, sql: str, params: tuple, chunk_rows: int, *, fk_off: bool = False) -> int:
    total = 0
    while True:
        n = hist.submit(lambda c: c.execute(sql, params + (chunk_rows,)).rowcount, wait=True, timeout=600,
                        solo_without_fk=fk_off)
        total += n
        if n < chunk_rows:
            return total


def prune_expired(hist, *, now: datetime | None = None, chunk_rows: int = PRUNE_CHUNK_ROWS,
                  dry_run: bool = False) -> dict:
    """Delete everything older than the rolling window (APPROVED policy only). Batches go through the
    writer queue, so snapshots, events and commands keep flowing between them."""
    h = hist.cfg.historian
    if h.retention_provenance != "APPROVED":
        return {"deleted": {}, "reason": f"retention policy is {h.retention_provenance}; nothing is deleted"}
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=h.retention_days)).isoformat(
        timespec="microseconds")
    if dry_run:
        q = lambda sql: hist.query(sql, (cutoff,))[0][0]  # noqa: E731
        return {"dry_run": True, "cutoff_utc": cutoff, "would_delete": {
            "samples": q("SELECT count(*) FROM samples WHERE acquired_utc < ?"),
            "events": q("SELECT count(*) FROM events WHERE utc < ?"),
            "commands": q("SELECT count(*) FROM commands WHERE intent_utc < ? AND outcome NOT IN "
                          "('ACCEPTED','EXECUTING')"),
            "alarm_occurrences": q("SELECT count(*) FROM alarm_occurrences WHERE closed=1 AND updated_utc < ?"),
            "bench_leases": q("SELECT count(*) FROM bench_leases WHERE expires_utc < ?"),
            "auth_sessions": q("SELECT count(*) FROM auth_sessions WHERE absolute_expiry_utc < ?")}}
    d = {"samples": _chunks(hist, "DELETE FROM samples WHERE id IN (SELECT id FROM samples WHERE acquired_utc < ? "
                                   "ORDER BY acquired_utc LIMIT ?)", (cutoff,), chunk_rows)}

    # Sample ids grow with their cycle (one writer, cycle row then its samples), so every cycle below
    # the oldest remaining sample's cycle has no samples left. Deleting by that bound with
    # foreign-key checks off avoids a full scan of samples per deleted cycle.
    def drop_cycles(c, limit):
        row = c.execute("SELECT cycle_pk FROM samples ORDER BY id LIMIT 1").fetchone()
        bound = row[0] if row else (c.execute("SELECT coalesce(max(cycle_pk), 0) + 1 FROM cycles").fetchone()[0])
        return c.execute("DELETE FROM cycles WHERE cycle_pk IN (SELECT cycle_pk FROM cycles WHERE cycle_pk < ? "
                         "AND persisted_utc < ? ORDER BY cycle_pk LIMIT ?)", (bound, cutoff, limit)).rowcount
    total = 0
    while True:
        n = hist.submit(lambda c: drop_cycles(c, chunk_rows), wait=True, timeout=600, solo_without_fk=True)
        total += n
        if n < chunk_rows:
            break
    d["cycles"] = total
    d["events"] = _chunks(hist, "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE utc < ? "
                                "ORDER BY utc LIMIT ?)", (cutoff,), chunk_rows)
    d["commands"] = _chunks(hist, "DELETE FROM commands WHERE request_id IN (SELECT request_id FROM commands "
                                  "WHERE intent_utc < ? AND outcome NOT IN ('ACCEPTED','EXECUTING') "
                                  "ORDER BY intent_utc LIMIT ?)", (cutoff,), chunk_rows)
    d["alarm_occurrences"] = _chunks(hist, "DELETE FROM alarm_occurrences WHERE occurrence_id IN (SELECT "
                                           "occurrence_id FROM alarm_occurrences WHERE closed=1 AND updated_utc < ? "
                                           "LIMIT ?)", (cutoff,), chunk_rows)
    d["bench_leases"] = _chunks(hist, "DELETE FROM bench_leases WHERE lease_id IN (SELECT lease_id FROM "
                                      "bench_leases WHERE expires_utc < ? LIMIT ?)", (cutoff,), chunk_rows)
    d["auth_sessions"] = _chunks(hist, "DELETE FROM auth_sessions WHERE session_hash IN (SELECT session_hash "
                                       "FROM auth_sessions WHERE absolute_expiry_utc < ? LIMIT ?)", (cutoff,),
                                 chunk_rows)
    res = {"cutoff_utc": cutoff, "retention_days": h.retention_days, "deleted": d}
    if any(d.values()):
        hist.event("RETENTION_PRUNED", res | {"note": "rows older than the rolling window; freed pages are "
                                                      "reused by new history"}, actor="system")
    return res


def apply_retention(hist, *, now: datetime | None = None, dry_run: bool = False) -> dict:
    """CLI entry point (`retention apply`): the same rolling prune the server runs hourly."""
    return prune_expired(hist, now=now, dry_run=dry_run)


class RetentionTask:
    """Runs the rolling prune in the background while the server runs, behind clock sanity checks.

    `tick(alarms)` is called from the thread that owns the alarm engine; it never blocks on deletes."""

    def __init__(self, hist, *, wall=time.time, monotonic=time.monotonic):
        self.hist, self._wall, self._mono = hist, wall, monotonic
        self.held: str | None = None
        self.running = False
        self.last_result: dict | None = None
        self.last_error: str | None = None
        self._next_due: float | None = None
        self._ref: tuple[float, float] | None = None
        self._started_check_done = False

    def _hold(self, why: str) -> None:
        self.held = why
        self.hist.event("RETENTION_HELD", {"reason": why}, actor="system")

    def _newest_prior_record(self) -> str | None:
        """Newest time written by an earlier process: snapshots, events, commands and the process
        register, so a history with audit records but no snapshot (or paused snapshots) is guarded too."""
        sid = self.hist.session_id
        rows = self.hist.query(
            "SELECT (SELECT persisted_utc FROM cycles WHERE session_id != ? ORDER BY cycle_pk DESC LIMIT 1), "
            "(SELECT utc FROM events WHERE coalesce(session_id, '') != ? ORDER BY utc DESC LIMIT 1), "
            "(SELECT intent_utc FROM commands WHERE session_id != ? ORDER BY intent_utc DESC LIMIT 1), "
            "(SELECT max(coalesce(ended_utc, started_utc)) FROM process_sessions WHERE session_id != ?)",
            (sid, sid, sid, sid))[0]
        stamps = [r for r in rows if r]
        return max(stamps, key=lambda t: datetime.fromisoformat(t)) if stamps else None

    def _check_clock(self) -> None:
        mono, wall = self._mono(), self._wall()
        if not self._started_check_done:
            self._started_check_done = True
            newest = self._newest_prior_record()
            if newest:
                gap_s = wall - datetime.fromisoformat(newest).timestamp()
                if gap_s < -300:
                    self._hold(f"system clock is {-gap_s / 3600:.1f} h behind the newest stored record "
                               f"({newest}); check the date and time")
                elif gap_s > HOLD_START_GAP_DAYS * 86400:
                    self._hold(f"system clock is {gap_s / 86400:.0f} days after the newest stored record "
                               f"({newest}); confirm the date before history older than the window is deleted")
        elif self._ref is not None:
            step = (wall - self._ref[1]) - (mono - self._ref[0])
            if step > HOLD_FORWARD_STEP_S:
                self._hold(f"wall clock stepped forward by {step / 3600:.1f} h while running; confirm the date "
                           "before history older than the window is deleted")
        self._ref = (mono, wall)

    def tick(self, alarms, sim_s: float | None = None) -> None:
        h = self.hist.cfg.historian
        if h.retention_provenance != "APPROVED":
            return
        self._check_clock()
        if self.held is not None:
            occ = alarms.open.get(("RETENTION_HELD", "historian"))
            if occ is not None and occ.acknowledged:
                alarms.clear("RETENTION_HELD", "historian")
                self.hist.event("RETENTION_RELEASED", {"reason": self.held, "acknowledged_by": occ.ack_user},
                                actor=occ.ack_user)
                self.held = None
                self._next_due = None
            else:
                alarms.raise_or_update("RETENTION_HELD", "historian", severity="WARNING", sim_s=sim_s,
                                       message=f"rolling retention held: {self.held}. Acknowledge this alarm "
                                               "once the date and time are correct.")
                return
        now = self._mono()
        if self.running or (self._next_due is not None and now < self._next_due):
            return
        self._next_due = now + h.retention_check_interval_s
        self.running = True
        threading.Thread(target=self._run, args=(datetime.fromtimestamp(self._wall(), tz=timezone.utc),),
                         name="historian-retention", daemon=True).start()

    def _run(self, at: datetime) -> None:
        try:
            self.last_result = prune_expired(self.hist, now=at) | {"run_utc": at.isoformat()}
            self.last_error = None
        except Exception as e:   # visible in status; the next run retries
            self.last_error = f"{at.isoformat()} {e!r}"
        finally:
            self.running = False

    def view(self) -> dict:
        return {"held": self.held, "running": self.running, "last_result": self.last_result,
                "last_error": self.last_error}


def storage_alarms(hist) -> list[tuple[str, str, str]]:
    """(rule_id, severity, message) for every capacity condition that holds now."""
    st = hist.storage()
    gib = 2**30
    out = []
    if st["snapshots_paused"] or st["below_snapshot_floor"]:
        out.append(("HISTORY_SNAPSHOTS_PAUSED", "CRITICAL",
                    f"free disk {st['free_bytes'] / gib:.2f} GiB below the snapshot floor "
                    f"{st['snapshot_floor_bytes'] / gib:.2f} GiB: periodic snapshots paused (events, commands and "
                    "alarms still recorded; nothing deleted). Free space to resume."))
    if st["below_reserve"]:
        out.append(("STORAGE_PRESSURE", "CRITICAL",
                    f"free disk {st['free_bytes'] / gib:.2f} GiB below reserve {st['reserve_bytes'] / gib:.2f} GiB: "
                    "new starts and ON commands refused"))
    why = []
    if st["below_warning"]:
        why.append(f"free disk {st['free_bytes'] / gib:.2f} GiB below {st['warning_free_bytes'] / gib:.2f} GiB")
    if st["over_budget"]:
        why.append(f"database {st['db_bytes'] / gib:.2f} GiB over budget {st['budget_bytes'] / gib:.2f} GiB")
    elif st["projected_over_budget"]:
        why.append(f"{st['retention_days']}-day projection {st['projected_window_bytes'] / gib:.2f} GiB over "
                   f"budget {st['budget_bytes'] / gib:.2f} GiB")
    if why:
        out.append(("STORAGE_CAPACITY", "WARNING", "; ".join(why) + ". History inside the retention window is "
                                                                    "never deleted to make room."))
    return out


def storage_pressure(hist) -> tuple[str | None, str]:
    """Most severe capacity condition as (severity or None, message)."""
    al = storage_alarms(hist)
    crit = [a for a in al if a[1] == "CRITICAL"]
    pick = crit[0] if crit else (al[0] if al else None)
    return (pick[1], pick[2]) if pick else (None, "ok")


STORAGE_RULES = ("HISTORY_SNAPSHOTS_PAUSED", "STORAGE_PRESSURE", "STORAGE_CAPACITY")


def evaluate_storage_alarm(hist, alarms, sim_s: float | None = None) -> None:
    """Raise/clear the capacity alarms and STORAGE_WRITE_FAILED on the given AlarmEngine."""
    active = {rule: (sev, msg) for rule, sev, msg in storage_alarms(hist)}
    for rule in STORAGE_RULES:
        if rule in active:
            sev, msg = active[rule]
            alarms.raise_or_update(rule, "historian", severity=sev, message=msg, sim_s=sim_s)
        else:
            alarms.clear(rule, "historian")
    if not hist.health["ok"]:
        alarms.raise_or_update("STORAGE_WRITE_FAILED", "historian", severity="CRITICAL", sim_s=sim_s,
                               message=f"historian degraded: {hist.health.get('degraded_reason')}; starts inhibited")
    else:
        alarms.clear("STORAGE_WRITE_FAILED", "historian")
