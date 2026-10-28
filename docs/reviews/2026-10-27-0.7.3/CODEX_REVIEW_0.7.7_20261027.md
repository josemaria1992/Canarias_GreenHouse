# Codex review — 0.7.7 and latest owner feedback

**Result: earlier corrections have landed, but this update does not yet meet the agreed behaviour. Fix the recovery defects below before claiming completion.**

Reviewed implementation `de3088ab7d`, summary `c32121f`, and subsequent CI-only fix `cc93b37d253f682e9b78f09527b6b725e5a81eec`, on `feat/operator-upgrade-0.7`, against previous feedback `75b63d7`. The last delta changes only the UI check's sub-millisecond timestamp comparison. Application source was reviewed and tested in an isolated snapshot; no product files, hardware state, deployment or merge were changed. The primary checkout's existing changes were preserved.

## Read this first: actual owner decisions

- The owner confirmed either pump can serve either sector, **sectors must never water simultaneously**, and an interrupted sector must not preempt another operation using the healthy pump.
- The owner **did not approve and now explicitly rejects the one-hour abandonment limit**. Do not substitute another invented timeout. The approved hard window finish and existing safety limits remain.
- Keep the approved mid-cycle sequence: immediate alarm/notification, failed pump OFF and verified, same sector valve stays open, healthy pump finishes the remainder, then the next sector follows its configured sequence. No physical operation is authorized by this review.
- Latest Twin request: import Overview's complete diagram, pipes and connections so it is ready for simulation; an empty design must actually look empty; flowmeters must sit in series, aligned with the pipe and grid like valves.
- **Empty-design scope confirmed by the owner after this review:** clear the Twin diagram, pipes and connections only. Keep everything else, including the Equipment library, real Overview, programs and history. Equipment must remain available in the side palette to start the new diagram; no equipment needs to be recreated.

## Findings and minimum corrections

### C077-01 — P1: Stop all can start a queued cycle

`src/aquacrop/programs/engine.py:438` stops current operations but leaves `_resume`; `:1388` automatically starts those pending remainders in MANUAL as well as AUTO.

Independent fake-I/O reproduction using Claude's busy-partner scenario: pause Sector 1 waiting for Pump 2, press Stop all, advance 15 seconds. Sector 1 resumes and sends **two new ON commands** (valve and pump). Stop all has therefore restarted watering.

Cancel pending remainders atomically on an explicit stop-all and audit the cancellation. Check related user-stop, mode/recovery, deactivation and lease-loss paths so a stop cannot leave a hidden automatic restart. Keep genuine temporary resource waiting distinct from cancellation. Add regressions asserting no subsequent ON after cancellation. Preserve existing OFF verification.

### C077-02 — P1: both sectors can water simultaneously

`programs/engine.py:719–783` locks only actuators used by the chosen action. Lead alternation (`:807`) chooses Pump 2 for the second sector without reserving a shared exclusion resource for the agreed sequential installation.

Independent reproduction: start Sector 1 with duty pair A/B, advance 3 seconds, start Sector 2 with the same pair, advance 3 seconds. **Both pumps and both sector valves are ON.** Claude's busy-partner test also constructs simultaneous sector watering, so its passing result does not establish the promised exclusivity.

Enforce sequential access on the server using the configured shared installation/pump-group relationship, including manual, scheduled and resumed starts. Queue/refuse the next sector with a clear reason until the active sector has completed verified shutdown; never preempt it. Do not hard-code the owner's equipment IDs or serialize unrelated installations. Test normal healthy operation as well as failure recovery. The current “then Sector 2” test manually calls `run_now` for Sector 2; it does not prove scheduled progression.

### C077-03 — P1: takeover sends failed-pump OFF once, then waits

`programs/engine.py:1353–1355` sends one OFF. `_replacing` at `:1419–1433` only reads OFF status until its 30-second timeout; it does not retry the held output. This violates AGENTS invariant 2: every stop sends OFF until verified.

