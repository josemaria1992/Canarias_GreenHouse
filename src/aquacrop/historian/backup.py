"""Consistent historian backups (never a raw copy of a live WAL database).

Default format `aquacrop-sql-gz-1`. How one snapshot is shared: the backup opens its own connection and
starts one read transaction (`BEGIN`; WAL readers see the database as of their first read). Inside that
transaction it reads the table list, every table's row count, the schema objects, the schema version,
the history range and the row fingerprints that go into the manifest, and then streams the SQL dump of
the same snapshot (`iterdump`: schema objects, every row, indexes) straight into gzip. The writer keeps
committing meanwhile; none of that is visible to the transaction, so the manifest and the dump describe
the same database state. The dump's INSERT count per table must equal the manifest count.

Publication is atomic and happens only after verification: the stream is written to `*.partial`,
re-read and verified against the in-memory manifest (checksum, gzip CRC, every statement parsed,
closing COMMIT, row count per table); only then is it renamed into place and its manifest written
through a temporary file and renamed. A backup is listed only once its manifest exists. A failed or
aborted backup leaves no partial file and no manifest, and the earlier backups are untouched; old
backups are rotated out only after the new one is published.

Space. No temporary full-size copy of the database is written: the only extra file is the compressed
stream itself (measured about 16x smaller than the database; ADR-0008). Before starting, the backup is
refused if a conservative estimate of its size would take free disk below the reserve; while writing,
free space, the source WAL size and the elapsed time are checked every BACKUP_CHECK_S, and the backup
aborts cleanly if free disk falls below the reserve, the WAL grows past `wal_abort_bytes` (this long
read transaction is what holds the WAL back) or `max_seconds` pass.

Full verification is a separate, heavier operation: `verify_backup(full=True)` restores into a scratch
database (it needs room for a whole database plus headroom, checked before it starts and while it
runs), then checks `integrity_check`, `foreign_key_check`, the schema version and schema objects, the
row count of every table, the first/last-row fingerprints and the application's own startup and
queries, and can write a restore-drill report. A valid checksum alone is not a proven restore.

Older single-file `.sqlite3` backups (SQLite backup API) stay verifiable and restorable. Pre-migration
safety copies still use `backup_to` (the same API), after a free-space preflight.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zlib
from pathlib import Path

from .. import __version__

FORMAT_SQL_GZ = "aquacrop-sql-gz-1"
_INSERT = re.compile(r'^INSERT INTO "([^"]+)" VALUES')
_STAMPED = re.compile(r"^aquacrop-(?P<stamp>.+?)\.(?P<ext>sql\.gz|sqlite3)$")
BACKUP_CHECK_S = 2.0              # free space / WAL / time checks while writing or restoring
FINGERPRINT_ROWS = 50             # first and last rows of each table hashed into the manifest
STALE_PARTIAL_S = 6 * 3600        # a *.partial this old belongs to a backup that died; removed
ESTIMATE_MARGIN = 1.5             # next backup estimate = previous size x margin (ratio not guaranteed)
ESTIMATE_DB_FRACTION = 0.25       # ... and never less than a quarter of the database's used size
SCRATCH_FACTOR = 1.2              # a full restore needs the source size plus 20 %


class BackupRefused(RuntimeError):
    """Not started: the preflight says it would not fit. Nothing was written."""


class BackupAborted(RuntimeError):
    """Stopped while writing (space, WAL growth or time). The partial file was removed."""


class BackupFailed(RuntimeError):
    """Written but not published: it did not match its snapshot or failed verification."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)   # backups hold password hashes; the state directory is 0700 as well
    except OSError:
        pass


def _fsync_dir(d: Path) -> None:
    if os.name != "posix":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".partial")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _private(tmp)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def manifest_path(backup: Path) -> Path:
    m = _STAMPED.match(backup.name)
    return backup.with_name(f"aquacrop-{m['stamp']}.manifest.json") if m else backup.with_suffix(".manifest.json")


