# Acceptance matrix

**Environment:** Linux x86_64 container, Python 3.13.16 (also 3.11.15 and 3.12.13), pymodbus
3.11.3, FastAPI 0.143, SQLite WAL. **Run:** 2026-10-11, software 0.2.0.dev0. The commit is
recorded in `CHANGELOG.md` for this release.
**Results:** `python -m pytest`: 183 passed (3.13); full suite also green on 3.11 and 3.12;
`pip check` clean on each. `demo verify --seed 42`: 21/21 PASS for `demo-48` and `demo-24`.
Installed-wheel check (`scripts/check_installed_wheel.py`): 13/13 PASS.

The acceptance IDs are this project's own (`docs/IMPLEMENTATION_PLAN.md`); the owner's
`ACCEPTANCE_TEST_PLAN.md` was never supplied, so no cross-mapping exists.

**Modbus results come from the real pymodbus client and RTU framer running over an in-memory
serial port. They are software evidence, not hardware evidence.** No hardware was connected and no
physical test was performed.

Status key: PASS · FAIL · BLOCKED (cannot run until something outside the software exists) ·
NOT RUN (could run, has not).

## Milestone 1: desktop platform

| ID | Status | Evidence |
|---|---|---|
| AT-INV-01 48/24 fixtures, unique point IDs | PASS | `unit/test_inventory.py::test_demo48_has_48_unique_points`, `test_demo24_has_24_points`, `test_point_id_contract` |
| AT-INV-02 arbitrary module mix, no code change | PASS | `test_arbitrary_module_mix_without_code_change` |
| AT-INV-03 (bus,address) uniqueness, 1–247 | PASS | `test_duplicate_address_same_bus_rejected`, `test_same_address_different_bus_ok`, `test_reserved_addresses_rejected` |
| AT-INV-04 profile rules, demo values refused in real profiles | PASS | `test_reserved_or_ephemeral_profiles_refused`, `test_simulated_profile_refuses_rtu_bus`, `test_demo_values_never_load_into_real_profile`, `test_physical_mapping_must_stay_empty` |
| AT-IO-01 every point injectable/commandable | PASS | `unit/test_runtime.py::test_io_test_every_point_individually` |
| AT-IO-02 one module failure isolated | PASS | `test_single_module_failure_isolated`; real-bus: `hardware/test_modbus.py::test_at_mb_07_*` |
| AT-IO-03 simulation never imports serial | PASS | `test_simulation_never_imports_serial`, `demo verify` isolation check |
| AT-HIS-01 every cycle records every point | PASS | `integration/test_historian.py::test_every_cycle_records_all_points_including_unchanged` |
| AT-AUD-01 intent before side effect, outcome after, rejections audited | PASS | `test_audit_intent_before_side_effect_and_on_refused_without_audit`, `test_commands_audited_with_outcomes`, `test_outcome_persist_failure_reports_unknown` |
| AT-SEC-01 login, roles, CSRF/origin, no default credentials | PASS | `integration/test_api.py::test_every_mutation_requires_session_origin_and_csrf`, `test_roles_enforced_server_side`, `test_login_failures_and_rate_limit` |
| AT-UI-01 seven views from packaged assets | PASS | `test_static_ui_is_packaged_and_offline`; headless Chromium run of all seven views (simulation) and the hardware views |

## Milestone 2: twin and control

| ID | Status | Evidence |
|---|---|---|
| AT-TWN-01 water ledger invariant | PASS | `unit/test_plant.py::test_ledger_holds_under_random_commands_long_run`, spill/starvation tests; max residual in `demo verify` ≤ 7.8e-11 L |
| AT-TWN-02 MANUAL and AUTO drive the same plant | PASS | `test_manual_drives_same_plant` |
| AT-TWN-03 determinism, speed invariance | PASS | `test_determinism_and_replay`, `test_speed_does_not_change_results` |
| AT-TWN-04 pause/step/reset/replay | PASS | `test_pause_step_semantics`, `test_determinism_and_replay`, `demo verify` replay check |
| AT-CTL-01 two-sector AUTO sequence | PASS | `test_scenario_presets[normal_cycle]` |
| AT-CTL-02 faults latch lockout; recovery sequence | PASS | scenario presets `pump_no_flow`, `reservoir_low`, `overflow_fill_stuck_open`, `valve_stuck_closed`, `di_failed_low`; `test_recovery_requires_ack_before_reset`, `test_latched_alarm_needs_reset` |
| AT-CTL-03 conflicting MANUAL during AUTO rejected and audited | PASS | `test_conflicting_manual_rejected_and_audited` |
| AT-ALM-01 alarm identity, ack ≠ clear ≠ reset, restart without duplicates | PASS | `test_alarm_value_change_updates_same_occurrence`, `test_alarm_ack_is_not_clear_and_hysteresis`, `test_alarm_persist_and_restore_without_duplicates` |
| AT-SCN-01 every mandatory scenario | PASS (see note) | 17 presets in `test_scenario_presets` and `demo verify`. Dashboard network disconnect: checked in headless Chromium (server killed → "CONNECTION LOST … STALE", commands disabled, no green status). Process restart: alarm restore and command reconciliation tests |
| AT-DEMO-01 headless `demo verify` | PASS | 21/21 on both fixtures; nonzero exit on any failure |

