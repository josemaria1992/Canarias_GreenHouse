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
from aquacrop.historian.retention import (ProposalMismatch, RetentionTask, apply_retention, create_archive,
                                          evaluate_storage_alarm, prune_expired, storage_alarms, verify_archive)
from aquacrop.historian.schema import CURRENT_VERSION, MIGRATIONS, connect, migrate, schema_version
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


def test_op03_configuration_definitions_referenced_by_retained_records_are_kept(h):
    """OP-03: the newest CONFIG_LOADED of each distinct configuration stays (it defines the
    config_version and scaling that retained samples refer to), also when it is older than the window;
    an older load of the same configuration goes. Open alarms, unfinished commands, recipes and stock
    readiness are never pruned."""
    old = iso(CUTOFF - timedelta(days=30))
    older = iso(CUTOFF - timedelta(days=60))

    def op(c):
        for ts, sha, ver in ((older, "a" * 64, "v1"), (old, "a" * 64, "v1"), (older, "b" * 64, "v0")):
            c.execute("INSERT INTO events(utc,kind,session_id,payload_json) VALUES(?,'CONFIG_LOADED','old',?)",
                      (ts, json.dumps({"config_id": "c", "config_version": ver, "sha256": sha, "config": {}})))
        c.execute("INSERT INTO commands(request_id,actor,target,desired,origin,issued_utc,expires_utc,"
                  "expected_config_version,priority,backend,session_id,intent_utc,outcome) VALUES('cmd-open','u',"
                  "'relay_A.DO1',1,'BENCH',?,?,'v',5,'MODBUS','old',?,'EXECUTING')", (older, older, older))
        c.execute("INSERT INTO stock_readiness(stock,status,actor,utc,json) VALUES('A','PREPARED','op',?,'{}')",
                  (older,))
    h.submit(op, wait=True)
    seed(h, CUTOFF - timedelta(days=1), tag="old", open_alarm=True)
    res = prune_expired(h, now=NOW)
    assert res["deleted"]["events"] >= 2
    kept = h.query("SELECT utc, payload_json FROM events WHERE kind='CONFIG_LOADED' ORDER BY id")
    assert [(r[0], json.loads(r[1])["sha256"][0]) for r in kept] == [(old, "a"), (older, "b")]
    assert h.query("SELECT outcome FROM commands WHERE request_id='cmd-open'")[0][0] == "EXECUTING"
    assert h.query("SELECT count(*) FROM stock_readiness")[0][0] == 1
    assert h.query("SELECT count(*) FROM alarm_occurrences WHERE closed=0")[0][0] == 1


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

    def run(self, seconds):
        self.wall += seconds
        self.mono += seconds


def _wait_task(task):
    end = time.monotonic() + 10
    while task.running and time.monotonic() < end:
        time.sleep(0.02)


def _task(h, clk):
    return RetentionTask(h, wall=clk.w, monotonic=clk.m)


def _kinds(h):
    h.wait_idle()
    return [r[0] for r in h.query("SELECT kind FROM events WHERE kind LIKE 'RETENTION_%' ORDER BY id")]


def test_hold_after_downtime_is_released_only_by_confirming_the_cutoff(h):
    seed(h, CUTOFF - timedelta(days=1), tag="old")
    seed(h, NOW - timedelta(hours=2), tag="last")               # newest record from the previous process
    clk = FakeClock(NOW.timestamp())
    task = _task(h, clk)
    al = AlarmEngine(h.cfg)
    task.tick(al)
    assert task.held["code"] == "UNVERIFIED_DOWNTIME" and "2.0 h" in task.held["reason"] and not task.running
    occ = al.open[("RETENTION_HELD", "historian")]
    assert occ.severity == "WARNING" and "acknowledging this alarm does not resume deletion" in occ.message
    al.acknowledge(occ.occurrence_id, "operator1")             # acknowledgement records only that
    clk.run(7200)
    task.tick(al)
    assert task.held and counts(h)["samples"] == 4 and not task.running
    prop = task.proposal()                                      # shown to the administrator before release
    assert prop["cutoff_utc"] == iso(CUTOFF + timedelta(hours=2)) and prop["would_delete"]["samples"] == 2
    assert prop["newest_earlier_process_record_utc"] == iso(NOW - timedelta(hours=2))
    with pytest.raises(ProposalMismatch) as e:                  # a different cutoff is refused with the new one
        task.confirm("admin1", iso(CUTOFF + timedelta(days=1)))
    assert e.value.proposal["cutoff_utc"] == prop["cutoff_utc"] and task.held
    task.confirm("admin1", prop["cutoff_utc"])
    assert task.held is None
    task.tick(al)
    _wait_task(task)
    assert task.last_result["deleted"]["samples"] == 2 and counts(h)["samples"] == 2
    assert ("RETENTION_HELD", "historian") not in al.open      # cleared, and closed since it was acknowledged
    assert _kinds(h) == ["RETENTION_HELD", "RETENTION_CLOCK_CONFIRMED", "RETENTION_PRUNED"]
    row = h.query("SELECT payload_json, actor FROM events WHERE kind='RETENTION_CLOCK_CONFIRMED'")[0]
    ev = json.loads(row["payload_json"])
    assert row["actor"] == "admin1" and ev["confirmed_by"] == "admin1" and ev["cutoff_utc"] == prop["cutoff_utc"]
    assert ev["hold"]["code"] == "UNVERIFIED_DOWNTIME" and ev["would_delete"]["samples"] == 2
    with pytest.raises(LookupError):
        task.confirm("admin1", prop["cutoff_utc"])              # nothing to release any more


