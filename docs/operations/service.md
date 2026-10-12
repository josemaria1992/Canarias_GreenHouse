# Operations: service, health, storage, backups, upgrade and rollback

Application health is not motor safety. Nothing here stops equipment.

## systemd service (Raspberry Pi OS)

Files in `deploy/`: `aquacrop-greenhouse.service`, `aquacrop.env.example`,
`aquacrop-backup.service` + `.timer`, `aquacrop-restore-drill.service` + `.timer`,
`journald-aquacrop.conf`. `systemd-analyze verify` parses the units; they have not run on a CM4 yet.

```text
sudo install -m 0640 -g aquacrop deploy/aquacrop.env.example /etc/aquacrop/aquacrop.env
sudo install -m 0644 deploy/aquacrop-greenhouse.service deploy/aquacrop-backup.service deploy/aquacrop-backup.timer \
     deploy/aquacrop-restore-drill.service deploy/aquacrop-restore-drill.timer /etc/systemd/system/
sudo install -D -m 0644 deploy/journald-aquacrop.conf /etc/systemd/journald.conf.d/aquacrop.conf
sudo systemctl restart systemd-journald
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop users bootstrap \
     --config /etc/aquacrop/hardware-read-only.yaml --state-dir /var/lib/aquacrop --username admin --generate
sudo systemctl daemon-reload
sudo systemctl enable --now aquacrop-greenhouse.service aquacrop-backup.timer aquacrop-restore-drill.timer
```

The restore-drill timer only runs when an approved scratch destination is mounted at
`/var/lib/aquacrop-drill` (see *Restore drill* below); until then it is skipped and Settings shows
the drill as due.

Properties of the unit: dedicated unprivileged user with only the `dialout` group; private
environment file holding just the config path; state in `/var/lib/aquacrop` (mode 0700); one
worker; `Restart=on-failure` after 10 s, at most 5 starts in 10 minutes; no dependency on
`network-online.target`. A restart always comes up `REAL — READ ONLY` (or STOPPED in simulation)
and never restores a bench lease.

Inside the process each RS485 bus has a supervised worker; if it fails more than 5 times in 10
minutes it stays `FAILED` and `/api/health` reports `acquisition_ok: false` while the web UI stays
up and says so. The simulation driver has the same supervision.

## Health

- `GET /api/health` (no sign-in): `{ok, storage_ok, acquisition_ok}` only. Suitable for a local
  watchdog: `curl -fsS http://127.0.0.1:8000/api/health`.
- `GET /api/status` (signed in): per-device health and last error, relay modes, bus worker
  heartbeat, snapshot recorder and retention state, historian queue depth, DB size, free disk,
  thresholds, 365-day projection, newest data age.
- Events worth alerting on: `POLL_OVERRUN`, `WALL_CLOCK_STEP`, `ACQUISITION_TASK_FAILED`,
  `HISTORY_SNAPSHOTS_MISSED`, `HISTORY_CLOCK_BEHIND`, `RETENTION_DEFERRED`, `BACKUP_FAILED`; alarms
  `DEVICE_COMM`, `STORAGE_CAPACITY` (also raised for a WAL above `wal_warning_bytes`),
  `STORAGE_PRESSURE`, `HISTORY_SNAPSHOTS_PAUSED`, `STORAGE_WRITE_FAILED`, `RETENTION_HELD`,
  `HISTORY_CLOCK_BEHIND`.

## Historian recording and storage (ADR-0008)

Owner requirements of 2026-10-12: a complete snapshot of every point every **60 s**, a rolling
**365-day** window, nothing deleted inside the window to make room. Bus polling (3 s), alarms, edges
and the dashboard keep their own timing; edges, alarm transitions, commands and configuration
changes are written when they happen.

Snapshots are taken every 60 s of **wall time** in every profile, simulation included (the run id
and simulated time are stored with each snapshot). Scheduling uses the monotonic clock; the UTC label
is the boundary at or before the snapshot (hh:mm:00; for 300 s hh:00, hh:05, ... hh:55) and is
written at most once per database, also across restarts and clock corrections. Labels that could not
be written are logged as `HISTORY_SNAPSHOTS_MISSED` with the cause and shown as gaps, never back-filled.
If the clock is set back behind the newest stored label, snapshots are held (event and WARNING alarm
`HISTORY_CLOCK_BEHIND`) until the clock passes it; `HISTORY_CLOCK_RESUMED` reports how many were held.

`historian.recording_interval_s` accepts 60 or 300. Keep 60 until the owner explicitly asks for
300 s; the change is a configuration edit plus a restart, and the restart records `CONFIG_CHANGED`
with the old and new value. `historian.sim_time_recording_s` (off by default) adds a separately
labelled simulation-time series for desktop analysis; it is counted on its own in the storage report.

