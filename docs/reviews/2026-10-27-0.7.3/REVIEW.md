# Review of AquaCrop 0.7.3 — 27 October 2026

**Result: partially implemented; not ready to call the approved update complete.** Review pinned to `f91e34da58f55f4fa7fe725a532969439dbe6525`. Fetched PR #11 head `3cc3c34f9943056948ec2098abeddc6f8170294a`; its only change after that baseline is `docs/RESULTS-0.7.1.md`. Review used an archived clean source tree, preserving the unrelated working checkout's 36 existing changes. No application source was edited, merged or deployed.

## Findings

| Priority | Finding and evidence | Required correction |
|---|---|---|
| P1 | Older Overview import is lossy. `topology/migrate.py:47–134` omits unresolved symbols, substitutes fixed geometry, discards annotations and creates routes without points. Independent reproduction: six symbols → three, missing source/tank/sector, retained pipe route → empty. The latest results ledger says the bench still uses its accepted legacy drawing. | Preserve the entire visual drawing while explicitly resolving hydraulic mappings; share presentation across views. Never guess connectivity or discard unresolved visual content. |
| P2 | Preview returns raw regex messages for missing selections. `api/programs.py:142–152`; reproduced exact owner message for empty block, condition item and condition role. Frontend condition rows begin with empty IDs. | Friendly, field-specific, complete validation; retain form entries and valid public IDs. The owner's exact failing field cannot be known without that request's payload; three concrete triggers are confirmed. |
| P2 | Tank size ignores capacity. `topology/definitions.py:46`, `diagram.py:96–100`. The symbol is always 100 × 120; `equipment/views.py:28–39` does not provide capacity to the drawing item metadata. | Shared capacity-aware geometry, preview and route validation; safe resizing of connected/contained equipment. |
| P2 | Tanks supported in code can still be absent from the palette. `web/hydraulic-editor.js:487–499` lists only existing unplaced items. Legacy tank symbols often refer to sensors, and migration lists tanks to create instead of preserving them in the editable drawing. | Repair legacy mapping and Equipment → Twin refresh; show already-placed versus missing status. Actual signed-in bench inventory was not independently inspected. |
| P2 | Sensor placement does not associate it with a tank. `web/hydraulic-editor.js:325–329,516–522,620–656`: association exists through a dropdown, but drag completion only records a moved symbol. | Drop-to-associate using existing non-water `measures`, persisted containment, tank movement, undo and ambiguity handling. |
| P2 | Approved configurable block sensor safety conditions are absent. `blocks/definitions.py:70–85`, `web/blocks.js:159–169`: maximum runtime and startup/stop steps exist, but no block-level sensor permissives/guards. Program-level conditions remain available. | Implement block conditions and apply them to all programs over that block; preserve the existing safety semantics. |
| P3 | Requested page simplifications remain. `web/index.html:44–51` separates Equipment types from Equipment; `web/blocks.js:189,201` still has the drawing instruction and both unwanted shortcuts. | Adjacent navigation labels and removal of those Blocks links/instruction. |

## What passed, and what that proves

- **58 existing regression checks passed independently:** 36 across ID APIs, program forms, windows, legacy migration, Twin design, previous journal fixes and stop plans; 10 across ID generation, module requests, commissioning permissions and Blocks; 1 build/partner-identity test; 11 JavaScript routing/alignment tests.
- **Five independent probes completed:** three reproduce the poor Preview messages; one reproduces legacy visual loss; one confirms exact canonical graph/diagram equality on the explicit-format import, including positions, route points/manual mode/pin, grid, label and annotation. The latter observed no fake-bus writes. Four probes passed initially; the fifth needed a missing fake-bus fixture import corrected in the external probe harness before it passed. No product fix was needed for that harness correction.
- Thus the modern saved-diagram API path does preserve geometry. This does not validate the old bench drawing migration or identical browser rendering.
- Editable public IDs and stable references, block runtime safety, window boundaries/handoffs, module permissions/requests, and the prior reviewed stop/journal cases have targeted passing evidence. This is not a full-suite rerun or universal safety assurance.

## CI, release and deployment limits

At the refreshed PR check snapshot: 18 successes, one failure, 17 still running. The failed UI job reports **81 passed, 2 failed**: it waits for removed `#overview-sectors`; it also expects drive diagnostics visible on Overview, while the source retains them inside collapsed Details. These journeys need updating with equivalent functional coverage. See the linked job in EVIDENCE.md.

Claude reports 758 passed / 8 skipped, 28 release checks passed, release SHA-256 `26d6580814d47026fa853f5ce8b69f8212e676f220dcba60b3a473155ba96c3c`, and matching deployed 0.7.3 main/Twin. Those figures and artifact checksum were not independently reproduced here. Both read-only health requests to `aquacrop-bench.local` timed out in this review session; that does not establish a bench outage. No signed-in bench walkthrough or physical test was performed.

The latest branch ledger itself leaves project exchange, drainage alternation, labelled nutrient preparation, historical run reconstruction and run-annotated charts open. The previous full upgrade scope must not be labelled finished. The new tank-capacity, drop-association and navigation details are the owner's latest corrections; they should be implemented as such rather than claimed as previously tested features.

Use **CLAUDE_FIX_INSTRUCTIONS.md** as the concise implementation handoff. The older approved package remains the reference for unaffected requirements; it need not be resent or reread in full.
