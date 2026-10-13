# Changelog

## 0.3.0 (2026-10-13): pre-hardware release

Stage 4 of the pre-hardware release, and the release itself: it contains the three 0.3.0 sections
below as well. Software and simulation only: nothing has run on the CM4, the modules, the card or the
OneDrive account, and no setting enables physical AUTO. What is still needed before each physical
step: `docs/RELEASE_READINESS.md`.

### Added
- Release ZIP (`scripts/build_release.py`): the wheel, `AquaCrop-Demo.cmd` and `aquacrop-demo.sh`,
  `README-FIRST.md`, the CM4 installer, configurations, guides, `requirements.lock`, offline
  wheelhouses for Windows (Python 3.12) and the CM4 (aarch64, Python 3.11), `RELEASE.json`
  (version, commit, `physical_verification: false`), `MANIFEST.sha256` of every file, the ZIP's own
  SHA-256, and `acceptance-manifest.json` (the owner's 45 gates with status and evidence, generated
  from `docs/ACCEPTANCE.md`). Private files (keys, databases, `rclone.conf`, `.env`) are refused.
- Desktop launcher `python -m aquacrop launch`: prerequisite checks, the full installation demo
  (`installation-48`, simulated) with its state under `%LOCALAPPDATA%\AquaCrop` (or the platform
  equivalent), a guided first administrator (no default password), a log file, the browser opened
  after the health check, and a clean stop. `AquaCrop-Demo.cmd` prepares a private environment from
  the bundled wheelhouse, offline.
- Guided CM4 installer `deploy/aquacrop-cm4.py` (standard library only): `check`, `install`,
  `upgrade` (verified backup with the running version first), `rollback`, `tls`, `status`;
  `--dry-run` and a staging `--root`. It verifies the release manifest, installs offline with hashes,
  switches releases atomically and never formats a disk, edits the boot configuration or creates an
  account with a default password.
- Remote access through the owner's OpenVPN: `docs/security/remote-access.md` (routes, return path,
  name, profiles), a Caddy TLS proxy on 443 with the application on 127.0.0.1, a firewall example,
  and the route test `python -m aquacrop netcheck --url https://... --ca-file ...`.
- Quarantine review before pruning: an administrator keeps quarantined records or releases them to
  retention, with a reason (`HISTORY_QUARANTINE_REVIEWED`, Settings, `POST
  /api/history/quarantine/review`). Unreviewed quarantines are never pruned; once older than the
  window they raise `HISTORY_QUARANTINE_REVIEW_DUE`.
- Cloud completion receipt (`aquacrop-<time>.receipt.json`), uploaded last after `cryptcheck` and
  checked the same way; pruning removes it first and the drill checks it. A replacement controller
  with no ledger picks the newest copy that has one.
- Replacement-controller recovery procedure (`offsite-backups.md` section 8.1), rehearsed by a test
  with the real rclone crypt remote, test-only keys and a local folder in place of OneDrive.
- Trends over a whole year: ranges up to 365 days; a point with more samples than fit is answered
  per bucket of 10 min, 1 h or 1 day with mean, min, max and sample counts, drawn as a solid mean
  with dashed min and max and labelled as aggregated. The CSV export (paged, complete) keeps every
  sample. The server keeps the buckets that can no longer change, so a refresh reads only the first
  and the newest ones, and the page fetches ranges over a day once a minute.
- `scripts/measure_process_workload.py` and `measure_storage.py --process-workload`: the storage
  measurement with the event and command rates of all processes running in AUTO;
  `scripts/measure_trend_cache.py` times a year chart with and without the kept buckets.
- `docs/RELEASE_READINESS.md`; acceptance rows for OP-01 to OP-06, BK-01 to BK-07, DL-01 to DL-05
  and UI-06; tests for 144 observations from three snapshots, kept configuration records, the year
  chart and paged export, no bus frame from backup, drill and restore, and the gate manifest.
- CI jobs `launcher` (Windows, macOS, Ubuntu), `release` (builds the ZIP), `release-windows`
  (offline first run from the ZIP) and `release-cm4-staging` (installer from the ZIP on ARM64).

### Changed
- Retention keeps the newest `CONFIG_LOADED` event of each distinct configuration, which defines
  the configuration and scaling that retained samples refer to, also past the window.
- `docs/installation/cm4.md` and `docs/operations/service.md` use the guided installer for install,
  upgrade and rollback; `service.md` now says this release migrates schema 2 to 3.
- The Tailscale section of `docs/security/security.md` is replaced by the OpenVPN route.
- Version 0.3.0.

## 0.3.0 (2026-10-12): process pages and installation diagram

Pre-hardware release, stage 3. Process control is simulation only; on a hardware profile the diagram
and the process pages are read-only observation and every signal stays UNBOUND.

### Added
- A clickable installation diagram on the Overview: mains, stock tanks A, B and acid with their fill
  and outlet valves and transfer pumps, the main tank with the proposed pH/EC probe point, both main
  pumps with K1/K2 on the shared drive, sectors 1 and 2, the sump and the return pumps. Every value is
  what the controllers read (value, unit, quality, age); requested state (outline) and observed
  feedback (fill) are drawn apart, and missing feedback is shown as such. Parts open their page by
  mouse or keyboard.