Measured growth, the WAL and concurrency results, and the eMMC budget are in ADR-0008 and the
reports in `measurements/` (with environment, command and seed). Check the real growth on the
installation after a few weeks, and again before the window fills:

```text
python -m aquacrop storage report --config /etc/aquacrop/hardware-read-only.yaml --state-dir /var/lib/aquacrop
```

It is read-only and safe while the server runs. It prints the database file and the WAL separately,
used bytes, free disk and the thresholds, the oldest and newest record, the 365-day projection, the
growth of the last seven days, the series by recording basis and the register tables. Overview and
Settings in the web UI show the same figures.

### WAL

The write-ahead log (`<db>-wal`) holds recent commits until a checkpoint copies them into the
database file. `journal_size_limit` (64 MiB) is only the size it is cut back to when a checkpoint
resets it; it is not a ceiling. While any reader holds an old snapshot (a backup, an export, a long
query), the WAL cannot reset and grows with every commit. Expected sizes: up to about 6 MiB between
checkpoints in normal service, at most 64 MiB at rest, and a transient allowance of 1 GiB
(`wal_reader_abort_bytes`) during long reads.

- The server runs a non-blocking checkpoint every 60 s and records how far it got; Settings shows
  the WAL size and, if checkpoints are held back, since when.
- A WAL above `wal_warning_bytes` (256 MiB) raises `STORAGE_CAPACITY` naming the cause; retention
  pruning waits (`RETENTION_DEFERRED`) until it is back below.
- A backup aborts cleanly when the WAL passes 1 GiB, when free disk falls below the reserve, or after
  `backup_max_seconds` (3 h); its partial file is removed and earlier backups are kept.
- Web trends (20,000 rows per point, 16 points), CSV export pages (200,000 rows) and the audit export
  (200,000 rows, marked `TRUNCATED` beyond) are bounded reads. The acquisition loop's own reads use a
  separate connection, so an export never delays a poll cycle.
- Never delete `*-wal` or `*-shm` files while the database is open: that loses committed history. To
  shrink a large WAL, end the long reader (stop the backup or export); the next checkpoint resets it.

## Low and full disk

Nothing inside the 365-day window is deleted for any of these. None of them stops equipment.

| Condition | Alarm | What happens |
|---|---|---|
| Database over `db_budget_bytes` (10 GiB), or the 365-day projection over it, or free disk under 4 GiB | `STORAGE_CAPACITY` WARNING | Visible only. Plan space: move backups or archives off the device. |
| Free disk under 2 GiB (`free_space_reserve_bytes`) | `STORAGE_PRESSURE` CRITICAL | New starts and ON commands refused (`can_accept_starts`). The daily backup refuses to start. |
| Free disk under 512 MiB (`snapshot_floor_bytes`) | `HISTORY_SNAPSHOTS_PAUSED` CRITICAL | Periodic snapshots pause; each skipped one is counted and `HISTORY_SNAPSHOTS_PAUSED` is logged once. Events, commands and alarms are still written. Snapshots resume by themselves once free space is back above 2 GiB (`HISTORY_SNAPSHOTS_RESUMED` with the count). |
| WAL above `wal_warning_bytes` (256 MiB) | `STORAGE_CAPACITY` WARNING | A long reader holds the WAL back. Retention waits; a backup aborts at 1 GiB. End the reader to recover. |
| A commit fails (disk full, I/O error) | `STORAGE_WRITE_FAILED` CRITICAL | The batch rolls back, health goes degraded, starts refused. History that could not be queued is counted (`dropped_cycles`). |

To recover space: copy old backups or archives off the device and delete them locally, or remove
other files. Do not delete database rows by hand.

## Backups and restore

```text
python -m aquacrop backup create --config C --state-dir S --destination /var/lib/aquacrop/backups --keep 7
python -m aquacrop backup verify --backup /var/lib/aquacrop/backups            # newest: checksum, stream, counts
python -m aquacrop backup verify --backup DIR_OR_FILE --full --scratch-dir /mnt/drill --config C \
       --report-dir /var/lib/aquacrop/backups/restore-drills                   # the restore drill
python -m aquacrop backup restore --backup FILE --target /tmp/restore-test.sqlite3   # new instance first
```

**What a backup is.** A gzip-compressed SQL dump plus a manifest. The backup opens one read
transaction; inside it, it reads the row count of every table, the schema objects (tables and
indexes), the schema version and `user_version`, the history range and first/last-row fingerprints,
and streams the dump of the same snapshot. The server keeps writing meanwhile without affecting it,
so manifest and dump describe the same moment. It is safe while the server runs.

