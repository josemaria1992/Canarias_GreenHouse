"""Consistent backups via the SQLite online backup API (never a raw copy of a live WAL database)."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from .. import __version__


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_to(con: sqlite3.Connection, dest: str | Path) -> Path:
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
    return dest


def create_backup(db_path: str | Path, destination: str | Path, *, config_id: str | None = None,
                  config_version: str | None = None) -> dict:
    """Create `<destination>/aquacrop-<ts>.sqlite3` plus a manifest with checksum and metadata."""
    from .schema import connect, schema_version, utc_iso
    destination = Path(destination)
    con = connect(db_path)
    try:
        stamp = utc_iso().replace(":", "").replace("+0000", "Z")
        dest = backup_to(con, destination / f"aquacrop-{stamp}.sqlite3")
        ver = schema_version(con)
    finally:
        con.close()
    manifest = {"file": dest.name, "sha256": _sha256(dest), "bytes": dest.stat().st_size, "created_utc": stamp,
                "schema_version": ver, "software_version": __version__, "source_db": str(Path(db_path).resolve()),
                "config_id": config_id, "config_version": config_version}
    dest.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest | {"path": str(dest)}


def verify_backup(path: str | Path) -> dict:
    """Checksum + integrity_check + schema version. Opens the copy read-only; never touches the live DB."""
    path = Path(path)
    if path.is_dir():
        cands = sorted(path.glob("aquacrop-*.sqlite3"))
        if not cands:
            raise FileNotFoundError(f"no backups in {path}")
        path = cands[-1]
    man_path = path.with_suffix(".manifest.json")
    result: dict = {"path": str(path), "ok": False}
    if man_path.exists():
        man = json.loads(man_path.read_text())
        result["manifest"] = man
        result["checksum_ok"] = man.get("sha256") == _sha256(path)
    else:
        result["checksum_ok"] = None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result["integrity"] = con.execute("PRAGMA integrity_check").fetchone()[0]
        row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        result["schema_version"] = int(row[0]) if row else None
        result["counts"] = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                            for t in ("samples", "events", "commands", "alarm_occurrences")}
    finally:
        con.close()
    result["ok"] = result["integrity"] == "ok" and result["checksum_ok"] is not False
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
    shutil.copyfile(v["path"], tmp)
    tmp.replace(target)
    return {"restored_from": v["path"], "target": str(target), "verification": v}