@pytest.mark.parametrize("offset_s,code", [(-40 * 60, None), (-2 * 3600, "UNVERIFIED_DOWNTIME"),
                                           (3 * 60, None), (10 * 60, "CLOCK_BEHIND_STORED")])
def test_start_check_catches_small_jumps(h, offset_s, code):
    """A start 40 min after the newest record is a restart; 2 h is unverified downtime. A clock 3 min
    behind the newest record is tolerated (NTP slew); 10 min behind holds."""
    seed(h, NOW + timedelta(seconds=offset_s), tag="last")
    task = _task(h, FakeClock(NOW.timestamp()))
    task.tick(AlarmEngine(h.cfg))
    assert (task.held or {}).get("code") == code


@pytest.mark.parametrize("step_s,code", [(120, None), (-120, None), (600, "CLOCK_STEP_FORWARD"),
                                         (-600, "CLOCK_STEP_BACKWARD")])
def test_clock_steps_while_running(h, step_s, code):
    seed(h, NOW - timedelta(minutes=5), tag="recent")
    clk = FakeClock(NOW.timestamp())
    task = _task(h, clk)
    al = AlarmEngine(h.cfg)
    task.tick(al)
    _wait_task(task)
    assert task.held is None
    clk.run(60)
    clk.wall += step_s
    task.tick(al)
    assert (task.held or {}).get("code") == code


def test_forward_jump_protects_current_window_records(h):
    """A clock that jumps two years ahead would put everything outside the window. Retention holds, the
    proposal shows what a confirmation would delete, and nothing is deleted without it."""
    for d in (300, 100, 1):
        seed(h, NOW - timedelta(days=d), tag=f"w{d}")
    seed(h, NOW - timedelta(minutes=5), tag="recent")
    clk = FakeClock(NOW.timestamp())
    task = _task(h, clk)
    al = AlarmEngine(h.cfg)
    task.tick(al)
    _wait_task(task)
    base = counts(h)
    assert task.held is None and task.last_result["deleted"]["samples"] == 0
    clk.run(60)
    clk.wall += 2 * 365 * 86400
    for _ in range(3):
        clk.run(3600)
        task.tick(al)
    assert task.held["code"] == "CLOCK_STEP_FORWARD" and "730.0 days" in task.held["reason"]
    h.wait_idle()
    after = counts(h)
    assert after["events"] == base["events"] + 1                # RETENTION_HELD; nothing deleted
    assert {k: v for k, v in after.items() if k != "events"} == {k: v for k, v in base.items() if k != "events"}
    assert task.proposal()["would_delete"]["samples"] == base["samples"]   # what a careless release would do


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
    task = _task(h, FakeClock(NOW.timestamp()))
    task.tick(AlarmEngine(h.cfg))
    assert task.held["code"] == "UNVERIFIED_DOWNTIME" and "400.0 days" in task.held["reason"] and not task.running
    assert counts(h)["commands"] == 1 and h.query("SELECT count(*) FROM events WHERE kind='DI_EDGE'")[0][0] == 1


def test_demo_policy_never_holds_or_deletes(h, cfg24):
    h.cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(update={"retention_provenance": "DEMO"})})
    seed(h, CUTOFF - timedelta(days=30), tag="old")
    task = _task(h, FakeClock(NOW.timestamp()))
    task.tick(AlarmEngine(h.cfg))
    assert task.held is None and not task.running and counts(h)["samples"] == 2
    assert task.view()["policy"] == "DEMO"


