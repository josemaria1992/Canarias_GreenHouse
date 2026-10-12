"""AT-RET-01..05, AT-CAP-01..03 (rolling 365-day window, clock guard, capacity alarms, low/full disk),
AT-OPS-02 (v1->v2 migration of pre-release databases) and archives (owner requirements of 2026-10-12)."""
import gzip
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aquacrop.control.alarms import AlarmEngine
from aquacrop.historian.retention import (RetentionTask, apply_retention, create_archive, evaluate_storage_alarm,
                                          prune_expired, storage_alarms, verify_archive)
from aquacrop.historian.schema import MIGRATIONS, connect, migrate, schema_version
from aquacrop.historian.writer import Historian
from aquacrop.twin.runtime import SimulationRuntime
from tests.helpers import with_sim_time_recording

# A "now" 200 days ahead keeps rows the historian writes in real time (events) inside the window.
NOW = (datetime.now(timezone.utc) + timedelta(days=200)).replace(microsecond=0)
CUTOFF = NOW - timedelta(days=365)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="microseconds")


def seed(h, t: datetime, *, tag: str = "x", points: int = 2, closed_alarm: bool = True, open_alarm: bool = False):
    """One recorded minute at time t: a cycle with samples, an event, a finished command and an alarm."""
    ts = iso(t)

    def op(c):
        pk = c.execute("INSERT INTO cycles(cycle_id,session_id,run_id,sim_s,persisted_utc,n_points,cadence_s,"
                       "cadence_basis) VALUES(?,?,NULL,NULL,?,?,60,'WALL')", (f"{tag}:{ts}", "old", ts, points)).lastrowid
        c.executemany("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,origin,"
                      "source_profile,config_version,device_uid,bus_id,session_id) VALUES(?,?,?,?,1,'GOOD','REAL',"
                      "'p','v','relay_A','rs485_a','old')",
                      [(pk, f"relay_A.DI{i + 1}", ts, ts) for i in range(points)])
        c.execute("INSERT INTO events(utc,kind,session_id,payload_json) VALUES(?,'DI_EDGE','old','{}')", (ts,))
        c.execute("INSERT INTO commands(request_id,actor,target,desired,origin,issued_utc,expires_utc,"
                  "expected_config_version,priority,backend,session_id,intent_utc,outcome) VALUES(?,'u','relay_A.DO1',"
                  "1,'BENCH',?,?,'v',5,'MODBUS','old',?,'READBACK_CONFIRMED')", (f"cmd-{tag}-{ts}", ts, ts, ts))
        for closed in ([1] if closed_alarm else []) + ([0] if open_alarm else []):
            c.execute("INSERT INTO alarm_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (f"alm-{tag}-{closed}-{ts}", "R", "T", "WARNING", closed, 1 - closed, closed, ts, ts, ts, "{}"))
    h.submit(op, wait=True)


def counts(h) -> dict:
    return {t: h.query(f"SELECT count(*) FROM {t}")[0][0]
            for t in ("cycles", "samples", "events", "commands", "alarm_occurrences")}


@pytest.fixture
def h(tmp_path, cfg24):
    hist = Historian(tmp_path / "db.sqlite3", cfg24, "s_ret")
    yield hist
    hist.close()


# ---------------------------------------------------------------------------------------- rolling window
def test_rolling_window_boundary_is_exactly_365_days(h):
    seed(h, CUTOFF - timedelta(seconds=1), tag="old", open_alarm=True)
    seed(h, CUTOFF, tag="edge")                                 # exactly 365 days old: kept
    seed(h, CUTOFF + timedelta(seconds=1), tag="in")
    seed(h, NOW - timedelta(days=1), tag="new")
    base = counts(h)
    res = prune_expired(h, now=NOW)
    assert res["cutoff_utc"] == iso(CUTOFF)
    d = res["deleted"]
    assert d["samples"] == 2 and d["cycles"] == 1 and d["events"] == 1 and d["commands"] == 1
    assert d["alarm_occurrences"] == 1                          # the closed one; the open one stays
    after = counts(h)
    assert after["samples"] == base["samples"] - 2 and after["cycles"] == base["cycles"] - 1
    assert h.query("SELECT min(acquired_utc) FROM samples")[0][0] == iso(CUTOFF)
    assert h.query("SELECT count(*) FROM alarm_occurrences WHERE closed=0")[0][0] == 1
    con = sqlite3.connect(h.db_path)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()
    h.wait_idle()
    ev = json.loads(h.query("SELECT payload_json FROM events WHERE kind='RETENTION_PRUNED'")[0][0])
    assert ev["deleted"]["samples"] == 2 and ev["retention_days"] == 365
    # a second run has nothing to do and records nothing
    assert not any(prune_expired(h, now=NOW)["deleted"].values())
    assert h.query("SELECT count(*) FROM events WHERE kind='RETENTION_PRUNED'")[0][0] == 1


def test_prune_runs_in_small_batches(h):
    t = CUTOFF - timedelta(days=3)
    for i in range(300):
        seed(h, t + timedelta(minutes=i), tag=f"b{i}", points=4, closed_alarm=False)
    seed(h, NOW - timedelta(hours=1), tag="keep", points=4, closed_alarm=False)
    commits = h.health["commits"]
    res = prune_expired(h, now=NOW, chunk_rows=100)
    assert res["deleted"]["samples"] == 1200 and res["deleted"]["cycles"] == 300
    assert h.health["commits"] - commits >= 12                 # >= 1200/100 separate transactions
    assert counts(h)["samples"] == 4 and counts(h)["cycles"] == 1


def test_dry_run_and_demo_policy_delete_nothing(h, cfg24):
    seed(h, CUTOFF - timedelta(days=10), tag="old")
    base = counts(h)
    dry = apply_retention(h, now=NOW, dry_run=True)
    assert dry["would_delete"]["samples"] == 2 and counts(h) == base
    h.cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(update={"retention_provenance": "DEMO"})})
    res = prune_expired(h, now=NOW)
    assert res["deleted"] == {} and "DEMO" in res["reason"] and counts(h) == base


