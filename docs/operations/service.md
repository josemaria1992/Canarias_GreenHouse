# Operations: service, health, storage, backups, upgrade and rollback

Application health is not motor safety. Nothing here stops equipment.

## systemd service (Raspberry Pi OS)

Files in `deploy/`: `aquacrop-greenhouse.service`, `aquacrop.env.example`,
`aquacrop-backup.service` + `.timer`, `journald-aquacrop.conf`. `systemd-analyze verify` parses
all three units (2026-10-11); they have not run on a CM4 yet.

```text
sudo install -m 0640 -g aquacrop deploy/aquacrop.env.example /etc/aquacrop/aquacrop.env
sudo install -m 0644 deploy/aquacrop-greenhouse.service deploy/aquacrop-backup.service deploy/aquacrop-backup.timer /etc/systemd/system/
sudo install -D -m 0644 deploy/journald-aquacrop.conf /etc/systemd/journald.conf.d/aquacrop.conf
sudo systemctl restart systemd-journald
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop users bootstrap \
     --config /etc/aquacrop/hardware-read-only.yaml --state-dir /var/lib/aquacrop --username admin --generate
sudo systemctl daemon-reload
sudo systemctl enable --now aquacrop-greenhouse.service aquacrop-backup.timer
```

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
  `HISTORY_SNAPSHOTS_MISSED`, `BACKUP_FAILED`; alarms `DEVICE_COMM`, `STORAGE_CAPACITY`,
  `STORAGE_PRESSURE`, `HISTORY_SNAPSHOTS_PAUSED`, `STORAGE_WRITE_FAILED`, `RETENTION_HELD`.

## Historian recording and storage (ADR-0008)

Owner requirements of 2026-10-12: a complete snapshot of every point every **60 s**, a rolling
**365-day** window, nothing deleted inside the window to make room. Bus polling (3 s), alarms, edges
and the dashboard keep their own timing; edges, alarm transitions, commands and configuration
changes are written when they happen.

`historian.recording_interval_s` accepts 60 or 300. Keep 60 until the owner explicitly asks for
300 s; the change is a configuration edit plus a restart, and the restart records `CONFIG_CHANGED`
with the old and new value.

### Measured growth (desktop, `scripts/measure_storage.py`, report in `measurements/`)

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

48 points, every 60 s, 400 simulated days through the real write path. Timings are from an x86
desktop; the CM4 and its eMMC will be slower and are measured in CP-00.

Check the real growth on the installation after a few weeks, and again before the window fills:

```text
python -m aquacrop storage report --config /etc/aquacrop/hardware-read-only.yaml --state-dir /var/lib/aquacrop
```

It is read-only and safe while the server runs. It prints the database size and used bytes, free
disk and the thresholds, the oldest and newest record, the 365-day projection and the growth of the
last seven days. Overview and Settings in the web UI show the same figures.

### Budget on the 32 GB eMMC

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

## Low and full disk

Nothing inside the 365-day window is deleted for any of these. None of them stops equipment.

| Condition | Alarm | What happens |
|---|---|---|
| Database over `db_budget_bytes` (10 GiB), or the 365-day projection over it, or free disk under 4 GiB | `STORAGE_CAPACITY` WARNING | Visible only. Plan space: move backups or archives off the device. |
| Free disk under 2 GiB (`free_space_reserve_bytes`) | `STORAGE_PRESSURE` CRITICAL | New starts and ON commands refused (`can_accept_starts`). The daily backup refuses to start. |
| Free disk under 512 MiB (`snapshot_floor_bytes`) | `HISTORY_SNAPSHOTS_PAUSED` CRITICAL | Periodic snapshots pause; each skipped one is counted and `HISTORY_SNAPSHOTS_PAUSED` is logged once. Events, commands and alarms are still written. Snapshots resume by themselves once free space is back above 2 GiB (`HISTORY_SNAPSHOTS_RESUMED` with the count). |
| A commit fails (disk full, I/O error) | `STORAGE_WRITE_FAILED` CRITICAL | The batch rolls back, health goes degraded, starts refused. History that could not be queued is counted (`dropped_cycles`). |