**Publication.** The dump is written to `*.partial`, then re-read and verified (checksum, whole gzip
stream, every statement, the closing `COMMIT`, row counts per table). Only a verified file is renamed
into place, and its manifest (written through a temporary file) is the commit marker: a backup
without a manifest is never listed or used. A failed, interrupted or aborted backup leaves no partial
file, and the earlier backups are untouched. `--keep 7` removes the oldest compressed backups only
after the new one is published. A `*.partial` file left by a backup process that died (power cut)
is removed by the first backup run more than 6 h later.

**Space.** No full-size temporary copy of the database is written when *creating* a backup: the only
extra file is the compressed stream. The start check refuses a backup if a conservative estimate (the
previous backup × 1.5, at least a quarter of the database) would take free disk below the reserve,
because the compression ratio depends on the data and is not guaranteed. While it writes, free disk,
the database WAL and the elapsed time are checked every 2 s, and it aborts cleanly below the reserve,
above a 1 GiB WAL or after 3 h. With 7 backups kept, an 8th exists briefly while the next one is
written.

**Restore and full verification need room for a whole database.** `verify --full` (the restore
drill), `restore` and the safety copy taken before a schema migration each need the database's size
plus 20 %, checked against the reserve before they start (and, for the drill, while it runs). On the
32 GB eMMC a one-year database cannot be restored beside the live one while keeping the reserve, so
these use a separate scratch destination.

`create` exits 3 when refused for space and 1 when it failed or aborted (nothing published, earlier
backups kept); the timer then shows as a failed unit (`systemctl --failed`). Restore refuses an
existing target unless `--maintenance` and `--config` are given and the installation's owner lock
shows the server is stopped. Older single-file `.sqlite3` backups still verify and restore. Backup
files are mode 0600 (they contain password hashes).

The backup timer runs `backup create --keep 7` at 02:30 daily with low CPU and I/O priority. The web
UI's *Create backup now* starts one in the background and logs `BACKUP_CREATED` or `BACKUP_FAILED`.
Off-device copies (SD card in a USB reader plus encrypted OneDrive copies through rclone) are
implemented but **not activated** until the owner confirms the card and the account/folder:
`offsite-backups.md` and ADR-0012. Until then local copies do not survive loss of the eMMC, and
Settings says so. Once activated, the daily backup, *Create backup now* and the monthly drill use the
verified card (never the eMMC) and the eMMC backup and drill timers are disabled.

### Restore drill

A valid checksum is not a proven restore. The drill restores the newest backup into a scratch
directory and checks `integrity_check`, `foreign_key_check`, the schema version and schema objects,
`user_version`, the row count of every table, the first/last-row fingerprints, and the application's
own start-up and queries against the restored copy (trends, events, alarms, the newest snapshot
label, a historian start with the capacity report). Each run writes a JSON report to
`backups/restore-drills/`; Settings shows the newest and marks the drill as due after
`restore_drill_interval_days` (30).

1. Initial drill: at installation, after the first backup, and after every upgrade.
2. Periodic drill: `aquacrop-restore-drill.timer` on the 1st of each month. It runs only when an
   approved scratch destination is mounted at `/var/lib/aquacrop-drill` (a USB disk or the
   off-device backup host; an open owner decision). It needs about 1.2 times the database size free
   there.
3. A failed drill (`systemctl --failed`, report `ok: false`) means the backups cannot be trusted:
   keep the existing backups, find the cause and run the drill again before relying on them.

## Retention and archives

The server applies the rolling window itself every hour (`historian.retention_check_interval_s`):
samples, cycles, events, finished commands, closed alarm occurrences, ended bench leases and expired
sign-in sessions older than 365 days are deleted through the single writer in transactions of at
most 5,000 rows, at most 100 transactions per run (the rest waits for the next run), so recording
continues between them. A run also waits while the WAL is above `wal_warning_bytes`. Each run that
deletes anything logs `RETENTION_PRUNED` with the counts and the cutoff; a run that stopped early
without deleting logs `RETENTION_DEFERRED`.

**What is never pruned.** Open alarms, users, the schema, migration and archive registers. Process
sessions and simulation runs are pruned only once no remaining row refers to them. So the database
levels off once the window is full but does not stop growing completely: the registers keep growing
slowly, and capacity monitoring and `storage report` stay relevant after the first year.

**Clock trust.** Deleting by date trusts the wall clock, and the device may start offline without
internet time. Retention therefore deletes only while the clock is trusted:

- at start, the clock must be no more than 5 min behind and no more than 1 h ahead of the newest
  record an earlier process stored (snapshot, event, command, process start or stop); a restart
  within the hour is trusted, a longer power cut or an RTC-less boot is not;
- while running, the clock must move with the monotonic clock within 5 min, so an NTP correction or
  a manual change of more than that either way is caught.

