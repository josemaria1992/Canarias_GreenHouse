# ADR-0008 Historian recording interval, 365-day retention and storage budget
Status: proposed, revised 2026-10-12 after the owner's review (R1 to R5); awaiting the owner's
acceptance. Supersedes the open "demo retention versus demo budget" question of 2026-10-11.

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

The owner reviewed the first version of this ADR the same day: the architecture is acceptable in
principle, with revisions requested on the WAL (R1), recording time (R2), backups (R3), retention
and the clock (R4), and wording and evidence (R5). This version answers them; the evidence is under
*Measurements* and in `ACCEPTANCE.md`.

## Decision

### Recording (R2)

`historian.recording_interval_s` accepts only 60 or 300; the default and every shipped configuration
use 60. One recorder takes a complete snapshot every 60 s of **wall time**, in real and simulation
profiles alike. It never touches a bus: it copies what acquisition already holds, one row per
configured point. A reading older than its staleness limit (three missed polls, at least poll
interval + 1 s) is written as `STALE` with a null value and the old reading in `last_good_*`; a point
not yet acquired in this process is `UNAVAILABLE`.

- **Simulation.** Each snapshot of a simulation stores the run id and the simulated time as
  provenance. The speed (1×, 10×, 60×) or a pause does not change how many snapshots an hour of wall
  time produces. A paused or stalled run keeps recording its frozen state as `STALE` after 3 s
  without progress, with the last good value kept, and never back-fills.
- **Optional simulation-time series.** `historian.sim_time_recording_s` (off by default) adds a
  separately named series every that many simulated seconds, never more often than
  `sim_time_recording_min_wall_s` of wall time. It is stored with `cadence_basis = SIMULATION_TIME`
  and `simtime/...` ids, counted separately by `storage report`, and selected in trends with
  `basis=SIMULATION_TIME`. It is not part of the measured budget, because it is off.
- **Truthful cadence.** Every stored cycle carries `cadence_s` and `cadence_basis`: `WALL` for the
  snapshots, `SIMULATION_TIME` for the optional series, `LEGACY_UNKNOWN` for imported v0.1 history.
- **Scheduling and labels.** The next wake-up is computed on the monotonic clock, so a wall-clock
  step never causes a burst and never makes the recorder sleep for the length of the step. The UTC
  label of a snapshot is the wall-clock boundary at or before the moment it is taken: hh:mm:00 for
  60 s; hh:00, hh:05, ... hh:55 for 300 s (multiples of 300 s since the Unix epoch, which are
  five-minute boundaries in every time zone in use). The label is identity and presentation only.
- **No duplicates.** A label is written at most once per database. The recorder starts after the
  newest label already stored, and the writer refuses, inside the same transaction that would write
  it, any label not after the newest stored one. This holds across restarts and clock corrections
  without a schema change (no new index, so no migration).
- **Clock steps.** A clock set forward writes one snapshot and logs the skipped labels; a clock set
  back holds snapshots (`HISTORY_CLOCK_BEHIND`, event and WARNING alarm) until the clock passes the
  newest stored label, and `HISTORY_CLOCK_RESUMED` reports how many intervals were held.
- **Missing intervals** are logged as `HISTORY_SNAPSHOTS_MISSED` with their cause (`NOT_RUNNING`
  between processes, `STALLED` process, `CLOCK_STEP_FORWARD`) and shown as gaps in trends; labels not
  written during a low-disk pause are counted by the pause instead (count, first and last label in
  Settings). Nothing is back-filled or fabricated.

**Timing that does not change.** Bus polling (`poll_interval_s`, 3 s), edge detection, alarm
evaluation, the dashboard and the simulation's 0.1 s physics keep their rates. Polls no longer write
history rows.

**Immediate records.** DI/DO edges (with their 3 s observation window), alarm transitions (raise,
clear, acknowledge, reset, close), commands with intent and outcome, bench leases, user changes and
`CONFIG_LOADED` / `CONFIG_CHANGED` are written when they happen. A still-active alarm refreshes its
stored last-seen time and value only with the next snapshot, which avoids an eMMC commit every poll:
the stored last-seen of an active alarm is at most one recording interval (60 s) old, and after a
crash it can be that much behind. Its raise and clear times are exact.

