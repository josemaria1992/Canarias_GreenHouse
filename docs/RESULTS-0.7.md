# AquaCrop 0.7.0: test record

What was run for 0.7.0, by whom and where, as **PASS / FAIL / BLOCKED / NOT RUN**, against the cases of the
owner's acceptance plan (upgrade package of 25 October 2026, ACCEPTANCE_TEST_PLAN.md). Software tests, fake-bus
checks, browser checks, packaged-release checks, CM4 measurements and owner physical results are kept apart.
Earlier results (0.6.x reviews, CI runs) are not evidence for anything new here.

Environment: Windows 11 desktop, Python 3.12 (`.venv`), Node.js for the router tests, headless Chromium through
Playwright for the browser journey. Branch `feat/equipment-programs-twin`; the release commit and checksums are in
"Packaged release" below.

## Summary

| Kind | Result |
|---|---|
| Full source test suite (unit, integration, acceptance, hardware fakes, router in Node) | PASS: 694 passed, 8 skipped, 0 failed on the code of `c5ac7cb` (before its two new test files, which pass separately: 8 passed). Skipped on Windows by design: the Linux CM4 installer, POSIX and rclone/openssl-dependent offsite and network checks |
| Fake-bus checks (no write from the twin, from imports, from viewers; existing wire-frame fixtures) | PASS, inside the suite |
| Browser journey, desktop and tablet width, three roles (`scripts/ui_check/run_journey_070.py`) | PASS, 29 of 29 checks (`operator/walkthrough-0.7/checks.json`) |
| Packaged release: build, checksums, verification | PASS, 28 of 28 `verify_release.py` checks with the bundled Python (see "Packaged release") |
| CM4: installed versions, upgrade, twin restart, rollback, load (TWIN-09, REL-04) | NOT RUN (see "CM4") |
| Owner physical results (IO 8CH test loads, wiring, any equipment) | NOT RUN |
| Owner acceptance of the walkthrough | NOT RUN: the owner's to give |

## Cases

"Tests" names the evidence; a case is PASS only for the part those tests cover, and the rest is named.

### Regression feedback