def _same_device(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return True   # unknown: assume it shares the live database's disk (keep the reserve)


def _used_bytes(con: sqlite3.Connection) -> int:
    page = con.execute("PRAGMA page_size").fetchone()[0]
    return (con.execute("PRAGMA page_count").fetchone()[0] - con.execute("PRAGMA freelist_count").fetchone()[0]) * page


def backup_to(con: sqlite3.Connection, dest: str | Path, *, reserve_bytes: int = 0) -> Path:
    """Full single-file copy through the SQLite backup API (pre-migration and legacy-import safety
    copies). It needs room for the whole database: refused if that would take free disk below
    `reserve_bytes`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    need = int(_used_bytes(con) * SCRATCH_FACTOR)
    free = _free_bytes(dest.parent)
    if free - need < reserve_bytes:
        raise BackupRefused(f"a full safety copy needs about {need / 2**30:.2f} GiB; free disk {free / 2**30:.2f} GiB "
                            f"would fall below the reserve {reserve_bytes / 2**30:.2f} GiB")
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(str(tmp))
    try:
        con.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
    except BaseException:
        out.close()
        tmp.unlink(missing_ok=True)
        raise
    out.close()
    os.replace(tmp, dest)
    _private(dest)
    _fsync_dir(dest.parent)
    return dest


def list_backups(destination: str | Path) -> list[Path]:
    """Published backups in a directory, oldest first. A compressed backup counts only once its manifest
    exists (the manifest is written last); partial files and manifests are excluded."""
    d = Path(destination)
    found = []
    for p in d.glob("aquacrop-*"):
        m = _STAMPED.match(p.name)
        if not m:
            continue
        if m["ext"] == "sql.gz" and not manifest_path(p).exists():
            continue
        found.append((m["stamp"], p))
    return [p for _, p in sorted(found)]


def _cleanup_stale(destination: Path, now: float) -> list[str]:
    """Remove leftovers of backups that died (power loss): old *.partial files, and compressed files
    whose manifest was never written."""
    gone = []
    for p in destination.glob("aquacrop-*"):
        try:
            stale = now - p.stat().st_mtime > STALE_PARTIAL_S
        except OSError:
            continue
        orphan = p.name.endswith(".sql.gz") and not manifest_path(p).exists()
        if stale and (p.name.endswith(".partial") or orphan):
            p.unlink(missing_ok=True)
            gone.append(p.name)
    return gone


def estimate_backup_bytes(db_path: Path, destination: Path) -> dict:
    """Conservative size of the next compressed backup: the larger of the previous one x margin and a
    quarter of the database's used size. The compression ratio depends on the data and is not
    guaranteed; the free-space checks while writing are what actually protect the disk."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        used = _used_bytes(con)
    finally:
        con.close()
    prev = [p for p in list_backups(destination) if p.name.endswith(".sql.gz")]
    prev_bytes = prev[-1].stat().st_size if prev else None
    est = max(int((prev_bytes or 0) * ESTIMATE_MARGIN), int(used * ESTIMATE_DB_FRACTION))
    return {"estimate_bytes": est, "previous_bytes": prev_bytes, "db_used_bytes": used}


def _fingerprints(con: sqlite3.Connection, tables: list[str]) -> dict[str, str]:
    """SHA-256 over the first and last FINGERPRINT_ROWS rows (rowid order, all columns) of each table:
    representative values that must come back unchanged from a restore."""
    out = {}
    for t in tables:
        h = hashlib.sha256()
        for order in ("ASC", "DESC"):
            for row in con.execute(f'SELECT * FROM "{t}" ORDER BY rowid {order} LIMIT {FINGERPRINT_ROWS}'):
                h.update(json.dumps(row, default=repr, ensure_ascii=False).encode())
        out[t] = h.hexdigest()
    return out


def _schema_objects(con: sqlite3.Connection) -> list[list[str]]:
    rows = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
                       "AND sql IS NOT NULL ORDER BY type, name").fetchall()
    return [list(r) for r in rows]


def _schema_digest(objects: list[list[str]]) -> str:
    return hashlib.sha256(json.dumps(objects).encode()).hexdigest()


