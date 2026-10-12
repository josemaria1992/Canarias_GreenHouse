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
python scripts/stress_concurrency.py --db OUT/measure.sqlite3 --out OUT/stress
```

- 48-point run: commit ⟪COMMIT⟫, script SHA-256 ⟪SHA_MEASURE⟫, ⟪M48_TIMES⟫.
- 24-point run: commit ⟪M24_COMMIT⟫ (same script).
- Stress run: commit ⟪STRESS_COMMIT⟫, script SHA-256 ⟪SHA_STRESS⟫, started ⟪ST_TIMES⟫.
- Environment: ⟪ENV⟫. Every report carries the full environment block (command line, seed,
  package versions, file system).

## Workload

Through the real write path, on a simulated timeline that ends when the run starts: complete
snapshots every 60 s; 20 state changes per digital input per day; 200 commands a day with durable
intent and outcome; 24 alarms a day (raise, acknowledge, clear, close; active alarms refreshed every
minute); every 7 days a 30-minute fault of one module with an input chattering on every 3 s poll for
10 minutes; the server's hourly bounded retention. Generated over 400 days: ⟪GEN⟫.

## 48 points, 400 days

| Quantity | Measured |
|---|---|
| Growth while the window fills | ⟪GROWTH_MB_DAY⟫ MB (⟪GROWTH_MIB_DAY⟫ MiB) per day |
| Database file at day 365 / day 400 | ⟪DB365⟫ GiB / ⟪DB400⟫ GiB (⟪DB_DELTA⟫) |
| Rows at day 400: samples, events, commands, alarm occurrences | ⟪ROWS400⟫ |
| Average database bytes per sample row | ⟪ROW_B⟫ |
| WAL: largest at the hourly samples / after close | ⟪WAL_MAX_MIB⟫ MiB / ⟪WAL_REST⟫ |
| Bytes the process wrote to storage | ⟪WRITTEN⟫ |
| Compressed backup | ⟪BK_GIB⟫ GiB (⟪BK_RATIO⟫ times smaller), written and verified in ⟪BK_MIN⟫ min |
| Quick verification | ⟪QV_S⟫ s |
| Restore drill | ⟪DRILL⟫ |

Database and WAL are reported separately; bytes written are a different quantity (everything the
process sent to the file system, including WAL frames, checkpoints, page rewrites and retention
deletes) and are not eMMC endurance evidence.

⟪CP_TABLE⟫

## 24 points, 30 days

⟪M24⟫.

## Concurrency stress (R1)

Real time on the 400-day database: snapshots at 1 per second (60 times the approved rate),
⟪ST_EVENTS⟫ events a second with a burst of ⟪ST_BURST⟫ a second for ⟪ST_BURST_S⟫ s and an alarm storm on every
analog point, a command every 2 s, the acquisition loop's storage check on every snapshot; during
it a consistent backup, year-range readers and retention catching up a 7-day backlog.

| Quantity | Result |
|---|---|
| Backup | ⟪ST_BACKUP⟫ |
| Year-range readers | ⟪ST_READERS⟫ |
| Retention catch-up | ⟪ST_RET⟫ |
| Largest WAL / afterwards | ⟪ST_WAL⟫ |
| Lowest free space (largest drop) | ⟪ST_FREE⟫ |
| Snapshots scheduled / written; lateness p99 / max | ⟪ST_SNAP⟫ |
| Writer round trip p99 / max; command intent p99 / max | ⟪ST_LAT⟫ |
| Status read p99 / max | ⟪ST_STATUS⟫ |
| Lost | ⟪ST_LOST⟫ |

## Test summary

⟪TESTSUMMARY⟫