def test_process_and_run_registers_are_pruned_only_when_nothing_refers_to_them(h):
    old_end = iso(CUTOFF - timedelta(days=2))

    def sessions(c):
        c.execute("INSERT INTO process_sessions VALUES('gone',?,?,'c','v','SIMULATED','x',1)",
                  (iso(CUTOFF - timedelta(days=3)), old_end))
        c.execute("INSERT INTO sim_runs VALUES('run_gone','gone',1,'c','v',?,'DEMO',NULL)", (old_end,))
        c.execute("INSERT INTO process_sessions VALUES('crashed',?,NULL,'c','v','SIMULATED','x',0)",
                  (iso(CUTOFF - timedelta(days=1, hours=1)),))
        c.execute("INSERT INTO process_sessions VALUES('kept',?,?,'c','v','SIMULATED','x',1)",
                  (iso(CUTOFF - timedelta(days=1)), iso(NOW - timedelta(days=1))))
    h.submit(sessions, wait=True)
    seed(h, CUTOFF + timedelta(hours=1), tag="in")             # a row still inside the window
    res = prune_expired(h, now=NOW)
    left = {r[0] for r in h.query("SELECT session_id FROM process_sessions")}
    # 'crashed' ended before 'kept' started (sessions run one at a time) and before the oldest kept row
    assert res["deleted"]["process_sessions"] == 2 and res["deleted"]["sim_runs"] == 1
    assert left == {"kept", "s_ret"}                            # never the running session
    assert h.query("SELECT count(*) FROM sim_runs")[0][0] == 0


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
def test_archive_verify_and_tamper_detection(tmp_path, cfg24, monkeypatch):
    import aquacrop.historian.retention as ret
    monkeypatch.setattr(ret, "ARCHIVE_PAGE_ROWS", 10)             # bounded pages: several reads per file
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
    assert migrate(con, db) == list(range(2, CURRENT_VERSION + 1))
    assert schema_version(con) == CURRENT_VERSION
    row = con.execute("SELECT backup_path FROM migrations WHERE version=2").fetchone()
    assert row[0] and (tmp_path / "backups").exists()
    bk = sqlite3.connect(row[0])
    assert bk.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "1"
    assert bk.execute("SELECT kind FROM events").fetchone()[0] == "OLD"
    con.close()


def _v1_db(path):
    con = connect(path)
    con.execute("BEGIN")
    for stmt in [s for s in MIGRATIONS[1][1].split(";") if s.strip()]:
        con.execute(stmt)
    con.execute("INSERT INTO schema_meta VALUES('schema_version','1')")
    con.execute("INSERT INTO events(utc,kind,payload_json) VALUES('2026-10-01T00:00:00+00:00','OLD','{}')")
    con.execute("COMMIT")
    return con


def test_pre_migration_copy_is_verified_or_nothing_migrates(tmp_path, monkeypatch):
    from aquacrop.historian import backup as bk
    con = _v1_db(tmp_path / "old.sqlite3")
    real = bk._table_counts
    calls = []

    def short_copy(c):                       # the copy (second call) misses a row
        out = real(c)
        calls.append(out)
        if len(calls) == 2:
            out = dict(out, events=out["events"] - 1)
        return out
    monkeypatch.setattr(bk, "_table_counts", short_copy)
    with pytest.raises(bk.BackupRefused, match="failed verification: events"):
        migrate(con, tmp_path / "old.sqlite3")
    assert schema_version(con) == 1                  # nothing migrated
    assert not [p for p in (tmp_path / "backups").iterdir()]   # no unverified copy left behind
    monkeypatch.setattr(bk, "_table_counts", real)
    assert migrate(con, tmp_path / "old.sqlite3") == list(range(2, CURRENT_VERSION + 1))
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


