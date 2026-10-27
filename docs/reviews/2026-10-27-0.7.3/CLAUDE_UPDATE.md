# Claude implementation update — 0.7.3 owner corrections

Status: **IMPLEMENTED and deployed to the bench as 0.7.5**; independent review pending.

## Source identity

- Implementation branch: `feat/operator-upgrade-0.7`
- Review baseline: `f91e34da58f55f4fa7fe725a532969439dbe6525`
- Implementation base: `e4a517a` (after `fdf40b8`)
- Final implementation commit (before this summary-only commit): `96e80d96dc` (release source `caf0f88`; it includes the owner-side CI fix `2b3b8a8`)
- Release: `aquacrop-0.7.5.zip` sha256 `78664cb35671f3af9fef700a762161d7d91612c00594bd64f324796a8f52a1fb`, build `0.7.5+caf0f88e99.w7ed33495`. `verify_release.py`: 28/28 PASS. (0.7.4, sha256 `46996a8c…`, was deployed first and superseded the same day; see the deployment notes.)

## Response to the requested changes

| Requirement | Result | Relevant files/tests and short explanation |
|---|---|---|
| Preview/Save: actionable missing-field messages; retain entries and ID behavior | PASS (software) | `programs/authoring.py` `form_problems`, `api/programs.py` `refuse`, `web/programs.js` (fields marked, first focused, entries kept). `tests/integration/test_program_problems_073.py` |
| Open program form: explicit handling of a newer validated block version | PASS (software) | `block_list` returns the latest validated version with `latest_version/status`; the form shows the version in use and a "Review and use vN" button |
| Legacy Overview import: retain components, symbols, dimensions, positions, annotations and pipe routes | PASS (software, bench drawing copy) | `topology/migrate.py` `propose`, and `diagram.py` with Shape/Placeholder/Sketch and symbol w/h. The real bench drawing (fixture `tests/fixtures/bench-legacy-drawing.json`, geometry/labels/item names only) gives 33 symbols, 8 placeholders, 5 labels, 1 box, 4 kept routes and 15 sketches, so all 47 symbols and 19 pipes are accounted for. `tests/unit/test_diagram_073.py` |
| New-format diagram import remains lossless; unexported Twin drafts protected | PASS (software) | Import from Overview saves the conversion with the replace guard. Twin and Overview are canonically equal (`tests/hardware/test_twin_design_071.py` DT-01). The DT-07 guard is unchanged. With no twin design, the Overview drawing shows read-only as REFERENCE (`/api/twin/design/reference`) |
| Tanks available through Equipment → Twin; clear absent/already-placed state | PASS (software) | `equipment/views.py` `_presentation` (capacity, level). The editor palette shows "none on Equipment / N to place / all placed". "Place here" on a placeholder keeps its place and size |
| Tank capacity affects shared presentation geometry and preserves valid attachments | PASS (software) | `diagram.tank_size` equals the JS `tankSize` rule (checked through node in `tests/unit/test_diagram_073.py`). Equipment shows the drawn size. Ports and routes follow the symbol box |
| Level sensor drop/containment associates without a pipe; persistence, tank movement and undo | PASS (software, headless editor) | `hydraulic-editor.js` `dropSensor`, `carried`, ambiguous-drop message. `tests/js/editor_tanks.test.js` covers association with no edge, a temperature sensor not associated, carry, placeholder placement and undo. Not exercised by hand in the browser |
| Equipment and Equipment types adjacent; unwanted Blocks links/instruction removed | PASS (browser, bench copy) | `web/index.html` nav order checked. Blocks has no links and the instruction is gone |
| Configurable block sensor safety conditions enforced by all affected programs | PASS (software), with limit | `BlockBody.safety.permissives/guards`; programs compiled over the block carry them first and name them as the block's. UNKNOWN never permits a start (existing engine rule, PROG-04 test). Old digests are unchanged. `tests/integration/test_block_conditions_073.py`. Limit: a program compiled over an older block version keeps that version's conditions until it is moved to the new version (the §1 "Review and use vN" path) |
| UI checks follow approved diagram-first Overview and retain equivalent coverage | PASS (CI on `e4a517a`: `ui` job green); later run see Validation | Release job UAT-12 failed because the tour pressed the disabled "Stop all processes"; fixed in `eafeb85`. `2b3b8a8` (owner side) updated the UI check for the shared-editor Overview |

