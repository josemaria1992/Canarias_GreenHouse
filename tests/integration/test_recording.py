"""AT-HIS-02..07: periodic snapshots decoupled from acquisition, immediate events, stale readings,
configuration events, restart and no schema migration (owner requirements of 2026-10-12, ADR-0008).

Hardware cases run the real HardwareService over the in-memory serial bus (software doubles only)."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aquacrop.config.schema import HistorianSettings
from aquacrop.historian.recording import HistoryRecorder, boundary_index, stale_limit_s
from aquacrop.historian.schema import CURRENT_VERSION
from aquacrop.historian.writer import Historian
from aquacrop.twin.runtime import SimulationRuntime


# ---------------------------------------------------------------------------------------- configuration
def test_recording_interval_is_60_s_and_only_60_or_300_allowed(cfg48, cfg24):
    assert HistorianSettings().recording_interval_s == 60
    assert cfg48.historian.recording_interval_s == cfg24.historian.recording_interval_s == 60
    assert HistorianSettings(recording_interval_s=300).recording_interval_s == 300
    for bad in (1, 3, 30, 59, 61, 120, 600):
        with pytest.raises(ValidationError):
            HistorianSettings(recording_interval_s=bad)
    assert cfg48.historian.retention_days == 365 and cfg48.historian.retention_provenance == "APPROVED"
    with pytest.raises(ValidationError):
        HistorianSettings(retention_days=364)
    assert HistorianSettings().db_budget_bytes == 10 * 2**30   # the old 2 GiB limit is gone


# ---------------------------------------------------------------------------------------- boundaries
class Clock:
    def __init__(self, wall):
        self.wall, self.mono = wall, 100.0

    def w(self):
        return self.wall

    def m(self):
        return self.mono


@pytest.mark.parametrize("interval", [60, 300])
def test_recorder_writes_each_wall_boundary_once_and_reports_skips(interval):
    clk = Clock(1_000_000 * interval + 0.5 * interval)   # halfway through a boundary period
    written, missed = [], []
    rec = HistoryRecorder(interval, lambda at, iv: written.append((at, iv)), on_missed=lambda n, why: missed.append(n),
                          wall=clk.w, monotonic=clk.m)
    rec._last_index = boundary_index(clk.wall, interval)   # what start() does, without the thread
    assert not rec.tick()                                  # a restart never back-fills the current period
    clk.wall = (1_000_001 * interval) - 0.001
    assert not rec.tick()
    clk.wall = 1_000_001 * interval                         # exactly on the boundary
    assert rec.tick() and not rec.tick()                    # written once
    assert written[-1][0] == datetime.fromtimestamp(1_000_001 * interval, tz=timezone.utc)
    assert written[-1][0].second == 0 and written[-1][1] == interval
    clk.wall -= 30                                          # wall clock stepped back: nothing rewritten
    assert not rec.tick()
    clk.wall = 1_000_005 * interval + 1                     # stepped forward over three boundaries
    assert rec.tick() and missed == [3] and len(written) == 2


def test_stale_limit_follows_the_bus_poll_rate():
    assert stale_limit_s(3.0) == 9.0 and stale_limit_s(0.2) == pytest.approx(1.2)


# ---------------------------------------------------------------------------------------- hardware
@pytest.fixture
def hw(fake_bus, tmp_path):
    from aquacrop.api.hardware import HardwareService
    cfg, bus, relay, ai = fake_bus
    hist = Historian(tmp_path / "hw.sqlite3", cfg, "sess-hw")
    made = []

    def make(**kw):
        svc = HardwareService(cfg, hist, "sess-hw", **kw)
        made.append(svc)
        return svc
    yield cfg, hist, make, bus, relay, ai
    for s in made:
        s.stop()
    hist.close()
    assert bus.write_frames() == []


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    return cond()


def test_hw_acquisition_keeps_its_rate_and_snapshots_are_separate_and_complete(hw):
    cfg, hist, make, bus, relay, ai = hw
    svc = make(recording_interval_s=0.5)       # test hook; installations use 60 s
    svc.start()
    time.sleep(2.6)
    polls = svc.workers["rs485_a"].polls
    svc.stop()                                 # freeze the counts before comparing them
    hist.wait_idle()
    cycles = hist.query("SELECT cycle_pk, cycle_id, cadence_s, cadence_basis, n_points FROM cycles")
    # Polling stays at 0.2 s (about 13 polls in 2.6 s) while the historian wrote about 5 snapshots.
    assert polls >= 9
    assert 3 <= len(cycles) <= 6 < polls
    n_points = len(cfg.all_points())
    for c in cycles:
        assert c["cadence_s"] == 0.5 and c["cadence_basis"] == "WALL" and c["n_points"] == n_points
        assert ":snap:" in c["cycle_id"]
    per = hist.query("SELECT cycle_pk, count(*) n, count(DISTINCT point_id) d FROM samples GROUP BY cycle_pk")
    assert all(r["n"] == r["d"] == n_points for r in per)
    # unchanged digital inputs are written in every snapshot, with their quality; a boundary that
    # falls before the first poll has returned (a slow first connect) records UNAVAILABLE, never a value
    q = hist.query("SELECT quality, reason FROM samples WHERE point_id='relay_A.DI1' ORDER BY cycle_pk")
    assert len(q) == len(cycles)
    first_good = next(i for i, r in enumerate(q) if r["quality"] == "GOOD")
    assert all(r["quality"] == "UNAVAILABLE" and r["reason"] == "no acquisition yet in this process"
               for r in q[:first_good])
    assert [r["quality"] for r in q[first_good:]] == ["GOOD"] * (len(q) - first_good) and len(q) - first_good >= 2
    assert svc.task_health()["recorder"]["snapshots"] == len(cycles)


def test_hw_edges_and_alarm_transitions_are_written_immediately(hw):
    cfg, hist, make, bus, relay, ai = hw
    svc = make()                                # real 60 s recording interval
    svc.start()
    assert _wait(lambda: svc.workers["rs485_a"].polls >= 2)
    relay.di[2] = True
    assert _wait(lambda: hist.query("SELECT count(*) FROM events WHERE kind='DI_EDGE'")[0][0] == 1, 2.0)
    relay.fault = "timeout"
    assert _wait(lambda: hist.query("SELECT count(*) FROM alarm_occurrences WHERE rule_id='DEVICE_COMM' "
                                    "AND active=1")[0][0] == 1, 3.0)
    ev = json.loads(hist.query("SELECT payload_json FROM events WHERE kind='DI_EDGE'")[0][0])
    assert ev["point"] == "relay_A.DI3" and ev["resolution_s"] == 0.2
    assert hist.query("SELECT count(*) FROM events WHERE kind='ALARM_RAISED'")[0][0] >= 1
    relay.fault = None
    assert _wait(lambda: hist.query("SELECT count(*) FROM alarm_occurrences WHERE rule_id='DEVICE_COMM' "
                                    "AND active=0")[0][0] == 1, 3.0)
    # none of this waited for a snapshot (unless the test happened to cross a minute boundary)
    assert hist.query("SELECT count(*) FROM cycles")[0][0] <= 1


def test_hw_snapshot_marks_stale_and_never_acquired_points(hw):
    cfg, hist, make, bus, relay, ai = hw
    svc = make()
    n = len(cfg.all_points())
    at = datetime.now(timezone.utc).replace(microsecond=0)
    svc.record_snapshot(at, 60)                 # before any poll
    hist.wait_idle()
    rows = hist.query("SELECT quality, reason, value FROM samples")
    assert len(rows) == n and {r["quality"] for r in rows} == {"UNAVAILABLE"}
    assert {r["reason"] for r in rows} == {"no acquisition yet in this process"}
    svc.poll_bus("rs485_a")
    time.sleep(stale_limit_s(cfg.buses[0].poll_interval_s) + 0.2)   # acquisition stalls (bus worker not running)
    svc.record_snapshot(at + timedelta(seconds=60), 60)
    hist.wait_idle()
    last = hist.query("SELECT point_id, quality, reason, value, raw_value, last_good_value, last_good_utc, "
                      "acquired_utc FROM samples WHERE cycle_pk=(SELECT max(cycle_pk) FROM cycles)")
    assert len(last) == n
    di = next(r for r in last if r["point_id"] == "relay_A.DI1")
    assert di["quality"] == "STALE" and di["value"] is None and di["raw_value"] is None
    assert di["last_good_value"] == 0.0 and di["last_good_utc"] and di["reason"].startswith("stale:")
    assert di["acquired_utc"] == (at + timedelta(seconds=60)).isoformat(timespec="microseconds")
    # a fresh poll makes the next snapshot current again
    svc.poll_bus("rs485_a")
    svc.record_snapshot(at + timedelta(seconds=120), 60)
    hist.wait_idle()
    q = hist.query("SELECT quality FROM samples WHERE point_id='relay_A.DI1' ORDER BY id DESC LIMIT 1")[0][0]
    assert q == "GOOD"


def test_hw_restart_preserves_history_and_resumes_on_next_boundary(fake_bus, tmp_path):
    from aquacrop.api.hardware import HardwareService
    cfg, bus, relay, ai = fake_bus
    db = tmp_path / "hw.sqlite3"
    h1 = Historian(db, cfg, "sess-1")
    h1.record_config(cfg)
    s1 = HardwareService(cfg, h1, "sess-1", recording_interval_s=0.5)
    s1.start()
    relay.fault = "timeout"                    # an alarm that is still open at shutdown
    assert _wait(lambda: h1.query("SELECT count(*) FROM alarm_occurrences WHERE active=1")[0][0] == 1, 3.0)
    assert _wait(lambda: h1.query("SELECT count(*) FROM cycles")[0][0] >= 2, 3.0)
    s1.stop()
    h1.close()
    import sqlite3
    con = sqlite3.connect(db)
    before = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("cycles", "samples", "events")}
    con.close()
    h2 = Historian(db, cfg, "sess-2")
    assert h2.applied_migrations == []          # same schema: nothing migrated, nothing rewritten
    assert h2.record_config(cfg)["changed"] is None
    s2 = HardwareService(cfg, h2, "sess-2", recording_interval_s=0.5)
    assert len(s2.alarms.open) == 1             # restored, not duplicated
    relay.fault = None
    t0 = time.time()
    s2.start()
    assert _wait(lambda: s2.recorder.snapshots >= 1, 3.0)
    s2.stop()
    h2.wait_idle()
    first = h2.query("SELECT cycle_id FROM cycles WHERE session_id='sess-2' ORDER BY cycle_pk LIMIT 1")[0][0]
    at = datetime.fromisoformat(first.split(":snap:")[1]).timestamp()
    assert at > t0 - 1e-6 and abs(at / 0.5 - round(at / 0.5)) < 1e-6   # a new boundary after restart
    for t, n in before.items():
        assert h2.query(f"SELECT count(*) FROM {t} WHERE rowid <= ?", (10**12,))[0][0] >= n
    assert h2.query("SELECT count(*) FROM samples WHERE session_id='sess-1'")[0][0] == before["samples"]
    assert h2.query("SELECT count(*) FROM alarm_occurrences")[0][0] == 1
    h2.close()


# ---------------------------------------------------------------------------------------- simulation
def _rt(cfg, h):
    rt = SimulationRuntime(cfg, audit=h, history=h, session_id=h.session_id, start_permitted=h.can_accept_starts)
    h.register_run(rt.run_id, rt.seed, rt.params.provenance, "test")
    return rt


@pytest.mark.parametrize("interval,expected", [(60, 10), (300, 2)])
def test_sim_snapshot_interval_in_simulated_time(tmp_path, cfg24, interval, expected):
    cfg = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(update={"recording_interval_s": interval})})
    h = Historian(tmp_path / "s.sqlite3", cfg, "s")
    rt = _rt(cfg, h)
    rt.run_for(600)
    h.wait_idle()
    rows = h.query("SELECT sim_s, cadence_s, n_points FROM cycles ORDER BY cycle_pk")
    assert len(rows) == expected
    assert all(r["cadence_s"] == interval and r["n_points"] == 24 for r in rows)
    assert [round(r["sim_s"]) for r in rows] == [interval * (i + 1) for i in range(expected)]
    h.close()


def test_sim_alarm_transitions_and_ack_do_not_wait_for_a_snapshot(tmp_path, cfg24):
    h = Historian(tmp_path / "s.sqlite3", cfg24, "s")
    rt = _rt(cfg24, h)
    rt.set_override("op", "analog_A.AI1", 19800, "t")
    rt.run_for(2)                                # far before the first 60 s snapshot
    h.wait_idle()
    assert h.query("SELECT count(*) FROM cycles")[0][0] == 0
    row = h.query("SELECT occurrence_id, acknowledged FROM alarm_occurrences WHERE rule_id='RESERVOIR_LEVEL_HIGH'")
    assert len(row) == 1 and row[0]["acknowledged"] == 0
    rt.acknowledge("op", "RESERVOIR_LEVEL_HIGH", "analog_A.AI1")   # no further simulation step
    h.wait_idle()
    assert h.query("SELECT acknowledged FROM alarm_occurrences")[0][0] == 1
    assert h.query("SELECT count(*) FROM events WHERE kind IN ('ALARM_RAISED','ALARM_ACKNOWLEDGED')")[0][0] == 2
    h.close()


def test_sim_snapshot_marks_stalled_acquisition_stale(tmp_path, cfg24):
    h = Historian(tmp_path / "s.sqlite3", cfg24, "s")
    rt = _rt(cfg24, h)
    rt.run_for(59)
    rt.acquisition_stalled = True
    rt.run_for(2)                                # snapshot at 60 s with readings ~1 s old
    h.wait_idle()
    q = {r[0] for r in h.query("SELECT quality FROM samples")}
    assert q == {"STALE"}
    r = h.query("SELECT * FROM samples WHERE point_id='analog_A.AI1'")[0]
    assert r["value"] is None and r["last_good_value"] is not None and r["sim_s"] == pytest.approx(60.0)
    h.close()


# ---------------------------------------------------------------------------------------- config and schema
def test_config_loaded_and_changed_events(tmp_path, cfg24):
    db = tmp_path / "c.sqlite3"
    h = Historian(db, cfg24, "s1")
    assert h.record_config(cfg24)["changed"] is None
    h.close()
    cfg300 = cfg24.model_copy(update={"historian": cfg24.historian.model_copy(update={"recording_interval_s": 300}),
                                      "config_version": "demo-2"})
    h = Historian(db, cfg300, "s2")
    ch = h.record_config(cfg300)["changed"]
    keys = {c["key"]: c for c in ch["changes"]}
    assert keys["historian.recording_interval_s"]["from"] == "60" and keys["historian.recording_interval_s"]["to"] == "300"
    assert keys["config_version"]["to"] == "demo-2"
    h.wait_idle()
    kinds = [r[0] for r in h.query("SELECT kind FROM events WHERE kind LIKE 'CONFIG_%' ORDER BY id")]
    assert kinds == ["CONFIG_LOADED", "CONFIG_LOADED", "CONFIG_CHANGED"]
    h.close()


def test_this_update_adds_no_schema_migration(tmp_path, cfg24):
    assert CURRENT_VERSION == 2                  # the schema delivered on main; 60 s/365 d needs no change
    db = tmp_path / "keep.sqlite3"
    h = Historian(db, cfg24, "s1")
    rt = _rt(cfg24, h)
    rt.run_for(120)
    h.wait_idle()
    n = h.query("SELECT count(*) FROM samples")[0][0]
    h.close()
    h = Historian(db, cfg24, "s2")
    assert h.applied_migrations == [] and h.query("SELECT count(*) FROM samples")[0][0] == n
    h.close()


def test_stale_sample_replace_keeps_traceability(cfg24):
    from aquacrop.domain.model import Origin, PointId, Quality
    from aquacrop.historian.recording import complete_snapshot
    from aquacrop.io.sampling import AcqContext, make_sample
    now = datetime.now(timezone.utc)
    ctx = AcqContext("s", "c")
    s = make_sample(cfg24, PointId.parse("relay_A.DI1"), True, acquired=now - timedelta(seconds=30), ctx=ctx,
                    origin=Origin.SIMULATED)
    out = complete_snapshot(cfg24, {"relay_A.DI1": s}, now=now, ctx=ctx, origin=Origin.SIMULATED,
                            stale_reason=lambda x: "stale: test")
    di = next(x for x in out if x.point_id == "relay_A.DI1")
    assert di.quality == Quality.STALE and di.sequence == s.sequence and di.last_good_value is True
    assert len(out) == len(cfg24.all_points())
