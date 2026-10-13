# Acceptance matrix

**Environment:** Linux x86_64 container, Python 3.13.16 (also 3.11.15 and 3.12.13), pymodbus
3.11.3, FastAPI 0.143, SQLite WAL, rclone 1.60.1. **Run:** 2026-10-13, software 0.3.0 (the
pre-hardware release). Earlier runs: 2026-10-11 at `0ce4842` (183 tests), ADR-0008 at `f0b18e2` (252
tests), quarantine and off-device backups (271 tests), pre-hardware stages 1 to 3 (352 tests), stage 4
(375 tests).
**Results:** `python -m pytest`: 410 passed on 3.11, 3.12 and 3.13 locally; `pip check` clean.
`demo verify --seed 42`: 21/21 PASS for `demo-48` and `demo-24`, 45/45 for `installation-48`.
Installed-wheel check (`scripts/check_installed_wheel.py`) on the wheel from the built release ZIP:
16/16 PASS. Browser check (`scripts/ui_check/`, headless Chromium): 58/58 PASS. Storage measurements and
concurrency stress: `operations/measurements/2026-10-12-measurements.md` and
`operations/measurements/2026-10-13-measurements.md`. GitHub CI repeats the suite on Ubuntu,
Windows and macOS (3.11 to 3.13) and Ubuntu ARM64 for every pull request.

The AT-... IDs of milestones 1 to 5 are this project's own (`docs/IMPLEMENTATION_PLAN.md`); the
original brief's `ACCEPTANCE_TEST_PLAN.md` was never supplied, so no cross-mapping exists. The PH,
PC, UI, OP, BK and DL gates are the owner's `PRE_HARDWARE_ACCEPTANCE_PLAN.md` (12 Oct 2026); the
release ZIP carries them as `acceptance-manifest.json`, generated from this file; there a gate's
status is PASS only when every part of it passed (38 of 45 in this release), otherwise the first part
that did not, and each part is listed with its scope.

**Modbus results come from the real pymodbus client and RTU framer running over an in-memory
serial port. They are software evidence, not hardware evidence.** No hardware was connected and no
physical test was performed.

