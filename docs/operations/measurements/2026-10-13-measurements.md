# Historian measurements, 2026-10-13: with the process workload (OP-06)

Desktop evidence for the pre-hardware gate OP-06: the 2026-10-12 one-year measurement repeated with
the events and commands of the process controllers added, then the concurrency stress on the
resulting database. Values are synthetic. **These are not CM4 or eMMC measurements and make no
endurance claim**; the CM4 figures belong to CP-00 (AT-STO-02).

## Files

| File | What |
|---|---|
| `2026-10-13-process-workload.json` | `measure_process_workload.py`: 48 simulated hours of `installation-48` with irrigation, return, refill and acid all in AUTO; events per kind with payload sizes and sample payloads, commands |
| `2026-10-13-storage-48pt-60s-400d-process.json` | `measure_storage.py --process-workload`, 48 points, 400 days: environment, workload, checkpoints, final storage, WAL, bytes written, backup, restore drill |
| `2026-10-13-stress-48pt-400d-process.json` | `stress_concurrency.py` on that database: marks, results, latencies, timeline every 5 s |
| `2026-10-13-trend-cache-48pt-365d.json` | `measure_trend_cache.py` on that database: a 365-day chart of 16 points without the cache, filling it, and refreshed from it |
| `2026-10-13-trend-cache-48pt-365d-by-run.json` | the same, repeated after the buckets were split by simulation run and recorder |
| `2026-10-13-storage-80pt-60s-400d-process.json` | `measure_storage.py --inventory 80 --process-workload --skip-drill`, 80 points, 400 days, then the full restore drill of its backup (section "80 points") |

## Reproduce

```text
python scripts/measure_process_workload.py --hours 48 --out workload.json
python scripts/measure_storage.py --days 400 --inventory 48 --process-workload workload.json --out OUT
python scripts/stress_concurrency.py --db OUT/measure.sqlite3 --out OUT/stress --backlog-days 14
python scripts/measure_trend_cache.py --db OUT/measure.sqlite3 --out OUT/trend-cache.json
```

- Workload: seed 1, 48 simulated hours at a 0.5 s tick, 279 s of wall time; file SHA-256
  `ea818535be36d62023a6d8159b007fc12f96318fa750a25f2a195b73f3b0c31d`.
- Storage run: script SHA-256 `b19e0a572492d5446bc40c09efc8811919097f3dc4e0714d87cb1113931090bc` (the
  `measure_storage.py` committed in this release), 2026-10-13T02:50:19+00:00 to
  2026-10-13T03:45:26+00:00. It ran on the stage 4 working tree before its first commit, so the
  report says `e7a60d9`, dirty, and version `0.2.0.dev0` (the version number was raised during the
  run); the database is schema 3. The script later gained one line (its measurement configuration names
  the site time zone, which a real profile must do since 2026-10-13); nothing it measures changed, but
  its SHA-256 now differs from the one above.
- Stress run: on the same database right after the storage run, at commit `3221430` (clean),
  2026-10-13T03:45:45+00:00 for 642 s; script SHA-256
  `deaf0de55563d653d24df93c70a74bfd45ef67d02b3178fa82e8a4cef864ecee`.
- Trend cache: `measure_trend_cache.py` on the same database afterwards, at commit `a723b3c`
  (clean), 2026-10-13T04:03:12+00:00; script SHA-256
  `eea54a409b1c2fda03c143211c7e4641f6eb0b961438638fa99db935ee08cea7`.
- Trend cache repeated: the same script on the same database, 2026-10-13T05:27:29+00:00, on the
  working tree of the review fix that keeps simulation runs and recorders apart, before it was
  committed (the report says `67c5af5`, dirty); `historian/trends.py` SHA-256
  `271dee7f9bdb21f1e632049d65d6896919a98345c03f1d4fb09dfcc90dc640bb`. A first attempt while the
  browser check ran took 86.1 s without the cache and is not reported.