Independent probe holds verification unavailable for eight seconds: **one OFF attempt** is recorded. A dropped/unconfirmed first command receives no retry during that interval.

Use the existing verified-stop obligation/retry mechanism with the held channel. Keep replacement ON blocked until verified. Also keep RR-01 monitoring of previously stopped outputs during STARTING: `_advance:1266` returns into replacement before its stopped-output check at `:1277`. Add dropped-OFF, delayed-readback and failed-pump-returns-ON tests; run the established stop regression suite.

### C077-04 — P1: litre recovery skips meter samples during changeover

`programs/engine.py:1266–1268` bypasses `_deliver` while replacing a pump. `_deliver:1497–1504` then integrates between the last pre-changeover and first post-changeover sample, or declares a missing-data gap, even when the meter continued producing valid readings.

Independent fake meter supplies fresh samples throughout, at 60 L/min when either pump runs and zero when both are stopped. Delayed OFF verification causes an eight-second changeover. Replacement succeeds, then the 50 L operation **fails with “9 s between flow readings; 9.00 L not measured”**. The engine simply did not consume the available intermediate readings. A shorter gap can interpolate across stopped time and overcount volume.

Continue valid acquisition/accounting through takeover or consume the actual intervening samples; never invent water delivered during downtime or hide genuinely uncertain volume. Test both short and longer changeovers, zero flow during the stop, genuine meter loss and busy-partner resume. Assert the aggregate original cycle's remaining litre target and audit links, not just that the replacement pump starts. Claude explicitly reported no dedicated litre-resume test.

### C077-05 — P2: an unapproved one-hour rule abandons unfinished irrigation

`programs/engine.py:1358,1392–1397` abandons a waiting remainder after 3600 seconds even with its window open. Remove this unapproved policy, retaining approved window/safety termination and explicit cancellation. The test named `test_a_waiting_rest_is_abandoned_when_its_window_ends_and_says_so` actually shortens this timeout to 30 seconds and never supplies a window: replace it with a real window-end regression and a wait-longer-than-one-hour regression with the window still open.

### C077-06 — P2: Start an empty design retains old drawing contents

`web/twin-graph.js:88–94` copies the old diagram and clears only `symbols` and `routes`. `sketches`, `placeholders`, `texts` and `shapes` survive, and `hydraulic-editor.js:495–518` still renders them.

Independent execution of the actual function shows an empty graph retaining the old pipe sketch, tank placeholder, box and label. Reset **all drawing content**, plus graph nodes/edges/measurement associations, using a clean diagram model. Retain intentional workspace settings and all underlying equipment/configuration. The owner has confirmed that everything else remains, and equipment must be available at the side for placing into the new diagram. Verify immediate blank appearance, a populated equipment palette, save/reload and no reference-overlay resurrection; preserve Equipment, real Overview, programs and history. Removing a symbol must never delete its equipment record.

### C077-07 — P2: inline meter insertion ignores grid snapping

`web/hydraulic-editor.js:310–316` puts the meter at the exact segment midpoint without using `snapG`. In a 10 px grid with a pump at (100,200) and valve at (510,200), insertion puts the meter at **(305,200)** although both original items are on-grid. This independently reproduces the owner's off-grid observation.

Snap placement along the segment while keeping the inlet/outlet precisely on its centreline, adequate clearance and orthogonal routes. Where an imported pipe itself is off-grid, alignment must not produce a disconnect or surprise jog. Cover horizontal/reverse/vertical insertions, rotations, moved ports, capacity-sized tanks and save/reload. Check the visible glyph too: `iconAt:603` currently receives no rotation, so the directional flow glyph stays horizontal for a vertical meter. Demonstrate the rendered result in a browser; a coordinate-only fake DOM test is insufficient for visual acceptance.

### C077-08 — P2 / unfinished owner journey: legacy import is not simulation-ready

