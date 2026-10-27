# AquaCrop 0.7.1: results ledger

Source: branch `feat/operator-upgrade-0.7`, draft PR josemaria1992/Canarias_GreenHouse#11 (not merged). Release
built from commit `15f2854ab7c67632fe5c288b8c4e54969aedd80d` (clean tree): `aquacrop-0.7.1.zip`, 2285 files,
SHA-256 `70065d9d69ae06120c76e88f37e296ba18c6adea27ae60abf75e0811e89e284e`; the wheel carries the same commit in
`aquacrop/_build.json`. `scripts/verify_release.py` on the extracted ZIP: **28/28 PASS** (Windows 11, Python 3.12).

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

## Bench (controller) — filled in at deployment

See the section *Deployment* added when the release was installed.

## Open (agreed scope not built in 0.7.1)

IMPORT-01 project exchange, PROG-09 drainage lead alternation, PROG-10 labelled simulated nutrient preparation,
HIST-03 run context reconstruction, DATA-01 run-annotated equipment charts (`docs/CAPABILITIES-0.7.md`). The extended
browser fault/proposal/approval/undo journey was exercised by API tests and partly by hand in the browser; the
Playwright journey (`scripts/ui_check/run_journey_070.py`) was not run (Node Playwright is not installed here).
