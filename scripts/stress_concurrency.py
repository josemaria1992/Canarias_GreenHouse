"""Concurrency stress for the historian WAL (ADR-0008, owner review R1): acquisition, events and
commands keep flowing while a slow consistent backup, year-range readers and a retention catch-up run
at the same time. Reports the largest WAL, the lowest free space, acquisition lateness, writer and
command latency, status-read latency, and whether every snapshot and event was written.

Runs in real time against a scratch database built by measure_storage.py, used in place (it is
modified; never point it at an installation's database):

    python scripts/measure_storage.py --days 400 --out /tmp/m
    python scripts/stress_concurrency.py --db /tmp/m/measure.sqlite3 --out /tmp/m/stress

Acquisition is accelerated (--pace complete snapshots per second; 1 = 60x the approved 60 s cadence)
and events and commands arrive far faster than on the installation, so the WAL sees more writes
while the long readers hold it than it would in service. The retention run catches up a backlog of
--backlog-days (as after a hold), repeated every --retention-every-s.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_storage import config, environment, file_sizes, process_io  # noqa: E402

from aquacrop.control.alarms import AlarmEngine  # noqa: E402
from aquacrop.domain.model import (CommandOrigin, CommandRecord, Origin, Outcome, OutputCommand,  # noqa: E402
                                   PointKind, new_id)
from aquacrop.historian.backup import BackupAborted, BackupFailed, BackupRefused, create_backup  # noqa: E402
from aquacrop.historian.recording import complete_snapshot  # noqa: E402
from aquacrop.historian.retention import (PRUNE_MAX_TRANSACTIONS_PER_RUN, evaluate_storage_alarm,  # noqa: E402
                                          prune_expired)
from aquacrop.historian.writer import Historian  # noqa: E402
from aquacrop.io.sampling import AcqContext, make_sample  # noqa: E402

MIB = 2**20


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 4)


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "p50": pct(xs, 0.5), "p95": pct(xs, 0.95), "p99": pct(xs, 0.99),
            "max": round(max(xs), 4) if xs else None, "mean": round(statistics.fmean(xs), 4) if xs else None}


class Stress:
    def __init__(self, a, cfg, h: Historian, db: Path, out: Path):
        self.a, self.cfg, self.h, self.db, self.out = a, cfg, h, db, out
        self.t0 = time.monotonic()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.marks: list[tuple[float, str]] = []
        self.lateness: list[float] = []
        self.tick_work: list[float] = []
        self.snapshots = {"scheduled": 0, "submitted": 0, "not_permitted": 0}
        self.events_submitted = 0
        self.cmd_latency: list[float] = []
        self.cmd_failures: list[str] = []
        self.commit_probe: list[float] = []
        self.status_probe: list[float] = []
        self.samples: list[dict] = []
        self.results: dict = {}
        self.base_wall = datetime.now(timezone.utc)

    def now(self) -> float:
        return time.monotonic() - self.t0

    def mark(self, what: str) -> None:
        with self.lock:
            self.marks.append((round(self.now(), 2), what))
        print(f"[{self.now():7.1f}s] {what}", flush=True)

    # ------------------------------------------------------------------ writers
    def acquisition(self) -> None:
        cfg, h, pace = self.cfg, self.h, self.a.pace
        pts = cfg.all_points()
        rng = random.Random(7)
        raw = {str(p): 12000.0 for p in pts}
        sid = h.session_id
        interval = 1.0 / pace
        engine = AlarmEngine(cfg, emit=lambda kind, payload: self._event(kind, payload, actor="system"))
        nxt = time.monotonic() + interval
        k = 0
        while not self.stop.is_set():
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            late = time.monotonic() - nxt
            self.lateness.append(late)
            nxt += interval
            tic = time.perf_counter()
            evaluate_storage_alarm(h, engine)          # what every poll cycle does before its snapshot
            h.alarms(engine.take_dirty())
            at = datetime.now(timezone.utc)
            ctx = AcqContext(sid, f"{sid}:stress:{k}")
            latest = {}
            for p in pts:
                if p.kind == PointKind.AI:
                    raw[str(p)] = min(19900.0, max(4100.0, raw[str(p)] + rng.gauss(0, 40)))
                    v = int(raw[str(p)])
                else:
                    v = rng.random() < 0.5
                latest[str(p)] = make_sample(cfg, p, v, acquired=at, ctx=ctx, origin=Origin.REAL)
            self.snapshots["scheduled"] += 1
            ok = h.snapshot(at, complete_snapshot(cfg, latest, now=at, ctx=ctx, origin=Origin.REAL,
                                                  stale_reason=lambda s: None), interval)
            self.snapshots["submitted" if ok else "not_permitted"] += 1
            self.tick_work.append(time.perf_counter() - tic)
            k += 1

    def traffic(self) -> None:
        """Steady events and commands, then a burst: a chattering input and an alarm storm."""
        cfg, h, a = self.cfg, self.h, self.a
        pts = cfg.all_points()
        di = [str(p) for p in pts if p.kind == PointKind.DI]
        do = [str(p) for p in pts if p.kind == PointKind.DO]
        ai = [str(p) for p in pts if p.kind == PointKind.AI]
        alarms = AlarmEngine(cfg, emit=lambda kind, payload: self._event(kind, payload, actor="system"))
        rng = random.Random(11)
        tick, next_cmd, burst_marked, cleared = 0.1, 0.0, False, False
        while not self.stop.is_set():
            t = self.now()
            in_burst = a.burst_at_s <= t < a.burst_at_s + a.burst_s
            rate = a.burst_events_per_s if in_burst else a.events_per_s
            for _ in range(rng.randrange(int(rate * tick), int(rate * tick) + 2)):
                self._event("DI_EDGE", {"point": rng.choice(di), "origin": "REAL", "resolution_s": 3.0})
            if in_burst and not burst_marked:
                burst_marked = True
                self.mark(f"burst: {a.burst_events_per_s:g} events/s for {a.burst_s:g} s and an alarm storm")
                for pid in ai:
                    alarms.raise_or_update("STRESS_HIGH", pid, severity="WARNING", sim_s=None,
                                           message=f"STRESS_HIGH on {pid}", value=90.0, threshold=85.0,
                                           quality="GOOD", origin="REAL")
                h.alarms(alarms.take_dirty())
            if burst_marked and not cleared and t >= a.burst_at_s + a.burst_s / 2:
                cleared = True
                for occ in list(alarms.open.values()):
                    alarms.acknowledge(occ.occurrence_id, "operator1")
                    alarms.clear(occ.rule_id, occ.target)
                h.alarms(alarms.take_dirty())
            if t >= next_cmd:
                next_cmd = t + 1.0 / a.commands_per_s
                self._command(rng.choice(do), rng.random() < 0.5)
            time.sleep(tick)

    def _event(self, kind, payload, actor=None):
        self.h.event(kind, payload, actor=actor)
        self.events_submitted += 1

    def _command(self, target: str, desired: bool) -> None:
        """The command path waits for its durable intent before dispatch: this is its latency."""
        at = datetime.now(timezone.utc)
        cmd = OutputCommand(new_id("cmd"), "operator1", target, desired, self.cfg.config_version, None, at,
                            at + timedelta(seconds=30), CommandOrigin.BENCH, "stress workload")
        rec = CommandRecord(cmd, Outcome.ACCEPTED)
        tic = time.perf_counter()
        try:
            self.h.command_intent(rec)
        except Exception as e:          # the real path would reject an ON command here
            self.cmd_failures.append(f"{self.now():.1f}s intent: {e!r}")
            return
        self.cmd_latency.append(time.perf_counter() - tic)
        rec.readback = desired
        rec.advance(Outcome.READBACK_CONFIRMED, "stress")
        try:
            self.h.command_outcome(rec)
        except Exception as e:
            self.cmd_failures.append(f"{self.now():.1f}s outcome: {e!r}")

    # ------------------------------------------------------------------ probes
    def probe(self) -> None:
        wal = Path(str(self.db) + "-wal")
        last_read = 0.0
        while not self.stop.is_set():
            t = self.now()
            tic = time.perf_counter()
            try:
                self.h.submit(lambda c: None, wait=True, timeout=60)
                self.commit_probe.append(time.perf_counter() - tic)
            except Exception as e:
                self.commit_probe.append(60.0)
                self.mark(f"writer probe failed: {e!r}")
            if t - last_read >= 1.0:
                last_read = t
                tic = time.perf_counter()
                st = self.h.storage()
                self.status_probe.append(time.perf_counter() - tic)
                ck = st.get("wal_checkpoint") or {}
                self.samples.append({"t_s": round(t, 2), "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                                     "db_file_bytes": self.db.stat().st_size,
                                     "free_bytes": shutil.disk_usage(self.out).free,
                                     "queue_depth": self.h._q.qsize(),
                                     "checkpoint_behind": bool(ck.get("behind_since_utc")),
                                     "wal_over_warning": st.get("wal_over_warning")})
            time.sleep(0.25)

    # ------------------------------------------------------------------ long readers and maintenance
    def backup(self) -> None:
        self._sleep_until(self.a.backup_at_s)
        hs = self.cfg.historian
        self.mark("backup started (one read transaction for the whole dump)")
        tic = time.perf_counter()
        try:
            man = create_backup(self.db, self.out / "backups", config_id=self.cfg.config_id,
                                config_version=self.cfg.config_version, keep=hs.backup_keep,
                                reserve_bytes=hs.free_space_reserve_bytes, wal_abort_bytes=hs.wal_reader_abort_bytes,
                                max_seconds=hs.backup_max_seconds)
            self.results["backup"] = {"outcome": "PUBLISHED", "bytes": man["bytes"], "watch": man["watch"],
                                      "verification": man.get("verification"),
                                      "row_counts_total": sum(man["row_counts"].values())}
        except (BackupRefused, BackupAborted, BackupFailed) as e:
            self.results["backup"] = {"outcome": type(e).__name__, "detail": str(e)}
        self.results["backup"]["seconds"] = round(time.perf_counter() - tic, 1)
        self.mark(f"backup finished: {self.results['backup']['outcome']}")

    def readers(self) -> None:
        self._sleep_until(self.a.readers_at_s)
        h, out = self.h, {}
        newest = h.query("SELECT max(acquired_utc) FROM samples WHERE acquired_utc < ?",
                         (self.base_wall.isoformat(),))[0][0]
        end = datetime.fromisoformat(newest)
        s, e = (end - timedelta(days=365)).isoformat(), end.isoformat()
        ids = [str(p) for p in self.cfg.all_points()][:16]
        self.mark("year-range trend query (16 points, API limit 20 000 rows each)")
        tic = time.perf_counter()
        rows = 0
        for pid in ids:
            rows += len(h.query("SELECT s.acquired_utc, s.value, s.raw_value, s.quality, s.origin, s.unit, s.sim_s, "
                                "s.sim_run_id, c.cadence_s, c.cadence_basis FROM samples s JOIN cycles c ON "
                                "c.cycle_pk=s.cycle_pk WHERE s.point_id=? AND s.acquired_utc>=? AND "
                                "s.acquired_utc<=? AND (? IS NULL OR c.cadence_basis=?) ORDER BY s.acquired_utc "
                                "LIMIT ?", (pid, s, e, None, None, 20001)))
        out["trend_year_16_points"] = {"seconds": round(time.perf_counter() - tic, 2), "rows": rows}
        self.mark("year-range CSV export page (API cap 200 000 rows)")
        tic = time.perf_counter()
        rows = len(h.query("SELECT id, acquired_utc, point_id, raw_value, raw_type, raw_unit, value, unit, quality, "
                           "reason, origin, calibration_ref, config_version, source_profile, device_uid, bus_id, "
                           "session_id, sim_run_id, sim_s, override_json FROM samples WHERE acquired_utc>=? AND "
                           "acquired_utc<=? AND id>? ORDER BY id LIMIT ?", (s, e, 0, 200001)))
        out["export_page_200k"] = {"seconds": round(time.perf_counter() - tic, 2), "rows": rows}
        self.mark("unbounded ad-hoc reader: full-table aggregate on its own connection (not an API path)")
        tic = time.perf_counter()
        con = sqlite3.connect(self.db)
        try:
            rows = len(con.execute("SELECT point_id, count(*), avg(value), min(acquired_utc), max(acquired_utc) "
                                   "FROM samples GROUP BY point_id").fetchall())
        finally:
            con.close()
        out["full_scan_aggregate"] = {"seconds": round(time.perf_counter() - tic, 2), "groups": rows}
        self.results["readers"] = out
        self.mark("readers finished")

    def retention(self) -> None:
        self._sleep_until(self.a.retention_at_s)
        runs = []
        while not self.stop.is_set():
            at = datetime.now(timezone.utc) + timedelta(days=self.a.backlog_days)
            tic = time.perf_counter()
            res = prune_expired(self.h, now=at, max_chunks=PRUNE_MAX_TRANSACTIONS_PER_RUN,
                                wal_limit_bytes=self.cfg.historian.wal_warning_bytes)
            runs.append({"t_s": round(self.now(), 1), "seconds": round(time.perf_counter() - tic, 1),
                         "transactions": res.get("transactions"), "deleted_samples": res["deleted"].get("samples", 0),
                         "deleted_total": sum(res["deleted"].values()), "incomplete": res.get("incomplete")})
            self.mark(f"retention run: {runs[-1]['deleted_total']} rows in {runs[-1]['transactions']} transactions"
                      + (f"; stopped: {res['incomplete']}" if res.get("incomplete") else "; backlog cleared"))
            if "incomplete" not in res:
                break
            self._sleep_until(self.now() + self.a.retention_every_s)
        self.results["retention_runs"] = runs

    def _sleep_until(self, t_s: float) -> None:
        while not self.stop.is_set() and self.now() < t_s:
            time.sleep(0.2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="scratch database from measure_storage.py (modified in place)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--inventory", type=int, choices=(24, 48), default=48)
    ap.add_argument("--pace", type=float, default=1.0, help="complete snapshots per second")
    ap.add_argument("--events-per-s", type=float, default=5.0)
    ap.add_argument("--commands-per-s", type=float, default=0.5)
    ap.add_argument("--burst-at-s", type=float, default=40.0)
    ap.add_argument("--burst-s", type=float, default=60.0)
    ap.add_argument("--burst-events-per-s", type=float, default=100.0)
    ap.add_argument("--backup-at-s", type=float, default=10.0)
    ap.add_argument("--readers-at-s", type=float, default=20.0)
    ap.add_argument("--retention-at-s", type=float, default=30.0)
    ap.add_argument("--retention-every-s", type=float, default=60.0)
    ap.add_argument("--backlog-days", type=float, default=7.0)
    ap.add_argument("--settle-s", type=float, default=120.0, help="keep writing after the readers finish")
    ap.add_argument("--max-s", type=float, default=3600.0)
    a = ap.parse_args()
    db, out = Path(a.db), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = config(a.inventory)
    io0 = process_io()
    before = file_sizes(db)
    rep: dict = {"environment": environment(out, 7), "parameters": vars(a), "files_before": before}
    con = sqlite3.connect(db)
    rep["rows_before"] = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                          for t in ("cycles", "samples", "events", "commands")}
    con.close()
    h = Historian(db, cfg, new_id("sess"), backend_name="MODBUS_RTU")
    s = Stress(a, cfg, h, db, out)
    rep["free_bytes_before"] = shutil.disk_usage(out).free
    workers = {n: threading.Thread(target=getattr(s, n), name=n, daemon=True)
               for n in ("acquisition", "traffic", "probe", "backup", "readers", "retention")}
    for t in workers.values():
        t.start()
    for n in ("backup", "readers", "retention"):
        workers[n].join(timeout=max(1.0, a.max_s - s.now()))
    s.mark(f"long readers and retention done; writing for {a.settle_s:g} s more")
    t_end = s.now() + a.settle_s
    while s.now() < min(t_end, a.max_s):
        time.sleep(0.5)
    s.stop.set()
    for n in ("acquisition", "traffic", "probe"):
        workers[n].join(timeout=30)
    h.wait_idle(120)
    after_writes = file_sizes(db)
    t_stress = s.now()
    con = sqlite3.connect(db)
    since = s.base_wall.isoformat()
    written = {"cycles": con.execute("SELECT count(*) FROM cycles WHERE persisted_utc >= ?", (since,)).fetchone()[0],
               "events": con.execute("SELECT count(*) FROM events WHERE utc >= ? AND session_id=? AND kind NOT LIKE "
                                     "'RETENTION_%'", (since, h.session_id)).fetchone()[0],
               "commands": con.execute("SELECT count(*) FROM commands WHERE session_id=?", (h.session_id,)).fetchone()[0],
               "commands_without_outcome": con.execute("SELECT count(*) FROM commands WHERE session_id=? AND outcome "
                                                       "IN ('ACCEPTED','EXECUTING')", (h.session_id,)).fetchone()[0]}
    con.close()
    health = dict(h.health)
    h.close()
    io1 = process_io()
    wal = [x["wal_bytes"] for x in s.samples]
    rep |= {
        "duration_s": round(t_stress, 1), "marks": s.marks, "results": s.results,
        "wal": {"max_bytes": max(wal) if wal else None,
                "seconds_over_warning": sum(1 for x in s.samples if x["wal_over_warning"]),
                "warning_bytes": cfg.historian.wal_warning_bytes,
                "reader_abort_bytes": cfg.historian.wal_reader_abort_bytes,
                "after_last_write_bytes": after_writes["wal_bytes"],
                "after_close_bytes": file_sizes(db)["wal_bytes"]},
        "free_space": {"min_bytes": min(x["free_bytes"] for x in s.samples) if s.samples else None,
                       "before_bytes": rep["free_bytes_before"],
                       "largest_drop_bytes": rep["free_bytes_before"] - min(x["free_bytes"] for x in s.samples)
                       if s.samples else None},
        "acquisition": {"pace_snapshots_per_s": a.pace, "lateness_s": summary(s.lateness),
                        "tick_work_s": summary(s.tick_work),
                        "snapshots": s.snapshots, "cycles_written": written["cycles"]},
        "writer_commit_latency_s": summary(s.commit_probe),
        "command_intent_latency_s": summary(s.cmd_latency), "command_failures": s.cmd_failures,
        "status_read_latency_s": summary(s.status_probe),
        "events": {"submitted_by_stress": s.events_submitted, "written": written["events"],
                   "lost": s.events_submitted - written["events"],
                   "note": "events this run submitted (edges, alarm transitions) against those stored; the "
                           "historian's own RETENTION_* events are not counted"},
        "commands": {"written": written["commands"], "without_outcome": written["commands_without_outcome"]},
        "writer_health": {k: health.get(k) for k in ("ok", "commits", "dropped_cycles", "degraded_reason",
                                                     "labels_refused")},
        "files_after": file_sizes(db),
        "process_write_bytes": (io1["write_bytes"] - io0["write_bytes"]) if io0 and io1 else None,
        "timeline": s.samples[::5]}
    text = json.dumps(rep, indent=2, default=str)
    (out / "stress.json").write_text(text)
    print(json.dumps({k: rep[k] for k in ("duration_s", "results", "wal", "free_space", "acquisition",
                                          "writer_commit_latency_s", "command_intent_latency_s", "command_failures",
                                          "status_read_latency_s", "events", "commands", "writer_health")},
                     indent=2, default=str))


if __name__ == "__main__":
    main()