`api/twin_app.py:679–723` copies an approved explicit graph/drawing, including its edges. That path passes the existing round-trip tests. But the bench still uses the earlier layout: `topology/migrate.py:140–166` leaves ambiguous/missing endpoints and inline equipment as sketches; mapping tank names alone does not complete the hydraulic graph. Claude's own walkthrough still reports 13 earlier pipes and seven meters needing completion. The fixture preserves 15 sketches and eight placeholders. Drawing preservation therefore does **not** establish a usable simulation network.

Deliver the owner's import journey using the actual bench drawing in a safe copy. Automatically retain all explicit known connections, pipe geometry, port choices, junctions, meters and sensor associations. For genuinely ambiguous legacy relationships, present one concise reconciliation preview, request only the missing factual choices and persist the accepted mapping. Do not force the owner to redraw known pipes, silently infer a crossing/join, bypass a meter, or merely recolour dashed sketches as connected. Once resolved and approved, subsequent Overview → Twin imports must be complete and repeatable without resolving them again. Show named blockers if a design cannot simulate; never label unresolved import “ready”.

## What is verified / limits

| Area | Independent outcome |
|---|---|
| C075-01..07 | Corresponding fixes present; focused proof, duty form, exchange, target-state, historical binding, refused tank-move and malformed-record checks pass. |
| Superseded block safety | Comparison and live-start/activation hooks present; focused tests pass. Full signed-in review/validate/retest/restart journey was not independently run. |
| UX-01..06 | Prerequisite guidance, Tank type availability, retained type editor, insertion and import checklist are implemented. Related tests pass. The owner's new Twin observations remain valid as above; authenticated real-bench walkthrough is NOT RUN. |
| Python | **43 focused tests PASS**, Python 3.12.14: replacement, block safety, types, prerequisites, exchange, history, duty proof, migration, diagram, proof context, program errors and fake-app design round trips. Three JUnit files in evidence. These are not physical hardware tests despite the repository folder name. |
| JavaScript | **25 tests PASS** across all six `tests/js/*.test.js` files. Additional observational probes reproduce the defects above; they deliberately report faulty behaviour and are not acceptance tests. |
| CI | Latest head `cc93b37` has successful UI, matrix, wheel and release/staging checks, with duplicate cancelled/skipped checks. The earlier O-04 failure was sub-millisecond truncation; the reviewed final fix addresses that assertion. |
| Raspberry / VPN | Independently GET-verified main and Twin health: **0.7.7**, identical build **0.7.7+de3088ab7d.w3d2015dd**. Open **https://aquacrop-bench.local/** over the VPN. The 127.0.0.1:8040 tab is a service on this PC, not the Pi. Health/build identity is not functional acceptance. |
| Hardware / real-browser acceptance | **NOT RUN**: physical failover, live channel operation, signed-in bench workflow and manual drag/render acceptance. No hardware commands, AUTO activation, lease or dosing in this review. |

## Efficient implementation handoff

Read this file and the affected source/tests first; historical packages need not be reread. Fix C077-01..05 together in the existing engine and add corrected-behaviour regressions; then C077-06..08 in the existing Twin journey. Avoid duplicate abstractions and new dashboards. Keep the real Overview drawing as the starting point.

Evidence probes are in `evidence-0.7.7/`; run from the repository root with Python source/test imports available. Convert each demonstrated failure into a proper regression asserting the corrected result. After focused tests pass, run the required suite once on the final implementation, then a browser journey on a copied bench dataset. Re-run only affected checks after further changes, and verify final CI/release identity before reporting deployment. Do not operate physical equipment to prove software fixes.

Update the existing **CLAUDE_UPDATE.md** with one concise row per C077 ID: implementation commit, exact test/browser evidence, remaining blockers and actual main/Twin deployment build. Separate author tests from owner/independent acceptance. Correct the summary's contradictory release filename. Do not say “all done” while the bench import still needs unresolved hydraulic decisions. PR #11 remains draft/unmerged; this feedback authorizes no merge or hardware operation.