To recover space: copy old backups or archives off the device and delete them locally, or remove
other files. Do not delete database rows by hand.

## Backups and restore

```text
python -m aquacrop backup create --config C --state-dir S --destination /var/lib/aquacrop/backups --keep 7
python -m aquacrop backup verify --backup /var/lib/aquacrop/backups            # newest, or a file
python -m aquacrop backup verify --backup FILE --full --scratch-dir /tmp      # also a test restore
python -m aquacrop backup restore --backup FILE --target /tmp/restore-test.sqlite3   # new instance first
```

A backup is a gzip-compressed SQL dump of one consistent read snapshot (safe while the server runs)
plus a manifest with SHA-256, per-table row counts, schema and software version and the history
range. It is streamed, so no full-size temporary copy is written. `create` verifies the new file
(checksum, whole gzip stream, every statement, row counts) before `--keep` removes the oldest
compressed backups, and refuses to start if the estimated size would take free disk below the
reserve. `verify --full` additionally restores into a scratch database and runs `integrity_check`
(needs room for one database). Restore rebuilds a database from the dump and checks integrity and
row counts before it is moved into place; it refuses an existing target unless `--maintenance` and
`--config` are given and the installation's owner lock shows the server is stopped. Older
single-file `.sqlite3` backups still verify and restore. Backup files are mode 0600 (they contain
password hashes).

The timer runs `backup create --keep 7` at 02:30 daily with low CPU and I/O priority; `create`
reports a refusal or failure as a failed unit (`systemctl --failed`). The web UI's *Create backup
now* starts one in the background and logs `BACKUP_CREATED` or `BACKUP_FAILED`. The off-device
destination is **TBD (owner policy)**: local copies do not survive loss of the eMMC.

## Retention and archives

The server applies the rolling window itself every hour (`historian.retention_check_interval_s`):
samples, cycles, events, finished commands, closed alarm occurrences, ended bench leases and expired
sign-in sessions older than 365 days are deleted in batches of 5,000 through the single writer, so
recording continues between batches. Each run that deletes anything logs `RETENTION_PRUNED` with the
counts and the cutoff. Open alarms, users, process sessions and the schema/migration/archive
registers are kept. Freed pages are reused, so the file stops growing after the first year.

Retention trusts the system clock only after a check. If at start the clock is more than 30 days
past (or behind) the newest record an earlier run stored (snapshot, event, command or process start
and stop), or it steps forward by more than an hour while running,
retention holds and raises `RETENTION_HELD` (WARNING). Check the date and time, then acknowledge the
alarm; that releases it (`RETENTION_RELEASED`).

```text
python -m aquacrop retention apply --config C --state-dir S --dry-run      # server stopped
python -m aquacrop archive create --config C --state-dir S --start 2026-10-01T00:00:00+00:00 --end 2026-11-01T00:00:00+00:00 --destination /var/lib/aquacrop/archive
python -m aquacrop archive verify --archive /var/lib/aquacrop/archive/archive-...
```

An archive (gzip CSV of samples, including the last good value of `STALE` rows, and events, with a
checksummed manifest) is the way to keep history
beyond 365 days; create it before the range leaves the window and copy it off the device (destination
TBD, owner policy). Archives
and `retention apply` need the server stopped. A configuration with `retention_provenance: DEMO`
deletes nothing.

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
   backup) on start.

Rollback: stop, switch the symlink back. If the database was migrated to a newer schema, the old
version refuses to open it (by design); restore the pre-upgrade backup to the state directory with
`backup restore --maintenance --config ...`. Never downgrade a migrated database in place. Secrets
and configuration in `/etc/aquacrop` stay untouched by upgrades.

## Clock

All stored times are UTC; the UI shows `Europe/Stockholm` with CET/CEST. Timing uses the
monotonic clock. Wall-clock steps larger than 2 s are recorded as `WALL_CLOCK_STEP`. See the CM4
guide for the RTC.