def test_cli_retention_apply_deletes_only_with_the_confirmed_cutoff(tmp_path, cfg24, capsys):
    from aquacrop import cli
    real_now = datetime.now(timezone.utc)
    h = Historian(tmp_path / Path(cfg24.historian.db_path).name, cfg24, "old")
    seed(h, real_now - timedelta(days=400), tag="old")
    seed(h, real_now - timedelta(days=1), tag="new")
    h.close()
    args = ["retention", "apply", "--config", "demo-24", "--state-dir", str(tmp_path)]
    assert cli.main(args) == 3                                  # shows the proposal and deletes nothing
    prop = json.loads(capsys.readouterr().out)
    assert prop["would_delete"]["samples"] == 2 and prop["newest_record_utc"]
    assert cli.main(args + ["--confirm-cutoff", iso(real_now)]) == 4    # not the proposed cutoff
    capsys.readouterr()
    assert cli.main(args + ["--confirm-cutoff", prop["cutoff_utc"]]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["deleted"]["samples"] == 2
    h = Historian(tmp_path / Path(cfg24.historian.db_path).name, cfg24, "check")
    assert counts(h)["samples"] == 2
    row = h.query("SELECT actor, payload_json FROM events WHERE kind='RETENTION_CLOCK_CONFIRMED'")
    assert len(row) == 1 and row[0]["actor"].startswith("cli:")
    h.close()


# ---------------------------------------------------------------------------------------- WAL (owner review R1)
def test_wal_is_reported_separately_and_warned_about(h, cfg24):
    seed(h, NOW - timedelta(minutes=1), tag="w")
    st = h.storage()
    assert st["db_bytes"] == st["db_file_bytes"] + st["wal_bytes"] and st["wal_bytes"] > 0
    assert st["wal_warning_bytes"] == 256 * 2**20 and not st["wal_over_warning"]
    _with(h, cfg24, wal_warning_bytes=1)
    al = dict((r, m) for r, _, m in storage_alarms(h))
    assert "WAL" in al["STORAGE_CAPACITY"] and "long reader" in al["STORAGE_CAPACITY"]


def test_checkpoint_progress_shows_a_reader_holding_the_wal(h, monkeypatch):
    import aquacrop.historian.writer as w
    monkeypatch.setattr(w, "CHECKPOINT_EVERY_S", 0.0)
    h._next_checkpoint = 0.0                                        # the one scheduled at start-up
    seed(h, NOW - timedelta(minutes=3), tag="a")
    h.flush()
    assert h.wal_checkpoint and h.wal_checkpoint["behind_since_utc"] is None
    reader = sqlite3.connect(h.db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM samples").fetchone()      # an old snapshot, held open
    for i in range(3):
        seed(h, NOW - timedelta(minutes=2 - i), tag=f"b{i}")
    h.flush()
    ck = h.wal_checkpoint
    assert ck["checkpointed_frames"] < ck["wal_frames"] and ck["behind_since_utc"]
    reader.rollback()
    reader.close()
    h.flush()
    h.flush()
    assert h.wal_checkpoint["behind_since_utc"] is None


def test_pruning_is_bounded_and_waits_while_the_wal_is_held(h):
    for i in range(6):
        seed(h, CUTOFF - timedelta(days=1, minutes=i), tag=f"o{i}")
    res = prune_expired(h, now=NOW, wal_limit_bytes=1)              # WAL above the limit: nothing deleted
    assert not any(res["deleted"].values()) and "WAL" in res["incomplete"] and counts(h)["samples"] == 12
    assert _kinds(h)[-1] == "RETENTION_DEFERRED"
    res = prune_expired(h, now=NOW, chunk_rows=2, max_chunks=3)    # three transactions of two rows
    assert res["deleted"]["samples"] == 6 and res["transactions"] == 3 and "transactions" in res["incomplete"]
    res = prune_expired(h, now=NOW, chunk_rows=2)                   # the next run finishes the job
    assert counts(h)["samples"] == 0 and "incomplete" not in res


def test_capacity_check_does_not_wait_behind_a_long_user_query(h):
    """The acquisition loop's storage check and the status page use their own read connection, so a
    year-range export holding the query connection never delays a poll cycle."""
    import threading
    import time as _time
    held, release = threading.Event(), threading.Event()

    def long_query():
        with h._read_lock:                                          # a user query in progress
            held.set()
            release.wait(10)
    t = threading.Thread(target=long_query)
    t.start()
    held.wait(5)
    tic = _time.monotonic()
    st = h.storage()
    h.load_open_alarms()
    h.newest_wall_label()
    assert _time.monotonic() - tic < 1.0 and st["db_bytes"] > 0
    release.set()
    t.join()


def test_per_poll_status_queries_are_index_lookups_not_scans(h):
    """The capacity check runs on every poll cycle and the status page: none of its queries may scan
    a table or a whole index (a combined min()/max() took 2.4 s on a one-year database)."""
    seed(h, NOW - timedelta(minutes=1), tag="p")
    sqls = []
    con = sqlite3.connect(h.db_path)
    con.set_trace_callback(sqls.append)
    from aquacrop.historian.writer import capacity_report, newest_wall_label
    capacity_report(con, h.db_path, h.cfg)
    newest_wall_label(con)
    con.set_trace_callback(None)
    selects = [q for q in sqls if q.lstrip().upper().startswith("SELECT")]
    assert selects
    for q in selects:
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q))
        assert "SCAN" not in plan, (q, plan)   # the newest-label walk is a bounded rowid search
    # The walk really does stop: its cost does not grow with the number of earlier snapshots.
    steps = []

    def cost():
        n = [0]
        con.set_progress_handler(lambda: n.__setitem__(0, n[0] + 1), 1)
        newest_wall_label(con)
        con.set_progress_handler(None, 1)
        return n[0]
    steps.append(cost())
    con.executemany("INSERT INTO cycles(cycle_id,session_id,persisted_utc,n_points,cadence_basis) "
                    "VALUES(?,?,?,0,'WALL')", [(f"x{i}", "s", iso(NOW - timedelta(days=30, minutes=i)))
                                                for i in range(20000)])
    con.execute("INSERT INTO cycles(cycle_id,session_id,persisted_utc,n_points,cadence_basis) "
                "VALUES('newest','s',?,0,'WALL')", (iso(NOW),))
    steps.append(cost())
    con.rollback()
    assert steps[1] <= steps[0] + 20, steps
    con.close()