def test_over_budget_is_a_warning_and_nothing_inside_the_window_is_deleted(h, cfg24):
    for i in range(5):
        seed(h, datetime.now(timezone.utc) - timedelta(days=30 * i), tag=f"w{i}")
    h.cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(update={"db_budget_bytes": 1})})
    base = counts(h)
    al = AlarmEngine(h.cfg)
    evaluate_storage_alarm(h, al)
    occ = al.open[("STORAGE_CAPACITY", "historian")]
    assert occ.severity == "WARNING" and "never deleted" in occ.message
    assert h.can_accept_starts() == (True, None)               # over budget alone does not block anything
    prune_expired(h)                                            # real clock: everything is inside the window
    assert counts(h) == base


# ---------------------------------------------------------------------------------------- clock guard
class FakeClock:
    def __init__(self, wall: float):
        self.wall, self.mono = wall, 1000.0

    def w(self):
        return self.wall

    def m(self):
        return self.mono


def _wait_task(task):
    end = time.monotonic() + 10
    while task.running and time.monotonic() < end:
        time.sleep(0.02)


def test_retention_holds_when_clock_is_far_past_the_newest_record_until_acknowledged(h):
    seed(h, CUTOFF - timedelta(days=1), tag="old")
    seed(h, NOW - timedelta(days=40), tag="last")               # newest record from the previous process
    clk = FakeClock(NOW.timestamp())
    task = RetentionTask(h, wall=clk.w, monotonic=clk.m)
    al = AlarmEngine(h.cfg)
    task.tick(al)
    assert task.held and "40 days" in task.held and not task.running
    occ = al.open[("RETENTION_HELD", "historian")]
    assert occ.severity == "WARNING" and "Acknowledge" in occ.message
    clk.mono += 7200
    clk.wall += 7200
    task.tick(al)
    assert task.held and counts(h)["samples"] == 4              # still held: nothing deleted
    al.acknowledge(occ.occurrence_id, "operator1")
    task.tick(al)
    _wait_task(task)
    assert task.held is None and ("RETENTION_HELD", "historian") not in al.open
    assert task.last_result["deleted"]["samples"] == 2
    h.wait_idle()
    kinds = [r[0] for r in h.query("SELECT kind FROM events WHERE kind LIKE 'RETENTION_%' ORDER BY id")]
    assert kinds == ["RETENTION_HELD", "RETENTION_RELEASED", "RETENTION_PRUNED"]
    task.tick(al)                                               # next run waits for the hourly interval
    assert not task.running