- Process pages: Nutrients & acid (tabs for stock A, stock B and acid, with the active recipe's pH
  target, EC target, A:B ratio or fixed volumes and dose bounds), Main tank, Main pumps, Sector 1,
  Sector 2, Drainage and Recipes. Each page has the diagram with its own parts highlighted, its
  measured and requested values, its alarms with Ack and what to do next, and a chart of the last
  hour of its signals. Each process panel shows state, phase, waiting reason, next step, owned
  equipment, outputs, last result, permissives and history, with AUTO, MANUAL, STOP and lockout
  reset, bounded manual forms and stock acknowledgements. The recipe page lists versions with
  validate, activate and roll back, and edits a new version from the server's form schema (choices
  for enumerated fields, the irrigation sequence editor, validation issues).
- On a hardware profile the Overview diagram and the process pages render read-only: parts only
  navigate, controls are replaced by the reason they are not offered, unmapped values read "not
  measured", and observations are labelled REAL or SIM (ADR-0009 addendum).
- `GET /api/plant`: the installation as the controllers see it (every logical signal with its
  binding, sample and age, the owner layout, the process summary, open alarms). Served on hardware
  profiles too, read-only and all UNBOUND (ADR-0009 addendum).
- `scripts/ui_check/`: browser checks in headless Chromium (every page, the diagram, the owner
  walkthrough with a restart, viewer refusals, offline and reconnect, phone, tablet and PC
  viewports, and the read-only pages on the hardware profile over the in-memory fake bus with zero
  write frames), run by the new CI job `ui`, which keeps screenshots as an artifact.
- Tests for the plant view on both profiles, the recipe form choices and the role table across
  every process, stock and recipe mutation including forged requests.

### Changed
- Stopping a process clears its planned next step and inhibit, so a lockout no longer shows a stale
  "next: stop S1 in ... s".
- The interim installation Overview from stage 2 is replaced by the diagram page; the Control view
  is hidden for the installation topology (its processes have their own pages).
- `/api/plant` returns full alarm occurrences (latch and reset state included) so pages can say what
  to do next.
- The page refresh no longer undoes a choice made while it was fetching (for example a fault picked on
  the twin page just before Apply), and a page change or an action that arrives during a refresh is
  drawn right after it instead of at the next tick.
- On a hardware profile a `NOT_CONFIGURED` drive adapter is shown UNBOUND like every other signal.

## 0.3.0 (2026-10-12): installation model and process controllers (ADR-0013, ADR-0014)

Pre-hardware release, stages 1 and 2. Simulation only: no physical AUTO exists and no flag enables it.

### Added
- `installation` twin topology and the `installation-48` configuration: the owner's installation
  (20,000 L main tank, stock tanks A, B and acid of 5,000 L, a sump of about 5,000 L, two main pumps
  on one shared drive selected by K1/K2, two return pumps, three transfer pumps, nine valve duties,
  13 channels with retained water and 52 hand-adjusted outlets). Facts are OWNER_CONFIRMED; every
  dynamic, switch level and span is DEMO.
- A volume ledger over every tank, channel and pipe (additions, consumption, spill, unmet demand)
  checked every scan, and a labelled synthetic pH/EC fixture (`synthetic-carbonate-ec-v1`) for
  qualitative controller tests. It is not a calibrated chemical model.
- A proposed 48-point allocation (`docs/model/signal_budget.md`): the drive run command is bound to
  a drive adapter (SIMULATED in the twin, NOT_CONFIGURED on hardware) because 16 DO are one short;
  missing feedbacks are handled explicitly and listed with owner options.
- Seven process controllers (irrigation, return, refill, acid, three stock fills) with their own
  modes, phases, permissives and fault latches; a supervisor and an equipment arbiter; separate
  acknowledge, reset and restart; bounded MANUAL operations; raw DO commands refused on the twin.
- Irrigation alternates P1/S1 and P2/S2 with the owner's changeover sequence and holds the valve and
  selection when the drive does not confirm stop. Refill latches one batch per low level and runs
  water and nutrients concurrently (fixed volumes with EC validation, or EC target keeping the A:B
  ratio). Acid lowers pH only, in bounded steps with settling and response checks. Return follows
  sump thresholds and the main-tank inhibit, with an alarm when the sump is high and the main tank
  full.
- Versioned recipes (draft, validate, activate, roll back) with units in field names; a labelled
  DEMO preset for simulation only (deliberately not the owner's illustrative example). Operator
  stock-preparation acknowledgements, invalidated by a fill or an uncovered level rise.
- Historian schema v3 (`recipes`, `recipe_activations`, `stock_readiness`), migrated after a verified
  backup. API: `/api/processes`, `/api/stocks`, `/api/recipes`; permissions `PROCESS_CONTROL`,
  `RECIPE_EDIT` (operator) and `RECIPE_ACTIVATE` (administrator, a changeable default).
- 40 installation scenarios; `demo verify --config installation-48` (42 checks); PH and PC
  acceptance tests; CI and the installed-wheel check run the installation verification.
- Docs: ADR-0013, ADR-0014, `model/installation_model.md`, `model/signal_budget.md`,
  `model/cause_and_effect.md`.

### Changed
- The simulation runtime acquires once per control period for the installation topology (the demo
  topology still scans every tick); configuration lookups are cached.

## 0.3.0 (2026-10-12): timestamp quarantine (ADR-0011) and off-device backups (ADR-0012)

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

## 0.3.0 (2026-10-12): historian at 60 s, rolling 365 days (ADR-0008, revised after review)

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
