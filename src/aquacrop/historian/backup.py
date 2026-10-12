"""Consistent historian backups (never a raw copy of a live WAL database).

Default format `aquacrop-sql-gz-1`: one read transaction gives a consistent snapshot while the server
keeps writing, and the SQL dump of that snapshot is streamed straight into gzip. No temporary full-size
copy is written, so a one-year database (about 8 GiB) and its backups fit on the 32 GB eMMC (measured
about 16x smaller than the database; ADR-0008). The manifest records the SHA-256, the row count of
every table in the snapshot, the schema and software version and the history range.

Verification re-checks the checksum, decompresses the whole stream (gzip CRC), parses every statement,
recounts rows per table against the manifest and requires the closing COMMIT. `full=True` additionally
restores into a scratch database and runs `integrity_check` (needs space for one database).

Older single-file `.sqlite3` backups (SQLite backup API) stay verifiable and restorable. Pre-migration
safety copies still use `backup_to` (the same API).
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
import zlib
from pathlib import Path

from .. import __version__

FORMAT_SQL_GZ = "aquacrop-sql-gz-1"
_INSERT = re.compile(r'^INSERT INTO "([^"]+)" VALUES')
_STAMPED = re.compile(r"^aquacrop-(?P<stamp>.+?)\.(?P<ext>sql\.gz|sqlite3)$")


class BackupRefused(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)   # backups hold password hashes; the state directory is 0700 as well
    except OSError:
        pass


def manifest_path(backup: Path) -> Path:
    m = _STAMPED.match(backup.name)
    return backup.with_name(f"aquacrop-{m['stamp']}.manifest.json") if m else backup.with_suffix(".manifest.json")


def backup_to(con: sqlite3.Connection, dest: str | Path) -> Path:
    """Full single-file copy through the SQLite backup API (pre-migration and legacy-import safety copies)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(str(tmp))
    try:
        con.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
    finally:
        out.close()
    tmp.replace(dest)
    _private(dest)
    return dest


def list_backups(destination: str | Path) -> list[Path]:
    """Backups in a directory, oldest first (both formats; partial files and manifests excluded)."""
    d = Path(destination)
    found = [(m["stamp"], p) for p in d.glob("aquacrop-*") if (m := _STAMPED.match(p.name))]
    return [p for _, p in sorted(found)]


def _estimate_bytes(db_path: Path, destination: Path) -> int:
    prev = [p for p in list_backups(destination) if p.name.endswith(".sql.gz")]
    if prev:
        return int(prev[-1].stat().st_size * 1.25)
    return int(db_path.stat().st_size / 4)   # no previous backup: measured ratio ~16x, assume 4x


def create_backup(db_path: str | Path, destination: str | Path, *, config_id: str | None = None,
                  config_version: str | None = None, keep: int | None = None, reserve_bytes: int = 0,
                  verify: bool = True) -> dict:
    """Write `<destination>/aquacrop-<ts>.sql.gz` and its manifest, verify it, then (with `keep`) delete
    the oldest compressed backups beyond `keep`. Refuses to start if the backup could take free disk
    below `reserve_bytes`."""
    from .schema import connect, utc_iso
    db_path, destination = Path(db_path), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    need = _estimate_bytes(db_path, destination)
    free = shutil.disk_usage(destination).free
    if free - need < reserve_bytes:
        raise BackupRefused(f"free disk {free / 2**30:.2f} GiB minus the estimated backup {need / 2**30:.2f} GiB "
                            f"would fall below the reserve {reserve_bytes / 2**30:.2f} GiB; free space or move "
                            "old backups off the device")
    stamp = utc_iso().replace(":", "").replace("+0000", "Z")
    out = destination / f"aquacrop-{stamp}.sql.gz"
    tmp = out.with_name(out.name + ".partial")
    con = connect(db_path)
    dumped: dict[str, int] = {}
    try:
        con.execute("BEGIN")   # read transaction: everything below sees one snapshot
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                            "'sqlite_%' ORDER BY name")]
        counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        ver_row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() \
            if "schema_meta" in tables else None
        rng = con.execute("SELECT min(acquired_utc), max(acquired_utc) FROM samples").fetchone() \
            if "samples" in tables else (None, None)
        with gzip.open(tmp, "wt", compresslevel=6, encoding="utf-8", newline="") as g:   # no newline translation
            for line in con.iterdump():
                if line.startswith("INSERT INTO "):
                    m = _INSERT.match(line)
                    if m:
                        dumped[m[1]] = dumped.get(m[1], 0) + 1
                g.write(line)
                g.write("\n")
        con.execute("COMMIT")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        con.close()
    if {t: dumped.get(t, 0) for t in tables} != counts:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"dump row counts {dumped} differ from the snapshot {counts}")
    tmp.replace(out)
    _private(out)
    manifest = {"format": FORMAT_SQL_GZ, "file": out.name, "sha256": _sha256(out), "bytes": out.stat().st_size,
                "created_utc": stamp, "schema_version": int(ver_row[0]) if ver_row else None,
                "software_version": __version__, "source_db": str(db_path.resolve()),
                "source_db_bytes": db_path.stat().st_size, "config_id": config_id, "config_version": config_version,
                "row_counts": counts, "oldest_sample_utc": rng[0], "newest_sample_utc": rng[1]}
    mp = manifest_path(out)
    mp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _private(mp)
    result = manifest | {"path": str(out)}
    if verify:
        v = verify_backup(out)
        result["verification"] = {k: v[k] for k in ("ok", "checksum_ok", "integrity", "counts")}
        if not v["ok"]:
            raise RuntimeError(f"new backup failed verification: {v}")
    if keep is not None:
        result["removed"] = rotate_backups(destination, keep)
    return result


