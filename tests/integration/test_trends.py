"""OP-03: aggregated year-range trends (historian/trends.py) and the cache of final buckets."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aquacrop.historian.trends import SETTLE_S, TrendCache, trend_buckets
from aquacrop.historian.writer import Historian

DAY = ("1 day", 86400, 10)


@pytest.fixture
def h(tmp_path, cfg24):
    hist = Historian(tmp_path / "db.sqlite3", cfg24, "s_trend")
    yield hist
    hist.close()


def add_hourly(h, start, end):
    rows, t = [], start
    while t < end:
        rows.append((t.isoformat(timespec="microseconds"), float(t.hour % 7)))
        t += timedelta(hours=1)

    def op(con):
        pk = con.execute("INSERT INTO cycles(cycle_id,session_id,run_id,sim_s,persisted_utc,n_points,cadence_s,"
                         "cadence_basis) VALUES(?,'old',NULL,NULL,?,1,3600,'WALL')", (f"c{start}", rows[0][0])).lastrowid
        con.executemany("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,origin,"
                        "source_profile,config_version,device_uid,bus_id,session_id,value) VALUES(?,'test.P',?,?,1,"
                        "'GOOD','REAL','p','v','d','b','old',?)", [(pk, a, a, v) for a, v in rows])
    h.submit(op, wait=True)


def reads(h, monkeypatch):
    """The sample ranges each trend query asked for."""
    seen, real = [], h.query

    def spy(sql, params=()):
        if "FROM samples" in sql:
            seen.append(params[-2:])
        return real(sql, params)
    monkeypatch.setattr(h, "query", spy)
    return seen


def test_cached_year_chart_equals_a_full_read_and_reads_only_what_can_change(h, monkeypatch):
    now = datetime(2026, 10, 13, 9, 17, 23, tzinfo=timezone.utc)
    add_hourly(h, now - timedelta(days=400), now)
    cache = TrendCache()
    seen = reads(h, monkeypatch)
    s, e = (now - timedelta(days=365)).isoformat(), now.isoformat()
    full = trend_buckets(h, "test.P", s, e, DAY, now=now)
    assert len(full) == 366 and full[0][13] < 24 and full[-1][13] == 9      # partial first and last day
    assert trend_buckets(h, "test.P", s, e, DAY, cache=cache, now=now) == full
    seen.clear()
    assert trend_buckets(h, "test.P", s, e, DAY, cache=cache, now=now) == full
    # the second refresh reads the cut first day and today only, never the year in between
    assert seen == [(s, "2025-10-14T00:00:00+00:00"), ("2026-10-13T00:00:00+00:00", e)]
    # two days later, with new samples: still the same as a full read, and only the new days are read
    later = now + timedelta(days=2)
    add_hourly(h, now, later)
    s2, e2 = (later - timedelta(days=365)).isoformat(), later.isoformat()
    seen.clear()
    assert trend_buckets(h, "test.P", s2, e2, DAY, cache=cache, now=later) == trend_buckets(h, "test.P", s2, e2, DAY, now=later)
    assert ("2026-10-13T00:00:00+00:00", "2026-10-15T00:00:00+00:00") in seen[:3]


def test_a_bucket_is_final_only_after_the_settling_time_and_a_quarantine_is_a_new_key(h, monkeypatch):
    now = datetime(2026, 10, 13, 0, SETTLE_S // 60 - 1, tzinfo=timezone.utc)   # just after midnight
    add_hourly(h, now - timedelta(days=30), now)
    cache = TrendCache()
    s, e = (now - timedelta(days=30)).isoformat(), now.isoformat()
    trend_buckets(h, "test.P", s, e, DAY, cache=cache, now=now)
    (lo, hi, _), = cache._d.values()
    assert hi == "2026-10-12T00:00:00+00:00"           # yesterday may still receive a late sample
    first = h.query("SELECT min(id), max(id) FROM samples")[0]
    monkeypatch.setattr(h, "quarantines", SimpleNamespace(items=[SimpleNamespace(ranges={"samples": (first[0], first[0] + 47)})]))
    seen = reads(h, monkeypatch)
    out = trend_buckets(h, "test.P", s, e, DAY, cache=cache, now=now)
    assert any(r[9] for r in out) and len(cache._d) == 2 and len(seen) == 3   # recomputed under the new key
    assert out == trend_buckets(h, "test.P", s, e, DAY, now=now)


def add_cycle(h, start, hours, run=None, basis="WALL", origin="REAL", value=1.0):
    def op(con):
        pk = con.execute("INSERT INTO cycles(cycle_id,session_id,run_id,sim_s,persisted_utc,n_points,cadence_s,"
                         "cadence_basis) VALUES(?,'old',?,NULL,?,1,3600,?)",
                         (f"c{start}{run}{basis}", run, start.isoformat(), basis)).lastrowid
        con.executemany("INSERT INTO samples(cycle_pk,point_id,acquired_utc,persisted_utc,sequence,quality,origin,"
                        "source_profile,config_version,device_uid,bus_id,session_id,sim_run_id,value) VALUES(?,'test.P',"
                        "?,?,1,'GOOD',?,'p','v','d','b','old',?,?)",
                        [(pk, a, a, origin, run, value) for a in
                         ((start + timedelta(hours=i)).isoformat(timespec="microseconds") for i in range(hours))])
    h.submit(op, wait=True)


def test_a_bucket_never_mixes_simulation_runs_or_the_simulation_time_recorder(h):
    day = datetime(2026, 10, 1, tzinfo=timezone.utc)
    add_cycle(h, day, 12, run="run_a", origin="SIMULATED", value=1.0)
    add_cycle(h, day + timedelta(hours=12), 12, run="run_b", origin="SIMULATED", value=9.0)
    add_cycle(h, day, 24, run="run_a", basis="SIMULATION_TIME", origin="SIMULATED", value=100.0)
    s, e = day.isoformat(), (day + timedelta(days=1)).isoformat()
    # the wall-clock chart: one row per run, the line broken between them, the recorder's samples left out
    rows = trend_buckets(h, "test.P", s, e, DAY)
    assert [(r[7], r[1], r[13], r[8]) for r in rows] == [("run_a", 1.0, 12, False), ("run_b", 9.0, 12, True)]
    # the recorder is its own series when asked for
    rows = trend_buckets(h, "test.P", s, e, DAY, basis="SIMULATION_TIME")
    assert [(r[7], r[1], r[13]) for r in rows] == [("run_a", 100.0, 24)]
