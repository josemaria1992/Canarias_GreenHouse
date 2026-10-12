# Historian measurements, 2026-10-12 (owner review of ADR-0008, R1 and R5)

Desktop evidence for ADR-0008: storage growth at the approved 60 s cadence over a full 365-day
window, the WAL, bytes written, backup and restore drill, and a concurrency stress run. These are
not CM4 or eMMC measurements; those belong to CP-00. Values are synthetic random walks.

The first measurement of 2026-10-12 (commit `f50c99f`, without environment capture or command,
alarm and fault traffic) is superseded by these and remains in the git history.

## Files

| File | What |
|---|---|
| `2026-10-12-storage-48pt-60s-400d.json` | `measure_storage.py`, 48 points, 400 days: environment, workload, checkpoints, final storage, WAL, bytes written, backup, restore drill |
| `2026-10-12-storage-24pt-60s-30d.json` | the same, 24 points, 30 days |
| `2026-10-12-stress-48pt-400d.json` | `stress_concurrency.py` on the 400-day database: marks, results, latencies, timeline every 5 s |

## Reproduce

```text
python scripts/measure_storage.py --days 400 --inventory 48 --out OUT       # seed 1 (default)
python scripts/measure_storage.py --days 30 --inventory 24 --out OUT24
python scripts/stress_concurrency.py --db OUT/measure.sqlite3 --out OUT/stress --backlog-days 14
```

- 48-point run: commit `5314251`, script SHA-256 `ef5e9daf0cc38d7adb1946819a540e646e4c87ce9118ee84c731d437815668ae`, 2026-10-12T12:34:36+00:00 to 2026-10-12T13:11:15+00:00.
- 24-point run: commit `c1ca943` (same script).
- Stress run: commit `bbe528f`, script SHA-256 `6c27eb6623de6d1812a8f3d0748c123018fe0f73f392be365ef11e88a31c1485`, started 2026-10-12T14:02:25+00:00, 8.1 min.
- Environment: Python 3.13.16, SQLite 3.45.1, Linux-6.18.44-fc-v114-x86_64 (Intel(R) Xeon(R) Processor @ 2.10GHz, 4 CPUs, 16 GiB RAM, ext4); pydantic 2.14.0, FastAPI 0.143.0, pymodbus 3.11.3. Every report carries the full environment block (command line, seed,
  package versions, file system).

## Workload

Through the real write path, on a simulated timeline that ends when the run starts: complete
snapshots every 60 s; 20 state changes per digital input per day; 200 commands a day with durable
intent and outcome; 24 alarms a day (raise, acknowledge, clear, close; active alarms refreshed every
minute); every 7 days a 30-minute fault of one module with an input chattering on every 3 s poll for
10 minutes; the server's hourly bounded retention. Generated over 400 days: 80,000 commands, 9,600 alarms raised, 57 device faults, 11,400 chatter edges, 0 retention runs stopped early.

## 48 points, 400 days

| Quantity | Measured |
|---|---|
| Growth while the window fills | 23.8 MB (22.7 MiB) per day |
| Database file at day 365 / day 400 | 8.10 GiB / 8.11 GiB (+12 MiB) |
| Rows at day 400: samples, events, commands, alarm occurrences | 25,228,800; 198,590; 73,000; 8,493 |
| Average database bytes per sample row | 345 |
| WAL: largest at the hourly samples / after close | 5.6 MiB / 0 bytes |
| Bytes the process wrote to storage | 83 GiB, about 0.21 GiB a day (9 times the daily growth of the file) |
| Compressed backup | 0.49 GiB (16.6 times smaller), written and verified in 5.8 min |
| Quick verification | 44 s |
| Restore drill | passed in 9.1 min (7.83 GiB restored, 9.73 GiB scratch required; integrity ok, 0 foreign-key violations, schema 2, application start-up ok) |

Database and WAL are reported separately; bytes written are a different quantity (everything the
process sent to the file system, including WAL frames, checkpoints, page rewrites and retention
deletes) and are not eMMC endurance evidence.