def test_retention_holds_for_a_history_without_snapshots(h):
    """An earlier process that wrote events and commands but no snapshot (stopped within its first
    minute, or snapshots paused on a full disk) still anchors the clock check."""
    ts = iso(NOW - timedelta(days=400))

    def audit_only(c):
        c.execute("INSERT INTO events(utc,kind,session_id,payload_json) VALUES(?,'DI_EDGE','old','{}')", (ts,))
        c.execute("INSERT INTO commands(request_id,actor,target,desired,origin,issued_utc,expires_utc,"
                  "expected_config_version,priority,backend,session_id,intent_utc,outcome) VALUES('c1','u',"
                  "'relay_A.DO1',1,'BENCH',?,?,'v',5,'MODBUS','old',?,'READBACK_CONFIRMED')", (ts, ts, ts))
    h.submit(audit_only, wait=True)
    assert counts(h)["cycles"] == 0
    clk = FakeClock(NOW.timestamp())
    task = RetentionTask(h, wall=clk.w, monotonic=clk.m)
    task.tick(AlarmEngine(h.cfg))
    assert task.held and "400 days" in task.held and not task.running
    assert counts(h)["commands"] == 1 and h.query("SELECT count(*) FROM events WHERE kind='DI_EDGE'")[0][0] == 1


def test_retention_holds_on_forward_clock_step_and_on_clock_behind_records(h):
    seed(h, NOW - timedelta(minutes=5), tag="recent")
    clk = FakeClock(NOW.timestamp())
    task = RetentionTask(h, wall=clk.w, monotonic=clk.m)
    al = AlarmEngine(h.cfg)
    task.tick(al)
    _wait_task(task)
    assert task.held is None and task.last_result is not None
    clk.mono += 60
    clk.wall += 60 + 2 * 3600                                   # wall jumped 2 h ahead of monotonic time
    task.tick(al)
    assert task.held and "stepped forward" in task.held
    behind = RetentionTask(h, wall=lambda: (NOW - timedelta(hours=2)).timestamp(), monotonic=clk.m)
    behind.tick(AlarmEngine(h.cfg))
    assert behind.held and "behind" in behind.held


# ---------------------------------------------------------------------------------------- capacity
def _with(h, cfg24, **kw):
    h.cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(update=kw)})
    return h.cfg


def test_capacity_levels_low_disk_reserve_and_snapshot_floor(h, cfg24):
    big = 1 << 60
    _with(h, cfg24, free_space_warning_bytes=big)
    assert [a[:2] for a in storage_alarms(h)] == [("STORAGE_CAPACITY", "WARNING")]
    _with(h, cfg24, free_space_warning_bytes=big, free_space_reserve_bytes=big)
    al = AlarmEngine(h.cfg)
    evaluate_storage_alarm(h, al)
    assert al.open[("STORAGE_PRESSURE", "historian")].severity == "CRITICAL"
    assert al.open[("STORAGE_CAPACITY", "historian")].severity == "WARNING"
    assert h.can_accept_starts() == (False, "free disk below reserve")
    # full-disk handling: below the floor periodic snapshots pause; events are still written
    _with(h, cfg24, free_space_warning_bytes=big, free_space_reserve_bytes=big, snapshot_floor_bytes=big)
    rt = SimulationRuntime(h.cfg, session_id="s_ret")
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    def snap(i):
        at = t0 + timedelta(minutes=i)
        return h.snapshot(at, rt.wall_snapshot(at, 0.0), 60, run_id=rt.run_id, sim_s=rt.sim_s)
    assert not snap(1) and not snap(2)
    h.wait_idle()
    assert counts(h)["cycles"] == 0 and h.health["paused_snapshots"] == 2
    evaluate_storage_alarm(h, al)
    assert al.open[("HISTORY_SNAPSHOTS_PAUSED", "historian")].severity == "CRITICAL"
    assert h.query("SELECT count(*) FROM events WHERE kind='HISTORY_SNAPSHOTS_PAUSED'")[0][0] == 1
    h.event("STILL_WRITTEN", {})
    h.wait_idle()
    assert h.query("SELECT count(*) FROM events WHERE kind='STILL_WRITTEN'")[0][0] == 1
    _with(h, cfg24)                                             # space freed
    assert snap(3)
    h.wait_idle()
    assert counts(h)["cycles"] == 1
    ev = json.loads(h.query("SELECT payload_json FROM events WHERE kind='HISTORY_SNAPSHOTS_RESUMED'")[0][0])
    assert ev["snapshots_not_written"] == 2               # the gap is reported with its labels, not back-filled
    assert (ev["first_label_not_written_utc"], ev["last_label_not_written_utc"]) == (
        (t0 + timedelta(minutes=1)).isoformat(), (t0 + timedelta(minutes=2)).isoformat())
    evaluate_storage_alarm(h, al)
    assert not any(k[0] in ("HISTORY_SNAPSHOTS_PAUSED", "STORAGE_PRESSURE", "STORAGE_CAPACITY") and o.active
                   for k, o in al.open.items())