class _Watch:
    """In-flight guard for long writes: free disk, the source WAL and elapsed time."""

    def __init__(self, where: Path, reserve_bytes: int, *, wal: Path | None = None, wal_abort_bytes: int | None = None,
                 max_seconds: float | None = None, clock=time.monotonic):
        self.where, self.reserve, self.wal, self.wal_abort = where, reserve_bytes, wal, wal_abort_bytes
        self.max_seconds, self._clock = max_seconds, clock
        self.t0 = self._next = clock()
        self.min_free: int | None = None
        self.max_wal = 0
        self.checks = 0

    def check(self, force: bool = False) -> None:
        now = self._clock()
        if not force and now < self._next:
            return
        self._next = now + BACKUP_CHECK_S
        self.checks += 1
        free = _free_bytes(self.where)
        self.min_free = free if self.min_free is None else min(self.min_free, free)
        if free < self.reserve:
            raise BackupAborted(f"free disk {free / 2**30:.2f} GiB fell below the reserve "
                                f"{self.reserve / 2**30:.2f} GiB while writing")
        if self.wal is not None:
            w = self.wal.stat().st_size if self.wal.exists() else 0
            self.max_wal = max(self.max_wal, w)
            if self.wal_abort is not None and w > self.wal_abort:
                raise BackupAborted(f"the database WAL reached {w / 2**20:.0f} MiB (limit "
                                    f"{self.wal_abort / 2**20:.0f} MiB) while this backup held its read snapshot")
        if self.max_seconds is not None and now - self.t0 > self.max_seconds:
            raise BackupAborted(f"backup exceeded {self.max_seconds:.0f} s")

    def view(self) -> dict:
        return {"seconds": round(self._clock() - self.t0, 3), "checks": self.checks, "min_free_bytes": self.min_free,
                "max_source_wal_bytes": self.max_wal}


