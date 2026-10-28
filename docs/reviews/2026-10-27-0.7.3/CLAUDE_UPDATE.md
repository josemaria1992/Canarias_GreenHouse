# Claude implementation update — 0.7.7 (C075 review, owner decisions and answers, UX-01..06), C077 fixes, UX-07 and R402

Status: **C077-01..08, UX-07 and R402-01..06 are implemented on the branch** (see below); **deployed to the bench as 0.7.8** on the owner's request (27 Oct 2026, 23:45 CET). No AUTO, lease or output command was used. Earlier: 0.7.7 was implemented and deployed to the bench (0.7.6 plus the owner's pump-replacement answers). CI on the final commit had not finished when this was
written. Independent review pending. The previous update (0.7.5) is in this file's Git history.

## Source identity

- Branch: `feat/operator-upgrade-0.7`. Implementation base: `75b63d7` (the review documents).
- Final implementation commit (before this summary-only commit): `de3088a` (0.7.7). 0.7.6 was `7075853`.
- Release: `aquacrop-0.7.7.zip`.
  - sha256 `3553ac782ece331d3c5ad42a9b1532666df23c8585f420a066c32dbd4da676a3`.
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

## C077 findings (Codex review 0.7.7)

Author tests and Claude's browser checks only. Independent acceptance and the owner's real-bench check are **NOT RUN**.

