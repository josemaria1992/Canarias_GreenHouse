"""Weekly schedule windows in the site time zone (Europe/Stockholm), resolved to UTC instants.

Owner policy (24 October 2026: "if there is no watering, plants die; make sure the program continues"):

* A start time that does not exist on the day clocks go forward (02:00-03:00 on the last Sunday of March)
  starts at the first valid local minute after the gap, 03:00, and keeps its full written length.
* A start time that occurs twice on the day clocks go back (02:00-03:00 on the last Sunday of October) starts
  once, at the first occurrence (fold 0). Its end follows from the length, not from the second 02:xx.
* A window's length is its written wall-clock length: 01:30-02:30 lasts 60 minutes on every day, including
  both change days. An overnight window (end at or before start) ends on the next local day.
* Each window of each day has one occurrence key: `<program>@<version>:<local start date>:<entry>`. The engine
  stores the key before it starts anything, so the same window is never run twice, whatever the clock does.
* A clock correction is judged on UTC: a window whose UTC end has not passed yet is still due and runs for its
  remaining part (a forward jump into an open window does not skip it; a backward jump cannot repeat a key
  already stored). A window whose end passed while nothing could run (outage, recovery, clock jump past the
  whole window) is MISSED and recorded, never replayed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(frozen=True)
class Window:
    key: str
    entry: int
    local_date: date
    start_utc: datetime
    end_utc: datetime
    start_local: str            # as shown to the operator, with the zone abbreviation
    shifted: str | None         # "SPRING_GAP" when the written start did not exist that day

    @property
    def duration_s(self) -> float:
        return (self.end_utc - self.start_utc).total_seconds()


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    v = int(h) * 60 + int(m)
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        raise ValueError(f"{hhmm} is not a time of day (HH:MM)")
    return v


def written_length_s(start: str, end: str) -> int:
    a, b = _minutes(start), _minutes(end)
    return ((b - a) % 1440 or 1440) * 60


def resolve_local(d: date, hhmm: str, tz: ZoneInfo) -> tuple[datetime, str | None]:
    """The UTC instant of local `hhmm` on `d`: first occurrence when repeated, next valid minute when skipped."""
    m = _minutes(hhmm)
    naive = datetime.combine(d, time(m // 60, m % 60))
    first = naive.replace(tzinfo=tz, fold=0)
    # nonexistent: converting to UTC and back does not give the same wall time
    back = first.astimezone(timezone.utc).astimezone(tz)
    if back.replace(tzinfo=None) != naive:
        probe = naive
        for _ in range(180):                     # gaps are at most a few hours; step by minute
            probe += timedelta(minutes=1)
            cand = probe.replace(tzinfo=tz, fold=0)
            if cand.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == probe:
                return cand.astimezone(timezone.utc), "SPRING_GAP"
        raise ValueError(f"cannot resolve {hhmm} on {d}")
    return first.astimezone(timezone.utc), None


def windows(program_ref: str, entries: list[dict], tz_name: str, start_day: date, days: int) -> list[Window]:
    """Every window that starts on the local days [start_day, start_day + days)."""
    tz = ZoneInfo(tz_name)
    out = []
    for n in range(days):
        d = start_day + timedelta(days=n)
        dow = DAYS[d.weekday()]
        for i, e in enumerate(entries):
            if dow not in e["days"]:
                continue
            s_utc, shifted = resolve_local(d, e["start"], tz)
            length = written_length_s(e["start"], e["end"])      # kept in full from a shifted start
            end_utc = s_utc + timedelta(seconds=length)
            local = s_utc.astimezone(tz)
            out.append(Window(key=f"{program_ref}:{d.isoformat()}:{i}", entry=i, local_date=d, start_utc=s_utc,
                              end_utc=end_utc, start_local=local.strftime("%a %Y-%m-%d %H:%M %Z"), shifted=shifted))
    return sorted(out, key=lambda w: (w.start_utc, w.entry))


def due(program_ref: str, entries: list[dict], tz_name: str, now_utc: datetime) -> list[Window]:
    """Windows open at `now_utc` (start <= now < end). Yesterday is included for overnight windows."""
    tz = ZoneInfo(tz_name)
    today = now_utc.astimezone(tz).date()
    return [w for w in windows(program_ref, entries, tz_name, today - timedelta(days=1), 2)
            if w.start_utc <= now_utc < w.end_utc]


def upcoming(program_ref: str, entries: list[dict], tz_name: str, now_utc: datetime, n: int = 5) -> list[Window]:
    tz = ZoneInfo(tz_name)
    today = now_utc.astimezone(tz).date()
    return [w for w in windows(program_ref, entries, tz_name, today - timedelta(days=1), 9) if w.end_utc > now_utc][:n]


def ended_between(program_ref: str, entries: list[dict], tz_name: str, a_utc: datetime, b_utc: datetime) -> list[Window]:
    """Windows that started and ended inside (a, b]: candidates for MISSED after an outage or a clock jump."""
    tz = ZoneInfo(tz_name)
    d0 = a_utc.astimezone(tz).date() - timedelta(days=1)
    span = (b_utc.astimezone(tz).date() - d0).days + 1
    if span > 400:
        span = 400
    return [w for w in windows(program_ref, entries, tz_name, d0, span) if a_utc < w.end_utc <= b_utc]


def overlap(entries_a: list[dict], entries_b: list[dict], tz_name: str, *, from_day: date, days: int = 14) -> list[str]:
    """Foreseeable overlaps of two weekly schedules over the next `days` days (activation preview)."""
    wa = windows("a", entries_a, tz_name, from_day, days)
    wb = windows("b", entries_b, tz_name, from_day, days)
    out = []
    for x in wa:
        for y in wb:
            if x.start_utc < y.end_utc and y.start_utc < x.end_utc:
                out.append(f"{x.start_local} overlaps {y.start_local}")
    return out