| ID | Result | Evidence and scope |
|---|---|---|
| FB-01 | PASS | `test_twin_proof_070::test_fb01_reset_and_rebinding_invalidate_the_proof_until_a_new_run`; topology change: `test_twin_graph_070::test_twin08_...`. Model-parameter change goes through the same context digest; no separate case |
| FB-02 | PASS | `test_feedback_070::test_f02_*` (sequence ON/OFF/ON, starts per hour across programs, restart is no reset) |
| FB-03 | PASS | `test_feedback_070::test_f03_*` (before start, during run, inverted, stale, missing channel) |
| FB-04 | PASS | `test_feedback_070::test_f04_*` (batch sizes, values carry their own step) |
| FB-05 | PASS | `test_feedback_070::test_f05_changed_pump_rate_...` |
| FB-06 | PASS | `test_r34_01_preview_precondition::test_fb06_...` (another session's AUTO needs a new confirmation) |
| FB-07 | PASS | `test_changes_durable_070::test_fb07_*` (failure at each step undone and reported, kill mid-apply settled at start, two approvals apply once) |

### Roles, security and runtime boundaries

| ID | Result | Evidence and scope |
|---|---|---|
| RBAC-01 | PASS | `test_rbac_070::test_rbac01_*`; browser: each role's menu and an old deep link as viewer |
| RBAC-02 | PASS | `test_rbac_070::test_rbac02_*` walks every GET route as viewer; browser: viewer API refusal; twin refuses a viewer ticket |
| RBAC-03 | PASS | `test_rbac_070::test_rbac03_*`, `test_mod02_*` (operator request is a record; closing it is an administrator's) |
| RBAC-04 | PASS (regression) | Existing bench-lease and I/O assignment tests; browser: an operator's channel stays PROPOSED. No new 0.7 case |
| RBAC-05 | PASS (regression) | Existing CSRF, origin, runtime-id and session tests in the suite; no new 0.7 case |
| RBAC-06 | PASS (regression) | Existing last-administrator and user tests; twin login follows the live role. Concurrent role change: NOT RUN |

### Equipment, blocks and topology

| ID | Result | Evidence and scope |
|---|---|---|
| EQ-01 | PASS | `test_blocks_070::test_eq01_*`; browser: six items from templates through the forms |
| EQ-02 | PASS (regression + browser) | Existing binding validation tests; browser: operator proposal is PROPOSED, not verification |
| EQ-03 | PASS | Browser: typed tank, scaling saved; a negative value refused beside the button with the field kept. Historical scale per revision: see HIST-03 |
| EQ-04 | NOT RUN | No case for one pump in two blocks, rename or retirement blockers |
| EQ-05 | PASS (part) | Rebinding invalidates evidence (FB-01); owed OFF on held channels: existing regression. Editing an active type: NOT RUN |
| TOPO-01 to TOPO-04 | PASS | `test_topology::test_topo01_*` to `test_topo04_*` |
| TOPO-05 | PASS | `test_topology::test_rte04_*` (drawing digest moves, graph digest never), `test_twin_graph_070::test_twin08_*` (a reconnect invalidates proof) |
| TOPO-06 | PASS | `test_migration_070::*` (explicit links only; inline equipment, loose ends and unlinked symbols listed, never connected) |
| IMPORT-01 | NOT RUN | Project exchange (JSON/YAML import) is not in 0.7.0; only the twin export travels |

### Routing and editing

| ID | Result | Evidence and scope |
|---|---|---|
| RTE-01, RTE-02 | PASS | `tests/js/router.test.js` (every arrangement and side pair; randomized obstacles) |
| RTE-03 | PASS | router.test.js; browser: a segment dragged with the mouse stays H/V and is pinned |
| RTE-04 | PASS | `test_topology::test_rte04_*`; browser: six symbols dragged with real mouse moves; every pipe H/V at each of 10 intermediate pointer positions |
| RTE-05 | PASS | router.test.js, `test_topology::test_rte05_*`, `test_graph_water::test_twin05_*` (a crossing carries nothing) |
| RTE-06, RTE-07 | PASS | router.test.js; `test_topology::test_rte07_*` (server rejects diagonals, detached and overlapping routes) |
| RTE-08 | PASS | `test_twin_graph_070::test_twin08_*` (route-only edits keep proof; reconnects invalidate it); migration ambiguities cannot activate (TOPO-06) |
| RTE-09 | PASS (desktop, tablet width) | Browser: connect by clicking ports, arrow keys move a selection after the page's refresh, no page-wide sideways scroll at 820 px. Touch gestures on a real tablet: NOT RUN |
| RTE-10 | PASS (desktop only) | router.test.js: 100 symbols / 150 pipes within the per-route budget on this desktop. Tablet and 400/400 stress: NOT RUN |

### Programs, schedules and ownership

| ID | Result | Evidence and scope |
|---|---|---|
| PROG-01 | PASS | `test_programs_070`, `test_program_forms_070`; browser: a volume program from the form, preview, save, validate, no JSON |
| PROG-02 | PASS (part) | Compiled plan opens the valve, waits, then starts the pump (preview in the browser); shared-drive changeover: existing regression |
| PROG-03 | PASS (part) | `test_prog03_*` (needs the block's meter in L/min); twin run delivered 10.083 L for 10 L. Stale or gapped measurement: existing engine regression only |
| PROG-04 | PASS | `test_prog04_*` (start condition unknown/false/true; stop condition stops through the plan, never DONE) |
| PROG-05 | NOT RUN | No noise/debounce case |
| PROG-06 | PASS | `test_prog06_*` and existing version-immutability tests |
| PROG-07, PROG-08 | NOT RUN | No 0.7 case |
| PROG-09, PROG-10 | NOT RUN | Drainage alternation and nutrient preparation through blocks are not in 0.7.0 |
| TIME-01, TIME-02 | PASS | `test_time01_*`, `test_time02_*` (date range by local start date; repeat after completion, count, hard end) |
| TIME-03 | PASS (regression) | Existing DST tests |
| TIME-04 | PASS (regression) | Existing engine tests for weekly windows open when AUTO is entered. The same for a repeat window: NOT RUN |
| TIME-05 | PASS (part) | `test_time02_*`: nothing starts after the hard end and a running cycle ends WINDOW_ENDED. A volume cycle cut short reported as incomplete: NOT RUN |
| CON-01, CON-02 | PASS (regression) | Existing arbiter and conflict tests |
| CON-03 | NOT RUN | No 0.7 case |

### Twin and alarms

| ID | Result | Evidence and scope |
|---|---|---|
| TWIN-01 | PASS | Twin tests count fake-bus write frames before and after (`test_twin_modules_export_070`, existing twin tests) |
| TWIN-02 to TWIN-06 | PASS | `test_graph_water::*`; browser: ledger residual 3e-13 L after a run |
| TWIN-07 | PASS (part) | TRIP and NO_FLOW faults stop the program with its cause (feedback and twin tests). Stuck valve, pressure, communication: NOT RUN |
| TWIN-08 | PASS (part) | `test_twin08_*`; run history kept in the twin's database. Saved scenarios: NOT RUN |
| TWIN-09 | NOT RUN | Needs the CM4 |
| ALM-01 | PASS | `test_item_alarms_070::*` (delay, deadband, unknown quality, acknowledge; no limits means no alarm) |
| ALM-02 to ALM-04 | PASS (regression) | Existing alarm tests; no new 0.7 case |

### History, migration and modules

| ID | Result | Evidence and scope |
|---|---|---|
| HIST-01, HIST-04, HIST-05, HIST-07 | PASS (regression) | Existing historian, recording, retention and storage tests |
| HIST-02 | PASS (part) | Program runs and timeline from recorded events (`test_twin08_*` reads the run list and CSV) |
| HIST-03 | NOT RUN | No case reconstructing a run's configuration after later edits |
| HIST-06 | PASS | Browser: the twin run is in the twin's history, not the real system's |
| DATA-01, DATA-03 | NOT RUN | Charts unchanged in 0.7.0; no new case |
| DATA-02 | PASS (part) | Runs CSV read back in `test_twin08_*` |
| MOD-01 | PASS | `test_twin_modules_export_070::test_mod01_*` |
| MOD-02 | PASS | `test_rbac_070::test_mod02_*` |
| MOD-03 to MOD-05 | PASS (regression) | Existing wizard and discovery tests; no USB adapter used |
| MOD-06 | PASS | Existing 80-point and fake-wire fixtures unchanged in the suite |
| DEP-01 | PASS | Migration 5 → 6 on a copy with data; interruption leaves schema 5 (`test_historian`, `test_retention`, `test_recording`) |
| DEP-02 | PASS | `test_migration_070::*`; the 0.6 drawing stays read-only |
| DEP-03 | PASS | `test_blocks_070::test_drawing_save_validate_block_and_revision_conflicts`, FB-07 |
| DEP-04 | PASS (regression) | Existing backup and restore tests; new tables are inside the same database file. No 0.7-specific case |
| DEP-05 | NOT RUN | 0.6.13 refusing schema 6 is by design (documented in `operations/upgrade-0.7.md`); not run against a 0.6.13 install |

### Safety and release evidence

| ID | Result | Evidence and scope |
|---|---|---|
| SAFE-01 to SAFE-05, SAFE-07 | PASS (regression) | Existing fake-wire, lease and gate tests in the suite, unchanged |
| SAFE-06 | PASS | FB-06, and `expect` checks on process actions (`test_r34_01_*`) |
| REL-01 | PASS | Full suite as above; skips are platform tools absent on Windows, not 0.7 cases. Software and fake-bus results only |
| REL-02 | PASS (simulation) | Browser journey: equipment → drawing (drag, connect) → block → volume program on a weekly schedule → twin run → history; viewer and tablet checks. Fault run in the browser: checked by hand earlier in the session, not in the scripted journey |
| UX-01 to UX-04 | PASS | Browser: a choice redraws its form at once; typed values survive the page's refresh; messages beside their button; no sideways scroll at tablet width |
| REL-03 | PASS (part) | Built from the clean commit; ZIP and manifest checksums, wheel identity, configs and the bundled Python 3.14.8 with every locked package verified. Cold start through the launcher, login, restart: NOT RUN on this build |
| REL-04 | NOT RUN | CM4 staged upgrade, paired twin and rollback |
| REL-05 | PASS (documents) | ADR-0023 to 0026, CAPABILITIES-0.7, upgrade-0.7, walkthrough-0.7 with screenshots from the journey. Owner acceptance: NOT RUN |

## Found and fixed by the browser journey

- A radio button or checkbox kept focus after a click, and the page treated that as typing: the form did not
  change until the next click elsewhere (choosing "Deliver a measured volume" left the time field showing).
- A click on a drawing symbol left the keyboard focus on the symbol, which the editor redraws at once: the arrow
  keys then scrolled the page instead of moving the symbol. The page's once-a-second refresh had the same effect.
- The twin's installation graph stayed empty after an import until the page was reloaded.
- Tank water and pipe flow in the twin were drawn black (no style).
- The program form's refusal for a missing feedback channel now says what to change.

## Packaged release

`python scripts/build_release.py --out <dir> --windows-runtime download --wheelhouse aarch64:3.11 --wheelhouse aarch64:3.13`
from a clean worktree of commit `c5ac7cb0f0b03a4afae66cf30dadc1e3bf44cd83`: `aquacrop-0.7.0.zip`, 2264 files, SHA-256
`fdd274b4e0aef0f84382644363467987cc46e9f2ae1359a3eb26b98262b8730a`. `scripts/verify_release.py` with the
release's own `runtime/python.exe`: 28 passed, 0 failed. The ZIP carries the documents as they stood at that commit
(this record without its final numbers).

## CM4

The installed versions on the bench controller were not read in this session (the remote query was not
permitted). Nothing was installed, upgraded, restarted or switched on any device.
