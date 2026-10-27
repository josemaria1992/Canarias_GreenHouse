# AquaCrop 0.7.1 to 0.7.3: results ledger

Source: branch `feat/operator-upgrade-0.7`, draft PR josemaria1992/Canarias_GreenHouse#11 (not merged).

| Release | Commit (clean tree) | ZIP SHA-256 | verify_release.py | Full suite on that commit |
|---|---|---|---|---|
| 0.7.1 | `15f2854ab7c67632fe5c288b8c4e54969aedd80d` | `70065d9d69ae06120c76e88f37e296ba18c6adea27ae60abf75e0811e89e284e` | 28/28 PASS | 757 passed, 8 skipped, 1 failed: a poll-overrun timing test while a release build ran in parallel; it passed 3/3 alone and in the previous full run |
| 0.7.2 | `06d7c6e9ffbb41b2b9c50b44ce54fada6919ede5` | `d51cb5d362c6dd37edc3c822f4f9498322c7f16d367f935c3c26607903e7729f` | 28/28 PASS | 758 passed, 8 skipped |
| **0.7.3** (installed) | `f91e34da58f55f4fa7fe725a532969439dbe6525` | `26d6580814d47026fa853f5ce8b69f8212e676f220dcba60b3a473155ba96c3c` | 28/28 PASS | 758 passed, 8 skipped (17 min, Windows 11, Python 3.12) |

Each wheel carries its commit in `aquacrop/_build.json`; ZIPs have 2285-2286 files including the CM4 wheelhouses for
Python 3.11 and 3.13 and the bundled Windows Python.

Evidence kinds are kept apart: **software** (automated tests), **browser** (real clicks and typing on a private test
instance of this build, simulated profile, stopped afterwards), **packaged release**, **controller** (the bench CM4),
**physical** (the owner at the bench). Physical results stay NOT RUN: nothing switched a real output for this release.

## Software and browser

| ID | Result | Evidence |
|---|---|---|
| RR-01 | PASS | `tests/unit/test_stop_plans_r07.py::test_rr01_*` (3); the reviewer's probe now fails its defect assertion |
| RR-02 | PASS | `tests/hardware/test_journal_rereview_rr.py::test_rr02_*` (4) |
| RR-03 | PASS | `test_journal_rereview_rr.py::test_rr03_*` (4) |
| RR-04 | PASS | `test_journal_rereview_rr.py::test_rr04_*` (3) |
| R07-KEEP | PASS | the R07 tests and the rest of the suite (below) |
| Reviewer probes (`r07_independent_probes.py`) | 4/4 no longer reproduce | each fails on its defect assertion against this code |
| UX-01 Overview | PASS (browser, API) | `docs/results-0.7.1/browser/12-overview-after-export-applied.jpg`; `tests/hardware/test_twin_design_071.py` |
| UX-02 Blocks / Equipment | PASS (browser) | `browser/04`, `browser/01`, `browser/02` |
| UX-03 members by type, shared item | PASS (browser, API) | `browser/05`, `browser/06`; shared-membership notes; `test_ids_071.py` (one identity) |
| UX-04 circle / checkbox geometry | PASS (browser, measured) | every checkbox and radio 18×18 px; `browser/05`, `browser/06`; focus ring kept (`:focus-visible`) |
| UX-05 capital icon names, inline messages | PASS (browser) | `browser/01`, `browser/02` |
| DT-01 import from Overview | PASS (API, unit, bench copy) | `test_twin_design_071.py`, `tests/unit/test_migration_071.py`; the bench drawing converts to 33 placed items, 4 pipes, 15 pipes to decide, 8 items to create |
| DT-02 new design → export | PASS (API) / PARTLY (browser) | API journey in `test_twin_design_071.py`; browser: edit, Straighten pipes, Save, Export on the private instance |
| DT-03 aligned ports, no jog | PASS (headless JS, browser) | `tests/js/editor_align.test.js` (3); `browser/10-twin-straightened.png` |
| DT-04 H/V during drags, anchors, junctions, gaps | PASS (existing RTE tests + JS) | `tests/js/router.test.js`, `editor_align.test.js`; anchor editing by UI not separately browser-recorded |
| DT-05 reroute keeps proof, reconnect stales it | PASS (existing) | `tests/hardware/test_twin_graph_070.py` |
| DT-06 planned equipment, no operation | PASS (API) | `test_twin_design_071.py` (no bus write, nothing activated); simulated channel stays in the twin (`test_twin_modules_export_070.py`) |
| DT-07 consistent revisions, Overview kept until applied | PASS (API, browser) | `test_twin_design_071.py` (incl. replace only on second press) |
| ID-01 | PASS | `tests/unit/test_ids_071.py`, `tests/js/ids.test.js`, `browser/01` |
| ID-02 | PASS | `tests/integration/test_ids_071.py` (rename, stale revision, duplicate any case, concurrent rename: one wins) |
| ID-03 | PASS (references, versions, history) | `test_ids_071.py`; in-flight operations unaffected by design (keys never change); crash during rename: one SQLite transaction |
| MOD-01 | PASS | `tests/integration/test_rbac_070.py`, `test_modules_071.py` |
| MOD-02 | PASS (fake bus) | `tests/hardware/test_commissioning_api.py` (commissioning operator, from a request); physical: NOT RUN |
| MOD-03 | PASS | `test_modules_071.py`, `test_commissioning_api.py` |
| PRG-01..PRG-06 | PASS | `tests/unit/test_windows_071.py` (6, deterministic simulated time, commands read in order) |
| PRG-07 | PASS (existing) | DST/restart/dedup tests unchanged and passing |
| Build identity / mismatch | PASS (API, browser) | `test_twin_service.py::test_pages_name_their_scripts_by_build_and_say_which_build_answers`; `browser/11-drift-warning.jpg` |
| Upgrade 0.6.12 → 0.7.1 and rollback on a copy of the bench database | PASS | `docs/results-0.7.1/upgrade-rollback-rehearsal.json` |
| Full suite | see below | |