def create_backup(db_path: str | Path, destination: str | Path, *, config_id: str | None = None,
                  config_version: str | None = None, keep: int | None = None, reserve_bytes: int = 0,
                  verify: bool = True, wal_abort_bytes: int | None = None, max_seconds: float | None = None) -> dict:
    """Write, verify and only then publish `<destination>/aquacrop-<ts>.sql.gz` and its manifest; then
    (with `keep`) remove the oldest compressed backups beyond `keep`. Raises BackupRefused (nothing
    written), BackupAborted or BackupFailed (partial removed, earlier backups untouched)."""
    from .schema import connect, utc_iso
    db_path, destination = Path(db_path), Path(destination)
    if keep is not None and keep < 1:
        raise ValueError("keep must be at least 1")
    destination.mkdir(parents=True, exist_ok=True)
    cleaned = _cleanup_stale(destination, time.time())
    est = estimate_backup_bytes(db_path, destination)
    free = _free_bytes(destination)
    if free - est["estimate_bytes"] < reserve_bytes:
        raise BackupRefused(f"free disk {free / 2**30:.2f} GiB minus the estimated backup "
                            f"{est['estimate_bytes'] / 2**30:.2f} GiB would fall below the reserve "
                            f"{reserve_bytes / 2**30:.2f} GiB; free space or move old backups off the device")
    stamp = utc_iso().replace(":", "").replace("+0000", "Z")
    out = destination / f"aquacrop-{stamp}.sql.gz"
    tmp = out.with_name(out.name + ".partial")
    watch = _Watch(destination, reserve_bytes, wal=Path(str(db_path) + "-wal"), wal_abort_bytes=wal_abort_bytes,
                   max_seconds=max_seconds)
    con = connect(db_path)
    dumped: dict[str, int] = {}
    try:
        con.execute("BEGIN")   # one read transaction: the manifest facts and the dump share its snapshot
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                            "'sqlite_%' ORDER BY name")]
        counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        ver_row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() \
            if "schema_meta" in tables else None
        user_version = con.execute("PRAGMA user_version").fetchone()[0]
        objects = _schema_objects(con)
        rng = (con.execute("SELECT min(acquired_utc) FROM samples").fetchone()[0],
               con.execute("SELECT max(acquired_utc) FROM samples").fetchone()[0]) \
            if "samples" in tables else (None, None)
        prints = _fingerprints(con, tables)
        watch.check(force=True)
        with gzip.open(tmp, "wt", compresslevel=6, encoding="utf-8", newline="") as g:   # no newline translation
            for line in con.iterdump():
                if line.startswith("INSERT INTO "):
                    m = _INSERT.match(line)
                    if m:
                        dumped[m[1]] = dumped.get(m[1], 0) + 1
                g.write(line)
                g.write("\n")
                watch.check()
        con.execute("COMMIT")
        watch.check(force=True)
        if {t: dumped.get(t, 0) for t in tables} != counts:
            raise BackupFailed(f"dump row counts {dumped} differ from the snapshot {counts}")
        with open(tmp, "r+b") as f:                       # Windows needs write access to flush to disk
            os.fsync(f.fileno())
        manifest = {"format": FORMAT_SQL_GZ, "file": out.name, "sha256": _sha256(tmp), "bytes": tmp.stat().st_size,
                    "created_utc": stamp, "schema_version": int(ver_row[0]) if ver_row else None,
                    "user_version": user_version, "schema_objects_sha256": _schema_digest(objects),
                    "schema_objects": len(objects), "software_version": __version__,
                    "source_db": str(db_path.resolve()), "source_db_bytes": db_path.stat().st_size,
                    "source_db_used_bytes": est["db_used_bytes"], "config_id": config_id,
                    "config_version": config_version, "row_counts": counts, "fingerprints": prints,
                    "oldest_sample_utc": rng[0], "newest_sample_utc": rng[1],
                    "snapshot": "manifest facts and dump read in one read transaction"}
        verification = None
        if verify:
            v = _verify_sql_gz(tmp, manifest)
            verification = {k: v[k] for k in ("ok", "checksum_ok", "integrity", "counts")}
            if not v["ok"]:
                raise BackupFailed(f"new backup failed verification ({v['integrity']}); not published, earlier "
                                   "backups kept")
    except BaseException:
        try:
            con.close()
        finally:
            tmp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp, out)                                   # publish the data, then its manifest
    _private(out)
    _fsync_dir(destination)
    _write_atomic(manifest_path(out), json.dumps(manifest, indent=2))
    result = manifest | {"path": str(out), "estimate": est, "watch": watch.view(), "cleaned_stale": cleaned}
    if verification is not None:
        result["verification"] = verification
    if keep is not None:
        result["removed"] = rotate_backups(destination, keep)
    return result


