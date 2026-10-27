# Claude implementation update — 0.7.7 (C075 review, owner decisions and answers, UX-01..06)

Status: **implemented and deployed to the bench as 0.7.7** (0.7.6 plus the owner's pump-replacement answers). CI on the final commit had not finished when this was
written. Independent review pending. The previous update (0.7.5) is in this file's Git history.

## Source identity

- Branch: `feat/operator-upgrade-0.7`. Implementation base: `75b63d7` (the review documents).
- Final implementation commit (before this summary-only commit): `de3088a` (0.7.7). 0.7.6 was `7075853`.
- Release: `aquacrop-0.7.6.zip`.
  - sha256 `3553ac782ece331d3c5ad42a9b1532666df23c8585f420a066c32dbd4da676a3` (`aquacrop-0.7.7.zip`).
  - Build `0.7.7+de3088ab7d.w3d2015dd`.
  - `verify_release.py` 28/28 PASS.

## C075 findings

| ID | Result | Change and evidence |
|---|---|---|
| C075-01 standby outside proof | FIXED | `twin/changes.program_items` includes both duty pumps. Proof, readiness, export and history share it. `tests/unit/test_prog0910_073.py::test_c075_01…`: rebinding only the standby changes the digest. Earlier duty-program proofs no longer match. |
| C075-02 duty picker | FIXED | `blocks.js` `dutyChoose`/`dutyProblem`: the partial choice is kept in the form, and save refuses a half pair. `tests/js/blocks_duty.test.js` |
| C075-03 export loses legacy drawing; no continuation | FIXED | The export carries the Overview drawing: the explicit graph, or the faithful conversion plus `legacy_layout` and source digest. JSON and YAML round-trip in `tests/integration/test_exchange_075.py`; the bench drawing passes the document guards. Continuation: imported items are listed on Equipment (Create with imported settings / Dismiss). Imported drawings are listed in the Twin (Open as the twin's design, with the replace guard), then follow the normal export and approval path. |
| C075-04 stale preview | FIXED | The preview digest covers the document, its rows and the installation state. Apply re-checks it under a lock and refuses with "preview it again". Covered by the concurrent-validation regression. |
| C075-05 tied timestamps | FIXED | The engine records the run context (item digests, channels, duty lead) in `PROGRAM_OP_STARTED`. History and charts use it, so later rebinds are listed as changes. The stop plan is shown. `tests/integration/test_hist_context_075.py` (tied timestamps, no sleeps) |
| C075-06 rejected move | FIXED | `moveSymbol` restores the symbol, the sensors it carries and every route it touched. `tests/js/editor_tanks.test.js` (Codex case: the drawing is byte-identical after the refusal). |
| C075-07 malformed records → 500 | FIXED | `exchange._shape` checks structure, IDs, duplicates and limits first and returns a readable 422. A failed write names what was already written. |

## Owner decisions

- **Superseded block safety: implemented.**
  - `programs/authoring.superseded_safety` compares the program's block version with the newest validated one: maximum runtime, start conditions and stop conditions. A draft changes nothing.
  - Refused on every real start (live engine refusal hook, manual and scheduled), at bench activation, and shown in Programs as CANNOT START with the versions and what changed.
  - Reviewing with the new version clears this rule only; the existing twin-proof gate still applies.
  - The check reads the stores on each start, so nothing is cached across a restart.
  - The program form now describes the pinned block version.
  - Tests: `tests/integration/test_block_safety_gate_075.py`.
- **Mid-cycle pump replacement: implemented for the confirmed parts** (engine `_try_replace`/`_replacing`).
  - Order: alarm `PROGRAM_PUMP_FAILED`, OFF sent to the failed pump and verified, then the partner ON, with the sector valve untouched. The rest of the same operation continues; the next sector's start leads with the healthy pump.
  - Limits: the operation deadline and window are not extended.
  - Refused with a reason, and the normal fault stop runs, when:
    - the partner is busy in another operation (never preempted);
    - the partner is not ready;
    - the twin proof does not count;
    - a pump of the pair was already replaced;
    - the failed pump is not verified OFF.
  - Fake-service tests: `tests/unit/test_pump_replacement_075.py`.
  - **Owner answers (recorded in `OWNER_DECISIONS_20261027.md`), implemented in 0.7.7 (`983081e`):**
    1. Either pump serves either sector: each sector block names both pumps as its duty pair.
    2. Sectors take turns and one healthy pump waters both. If the partner is busy with the other sector, the
       interrupted cycle stops through its normal stop (failed pump OFF, its valve closed, verified); its remaining
       time or litres wait, and when the partner is free the rest starts again on it (`PROGRAM_PUMP_WAITING`,
       `PROGRAM_RESUMED`). The other sector is never interrupted; both are never watered at once. The rest is
       abandoned, and recorded, if its window ends or it has waited an hour.
    3. The changeover is not watering (the remaining time counts from the new pump's start); a litre cycle delivers
       exactly its litres.
  - Tests: `test_when_the_partner_serves_the_other_sector_this_sector_waits_its_turn_then_finishes`,
    `test_a_waiting_rest_is_abandoned_when_its_window_ends_and_says_so`. The litre-cycle resume follows the same
    path but has no dedicated fake-meter test.

## UX-01..UX-06

| Item | Result | Evidence |
|---|---|---|
| UX-01 empty Blocks choices | DONE | `/api/blocks/prerequisites` (`blocks/prerequisites.py`). Each role's state is shown with the equipment's names: not created, on Equipment but not drawn, waiting for approval, or unlinked earlier symbol. Meters show "planned: no channel / not verified / no scaling" and "not on the chosen path". Labels are "Water source" and "Watering destination" with examples; the setup order is shown. `tests/integration/test_block_prerequisites_076.py`. Browser: before approval every choice said "waiting for approval"; after approval there were 6/7/7 choices (`evidence-0.7.6/04-blocks-after-approval.jpg`). |
| UX-02 inline meter | DONE | "Insert flow meter into this pipe" splits the edge through inlet → outlet. The meter sits on the longest straight run, turned with the flow (vertical and reverse handled), with 90° routes. Edge metadata and lineage are kept; taps move to the upstream half. One undo step; refusals change nothing. `tests/js/editor_meter.test.js`. Browser with real pointer and keyboard: inserted Return flow into Sector 1 → Drainage tank, undone, redone, saved, reopened, exported and approved (`03-twin-meter-inserted-in-pipe.jpg`). |
| UX-03 dashed states | DONE | Water pipes are solid. Earlier pipes are dotted and selectable, saying "not a water connection yet", with Connect these ends only when nothing is drawn along them. The browser check found the bypass risk this rule now prevents; a regression test covers it. Placeholders keep their symbol and size with "Needs equipment link". Measuring lines say "no water connection". A compact legend is shown in every view. |
| UX-04 finish checklist | DONE | Twin "Finish the imported drawing": link equipment, confirm earlier pipes, put meters into pipes, then save/run/export. Each step has Show buttons, and the list follows edits. Browser: after creating the drawing's tanks, sectors and source by name, Import from Overview linked all of them (0 left, 13 pipes, 7 meters). |
| UX-05 Tank type | DONE | Each type shows its state (TEMPLATE / DRAFT / ACTIVE / RETIRED). An administrator's "Make available" adds and activates a built-in type in one step. It never reactivates a retired type, replaces an edited draft or touches items. Operators are told who can act. A tank needs no channel; the capacity field shows its drawn size. `tests/integration/test_equipment_types_075.py`. Browser on the bench copy: Tank was "built in, not added yet"; Make Tank available worked from a real click without a restart; a 20000 L tank was created and shown as 200 × 240 (`01-…`). |
| UX-06 editor closes | DONE | Open state and unsaved fields are keyed by `data-state-key` (type id + editor kind), not by caption or position. Focus, caret and scroll are kept through refreshes. Closing with unsaved changes asks first. A version change while editing is warned about and the text is kept. Browser with real clicks among 13 identically labelled editors: the editor stayed open through repeated refreshes without focus; typing and clicking away kept "EC probe edited" in that card only; a second editor stayed independent; a failed save kept the editor open and its text (`05-…`). |

## Validation

- **Focused tests:** the new tests named above, plus the earlier owner-correction and feature suites.
- **Full suite:** run once after stabilising — about 800 Python tests passed with 0 failures, and every `tests/js/*.test.js` passed.
- **Browser journey:** fresh copy of the bench data (pre-0.7.1 backup), local only, fake bus, throwaway accounts.
  - Three defects found and fixed there (`957aa24`):
    - the Equipment page did not render (a name clash);
    - an earlier-pipe connect could bypass equipment drawn on it;
    - the checklist did not follow edits.
- **Python 3.11 preflight:** on the CM4, with the built 0.7.6 release.
  - Throwaway venv from its own wheelhouse, simulated config, `/tmp` state, own port; removed afterwards.
  - Imports, authenticated Preview (refused with its field), Save, Programs, block prerequisites and project export all passed.
  - The running services stayed active.
- **CI:**
  - `2b3b8a8` BOARD-01: the check now waits for the menu and reports its actual text (`7a752e4`).
  - Locally this PC cannot reproduce BOARD-01: the run stops earlier on two steps that fail identically on the CI-green `e4a517a` (a Windows Chromium difference).
  - UAT-12 tour fix: `eafeb85`.
  - On `7075853` at the time of writing: 12 checks passing, 24 pending, 0 failing. **Not reported green.**

## Deployment and access

- **Bench:** 0.7.5 → 0.7.6 at 16:25 CET, then 0.7.7 at 17:29 CET. Main and Twin both run `0.7.7+de3088ab7d.w3d2015dd`. Before 0.7.7: full suite passed again, release 28/28, Python 3.11 preflight passed.
- **Rollback:** pre-upgrade backups verified (`…T152537.684992Z.sql.gz` before 0.7.6, `…T162935.191682Z.sql.gz` before 0.7.7); 0.7.6 stays installed.
- **Data:** accepted drawing sha `7ef8c51b…` unchanged; schema 7; 35 items; no leases; no commands.
- **Access record:** written privately outside Git (`%LOCALAPPDATA%\AquaCrop\commissioning\ACCESS-20261027.md`: hostname, address, Caddy root CA and its fingerprint).
  - **Finding:** the controller's 192.168.68.123 is a DHCP lease, not a reservation; the owner should reserve it in the router.
  - Other VPN clients need the same hosts entry, or a DNS push from the VPN.
- **Signed-in walkthrough on the real bench: NOT RUN.** Claude has no bench account and must not create one; the journey above ran on a copy of the bench data.
- **Physical operation, commissioning, merge:** none. PR #11 stays draft.

## Remaining

- CI result on `7075853`.
- The signed-in real-bench walkthrough by the owner.
- VPN DNS or hosts entries for other clients, and the DHCP reservation.

Ready for independent Codex re-review: **YES** (with CI pending as stated).
