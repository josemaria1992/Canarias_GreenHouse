# Changelog

## Unreleased (2026-10-12): historian at 60 s, rolling 365 days (ADR-0008)

### Changed
- History is a complete snapshot of every point every 60 s (`historian.recording_interval_s`, 60 or
  300; 60 stays active until the owner asks for 300). Snapshots are taken on wall-clock boundaries
  by their own recorder; bus polling (3 s), alarm evaluation, edges and the dashboard keep their
  timing, and polls no longer write history rows. In simulation the interval is simulated time.
- Readings older than three polls are recorded as `STALE` (null value, last good value kept);
  points not yet acquired are `UNAVAILABLE`. Missed boundaries are logged, never back-filled.
- Alarm transitions (raise, clear, ack, reset) are written immediately; DI/DO edges and commands
  already were. New `CONFIG_LOADED` / `CONFIG_CHANGED` start events with a diff.
- Retention is a rolling 365-day window applied hourly in small batches, with a clock guard
  (`RETENTION_HELD`). No size limit deletes history inside the window.
- The 2 GiB demo budget is replaced by a 10 GiB database budget and free-space thresholds
  (warning 4 GiB, reserve 2 GiB, snapshot floor 512 MiB) with defined alarms and effects.
- Backups are gzip-compressed SQL dumps of one consistent snapshot (`*.sql.gz`, about 17 times smaller
  than the database), verified before rotation (`--keep 7`) and refused near the reserve; `verify
  --full` test-restores. Earlier `.sqlite3` backups still verify and restore.

### Added
- `aquacrop storage report` (growth, 365-day projection, thresholds) and the same figures in
  Overview and Settings; `scripts/measure_storage.py`.

### Fixed
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