## Validation

- **Focused regressions:** the files above plus `tests/unit/test_prog0910_073.py` (4), `tests/integration/test_import01_073.py` (2) and `tests/integration/test_hist03_data01_073.py` (1). The twin PROG-10 API case is in `test_twin_design_071.py`. JS: every `tests/js/*.test.js` passes run per file.
- **Actual legacy bench drawing on an isolated copy:** PASS.
  - Pre-0.7.1 backup copy served locally with throwaway accounts and a fake bus.
  - Overview → Twin reference → Import from Overview: 8 placeholders, 15 sketches and 5 labels on both sides.
  - Screenshots: `evidence-0.7.4/overview-legacy-drawing-bench-copy.jpg` and `twin-after-import-bench-copy.jpg`. `overview-before-css-fix.jpg` shows the styling defect found and fixed.
  - Project re-import of its own export: 46 SAME, 1 drawing PROPOSAL.
- **Full suite:** run once, with 2 failures.
  - A block digest test that predated the `duty` field.
  - A Windows file lock on the offsite status write; `backup.py` now retries it.
  - Both fixed and re-run green. No second full run.
- **CI:**
  - `e4a517a`: all test jobs and the `ui` job pass; the release tour failed UAT-12 (fixed).
  - `21ec351` failed: Python 3.11 f-string, a HIST-03 clock tie and the UI check. `2b3b8a8` fixes them.
  - Not re-checked after `2b3b8a8`/`caf0f88` at the time of writing.
- **Release:** 0.7.5 built from a clean tree; 28/28 PASS. The whole tree also compiles with the bench's Python 3.11.
- **Not run:**
  - A Playwright owner journey on the bench itself.
  - Manual sensor drag in a browser (covered headless).
  - A twin export → Overview approval on the bench copy (covered by DT-02).

## Deployment and owner data

- **Deployment:** 0.7.4 at 11:21, then 0.7.5 at 13:34 CET (27 Oct). The 0.7.4 SSH session dropped mid-upgrade, but the upgrade completed. 0.7.4 contained the Python 3.11 syntax error, so program preview/save would have failed on the CM4; 0.7.5 replaced it after about 2 h.
- **Running builds:** main and twin both `0.7.5+caf0f88e99.w7ed33495`. `aquacrop.programs.authoring` imports with the bench Python 3.11.2.
- **Backup and rollback:**
  - Verified pre-upgrade backups `/var/lib/aquacrop/backups/pre-upgrade/aquacrop-2026-10-27T102033.466513Z.sql.gz` (before 0.7.4) and `…T123415.811058Z.sql.gz` (before 0.7.5).
  - 0.7.4 and 0.7.3 stay installed for rollback.
- **Data preserved:**
  - Accepted drawing `899f27e51f00ea2a.json` sha `7ef8c51b…`, unchanged.
  - Schema 7; 35 items; no bench leases; no commands since the upgrades.
- **Signed-in walkthrough at `https://aquacrop-bench.local/`:** NOT RUN. Claude has no bench account and must not create one; the substitute was a copy of the bench data. Unauthenticated health through Caddy answers 0.7.5.
- **Physical operation/commissioning:** NOT RUN and not authorized. Nothing was energized, written to a module, put in AUTO or dosed.

## Remaining issues and decisions

- **Outstanding in this cycle:**
  - CI on the final commits is still to be confirmed.
  - A manual browser pass of sensor drag/undo.
  - The block-condition version limit described above.
- **Previously deferred scope, now built:**
  - IMPORT-01: `exchange.py`, `/api/project/*`, Settings card.
  - PROG-09: block `duty`; engine `_duty`, `PROGRAM_STANDBY_LEADS`. Takeover is at start; a mid-run takeover after a failed verified start is not built.
  - PROG-10: `twin/nutrient_sim.py`, twin card; `simulation_only` refused by the real engine.
  - HIST-03: `historian/runs.py` `configuration`.
  - DATA-01: run chart and an Equipment "Chart its channels" link.
- **Owner questions:**
  1. Should a program automatically pick up a newer validated block version's conditions, or keep the explicit review step?
  2. For PROG-09, is takeover at the next start enough, or should the standby start immediately after the lead fails mid-run?

Ready for independent Codex re-review: **YES**.
