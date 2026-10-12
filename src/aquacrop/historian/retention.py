"""Archive, rolling retention and capacity alarms (owner requirements of 2026-10-12, ADR-0008).

* Rolling retention keeps a 365-day window. Under an APPROVED policy, rows older than the window are
  deleted in small batches through the single writer, every `retention_check_interval_s` while the
  server runs (and with `retention apply --confirm-cutoff` while it is stopped). Each run that deletes
  anything is recorded as a RETENTION_PRUNED event. Nothing inside the window is ever deleted, for a
  size limit or any other reason. Open alarms, users and the schema/migration/archive registers are
  never pruned; process-session and simulation-run records are pruned only once no remaining row can
  refer to them. The database therefore stops growing only approximately once the window is full:
  the registers keep growing slowly and capacity monitoring stays on after the first year.
* Retention deletes only while the wall clock is trusted, and it never assumes internet time. The
  clock is trusted at start when it is no more than CLOCK_TOLERANCE_S behind and no more than
  CONFIRM_AFTER_DOWNTIME_S ahead of the newest record an earlier process stored; while running it
  stays trusted as long as it moves with the monotonic clock (within CLOCK_TOLERANCE_S). Anything
  else (an offline start after a long power cut, an RTC-less boot, a large NTP correction either way)
  holds deletion and raises RETENTION_HELD. Holding only keeps more history (about 23 MB a day).
  Deletion resumes only when an administrator confirms the proposed cutoff (`confirm()`, recorded as
  RETENTION_CLOCK_CONFIRMED); acknowledging the alarm only records the acknowledgement.
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
ARCHIVE_PAGE_ROWS = 50000


def _parse(ts: str) -> str:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def create_archive(hist, start: str, end: str, destination: str | Path) -> dict:
    """Export [start, end) in pages of ARCHIVE_PAGE_ROWS rows (short read transactions, bounded memory).
    It is a maintenance operation run with the server stopped (`archive create`), so the range does not
    change between pages."""
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
             f"SELECT {', '.join(SAMPLE_COLS)} FROM samples WHERE acquired_utc>=? AND acquired_utc<? AND id>? "
             "ORDER BY id LIMIT ?"),
            ("events.csv.gz", EVENT_COLS,
             f"SELECT {', '.join(EVENT_COLS)} FROM events WHERE utc>=? AND utc<? AND id>? ORDER BY id LIMIT ?")):
        p = out / name
        n = 0
        with gzip.open(p, "wt", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            last = 0
            while True:   # bounded readers: short read transactions of ARCHIVE_PAGE_ROWS rows, by id
                rows = hist.query(sql, (start, end, last, ARCHIVE_PAGE_ROWS))
                for r in rows:
                    w.writerow([r[c] for c in cols])
                n += len(rows)
                if len(rows) < ARCHIVE_PAGE_ROWS:
                    break
                last = rows[-1]["id"]
        files[name] = {"sha256": _sha256(p), "rows": n, "bytes": p.stat().st_size}
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
PRUNE_MAX_TRANSACTIONS_PER_RUN = 100   # about a week of 48-point history per hourly run when catching up
CLOCK_TOLERANCE_S = 300.0           # wall vs monotonic-carried time; and how far behind stored records
CONFIRM_AFTER_DOWNTIME_S = 3600.0   # a start this long after the newest stored record needs confirmation
PROPOSAL_TOLERANCE_S = 600.0        # a confirmation must echo a cutoff within this of the current one


class _Stop(Exception):
    pass


def _chunks(hist, sql: str, params: tuple, chunk_rows: int, *, fk_off: bool = False, guard=None,
            into: dict | None = None, key: str = "") -> int:
    """Delete in transactions of at most `chunk_rows` rows; `guard()` may stop between transactions.
    With `into`, the running count is kept in `into[key]`, so a stop keeps what was already deleted."""
    acc = into if into is not None else {}
    acc[key] = acc.get(key, 0)
    while True:
        if guard is not None:
            guard()
        n = hist.submit(lambda c: c.execute(sql, params + (chunk_rows,)).rowcount, wait=True, timeout=600,
                        solo_without_fk=fk_off)
        acc[key] += n
        if n < chunk_rows:
            return acc[key]


def prune_expired(hist, *, now: datetime | None = None, chunk_rows: int = PRUNE_CHUNK_ROWS,
                  dry_run: bool = False, max_chunks: int | None = None, wal_limit_bytes: int | None = None) -> dict:
    """Delete everything older than the rolling window (APPROVED policy only). Batches go through the
    writer queue, so snapshots, events and commands keep flowing between them. Each transaction deletes
    at most `chunk_rows` rows; the run stops early (the rest waits for the next run) after `max_chunks`
    transactions or when the WAL is above `wal_limit_bytes` (a long reader is holding it back, and
    deleting more would only grow it)."""
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
    budget = {"chunks": 0}
    wal = Path(str(hist.db_path) + "-wal")

    def guard():
        if wal_limit_bytes is not None and wal.exists() and wal.stat().st_size > wal_limit_bytes:
            raise _Stop(f"WAL {wal.stat().st_size / 2**20:.0f} MiB above {wal_limit_bytes / 2**20:.0f} MiB: a long "
                        "reader is holding it back; the rest waits for the next run")
        if max_chunks is not None and budget["chunks"] >= max_chunks:
            raise _Stop(f"{max_chunks} delete transactions in this run; the rest waits for the next run")
        budget["chunks"] += 1

    d: dict[str, int] = {}
    stopped = None
    try:
        _chunks(hist, "DELETE FROM samples WHERE id IN (SELECT id FROM samples WHERE acquired_utc < ? "
                      "ORDER BY acquired_utc LIMIT ?)", (cutoff,), chunk_rows, guard=guard, into=d, key="samples")

        # Sample ids grow with their cycle (one writer, cycle row then its samples), so every cycle below
        # the oldest remaining sample's cycle has no samples left. Deleting by that bound with
        # foreign-key checks off avoids a full scan of samples per deleted cycle.
        def drop_cycles(c, limit):
            row = c.execute("SELECT cycle_pk FROM samples ORDER BY id LIMIT 1").fetchone()
            bound = row[0] if row else (c.execute("SELECT coalesce(max(cycle_pk), 0) + 1 FROM cycles").fetchone()[0])
            return c.execute("DELETE FROM cycles WHERE cycle_pk IN (SELECT cycle_pk FROM cycles WHERE cycle_pk < ? "
                             "AND persisted_utc < ? ORDER BY cycle_pk LIMIT ?)", (bound, cutoff, limit)).rowcount
        d["cycles"] = 0
        while True:
            guard()
            n = hist.submit(lambda c: drop_cycles(c, chunk_rows), wait=True, timeout=600, solo_without_fk=True)
            d["cycles"] += n
            if n < chunk_rows:
                break
        _chunks(hist, "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE utc < ? "
                      "ORDER BY utc LIMIT ?)", (cutoff,), chunk_rows, guard=guard, into=d, key="events")
        _chunks(hist, "DELETE FROM commands WHERE request_id IN (SELECT request_id FROM commands "
                      "WHERE intent_utc < ? AND outcome NOT IN ('ACCEPTED','EXECUTING') "
                      "ORDER BY intent_utc LIMIT ?)", (cutoff,), chunk_rows, guard=guard, into=d, key="commands")
        _chunks(hist, "DELETE FROM alarm_occurrences WHERE occurrence_id IN (SELECT "
                      "occurrence_id FROM alarm_occurrences WHERE closed=1 AND updated_utc < ? "
                      "LIMIT ?)", (cutoff,), chunk_rows, guard=guard, into=d, key="alarm_occurrences")
        _chunks(hist, "DELETE FROM bench_leases WHERE lease_id IN (SELECT lease_id FROM "
                      "bench_leases WHERE expires_utc < ? LIMIT ?)", (cutoff,), chunk_rows,
                guard=guard, into=d, key="bench_leases")
        _chunks(hist, "DELETE FROM auth_sessions WHERE session_hash IN (SELECT session_hash "
                      "FROM auth_sessions WHERE absolute_expiry_utc < ? LIMIT ?)", (cutoff,),
                chunk_rows, guard=guard, into=d, key="auth_sessions")
        guard()
        d |= hist.submit(lambda c: _prune_provenance(c, cutoff, hist.session_id), wait=True, timeout=600)
    except _Stop as e:
        stopped = str(e)
    res = {"cutoff_utc": cutoff, "retention_days": h.retention_days, "deleted": d, "transactions": budget["chunks"]}
    if stopped:
        res["incomplete"] = stopped
    if any(d.values()):
        hist.event("RETENTION_PRUNED", res | {"note": "rows older than the rolling window; freed pages are "
                                                      "reused by new history"}, actor="system")
    elif stopped:
        hist.event("RETENTION_DEFERRED", res, actor="system")
    return res


def _prune_provenance(c, cutoff: str, current_session: str) -> dict:
    """Process-session and simulation-run records are provenance for samples, events and commands. One
    is deleted only when it ended before the cutoff and the oldest remaining sample, cycle, event and
    command are all later than its end, so nothing left in the database can refer to it. Sessions run
    one at a time (the owner lock), so a session without a recorded end ended before the next start."""
    oldest = [r[0] for r in (
        c.execute("SELECT min(acquired_utc) FROM samples").fetchone(),
        c.execute("SELECT persisted_utc FROM cycles ORDER BY cycle_pk LIMIT 1").fetchone() or (None,),
        c.execute("SELECT min(utc) FROM events").fetchone(),
        c.execute("SELECT min(intent_utc) FROM commands").fetchone()) if r[0]]
    floor = min([cutoff] + oldest, key=lambda t: datetime.fromisoformat(t))
    rows = c.execute("SELECT session_id, started_utc, ended_utc FROM process_sessions ORDER BY started_utc").fetchall()
    gone = []
    for i, (sid, started, ended) in enumerate(rows):
        end = ended or (rows[i + 1][1] if i + 1 < len(rows) else None)
        if sid == current_session or end is None:
            continue
        if datetime.fromisoformat(end) < datetime.fromisoformat(floor):
            gone.append(sid)
    runs = 0
    for sid in gone:
        runs += c.execute("DELETE FROM sim_runs WHERE session_id=?", (sid,)).rowcount
        c.execute("DELETE FROM process_sessions WHERE session_id=?", (sid,))
    return {"process_sessions": len(gone), "sim_runs": runs}


def clock_sync_status() -> str:
    """Kernel clock-discipline state, for information only (it never releases a hold): SYNCHRONIZED
    when something (NTP, GPS, PTP) disciplines the clock, UNSYNCHRONIZED, or UNKNOWN."""
    try:
        import ctypes
        import ctypes.util
        import sys
        if not sys.platform.startswith("linux"):
            return "UNKNOWN"
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        buf = ctypes.create_string_buffer(1024)            # struct timex, modes=0: read only
        state = libc.adjtimex(buf)
        if state < 0:
            return "UNKNOWN"
        return "UNSYNCHRONIZED" if state == 5 else "SYNCHRONIZED"   # 5 = TIME_ERROR
    except Exception:
        return "UNKNOWN"


def _span(seconds: float) -> str:
    if seconds >= 2 * 86400:
        return f"{seconds / 86400:.1f} days"
    if seconds >= 7200:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 60:.0f} min"


class ProposalMismatch(ValueError):
    def __init__(self, proposal: dict):
        super().__init__(f"the proposed cutoff is now {proposal['cutoff_utc']}; review it and confirm again")
        self.proposal = proposal


def retention_proposal(hist, *, wall_s: float, trusted_s: float | None = None) -> dict:
    """What a confirmation would release: the cutoff the clock gives now and the rows older than it."""
    h = hist.cfg.historian
    basis = wall_s if trusted_s is None else min(wall_s, trusted_s)
    at = datetime.fromtimestamp(basis, tz=timezone.utc)
    dry = prune_expired(hist, now=at, dry_run=True)
    sid = hist.session_id
    newest = _newest_record(hist, exclude_session=None)
    prior = _newest_record(hist, exclude_session=sid)
    return {"wall_utc": datetime.fromtimestamp(wall_s, tz=timezone.utc).isoformat(),
            "cutoff_utc": dry.get("cutoff_utc"), "retention_days": h.retention_days,
            "would_delete": dry.get("would_delete", {}), "newest_record_utc": newest,
            "newest_earlier_process_record_utc": prior, "clock_sync": clock_sync_status(),
            "note": "Confirm only after checking the date and time against an independent source. Rows older "
                    "than the cutoff are deleted in the next run; rows inside the window are never deleted."}


def confirm_cutoff(hist, cutoff_utc: str, *, wall_s: float, trusted_s: float | None, actor: str,
                   hold: dict | None) -> dict:
    """Check an administrator's confirmation against the current proposal and record it."""
    prop = retention_proposal(hist, wall_s=wall_s, trusted_s=trusted_s)
    if prop["cutoff_utc"] is None:
        raise LookupError(f"retention policy is {hist.cfg.historian.retention_provenance}; nothing is deleted")
    given = datetime.fromisoformat(cutoff_utc)
    if given.tzinfo is None:
        raise ValueError("the cutoff must include a UTC offset")
    if abs((given - datetime.fromisoformat(prop["cutoff_utc"])).total_seconds()) > PROPOSAL_TOLERANCE_S:
        raise ProposalMismatch(prop)
    hist.event("RETENTION_CLOCK_CONFIRMED", {"confirmed_by": actor, "cutoff_utc": prop["cutoff_utc"],
                                             "wall_utc": prop["wall_utc"], "hold": hold,
                                             "would_delete": prop["would_delete"],
                                             "clock_sync": prop["clock_sync"]}, actor=actor)
    return prop