def rotate_backups(destination: str | Path, keep: int) -> list[str]:
    """Keep the newest `keep` compressed backups; remove older ones with their manifests. Called only
    after the newest backup verified. Single-file and pre-migration copies are left alone."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    gz = [p for p in list_backups(destination) if p.name.endswith(".sql.gz")]
    removed = []
    for p in gz[:-keep]:
        manifest_path(p).unlink(missing_ok=True)
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


def _load_sql_gz(path: Path, target: Path) -> None:
    con = sqlite3.connect(str(target), isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=OFF")   # scratch file until verified and renamed
        con.execute("PRAGMA synchronous=OFF")
        con.execute("BEGIN")
        for st in _statements(path):
            if st in ("BEGIN TRANSACTION;", "COMMIT;"):
                continue
            con.execute(st)
        con.execute("COMMIT")
        con.execute("PRAGMA journal_mode=DELETE")
    finally:
        con.close()


def _db_facts(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                            "'sqlite_%'")]
        counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()
    return {"integrity": integrity, "schema_version": int(row[0]) if row else None, "counts": counts}


def verify_backup(path: str | Path, *, full: bool = False, scratch_dir: str | Path | None = None) -> dict:
    """Never touches the live database. A directory means its newest backup."""
    path = Path(path)
    if path.is_dir():
        cands = list_backups(path)
        if not cands:
            raise FileNotFoundError(f"no backups in {path}")
        path = cands[-1]
    mp = manifest_path(path)
    result: dict = {"path": str(path), "ok": False}
    man = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    result["manifest"] = man
    result["checksum_ok"] = (man.get("sha256") == _sha256(path)) if man else None
    if path.name.endswith(".sql.gz"):
        if man is None:
            result["integrity"] = "manifest missing"
            return result
        try:
            scan = _scan_sql_gz(path)
        except (OSError, EOFError, UnicodeDecodeError, zlib.error) as e:   # corrupt or truncated gzip stream
            result["integrity"] = f"unreadable: {e!r}"
            return result
        result["schema_version"] = man.get("schema_version")
        result["counts"] = {t: scan["counts"].get(t, 0) for t in man["row_counts"]}
        problems = []
        if not scan["complete"]:
            problems.append("dump incomplete (missing BEGIN/COMMIT)")
        if result["counts"] != man["row_counts"]:
            problems.append(f"row counts {result['counts']} differ from manifest {man['row_counts']}")
        if full and not problems:
            with tempfile.TemporaryDirectory(dir=scratch_dir) as d:
                db = Path(d) / "verify.sqlite3"
                _load_sql_gz(path, db)
                facts = _db_facts(db)
            if facts["integrity"] != "ok":
                problems.append(f"restored copy integrity_check: {facts['integrity']}")
            if facts["counts"] != man["row_counts"]:
                problems.append(f"restored row counts {facts['counts']} differ from manifest")
        result["integrity"] = "; ".join(problems) if problems else (
            "ok (restored and integrity_check)" if full else "ok (stream, statements and row counts)")
        result["ok"] = not problems and result["checksum_ok"] is True
        return result
    facts = _db_facts(path)
    result |= facts
    result["ok"] = facts["integrity"] == "ok" and result["checksum_ok"] is not False
    return result


def restore_backup(backup: str | Path, target_db: str | Path, *, allow_existing: bool = False) -> dict:
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
    tmp = target.with_suffix(".restoring")
    tmp.unlink(missing_ok=True)
    src = Path(v["path"])
    if src.name.endswith(".sql.gz"):
        _load_sql_gz(src, tmp)
        facts = _db_facts(tmp)
        if facts["integrity"] != "ok" or facts["counts"] != v["manifest"]["row_counts"]:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"restored copy failed checks: {facts}")
    else:
        shutil.copyfile(src, tmp)
    tmp.replace(target)
    _private(target)
    return {"restored_from": str(src), "target": str(target), "verification": v}
