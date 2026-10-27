# AquaCrop 0.7.3 — owner corrections and review feedback

Review baseline: `f91e34da58f55f4fa7fe725a532969439dbe6525`, PR #11; `3cc3c34` only updated the results ledger. At publication, the branch has advanced to `fdf40b8`, which updates the old UI checks; see section 6. This brief supplements the approved 26 October package; the owner's 27 October corrections below take precedence. Keep unrelated agreed work and safety requirements intact. Commit your implementation summary to `CLAUDE_UPDATE.md` beside this file so Codex can review the next update directly from Git.

Implement these corrections in the existing branch. Keep the PR draft. Do not invent wiring, capacities, calibration or operating thresholds. Drawing changes never authorize physical operation.

## 1. Preview must explain what needs fixing

Confirmed in `api/programs.py:142–152`: the first Pydantic message is exposed verbatim. Empty `block_id`, an unfinished condition's `item`, and its empty `role` all produce the owner's exact regex error. This is not evidence that uppercase public IDs are the cause: the UI supplies stable internal block/item keys.

Translate expected validation failures into actionable messages and field paths, on Preview and Save, using one shared formatter. Examples: “Choose a validated block on Blocks”; “Start condition 1: choose a reading”; “Enter minutes per cycle”; “Add a watering window and its waiting time”; “Set the block's maximum runtime on Blocks.” Show all relevant missing inputs, focus/highlight the first, and preserve entered values. Make nested condition errors visible beside that row. Never ignore an unfinished safety condition to make Preview pass. Retain valid case-preserving public IDs and stable internal keys; do not solve this by relaxing internal-key validation.

Also handle a block revised while the form is open: offer an explicit refresh/review of its latest validated version without discarding unrelated form entries. Show the version being used; do not silently retain an invisible obsolete selection.

## 2. Import the owner's actual drawing faithfully

The explicit graph/diagram path in `api/twin_app.py:635–668` passed an independent exact graph/diagram comparison. Preserve it. The failure is reproducible in `topology/migrate.py`: it skips symbols without new equipment items, substitutes fixed-size symbols, discards annotations and supplies empty routes instead of copying existing pipe points. A six-symbol fixture became three symbols; its retained pipe lost its route. The real bench results ledger says its accepted drawing is still the legacy format.

Provide one consistent drawing representation/renderer for Overview, Twin and the read-only Blocks picture. Import must retain all components, names, visual symbol identity, sizes, positions, annotations and existing valid orthogonal routes. Preserve manual routes, pins, anchors, orientation and stable equipment references. Do not replace the owner's layout with an automatically arranged set of icons.

Separate visual preservation from hydraulic interpretation. Keep unresolved legacy symbols/routes visible and clearly marked while their mappings are reviewed; do not drop them or infer a hydraulic junction from touching geometry. Match existing equipment where unambiguous; expose unresolved mappings and any required equipment creation through the dedicated Equipment page. Preserve the original backup. If an old route violates the orthogonal rule, show the original as reference and preview the necessary correction; do not silently claim both exact geometry and a corrected route.

Reopen the saved Twin draft reliably. If no draft exists, explain that state and show the current Overview as the import reference, rather than an unexplained blank editor. Import must never overwrite an unexported draft without the existing explicit replacement confirmation. Import alone must not resize/re-layout the drawing. Capacity edits are a separate intentional change.

## 3. Tanks must be available and capacity must affect their size

`hydraulic-editor.js:487–499` already supports tanks, but only shows existing, unplaced items of each kind. A legacy tank drawn via a level sensor is not a Tank equipment item. Fix that migration/data journey, rather than merely adding another hardcoded palette category.

Keep equipment creation/configuration on Equipment. Provide a clear Tank type there and make newly created/imported tanks available in Twin without a destructive re-import of the entire draft. Explain whether tanks are already placed or genuinely absent; hide neither condition behind an empty category. Do not create a second equipment library in Blocks or Twin.