| Day | Database file | Samples | Events | Commands | Alarm occurrences | WAL before checkpoint | Process bytes written |
|---|---|---|---|---|---|---|---|
| 1 | 0.02 GiB | 69,120 | 529 | 200 | 24 | 4.9 MiB | 0.2 GiB |
| 7 | 0.15 GiB | 483,840 | 3,808 | 1,400 | 167 | 5.2 MiB | 1.4 GiB |
| 30 | 0.66 GiB | 2,073,600 | 16,275 | 6,000 | 707 | 5.4 MiB | 6.1 GiB |
| 60 | 1.32 GiB | 4,147,200 | 32,504 | 12,000 | 1,411 | 5.4 MiB | 12.2 GiB |
| 90 | 1.98 GiB | 6,220,800 | 48,782 | 18,000 | 2,111 | 5.2 MiB | 18.5 GiB |
| 120 | 2.65 GiB | 8,294,400 | 65,184 | 24,000 | 2,813 | 5.5 MiB | 24.7 GiB |
| 150 | 3.31 GiB | 10,368,000 | 81,250 | 30,000 | 3,506 | 5.6 MiB | 31.0 GiB |
| 180 | 3.98 GiB | 12,441,600 | 97,494 | 36,000 | 4,201 | 5.4 MiB | 37.2 GiB |
| 210 | 4.65 GiB | 14,515,200 | 113,837 | 42,000 | 4,900 | 5.2 MiB | 43.4 GiB |
| 240 | 5.32 GiB | 16,588,800 | 130,188 | 48,000 | 5,600 | 5.3 MiB | 49.6 GiB |
| 270 | 5.98 GiB | 18,662,400 | 146,456 | 54,000 | 6,297 | 5.3 MiB | 55.8 GiB |
| 300 | 6.65 GiB | 20,736,000 | 162,606 | 60,000 | 6,995 | 5.4 MiB | 62.0 GiB |
| 330 | 7.32 GiB | 22,809,600 | 178,853 | 66,000 | 7,691 | 5.3 MiB | 68.2 GiB |
| 360 | 7.98 GiB | 24,883,200 | 194,976 | 72,000 | 8,392 | 5.4 MiB | 74.3 GiB |
| 365 | 8.10 GiB | 25,228,800 | 197,698 | 73,000 | 8,511 | 5.1 MiB | 75.3 GiB |
| 390 | 8.11 GiB | 25,228,800 | 198,339 | 73,000 | 8,501 | 5.4 MiB | 81.1 GiB |
| 400 | 8.11 GiB | 25,228,800 | 198,590 | 73,000 | 8,493 | 5.4 MiB | 83.4 GiB |

## 24 points, 30 days

12.0 MB (11.4 MiB) per day, about 4.1 GiB a year; backup 16.4 times smaller; restore drill passed.

## Concurrency stress (R1)

Real time on the 400-day database: snapshots at 1 per second (60 times the approved rate),
5 events a second with a burst of 100 a second for 60 s and an alarm storm on every
analog point, a command every 2 s, the acquisition loop's storage check on every snapshot; during
it a consistent backup, year-range readers and retention catching up a 14-day backlog.

| Quantity | Result |
|---|---|
| Backup | published after 5.9 min (0.48 GiB, verified before publishing; largest WAL it saw 377 MiB) |
| Year-range readers | trend, 16 points over a year (API limit): 5.8 s; CSV export page of 200,000 rows: 6.6 s; unbounded full-table aggregate (not an API path): 60.5 s |
| Retention catch-up | 6 runs, 499,756 rows deleted in 108 transactions; run 1: 494,165 rows, stopped (100 delete transactions in this run); run 2: 0 rows, stopped (WAL 273 MiB above 256 MiB: a long reader is holding it back); run 3: 0 rows, stopped (WAL 304 MiB above 256 MiB: a long reader is holding it back); run 4: 0 rows, stopped (WAL 330 MiB above 256 MiB: a long reader is holding it back); run 5: 0 rows, stopped (WAL 357 MiB above 256 MiB: a long reader is holding it back); run 6: 5,591 rows, finished |
| Largest WAL / afterwards | 377 MiB (246 s above the 256 MiB warning) / 64 MiB after the readers ended, 0 bytes after close |
| Lowest free space (largest drop) | 19.37 GiB (869 MiB) |
| Snapshots scheduled / written; lateness p99 / max | 485 / 485; 1.1 ms / 2.2 ms |
| Writer round trip p99 / max; command intent p99 / max | 4.7 ms / 813 ms; 23 ms / 547 ms |
| Status read p99 / max | 2.5 ms / 6.9 ms |
| Lost | 0 of 8,398 events; 0 of 241 commands without outcome or refused |

The first stress run (commit `c7b2ca6`) found a defect: the capacity check that runs on every poll read the oldest and newest sample times in one `SELECT min(), max()`, which SQLite answers with a full index scan, 2.4 s on the one-year database. Two threads calling it kept a reader open almost continuously, the WAL could not reset and grew past 630 MiB, and retention deferred itself every time. That run was stopped. Commit `bbe528f` splits the query into two indexed lookups (0.3 ms) and a test now checks the query plan of every per-poll status query. The results above are from the rerun after the fix, on the same database; the stopped run had already pruned part of the backlog, so the rerun moved the clock 14 days ahead and the table gives what retention actually deleted.

## Test summary

At commit `bbe528f` (only documentation changed after): `python -m pytest` 251 passed on Python 3.11.15, 3.12.13 and 3.13.16, `pip check` clean; `demo verify --seed 42` 21/21 PASS for `demo-48` and `demo-24`; installed-wheel check 13/13 PASS. GitHub CI results are on PR #1. All of these run on simulated or in-memory devices; none is a hardware test.
