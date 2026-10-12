"""AT-QUA-01..07: timestamp quarantine after a clock correction (owner request of 2026-10-12, ADR-0011).

Clock corrections, quarantine, restart and retention are exercised together through the simulation
service (recorder, retention task and alarm engine on fake wall/monotonic clocks); every UTC stamp the
historian writes follows the fake wall clock."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import aquacrop.historian.writer as writer_mod
from aquacrop.historian.quarantine import QUARANTINE_EVENT, QuarantineMismatch, confirm, propose
from aquacrop.historian.retention import prune_expired
from aquacrop.historian.writer import Historian, newest_wall_label

T0 = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0


class Clock:
    def __init__(self, wall):
        self.wall, self.mono = float(wall), 1000.0

    def w(self):
        return self.wall

    def m(self):
        return self.mono

    def run(self, seconds):
        self.wall += seconds
        self.mono += seconds


@pytest.fixture
def clk(monkeypatch):
    c = Clock(T0 - 0.25)
    # every stamp the historian writes (events, sessions, persisted times) follows the fake wall clock
    monkeypatch.setattr(writer_mod, "utc_iso",
                        lambda dt=None: (dt or datetime.fromtimestamp(c.wall, timezone.utc)).isoformat(
                            timespec="microseconds"))
    import importlib
    for name in ("twin.runtime", "io.virtual", "control.alarms", "control.commands", "api.service"):
        mod = importlib.import_module(f"aquacrop.{name}")
        if hasattr(mod, "utcnow"):          # acquisition stamps follow the fake wall clock too
            monkeypatch.setattr(mod, "utcnow", lambda: datetime.fromtimestamp(c.wall, timezone.utc))
    return c


def _service(db, cfg, sid, clk):
    from aquacrop.api.service import SimulationService
    h = Historian(db, cfg, sid)
    return h, SimulationService(cfg, h, sid, wall=clk.w, monotonic=clk.m)


def _run(svc, clk, seconds, step=0.5):
    for _ in range(int(round(seconds / step))):
        clk.run(step)
        svc.drive_step()


def _cycles(h):
    h.wait_idle()
    return [r[0] for r in h.query("SELECT cycle_id FROM cycles WHERE cadence_basis='WALL' ORDER BY cycle_pk")]


def _label(cycle_id):
    return datetime.fromisoformat(cycle_id.rsplit("/", 1)[-1]).timestamp()


def _prop(h, wall):
    h.wait_idle()
    return h.read(lambda c: propose(c, h.quarantines, wall_s=wall))


def test_forward_step_then_correction_quarantine_restart_and_retention(tmp_path, cfg24, clk):
    db = tmp_path / "q.sqlite3"
    h, svc = _service(db, cfg24, "s1", clk)
    _run(svc, clk, 180)                                   # T0, T0+60, T0+120: correct clock
    good = _cycles(h)
    assert len(good) == 3 and svc.retention.held is None
    clk.wall += 2 * DAY                                   # the clock jumps two days ahead
    _run(svc, clk, 300)
    future = _cycles(h)[3:]
    assert len(future) == 5 and all(_label(c) > T0 + DAY for c in future)
    assert svc.retention.held["code"] == "CLOCK_STEP_FORWARD"
    clk.wall -= 2 * DAY                                   # corrected back: new labels would be "in the past"
    _run(svc, clk, 180)
    assert svc.recorder.behind is not None and len(_cycles(h)) == 8           # snapshots held, nothing written
    assert svc.runtime.alarms.is_active("HISTORY_CLOCK_BEHIND", "historian")

    prop = _prop(h, clk.wall)
    assert prop["needed"] and prop["clock_epoch"] == 1
    assert prop["start"]["kind"] in ("HISTORY_SNAPSHOTS_MISSED", "RETENTION_HELD") and prop["end"]["kind"] == \
        "HISTORY_CLOCK_BEHIND"
    pks = [r[0] for r in h.query("SELECT cycle_pk FROM cycles ORDER BY cycle_pk")]
    assert prop["ranges"]["cycles"] == [pks[3], pks[7]]  # exactly the five future-dated snapshots
    assert prop["counts"]["samples"] == 5 * 24
    ev = h.query("SELECT id, utc FROM events WHERE id BETWEEN ? AND ?", tuple(prop["ranges"]["events"]))
    assert ev and all(datetime.fromisoformat(r[1]).timestamp() > T0 + DAY for r in ev)   # only wrong-clock events
    before = {t: h.query(f"SELECT count(*) FROM {t}")[0][0] for t in ("cycles", "samples", "events", "commands")}
    stamps = h.query("SELECT id, acquired_utc, value FROM samples ORDER BY id")

    with pytest.raises(ValueError):                       # the date check is part of the confirmation
        confirm(h, prop["proposal_id"], actor="admin1", wall_s=clk.wall, clock_checked=False)
    with pytest.raises(QuarantineMismatch):
        confirm(h, "0" * 16, actor="admin1", wall_s=clk.wall, clock_checked=True)
    q = confirm(h, prop["proposal_id"], actor="admin1", wall_s=clk.wall, clock_checked=True)
    svc.retention.hold_for_quarantine(q.quarantine_id)
    svc.recorder.rebase("test")
    # nothing deleted or rewritten
    assert {t: h.query(f"SELECT count(*) FROM {t}")[0][0] for t in ("cycles", "samples")} == \
        {"cycles": before["cycles"], "samples": before["samples"]}
    assert [tuple(r) for r in h.query("SELECT id, acquired_utc, value FROM samples ORDER BY id")] == \
        [tuple(r) for r in stamps]
    row = h.query("SELECT actor, payload_json FROM events WHERE kind=?", (QUARANTINE_EVENT,))[0]
    pl = json.loads(row[1])
    assert row[0] == "admin1" and pl["clock_checked_by_administrator"] and pl["clock_epoch"] == 1

    _run(svc, clk, 125)                                   # recording resumes in clock epoch 1
    ids = _cycles(h)
    assert len(ids) == 11 and all(i.startswith("slot/60s/e1/") for i in ids[8:])
    assert not svc.runtime.alarms.is_active("HISTORY_CLOCK_BEHIND", "historian")
    assert svc.retention.held is not None                 # quarantine does not release retention
    assert _prop(h, clk.wall)["needed"] is False          # nothing left to quarantine

    # restart: the epoch and the hold survive, recording continues after the newest trusted label
    newest = newest_wall_label(h._status, h.quarantines)
    h.close()
    h, svc = _service(db, cfg24, "s2", clk)
    assert h.quarantines.clock_epoch == 1 and newest_wall_label(h._status, h.quarantines) == newest
    _run(svc, clk, 65)
    ids = _cycles(h)
    assert len(ids) == 12 and ids[-1].startswith("slot/60s/e1/") and svc.recorder.behind is None
    assert svc.retention.held["code"] == "TIMESTAMPS_QUARANTINED"
    assert "before this process started" in svc.retention.held["reason"]
    assert h.health["labels_refused"] == 0

    # retention: released only by a separate confirmation; quarantined rows follow the quarantine time
    p = svc.retention.proposal()
    assert p["would_delete"]["samples"] == 0
    svc.retention.confirm("admin1", p["cutoff_utc"])
    assert svc.retention.held is None
    qtime = datetime.fromisoformat(q.confirmed_utc)        # about T0 + 11 min, a trusted time
    t0 = datetime.fromtimestamp(T0, timezone.utc)
    prune_expired(h, now=t0 + timedelta(days=365, minutes=5))   # cutoff T0 + 5 min
    left = {r[0] for r in h.query("SELECT cycle_pk FROM cycles")}
    assert not left & set(pks[:3])                             # trusted rows older than the window went
    assert set(pks[3:8]) <= left                               # quarantined rows stay: the quarantine is younger
    prune_expired(h, now=qtime + timedelta(days=365, minutes=1))
    left = {r[0] for r in h.query("SELECT cycle_pk FROM cycles")}
    assert not left & set(pks[3:8])    # gone a year after the quarantine, not a year after their (future) stamps
    assert h.query("SELECT count(*) FROM samples WHERE id BETWEEN ? AND ?", tuple(prop["ranges"]["samples"]))[0][0] == 0
    assert h.query("SELECT count(*) FROM events WHERE kind=?", (QUARANTINE_EVENT,))[0][0] == 1   # register kept
    h.close()


def test_clock_wrong_from_boot_corrected_at_restart(tmp_path, cfg24, clk):
    """A process that started with its clock a day ahead (no step seen) and a restart with the right
    clock: the whole earlier process is proposed, up to the start of the corrected one."""
    db = tmp_path / "b.sqlite3"
    h, svc = _service(db, cfg24, "s0", clk)
    _run(svc, clk, 120)
    h.close()
    clk.wall += DAY
    h, svc = _service(db, cfg24, "s1", clk)               # wrong from boot (+1 day, looks like downtime)
    _run(svc, clk, 180)
    h.close()
    clk.wall -= DAY
    h, svc = _service(db, cfg24, "s2", clk)               # restarted with the correct clock
    _run(svc, clk, 10)
    # s1's unconfirmed downtime hold is restored (holds survive restarts); the recorder sees the clock
    # behind s1's labels and records where the correction is
    assert svc.retention.held["code"] == "UNVERIFIED_DOWNTIME" and svc.recorder.behind is not None
    prop = _prop(h, clk.wall)
    assert prop["start"]["kind"] == "PROCESS_START" and prop["start"]["session_id"] == "s1"
    assert prop["end"]["kind"] == "HISTORY_CLOCK_BEHIND" and prop["end"]["session_id"] == "s2"
    s1 = [r[0] for r in h.query("SELECT cycle_pk FROM cycles WHERE session_id='s1' ORDER BY cycle_pk")]
    assert prop["ranges"]["cycles"] == [s1[0], s1[-1]] and prop["sessions"] == ["s1"]
    ev_sessions = {r[0] for r in h.query("SELECT DISTINCT session_id FROM events WHERE id BETWEEN ? AND ?",
                                         tuple(prop["ranges"]["events"]))}
    assert ev_sessions == {"s1"}                           # the corrected process's own events are not included
    q = confirm(h, prop["proposal_id"], actor="admin1", wall_s=clk.wall, clock_checked=True)
    svc.retention.hold_for_quarantine(q.quarantine_id)
    svc.recorder.rebase("test")
    _run(svc, clk, 125)
    assert _cycles(h)[-1].startswith("slot/60s/e1/") and svc.recorder.behind is None
    h.close()
    # after the quarantine, a restart compares the clock with trusted records only
    h, svc = _service(db, cfg24, "s3", clk)
    _run(svc, clk, 10)
    assert svc.retention.held["code"] == "TIMESTAMPS_QUARANTINED"   # still held until confirmed
    p = svc.retention.proposal()
    svc.retention.confirm("admin1", p["cutoff_utc"])
    h.close()
    h, svc = _service(db, cfg24, "s4", clk)
    _run(svc, clk, 10)
    assert svc.retention.held is None                      # confirmed; s1's future stamps no longer count
    h.close()


def test_label_identity_is_epoch_and_label(tmp_path, cfg24, clk):
    """A correctly timed label equal to a quarantined one is a new snapshot in the new epoch; the same
    label twice in one epoch is refused."""
    from aquacrop.historian.recording import slot_clock_epoch
    h = Historian(tmp_path / "l.sqlite3", cfg24, "s")
    at = datetime.fromtimestamp(T0 + DAY, timezone.utc)
    clk.wall = T0 + DAY
    h.snapshot(at, [], 60)                                 # future-dated label written with a wrong clock
    h.wait_idle()
    clk.wall = T0
    prop = _prop(h, clk.wall)
    confirm(h, prop["proposal_id"], actor="a", wall_s=clk.wall, clock_checked=True)
    h.snapshot(datetime.fromtimestamp(T0 + 60, timezone.utc), [], 60)
    h.snapshot(at, [], 60)                                 # the same label, now correctly timed: accepted
    h.snapshot(at, [], 60)                                 # a genuine duplicate in epoch 1: refused
    h.wait_idle()
    ids = [r[0] for r in h.query("SELECT cycle_id FROM cycles ORDER BY cycle_pk")]
    assert [slot_clock_epoch(i) for i in ids] == [0, 1, 1] and _label(ids[0]) == _label(ids[2])
    assert h.health["labels_refused"] == 1
    h.close()


def test_nothing_to_quarantine_without_future_stamps(tmp_path, cfg24, clk):
    h = Historian(tmp_path / "n.sqlite3", cfg24, "s")
    h.snapshot(datetime.fromtimestamp(T0, timezone.utc), [], 60)
    prop = _prop(h, T0 + 60)
    assert prop["needed"] is False
    with pytest.raises(LookupError):
        confirm(h, "0" * 16, actor="a", wall_s=T0 + 60, clock_checked=True)
    assert h.query("SELECT count(*) FROM events WHERE kind=?", (QUARANTINE_EVENT,))[0][0] == 0
    h.close()


def test_proposal_never_reaches_into_the_running_process(tmp_path, cfg24):
    """A later process (the one running while an administrator reads the proposal) keeps writing; its
    records are never proposed, and its own clock marker does not change the proposal."""
    db = tmp_path / "r.sqlite3"
    fut = datetime.now(timezone.utc) + timedelta(days=1)
    h = Historian(db, cfg24, "earlier")
    h.submit(lambda c: c.execute("INSERT INTO events(utc,kind,session_id,payload_json) VALUES(?,'DI_EDGE',"
                                 "'earlier','{}')", (fut.isoformat(),)), wait=True)
    h.close()
    h = Historian(db, cfg24, "running")
    h.event("TEST_EVENT", {}, actor="t")
    h.wait_idle()
    first_running = h.query("SELECT min(id) FROM events WHERE session_id='running'")[0][0]
    wall = datetime.now(timezone.utc).timestamp()
    p1 = h.read(lambda c: propose(c, h.quarantines, wall_s=wall))
    assert p1["needed"] and p1["ranges"]["events"][1] < first_running
    h.event("HISTORY_CLOCK_BEHIND", {"behind_s": 86400}, actor="system")
    h.event("TEST_EVENT", {}, actor="t")
    h.wait_idle()
    p2 = h.read(lambda c: propose(c, h.quarantines, wall_s=wall))
    assert p2["proposal_id"] == p1["proposal_id"]
    h.close()


def test_api_quarantine_is_admin_only_audited_and_flags_charts_and_exports(tmp_path, cfg48):
    """AT-QUA-06/07 through the real app: proposal, permissions, date check, and SUSPECT flags in
    trends, sample export, audit export, events and command history."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from tests.integration.test_api import ORIGIN, PW, _app, login
    db = tmp_path / Path(cfg48.historian.db_path).name
    h = Historian(db, cfg48, "earlier")
    fut = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(days=2)
    ts = fut.isoformat(timespec="microseconds")

    def op(c):                                 # what a process with its clock two days ahead left behind
        pk = c.execute("INSERT INTO cycles(cycle_id,session_id,persisted_utc,n_points,cadence_s,cadence_basis) "
                       "VALUES(?,?,?,1,60,'WALL')", (f"slot/60s/{fut.isoformat()}", "earlier", ts)).lastrowid
        c.execute("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,value,origin,"
                  "source_profile,config_version,device_uid,bus_id,session_id) VALUES(?,'analog_A.AI1',?,?,1,'GOOD',"
                  "4.5,'SIMULATED','p','v','analog_A','sim','earlier')", (pk, ts, ts))
        c.execute("INSERT INTO events(utc,kind,session_id,payload_json) VALUES(?,'DI_EDGE','earlier','{}')", (ts,))
        c.execute("INSERT INTO commands(request_id,actor,target,desired,origin,issued_utc,expires_utc,"
                  "expected_config_version,priority,backend,session_id,intent_utc,outcome) VALUES('c-fut','u',"
                  "'relay_A.DO1',1,'SIMULATED',?,?,'v',5,'SIMULATED','earlier',?,'READBACK_CONFIRMED')", (ts, ts, ts))
    h.submit(op, wait=True)
    h.close()
    with TestClient(app := _app(tmp_path, cfg48), base_url=ORIGIN) as c:
        login(c, "view1", PW["view1"])
        st = c.get("/api/history/quarantine").json()
        prop = st["proposal"]
        assert prop["needed"] and prop["counts"]["samples"] == 1 and st["clock_epoch"] == 0
        op_h = login(c, "oper1", PW["oper1"])
        body = {"proposal_id": prop["proposal_id"], "clock_checked": True}
        assert c.post("/api/history/quarantine", json=body, headers=op_h).status_code == 403
        adm = login(c, "admin1", PW["admin1"])
        assert c.post("/api/history/quarantine", json=body | {"clock_checked": False}, headers=adm).status_code == 422
        r = c.post("/api/history/quarantine", json=body | {"proposal_id": "0" * 16}, headers=adm)
        assert r.status_code == 409 and r.json()["detail"]["proposal"]["proposal_id"] == prop["proposal_id"]
        r = c.post("/api/history/quarantine", json=body, headers=adm)
        held = r.json()["retention_held"]       # supersedes the startup hold it answers; still held
        assert r.status_code == 200 and held["code"] == "TIMESTAMPS_QUARANTINED" and "CLOCK_BEHIND_STORED" in \
            held["reason"]
        assert c.get("/api/history/quarantine").json()["clock_epoch"] == 1
        app.state.hist.wait_idle()
        rng = f"&start={(fut - timedelta(minutes=5)).isoformat()}&end={(fut + timedelta(minutes=5)).isoformat()}"
        rng = rng.replace("+", "%2B")
        tr = c.get("/api/trends?points=analog_A.AI1" + rng).json()
        i = tr["columns"].index("timestamp_suspect")
        assert [row[i] for row in tr["series"]["analog_A.AI1"]] == [True]
        csv_text = c.get("/api/export.csv?points=analog_A.AI1" + rng).text
        assert "timestamp_suspect" in csv_text and ",SUSPECT,q_" in csv_text
        audit = c.get("/api/export/audit.csv?" + rng[1:]).text
        assert "DI_EDGE" in audit and ",SUSPECT,q_" in audit
        evs = c.get("/api/events?kind=DI_EDGE" + rng).json()["events"]
        assert evs and all(e["timestamp_suspect"] for e in evs)
        cmds = [x for x in c.get("/api/commands/history").json() if x["request_id"] == "c-fut"]
        assert cmds[0]["timestamp_suspect"] is True
        # values and stamps are as recorded
        row = app.state.hist.query("SELECT acquired_utc, value FROM samples WHERE point_id='analog_A.AI1' "
                                   "AND session_id='earlier'")[0]
        assert row[0] == ts and row[1] == 4.5
