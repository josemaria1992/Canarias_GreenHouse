"""AT-OPS-01 / AT-BAK-01..05: compressed consistent backups, verification, rotation, low-disk refusal,
restore to a new instance, compatibility with single-file backups (owner requirements of 2026-10-12)."""
import gzip
import json
import os
import sqlite3
import threading
import time

import pytest

from aquacrop.historian.backup import (BackupRefused, _sha256, backup_to, create_backup, list_backups,
                                       manifest_path, restore_backup, verify_backup)
from aquacrop.historian.writer import Historian
from aquacrop.twin.runtime import SimulationRuntime
from tests.helpers import with_sim_time_recording


REASON = "reason with 'quotes'\nLF, CRLF\r\nand a lone CR\r: riego ñ"   # must survive byte for byte


@pytest.fixture
def live(tmp_path, cfg24):
    cfg = with_sim_time_recording(cfg24)     # history rows without waiting for wall-time labels
    h = Historian(tmp_path / "live.sqlite3", cfg, "s1")
    rt = SimulationRuntime(cfg, audit=h, history=h, session_id="s1", start_permitted=h.can_accept_starts)
    rt.set_override("op", "analog_A.AI1", 19800, REASON)
    rt.run_for(180)
    h.wait_idle()
    yield h, rt
    h.close()


def test_compressed_backup_verifies_and_restores_to_a_new_instance(live, tmp_path, cfg24):
    h, rt = live
    man = create_backup(h.db_path, tmp_path / "bk", config_id=cfg24.config_id)
    assert man["format"] == "aquacrop-sql-gz-1" and man["file"].endswith(".sql.gz")
    assert man["row_counts"]["samples"] == 3 * 24 and man["verification"]["ok"]
    assert man["bytes"] < h.db_path.stat().st_size
    assert manifest_path(tmp_path / "bk" / man["file"]).exists()
    if os.name == "posix":
        assert oct((tmp_path / "bk" / man["file"]).stat().st_mode & 0o777) == "0o600"
    v = verify_backup(man["path"], full=True, scratch_dir=tmp_path)
    assert v["ok"] and v["integrity"].startswith("ok (restored")
    with pytest.raises(FileExistsError):
        restore_backup(man["path"], h.db_path)
    res = restore_backup(man["path"], tmp_path / "restored" / "new.sqlite3")
    con = sqlite3.connect(res["target"])
    assert con.execute("SELECT count(*) FROM samples").fetchone()[0] == 72
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    reason = con.execute("SELECT reason FROM samples WHERE point_id='analog_A.AI1' LIMIT 1").fetchone()[0]
    assert reason.endswith(REASON)
    con.close()
    # the restored copy opens with the same software without any migration
    h2 = Historian(res["target"], cfg24, "s2")
    assert h2.applied_migrations == []
    h2.close()


