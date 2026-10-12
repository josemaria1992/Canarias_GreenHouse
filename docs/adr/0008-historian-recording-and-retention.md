# ADR-0008 Historian recording interval, 365-day retention and storage budget
Status: accepted (owner requirements of 2026-10-12). Supersedes the open "demo retention versus
demo budget" question of 2026-10-11.

## Context

The first release wrote every acquisition cycle (48 points every 3 s, about 445 MiB/day), proposed
7 days of retention and a 2 GiB budget that could not hold it. On 2026-10-12 the owner set the
historian requirements:

1. keep one year of history as a rolling 365-day window;
2. record complete snapshots (unchanged readings and data quality included) every 60 s;
3. allow 300 s as a configuration, but keep 60 s active until the owner explicitly asks for 300 s;
4. keep acquisition, control, alarm evaluation and dashboard timing separate and unchanged;
5. record commands, outcomes, alarm transitions, configuration changes and observed digital-state
   changes when they happen, not at minute boundaries;
6. keep the existing SQLite database and its history: no migration, no hardware purchase;
7. recalculate storage at 60 s, verify actual growth, replace the 2 GiB limit;
8. never silently delete inside the window to satisfy a size limit; bounded retention, visible
   capacity monitoring, defined low/full-disk handling;
9. verify backups, retention boundaries, intervals, immediate events, stale readings and restarts.

## Decision

**Recording.** `historian.recording_interval_s` accepts only 60 or 300; the default and every
shipped configuration use 60. On a real installation a recorder thread writes one complete
snapshot on each wall-clock boundary (hh:mm:00). It never touches a bus: it copies what acquisition
already holds. Each snapshot has one row per configured point. A reading older than its staleness
limit (three missed polls, at least poll interval + 1 s) is written as `STALE` with a null value and
the old reading in `last_good_*`; a point not yet acquired in this process is `UNAVAILABLE`. A
boundary is written at most once per process; boundaries that could not be written (clock step,
stalled process) produce `HISTORY_SNAPSHOTS_MISSED`. In simulation the interval is simulated time.

**Timing that does not change.** Bus polling (`poll_interval_s`, 3 s), edge detection, alarm
evaluation, the dashboard and the simulation's 0.1 s physics keep their rates. Polls no longer
write history rows themselves.

**Immediate records.** DI/DO edges (with their 3 s observation window), alarm transitions (raise,
clear, acknowledge, reset, close), commands with intent and outcome, bench leases, user changes and
the new `CONFIG_LOADED` / `CONFIG_CHANGED` start events are written when they happen. Active
alarms only refresh their last-seen value with the snapshot, which avoids an eMMC commit every poll.

**Schema.** Unchanged (version 2, as on `main`). Snapshots use the existing `cycles` and `samples`
tables with `cadence_s = 60` and `cadence_basis = WALL`; opening an existing database applies no
migration and rewrites nothing. `STALE` is a new value in the existing quality text column.

**Retention.** Rolling window of `retention_days = 365` (minimum 365) under the `APPROVED` policy.
While the server runs, rows older than the window are deleted every hour in batches of 5,000
through the single writer: samples, cycles, events, finished commands, closed alarm occurrences,
ended bench leases and expired sign-in sessions. Open alarms, users, process sessions and the
schema/migration/archive registers stay. Each run that deletes anything writes `RETENTION_PRUNED`.
Freed pages are reused by new history, so the file stops growing after one year. The `DEMO` policy
still deletes nothing.

**Clock guard.** Deleting by date trusts the wall clock, so retention holds and raises
`RETENTION_HELD` (WARNING) when the clock is more than 30 days past, or behind, the newest stored
record at start, or steps forward by more than an hour while running. Acknowledging that alarm
after checking the date releases it.

**Never deleting inside the window.** No size limit, budget or low-disk condition deletes or thins
history inside the window. Capacity problems are alarms with defined effects:

| Condition | Alarm | Effect |
|---|---|---|
| Database over `db_budget_bytes`, or 365-day projection over it, or free disk < 4 GiB | `STORAGE_CAPACITY` WARNING | none; visible in Overview/Settings |
| Free disk < 2 GiB (`free_space_reserve_bytes`) | `STORAGE_PRESSURE` CRITICAL | new starts and ON commands refused |
| Free disk < 512 MiB (`snapshot_floor_bytes`) | `HISTORY_SNAPSHOTS_PAUSED` CRITICAL | periodic snapshots pause (counted, event); events, commands and alarms still written; resumes above the reserve |
| A commit fails (disk full, I/O error) | `STORAGE_WRITE_FAILED` CRITICAL | batch rolled back, health degraded, starts refused |