Replace the fixed tank box (`topology/definitions.py:46`, `diagram.py:96`) with shared capacity-aware presentation geometry. A 5,000 L tank must be visibly smaller than a 20,000 L tank, with readable capacity and space for its level sensor. Preview changes when capacity is selected; save them consistently across all three views. This is schematic sizing, not an assertion of physical dimensions. Preserve topology, sensor associations and valid pipe attachment when size changes; reroute affected ends orthogonally, retaining unaffected manual routes. Update browser geometry, backend anchor/route validation and routing obstacles together; CSS-only scaling is insufficient. Keep legacy dimensions intact on import until an intentional sizing edit is made.

## 4. Associate a level sensor by placing it in/on its tank

The graph already has non-hydraulic `measures` associations and the editor has a manual Measures dropdown. Extend that existing model. Dropping a compatible level sensor in/on a tank should visibly associate it with that tank, without creating a water edge or pipe. Show the tank highlight before release and the association afterwards. Persist both association and placement, and carry the sensor with its tank when the tank moves. Keep the manual association control accessible.

Do not associate unrelated sensor types or choose silently between overlapping tanks. Provide an explicit way to reassign/detach; ambiguous drops must explain the choice. Contained symbols must remain selectable and must not be rejected as invalid equipment overlap. Support undo/redo and the same result through import/export/reload. Do not infer runtime hydraulic connections from visual containment. Existing sensor units/scaling and UNKNOWN behavior still apply.

## 5. Simplify the requested navigation and Blocks page

- Move Equipment types beside Equipment in navigation (`web/index.html:44–51`), retaining role restrictions.
- Remove the “Equipment” and “Design the drawing in Digital Twin” shortcuts from Blocks (`web/blocks.js:201`) and the redundant drawing-design instruction. Keep the read-only orientation diagram, members and safety configuration.
- The approved per-block safety conditions remain incomplete: `BlockBody.safety` currently has only maximum runtime, with startup/stop plans elsewhere. Provide configurable block start permissives/stop conditions using the existing condition model, applied to every program using that block alongside its program-specific conditions. Unknown readings must not permit starting. Do not confuse stop-plan steps with sensor conditions or invent thresholds. Preserve old block digests/versions unless an explicit edit occurs; invalidate/review affected compiled programs and proof as required.

## 6. Finish the acceptance work

The reviewed GitHub UI job failed with 81 passed / 2 failed: it waited for removed `#overview-sectors`, and expected the drive table to be visible while the new page puts it inside collapsed Details. Since that review, `fdf40b8` has updated those journeys to follow the diagram and open Details. Verify that change and the resulting CI; do not repeat completed work. Retain equivalent coverage of sector readings/history and drive status/UNKNOWN/read-only behavior. Do not restore the removed panels or delete checks just to obtain green CI.

Add focused regressions for sections 1–5, including a copy of the actual accepted legacy bench drawing, not only newly constructed explicit graphs. Compare canonical diagram data AND rendered screenshots across Overview → Twin → Overview. Test the complete owner journey: Tank creation/capacity → placement → sensor drop → connection → save/reopen → import/export/review/apply. Use fake services; no outputs need to operate. An expected-error reproduction passing is evidence of a defect, not product acceptance.

Then run the full required checks once, build/verify the release once, and deploy the verified main/Twin pair to the existing canonical bench address using the approved backup/rollback procedure. Preserve owner data and the Overview drawing. Confirm both running build IDs and complete the signed-in bench walkthrough; report anything that cannot be checked. Keep `https://aquacrop-bench.local/` as the single owner entry point. Do not merge or operate equipment.

## Token-efficient working agreement

Read this file first, then only the relevant code/tests and unresolved approved requirements. Do not reread/reprint every historical attachment or resend the old package. Reuse the existing router, graph, condition and ID layers. Make one coherent patch; run affected tests while iterating and the full required suite once after the patch stabilizes. Investigate new failures rather than repeatedly running an unchanged 17-minute suite. Report changed behavior, exact commit/build, test counts, deployment evidence and remaining gaps concisely; attach logs instead of pasting them. Ask only concrete unresolved product/engineering questions.

Feedback: the identity, ID, window, module-permission and prior stop/journal fixes have useful regression coverage. The completion claims need to distinguish that backend coverage from the owner's existing-data and visual workflows. The requested import behavior was not fulfilled by listing missing legacy components and rerouting their pipes. Do not mark the package complete while these corrections or the explicitly deferred project import, drainage alternation, nutrient simulation and history/chart requirements remain open.
