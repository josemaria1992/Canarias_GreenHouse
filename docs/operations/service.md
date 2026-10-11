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
  heartbeat, historian queue depth, DB size, free disk, budget/reserve, newest data age.
- Events worth alerting on: `POLL_OVERRUN`, `WALL_CLOCK_STEP`, `ACQUISITION_TASK_FAILED`,
  alarms `DEVICE_COMM`, `STORAGE_PRESSURE`, `STORAGE_WRITE_FAILED`.

## Storage sizing (measured)

Measured 2026-10-11 on Linux x86_64 with the current schema (v2): **about 16.2 KB per 48-point
cycle** including the two sample indexes (samples table 67 %, point/time index 18 %, time index
13 %).

| Cadence | Cycles/day | Point observations/day | DB growth/day |
|---|---:|---:|---:|
| 48 points every 3 s | 28,800 | 1,382,400 | ≈ 445 MiB |
| 48 points every 10 s | 8,640 | 414,720 | ≈ 134 MiB |
| 24 points every 3 s | 28,800 | 691,200 | ≈ 225 MiB (estimate: half the rows) |

**Conflict to resolve (owner decision):** the brief's DEMO proposal of 7 days at full resolution
needs ≈ 3.0 GiB at 48 points / 3 s, but its DEMO database budget is 2 GiB, which lasts ≈ 4.6 days.
Options: raise the budget (the 32 GB eMMC has room), shorten full-resolution retention, poll more
slowly, or invest in a more compact schema (moving per-cycle text columns to the cycle row is
estimated to save roughly half; not done). Until decided, the `STORAGE_PRESSURE` warning appears
when the budget is exceeded and nothing is deleted.

eMMC headroom to plan for (32 GB nominal, ≈ 29 GiB usable): OS and packages ≈ 3–4 GiB, journald cap
200 MiB, database budget, WAL up to 64 MiB after checkpoint, local backups (each a full copy of the
database), archive exports (gzip CSV, smaller), plus room for one upgrade's release directory. 12-bit
ADC resolution says nothing about accuracy or eMMC endurance; endurance is not measured.

## Disk pressure and failures

- Free disk below `free_space_reserve_bytes` (DEMO 1 GiB): `STORAGE_PRESSURE` CRITICAL, new starts
  and ON commands refused (`can_accept_starts`), visible in Overview.
- Database over `db_budget_bytes` (DEMO 2 GiB): `STORAGE_PRESSURE` WARNING.
- A failed commit (disk full, I/O error): the whole batch rolls back, health goes degraded,
  `STORAGE_WRITE_FAILED` CRITICAL, starts inhibited. History that could not be queued is counted
  (`dropped_cycles`), never silently skipped. This does not stop real equipment.

## Backups and restore

```text
python -m aquacrop backup create --config C --state-dir S --destination /var/lib/aquacrop/backups
python -m aquacrop backup verify --backup /var/lib/aquacrop/backups          # newest, or a file
python -m aquacrop backup restore --backup FILE --target /tmp/restore-test.sqlite3   # new instance first
```

Backups use the SQLite online backup API (consistent with WAL, safe while the server runs) and
write a manifest with SHA-256, schema and software version. Verify re-checks the checksum and runs
`integrity_check` on the copy read-only. Restore refuses an existing target unless `--maintenance`
and `--config` are given and the installation's owner lock shows the server is stopped. The timer
runs a backup at 02:30 daily. Off-device backup destination and backup retention: **TBD (owner
policy)**; until then old backups must be removed by hand.

## Archive and retention

```text
python -m aquacrop archive create --config C --state-dir S --start 2026-10-01T00:00:00+00:00 --end 2026-10-02T00:00:00+00:00 --destination /var/lib/aquacrop/archive
python -m aquacrop archive verify --archive /var/lib/aquacrop/archive/archive-...
python -m aquacrop retention apply --config C --state-dir S --dry-run
```

Both archive and retention need the server stopped. An archive holds gzip CSV of samples and
events plus a manifest (checksums, row counts, configuration and calibration references) and is
registered in the database. `retention apply` deletes sample rows only if the policy is
`APPROVED`, the rows are older than `retention_days`, and they lie inside a registered archive that
still verifies. Events, commands, alarms and leases are never pruned. The shipped policy is `DEMO`,
so it deletes nothing.

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