**Schema.** Unchanged (version 2, as on `main`). Opening an existing database applies no migration
and rewrites nothing. `STALE` is a new value in the existing quality text column.

### WAL (R1)

`journal_size_limit` (64 MiB) is the size the WAL is cut back to when a checkpoint resets it; it is
not a ceiling. While any reader holds an old snapshot, the WAL cannot reset and grows with every
commit. The allowance is therefore:

| WAL state | Allowance | Basis |
|---|---|---|
| Between automatic checkpoints | about 6 MiB | measured maximum ⟪WAL_MAX_MIB⟫ MiB over 400 days (*Measurements*) |
| At rest after a reset | ≤ 64 MiB | `journal_size_limit` |
| Transient, while a long reader holds it | ≤ 1 GiB | `wal_reader_abort_bytes`; the backup aborts there |

Controls:

- **Monitoring.** The storage report gives the database file and the WAL separately. The writer runs
  a non-blocking (`PASSIVE`) checkpoint every 60 s and records how far it got; frames left behind
  mean a reader is holding the WAL, and Settings shows since when. A WAL above `wal_warning_bytes`
  (256 MiB) raises `STORAGE_CAPACITY` (WARNING) naming the cause.
- **Bounded readers.** The backup is the longest reader; it aborts cleanly at a 1 GiB WAL, below the
  free-space reserve or after `backup_max_seconds` (3 h). Web trends read at most 20,000 rows per
  point for 16 points; CSV export pages at most 200,000 rows; the audit export stops at 200,000 rows
  with a `TRUNCATED` marker; archives page by 50,000 rows with the server stopped.
- **Bounded writers.** Retention deletes at most 5,000 rows per transaction and 100 transactions per
  run, and does not start a transaction while the WAL is above the warning size
  (`RETENTION_DEFERRED`); the rest waits for the next hourly run.
- **Acquisition stays responsive.** The reads the acquisition loop and status page depend on
  (capacity check, open alarms, newest label) use their own connection, so a long export never
  delays a poll cycle. Commands wait only for their durable intent, a single short transaction.
- **Recovery near critical storage.** Free-space thresholds still apply (table below). A long reader
  is ended by its own guard or by stopping the export/backup; the next checkpoint then resets the
  WAL. The software never deletes `-wal` or `-shm` files, and the operations guide says never to:
  that would lose committed history.

### Retention and the clock (R4)

Rolling window of `retention_days = 365` (minimum 365) under the `APPROVED` policy. While the server
runs, rows older than the window are deleted every hour in the bounded runs above: samples, cycles,
events, finished commands, closed alarm occurrences, ended bench leases and expired sign-in sessions.
Each run that deletes anything writes `RETENTION_PRUNED`. The `DEMO` policy deletes nothing and never
holds; the UI and docs say that its database grows without bound.

**Registers and lifecycle.** Open alarms, users and the schema, migration and archive registers are
never pruned. Process sessions and simulation runs are pruned only once no remaining row refers to
them, so provenance is never cut. The database therefore levels off once the window is full but
does not stop growing completely; monitoring and `storage report` continue after the first year.
Keeping history beyond the window is an archive (gzip CSV with a checksummed manifest), copied off
the device.

**Clock trust.** Deleting by date trusts the wall clock, and the device may start offline with no
internet time. No time source is assumed. Retention deletes only while the clock is trusted:

- at start, the clock is at most 5 min (`CLOCK_TOLERANCE_S`) behind and at most 1 h
  (`CONFIRM_AFTER_DOWNTIME_S`) ahead of the newest record any earlier process stored (snapshot,
  event, command, process start or stop). A restart within the hour is trusted; a longer power cut,
  an RTC-less boot or a clock set back is not;
