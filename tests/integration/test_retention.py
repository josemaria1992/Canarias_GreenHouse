"""AT-OPS-02 (v1->v2 migration), AT-OPS-03 (storage pressure, archive, retention)."""
import gzip
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aquacrop.control.alarms import AlarmEngine
from aquacrop.historian.retention import (apply_retention, create_archive, evaluate_storage_alarm,
                                          verify_archive)
from aquacrop.historian.schema import MIGRATIONS, connect, migrate, schema_version
from aquacrop.historian.writer import Historian
from aquacrop.twin.runtime import SimulationRuntime


def filled(tmp_path, cfg, seconds=5):
    h = Historian(tmp_path / "db.sqlite3", cfg, "s_ret")
    rt = SimulationRuntime(cfg, audit=h, history=h, session_id="s_ret", start_permitted=h.can_accept_starts)
    h.register_run(rt.run_id, rt.seed, "DEMO", "t")
    rt.run_for(seconds)
    h.wait_idle()
    return h


def window(h):
    lo, hi = h.query("SELECT min(acquired_utc), max(acquired_utc) FROM samples")[0]
    return lo, (datetime.fromisoformat(hi) + timedelta(seconds=1)).isoformat()


def test_demo_policy_never_deletes(tmp_path, cfg24):
    h = filled(tmp_path, cfg24)
    n = h.query("SELECT count(*) FROM samples")[0][0]
    res = apply_retention(h, now=datetime.now(timezone.utc) + timedelta(days=365))
    assert res["deleted"] == 0 and "DEMO" in res["reason"]
    assert h.query("SELECT count(*) FROM samples")[0][0] == n
    h.close()


def test_archive_verify_and_tamper_detection(tmp_path, cfg24):
    h = filled(tmp_path, cfg24)
    lo, hi = window(h)
    a = create_archive(h, lo, hi, tmp_path / "arch")
    assert a["files"]["samples.csv.gz"]["rows"] == h.query("SELECT count(*) FROM samples")[0][0]
    assert verify_archive(a["path"], a["manifest_sha256"])["ok"]
    with pytest.raises(FileExistsError):
        create_archive(h, lo, hi, tmp_path / "arch")
    p = tmp_path / "arch" / a["path"].split("/")[-1] / "samples.csv.gz"
    with gzip.open(p, "at") as f:
        f.write("tampered\n")
    v = verify_archive(a["path"], a["manifest_sha256"])
    assert not v["ok"] and any("checksum" in x for x in v["problems"])
    h.close()


def test_approved_policy_prunes_only_verified_archived_rows(tmp_path, cfg24):
    cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(
        update={"retention_provenance": "APPROVED", "retention_days": 1})})
    h = filled(tmp_path, cfg)
    lo, hi = window(h)
    later = datetime.now(timezone.utc) + timedelta(days=3)
    res = apply_retention(h, now=later)              # nothing archived yet -> nothing deleted
    assert res["deleted"] == 0 and res["unarchived_older_rows"] > 0
    mid = (datetime.fromisoformat(lo) + (datetime.fromisoformat(hi) - datetime.fromisoformat(lo)) / 2).isoformat()
    create_archive(h, lo, mid, tmp_path / "arch")    # archive the first half only
    before = h.query("SELECT count(*) FROM samples")[0][0]
    archived = h.query("SELECT count(*) FROM samples WHERE acquired_utc < ?", (mid,))[0][0]
    res = apply_retention(h, now=later)
    assert res["deleted"] == archived > 0
    assert h.query("SELECT count(*) FROM samples")[0][0] == before - archived
    assert h.query("SELECT count(*) FROM events")[0][0] > 0     # audit untouched
    h.close()


def test_storage_pressure_alarm_and_start_inhibit(tmp_path, cfg24):
    cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(
        update={"free_space_reserve_bytes": 1 << 60, "db_budget_bytes": 1})})
    h = Historian(tmp_path / "db.sqlite3", cfg, "s_p")
    al = AlarmEngine(cfg)
    evaluate_storage_alarm(h, al)
    assert al.open[("STORAGE_PRESSURE", "historian")].severity == "CRITICAL"
    assert h.can_accept_starts() == (False, "free disk below reserve")
    h.fail_writes = True
    with pytest.raises(Exception):
        h.flush()
    evaluate_storage_alarm(h, al)
    assert ("STORAGE_WRITE_FAILED", "historian") in al.open
    h.close()


def test_v1_database_migrates_to_v2_backup_first(tmp_path):
    db = tmp_path / "old.sqlite3"
    con = connect(db)
    con.execute("BEGIN")
    for stmt in [s for s in MIGRATIONS[1][1].split(";") if s.strip()]:
        con.execute(stmt)
    con.execute("INSERT INTO schema_meta VALUES('schema_version','1')")
    con.execute("INSERT INTO migrations VALUES(1,'2026-10-01T00:00:00+00:00','initial',NULL)")
    con.execute("INSERT INTO events(utc,kind,payload_json) VALUES('2026-10-01T00:00:00+00:00','OLD','{}')")
    con.execute("COMMIT")
    assert migrate(con, db) == [2]
    assert schema_version(con) == 2
    row = con.execute("SELECT backup_path FROM migrations WHERE version=2").fetchone()
    assert row[0] and (tmp_path / "backups").exists()
    bk = sqlite3.connect(row[0])
    assert bk.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "1"
    assert bk.execute("SELECT kind FROM events").fetchone()[0] == "OLD"
    con.close()


def test_cli_archive_requires_stopped_server(tmp_path, cfg24):
    from aquacrop import cli
    from aquacrop.ownership import OwnerLock, lock_name
    lk = OwnerLock(tmp_path / lock_name("config", cfg24.config_id)).acquire()
    try:
        with pytest.raises(SystemExit, match="stop the running server"):
            cli.main(["archive", "create", "--config", "demo-24", "--state-dir", str(tmp_path), "--start",
                      "2026-01-01T00:00:00+00:00", "--end", "2026-01-02T00:00:00+00:00", "--destination",
                      str(tmp_path / "a")])
    finally:
        lk.release()