- Environment: Python 3.13.16, SQLite 3.45.1, Linux-6.18.44-fc-v114-x86_64 (Intel(R) Xeon(R)
  Processor @ 2.10GHz, 4 CPUs, 16 GiB RAM, ext4). Other jobs shared the machine during part of the
  run, so the durations are not benchmarks.

## Workload

Everything of the 2026-10-12 workload (complete snapshots every 60 s, 20 state changes per digital
input per day, 200 commands a day, 24 alarms a day, a 30-minute module fault every 7 days with a
chattering input, hourly retention), **plus** the process controllers' own records at the rates
measured on the simulated installation with every process in AUTO around the clock:

| Per day | Count | Mean payload |
|---|---|---|
| Process events (all kinds) | 7,922 | |
| `PROCESS_TRANSITION` | 3,315 | 295 bytes |
| `DI_EDGE` / `DO_EDGE` of the process equipment | 1,815 / 1,298 | 161 bytes |
| Drive adapter command intent and result | 558 each | 300 / 351 bytes |
| `IRRIGATION_STEP_COMPLETED` | 279 | 239 bytes |
| `RETURN_RUN_ENDED`, acid doses, refill batches | 52, 12.5, 1 | 232 to 1,468 bytes |
| Controller commands (durable intent and outcome) | 1,299 | |

Irrigation alternates S1/P1 and S2/P2 for 300 s each with no pause, so these rates are an upper
bound: a schedule with pauses writes less. Over 400 days the run generated 3,168,944 process events
and 519,407 controller commands on top of the 2026-10-12 traffic.

## 48 points, 400 days, with the process workload

| Quantity | Measured | 2026-10-12, without it |
|---|---|---|
| Growth while the window fills | 28.6 MB (27.3 MiB) per day | 23.8 MB per day |
| Database file at day 365 / day 400 | 9.71 GiB / 9.72 GiB | 8.10 / 8.11 GiB |
| Rows at day 400: samples, events, commands, alarm occurrences | 25,228,800; 3,090,185; 546,960; 8,509 | 25,228,800; 198,590; 73,000; 8,493 |
| WAL: largest at the hourly samples / after close | 5.4 MiB / 0 bytes | 5.6 MiB / 0 bytes |
| Bytes the process wrote to storage | 223 GiB, about 0.56 GiB a day | 83 GiB, 0.21 GiB a day |
| Compressed backup | 0.55 GiB (17.5 times smaller), written and verified in 7.6 min | 0.49 GiB, 5.8 min |
| Quick verification | 66 s | 44 s |
| Restore drill | passed in 11.1 min (9.41 GiB restored, 11.67 GiB scratch required; integrity ok, 0 foreign-key violations, schema 3, application start-up ok) | 9.1 min, 9.73 GiB scratch |

Database and WAL are reported separately; bytes written are everything the process sent to the
file system (WAL frames, checkpoints, page rewrites, retention deletes) and are not eMMC endurance
evidence.

**Against the budget (ADR-0008):** the database stays inside its 10 GiB allowance with 0.28 GiB
(about 3%) to spare, and eight backups (seven kept plus one being written) take about 4.4 GiB of
the 4.5 GiB allowed. Both hold, but only just, and only for this worst case of every process in
AUTO without a pause. The `STORAGE_CAPACITY` warning fires if the measured 365-day projection on the
controller ever exceeds the allowance; the eMMC budget leaves about 3.8 GiB unallocated if the
owner chooses to raise it after CP-00. Nothing was changed here.