- while running, the clock moves with the monotonic clock within 5 min. An NTP correction or a manual
  change larger than that, in either direction, is caught.

Anything else holds deletion and raises `RETENTION_HELD` (WARNING) with the reason. The 30-day
threshold of the first version is gone: it missed smaller jumps. Holding only keeps more history
(about ⟪GROWTH_MB_DAY⟫ MB a day).

**Release.** Acknowledging the alarm only records the acknowledgement. Deletion resumes only when an
administrator confirms the proposed cutoff: Settings (or `GET /api/retention`) shows the reason, the
clock reading, the newest record of an earlier process, the proposed cutoff and the rows it would
delete; the confirmation (`POST /api/retention/confirm-clock`, permission `config_admin`) must echo
that cutoff within 10 min, otherwise it is refused with the new proposal. It is recorded as
`RETENTION_CLOCK_CONFIRMED` with the administrator, the cutoff, the hold and the counts. The command
line follows the same rule (`retention apply --confirm-cutoff`, server stopped). Rows inside the
window are never deleted by a release.

### Never deleting inside the window

No size limit, budget or low-disk condition deletes or thins history inside the window. Capacity
problems are alarms with defined effects:

| Condition | Alarm | Effect |
|---|---|---|
| Database over `db_budget_bytes`, or 365-day projection over it, or free disk < 4 GiB, or WAL > 256 MiB | `STORAGE_CAPACITY` WARNING | none, beyond retention waiting on a large WAL; visible in Overview/Settings |
| Free disk < 2 GiB (`free_space_reserve_bytes`) | `STORAGE_PRESSURE` CRITICAL | new starts, ON commands and backups refused |
| Free disk < 512 MiB (`snapshot_floor_bytes`) | `HISTORY_SNAPSHOTS_PAUSED` CRITICAL | periodic snapshots pause (counted, shown as a gap); events, commands and alarms still written; resumes above the reserve |
| A commit fails (disk full, I/O error) | `STORAGE_WRITE_FAILED` CRITICAL | batch rolled back, health degraded, starts refused |

None of these stops real equipment.

### Backups (R3)

**One shared snapshot.** A backup opens one read transaction. Inside it, it reads the table list, the
row count of every table, the schema objects (tables and indexes, digested), the schema version and
`user_version`, the history range and the first/last-row fingerprints, and streams the SQL dump of the
same snapshot into gzip. The writer keeps committing meanwhile without affecting it, so the manifest
and the dump describe one database state; the dump's row count per table must equal the manifest's.

**Verify, then publish atomically.** The stream is written to `*.partial`, re-read and verified
(checksum, gzip CRC, every statement, closing `COMMIT`, row counts). Only then is it renamed into place
and its manifest written through a temporary file and renamed; the manifest is the commit marker.
A failed, interrupted or aborted backup leaves no partial file and no manifest, and earlier backups
are untouched. Rotation (`--keep 7`) removes old backups only after the new one is published.

**Space while writing.** Creating a backup writes no full-size temporary copy of the database; the
only extra file is the compressed stream. That is the only operation the "no full-size temporary
copy" statement covers. The start check reserves a conservative estimate of the next backup (the
previous one × 1.5, at least a quarter of the database's used size), because the compression ratio
depends on the data and is not guaranteed. While writing, free space, the WAL and the elapsed time
are checked every 2 s and the backup aborts below the reserve. With 7 kept, an 8th exists while the
next one is written; the budget counts 8.

**Operations that need a whole database of space.** Full verification (the restore drill), a restore
and the safety copy before a schema migration each need the database's size plus 20 %, checked
against the reserve before they start (and while the drill runs). They use a separate scratch
destination: on the 32 GB eMMC, a one-year database cannot be restored next to the live one while
keeping the reserve.