Status key: PASS · FAIL · BLOCKED (cannot run until something outside the software exists) ·
NOT RUN (could run, has not) · NOT CONFIGURED (needs the owner's account or settings). "PASS
(desktop)", "PASS (fakes)" or "PASS (fake bus)" name what stood in for the hardware; none of them is
physical verification.

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
| AT-HIS-01 every snapshot records every point, unchanged values included | PASS | `integration/test_historian.py::test_every_cycle_records_all_points_including_unchanged`, `test_hw_acquisition_keeps_its_rate_and_snapshots_are_separate_and_complete` |
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
| AT-MB-08 drive Modbus interface (ADR-0015): FC06 vector and echo, refusal of any unconfigured unit or register before sending, status decode, run sequence and failure midway, no write on polling, preflight refusals, lease run/stop, diagnostics read-only | PASS (fake bus) | `hardware/test_vfd_modbus.py` (8 tests, a register-level drive stand-in), `test_at_mb_03_*` (no register write on a bus without a drive), `unit/test_drive_config.py` |
| Real hardware for any AT-MB item | BLOCKED | no hardware; see milestone 5 |

## Milestone 4: packaging, security, operations

| ID | Status | Evidence |
|---|---|---|
| AT-PKG-01 wheel outside the tree serves UI offline | PASS (Linux x86_64, py3.13) | `scripts/check_installed_wheel.py` 16/16 (2026-10-12, stage 3 wheel) |
| AT-PKG-01 Windows / macOS / ARM64 | PASS (GitHub CI) | run 38221964706 on `b3c32eb`: full suite, `demo verify` and the serial-isolation check on Windows 2022, macOS 14 and Ubuntu 24.04 (Python 3.11, 3.12, 3.13) and Ubuntu ARM64 (3.11, 3.13); installed-wheel check on all four. The first run on `main` (`b5700c8`) failed on Windows only: a legacy test read a file in the Windows code page (fixed). ARM64 here is a hosted runner, not a CM4 |
| AT-PKG-02 hash-locked requirements, `pip check` | PASS | `requirements.lock`, `requirements-dev.lock`; installs and `pip check` on 3.11/3.12/3.13; ARM64 py3.11 wheels all available |
| AT-SEC-02 expiry, revocation, rate limits, CSV escaping, XSS-safe | PASS | `test_idle_expiry`, `test_password_change_revokes_sessions_and_logout`, `test_csv_export_escapes_and_discloses_completeness`, CSP header + `textContent`-only UI |
| AT-OPS-01 backup, verify, restore to new instance | PASS | see AT-BAK-01 … AT-BAK-06 |
| AT-OPS-02 migrations, v0.1 backup-first | PASS | `test_migration_backup_first_and_no_downgrade`, `test_v1_database_migrates_to_v2_backup_first`, `integration/test_migrate_v01.py` |
| AT-OPS-03 budget / full disk ⇒ degraded, starts inhibited | PASS | `test_storage_failure_inhibits_starts`, `test_bench_on_refused_when_storage_degraded`, AT-CAP-01 |
| AT-OPS-04 acquisition supervision | PASS | `integration/test_supervision.py` |
| AT-OPS-05 systemd unit, CM4 guide, rollback doc | PASS (documents) / NOT RUN (on a CM4) | `deploy/`, `systemd-analyze verify` parses; `docs/installation/cm4.md`, `docs/operations/service.md` |
| Storage sizing measured | PASS (desktop) | superseded by AT-STO-01 (60 s, 365 days, ADR-0008) |

## Historian recording, retention and backups (owner requirements of 2026-10-12, ADR-0008 revised after the owner review)

Test names are in `tests/integration/` (`test_recording.py`, `test_retention.py`, `test_backup.py`,
`test_api.py`, `test_historian.py`). Desktop measurements: `operations/measurements/` (`2026-10-12-measurements.md`).

| ID | Status | Evidence |
|---|---|---|
| AT-HIS-02 60 s by default and in every shipped config; only 60 or 300 accepted | PASS | `test_recording_interval_is_60_s_and_only_60_or_300_allowed`, `test_default_history_is_60_s_of_wall_time_at_any_speed` |
| AT-HIS-03 labels are wall-clock boundaries on a monotonic schedule, each once (60 s and 300 s); 300 s labels are five-minute boundaries | PASS | `test_recorder_labels_wall_boundaries_once_on_a_monotonic_schedule[60]`, `[300]`, `test_300_s_labels_are_five_minute_boundaries` |
| AT-HIS-04 acquisition, alarms, edges and dashboard keep their rate; polls write no history rows | PASS | `test_hw_acquisition_keeps_its_rate_and_snapshots_are_separate_and_complete`, `hardware/test_modbus.py::test_physical_edges_overrun_and_gaps_are_events` |
| AT-HIS-05 edges, alarm transitions, ack/reset, commands and config changes written when they happen | PASS | `test_hw_edges_and_alarm_transitions_are_written_immediately`, `test_sim_alarm_transitions_and_ack_do_not_wait_for_a_snapshot`, `test_config_loaded_and_changed_events`, `test_commands_audited_with_outcomes` |
| AT-HIS-06 stale and never-acquired readings recorded as `STALE` / `UNAVAILABLE`, last good value kept | PASS | `test_stale_limit_follows_the_bus_poll_rate`, `test_hw_snapshot_marks_stale_and_never_acquired_points`, `test_sim_snapshot_marks_stalled_acquisition_stale`, `test_stale_sample_replace_keeps_traceability` |
| AT-HIS-07 restart keeps history, no back-fill, continues after the newest stored label | PASS | `test_hw_restart_preserves_history_and_resumes_on_next_boundary`, `test_restart_continues_after_the_newest_stored_label`, `test_alarm_persist_and_restore_without_duplicates` |
| AT-HIS-08 simulation records on wall time with run id and simulated time; 1×, 10× and 60× give the same count (R2) | PASS | `test_sim_records_on_wall_time_whatever_the_speed[1]`, `[10]`, `[60]`, `test_default_history_is_60_s_of_wall_time_at_any_speed` |
| AT-HIS-09 no schema migration; an existing database opens unchanged | PASS | `test_this_update_adds_no_schema_migration` |
| AT-HIS-10 a label is stored once per database, checked in the writing transaction, across restarts (also on a boundary) and clock corrections (R2) | PASS | `test_a_label_is_stored_once_per_database_across_sessions`, `test_sim_restart_on_a_boundary_and_clock_corrections_never_duplicate_a_label` |
| AT-HIS-11 forward step: one snapshot, gap logged; backward step: held until the clock passes the newest label; stalled process: missed labels logged; nothing fabricated (R2) | PASS | `test_forward_clock_step_writes_one_snapshot_and_logs_the_label_gap`, `test_backward_clock_step_holds_until_the_clock_passes_the_newest_label`, `test_stalled_process_logs_missed_labels_without_back_fill`, `test_labels_not_written_for_low_disk_are_not_reported_again_as_missed` |
| AT-HIS-12 a paused simulation keeps wall-time snapshots, frozen readings `STALE`, no back-fill (R2) | PASS | `test_sim_pause_keeps_wall_snapshots_and_records_frozen_readings_as_stale` |
| AT-HIS-13 the optional simulation-time series is off by default, separately labelled and counted, and selectable in trends (R2) | PASS | `test_optional_sim_time_recorder_is_off_by_default_and_separately_labelled`, `test_sim_time_recorder_is_separate_bounded_and_labelled_at_speed`, `test_trend_gap_flags_from_recorded_cadence` |
| AT-RET-01 rolling window boundary at exactly 365 days | PASS | `test_rolling_window_boundary_is_exactly_365_days` |
| AT-RET-02 pruning in batches through the writer; dry run and `DEMO` delete nothing | PASS | `test_prune_runs_in_small_batches`, `test_dry_run_and_demo_policy_delete_nothing`, `test_demo_policy_never_holds_or_deletes` |
| AT-RET-03 over budget never deletes inside the window | PASS | `test_over_budget_is_a_warning_and_nothing_inside_the_window_is_deleted` |
| AT-RET-04 clock trust at start and while running catches small jumps either way, also for a history without snapshots (R4) | PASS | `test_start_check_catches_small_jumps[...]` (40 min restart trusted, 2 h downtime held, 3 min behind tolerated, 10 min behind held), `test_clock_steps_while_running[...]` (±2 min trusted, ±10 min held), `test_retention_holds_for_a_history_without_snapshots` |
| AT-RET-05 size levels off once the window is full (freed pages reused) | PASS (desktop) | `measure_storage.py --days 400`: 8.10 GiB at day 365, 8.11 GiB at day 400; samples 25,228,800 at day 365 and 25,228,800 at day 400 |
| AT-RET-06 a hold is released only by an administrator confirming the shown cutoff, audited; acknowledging the alarm does not release it; the command line follows the same rule (R4) | PASS | `test_hold_after_downtime_is_released_only_by_confirming_the_cutoff`, `test_retention_hold_is_released_by_an_admin_confirmation_not_by_an_alarm_ack`, `test_cli_retention_apply_deletes_only_with_the_confirmed_cutoff`; headless Chromium: Settings shows the hold and proposal, the release button stays disabled until the date check is ticked, release recorded as `RETENTION_CLOCK_CONFIRMED` by the administrator |
| AT-RET-07 forward and backward clock corrections never delete current-window records (R4) | PASS | `test_forward_jump_protects_current_window_records`, `test_clock_steps_while_running[-600-CLOCK_STEP_BACKWARD]` |
| AT-RET-08 registers: process sessions and runs pruned only when nothing refers to them; the running session never (R4) | PASS | `test_process_and_run_registers_are_pruned_only_when_nothing_refers_to_them` |
| AT-WAL-01 WAL reported separately from the database file, warned above 256 MiB; checkpoint progress shows a reader holding it (R1) | PASS | `test_wal_is_reported_separately_and_warned_about`, `test_checkpoint_progress_shows_a_reader_holding_the_wal` |
| AT-WAL-02 retention runs are bounded and wait while the WAL is held, keeping partial counts (R1) | PASS | `test_pruning_is_bounded_and_waits_while_the_wal_is_held` |
| AT-WAL-03 long readers are bounded: the backup aborts on WAL growth; export, audit export and analytics are capped (R1) | PASS | `test_wal_growth_cancels_the_long_reader`, `test_csv_export_escapes_and_discloses_completeness` |
| AT-WAL-04 the acquisition loop's storage check never waits behind a user query (R1) | PASS | `test_capacity_check_does_not_wait_behind_a_long_user_query` |
| AT-WAL-05 concurrent acquisition, events, commands, backup, year-range readers and retention: WAL, free space and responsiveness (R1) | PASS (desktop) | `stress_concurrency.py` on the 400-day database: backup published after 5.9 min (0.48 GiB, verified before publishing; largest WAL it saw 377 MiB); largest WAL 377 MiB; snapshots 485 / 485; 1.1 ms / 2.2 ms; command intent p99/max 23 ms / 547 ms; lost: 0 of 8,398 events; 0 of 241 commands without outcome or refused |
| AT-CAP-01 low disk, reserve, snapshot floor and write failure have defined effects | PASS | `test_capacity_levels_low_disk_reserve_and_snapshot_floor`, `test_write_failure_is_critical_and_inhibits_starts` |
| AT-CAP-02 capacity visible: projection, thresholds, growth, WAL, snapshot gaps, DEMO growth, off-device state | PASS | `test_projection_of_the_365_day_size_is_reported`, `test_backup_state_shows_off_device_unconfigured_and_restore_drill_due`, `storage report`; headless Chromium: Overview storage line, Settings storage, retention and backup cards |
| AT-BAK-01 compressed backup verifies and restores to a new instance; text byte-exact (quotes, CR, LF, non-ASCII) | PASS | `test_compressed_backup_verifies_and_restores_to_a_new_instance` |
| AT-BAK-02 tampered or truncated backups fail verification | PASS | `test_tampered_or_truncated_backups_fail_verification` |
| AT-BAK-03 consistent while the historian keeps writing; manifest and dump share one snapshot (R3) | PASS | `test_backup_is_consistent_while_the_historian_keeps_writing`, `test_concurrent_backups_restore_to_their_own_snapshot` |
| AT-BAK-04 rotation only after publication; refused when it would eat the reserve | PASS | `test_rotation_keeps_newest_and_leaves_other_copies`, `test_backup_refused_when_it_would_eat_the_reserve`, `test_cli_backup_keep_and_refusal` |
| AT-BAK-05 earlier single-file backups still verify and restore | PASS | `test_single_file_backups_remain_verifiable_and_restorable` |
| AT-BAK-06 backup, verification and restore drill of a one-year database | PASS (desktop) | `measure_storage.py`: 8.11 GiB database, 0.49 GiB backup written and verified in 5.8 min, quick verification 44 s, restore drill passed in 9.1 min (7.83 GiB restored, 9.73 GiB scratch required; integrity ok, 0 foreign-key violations, schema 2, application start-up ok) |
| AT-BAK-07 a backup that fails verification publishes nothing and keeps earlier backups (R3) | PASS | `test_failed_verification_publishes_nothing_and_keeps_earlier_backups` |
| AT-BAK-08 running out of space while writing, an interrupted backup and stale partials leave nothing behind (R3) | PASS | `test_running_out_of_space_while_writing_aborts_cleanly`, `test_interrupted_backup_leaves_nothing_behind`, `test_stale_partials_and_unpublished_files_are_never_listed_and_get_cleaned` |
| AT-BAK-09 restore drill: integrity, foreign keys, schema, counts, values and application start-up; needs scratch space and keeps the reserve; writes a report (R3) | PASS | `test_restore_drill_catches_tampering_that_keeps_counts_and_checksum`, `test_restore_drill_needs_scratch_space_and_keeps_the_reserve`, `test_cli_restore_drill_writes_a_report` |
| AT-BAK-10 the safety copy before a migration needs room for the whole database (R3) | PASS | `test_safety_copy_before_a_migration_needs_room_for_the_whole_database` |
| AT-STO-01 growth at 60 s, 365-day size and budget, with environment and workload recorded (R5) | PASS (desktop) | 23.8 MB/day; 8.10 GiB at 365 days with commands, alarms and faults; budget 10 GiB database, 1 GiB transient WAL, 4.5 GiB backups (ADR-0008); environment, commit, command and seed in the report; 24 points: 12.0 MB (11.4 MiB) per day, about 4.1 GiB a year; backup 16.4 times smaller; restore drill passed |
| AT-STO-02 growth, backup time, WAL and free space on the CM4 eMMC | BLOCKED | needs the CM4; part of CP-00 (`storage report`, `measure_storage.py --days 7`, `stress_concurrency.py`) |

## Timestamp quarantine (owner request of 2026-10-12 16:01 UTC, ADR-0011)

Tests in `tests/integration/test_quarantine.py`; every UTC stamp the historian writes follows a fake
wall clock, so clock steps, quarantine, restart and retention run together in one process.

| ID | Status | Evidence |
|---|---|---|
| AT-QUA-01 original records, values and stamps kept; affected records flagged; nothing deleted or rewritten | PASS | `test_forward_step_then_correction_quarantine_restart_and_retention` (row counts and every sample's id, stamp and value identical before and after) |
| AT-QUA-02 recording resumes right after the quarantine in a new clock epoch; old future-dated labels no longer block; no back-fill | PASS | same test (11 snapshots after the quarantine, ids `slot/60s/e1/...`, `HISTORY_CLOCK_BEHIND` cleared) |
| AT-QUA-03 genuine duplicates still refused: identity is epoch plus label, ordered by sequence | PASS | `test_label_identity_is_epoch_and_label` |
| AT-QUA-04 retention held until a separate date-and-cutoff confirmation, also across a restart; quarantined records go only a window after the quarantine; the register is never pruned | PASS | `test_forward_step_then_correction_quarantine_restart_and_retention` |
| AT-QUA-05 a process whose clock was wrong from boot, corrected at restart, is proposed from its start to the correction | PASS | `test_clock_wrong_from_boot_corrected_at_restart`, `test_nothing_to_quarantine_without_future_stamps` |
| AT-QUA-06 administrator only, date check required, a changed proposal refused, audited with the actor | PASS | `test_api_quarantine_is_admin_only_audited_and_flags_charts_and_exports` |
| AT-QUA-07 suspect records distinguished in trends, sample and audit exports, events, alarms and command history | PASS | same test; headless Chromium: dashed grey suspect series with rings and caption, `SUSPECT TIME` markers, Settings card before and after the quarantine |

## Off-device backups (owner request of 2026-10-12 16:01 UTC, ADR-0012; not activated)

Tests in `tests/integration/test_offsite.py`. The card is simulated (a replaceable kernel probe) and
the cloud is a local remote; `test_real_rclone_crypt_round_trip` runs the real rclone binary
(1.60.1 here; Ubuntu's package in CI) through a local crypt remote. **No physical card, USB reader or
OneDrive account was used.**

| ID | Status | Evidence |
|---|---|---|
| AT-OFF-01 card identity verified before writing: mount point, not the eMMC, confirmed UUID, read-write, enrolment marker | PASS | `test_card_identity_checks_fail_closed`, `test_linux_mountinfo_parsing` |
| AT-OFF-02 card missing: nothing written anywhere, no fallback to the eMMC, failure recorded | PASS | `test_missing_card_writes_nothing_and_never_falls_back` |
| AT-OFF-03 daily consistent compressed backup with checksum and manifest on the card, re-verified from the card | PASS | `test_backup_verified_queued_offline_then_uploaded_when_back` |
| AT-OFF-04 upload is copy only (`copyto`), through a crypt remote, verified with `cryptcheck`; an unverified upload is not counted; a non-crypt remote is refused | PASS | same test, `test_rclone_wrapper_issues_only_copy_operations`, `test_real_rclone_crypt_round_trip` (names and contents encrypted at rest) |
| AT-OFF-05 offline: uploads queued with backoff, recording continues, the queue survives a restart and drains when the connection returns | PASS | `test_backup_verified_queued_offline_then_uploaded_when_back` |
| AT-OFF-06 failed creation, failed verification (before or after publication) and a full card keep earlier backups | PASS | `test_failed_creation_or_verification_keeps_earlier_backups` |
| AT-OFF-07 7 local / 30 + 12 cloud proposed and shown; nothing pruned before an administrator confirms that policy; a changed policy pauses pruning; queued and unknown copies never pruned; refused when names are later than the clock | PASS | `test_copy_policy_selection`, `test_pruning_waits_for_confirmation_and_never_deletes_unuploaded` |
| AT-OFF-08 monthly drill from the downloaded, decrypted cloud copy into SD scratch space; 10 GiB plus headroom preflight; sessions and bench leases removed from the copy; production database untouched; scratch removed (also after a crash) | PASS | `test_monthly_drill_from_cloud_copy_into_card_scratch`, `test_real_rclone_crypt_round_trip` |
| AT-OFF-09 shipped disabled; nothing runs until the card and account are confirmed | PASS | `test_not_activated_until_owner_confirms` |
| AT-OFF-10 outcomes recorded once as events (also after a restart); alarms for failed backups, upload backlog and drills | PASS | `test_monitor_copies_journal_once_and_raises_alarms` |
| AT-OFF-11 status in the API and Settings; policy confirmation administrator only, audited | PASS | `test_api_shows_state_and_policy_confirmation_is_admin_only`; headless Chromium: Settings card with a simulated card and an offline queue, policy confirmation |
| AT-OFF-12 the physical card in its USB reader on the CM4, the owner's OneDrive account, token refresh, throughput, the escrow test | BLOCKED | card and folder chosen 2026-10-12; needs the hardware, the folder shared with the signing-in account, and the rclone setup on the controller (CP-00) |

## Pre-hardware release: installation model (PH) and process controllers (PC)

Gates from the owner's `PRE_HARDWARE_ACCEPTANCE_PLAN.md` (12 Oct 2026). Configuration
`installation-48` (SIMULATED), ADR-0013 and ADR-0014. **Everything here runs on the synthetic twin
with DEMO dynamics and a synthetic chemistry fixture. None of it is physical verification.** Scenario
names refer to `src/aquacrop/twin/installation_scenarios.py`; each runs as
`tests/acceptance/test_installation_pc.py::test_scenario[<name>]` and in `demo verify --config
installation-48` (45/45 PASS: 43 scenarios plus determinism and replay, 48-point I/O round trip,
isolation and inventory checks). The UI gates are in the next section; OP, BK and DL follow in a
later pull request.

| ID | Status | Evidence |
|---|---|---|
| PH-01 fixture: 20,000 L main, three 5,000 L stocks, about 5,000 L sump, 2 main + 2 return + 3 dosing pumps, nine valve duties, no agitator or third main pump | PASS | `acceptance/test_installation_ph.py::test_ph01_fixture_facts` |
| PH-02 S1 = channels 1-6, S2 = 7-13; 52 outlets as adjustable distribution, not electric valves | PASS | `test_ph02_sectors_and_outlets` |
| PH-03 volume conservation over five tanks, channel retained water, transit, additions, consumption and spill; retained depth kept after a sector stops; saturation, source-empty, simultaneous flows | PASS | `test_ph03_ledger_closes_under_simultaneous_flows`, `test_ph03_retained_depth_preserved_after_sector_stops`, `test_ph03_source_empty_is_unmet_demand_not_invented_water`, `test_ph03_saturated_transmitter_is_flagged_and_plant_untouched`; every scenario asserts the ledger residual |
| PH-04 48 unique points, each injectable on the I/O test source; module inventories and quality visible; unmapped channels testable without invented coupling | PASS | `test_ph04_all_48_points_round_trip_on_io_test`, `test_ph04_processes_refuse_io_test_source_and_raw_twin_commands` |
| PH-05 typed raw/scaled AI, 4-20 mA, out-of-range/NaN/stale/unavailable; DI edges; all 16 DO with audited outcomes; no ownership leak between plant and I/O test | PASS | `test_ph05_stale_unavailable_and_edges`, `test_ph05_controller_commands_are_audited_with_process_and_recipe`, `test_ph05_no_ownership_leak_after_stop_and_reset`, `test_ph04_all_48_points_round_trip_on_io_test` |
| PH-06 same seed, recipe and actions replay; pause, step and speed do not change integration order or the wall-time historian cadence | PASS | `test_ph06_replay_reproduces_state`, `test_ph06_speed_pause_and_step_do_not_change_results`; wall-time cadence: `integration/test_recording.py::test_sim_records_on_wall_time_whatever_the_speed` (recorder shared by both topologies; run on the demo fixture) |
| PH-07 recipe validation (units, ordered thresholds, usable capacity, finite values, positive maxima, incompatible endpoints); draft, activate, version and rollback persisted and audited; active recipe immutable, changes apply next cycle | PASS | `test_ph07_validation_catches_missing_fields_and_ranges`, `test_ph07_validation_catches_unsafe_orderings`, `test_ph07_lifecycle_audited_immutable_rollback`, `test_ph07_recipe_change_applies_next_cycle`, `integration/test_api_installation.py::test_recipe_roles_validation_and_persistence` |
| PH-08 missing calibration, mixing or mapping never inherits DEMO values; a profile or recipe change never enables hardware writes | PASS | `test_ph08_demo_preset_only_in_simulation_and_not_the_owner_example`, `test_ph08_real_profile_cannot_carry_simulated_drive_adapter_or_twin`, `test_ph08_installation_labels_are_demo`, `test_pc08_mixing_readiness_is_not_verified_on_a_physical_profile` |
| PC-01 two full alternating cycles P1/S1 then P2/S2, 300 s each; edited durations apply next cycle; never both pumps selected | PASS | scenarios `irrigation_alternation`, `recipe_edit_next_cycle` |
| PC-02 stop, verified stopped, old valve closed, new pump selected, new valve open, at least 1 s lead, start, run and flow verified; missing stop feedback forbids selection | PASS | scenarios `irrigation_alternation` (event order checked), `manual_irrigation_operator_pump`, `vfd_stop_feedback_missing`, `sector_valve_stuck_closed`, `contactor_welded`, `contactor_no_close`, `drive_no_run`, `drive_modbus_refused`, `drive_modbus_ignores_writes` (run command over the simulated drive's Modbus interface, ADR-0015) |
| PC-03 MANUAL selects either main pump under the same interlocks; AUTO rejects leftover manual ownership; restart begins stopped without actuation | PASS | scenario `manual_irrigation_operator_pump`; `test_pc03_restart_begins_stopped_and_auto_rejects_leftover_outputs` |
| PC-04 low main level latches one refill batch; A, B and water overlap and stop independently; chatter makes no duplicate batch | PASS | scenario `refill_ec_target`; `test_pc04_level_chatter_and_repeated_samples_latch_one_batch` (chatter shorter than the debounce, chatter during a batch, no new batch until the level passes trigger + hysteresis) |
| PC-05 refill runs while irrigation and return stay enabled; flows coordinated without unaccounted overflow | PASS | scenario `refill_concurrency`; `test_pc05_a_process_cannot_command_another_process_equipment`, `test_pc05_unrelated_mode_change_does_not_touch_other_processes` |
| PC-06 FIXED_VOLUMES with EC validation and EC_TARGET with A:B ratio: explicit selection, distinct completion, bounds, traceability; an unset target cannot become production-ready | PASS | scenarios `refill_ec_target`, `refill_fixed_volumes`, `refill_fixed_ec_out_of_band`; `test_ph07_validation_catches_missing_fields_and_ranges` |
| PC-07 synthetic acid to an adjustable target near pH 6 with bounded steps and settling; already in band, undershoot, stale probe, wrong/no response, batch maximum; no base actuator | PASS (synthetic fixture) | scenarios `acid_to_target`, `acid_already_in_band`, `acid_undershoot`, `acid_no_response`, `acid_batch_limit`, `acid_ph_unavailable` |
| PC-08 no real mixing method: physical dosing readiness blocked with a reason; chemistry labelled and versioned; no linear pH averaging; no fictitious agitator | PASS | `test_pc08_mixing_readiness_is_not_verified_on_a_physical_profile`; fixture id `synthetic-carbonate-ec-v1` in every snapshot; `InstallationFacts.agitators` fixed at 0 |
| PC-09 stock empty, stock not prepared, no flow and transfer timeout stop the affected paths; water filling never asserts readiness; acknowledgement audited and invalidated by a defined stock change | PASS | scenarios `refill_stock_not_prepared`, `refill_stock_empty`, `refill_transfer_no_flow`, `refill_transfer_timeout`, `stock_fill_invalidates`; `test_pc09_stock_acknowledgement_is_audited_and_invalidated_by_level_rise`, `test_pc09_readiness_survives_restart_through_its_store` |
| PC-10 return on sump thresholds, respects main high level and sump low level, shows the duty policy; both pumps exercised; no inferred failover or rotation | PASS | scenarios `return_alternation`, `return_pump_no_flow` |
| PC-11 sump high with the main tank full alarms and holds the transfer; pump and valve faults propagate as defined; safe responses logged | PASS | scenario `sump_high_main_full`; the fault scenarios under PC-12 |
| PC-12 a fault stops and locks out; ack alone cannot reset; reset while the fault is active fails; a healthy reset does not restart; a deliberate restart succeeds under the same role rules locally and remotely | PASS | scenarios `vfd_trip_recovery`, `drive_modbus_comm_loss`, `safety_chain_open`, `main_pump_no_flow`, `sector_valve_stuck_open`, `main_level_open_loop`, `stale_acquisition`, `module_timeout`, `main_source_empty`, `main_low_low`; `test_pc12_ack_reset_restart_are_distinct`, `test_pc12_storage_fault_inhibits_new_starts`, `test_pc12_stop_all_brings_every_output_off`; drive status word against the hardwired inputs, comm loss and replay: `acceptance/test_installation_vfd.py`; roles: `integration/test_api_installation.py` (one API for local and remote) |
| PC-13 bounded MANUAL fills, transfers and doses honour quantity, time, capacity and arbitration; a stale required sensor disables the operation with an actionable reason | PASS | scenarios `manual_bounds_rejected`, `raw_command_refused`, `stale_acquisition` |

## Pre-hardware release: process pages and installation diagram (UI)

Browser evidence comes from `scripts/ui_check/run_ui_check.py`: it serves `installation-48`
(SIMULATED) on 127.0.0.1 with a throwaway state directory and administrator, drives headless Chromium
through Playwright, restarts the server and checks what survived, then serves the HARDWARE_READ_ONLY
profile over the in-memory fake bus (`tests/fakes`) for the read-only pages. Every screen step is
paired with an API assertion on the controller state. It runs in CI (job `ui`, screenshots and
`checks.json` kept as the `ui-evidence` artifact); the local run of 2026-10-12 passed 54 checks with
Playwright 1.56.1 and Chromium. **Simulation and a fake bus only; none of this is physical verification.**

| ID | Status | Evidence |
|---|---|---|
| UI-01 overview plus nutrients/acid, main tank, main pumps, sector 1, sector 2 and drainage pages; shared alarm, trend, I/O, settings and backup functions | PASS | `ui_check` "UI-01 nav" and "UI-01 page *" (13 pages with content, no page errors, no stale banner); "UI-01 process page *" (each page has the diagram with its own parts highlighted, its controls or a link to them, its alarms and a last-hour trend); "UI-01 nutrients tabs" (stock A, stock B and acid tabs with the active recipe's pH target, EC target and A:B ratio); "UI-01 actionable alarm on the process page" (the irrigation lockout listed on Main pumps with Ack and the next step); `integration/test_api.py::test_static_ui_is_packaged_and_offline` (plant.js packaged, no external URL, no innerHTML, no style attributes under the CSP) |
| UI-02 click-through diagram matching the confirmed topology; state, flow, volume, pH, EC and pressure with units, quality and age; requested versus observed; no misleading installed-probe or mixer labels | PASS | `ui_check` "UI-02 *" (25 clickable parts with labels, mouse and keyboard navigation, units, quality and age on the diagram, probe "proposed; not installed or verified", "No agitator or air pump"); `integration/test_api_installation.py::test_plant_view_binds_every_signal_with_units_quality_and_age` (48 points, 13 UNBOUND, 1 drive adapter, owner layout); hardware profile: `hardware/test_api_hardware.py::test_plant_view_is_read_only_and_unbound_on_a_hardware_profile` and `ui_check` "HW *" (the same pages read-only over the fake bus: reason shown instead of controls, unmapped values "not measured", observations labelled REAL, no Recipes page, and "DL-03 no write from the browser on the read-only profile": 0 write frames on the fake bus) |
| UI-03 owner walkthrough: edit a recipe, run concurrent processes, inject a fault, recover deliberately, export data, restart | PASS (simulation) | `ui_check` "UI-03 *": sector 1 300 to 240 s saved, validated and activated as `demo-sim@v2`; irrigation, return, refill and acid in AUTO together with the three stocks acknowledged; drive TRIP locks irrigation out while the other three stay AUTO; reset refused (409) before acknowledgement, then acknowledge, reset (stays STOPPED) and AUTO on request; CSV downloaded with rows and its completeness footer; after a server restart every process is STOPPED, `demo-sim@v2` is still active and the stocks are still PREPARED. Screenshots `walk-1` to `walk-6` |
| UI-04 viewer/operator/admin table enforced at the API including forged and direct requests; individual login, expiry/revocation, password change, CSRF/origin and rate-limit regressions | PASS | `integration/test_api_installation.py::test_role_table_is_enforced_on_every_process_and_recipe_mutation` (12 mutations x 3 roles, plus missing CSRF, wrong origin, forged token and no session); `ui_check` "UI-04 viewer" (new account must change its password, controls disabled, forged browser request 403); existing regressions `test_every_mutation_requires_session_origin_and_csrf`, `test_roles_enforced_server_side`, `test_login_failures_and_rate_limit`, `test_idle_expiry`, `test_password_change_revokes_sessions_and_logout` in `integration/test_api.py` |
| UI-05 offline packaged assets; PC, tablet and phone viewports; no colour-only alarm semantics; a broken and reconnected network shows stale data and does not replay commands | PASS | `ui_check` "UI-05 viewport phone 390px / tablet 820px / pc 1366px" (eight pages each, no horizontal overflow), "UI-05 text on every status mark", "UI-05 offline" (stale banner while offline; a STOP clicked offline is not sent and not replayed after reconnecting); `test_static_ui_is_packaged_and_offline` |
| UI-06 OpenVPN/TLS deployment guide and route test; real home-to-greenhouse check | PASS (guide and route test) / NOT RUN (real route) | `security/remote-access.md` (OpenVPN route, return path and name; Caddy `tls internal` on 443 in front of 127.0.0.1:8000; firewall example `deploy/nftables-aquacrop.conf.example`); `deploy/aquacrop-cm4.py tls` (`test_tls_step_writes_the_proxy_and_dropin_only`: loopback bind, `--behind-tls-proxy`, never 0.0.0.0); route test `aquacrop netcheck` (`integration/test_netcheck.py`: name, TCP, certificate from the private root, validity, health through the proxy, no plain HTTP; failures name the fix). Local operation needs no internet (assets packaged). The home-to-greenhouse check stays NOT RUN until the controller is on the LAN (`RELEASE_READINESS.md`) |

## Pre-hardware release: operations, backups and delivery (OP, BK, DL)

Gates of the owner's `PRE_HARDWARE_ACCEPTANCE_PLAN.md` (12 Oct 2026). Where an earlier row already
proves part of a gate it is named (AT-...). **Fakes only for hardware, card, cloud and CM4**
(`RELEASE_READINESS.md` section 5); the parts that need them are NOT RUN or NOT CONFIGURED, never PASS.

| ID | Status | Evidence |
|---|---|---|
| OP-01 three complete 60 s snapshots of 48 points are 144 observations, unchanged and unavailable values included; 300 s is an option, not the default, and does not slow acquisition or control | PASS | `integration/test_recording.py::test_op01_three_60_s_snapshots_of_the_installation_are_144_observations` (installation-48, a module timed out: its 8 points recorded UNAVAILABLE in every snapshot; the same control ticks at 60 s and 300 s); AT-HIS-02, AT-HIS-03, AT-HIS-04 |
| OP-02 commands and outcomes, alarms, configuration and observed DI/DO changes recorded when they happen, between snapshots; a stale sample keeps its original acquisition time and last good value apart from the snapshot time | PASS | AT-HIS-05, AT-HIS-06 (`test_hw_snapshot_marks_stale_and_never_acquired_points`: `last_good_value`, `last_good_utc`); process commands with process and recipe: `acceptance/test_installation_ph.py::test_ph05_controller_commands_are_audited_with_process_and_recipe` |
| OP-03 365-day boundary; active state, configuration and calibration references kept; no deletion inside the window for size; year-range queries and exports complete; bounded, aggregated charts | PASS | AT-RET-01, AT-RET-03, AT-RET-08; `integration/test_retention.py::test_op03_configuration_definitions_referenced_by_retained_records_are_kept` (newest `CONFIG_LOADED` of each configuration kept beyond the window; unfinished commands, open alarms, recipes and stock readiness never pruned); `integration/test_api.py::test_op03_year_range_chart_is_aggregated_and_export_pages_are_complete` (363 days of one point, all inside the retention window, charted whole as 353 daily buckets with mean, min, max and counts, partial days and a ten-day hole marked; a 12 h range stays raw; the paged CSV export returns all 8,472 samples exactly once and ends `COMPLETE`); `integration/test_trends.py` (the kept final buckets give the same chart as a full read, a refresh reads only the first and the newest buckets, a new quarantine is read again in full); `ui_check` "OP-03 year range on the trends page" |
| OP-04 clock jumps and restarts hold retention, show the proposed cutoff and need a distinct administrator confirmation; detection deletes, restarts and outputs nothing | PASS | AT-RET-04, AT-RET-06, AT-RET-07, AT-HIS-11 |
| OP-05 quarantine and resume: original rows and stamps unchanged, time reliability separate from quality, durable epoch, no label collision, provenance in charts and exports, retention guarded separately; suspect ranges kept until an administrator reviews them | PASS | AT-QUA-01 … AT-QUA-07; review before pruning (stage 4): `integration/test_quarantine.py::test_forward_step_then_correction_quarantine_restart_and_retention` (an unreviewed quarantine is kept past the window; KEEP keeps it; RELEASE lets it go a window after the quarantine; refused reviews leave the writer healthy), `test_unreviewed_quarantine_older_than_the_window_raises_review_due` (`HISTORY_QUARANTINE_REVIEW_DUE`), `test_api_quarantine_is_admin_only_audited_and_flags_charts_and_exports` (review: operator 403, bad reason 422, unknown 409, administrator 200, audited `HISTORY_QUARANTINE_REVIEWED`) |
| OP-06 storage report with the process event workload: database, WAL, bytes written and free space apart; long readers and checkpoints; backup and export at the same time; low and full disk. No CM4 endurance claim | PASS (desktop) / NOT RUN (CM4) | `operations/measurements/2026-10-13-measurements.md`: the event and command rates of every process in AUTO on `installation-48` (7,922 events and 1,299 commands a day, `measure_process_workload.py`) replayed for 400 days of 48 points at 60 s: 28.6 MB a day; database 9.71 GiB at day 365 and 9.72 GiB at day 400, reported apart from the WAL (largest 5.4 MiB, 0 bytes after close) and from the bytes written (223 GiB, about 0.56 GiB a day; not endurance evidence); backup 0.55 GiB written and verified in 7.6 min; restore drill passed in 11.1 min (11.67 GiB scratch). Against ADR-0008: 0.28 GiB of the 10 GiB allowance and about 0.1 GiB of the 4.5 GiB backup allowance left (addendum; nothing changed, CP-00 decides). `stress_concurrency.py` on that database: backup published while acquisition, events, commands, year-range readers, an export page and retention ran; largest WAL 428 MiB, released after the readers; lowest free space 16.41 GiB; snapshots 642 / 642; 0 of 9,167 events and 0 of 319 commands lost. The aggregated year chart (38 s for 16 points under load) keeps its final buckets: a refresh takes 46 ms (`integration/test_trends.py`, `measure_trend_cache.py`). Low and full disk: `test_running_out_of_space_while_writing_aborts_cleanly`, `test_capacity_levels_low_disk_reserve_and_snapshot_floor`, `test_pc12_storage_fault_inhibits_new_starts`, `test_restore_drill_needs_scratch_space_and_keeps_the_reserve`. The CM4 eMMC: CP-00 (AT-STO-02) |
| BK-01 consistent backup while acquisition, events and retention run; corrupted copy rejected; partial copy never selectable; a failed backup keeps the earlier good one | PASS | AT-BAK-01 … AT-BAK-03, AT-BAK-07, AT-BAK-08 |
| BK-02 card missing, wrong, read-only, removed during a job or too small: clear failure, never the bare mount point or the eMMC | PASS (fakes) / NOT RUN (the card) | AT-OFF-01, AT-OFF-02, AT-OFF-06; the 64 GB card in its USB reader: `RELEASE_READINESS.md` |
| BK-03 upload queue survives restarts; idempotent retries for no internet, expired authorization, throttling and quota; process availability unaffected | PASS (fakes) | AT-OFF-05, AT-OFF-10; `test_backup_verified_queued_offline_then_uploaded_when_back` |
| BK-04 copies only under the installation's crypt prefix; decrypted content verified, an upload alone is not enough; completion receipt last; secrets kept out of files, logs and source; recovery keys documented | PASS (real rclone, local remote) / NOT CONFIGURED (OneDrive account) | AT-OFF-04; receipt uploaded and checked after `cryptcheck`, none for an unverified upload, removed first when pruning (`test_backup_verified_queued_offline_then_uploaded_when_back`, `test_pruning_waits_for_confirmation_and_never_deletes_unuploaded`); `test_real_rclone_crypt_round_trip` (names and contents encrypted at rest); keys: `operations/offsite-backups.md` section 4 |
| BK-05 7 local / 30 daily / 12 monthly; one copy can serve several roles; pending uploads, the last recoverable copy, active drills and clock holds protected; local removal never deletes the cloud copy | PASS | AT-OFF-07 |
| BK-06 monthly cloud download, decryption and restore check (integrity, schema, counts, provenance, application queries) in isolated card scratch; capacity at least about 10 GiB plus working space; no drivers, sessions or leases revived | PASS (fakes and real rclone) / NOT RUN (live cloud) | AT-OFF-08; the drill also checks the receipt; `hardware/test_modbus.py::test_dl03_backup_restore_and_restore_drill_send_no_frame` |
| BK-07 replacement-machine recovery with temporary fake secrets and provider; say whether real key and account recovery was shown | PASS (rehearsal) / NOT RUN (real keys and account) | `integration/test_offsite.py::test_bk07_replacement_controller_recovers_with_only_the_escrowed_keys`: with the real rclone crypt remote, the old database, card and `rclone.conf` deleted; a fresh directory with only the two escrowed values lists, passes over a newer copy cut off before its receipt, downloads, decrypts, checks the receipt checksum, runs `backup verify --full` and the maintenance restore, and the restored database records again; a mistyped salt reads nothing. **Real key and account recovery was NOT demonstrated** (test-only keys, a local folder for OneDrive). Procedure: `operations/offsite-backups.md` section 8.1 |
| DL-01 Windows launcher: cold start, missing prerequisite, first administrator, browser, persistence, clean stop; installed wheel outside the checkout serves UI and configurations offline | PASS | `integration/test_launcher.py` (on Windows, macOS and Ubuntu in CI); CI job `release-windows` (the built ZIP unpacked on Windows, offline first run from the bundled wheelhouse, installed-wheel check); `scripts/check_installed_wheel.py` 16/16 PASS on the wheel from the locally built ZIP (Python 3.13) |
| DL-02 ARM64 locked dependencies and build; guided install, upgrade and rollback validated in CI or an isolated host; CM4 OS, UART and performance NOT RUN | PASS (CI, staging root) / NOT RUN (CM4) | `unit/test_cm4_installer.py` (install, upgrade with the pre-upgrade backup before the switch, rollback, damaged release refused, dry run changes nothing, TLS step, never formats or edits the boot configuration); CI job `release-cm4-staging` (the built ZIP on Ubuntu ARM64, Python 3.11: check, install, demo verify, installed-wheel check, upgrade, rollback, status); test matrix on `ubuntu-24.04-arm` |
| DL-03 simulated, read-only, restore and shadow paths cannot write to hardware; fake-serial regression for start, reconnect, shutdown, recovery and rejected API calls | PASS (fake bus) | AT-MB-05 (`test_at_mb_05_read_only_start_poll_reconnect_shutdown_zero_writes`, `test_at_mb_05_simulated_and_shadow_profiles_cannot_open_a_bus`), `test_dl03_backup_restore_and_restore_drill_send_no_frame` (backup, full drill with the hardware configuration and restore: not one frame); `ui_check` "DL-03 no write from the browser on the read-only profile"; on the wire: CP-05, NOT RUN |
| DL-04 bench lease: scope, expiry, session, configuration, preflight, readback, unknown outcome; short or empty replies rejected; one bus owner, serialized, stop priority, module faults isolated | PASS (fake bus) | AT-MB-03, AT-MB-04, AT-MB-06, AT-MB-07; real relays: CP-04, NOT RUN |
| DL-05 release ZIP, hashes, version and commit, exact commands, guides, readiness report and requirement-to-test manifest; no unsigned template presented as a PASS | PASS | `scripts/build_release.py` (CI job `release`, artifact `release`): wheel, launchers, CM4 installer, configurations, guides, `requirements.lock`, offline wheelhouses `win_amd64-py312` and `aarch64-py311`, `RELEASE.json` (version, commit, `physical_verification: false`), `MANIFEST.sha256` of every file and the ZIP's own `.sha256`; `RELEASE_READINESS.md`; `acceptance-manifest.json`; commissioning templates marked UNSIGNED |

## Milestone 5: hardware arrival

| ID | Status | Evidence |
|---|---|---|
| CP-00 … CP-06 procedures and unsigned templates | PASS (documents) | `docs/commissioning/` |
| CP-00 … CP-05 execution | BLOCKED | needs hardware on the isolated Malmö bench and the owner's written authorization of the setup |
| CP-06 execution (drive Modbus map) | BLOCKED | needs the drive, its Modbus manual and the drive settings of `model/vfd_modbus.md` §4 |

## Production limitation matrix

| Area | State in this release |
|---|---|
| Physical AUTO / live control | Does not exist; no flag enables it |
| Hydraulic topology | Tank sizes, equipment counts, sectors and channel geometry confirmed by the owner (2026-10-12); dynamics, switch levels and spans are DEMO |
| Wiring, VFD interface, probes, production recipes, safe-state matrix | TBD — requires operator confirmation; the 48-point allocation and the cause-and-effect table are proposals (ADR-0013, `model/signal_budget.md`, `model/cause_and_effect.md`) |
| Process controllers (irrigation, return, refill, acid, stock fills) | Simulation only; mixing readiness is NOT_VERIFIED on any physical profile, so dosing cannot run there (ADR-0014) |
| Chemistry | Synthetic fixture `synthetic-carbonate-ec-v1` for qualitative controller tests; not calibrated |
| Calibration | None verified; real profiles refuse non-VERIFIED scaling |
| AI channel modes on real modules | UNKNOWN until CP-01/CP-02; values stay UNSCALED |
| Shadow / what-if | Not served (ADR-0002) |
| Alarm severities for physical equipment | TBD; only DEMO rules shipped |
| Retention and off-device backups | Rolling 365 days, snapshots every 60 s (owner, 2026-10-12; ADR-0008 accepted); off-device: 64 GB ext4 SD card and the shared OneDrive folder `AQUACROP_DATOS_CANARIAS` chosen (owner, 2026-10-12), card UUID and rclone remote TBD until set up on the controller |
| Role assignments and remote physical rights | Proposal only; owner approval required |
| CM4 performance, UART mapping, RTC | Not measured / not verified |
| Safety functions (E-stop, motor protection, dry-run, overfill) | Outside software; never replaced by it |
