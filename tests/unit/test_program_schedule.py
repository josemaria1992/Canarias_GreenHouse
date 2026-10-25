"""0.5.0 weekly schedules in Europe/Stockholm: daylight-saving and clock-change policy (owner, 2026-10-24)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from aquacrop.programs import schedule as sc

TZ = "Europe/Stockholm"
ALL = list(sc.DAYS)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_a_start_in_the_spring_gap_runs_at_three_with_its_full_length():
    # 2027-03-28: 02:00 CET -> 03:00 CEST; 02:30 does not exist
    w = sc.windows("p@1", [{"days": ALL, "start": "02:30", "end": "03:00"}], TZ, date(2027, 3, 28), 1)
    assert len(w) == 1 and w[0].shifted == "SPRING_GAP"
    assert w[0].start_utc == utc(2027, 3, 28, 1, 0)          # 03:00 CEST
    assert w[0].duration_s == 30 * 60


def test_a_start_in_the_repeated_autumn_hour_runs_once_at_the_first():
    # 2026-10-25: 03:00 CEST -> 02:00 CET; 02:30 happens twice
    w = sc.windows("p@1", [{"days": ALL, "start": "02:30", "end": "03:30"}], TZ, date(2026, 10, 25), 1)
    assert len(w) == 1 and w[0].start_utc == utc(2026, 10, 25, 0, 30) and w[0].duration_s == 3600


def test_window_length_is_the_written_length_on_change_days():
    for d in (date(2026, 10, 25), date(2027, 3, 28), date(2026, 11, 2)):
        w = sc.windows("p@1", [{"days": ALL, "start": "01:30", "end": "04:30"}], TZ, d, 1)
        assert w[0].duration_s == 3 * 3600


def test_overnight_window_ends_the_next_day_and_is_due_after_midnight():
    e = [{"days": ["MON"], "start": "22:00", "end": "02:00"}]
    w = sc.windows("p@1", e, TZ, date(2026, 11, 2), 1)       # Monday
    assert w[0].duration_s == 4 * 3600
    tue_0100 = utc(2026, 11, 3, 0, 0)                         # 01:00 CET Tuesday
    assert [x.key for x in sc.due("p@1", e, TZ, tue_0100)] == ["p@1:2026-11-02:0"]


def test_only_selected_weekdays_and_multiple_entries():
    e = [{"days": ["MON", "WED"], "start": "06:00", "end": "06:10"}, {"days": ["WED"], "start": "18:00", "end": "18:05"}]
    w = sc.windows("p@1", e, TZ, date(2026, 11, 2), 7)
    assert [(x.local_date.isoformat(), x.entry) for x in w] == [("2026-11-02", 0), ("2026-11-04", 0), ("2026-11-04", 1)]


def test_keys_are_stable_so_a_backward_clock_jump_cannot_repeat_a_window():
    e = [{"days": ALL, "start": "06:00", "end": "07:00"}]
    a = sc.due("p@1", e, TZ, utc(2026, 11, 2, 5, 10))
    b = sc.due("p@1", e, TZ, utc(2026, 11, 2, 5, 40))
    assert a[0].key == b[0].key


def test_a_forward_jump_into_an_open_window_still_finds_it_and_past_ones_are_missed():
    e = [{"days": ALL, "start": "06:00", "end": "07:00"}]
    assert sc.due("p@1", e, TZ, utc(2026, 11, 2, 5, 55))      # 06:55 local, 5 minutes left
    missed = sc.ended_between("p@1", e, TZ, utc(2026, 11, 2, 4, 0), utc(2026, 11, 4, 8, 0))
    assert [m.local_date.isoformat() for m in missed] == ["2026-11-02", "2026-11-03", "2026-11-04"]


def test_overlap_preview_and_bad_times():
    a = [{"days": ["MON"], "start": "06:00", "end": "07:00"}]
    b = [{"days": ["MON"], "start": "06:30", "end": "06:40"}]
    assert sc.overlap(a, b, TZ, from_day=date(2026, 11, 2), days=7)
    with pytest.raises(ValueError):
        sc.windows("p@1", [{"days": ALL, "start": "25:00", "end": "26:00"}], TZ, date(2026, 11, 2), 1)