| ID | Implementation | Author evidence | Remaining |
|---|---|---|---|
| C077-01 Stop all restarts a queued cycle | `ee0f263`. Stop all, a user stop (also of a paused operation's id), leaving AUTO (automatic remainders), a lost lease, and a deactivated or changed program now cancel a waiting remainder. Each cancellation is audited as `PROGRAM_RESUME_CANCELLED`. A genuine wait for the partner pump continues until it is free. | `tests/unit/test_recovery_c077.py`: 3 tests assert that no ON follows a cancellation for 60–120 s after the pump is free. Codex's `observe_stop_all` setup is now refused at its first step: C077-02 makes the busy-partner state it builds unreachable. | — |
| C077-02 sectors water simultaneously | `ee0f263`. A duty pair is one pump group. A program holds a token for each pump of its pair, and for each pair pump it switches, from its start until its verified stop. The group comes from configuration (duty pairs), never from equipment names. By hand, the second sector is refused with the reason. A scheduled start (weekly, repeat, condition, keep-between) waits its turn unclaimed (`PROGRAM_WAITING_TURN`): it never preempts and never opens a conflict choice, and it is MISSED with the reason if its window ends. A waiting remainder goes before other sectors. Programs outside the group are not serialized. | Tests: refused by hand; a named single pump counts as the group; two scheduled sectors in one window run one after the other, never both valves open at any 0.5 s step, the second starting after the first's verified stop; failure progression (healthy pump finishes, then the next sector on it); an unrelated valve program runs alongside. Codex's `observe_parallel` now gets `Refused: s2 waits its turn …`. | Owner check on the bench. The pause/resume path is now defensive only: no sector program can hold the partner. |
| C077-03 one OFF, then waiting | `ee0f263`. While the failed pump is not verified OFF, its held output gets OFF every 2 s (`REPLACE_OFF_RETRY_S`); the partner stays OFF until then. The RR-01 check (an output verified OFF earlier reads ON again) now also runs during the changeover. | Tests: ≥3 OFFs in 7.5 s with no ON; a dropped first OFF is sent again; the failed pump reading ON after the changeover fails the operation. Codex's probe: 4 OFF attempts in 8 s (was 1). Established stop regressions pass. | — |
| C077-04 litres skipped during changeover | `ee0f263`. During a changeover in a DELIVER step, the meter's readings keep being counted (`_account`), at 0 L while both pumps are stopped. The no-flow timer restarts when the partner runs. A genuinely lost meter still fails the operation with the volume it could not measure. | Changeovers of 0, 8 and 20 s: the same 50 L target finishes DONE with uncertain 0 L, and the replacement event links to the same operation. The volume at the changeover is the same whatever the delay, so nothing is counted for the stopped time. A lost meter fails with "flow reading lost". | Physical flow behaviour: NOT RUN. |
| C077-05 one-hour abandonment | `ee0f263`. `RESUME_GIVE_UP_S` is removed. Only the window's end, a stop, or a program change ends the wait. | Tests: waits more than an hour (no window), then finishes the remainder; a real weekly window ends it with "its window ended". The old 30 s-timeout test is replaced. | — |
| C077-06 empty design keeps old content | `967af7c`. New design builds a clean diagram: only view, title, size and grid stay. It asks first even over a drawing with only earlier-drawing content. A saved empty design shows as "empty design", never the Overview reference again. Equipment stays listed to place. | `tests/js/twin_empty_design.test.js` (3); API: an empty design saved after a legacy import is empty on reload, items are still offered, and the Overview and Equipment are unchanged. Browser on the bench copy: 0 symbols, 0 sketches, 0 placeholders, 41 Place buttons (`evidence-c077/06-empty-design.png`). | — |
| C077-07 meter off-grid; glyph not turned | `967af7c`. The meter snaps to the grid along its pipe, clear of the run's ends. Its inlet and outlet stay on the pipe's centre line: an off-grid imported pipe keeps its line, with no jog and no disconnect. The flow glyph rotates with the meter. | `tests/js/editor_meter.test.js` (+3): horizontal, reverse, vertical up and down; an off-grid pipe; the rendered glyph transform. Codex's probe now gives x=310 (on grid). Browser: the vertical fill meter's glyph is `rotate(90 12 12)` (`evidence-c077/07-vertical-meter.png`). | Capacity-sized tanks and moved ports are covered by the existing editor tests only. |
| C077-08 legacy import not simulation-ready | `b1db08f`. Kept without asking: named ends, each pipe's gate item and flow meter in series in drawn order, and the junctions shared single ports need. In-series equipment is turned and centred on its pipe. Asked once, as factual yes/no with the drawing's suggestion: equipment drawn on a pipe, an end touching a symbol or pipe, a sensor drawn by a pipe or tank. Only a yes connects. Open questions, dangling ends, missing equipment and graph errors are named blockers; the design is never "ready" while one is left. Answers are kept per earlier drawing, so a later Import from Overview applies them without asking. Connections with no room to be drawn are named; the router never draws them through equipment. | `tests/unit/test_reconcile_c077.py` (6) on the bench drawing fixture: missing tanks block by name; all 13 questions answered → 19/19 pipes, 45 edges, every flow meter in series, sensors associated, no graph error; a "no" is respected; an open question stays a blocker; deterministic. API: answers are kept, and a second import is complete with nothing asked again. Browser on a copy of the bench drawing, after creating the tanks, sectors and mains: 13 questions → Apply → "Connections complete: ready to simulate"; re-import asks nothing; 37 of 45 connections drawn, 8 named as not drawn yet (`evidence-c077/08b…`, `08c…`). | **Owner decisions:** the 13 answers for the real bench (the suggestions were accepted on the copy only; e.g. whether Return pump 2 is really in series). **Drawing:** 8 connections in the sector corner and the shared pump header need equipment moved apart before they can be drawn. **Real bench:** tank, sector and mains items must exist there. |

**Source and deployment:** `ee0f263`, `967af7c`, `b1db08f`. These are **not deployed**: the bench still runs `0.7.7+de3088ab7d.w3d2015dd` (main and Twin, as last verified by Codex). No hardware command, lease, AUTO activation or physical test was performed.

### Owner answers for the bench drawing import (27 Oct 2026, in chat)

These are the owner's facts about the physical bench. They are recorded here and **not applied**: the import needs the C077 code deployed first.

| Question | Owner's answer |
|---|---|
| `start:fill-main`, `fill-a`, `fill-b`, `fill-acid`: does each fill pipe join the mains pipe? | **Yes.** A pipe runs from mains to each tank with a valve in between. These are the explicit `v-fill-*` valves. |
| `inline:dose-a/b/acid:v-out-*`: is the outlet valve in series on the dosing pipe? | **No outlet valve** (owner, 28 Oct 2026). Each dosing line, after the fertiliser and acid tanks, has a pump and a flow meter (the explicit `tp-*` and `flow-tp-*`). The three valve items "Fertiliser A outlet", "Fertiliser B outlet" and "Acid outlet" have no physical valve: the owner retires them on Equipment, and the question then no longer arises. |
| `inline:return:rp2`: is Return pump 2 in series with Return pump 1? | **No.** They are parallel pumps inside the drainage tank, and the second one is there in case one fails. Added in `fc34fe6`: a pump drawn on a line that already has a pump is asked Yes (in series), No, or **Parallel spare**. Parallel spare gives it the same suction and the same downstream as its partner, never in series. A test confirms that with this answer the bench drawing is ready and that Return pumps 1 and 2 both take from the drainage tank and join before the return meter. |
| `tap:ec`, `tap:ph`: do they measure Main tank? | **Yes.** |
| `tap:temp`: does Main tank temperature measure Main tank? | **No.** There is no main tank temperature sensor (owner, 28 Oct 2026): the configured item "Main tank temperature" has no physical sensor, and the owner retires it on Equipment. |
| `tap:pres-s1`, `tap:pres-s2`: do they measure the sector pipes? | **Yes.** |

## R402 findings (Codex review of e40285f)

Author tests and Claude's browser checks only. Independent acceptance: **NOT RUN**.

| ID | Implementation | Author evidence | Remaining |
|---|---|---|---|
| R402-01 Stop all and a waiting turn | `0c44246`. Stop all and the asynchronous stop (lease loss) settle every start that waits for its pump group, each with a `PROGRAM_START_CANCELLED` record. A weekly start, or a repeat window's next cycle, is recorded CANCELLED for that window. A condition is disarmed until it clears and re-arms as usual. A keep-between program counts as just stopped, so its minimum off time applies. Future windows and the mode are unchanged. A condition waiting its turn now shows WAITING. | `tests/unit/test_recovery_c077.py`: weekly, repeat and condition, each with Stop all and with lease loss: no ON for 180 s, the occurrence CANCELLED, mode still AUTO. The condition re-arms only after clearing. Codex's probe `stopped_turn`: **0 ONs** after Stop all (was 2). | Whether leaving AUTO should also record waiting occurrences CANCELLED is unchanged (they are cleared; AUTO re-entry skips windows already begun). |
| R402-02 flow units | `3a20a28`. `FLOW_TO_L_MIN` covers L/min, L/h, m³/h (m3/h) and L/s. A litre step converts each reading from the meter's scaling unit, so targets, the volume cap and the minimum-flow threshold all see L/min. A litre meter reading another quantity is refused at start, and a litre step fails if one appears. The twin writes its L/min water into a meter in that meter's unit. | The same physical 10 L/min meter scaled in each unit delivers its 5 L target within 0.6 L of the water that actually flowed. A pressure "meter" is refused with no ON. Codex's L/h probe no longer finishes early (50 L at 10 L/min needs about 5 min). | Physical flow: NOT RUN. |
| R402-03 target met during a changeover | `0c44246`. During a pump replacement in a litre step, a met target (when no later RUN or DELIVER needs a pump) or the equipment's volume cap ends the operation through the established stop path, with every OFF retried until verified. The partner is **not** started (`PROGRAM_PUMP_REPLACEMENT_NOT_NEEDED`). A later watering step still brings the partner in. | Residual flow with dropped OFFs (the 11 L case): no standby ON, result DONE, all outputs OFF. The cap crossed by residual flow gives RUNTIME_GUARD with no standby ON. A later RUN step still replaces. Codex's probe: **0** additional ONs. | Water that flows while an unverified pump keeps running after the stop is not counted (the stop path does not meter); the alarm path is unchanged. |
| R402-04 Button polarity | `2fdad78`. Any new type (a built-in kind, Button or switch included) and any copy starts every digital input with no polarity, so the form asks and refuses to save until it is answered. Editing a stored type keeps its saved polarity. | JS model tests (Button, copy, stored edit). The API saves the answered polarity for every kind. Browser: a new Button has no polarity chosen and is refused until answered, then saved `inverted: true` as answered (`evidence-r402/03-…`). | — |
| R402-05 what a reading measures | `2fdad78`. For a new reading, choosing what it measures sets its key, label and unit, so it persists. A reading already in a saved version keeps its key and quantity, because items hold its channel and history; the choice is shown fixed with the reason. A unit that does not fit the quantity is named before saving. | JS model tests, and an API journey: selector → draft endpoint → reopened form gives pressure/bar. Browser: Flow rate changed to Pressure saves as `pressure`/bar and reopens as Pressure, bar, fixed (`04-…`). | — |
| R402-06 readiness after New design or edits | `8cbc344`. The reconcile view states whether the saved design is still exactly the import's result (the revisions it recorded). After a New design or any saved edit it is never ready: it names the change and the current graph's own errors, and keeps the answers for a deliberate re-import. | API: reconciled import → edit saved → "changed"; → New design saved → empty, not ready, answers kept. Browser 8/8 on a bench copy: remove a connection, save, reload → not ready; re-import → ready with nothing asked; New design, save, reload → no ready panel (`evidence-r402/01-…`, `02-…`). | — |

**Validation:**
- Full suite on the final code: 844 passed, 8 skipped, 0 failed (on 8cbc344); all JS test files pass.
- Codex's probes: re-run as described in the table.
- UX-07 browser demo re-run: 25/25.
- The demos revealed one harness slip, my seed's invalid pH/EC icon. A test-only focus step was needed because the app does not redraw while a text field has focus; a real click on a list moves the focus.

## UX-07 equipment types without code

Implementation: `6bf1c25`. The import's parallel-spare answer is `fc34fe6`. Author tests and Claude's browser demo only; independent acceptance: **NOT RUN**.

| Requirement | What was built | Evidence |
|---|---|---|
| No JSON, same model | New type and Edit open one guided form (`web/type-form.js`). It writes the existing `TypeBody` through the existing `POST /api/equipment/types/draft`; there is no second schema or store. The JSON textarea and the enum help text are removed. Type cards read in words. | `tests/integration/test_type_form_ux07.py`: for every kind, the form's output is accepted by the draft endpoint and the answered polarity is stored. Browser: no textarea on the page. |
| Choose what it is | 13 visual kinds plus "Copy an existing type". | `evidence-ux07/01-kind-picker.png` |
| Icons and symbol preview | A gallery of every icon (18), with an enlarged preview. Beside it is the actual diagram symbol with labelled connection points, drawn by `AqHydraulicEditor.symbolPreview`, which uses the renderer's own box, icon and port rules. The icon never adds ports or behaviour. A tank preview is labelled representative: capacity is entered on each tank item. | Browser: 18/18 icons chosen and previewed. The pump preview is 40×40 with Suction and Discharge, the same as the diagram renderer draws it (`02-…`, `04-tank-form.png`). |
| Relevant questions | Pump: control output, "running?", "fault?". Valve: open and closed position. Sensors and meters: what it measures, unit, signal (4–20 mA, 0–20 mA, 0–5 V, 1–5 V, each explained). Contact polarity starts unchosen and blocks saving until it is answered. | Browser checks UX07-2 and UX07-8 (`03-pump-form.png`, `05-…`). |
| Details and limits | Details with value type, unit, range and "must be filled in". Limits in minutes and seconds with explanations. Missing and duplicate names are reported where they are added. | UX07-5 |
| Add a feature | Reading, status signal, detail, control output, or "something the form cannot do". A second control output, and anything unsupported, is explained. The need is kept on the page with "not sent, and nobody has been notified", plus a Copy button. | UX07-5, UX07-9 (custom type with a temperature reading, a Door open signal and a Manufacturer detail; twin stays NOT MODELLED) |
| Summary, save, reopen | Summary in words; missing fields named per field. Reopening a draft gives the same summary and answers. A second edit changes only the edited field: simulation, operations and roles are kept. A no-change edit of the built-in Valve saves the same definition. | UX07-5, UX07-6 |
| State and permissions | Entries, selection and the open editor survive refreshes, keyed per editor (UX-06). Activation is unchanged: the types page stays administrator-only, and an operator's activation is refused by the server; the administrator activates. | UX07-4, UX07-11 |

Browser demo: a fresh bench-data copy (localhost, simulated, throwaway accounts), headless Chromium, real clicks and typing, **25/25** (`evidence-ux07/checks.json`, script `check_ux07.cjs`). The demo found and fixed three defects:
- the Add buttons stayed disabled after typing;
- an old missing-field message stayed after the field was fixed;
- the limits fields were laid out in a column.

**Unsupported, and explained in the form:** a second control output (close, reverse, a second speed); any behaviour the engine has no logic for; twin models beyond the existing ones (NOT MODELLED stays). A CLOSED position signal is shown with the equipment, but programs do not use it.

**Owner decisions, 28 Oct 2026, implemented in `9e0ef90`:**
- **Requests go to the operators, who review them and approve or reject them.**
  - "Send to the operators for review" in Add a feature records the request; it is kept on the page only until it is sent.
  - A Requests card on Equipment types lists every request for operators and administrators, with Approve, and Reject (a reason is required). One decision per request, with the reviewer and note shown.
  - Requests are audited events (`EQUIPMENT_TYPE_REQUEST`, `EQUIPMENT_TYPE_REQUEST_REVIEWED`), so there is no new table and the schema stays 7. Approving changes no type, item, program or output.
  - Not decided, so not restricted: whether a requester may approve their own request.
- **Operators open Equipment types.** It is in the operator menu and role text, for drafting with the guided form and for requests. Activating, retiring and "Make available" stay with administrators: the server refuses an operator's activation, and the button is disabled for them.
- Evidence:
  - `tests/integration/test_type_requests_owner.py`: send; viewer refused; listing for operators and administrators; reject without a reason refused; one decision only; unknown request 404; types and items unchanged; operator activation refused. `test_rbac_070.py` covers the operator menu.
  - Browser on a bench copy (`evidence-owner28/`), 5/5: an operator opens the page, sends a request from the form, then approves it with a note; activation is refused.
  - The UX-07 demo was re-run with the new behaviour: 25/25.

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

- **UX-07 (author tests):** `tests/js/type_form.test.js` (6), `tests/integration/test_type_form_ux07.py`, the equipment-type and import suites, all 8 JS test files; browser 25/25.
- **C077 (author tests, not acceptance):** full suite on `b1db08f`: 824 Python tests passed, 0 failed; all 8 `tests/js/*.test.js` passed. Browser checks on a bench-data copy, 7/7 (`evidence-c077/checks.json`). Independent acceptance: NOT RUN.

## Deployment and access

- **0.7.8 (owner's request, 27 Oct 2026):**
  - Release `aquacrop-0.7.8.zip` from `ea9301f`, sha256 `30155215c00b8f4c95205df0be1384ab0facf629a595fe8340d4861b4b7fd14a`, build `0.7.8+ea9301f265.wd95a6186`.
  - Before the upgrade: full suite on `8cbc344` (844 passed, 8 skipped, 0 failed), every JS test passed, `verify_release.py` 28/28. On the CM4, Python 3.11 compiled the wheel's 118 modules, and `preflight311.sh` passed (imports, refused Preview/Save, programs, block prerequisites, project export).
  - Upgrade 0.7.7 → 0.7.8 with `deploy/aquacrop-cm4.py upgrade`. The pre-upgrade backup `/var/lib/aquacrop/backups/pre-upgrade/aquacrop-2026-10-27T224456.937879Z.sql.gz` was verified. 0.7.7 stays installed for rollback (`/opt/aquacrop/previous`).
  - After the upgrade: main, twin and Caddy are active; health through Caddy reports MAIN 0.7.8 with storage and acquisition OK; TWIN reports 0.7.8; no service errors were logged.
  - Not done: a signed-in walkthrough on the real bench (owner) and any physical test. The owner's recorded import answers were **not** applied.
  - Earlier deployments are below.

- **Bench:** 0.7.5 → 0.7.6 at 16:25 CET, then 0.7.7 at 17:29 CET. Main and Twin both run `0.7.7+de3088ab7d.w3d2015dd`. Before 0.7.7: full suite passed again, release 28/28, Python 3.11 preflight passed.
- **Rollback:** pre-upgrade backups verified (`…T152537.684992Z.sql.gz` before 0.7.6, `…T162935.191682Z.sql.gz` before 0.7.7); 0.7.6 stays installed.
- **Data:** accepted drawing sha `7ef8c51b…` unchanged; schema 7; 35 items; no leases; no commands.
- **Access record:** written privately outside Git (`%LOCALAPPDATA%\AquaCrop\commissioning\ACCESS-20261027.md`: hostname, address, Caddy root CA and its fingerprint).
  - **Finding:** the controller's 192.168.68.123 is a DHCP lease, not a reservation; the owner should reserve it in the router.
  - Other VPN clients need the same hosts entry, or a DNS push from the VPN.
- **Signed-in walkthrough on the real bench: NOT RUN.** Claude has no bench account and must not create one; the journey above ran on a copy of the bench data.
- **Physical operation, commissioning, merge:** none. PR #11 stays draft.

## Remaining

- **C077:** deployed in 0.7.8. The owner answered the dosing valves and the temperature sensor (28 Oct): the three outlet valve items and "Main tank temperature" are to be retired on Equipment by the owner. Return pump 2 can now be answered as a parallel spare (`fc34fe6`). Tank, sector and mains items are needed on the real bench. 8 drawn connections need equipment moved apart.
- **UX-07:** deployed in 0.7.8; the owner's request routing and operator access (`9e0ef90`) are **not deployed yet**.

- CI result on `7075853`.
- The signed-in real-bench walkthrough by the owner.
- VPN DNS or hosts entries for other clients, and the DHCP reservation.

Ready for independent Codex re-review of R402-01..06 (and the C077/UX-07 rows they amend): **YES**, apart from the open owner items above.