def rotate_backups(destination: str | Path, keep: int) -> list[str]:
    """Keep the newest `keep` published compressed backups; remove older ones with their manifests.
    Called only after the newest backup was published. Single-file and pre-migration copies are left
    alone."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    gz = [p for p in list_backups(destination) if p.name.endswith(".sql.gz")]
    removed = []
    for p in gz[:-keep]:
        manifest_path(p).unlink(missing_ok=True)   # unlisted first: a half-removed backup is never "valid"
        p.unlink(missing_ok=True)
        removed.append(p.name)
    return removed


def _statements(path: Path):
    buf = ""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as g:   # CR/LF inside text values stay as stored
        for line in g:
            buf += line
            if sqlite3.complete_statement(buf):
                yield buf.strip()
                buf = ""
    if buf.strip():
        yield buf.strip()   # incomplete trailing statement: verification will reject it


def _scan_sql_gz(path: Path) -> dict:
    counts: dict[str, int] = {}
    first = last = None
    for st in _statements(path):
        if first is None and not st.upper().startswith("PRAGMA"):
            first = st
        last = st
        m = _INSERT.match(st)
        if m:
            counts[m[1]] = counts.get(m[1], 0) + 1
    return {"counts": counts, "complete": first == "BEGIN TRANSACTION;" and last == "COMMIT;"}


def _load_sql_gz(path: Path, target: Path, *, user_version: int | None = None, watch: _Watch | None = None) -> None:
    con = sqlite3.connect(str(target), isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=OFF")   # scratch file until verified and renamed
        con.execute("PRAGMA synchronous=OFF")
        con.execute("BEGIN")
        for st in _statements(path):
            if st in ("BEGIN TRANSACTION;", "COMMIT;"):
                continue
            con.execute(st)
            if watch is not None:
                watch.check()
        if user_version:
            con.execute(f"PRAGMA user_version={int(user_version)}")
        con.execute("COMMIT")
        con.execute("PRAGMA journal_mode=DELETE")
    finally:
        con.close()


def _db_facts(db: Path, *, deep: bool = False) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        has_meta = con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_meta'").fetchone()
        row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() if has_meta else None
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                            "'sqlite_%' ORDER BY name")]
        counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        facts = {"integrity": integrity, "schema_version": int(row[0]) if row else None, "counts": counts}
        if deep:
            objects = _schema_objects(con)
            facts |= {"foreign_key_violations": len(con.execute("PRAGMA foreign_key_check").fetchall()),
                      "user_version": con.execute("PRAGMA user_version").fetchone()[0],
                      "schema_objects_sha256": _schema_digest(objects), "schema_objects": len(objects),
                      "fingerprints": _fingerprints(con, tables)}
    finally:
        con.close()
    return facts


def _app_smoke(db: Path, cfg=None) -> dict:
    """The application's own startup path and queries against a restored copy: schema check/migration,
    the queries behind trends, events, alarms and the snapshot high-water mark; with a configuration,
    a full historian start and stop and the capacity report."""
    from .schema import CURRENT_VERSION, connect, migrate, schema_version
    from .writer import NEWEST_WALL_CYCLE_SQL, Historian
    con = connect(db)
    try:
        ver = schema_version(con)
        if ver > CURRENT_VERSION:
            return {"ok": False, "error": f"schema {ver} is newer than this software ({CURRENT_VERSION})"}
        applied = migrate(con, None)
        pid = con.execute("SELECT point_id FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        trend = con.execute("SELECT s.acquired_utc, s.value, s.quality, c.cadence_basis FROM samples s JOIN cycles c "
                            "ON c.cycle_pk=s.cycle_pk WHERE s.point_id=? ORDER BY s.acquired_utc DESC LIMIT 100",
                            (pid[0] if pid else "",)).fetchall()
        events = con.execute("SELECT id, utc, kind, payload_json FROM events ORDER BY id DESC LIMIT 100").fetchall()
        for e in events:
            json.loads(e[3])
        alarms = [json.loads(r[0]) for r in con.execute("SELECT json FROM alarm_occurrences WHERE closed=0")]
        con.execute(NEWEST_WALL_CYCLE_SQL).fetchone()
        out = {"ok": True, "schema_version": ver, "migrations_applied": applied, "trend_rows": len(trend),
               "events_read": len(events), "open_alarms": len(alarms)}
    finally:
        con.close()
    if cfg is not None:
        h = Historian(db, cfg, "restore_drill")
        try:
            st = h.storage()
            out |= {"historian_started": True, "capacity_report_db_bytes": st["db_bytes"],
                    "open_alarms_loaded": len(h.load_open_alarms())}
        finally:
            h.close()
    return out


def _verify_sql_gz(path: Path, man: dict, *, full: bool = False, scratch_dir: str | Path | None = None,
                   reserve_bytes: int = 0, cfg=None) -> dict:
    result: dict = {"path": str(path), "ok": False, "manifest": man}
    result["checksum_ok"] = man.get("sha256") == _sha256(path)
    try:
        scan = _scan_sql_gz(path)
    except (OSError, EOFError, UnicodeDecodeError, zlib.error) as e:   # corrupt or truncated gzip stream
        result["integrity"] = f"unreadable: {e!r}"
        return result
    result["schema_version"] = man.get("schema_version")
    result["counts"] = {t: scan["counts"].get(t, 0) for t in man["row_counts"]}
    problems = []
    if not result["checksum_ok"]:
        problems.append("checksum differs from the manifest")
    if not scan["complete"]:
        problems.append("dump incomplete (missing BEGIN/COMMIT)")
    if result["counts"] != man["row_counts"]:
        problems.append(f"row counts {result['counts']} differ from manifest {man['row_counts']}")
    if full and not problems:
        full_res = _full_restore_check(path, man, scratch_dir=scratch_dir, reserve_bytes=reserve_bytes, cfg=cfg)
        result["full"] = full_res
        problems += full_res["problems"]
    result["integrity"] = "; ".join(problems) if problems else (
        "ok (restored: integrity, foreign keys, schema, counts, fingerprints, application smoke test)" if full
        else "ok (stream, statements and row counts)")
    result["ok"] = not problems
    return result


def _full_restore_check(path: Path, man: dict, *, scratch_dir, reserve_bytes: int, cfg) -> dict:
    scratch = Path(scratch_dir) if scratch_dir else Path(tempfile.gettempdir())
    scratch.mkdir(parents=True, exist_ok=True)
    need = int((man.get("source_db_used_bytes") or man.get("source_db_bytes") or 0) * SCRATCH_FACTOR)
    source = Path(man.get("source_db") or "")
    keep = reserve_bytes if (not source.exists() or _same_device(scratch, source.parent)) else 0
    free = _free_bytes(scratch)
    out: dict = {"scratch_dir": str(scratch), "needed_bytes": need, "free_bytes_before": free,
                 "reserve_kept_bytes": keep, "problems": []}
    if free - need < keep:
        out["problems"].append(f"not enough scratch space for a full restore: needs about {need / 2**30:.2f} GiB, "
                               f"{free / 2**30:.2f} GiB free, {keep / 2**30:.2f} GiB must stay free; use a "
                               "separate approved scratch destination")
        return out
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(dir=scratch) as d:
        db = Path(d) / "verify.sqlite3"
        watch = _Watch(scratch, keep)
        try:
            _load_sql_gz(path, db, user_version=man.get("user_version"), watch=watch)
        except BackupAborted as e:
            out["problems"].append(f"restore aborted: {e}")
            return out
        facts = _db_facts(db, deep=True)
        out["restore_seconds"] = round(time.monotonic() - t0, 3)
        out["restored_bytes"] = db.stat().st_size
        out["min_free_bytes"] = watch.min_free
        p = out["problems"]
        if facts["integrity"] != "ok":
            p.append(f"integrity_check: {facts['integrity']}")
        if facts["foreign_key_violations"]:
            p.append(f"foreign_key_check: {facts['foreign_key_violations']} violations")
        if facts["schema_version"] != man.get("schema_version"):
            p.append(f"schema version {facts['schema_version']} != manifest {man.get('schema_version')}")
        if man.get("schema_objects_sha256") and facts["schema_objects_sha256"] != man["schema_objects_sha256"]:
            p.append("schema objects (tables, indexes) differ from the source")
        if man.get("user_version") is not None and facts["user_version"] != man["user_version"]:
            p.append(f"user_version {facts['user_version']} != manifest {man['user_version']}")
        if facts["counts"] != man["row_counts"]:
            p.append(f"restored row counts {facts['counts']} differ from manifest")
        if man.get("fingerprints"):
            bad = [t for t, f in man["fingerprints"].items() if facts["fingerprints"].get(t) != f]
            if bad:
                p.append(f"first/last-row fingerprints differ for {bad}")
        try:
            out["smoke"] = _app_smoke(db, cfg)
            if not out["smoke"]["ok"]:
                p.append(f"application smoke test: {out['smoke'].get('error')}")
        except Exception as e:   # the application could not start on or query the restored copy
            out["smoke"] = {"ok": False, "error": repr(e)}
            p.append(f"application smoke test: {e!r}")
        out["facts"] = {k: facts[k] for k in ("integrity", "foreign_key_violations", "schema_version", "user_version",
                                               "schema_objects")}
    return out


def verify_backup(path: str | Path, *, full: bool = False, scratch_dir: str | Path | None = None,
                  reserve_bytes: int = 0, cfg=None, report_dir: str | Path | None = None) -> dict:
    """Never touches the live database. A directory means its newest published backup. `full=True` is
    the restore drill (see the module notes); `report_dir` keeps a JSON report of it."""
    path = Path(path)
    if path.is_dir():
        cands = list_backups(path)
        if not cands:
            raise FileNotFoundError(f"no backups in {path}")
        path = cands[-1]
    mp = manifest_path(path)
    man = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    if path.name.endswith(".sql.gz"):
        if man is None:
            result = {"path": str(path), "ok": False, "manifest": None, "checksum_ok": None,
                      "integrity": "manifest missing"}
        else:
            result = _verify_sql_gz(path, man, full=full, scratch_dir=scratch_dir, reserve_bytes=reserve_bytes,
                                    cfg=cfg)
    else:
        result = {"path": str(path), "manifest": man}
        result["checksum_ok"] = (man.get("sha256") == _sha256(path)) if man else None
        result |= _db_facts(path)
        result["ok"] = result["integrity"] == "ok" and result["checksum_ok"] is not False
    if full and report_dir is not None:
        from .schema import utc_iso
        rd = Path(report_dir)
        rd.mkdir(parents=True, exist_ok=True)
        report = {"kind": "restore-drill", "utc": utc_iso(), "software_version": __version__, "backup": str(path),
                  "ok": result["ok"], "integrity": result.get("integrity"), "full": result.get("full"),
                  "row_counts": (man or {}).get("row_counts")}
        name = f"restore-drill-{report['utc'].replace(':', '')}.json"
        _write_atomic(rd / name, json.dumps(report, indent=2, default=str))
        result["report"] = str(rd / name)
    return result


def latest_drill(report_dir: str | Path) -> dict | None:
    """The newest restore-drill report in `report_dir` (for status pages), or None."""
    rd = Path(report_dir)
    reps = sorted(rd.glob("restore-drill-*.json")) if rd.exists() else []
    if not reps:
        return None
    try:
        r = json.loads(reps[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"utc": r.get("utc"), "ok": r.get("ok"), "backup": r.get("backup"), "integrity": r.get("integrity")}


def restore_backup(backup: str | Path, target_db: str | Path, *, allow_existing: bool = False,
                   reserve_bytes: int = 0) -> dict:
    """Restore into a *separate* target instance. Refuses to overwrite an existing DB unless explicitly allowed
    (the CLI only allows that behind a maintenance gate with the server stopped)."""
    v = verify_backup(backup)
    if not v["ok"]:
        raise RuntimeError(f"backup failed verification: {v}")
    target = Path(target_db)
    if target.exists() and not allow_existing:
        raise FileExistsError(f"{target} exists; restore to a new instance first")
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        side = Path(str(target) + suffix)
        if side.exists():
            if not allow_existing:
                raise FileExistsError(side)
            side.unlink()
    man = v["manifest"] or {}
    need = int((man.get("source_db_used_bytes") or man.get("source_db_bytes") or 0) * SCRATCH_FACTOR)
    free = _free_bytes(target.parent)
    if free - need < reserve_bytes:
        raise BackupRefused(f"restore needs about {need / 2**30:.2f} GiB; free disk {free / 2**30:.2f} GiB would "
                            f"fall below the reserve {reserve_bytes / 2**30:.2f} GiB")
    tmp = target.with_suffix(".restoring")
    tmp.unlink(missing_ok=True)
    src = Path(v["path"])
    try:
        if src.name.endswith(".sql.gz"):
            _load_sql_gz(src, tmp, user_version=man.get("user_version"), watch=_Watch(target.parent, reserve_bytes))
            facts = _db_facts(tmp)
            if facts["integrity"] != "ok" or facts["counts"] != man["row_counts"]:
                raise RuntimeError(f"restored copy failed checks: {facts}")
        else:
            shutil.copyfile(src, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, target)
    _private(target)
    return {"restored_from": str(src), "target": str(target), "verification": v}