None of these stops real equipment.

**Backups.** The default backup is now a compressed SQL dump of one consistent read snapshot,
streamed into gzip (`*.sql.gz` + manifest with SHA-256 and per-table row counts). A full-size
temporary copy is never written. Verification checks the checksum, the whole gzip stream, every
statement and the row counts; `--full` also restores into a scratch database and runs
`integrity_check`. The daily timer keeps 7 and deletes older ones only after the new one verifies,
and refuses to start if it would take free disk below the reserve. Older `.sqlite3` backups remain
verifiable and restorable.

## Measurements (desktop, 2026-10-12)

`scripts/measure_storage.py`: 48 points (2 Relay (D) + 2 AI 8CH, scaled AI rows), complete
snapshots every 60 s on a simulated 400-day timeline, 20 state changes per digital point per day as
edge events, hourly retention, current schema and real-length identifiers.

| Quantity | Measured |
|---|---|
| Growth while the window fills | 23.6 MB (22.5 MiB) per day, linear |
| Database after 365 days | 8.03 GiB |
| Database after 400 days (window full, pruned hourly) | 8.05 GiB: +18 MiB in the 35 days after the window filled (about 2% of the filling rate); freed pages are reused |
| of which periodic samples, with their two indexes | 7.87 GiB (table 5.34 GiB, indexes 2.53 GiB) |
| of which snapshot headers (`cycles`) | 0.08 GiB |
| of which events (640 edges per day, hourly retention records) | 0.10 GiB |
| Compressed backup of the one-year database | 0.48 GiB (16.7 times smaller), written in 4.7 min |
| Backup verification (checksum, full stream, row counts) | 38 s |
| Restore into a new database, with `integrity_check` | 8.3 min (7.79 GiB restored) |

Timings are from an x86 desktop; the CM4 and its eMMC will be slower and are measured in CP-00.

The owner's benchmark of about 7.93 GiB/year for periodic samples is confirmed (7.87 GiB measured).
Values in the test are random walks. Real signals should change the size little (inferred): a
sample row averages 227 bytes, of which the value takes 8. `storage report` checks it on the device.
At 300 s the year would be about 1.7 GiB.

## Budget (32 GB eMMC, CM4004032)

Usable space is an estimate: the 32 GB eMMC is about 29 GiB; after the boot partition, file-system
overhead and the 5% root reservation about 26.5 GiB remain. The OS footprint is confirmed in CP-00.

| Item | Allowance | Basis |
|---|---|---|
| OS, Python runtime and packages | 4.0 GiB | estimate; confirmed in CP-00 |
| Current and previous release, wheelhouse | 1.0 GiB | estimate |
| System journal | 0.2 GiB | `journald-aquacrop.conf` (`SystemMaxUse=200M`) |
| Historian database (`db_budget_bytes`) | 10.0 GiB | 8.05 GiB measured for a full window, plus room for more edges, alarms and commands than the test assumed |
| SQLite WAL | 0.06 GiB | `journal_size_limit` 64 MiB |
| Local backups (`--keep 7`) | 4.0 GiB | 7 × 0.48 GiB measured = 3.4 GiB |
| Free-space reserve (`free_space_reserve_bytes`) | 2.0 GiB | below it, starts, ON commands and backups are refused |
| **Total** | **21.3 GiB** | about 5 GiB of the 26.5 GiB left unallocated |

Expected free space once the window is full: about 9 to 10 GiB (warning below 4 GiB, reserve 2 GiB,
snapshot floor 512 MiB). The superseded 2 GiB limit could not hold even two months at 60 s. Keeping
7 uncompressed copies of the database (as before) would have needed about 56 GiB, which is why
backups are now compressed.

## Consequences

- Fits the existing 32 GB eMMC with the current schema, 60 s and 365 days. No change of retention,
  recording frequency, storage hardware or database architecture is needed.
- The proposal assumes the OS footprint and the compression of real signals; both are checked on
  the device in CP-00 with `aquacrop storage report` (growth, projection, thresholds).
- An off-device backup destination is still an owner decision; local copies do not protect against
  loss of the eMMC.
- A future schema migration still takes a full-size safety copy first (about 8 GiB once the window
  is full). The expected 9 to 10 GiB free covers it, but narrowly; that migration should switch its
  safety copy to the compressed format.
- Trends from real installations now have one point per minute per signal; edges and alarm
  transitions keep their own timestamps between minutes.