| Day | Database file | Samples | Events | Commands | Alarm occurrences | WAL before checkpoint | Process bytes written |
|---|---|---|---|---|---|---|---|
| 1 | 0.03 GiB | 69,120 | 8,450 | 1,498 | 24 | 4.7 MiB | 0.5 GiB |
| 7 | 0.18 GiB | 483,840 | 59,250 | 10,488 | 165 | 4.8 MiB | 3.8 GiB |
| 30 | 0.79 GiB | 2,073,600 | 253,936 | 44,955 | 699 | 4.9 MiB | 16.5 GiB |
| 60 | 1.59 GiB | 4,147,200 | 507,849 | 89,910 | 1,397 | 4.8 MiB | 33.2 GiB |
| 90 | 2.38 GiB | 6,220,800 | 761,837 | 134,865 | 2,100 | 4.8 MiB | 49.9 GiB |
| 120 | 3.18 GiB | 8,294,400 | 1,015,861 | 179,823 | 2,803 | 4.8 MiB | 66.6 GiB |
| 150 | 3.98 GiB | 10,368,000 | 1,269,618 | 224,778 | 3,497 | 4.8 MiB | 83.4 GiB |
| 180 | 4.78 GiB | 12,441,600 | 1,523,511 | 269,735 | 4,191 | 4.9 MiB | 100.1 GiB |
| 210 | 5.58 GiB | 14,515,200 | 1,777,482 | 314,688 | 4,892 | 4.8 MiB | 116.8 GiB |
| 240 | 6.38 GiB | 16,588,800 | 2,031,553 | 359,644 | 5,593 | 5.0 MiB | 133.5 GiB |
| 270 | 7.18 GiB | 18,662,400 | 2,285,410 | 404,599 | 6,289 | 4.8 MiB | 150.3 GiB |
| 300 | 7.98 GiB | 20,736,000 | 2,539,252 | 449,553 | 6,990 | 4.8 MiB | 167.2 GiB |
| 330 | 8.78 GiB | 22,809,600 | 2,793,220 | 494,510 | 7,690 | 4.8 MiB | 184.0 GiB |
| 360 | 9.58 GiB | 24,883,200 | 3,046,984 | 539,464 | 8,390 | 4.8 MiB | 200.8 GiB |
| 365 | 9.71 GiB | 25,228,800 | 3,089,346 | 546,957 | 8,508 | 4.9 MiB | 203.6 GiB |
| 390 | 9.72 GiB | 25,228,800 | 3,089,951 | 546,962 | 8,511 | 5.4 MiB | 217.7 GiB |
| 400 | 9.72 GiB | 25,228,800 | 3,090,185 | 546,960 | 8,509 | 5.4 MiB | 223.3 GiB |

## Concurrency stress on the one-year database

Real time on the 400-day database with the process workload, with the same parameters as on
2026-10-12: snapshots at 1 per second (60 times the approved rate), 5 events a second with a burst
of 100 a second for 60 s and an alarm storm on every analog point, a command every 2 s, the
acquisition loop's storage check on every snapshot; during it a consistent backup, year-range
readers and retention catching up a 14-day backlog.

| Quantity | Result | 2026-10-12, without the process workload |
|---|---|---|
| Backup | published after 8.2 min (0.55 GiB, verified before publishing; largest WAL it saw 428 MiB) | 5.9 min, 0.48 GiB, 377 MiB |
| Year-range readers | trend, 16 points over a year as raw rows (API limit 20,000 per point): 2.0 s; the same chart aggregated per day as `/api/trends` draws it (5,782 buckets from 8,298,561 samples, no cache): 38.3 s; CSV export page of 200,000 rows: 10.0 s; unbounded full-table aggregate (not an API path): 80.8 s | raw trend 5.8 s, export page 6.6 s, full-table aggregate 60.5 s; no aggregated chart then |
| Retention catch-up | 9 runs, 1,131,298 rows (970,800 samples) deleted in 235 transactions; run 1 stopped after 100 delete transactions; runs 2 to 7 deleted nothing (WAL 280 to 412 MiB, above 256 MiB: a long reader is holding it back); run 8 stopped after 100 transactions; run 9 finished at 521 s | 6 runs, 499,756 rows |
| Largest WAL / afterwards | 428 MiB (372 s above the 256 MiB warning) / 64 MiB after the readers ended, 0 bytes after close | 377 MiB (246 s) / 64 MiB / 0 |
| Lowest free space (largest drop) | 16.41 GiB (997 MiB) | 19.37 GiB (869 MiB) |
| Snapshots scheduled / written; lateness p99 / max | 642 / 642; 10.7 ms / 75.5 ms | 485 / 485; 1.1 ms / 2.2 ms |
| Writer round trip p99 / max; command intent p99 / max | 12.1 ms / 733 ms; 10.3 ms / 27.8 ms | 4.7 ms / 813 ms; 23 ms / 547 ms |
| Status read p99 / max | 2.2 ms / 6.0 ms | 2.5 ms / 6.9 ms |
| Lost | 0 of 9,167 events; 0 of 319 commands without outcome or refused | 0 of 8,398; 0 of 241 |