def _newest_record(hist, exclude_session: str | None) -> str | None:
    """Newest time written by any process (or by earlier ones): snapshots, events, commands and the
    process register, so a history with audit records but no snapshot is covered too."""
    sid = exclude_session or "<none>"
    rows = hist.query(
        "SELECT (SELECT persisted_utc FROM cycles WHERE session_id != ? ORDER BY cycle_pk DESC LIMIT 1), "
        "(SELECT utc FROM events WHERE coalesce(session_id, '') != ? ORDER BY utc DESC LIMIT 1), "
        "(SELECT intent_utc FROM commands WHERE session_id != ? ORDER BY intent_utc DESC LIMIT 1), "
        "(SELECT max(coalesce(ended_utc, started_utc)) FROM process_sessions WHERE session_id != ?)",
        (sid, sid, sid, sid))[0]
    stamps = [r for r in rows if r]
    return max(stamps, key=lambda t: datetime.fromisoformat(t)) if stamps else None


def apply_retention(hist, *, now: datetime | None = None, dry_run: bool = False) -> dict:
    """The rolling prune at an explicit time (tests, measurements). Operators use `retention apply`,
    which first shows the proposal and deletes only with --confirm-cutoff."""
    return prune_expired(hist, now=now, dry_run=dry_run)


class RetentionTask:
    """Runs the rolling prune in the background while the server runs, only while the clock is trusted.

    `tick(alarms)` is called from the thread that owns the alarm engine; it never blocks on deletes.
    `confirm(actor, cutoff_utc)` is the administrator's release of a hold (API or CLI)."""

    def __init__(self, hist, *, wall=time.time, monotonic=time.monotonic):
        self.hist, self._wall, self._mono = hist, wall, monotonic
        self._lock = threading.RLock()
        self.held: dict | None = None
        self.running = False
        self.last_result: dict | None = None
        self.last_error: str | None = None
        self._next_due: float | None = None
        self._anchor: tuple[float, float] | None = None   # (wall, monotonic) while the clock is trusted
        self._started_check_done = False

    def _hold(self, code: str, reason: str, wall: float) -> None:
        self._anchor = None
        if self.held is None:
            self.held = {"code": code, "reason": reason, "since_utc": datetime.fromtimestamp(wall, timezone.utc).isoformat()}
            self.hist.event("RETENTION_HELD", dict(self.held), actor="system")

    def _newest_prior_record(self) -> str | None:
        return _newest_record(self.hist, exclude_session=self.hist.session_id)

    def trusted_now(self) -> float | None:
        """Wall time carried forward on the monotonic clock from the last trusted reading."""
        with self._lock:
            if self._anchor is None:
                return None
            return self._anchor[0] + (self._mono() - self._anchor[1])

    def _check_clock(self) -> None:
        mono, wall = self._mono(), self._wall()
        with self._lock:
            if self.held is not None:
                return
            if not self._started_check_done:
                self._started_check_done = True
                newest = self._newest_prior_record()
                if newest:
                    gap_s = wall - datetime.fromisoformat(newest).timestamp()
                    if gap_s < -CLOCK_TOLERANCE_S:
                        self._hold("CLOCK_BEHIND_STORED", f"the system clock is {_span(-gap_s)} behind the newest "
                                   f"stored record ({newest})", wall)
                    elif gap_s > CONFIRM_AFTER_DOWNTIME_S:
                        self._hold("UNVERIFIED_DOWNTIME", f"the system clock is {_span(gap_s)} after the newest "
                                   f"stored record ({newest}); the date after this downtime cannot be "
                                   "cross-checked against stored history", wall)
                if self.held is None:
                    self._anchor = (wall, mono)
                return
            if self._anchor is None:
                self._anchor = (wall, mono)
                return
            step = wall - (self._anchor[0] + (mono - self._anchor[1]))
            if abs(step) > CLOCK_TOLERANCE_S:
                self._hold("CLOCK_STEP_FORWARD" if step > 0 else "CLOCK_STEP_BACKWARD",
                           f"the wall clock stepped {'forward' if step > 0 else 'backward'} by {_span(abs(step))} "
                           "against the monotonic clock while running", wall)
            else:
                self._anchor = (wall, mono)   # follow small corrections

    def proposal(self) -> dict:
        return retention_proposal(self.hist, wall_s=self._wall(), trusted_s=self.trusted_now()) | {
            "held": self.held, "policy": self.hist.cfg.historian.retention_provenance}

    def confirm(self, actor: str, cutoff_utc: str) -> dict:
        """Release a hold after an administrator checked the date: `cutoff_utc` must echo the proposal
        shown to them (within PROPOSAL_TOLERANCE_S), otherwise ProposalMismatch carries the new one."""
        with self._lock:
            if self.held is None:
                raise LookupError("retention is not held")
            prop = confirm_cutoff(self.hist, cutoff_utc, wall_s=self._wall(), trusted_s=None, actor=actor,
                                  hold=self.held)
            self.held = None
            self._anchor = (self._wall(), self._mono())
            self._started_check_done = True
            self._next_due = None
            return prop

    def tick(self, alarms, sim_s: float | None = None) -> None:
        h = self.hist.cfg.historian
        if h.retention_provenance != "APPROVED":
            return
        self._check_clock()
        held = self.held
        if held is not None:
            alarms.raise_or_update("RETENTION_HELD", "historian", severity="WARNING", sim_s=sim_s,
                                   message=f"rolling retention held: {held['reason']}. Nothing is deleted while "
                                           "held. An administrator must check the date and confirm the proposed "
                                           "cutoff (Settings, or retention apply --confirm-cutoff with the server "
                                           "stopped); acknowledging this alarm does not resume deletion.")
            return
        alarms.clear("RETENTION_HELD", "historian")
        now = self._mono()
        if self.running or (self._next_due is not None and now < self._next_due):
            return
        self._next_due = now + h.retention_check_interval_s
        self.running = True
        basis = min(self._wall(), self.trusted_now() or self._wall())
        threading.Thread(target=self._run, args=(datetime.fromtimestamp(basis, tz=timezone.utc),),
                         name="historian-retention", daemon=True).start()

    def _run(self, at: datetime) -> None:
        try:
            self.last_result = prune_expired(self.hist, now=at, max_chunks=PRUNE_MAX_TRANSACTIONS_PER_RUN,
                                             wal_limit_bytes=self.hist.cfg.historian.wal_warning_bytes) | {
                "run_utc": at.isoformat()}
            self.last_error = None
        except Exception as e:   # visible in status; the next run retries
            self.last_error = f"{at.isoformat()} {e!r}"
        finally:
            self.running = False

    def view(self) -> dict:
        return {"held": self.held, "running": self.running, "last_result": self.last_result,
                "last_error": self.last_error, "policy": self.hist.cfg.historian.retention_provenance,
                "clock_trusted": self._anchor is not None, "clock_sync": clock_sync_status()}


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
    if st["wal_over_warning"]:
        why.append(f"WAL {st['wal_bytes'] / 2**20:.0f} MiB above {st['wal_warning_bytes'] / 2**20:.0f} MiB (a long "
                   "reader such as a backup or export is holding back checkpoints; retention pruning waits)")
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


def evaluate_recorder_alarm(recorder, alarms, sim_s: float | None = None) -> None:
    """HISTORY_CLOCK_BEHIND (WARNING) while the wall clock is behind the newest stored snapshot label."""
    b = recorder.behind
    if b is None:
        alarms.clear("HISTORY_CLOCK_BEHIND", "historian")
        return
    alarms.raise_or_update("HISTORY_CLOCK_BEHIND", "historian", severity="WARNING", sim_s=sim_s,
                           message=f"system clock is {b['behind_s']:.0f} s behind the newest stored snapshot "
                                   f"({b['newest_label_utc']}): periodic snapshots are held until the clock passes "
                                   "it (no label is written twice); events, commands and alarms are still recorded. "
                                   "Check the date and time.")


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
