# Changelog

## Unreleased (2026-10-12): timestamp quarantine (ADR-0011) and off-device backups (ADR-0012)

### Added
- Timestamp quarantine: when records are stamped later than the clock (a clock that ran ahead and
  was corrected), Settings proposes which records and why; an administrator who checked the date
  quarantines them (`HISTORY_TIMESTAMPS_QUARANTINED`). Records, values and stamps are kept and
  flagged; recording resumes at once in a new clock epoch (`slot/60s/e<n>/<label>`); retention stays
  held until the date and cutoff are confirmed separately. No schema migration.
- Suspect stamps are visible everywhere: a dashed grey series with rings in trends,
  `timestamp_suspect`/`quarantine_id` (and `snapshot_seq`/`seq`) in the sample and audit exports,
  `SUSPECT TIME` on events, alarm occurrences and commands.
- Clock events carry a watermark (the newest id of each table) so a quarantine maps exactly to records.
- Off-device backups, **not activated**: `offsite_backup` configuration; `offsite enroll-card`,
  `check`, `backup`, `upload`, `plan`, `drill`, `status`; SD-card identity checks (mount, UUID,
  read-write, enrolment marker, never the eMMC); daily verified backups on the card; encrypted
  copies with rclone `copyto` through a crypt remote, verified with `cryptcheck`; a persistent
  upload queue with backoff; monthly restore drills from the cloud copy into SD scratch space with a
  10 GiB-plus-headroom preflight; a copy policy (proposed 7 local, 30 daily + 12 monthly in the
  cloud) that prunes only after an administrator confirms it; `OFFSITE_*` events and the
  `OFFSITE_BACKUP` alarm; a Settings card; systemd units and an fstab example;
  `docs/operations/offsite-backups.md` (authorization, credentials, recovery keys, activation).
- CI installs rclone on Linux and requires the real rclone round trip there.
- ADR-0008, ADR-0011 and ADR-0012 accepted by the owner (2026-10-12 20:04 UTC).
- Review fixes: a quarantine whose records retention deleted is retired (`HISTORY_QUARANTINE_RETIRED`)
  so reused ids are neither flagged nor deleted; alarm occurrences refreshed by the wrong clock are
  quarantined too; activation requires the cloud remote; a read-back error from the card is recorded
  as `VERIFY_FAILED`.
- Owner choices for off-device backups recorded in `docs/operations/offsite-backups.md`: 64 GB ext4
  card, the shared folder in a colleague's personal OneDrive (with setup steps for a shared folder),
  a sealed printed key copy, and the 7 / 30 + 12 copy policy. Still not activated.

### Changed
- A confirmed quarantine supersedes the clock hold it answers (`TIMESTAMPS_QUARANTINED`); a hold
  that was in force at shutdown is restored at start.

## Unreleased (2026-10-12): historian at 60 s, rolling 365 days (ADR-0008, revised after review)

### Changed
- History is a complete snapshot of every point every 60 s of wall time (`historian.recording_interval_s`,
  60 or 300; 60 stays active until the owner asks for 300), in real and simulation profiles alike;
  a simulation's run id and simulated time are stored with each snapshot. Bus polling (3 s), alarm
  evaluation, edges and the dashboard keep their timing, and polls no longer write history rows.
- The recorder schedules on the monotonic clock and labels each snapshot with its wall-clock
  boundary (hh:mm:00; hh:00, hh:05, ... for 300 s). A label is written at most once per database,
  checked inside the writing transaction, across restarts and clock corrections. A clock set back
  holds snapshots until it passes the newest label (`HISTORY_CLOCK_BEHIND`); missed labels are
  logged with their cause and never back-filled.
- Readings older than three polls are recorded as `STALE` (null value, last good value kept);
  points not yet acquired are `UNAVAILABLE`; a paused simulation's frozen state is `STALE`.
- Alarm transitions (raise, clear, ack, reset) are written immediately; DI/DO edges and commands
  already were. New `CONFIG_LOADED` / `CONFIG_CHANGED` start events with a diff.
- Retention is a rolling 365-day window applied hourly in bounded runs (5,000 rows per transaction,
  100 transactions per run, waits while the WAL is large). It deletes only while the clock is
  trusted (restart within 1 h, no step over 5 min); otherwise it holds (`RETENTION_HELD`) until an
  administrator confirms the proposed cutoff (`RETENTION_CLOCK_CONFIRMED`). Acknowledging the alarm
  no longer releases it. No size limit deletes history inside the window.