## Bench (controller): deployment, 27 October 2026

| Step | Result |
|---|---|
| Inventory before | 0.6.12 (`/opt/aquacrop/releases/0.6.12`), schema 5, 37 item versions (33 active), 9 bindings, 1 draft program, 3 accounts; services `aquacrop-greenhouse` and `aquacrop-twin`; accepted Overview drawing `899f27e51f00ea2a` (25 Oct 16:18); twin `layout-draft.json` and an unaccepted proposal `layout-b2de1be954f8` in the feed |
| Backups off the controller | `%LOCALAPPDATA%\AquaCropackupsench-pre-0.7.1-20261026T230817Z` and `...-20261027T074429Z` (SQLite online copies of main and twin, layout, change journal, twin state, feed, `/etc/aquacrop`; SHA-256 checked after copying); the upgrade tool's own verified backups in `/var/lib/aquacrop/backups/pre-upgrade` and `pre-migrate-v5-2026-10-27T074555...` |
| Local preview states | `%LOCALAPPDATA%\AquaCropackups\consolidation-20261026` (8000/8001 dev pair, 8030/8031 review pair, demo) |
| Rehearsal on a copy | PASS: `docs/results-0.7.1/upgrade-rollback-rehearsal.json` (0.6.12 backup, 5→7, every row kept, 0.6.12 refuses schema 7, 0.6.12 maintenance restore returns schema 5); twin database 5→7 PASS |
| Owner data on this build (copy) | PASS: Overview keeps `899f27e5…` (47 symbols) with the migration note, 33 items keep their IDs and have a water kind, program kept, wizard 403 for a plain operator and allowed for a commissioning operator, viewer sees the Overview only |
| Install | `deploy/aquacrop-cm4.py upgrade`: 0.6.12 → 0.7.1 → 0.7.2 → **0.7.3**, each from its verified ZIP |
| After | `https://aquacrop-bench.local/api/health` and `/twin/api/health`: both `0.7.3+f91e34da58.w967e67c5`; each side reports the other "same build"; schema 7, integrity ok, 37/9/1/3 records kept; accepted drawing and twin draft byte-identical to the backup; no command and no lease since the upgrade; no warnings in the service logs; releases 0.6.12 to 0.7.2 kept for rollback |
| Signed-in pages on the bench | **NOT RUN by me**: I have no bench account and did not create one on the real installation. The same build was checked signed in on a copy of the bench database (row above) and in the browser on a private simulated instance |
| Physical outputs, module writes, AUTO, commissioning | NOT RUN (nothing switched) |

Rollback: `deploy/aquacrop-cm4.py rollback --to 0.6.12` and restore both databases from the pre-upgrade backups
(the main database from `pre-upgrade`, the twin's from the off-controller copy): 0.6.12 refuses schema 7.

## Retired local previews

The PC services on 127.0.0.1:8000 (dev main, 0.6.10 snapshot / 0.7 files), :8001 (twin from the desktop 0.6.10
checkout), :8030 and :8031 (temporary review pair) were re-identified by port, PID and command line and stopped
(process trees; Windows had to terminate them, they have no window to close). Their databases pass integrity checks
afterwards, and all four state folders are in the consolidation backup. No source folder or database was deleted.
The only owner entry point is **https://aquacrop-bench.local/**.

## Open (agreed scope not built in 0.7.1)

IMPORT-01 project exchange, PROG-09 drainage lead alternation, PROG-10 labelled simulated nutrient preparation,
HIST-03 run context reconstruction, DATA-01 run-annotated equipment charts (`docs/CAPABILITIES-0.7.md`). The extended
browser fault/proposal/approval/undo journey was exercised by API tests and partly by hand in the browser; the
Playwright journey (`scripts/ui_check/run_journey_070.py`) was not run (Node Playwright is not installed here).