def test_write_failure_is_critical_and_inhibits_starts(h):
    al = AlarmEngine(h.cfg)
    h.fail_writes = True
    with pytest.raises(Exception):
        h.flush()
    evaluate_storage_alarm(h, al)
    assert al.open[("STORAGE_WRITE_FAILED", "historian")].severity == "CRITICAL"
    assert h.can_accept_starts()[0] is False
    h.fail_writes = False


def test_projection_of_the_365_day_size_is_reported(h):
    for i in range(4):
        seed(h, NOW - timedelta(days=2) + timedelta(hours=16 * i), tag=f"p{i}")
    st = h.storage()
    assert st["history_span_days"] == pytest.approx(2.0)
    assert st["projected_window_bytes"] == pytest.approx(st["db_used_bytes"] * 365 / 2, rel=1e-6)
    assert st["recording_interval_s"] == 60 and st["retention_days"] == 365


# ---------------------------------------------------------------------------------------- archives / migration
def test_archive_verify_and_tamper_detection(tmp_path, cfg24):
    cfg24 = with_sim_time_recording(cfg24)
    h = Historian(tmp_path / "a.sqlite3", cfg24, "s_arc")
    rt = SimulationRuntime(cfg24, audit=h, history=h, session_id="s_arc", start_permitted=h.can_accept_starts)
    rt.run_for(180)
    h.wait_idle()
    lo, hi = h.query("SELECT min(acquired_utc), max(acquired_utc) FROM samples")[0]
    hi = (datetime.fromisoformat(hi) + timedelta(seconds=1)).isoformat()
    a = create_archive(h, lo, hi, tmp_path / "arch")
    assert a["files"]["samples.csv.gz"]["rows"] == h.query("SELECT count(*) FROM samples")[0][0] == 3 * 24
    assert verify_archive(a["path"], a["manifest_sha256"])["ok"]
    with gzip.open(Path(a["path"]) / "samples.csv.gz", "rt", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    assert header[-2:] == ["last_good_value", "last_good_utc"]   # STALE rows keep their last good reading
    with pytest.raises(FileExistsError):
        create_archive(h, lo, hi, tmp_path / "arch")
    p = Path(a["path"]) / "samples.csv.gz"
    with gzip.open(p, "at", encoding="utf-8") as f:
        f.write("tampered\n")
    v = verify_archive(a["path"], a["manifest_sha256"])
    assert not v["ok"] and any("checksum" in x for x in v["problems"])
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


def test_cli_archive_and_retention_require_stopped_server(tmp_path, cfg24):
    from aquacrop import cli
    from aquacrop.ownership import OwnerLock, lock_name
    lk = OwnerLock(tmp_path / lock_name("config", cfg24.config_id)).acquire()
    try:
        with pytest.raises(SystemExit, match="stop the running server"):
            cli.main(["archive", "create", "--config", "demo-24", "--state-dir", str(tmp_path), "--start",
                      "2026-01-01T00:00:00+00:00", "--end", "2026-01-02T00:00:00+00:00", "--destination",
                      str(tmp_path / "a")])
        with pytest.raises(SystemExit, match="stop the running server"):
            cli.main(["retention", "apply", "--config", "demo-24", "--state-dir", str(tmp_path), "--dry-run"])
    finally:
        lk.release()