- The 2 GiB demo budget is replaced by a 10 GiB database budget and free-space thresholds
  (warning 4 GiB, reserve 2 GiB, snapshot floor 512 MiB) with defined alarms and effects.
- Backups are gzip-compressed SQL dumps of one consistent snapshot (`*.sql.gz`) with the schema
  objects and versions in the manifest. They are verified before an atomic publish, removed cleanly
  on failure (earlier backups kept), and abort below the reserve, above a 1 GiB WAL or after 3 h.
  Earlier `.sqlite3` backups still verify and restore.
- Status reads used by the acquisition loop use their own database connection, so a long export
  never delays a poll cycle.

### Added
- WAL size, checkpoint progress and a WAL warning (`wal_warning_bytes`) in the storage report,
  Overview and Settings.
- Restore drill: `backup verify --full` restores to a separate scratch directory and checks
  integrity, foreign keys, schema, counts, fingerprints and the application's own start-up and
  queries, with a JSON report; monthly `aquacrop-restore-drill.timer`; Settings shows the drill
  and the off-device copy as not configured.
- Settings: retention hold, proposed cutoff and an administrator release; snapshots not written
  during a low-disk pause; the DEMO no-deletion note.
- Optional simulation-time series (`historian.sim_time_recording_s`, off by default), labelled
  separately and counted separately.
- `aquacrop storage report` (growth, 365-day projection, thresholds, database and WAL separately,
  series and registers); `scripts/measure_storage.py` (commands, alarms, faults, environment) and
  `scripts/stress_concurrency.py` (WAL and responsiveness under long readers).

### Fixed
- Login and request rate limits use a sliding 60 s window. The fixed per-minute window allowed up
  to twice the limit across a minute boundary, and made the login test fail when it straddled one.
- The capacity check on every poll read the oldest and newest sample times with one full index
  scan (2.4 s on a one-year database), which kept the WAL from resetting and deferred retention;
  it now uses two indexed lookups. Found by the concurrency stress run.
- Text files are read and written as UTF-8 on every platform (Windows CI failure on `main`), and
  backups keep CR/LF inside text values unchanged.

### Unchanged
- Database schema (version 2) and existing history: no migration. No hardware change.

## 0.2.0.dev0 (2026-10-11)

Replaces the v0.1 prototype (kept unchanged in `legacy/`, SHA-256 `fa118eb2…6dd8`).

### Added
- Generic inventory: any number of Relay (D) and AI 8CH modules on any number of buses; stable
  point IDs (`relay_A.DO1`); `demo-48` and `demo-24` fixtures; strict profile validation.
- Causal process twin with a closed water ledger, actuator dynamics and faults, 17 scenario
  presets, pause/step/speed/replay, deterministic seeds.
- Two-sector simulation AUTO with prechecks, flow verification, latched lockout and deliberate
  recovery.
- Persistent alarm occurrences (rule + target identity, ack ≠ clear ≠ reset, latch, hysteresis,
  debounce) restored after restart.
- SQLite historian: full provenance on every sample, every point every cycle, batched writer,
  migrations (backup first), backups with manifests, archive and retention, storage alarms.
- Authenticated API and seven-view UI: Argon2id accounts, roles, commissioning flag, server-side
  sessions, CSRF and origin checks, rate limits, strict CSP, stale-data handling.
- Read-only Modbus RTU on a pinned pymodbus 3.11.3 adapter with strict reply validation,
  per-device health and backoff, physical edge events, and `diagnostics read`.
- Bench write lease (commissioning only, isolated bench): exact allowlist, typed confirmation,
  mode preflight, FC05 + FC01 readback, never restored after restart.
- v0.1 database import (`migrate-v01`) and `python -m greenhouse` compatibility launcher.
- Hash-locked requirements, wheelhouse script, installed-wheel check, systemd units, CI workflow.
- Documentation: model, operator, installation (desktop, CM4), security, operations, vendor
  protocol record, ADRs, acceptance matrix, milestone 5 procedures and unsigned templates.

### Not in this release
Physical AUTO / live control, HYBRID_SHADOW, FC0F, production topology, calibration and recipes.