## Milestone 3: Modbus (software doubles only)

| ID | Status | Evidence |
|---|---|---|
| AT-MB-01 request vectors byte-exact; response decoding | PASS | `hardware/test_modbus.py::test_at_mb_01_*` |
| AT-MB-02 FC01/02/03/04/05 through real client | PASS | `test_at_mb_02_*`, `test_at_mb_06_armed_write_confirmed_by_readback` |
| AT-MB-02 optional FC0F | NOT RUN | not implemented (ADR-0005) |
| AT-MB-03 short/malformed/CRC/exception/wrong-unit/wrong-FC | PASS | `test_at_mb_03_*` (incl. cases pymodbus itself accepts) |
| AT-MB-04 eight NORMAL modes or no write | PASS | `test_at_mb_04_*` |
| AT-MB-05 zero write frames: start, poll, reconnect, shutdown, read-only | PASS | `test_at_mb_05_*`, `test_diagnostics_read_sends_no_writes`, `hardware/test_api_hardware.py` |
| AT-MB-05 shadow profile | BLOCKED | shadow not served until a physical mapping is approved (ADR-0002) |
| AT-MB-06 lease expiry/revocation/allowlist/config/session; uncertain write | PASS | `test_at_mb_06_*` (19 cases), restart never restores a lease, STOP priority |
| AT-MB-07 per-device health, backoff | PASS | `test_at_mb_07_*` |
| Real hardware for any AT-MB item | BLOCKED | no hardware; see milestone 5 |

## Milestone 4: packaging, security, operations

| ID | Status | Evidence |
|---|---|---|
| AT-PKG-01 wheel outside the tree serves UI offline | PASS (Linux x86_64, py3.13) | `scripts/check_installed_wheel.py` 13/13 |
| AT-PKG-01 Windows / macOS / ARM64 | NOT RUN | CI workflow written; no repository to run it (ADR-0007) |
| AT-PKG-02 hash-locked requirements, `pip check` | PASS | `requirements.lock`, `requirements-dev.lock`; installs and `pip check` on 3.11/3.12/3.13; ARM64 py3.11 wheels all available |
| AT-SEC-02 expiry, revocation, rate limits, CSV escaping, XSS-safe | PASS | `test_idle_expiry`, `test_password_change_revokes_sessions_and_logout`, `test_csv_export_escapes_and_discloses_completeness`, CSP header + `textContent`-only UI |
| AT-OPS-01 backup API, verify, restore to new instance | PASS | `test_backup_verify_restore_to_new_instance` |
| AT-OPS-02 migrations, v0.1 backup-first | PASS | `test_migration_backup_first_and_no_downgrade`, `test_v1_database_migrates_to_v2_backup_first`, `integration/test_migrate_v01.py` |
| AT-OPS-03 budget / full disk ⇒ degraded, starts inhibited | PASS | `test_storage_failure_inhibits_starts`, `test_storage_pressure_alarm_and_start_inhibit`, `test_bench_on_refused_when_storage_degraded`, retention tests |
| AT-OPS-04 acquisition supervision | PASS | `integration/test_supervision.py` |
| AT-OPS-05 systemd unit, CM4 guide, rollback doc | PASS (documents) / NOT RUN (on a CM4) | `deploy/`, `systemd-analyze verify` parses; `docs/installation/cm4.md`, `docs/operations/service.md` |
| Storage sizing measured | PASS (desktop) | ≈ 16.2 KB per 48-point cycle; demo budget conflict in ADR-0008 |

## Milestone 5: hardware arrival

| ID | Status | Evidence |
|---|---|---|
| CP-00 … CP-05 procedures and unsigned templates | PASS (documents) | `docs/commissioning/` |
| CP-00 … CP-05 execution | BLOCKED | needs hardware on an isolated bench and the owner's written authorization of the setup |

## Production limitation matrix

| Area | State in this release |
|---|---|
| Physical AUTO / live control | Does not exist; no flag enables it |
| Hydraulic topology, wiring, VFD, probes, recipes, safe-state matrix | TBD — requires operator confirmation |
| Calibration | None verified; real profiles refuse non-VERIFIED scaling |
| AI channel modes on real modules | UNKNOWN until CP-01/CP-02; values stay UNSCALED |
| Shadow / what-if | Not served (ADR-0002) |
| Alarm severities for physical equipment | TBD; only DEMO rules shipped |
| Retention and off-device backups | DEMO policy deletes nothing; destination TBD; budget conflict open (ADR-0008) |
| Role assignments and remote physical rights | Proposal only; owner approval required |
| CM4 performance, UART mapping, RTC | Not measured / not verified |
| Safety functions (E-stop, motor protection, dry-run, overfill) | Outside software; never replaced by it |