**Restore drill.** A checksum alone is not a proven restore. The drill restores into the scratch
destination and checks `integrity_check`, `foreign_key_check`, the schema version, schema objects and
`user_version`, the row count of every table, the first/last-row fingerprints, and the application's
own start-up and queries on the restored copy (trends, events, alarms, the newest label, a historian
start with the capacity report). It runs once at installation and after each upgrade, then monthly
(`aquacrop-restore-drill.timer`, `restore_drill_interval_days` = 30) when an approved scratch
destination is mounted; it writes a JSON report that Settings shows, with the drill marked due
otherwise. The off-device backup destination, still an owner decision, is shown as not configured.

## Measurements (desktop, 2026-10-12)

Reproduction, environment and raw output: `docs/operations/measurements/` (⟪MEAS_MD⟫). Both scripts
create their own scratch database; nothing touches an installation.

```text
python scripts/measure_storage.py --days 400 --inventory 48 --out OUT            # seed 1
python scripts/measure_storage.py --days 30 --inventory 24 --out OUT24
python scripts/stress_concurrency.py --db OUT/measure.sqlite3 --out OUT/stress
```

Environment: ⟪ENV⟫. Commit ⟪COMMIT⟫ (script checksums in the reports).

**Workload** through the real write path, on a simulated timeline ending at the run's start: complete
snapshots of 48 points every 60 s; 20 state changes per digital input per day; 200 operator commands
a day (durable intent, outcome, output edge); 24 alarms a day (raise, acknowledge, clear, close, with
still-active alarms refreshed every minute); every 7 days a 30-minute fault of one module (its points
`UNAVAILABLE`, a `DEVICE_COMM` alarm) with an input chattering on every poll for 10 minutes; the
server's hourly bounded retention. Values are random walks.

| Quantity | Measured |
|---|---|
| Growth while the window fills | ⟪GROWTH_MB_DAY⟫ MB (⟪GROWTH_MIB_DAY⟫ MiB) per day, linear |
| Database file after 365 days | ⟪DB365⟫ GiB |
| Database file after 400 days (window full, pruned hourly) | ⟪DB400⟫ GiB (⟪DB_DELTA⟫ in the 35 days after the window filled) |
| Rows at day 400: samples, events, commands, alarm occurrences | ⟪ROWS400⟫ |
| WAL: largest seen at the hourly samples / at rest after close | ⟪WAL_MAX_MIB⟫ MiB / ⟪WAL_REST⟫ |
| Bytes the process wrote to storage over 400 days | ⟪WRITTEN⟫ |
| Compressed backup of the one-year database | ⟪BK_GIB⟫ GiB (⟪BK_RATIO⟫ times smaller), written and verified in ⟪BK_MIN⟫ min |
| Quick verification (checksum, stream, row counts) | ⟪QV_S⟫ s |
| Restore drill (restore to scratch, integrity, foreign keys, schema, counts, fingerprints, application start-up) | ⟪DRILL⟫ |
| 24 points, 30 days (same workload rates) | ⟪M24⟫ |

The bytes written are what the measurement process sent to the storage layer (WAL, checkpoints,
page rewrites, retention deletes) on a desktop file system. They are not eMMC endurance evidence: the
eMMC's own write amplification is unknown, and endurance is assessed in CP-00.

**Concurrency stress (R1 evidence)** on the 400-day database, in real time: snapshots at 1 per
second (60 times the approved rate), ⟪ST_EVENTS⟫ events a second with a burst of ⟪ST_BURST⟫ a second for
⟪ST_BURST_S⟫ s and an alarm storm on every analog point, a command every 2 s; during it a consistent
backup, year-range readers and a retention run catching up a 7-day backlog.

| Quantity | Result |
|---|---|
| Backup | ⟪ST_BACKUP⟫ |
| Year-range readers | ⟪ST_READERS⟫ |
| Retention catch-up | ⟪ST_RET⟫ |
| Largest WAL / at rest afterwards | ⟪ST_WAL⟫ |
| Lowest free space (drop during the run) | ⟪ST_FREE⟫ |
| Snapshots scheduled / written; lateness p99 / max | ⟪ST_SNAP⟫ |
| Writer round trip p99 / max; command intent p99 / max | ⟪ST_LAT⟫ |
| Status read p99 / max | ⟪ST_STATUS⟫ |
| Events and commands lost | ⟪ST_LOST⟫ |

