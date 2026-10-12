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


# ---------------------------------------------------------------------------------------- recorder timing
class Clock:
    """Wall and monotonic clocks moved by the test. `run(s)` advances both together."""

    def __init__(self, wall):
        self.wall, self.mono = float(wall), 1000.0

    def w(self):
        return self.wall

    def m(self):
        return self.mono

    def run(self, seconds):
        self.wall += seconds
        self.mono += seconds


def _recorder(clk, interval, *, stored=None, ok=True):
    written, events = [], []
    rec = HistoryRecorder(interval, lambda at, iv: (written.append(at), ok)[1],
                          on_event=lambda kind, payload: events.append((kind, payload)),
                          high_water=lambda: stored, wall=clk.w, monotonic=clk.m)
    rec.start(thread=False)
    return rec, written, events


def _drive(rec, clk, seconds, step=0.25):
    for _ in range(int(round(seconds / step))):
        clk.run(step)
        rec.tick()


@pytest.mark.parametrize("interval", [60, 300])
def test_recorder_labels_wall_boundaries_once_on_a_monotonic_schedule(interval):
    clk = Clock(1_000_000 * interval + 0.5 * interval)   # halfway through a period
    rec, written, events = _recorder(clk, interval)
    assert not rec.tick()                                  # a start never back-fills the current period
    _drive(rec, clk, 0.5 * interval - 0.5)
    assert written == []
    _drive(rec, clk, 3 * interval)                         # three labels in three intervals, no more
    assert [w.timestamp() for w in written] == [(1_000_001 + i) * interval for i in range(3)]
    assert all(w.second == 0 and w.minute % (interval // 60) == 0 for w in written)   # hh:mm:00 / hh:05:00
    assert events == [] and rec.health()["ok"]


def test_300_s_labels_are_five_minute_boundaries():
    clk = Clock(datetime(2026, 10, 12, 10, 2, 17, tzinfo=timezone.utc).timestamp())
    rec, written, events = _recorder(clk, 300)
    _drive(rec, clk, 1200)
    assert [w.strftime("%H:%M:%S") for w in written] == ["10:05:00", "10:10:00", "10:15:00", "10:20:00"]


def test_forward_clock_step_writes_one_snapshot_and_logs_the_label_gap():
    clk = Clock(1_000_000 * 60 + 30)
    rec, written, events = _recorder(clk, 60)
    _drive(rec, clk, 60)                                   # writes label +1
    clk.wall += 3600                                       # NTP steps the wall clock forward one hour
    _drive(rec, clk, 120)
    assert len(written) == 3                               # one per interval of monotonic time: no burst
    kinds = [k for k, _ in events]
    assert kinds == ["HISTORY_SNAPSHOTS_MISSED"]
    ev = events[0][1]
    assert ev["cause"] == "CLOCK_STEP_FORWARD" and ev["labels"] == 60 and ev["clock_step_labels"] == 60
    assert (written[1] - written[0]).total_seconds() == 3660   # the label gap stays visible, nothing invented


def test_stalled_process_logs_missed_labels_without_back_fill():
    clk = Clock(1_000_000 * 60 + 30)
    rec, written, events = _recorder(clk, 60)
    _drive(rec, clk, 60)
    clk.run(5 * 60)                                        # the process was blocked for five minutes
    rec.tick()
    assert len(written) == 2 and (written[1] - written[0]).total_seconds() == 300
    (kind, ev), = events
    assert kind == "HISTORY_SNAPSHOTS_MISSED" and ev["cause"] == "STALLED" and ev["labels"] == 4


def test_backward_clock_step_holds_until_the_clock_passes_the_newest_label():
    clk = Clock(1_000_000 * 60 + 30)
    rec, written, events = _recorder(clk, 60)
    _drive(rec, clk, 120)                                  # labels +1, +2
    newest = written[-1]
    clk.wall -= 30                                         # small correction: next label just comes later
    _drive(rec, clk, 120)
    assert len(written) == 4 and events == []
    clk.wall -= 600                                        # large correction: the clock is behind stored labels
    _drive(rec, clk, 300)
    assert len(written) == 4 and rec.behind is not None and not rec.health()["ok"]
    assert [k for k, _ in events] == ["HISTORY_CLOCK_BEHIND"]
    _drive(rec, clk, 400)                                  # real time passes the newest label again
    assert rec.behind is None
    kinds = [k for k, _ in events]
    assert kinds == ["HISTORY_CLOCK_BEHIND", "HISTORY_CLOCK_RESUMED"]
    held = events[1][1]["held_intervals"]
    assert held >= 5
    labels = [w.timestamp() for w in written]
    assert labels == sorted(set(labels)) and newest in written          # never twice, never out of order


def test_restart_continues_after_the_newest_stored_label():
    label = 1_000_000 * 60.0
    # restart in the same period as the label the previous process wrote: no duplicate, no gap
    clk = Clock(label + 0.2)
    rec, written, events = _recorder(clk, 60, stored=label)
    _drive(rec, clk, 61)
    assert [w.timestamp() for w in written] == [label + 60] and events == []
    # restart exactly on a boundary the previous process did not reach: that label is reported missed
    clk = Clock(label)
    rec, written, events = _recorder(clk, 60, stored=label - 60)
    _drive(rec, clk, 61)
    assert [w.timestamp() for w in written] == [label + 60]
    (kind, ev), = events
    assert kind == "HISTORY_SNAPSHOTS_MISSED" and ev["cause"] == "NOT_RUNNING" and ev["labels"] == 1
    # restart with the clock behind the stored label: held, not duplicated
    clk = Clock(label - 300)
    rec, written, events = _recorder(clk, 60, stored=label)
    _drive(rec, clk, 120)
    assert written == [] and rec.behind is not None


def test_labels_not_written_for_low_disk_are_not_reported_again_as_missed():
    clk = Clock(1_000_000 * 60 + 30)
    rec, written, events = _recorder(clk, 60, ok=False)
    _drive(rec, clk, 180)
    assert len(written) == 3 and rec.snapshots == 0 and rec.not_written == 3 and events == []


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
        assert c["cycle_id"].startswith("slot/0.5s/")
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
    at = datetime.fromisoformat(first.split("/", 2)[2]).timestamp()
    assert at > t0 - 1e-6 and abs(at / 0.5 - round(at / 0.5)) < 1e-6   # a new boundary after restart
    for t, n in before.items():
        assert h2.query(f"SELECT count(*) FROM {t} WHERE rowid <= ?", (10**12,))[0][0] >= n
    assert h2.query("SELECT count(*) FROM samples WHERE session_id='sess-1'")[0][0] == before["samples"]
    assert h2.query("SELECT count(*) FROM alarm_occurrences")[0][0] == 1
    h2.close()


# ---------------------------------------------------------------------------------------- durable labels
def test_a_label_is_stored_once_per_database_across_sessions(tmp_path, cfg24):
    """Two historian sessions on one database (a restart overlapping the old process, or two processes):
    the label check runs inside the writer transaction, so the second write of a label is refused."""
    db = tmp_path / "d.sqlite3"
    h1, h2 = Historian(db, cfg24, "p1"), Historian(db, cfg24, "p2")
    rt = SimulationRuntime(cfg24, session_id="p1")
    at = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    snap = rt.wall_snapshot(at, 0.0)
    h1.snapshot(at, snap, 60)
    h1.wait_idle()
    h2.snapshot(at, snap, 60)                               # same label from the other session
    h2.snapshot(at - timedelta(minutes=5), snap, 60)         # an older label (clock behind)
    h2.wait_idle()
    assert h2.newest_wall_label() == at.timestamp()
    rows = h2.query("SELECT cycle_id, session_id FROM cycles")
    assert [(r["cycle_id"], r["session_id"]) for r in rows] == [("slot/60s/2026-10-12T10:00:00+00:00", "p1")]
    assert h2.health["labels_refused"] == 2
    assert h2.query("SELECT count(*) FROM events WHERE kind='HISTORY_LABEL_REFUSED'")[0][0] == 2
    h2.snapshot(at + timedelta(minutes=1), snap, 60)
    h2.wait_idle()
    assert h2.query("SELECT count(*) FROM cycles")[0][0] == 2
    h1.close()
    h2.close()


# ---------------------------------------------------------------------------------------- simulation
def _service(db, cfg, sid, clk):
    from aquacrop.api.service import SimulationService
    h = Historian(db, cfg, sid)
    return h, SimulationService(cfg, h, sid, wall=clk.w, monotonic=clk.m)


def _run(svc, clk, seconds, step=0.5):
    for _ in range(int(round(seconds / step))):
        clk.run(step)
        svc.drive_step()


LABEL = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("speed", [1, 10, 60])
def test_sim_records_on_wall_time_whatever_the_speed(tmp_path, cfg24, speed):
    """Equal wall duration => equal snapshot count and labels at 1x, 10x and 60x; the simulated time
    and run id are stored with each snapshot as provenance."""
    clk = Clock(LABEL - 0.25)
    h, svc = _service(tmp_path / "s.sqlite3", cfg24, "s", clk)
    svc.apply_speed(speed)
    _run(svc, clk, 60.5)                        # 60.5 s of wall time: crosses LABEL and LABEL + 60 s
    h.wait_idle()
    rows = h.query("SELECT cycle_id, cadence_s, cadence_basis, run_id, sim_s, n_points FROM cycles ORDER BY cycle_pk")
    assert [r["cycle_id"] for r in rows] == [f"slot/60s/{datetime.fromtimestamp(LABEL + 60 * i, timezone.utc).isoformat()}"
                                             for i in range(2)]
    assert all(r["cadence_s"] == 60 and r["cadence_basis"] == "WALL" and r["n_points"] == 24 for r in rows)
    assert {r["run_id"] for r in rows} == {svc.runtime.run_id}
    assert rows[1]["sim_s"] - rows[0]["sim_s"] == pytest.approx(60 * speed, abs=1.0)
    assert h.query("SELECT DISTINCT quality FROM samples WHERE point_id='analog_A.AI1'")[0][0] == "GOOD"
    assert svc.task_health()["recorder"]["snapshots"] == 2
    h.close()


def test_sim_pause_keeps_wall_snapshots_and_records_frozen_readings_as_stale(tmp_path, cfg24):
    clk = Clock(LABEL - 0.25)
    h, svc = _service(tmp_path / "s.sqlite3", cfg24, "s", clk)
    _run(svc, clk, 1)                           # label LABEL written while running
    svc.clock.pause()
    _run(svc, clk, 120)                         # two more labels while paused
    h.wait_idle()
    rows = h.query("SELECT c.sim_s, s.quality, s.reason, s.last_good_value, s.acquired_utc FROM cycles c "
                   "JOIN samples s ON s.cycle_pk=c.cycle_pk WHERE s.point_id='analog_A.AI1' ORDER BY c.cycle_pk")
    assert len(rows) == 3
    assert rows[0]["quality"] == "GOOD"
    for r in rows[1:]:
        assert r["quality"] == "STALE" and "paused" in r["reason"] and r["last_good_value"] is not None
        assert r["sim_s"] == svc.runtime.sim_s                            # simulated time stood still
    assert rows[1]["acquired_utc"] != rows[2]["acquired_utc"]             # each row carries its own label time
    svc.clock.resume()
    _run(svc, clk, 60)
    h.wait_idle()
    last = h.query("SELECT quality FROM samples WHERE point_id='analog_A.AI1' ORDER BY id DESC LIMIT 1")[0][0]
    assert last == "GOOD"
    h.close()


def test_sim_snapshot_marks_stalled_acquisition_stale(tmp_path, cfg24):
    clk = Clock(LABEL - 5)
    h, svc = _service(tmp_path / "s.sqlite3", cfg24, "s", clk)
    _run(svc, clk, 4)
    svc.runtime.acquisition_stalled = True      # the simulated acquisition stops; the plant keeps running
    _run(svc, clk, 2)                           # label written ~2 s of simulated time after the last reading
    h.wait_idle()
    q = {r[0] for r in h.query("SELECT quality FROM samples")}
    assert q == {"STALE"}
    r = h.query("SELECT * FROM samples WHERE point_id='analog_A.AI1'")[0]
    assert r["value"] is None and r["last_good_value"] is not None and "simulated time" in r["reason"]
    h.close()


def test_sim_restart_on_a_boundary_and_clock_corrections_never_duplicate_a_label(tmp_path, cfg24):
    from aquacrop.historian.retention import evaluate_recorder_alarm
    db = tmp_path / "s.sqlite3"
    clk = Clock(LABEL - 0.25)
    h, svc = _service(db, cfg24, "s1", clk)
    _run(svc, clk, 0.5)                         # writes LABEL
    h.close()
    # the next process starts in the same minute (a restart right on the boundary)
    h, svc = _service(db, cfg24, "s2", clk)
    _run(svc, clk, 60)                          # LABEL is not written again; LABEL+60 is
    clk.wall -= 600                             # the clock is corrected backwards by ten minutes
    _run(svc, clk, 120)
    assert svc.recorder.behind is not None
    assert svc.runtime.alarms.is_active("HISTORY_CLOCK_BEHIND", "historian")
    _run(svc, clk, 600)                         # real time catches up with the stored labels
    assert svc.recorder.behind is None and not svc.runtime.alarms.is_active("HISTORY_CLOCK_BEHIND", "historian")
    clk.wall += 3600                            # and later steps forward by an hour
    _run(svc, clk, 120)
    h.wait_idle()
    ids = [r[0] for r in h.query("SELECT cycle_id FROM cycles ORDER BY cycle_pk")]
    labels = [datetime.fromisoformat(i.split("/", 2)[2]).timestamp() for i in ids]
    assert labels == sorted(set(labels)) and labels[:2] == [LABEL, LABEL + 60]
    kinds = [r[0] for r in h.query("SELECT kind FROM events WHERE kind LIKE 'HISTORY_%' ORDER BY id")]
    assert kinds == ["HISTORY_CLOCK_BEHIND", "HISTORY_CLOCK_RESUMED", "HISTORY_SNAPSHOTS_MISSED"]
    missed = json.loads(h.query("SELECT payload_json FROM events WHERE kind='HISTORY_SNAPSHOTS_MISSED'")[0][0])
    assert missed["cause"] == "CLOCK_STEP_FORWARD" and missed["labels"] == 60
    assert h.health["labels_refused"] == 0     # the recorder never even offered a duplicate
    h.close()


def test_optional_sim_time_recorder_is_off_by_default_and_separately_labelled(tmp_path, cfg24):
    from tests.helpers import with_sim_time_recording
    h = Historian(tmp_path / "s.sqlite3", cfg24, "s")
    rt = SimulationRuntime(cfg24, audit=h, history=h, session_id="s")
    rt.run_for(600)
    h.wait_idle()
    assert h.query("SELECT count(*) FROM cycles")[0][0] == 0 and rt.sim_time_recording_s is None
    h.close()
    for interval, expected in ((60, 10), (300, 2)):
        cfg = with_sim_time_recording(cfg24, interval)
        h = Historian(tmp_path / f"t{interval}.sqlite3", cfg, "s")
        rt = SimulationRuntime(cfg, audit=h, history=h, session_id="s")
        rt.run_for(600)
        h.wait_idle()
        rows = h.query("SELECT cycle_id, sim_s, cadence_s, cadence_basis FROM cycles ORDER BY cycle_pk")
        assert len(rows) == expected
        assert all(r["cadence_basis"] == "SIMULATION_TIME" and r["cadence_s"] == interval
                   and r["cycle_id"].startswith(f"simtime/{rt.run_id}/") for r in rows)
        assert [round(r["sim_s"]) for r in rows] == [interval * (i + 1) for i in range(expected)]
        h.close()


def test_sim_alarm_transitions_and_ack_do_not_wait_for_a_snapshot(tmp_path, cfg24):
    h = Historian(tmp_path / "s.sqlite3", cfg24, "s")
    rt = _rt(cfg24, h)
    rt.set_override("op", "analog_A.AI1", 19800, "t")
    rt.run_for(2)
    h.wait_idle()
    assert h.query("SELECT count(*) FROM cycles")[0][0] == 0
    row = h.query("SELECT occurrence_id, acknowledged FROM alarm_occurrences WHERE rule_id='RESERVOIR_LEVEL_HIGH'")
    assert len(row) == 1 and row[0]["acknowledged"] == 0
    rt.acknowledge("op", "RESERVOIR_LEVEL_HIGH", "analog_A.AI1")   # no further simulation step
    h.wait_idle()
    assert h.query("SELECT acknowledged FROM alarm_occurrences")[0][0] == 1
    assert h.query("SELECT count(*) FROM events WHERE kind IN ('ALARM_RAISED','ALARM_ACKNOWLEDGED')")[0][0] == 2
    h.close()


def _rt(cfg, h):
    rt = SimulationRuntime(cfg, audit=h, history=h, session_id=h.session_id, start_permitted=h.can_accept_starts)
    h.register_run(rt.run_id, rt.seed, rt.params.provenance, "test")
    return rt


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
    from tests.helpers import with_sim_time_recording
    assert CURRENT_VERSION == 2                  # the schema delivered on main; 60 s/365 d needs no change
    db = tmp_path / "keep.sqlite3"
    cfg24 = with_sim_time_recording(cfg24)
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