def test_tampered_or_truncated_backups_fail_verification(live, tmp_path):
    h, rt = live
    man = create_backup(h.db_path, tmp_path / "bk")
    p = tmp_path / "bk" / man["file"]
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 0xFF
    p.write_bytes(bytes(data))
    v = verify_backup(p)
    assert not v["ok"] and v["checksum_ok"] is False
    # a truncated stream with a matching checksum (manifest rewritten) is still rejected
    m2 = create_backup(h.db_path, tmp_path / "bk2")
    q = tmp_path / "bk2" / m2["file"]
    full = gzip.decompress(q.read_bytes())
    q.write_bytes(gzip.compress(full[: len(full) // 2]))
    m = json.loads(manifest_path(q).read_text(encoding="utf-8"))
    m["sha256"] = _sha256(q)
    manifest_path(q).write_text(json.dumps(m))
    v = verify_backup(q)
    assert v["checksum_ok"] and not v["ok"] and ("incomplete" in v["integrity"] or "differ" in v["integrity"])
    with pytest.raises(RuntimeError):
        restore_backup(q, tmp_path / "never.sqlite3")
    assert not (tmp_path / "never.sqlite3").exists()


def test_backup_is_consistent_while_the_historian_keeps_writing(live, tmp_path):
    h, rt = live
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            rt.run_for(60)
            time.sleep(0.005)
    t = threading.Thread(target=writer)
    t.start()
    try:
        mans = [create_backup(h.db_path, tmp_path / "bk") for _ in range(3)]
    finally:
        stop.set()
        t.join()
    for m in mans:   # each dump matches the snapshot it was taken from (checked inside create_backup)
        assert m["verification"]["ok"]
        assert m["row_counts"]["samples"] == 24 * m["row_counts"]["cycles"]


def test_rotation_keeps_newest_and_leaves_other_copies(live, tmp_path):
    h, rt = live
    dest = tmp_path / "bk"
    pre = backup_to(sqlite3.connect(h.db_path), dest / "pre-migrate-v1-x.sqlite3")
    names = []
    for _ in range(4):
        names.append(create_backup(h.db_path, dest, keep=2)["file"])
        time.sleep(0.002)
    left = [p.name for p in list_backups(dest)]
    assert left == names[-2:] and pre.exists()
    assert not manifest_path(dest / names[0]).exists()
    with pytest.raises(ValueError):
        create_backup(h.db_path, dest, keep=0)


def test_backup_refused_when_it_would_eat_the_reserve(live, tmp_path):
    h, rt = live
    with pytest.raises(BackupRefused, match="reserve"):
        create_backup(h.db_path, tmp_path / "bk", reserve_bytes=1 << 60)
    assert list_backups(tmp_path / "bk") == []


def test_single_file_backups_remain_verifiable_and_restorable(live, tmp_path):
    h, rt = live
    p = backup_to(sqlite3.connect(h.db_path), tmp_path / "old" / "aquacrop-20261011T000000Z.sqlite3")
    v = verify_backup(tmp_path / "old")
    assert v["ok"] and v["integrity"] == "ok" and v["checksum_ok"] is None
    res = restore_backup(p, tmp_path / "r.sqlite3")
    assert sqlite3.connect(res["target"]).execute("SELECT count(*) FROM samples").fetchone()[0] == 72


def test_cli_backup_keep_and_refusal(tmp_path, cfg24):
    from aquacrop import cli
    h = Historian(tmp_path / "demo-24.sqlite3", cfg24, "s")
    h.event("X", {})
    h.close()
    assert cli.main(["backup", "create", "--config", "demo-24", "--state-dir", str(tmp_path), "--destination",
                     str(tmp_path / "bk"), "--keep", "1"]) == 0
    assert cli.main(["backup", "verify", "--backup", str(tmp_path / "bk"), "--full"]) == 0
    assert cli.main(["storage", "report", "--config", "demo-24", "--state-dir", str(tmp_path)]) == 0


# ---------------------------------------------------------------------------------------- R3 (owner review)
from aquacrop.historian import backup as bk_mod   # noqa: E402
from aquacrop.historian.backup import (BackupAborted, BackupFailed, latest_drill,  # noqa: E402
                                       verify_backup as _verify)


def _leftovers(d):
    return sorted(p.name for p in d.iterdir() if p.name.endswith(".partial"))


def test_concurrent_backups_restore_to_their_own_snapshot(live, tmp_path, cfg24):
    """Manifest facts (counts, fingerprints, schema) and the dump come from one read transaction while
    the historian keeps writing: a full restore of each matches its manifest exactly."""
    h, rt = live
    stop = threading.Event()
    writes = [0]

    def writer():
        while not stop.is_set():
            rt.run_for(60)
            h.event("BURST", {"n": 1})
            writes[0] += 1
    t = threading.Thread(target=writer)
    t.start()
    try:
        mans = []
        for _ in range(2):
            # at least one more write before each backup, so the two snapshots must differ
            # (a fast runner could otherwise take both before the writer thread gets scheduled)
            n, deadline = writes[0], time.monotonic() + 30
            while writes[0] == n and time.monotonic() < deadline:
                time.sleep(0.005)
            h.flush()                     # the writer only queues: persist that write before the snapshot reads
            mans.append(create_backup(h.db_path, tmp_path / "bk"))
    finally:
        stop.set()
        t.join()
    for m in mans:
        v = _verify(m["path"], full=True, scratch_dir=tmp_path / "scratch", cfg=h.cfg)
        assert v["ok"], v["integrity"]
        f = v["full"]
        assert f["facts"]["foreign_key_violations"] == 0 and f["facts"]["schema_version"] == m["schema_version"]
        assert f["smoke"]["ok"] and f["smoke"]["historian_started"]
    assert mans[0]["row_counts"] != mans[1]["row_counts"]          # the writer really ran in between


def test_failed_verification_publishes_nothing_and_keeps_earlier_backups(live, tmp_path, monkeypatch):
    h, rt = live
    dest = tmp_path / "bk"
    good = create_backup(h.db_path, dest)
    monkeypatch.setattr(bk_mod, "_verify_sql_gz", lambda path, man, **kw: {
        "ok": False, "checksum_ok": True, "integrity": "simulated verification failure", "counts": {}})
    with pytest.raises(BackupFailed, match="not published"):
        create_backup(h.db_path, dest, keep=1)
    assert [p.name for p in list_backups(dest)] == [good["file"]]   # rotation never ran
    assert _leftovers(dest) == [] and len(list(dest.glob("*.manifest.json"))) == 1
    monkeypatch.undo()
    assert _verify(dest)["ok"]


def test_running_out_of_space_while_writing_aborts_cleanly(live, tmp_path, monkeypatch):
    h, rt = live
    dest = tmp_path / "bk"
    good = create_backup(h.db_path, dest)
    calls = {"n": 0}
    real = bk_mod._free_bytes

    def shrinking(path):
        calls["n"] += 1
        return real(path) if calls["n"] <= 2 else 10 * 2**20       # preflight and first check pass, then 10 MiB
    monkeypatch.setattr(bk_mod, "_free_bytes", shrinking)
    monkeypatch.setattr(bk_mod, "BACKUP_CHECK_S", 0.0)
    with pytest.raises(BackupAborted, match="below the reserve"):
        create_backup(h.db_path, dest, reserve_bytes=2**30)
    assert [p.name for p in list_backups(dest)] == [good["file"]] and _leftovers(dest) == []


def test_interrupted_backup_leaves_nothing_behind(live, tmp_path, monkeypatch):
    h, rt = live
    dest = tmp_path / "bk"
    calls = {"n": 0}

    def enospc(path):
        calls["n"] += 1
        if calls["n"] > 2:
            raise OSError(28, "No space left on device")
        return 1 << 50
    monkeypatch.setattr(bk_mod, "_free_bytes", enospc)
    monkeypatch.setattr(bk_mod, "BACKUP_CHECK_S", 0.0)
    with pytest.raises(OSError):
        create_backup(h.db_path, dest)
    assert list(dest.iterdir()) == []


def test_wal_growth_cancels_the_long_reader(live, tmp_path):
    h, rt = live
    h.event("X", {})
    h.wait_idle()
    with pytest.raises(BackupAborted, match="WAL"):
        create_backup(h.db_path, tmp_path / "bk", wal_abort_bytes=1)
    assert list((tmp_path / "bk").iterdir()) == []


def test_stale_partials_and_unpublished_files_are_never_listed_and_get_cleaned(live, tmp_path):
    h, rt = live
    dest = tmp_path / "bk"
    dest.mkdir()
    old = time.time() - 7 * 3600
    for name in ("aquacrop-20260101T000000Z.sql.gz.partial", "aquacrop-20260101T000001Z.sql.gz",
                 "aquacrop-20260101T000002Z.sql.gz"):
        (dest / name).write_bytes(b"x")
    for name in ("aquacrop-20260101T000000Z.sql.gz.partial", "aquacrop-20260101T000001Z.sql.gz"):
        os.utime(dest / name, (old, old))
    assert list_backups(dest) == []                                  # no manifest: not a backup
    m = create_backup(h.db_path, dest)
    assert sorted(m["cleaned_stale"]) == ["aquacrop-20260101T000000Z.sql.gz.partial",
                                          "aquacrop-20260101T000001Z.sql.gz"]
    assert (dest / "aquacrop-20260101T000002Z.sql.gz").exists()      # recent: may still be in progress
    assert [p.name for p in list_backups(dest)] == [m["file"]]


def test_restore_drill_catches_tampering_that_keeps_counts_and_checksum(live, tmp_path, cfg24):
    """A dump edited so that row counts still match and the manifest checksum is re-signed passes the
    stream check but fails the restore drill (foreign keys, fingerprints)."""
    h, rt = live
    man = create_backup(h.db_path, tmp_path / "bk")
    p = tmp_path / "bk" / man["file"]
    text = gzip.decompress(p.read_bytes()).decode("utf-8")
    first = text.index('INSERT INTO "samples" VALUES(1,1,')
    text = text[:first] + 'INSERT INTO "samples" VALUES(1,999999,' + text[first + len('INSERT INTO "samples" VALUES(1,1,'):]
    p.write_bytes(gzip.compress(text.encode("utf-8")))
    mf = manifest_path(p)
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["sha256"] = _sha256(p)
    mf.write_text(json.dumps(m), encoding="utf-8")
    assert _verify(p)["ok"]                                          # stream, statements and counts look fine
    v = _verify(p, full=True, scratch_dir=tmp_path / "scratch", cfg=cfg24, report_dir=tmp_path / "drills")
    assert not v["ok"] and "foreign_key_check" in v["integrity"] and "fingerprints differ" in v["integrity"]
    d = latest_drill(tmp_path / "drills")
    assert d["ok"] is False and d["utc"]


def test_restore_drill_needs_scratch_space_and_keeps_the_reserve(live, tmp_path, monkeypatch):
    h, rt = live
    man = create_backup(h.db_path, tmp_path / "bk")
    monkeypatch.setattr(bk_mod, "_free_bytes", lambda path: 1)
    v = _verify(man["path"], full=True, scratch_dir=tmp_path / "scratch")
    assert not v["ok"] and "not enough scratch space" in v["integrity"]
    assert not any((tmp_path / "scratch").iterdir())
    with pytest.raises(BackupRefused):
        restore_backup(man["path"], tmp_path / "r.sqlite3", reserve_bytes=2**30)
    assert not (tmp_path / "r.sqlite3").exists()


def test_safety_copy_before_a_migration_needs_room_for_the_whole_database(live, tmp_path):
    h, rt = live
    with pytest.raises(BackupRefused, match="safety copy"):
        backup_to(sqlite3.connect(h.db_path), tmp_path / "pre" / "pre-migrate.sqlite3", reserve_bytes=1 << 60)
    assert not list((tmp_path / "pre").iterdir())


def test_cli_restore_drill_writes_a_report(live, tmp_path, cfg24):
    from aquacrop import cli
    h, rt = live
    create_backup(h.db_path, tmp_path / "bk")
    assert cli.main(["backup", "verify", "--backup", str(tmp_path / "bk"), "--full", "--scratch-dir",
                     str(tmp_path / "scratch"), "--config", "demo-24", "--report-dir", str(tmp_path / "drills")]) == 0
    d = latest_drill(tmp_path / "drills")
    assert d["ok"] is True and "application smoke test" in d["integrity"]