Timings are from an x86 desktop; the CM4 and its eMMC will be slower, and growth, backup time, WAL
behaviour and free space are to be verified on the device in CP-00 (`storage report`,
`measure_storage.py --days 7`, `stress_concurrency.py`).

Sizes: the owner's benchmark of about 7.93 GiB/year for periodic samples is consistent with this
measurement. Real signals should change the size little (inferred: a sample row averages ⟪ROW_B⟫
bytes in the database, of which the value takes 8). At 300 s the year would be about one fifth.

## Budget (32 GB eMMC, CM4004032)

Usable space is an estimate: the 32 GB eMMC is about 29 GiB; after the boot partition, file-system
overhead and the 5% root reservation about 26.5 GiB remain. The OS footprint is to be verified in
CP-00.

| Item | Allowance | Basis |
|---|---|---|
| OS, Python runtime and packages | 4.0 GiB | estimate; to be verified in CP-00 |
| Current and previous release, wheelhouse | 1.0 GiB | estimate |
| System journal | 0.2 GiB | `journald-aquacrop.conf` (`SystemMaxUse=200M`) |
| Historian database (`db_budget_bytes`) | 10.0 GiB | ⟪DB400⟫ GiB measured for a full window with the workload above, plus room for more traffic |
| SQLite WAL | 1.0 GiB | ≤ 64 MiB at rest; up to 1 GiB transient while a long reader holds it (`wal_reader_abort_bytes`) |
| Local backups (7 kept + 1 being written) | ⟪BK_BUDGET⟫ GiB | 8 × ⟪BK_GIB⟫ GiB measured, plus margin; the start check keeps up to ⟪BK_EST⟫ GiB free for the one being written |
| Free-space reserve (`free_space_reserve_bytes`) | 2.0 GiB | below it, starts, ON commands and backups are refused |
| **Total** | **⟪TOTAL⟫ GiB** | about ⟪UNALLOC⟫ GiB of the 26.5 GiB left unallocated |

Expected free space once the window is full, with 7 backups: about ⟪FREE_FULL⟫ GiB (warning below
4 GiB, reserve 2 GiB, snapshot floor 512 MiB). Not in the budget, because they need a separate
scratch destination: the restore drill and a restore (⟪SCRATCH⟫ GiB for a full window) and the safety
copy before a future schema migration (the same).

For comparison, the superseded 2 GiB limit: at ⟪GROWTH_MIB_DAY⟫ MiB a day, 60 days take about
⟪D60⟫ GiB and 2 GiB holds about ⟪D2G⟫ days. Keeping 7 uncompressed copies of the database would need
about ⟪UNCOMP⟫ GiB, which is why backups are compressed.

## Consequences

- Projected to fit the existing 32 GB eMMC under the measured desktop workload, with the current
  schema, 60 s and 365 days, pending CP-00. No change of retention, recording frequency, storage
  hardware or database architecture is proposed.
- The projection assumes the OS footprint, the compression of real signals and the eMMC's speed; all
  are to be verified on the device in CP-00 with `aquacrop storage report`, `measure_storage.py` and
  `stress_concurrency.py`.
- An off-device backup destination is still an owner decision; local copies do not protect against
  loss of the eMMC. The same destination (or a USB disk) is needed as scratch space for the monthly
  restore drill.
- A future schema migration takes a full-size safety copy first (about ⟪DB400⟫ GiB once the window is
  full, plus 20 %). That does not fit on the eMMC beside a full window while keeping the reserve, so
  such a migration refuses to start until scratch space is provided or its safety copy is switched to
  the compressed format. This update has no migration.
- Trends from real installations have one point per minute per signal; edges and alarm transitions
  keep their own timestamps between minutes.

## Recommendation

Retain the proposed 10 GiB main-database allowance for evaluation, without shortening the approved
one-year window or enabling five-minute recording.