Every snapshot, event and command was written and the WAL was released after the long readers,
as before. Lateness was higher than on 2026-10-12 and stayed far below the 60 s cadence; the machine
also ran other work during these measurements, so the durations are not benchmarks.

**The aggregated year chart was the slowest API read.** At 38 s for 16 points while the backup ran
(about 2.4 s a point), a trends page set to 365 days and refreshed every second would have kept a
long reader open almost continuously and held back the WAL, as the capacity query did on
2026-10-12. Commit `2288518` keeps the buckets that can no longer change in the server
(`historian/trends.py`, `TrendCache`): a refresh reads only the first bucket, which the range cuts,
and the newest ones, which can still change; the page fetches ranges over a day again once a
minute. On the same database afterwards, without the stress load, the same 16-point chart took
23.7 s without the cache, 23.5 s for the first read that fills it and 46 ms for a refresh
30 s after the newest sample, all three equal (5,632 buckets from 8,096,272 samples;
`2026-10-13-trend-cache-48pt-365d.json`). `integration/test_trends.py` checks that a cached chart
equals a full read, before and after two more days of samples, and that a new quarantine is read
again in full. The first read of a year chart after a restart still takes the full time; the page
shows at most 8 points.

The buckets were then split by simulation run and recorder, so that a bucket never averages two
runs or the wall-clock snapshots with the finer simulation-time recorder (a review finding; the
query now joins each sample's cycle). Repeated on the same database without other load: 27.1 s
without the cache, 26.0 s for the first read and 45 ms for a refresh, all three equal and the same
5,632 buckets and 8,096,272 samples, since this database holds wall-clock snapshots of one run
(`2026-10-13-trend-cache-48pt-365d-by-run.json`). The stress figure of 38 s above was measured with
the earlier query.

## Low and full disk

Not measured here; covered by tests that run on every pull request: a backup that runs out of space
while writing aborts and leaves nothing (`test_running_out_of_space_while_writing_aborts_cleanly`),
the capacity levels and the free-space reserve refuse starts, ON commands and backups
(`test_capacity_levels_low_disk_reserve_and_snapshot_floor`, `test_pc12_storage_fault_inhibits_new_starts`),
snapshots not written for low disk are not reported again as missed
(`test_labels_not_written_for_low_disk_are_not_reported_again_as_missed`), and the restore drill
refuses to start without scratch space (`test_restore_drill_needs_scratch_space_and_keeps_the_reserve`).

## 80 points, 400 days, with the process workload (owner inventory of 2026-10-13)

The same run as above with the six-module inventory (16 AI + 32 DI + 32 DO; the added IO 8CH
module `digital_B` takes the weekly 30-minute fault with chatter). Everything else unchanged: 60 s
complete snapshots, 365-day rolling retention, events immediate, SQLite schema 3, the same process
workload file (every process in AUTO without a pause: a worst case). Desktop: Linux container,
Python 3.13.16, commit `53c7f27` plus the uncommitted module patch (the code of `2c42775`),
2026-10-13T13:08:11Z to 14:18:09Z. The disk could not hold the database and a restored copy at the
same time, so the drill ran right after, on the same backup, with the measured database deleted.

```text
python scripts/measure_storage.py --days 400 --inventory 80 --process-workload \
    docs/operations/measurements/2026-10-13-process-workload.json --skip-drill --out OUT
# then, with OUT/measure.sqlite3 removed: aquacrop.historian.backup.verify_backup(OUT/backups, full=True,
#     scratch_dir=OUT/drill-scratch, cfg=measure_storage.config(80))
```

| Quantity | 80 points | 48 points (above) |
|---|---|---|
| Growth while the window fills | 44.2 MB (42.2 MiB) per day | 28.6 MB per day |
| Database file at day 365 / day 400 | **15.02 GiB / 15.04 GiB** | 9.71 / 9.72 GiB |
| Rows at day 400: samples, events, commands, alarm occurrences | 42,048,000; 3,206,975; 546,960; 8,509 | 25,228,800; 3,090,185; 546,960; 8,509 |
| WAL: largest at the hourly samples / after close | 6.4 MiB / 0 bytes | 5.4 MiB / 0 bytes |
| Bytes the process wrote to storage | 356 GiB, about 0.89 GiB a day | 223 GiB |
| Compressed backup | 0.82 GiB (18.4 times smaller), written and verified in 11.7 min | 0.55 GiB, 7.6 min |
| Quick verification | 80 s | 66 s |
| Restore drill | passed in 15.9 min (14.56 GiB restored; **18.05 GiB scratch required**; integrity ok, 0 foreign-key violations, schema 3, application start-up ok) | 11.1 min, 11.67 GiB |

The owner's one-day measurement (13.2 GiB/year) had no process workload; with it the year is
15.0 GiB. Both exceed the 10 GiB planning budget, which is a warning only and never deletes.

| Day | Database file | Samples | Events | Commands | Alarm occurrences | WAL before checkpoint | Process bytes written |
|---|---|---|---|---|---|---|---|
| 1 | 0.04 GiB | 115,200 | 8,745 | 1,498 | 24 | 4.8 MiB | 0.8 GiB |
| 30 | 1.22 GiB | 3,456,000 | 263,499 | 44,955 | 699 | 5.5 MiB | 26.4 GiB |
| 90 | 3.68 GiB | 10,368,000 | 790,401 | 134,865 | 2,100 | 5.1 MiB | 79.9 GiB |
| 180 | 7.39 GiB | 20,736,000 | 1,581,294 | 269,735 | 4,191 | 5.3 MiB | 160.4 GiB |
| 270 | 11.10 GiB | 31,104,000 | 2,371,659 | 404,599 | 6,289 | 5.2 MiB | 240.7 GiB |
| 365 | 15.02 GiB | 42,048,000 | 3,205,992 | 546,957 | 8,508 | 5.2 MiB | 325.4 GiB |
| 400 | 15.04 GiB | 42,048,000 | 3,206,975 | 546,960 | 8,509 | 6.4 MiB | 355.6 GiB |

### Full-size space requirements derived from it (for the owner to agree)

| Where | What | Space at a full 365-day window |
|---|---|---|
| CM4 eMMC (CM4004032: 32 GB, about 29.8 GiB) | working database | 15.0 GiB |
| | WAL, transient allowance (ADR-0008 R1; measured peak 6.4 MiB) | 1 GiB |
| | free-space warning threshold (`free_space_warning_bytes`) kept free | 4 GiB |
| | OS, application, logs | not measured: CP-00 (left over: about 9.8 GiB) |
| SD card (64 GB ext4, about 58 GiB usable, inferred from the card size) | seven local daily backups plus one being written | 8 x 0.82 = 6.6 GiB |
| | restore drill: downloaded cloud copy + restored database x 1.2 + 1 GiB headroom (the 10 GiB minimum is exceeded) | 0.82 + 18.05 + 1 = **19.9 GiB** |
| | total | about 26.5 GiB, leaving about 31 GiB |
| OneDrive folder | 30 daily + 12 monthly encrypted copies | 42 x 0.82 = 34.4 GiB |

Proposed (not applied): raise `db_budget_bytes` for 80-point profiles from 10 GiB to 17 GiB
(measured 15.0 GiB plus about 13 %). Retention (365 days), cadence (60 s) and the SQLite format stay
as they are. The drill preflight already sizes itself from the backup's database size, so the 19.9
GiB is enforced without a configuration change. All of this is desktop evidence: the eMMC, the card
and the OneDrive copy are measured on the CM4 at CP-00.
