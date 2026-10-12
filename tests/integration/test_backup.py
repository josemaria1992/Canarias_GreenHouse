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