Anything else holds deletion and raises `RETENTION_HELD` (WARNING) with the reason. Holding only keeps
more history (about 23 MB a day); nothing is lost.

**Releasing a hold** needs an administrator; acknowledging the alarm only records the acknowledgement.

1. Check the date and time on the device against an independent source (a phone, a radio clock).
   Correct it first if it is wrong; the proposal follows the clock.
2. In Settings, the Retention card shows the reason, the proposed cutoff, the clock reading, the newest
   record of an earlier process and the rows a release would delete. Rows inside the window are never
   deleted.
3. Tick "I checked the date and time against an independent source" and press *Confirm date and
   release*. The request echoes the cutoff that was shown; if the clock moved more than 10 min since,
   it is refused with the new proposal. The release is recorded as `RETENTION_CLOCK_CONFIRMED` with
   the administrator, the cutoff, the hold and the counts.

With the server stopped, the same rule applies to the command line: `retention apply` prints the
proposal and deletes nothing unless `--confirm-cutoff` echoes it.

```text
python -m aquacrop retention apply --config C --state-dir S                # proposal only (server stopped)
python -m aquacrop retention apply --config C --state-dir S --confirm-cutoff 2025-10-12T12:00:00.000000+00:00
python -m aquacrop archive create --config C --state-dir S --start 2026-10-01T00:00:00+00:00 --end 2026-11-01T00:00:00+00:00 --destination /var/lib/aquacrop/archive
python -m aquacrop archive verify --archive /var/lib/aquacrop/archive/archive-...
```

An archive (gzip CSV of samples, including the last good value of `STALE` rows, and events, with a
checksummed manifest) is the way to keep history beyond 365 days; create it before the range leaves
the window and copy it off the device (destination TBD, owner policy). Archives and `retention apply`
need the server stopped.

A configuration with `retention_provenance: DEMO` deletes nothing and never holds: its database grows
without bound, and Settings and Overview say so. Only the capacity alarms and the snapshot floor
protect the disk.

## Crash and power-loss recovery

On start the server: applies pending migrations (after a backup), marks commands left in
`ACCEPTED`/`EXECUTING` as `OUTCOME_UNKNOWN` (`COMMANDS_RECONCILED` event), closes bench lease rows
from the previous process, restores open alarm occurrences without duplicating them, and starts
STOPPED / read-only. It never re-sends an old command.

## Upgrade and rollback

Layout: `/opt/aquacrop/releases/<version>/venv`, `/opt/aquacrop/current` symlink.

1. Build the wheelhouse for the new version (aarch64, the Pi's Python) and copy it over.
2. `backup create` and `backup verify`. Keep the manifest.
3. Create the new release venv and install from the wheelhouse.
4. Test against a restored copy: `backup restore --target /tmp/upgrade-test.sqlite3`, then run the
   new version's `serve` with a scratch state dir pointing at that copy; confirm it migrates and
   starts read-only.
5. `systemctl stop`, switch the symlink, `systemctl start`. The new version migrates (after its own
   safety copy) on start. The safety copy needs the database size plus 20 % free above the reserve;
   once the window is full that does not fit on the eMMC, so the migration refuses to start until
   space is provided (ADR-0008). This release has no migration.

Rollback: stop, switch the symlink back. If the database was migrated to a newer schema, the old
version refuses to open it (by design); restore the pre-upgrade backup to the state directory with
`backup restore --maintenance --config ...`. Never downgrade a migrated database in place. Secrets
and configuration in `/etc/aquacrop` stay untouched by upgrades.

## Clock

All stored times are UTC; the UI shows `Europe/Stockholm` with CET/CEST. Timing uses the
monotonic clock. Wall-clock steps larger than 2 s are recorded as `WALL_CLOCK_STEP`. See the CM4
guide for the RTC.

**Records stamped in the future (ADR-0011).** If the clock ran ahead and was then corrected, the
future-dated snapshot labels would hold new snapshots until real time passes them
(`HISTORY_CLOCK_BEHIND`). Settings, *Clock and timestamps*, then shows a proposal: which records
(per table, with their stamped range) and why (the forward step or process start, and the backward
correction). An administrator checks the date against an independent source, ticks the box and
quarantines them. Nothing is deleted or rewritten: the records keep their stamps and are flagged
`SUSPECT` in trends (dashed grey), exports (`timestamp_suspect`, `quarantine_id`), events, alarms
and command history. Recording resumes at the next minute in a new clock epoch (snapshot ids
`slot/60s/e<n>/...`). Retention stays held (`TIMESTAMPS_QUARANTINED`) until the separate
date-and-cutoff confirmation; quarantined records are deleted only a full window after the
quarantine itself.
