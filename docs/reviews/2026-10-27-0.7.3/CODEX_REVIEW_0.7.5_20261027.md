# Codex follow-up: 0.7.5 implementation

**Result: changes are substantial, but “all done” and “all five open features built” are not yet supported.**

Reviewed `7a70ee6026eef2a53214b127087f4d0f8e6bb7b2` on `feat/operator-upgrade-0.7`, against handoff `e4a517a`. The release code is `caf0f88`; the changes from there through `7a70ee6` are documentation only. Read Claude's committed summary and the changed implementation; ran targeted independent checks against an archived source snapshot. This review changes no application code, operating policy, deployment or hardware state. The unrelated working checkout's 36 existing changes were preserved.

## Findings requiring correction

### C075-01 — P1: standby pump is outside the Twin proof's equipment scope

`src/aquacrop/twin/changes.py:49–64` collects equipment only from fields named `item`, `meter` or `sensor`. The new `duty: [pump-a, pump-b]` metadata is not included. `programs/proof.py:24` uses that incomplete set, whereas `programs/engine.py:789–825` can switch the operation to pump-b.

Independent reproduction, including an explicit topology containing both pumps: the context names only `pump-a`. Rebinding pump-b's start channel from `digital_A.DO3` to `digital_A.DO5` produces **the same context digest**. The requirement that changed equipment/channel context invalidate accepted Twin proof therefore does not cover the standby.

Fix the shared dependency extraction to include both duty pumps, and audit its proof/export/validation consumers. Both potential pumps' relevant settings, channels, feedback and faults must participate in readiness/proof. Add a regression that changes only the standby binding and requires stale proof. Previously accepted duty-program results must not remain valid under the corrected context. Do not claim that a physical output was exercised in this review; this was a context calculation using fake item views.

### C075-02 — P2: the Blocks UI cannot configure a new duty pair

`web/blocks.js:217–228` captures `cur = ["", ""]` when no duty pair exists. Each dropdown change copies that same original array, and writes `body.duty` only when both entries exist. Selecting the first pump discards its partial selection; selecting the second still sees the original empty first entry. Both displayed selections can be filled while the model remains `duty: null`.

An independent execution of the actual `dutyPicker` handlers reproduces this. Preserve partial selection in form state, derive updates from current state, and validate completeness on save. Add a real UI/form regression that selects two previously unset pumps, saves, reopens and verifies the pair and alternation. The existing engine-only alternation test does not exercise this journey.

### C075-03 — P2: whole-project export loses a legacy Overview drawing

`exchange.py:42–56` exports only `blocks.topology()` and `blocks.diagram("installation")`; it never includes the accepted legacy layout when no explicit installation graph exists. A fake installation with **47 visible legacy drawing elements** exported an empty graph and **zero drawing symbols/placeholders**. That is the same format Claude's deployment notes say the accepted bench drawing still uses.

Include the actual accepted drawing in project export, either preserving its format with source identity or using the faithful converter without applying anything. Round-trip the actual accepted legacy drawing through JSON and YAML, checking all visual elements and routes. Do not require an unrelated live diagram conversion just to export the current installation accurately.

There is also no completed user-facing continuation for imported drawings: `exchange.apply` writes `state_dir/imports/drawing-<digest>.json`, and Settings reports a proposal count. A repository search found no reader/review/apply route for those saved proposals. Provide a discoverable path into the existing reviewed Twin drawing workflow, retaining source data and conflicts. Item proposals likewise need usable record details and a clear continuation through Equipment, rather than requiring the owner to recreate an imported project from memory. Do not mark IMPORT-01 complete merely because a proposal file exists.

### C075-04 — P2: import approval is not bound to the previewed target state

`exchange.py:218` hashes only the imported document. `api/app.py:928–934` compares that hash, then `exchange.apply` recomputes actions against the installation's **current** state. It does not compare the actions or target versions the owner reviewed.

Independent reproduction: preview says block s1 is `SAME`, so no block write is proposed. Another edit validates s1 v2. Applying the unchanged document with the old preview digest returns 200 and writes an unreviewed s1 v3 draft containing the older description. No live activation occurred, but the promise to apply only the previewed changes is broken.

Bind approval to both document identity and target revisions/actions, with concurrency protection during the write. Refuse stale previews and require a fresh preview. Add the above concurrent-edit regression; preserve all existing drafts and validated versions.

### C075-05 — P2: historical configuration can select changes made after the run

`historian/runs.py:108–149` determines bindings and item versions using timestamps alone (`utc <= started_utc`). Multiple writes can share an instant. A run-start event followed by a rebind at the same timestamp is reconstructed using the later binding.

Independent deterministic test: pump start was bound to `relay_B.DO7` when the run-start event was recorded. A subsequent rebind to `DO6` shared its timestamp. The run detail reported **DO6 as the historical channel** and **no change since**, so its chart would select the wrong channel. The implementation test's added `sleep(0.05)` avoids the case instead of fixing ordering.

Capture the effective immutable run context at start, or retain a durable record-order/revision watermark sufficient to reconstruct it exactly; use the held configuration where appropriate. Include the selected duty lead and actual executed branch. Test tied timestamps, later rebinding and restart without sleeps. Historical context and chart selection must share the corrected identity.

### C075-06 — P2: rejected tank movement leaves its sensor displaced

`web/hydraulic-editor.js:337–342` translates carried sensors before routing the tank's new position. If routing fails, it restores the tank's x/y but not the sensors' positions.

Independent editor reproduction: tank x=100, associated sensor x=120. A rejected move returns `false` and restores tank x=100, but leaves sensor **x=1220**. This is not an accepted move and may not create an undo checkpoint. Roll back the entire attempted movement, including children, routes and pins, or compute a valid move before mutating state. Test failed moves and cancellation as well as successful carry/drop/undo in a browser.

### C075-07 — P2: structurally malformed project records produce HTTP 500

`exchange.preview` assumes record/container shapes before typed validation, e.g. `t.get(...)` at line 126. A valid JSON project envelope with `equipment.types: [1]` returns **500**, rather than a clear invalid-document response. Similar assumptions exist for items/blocks/programs.

Validate the complete document structure, IDs, duplicates and record limits before preview or mutation. Return readable 4xx errors identifying the bad record. Keep apply consistent with the validated preview and ensure malformed documents cannot leave partial unreported writes.

## What is confirmed working, and what remains unverified

- **66 existing regression checks passed on this exact source:** 18 focused owner-correction/new-feature tests; 31 earlier proof, journal, stop-plan and window checks; 17 JavaScript routing/editor checks.
- **Seven independent observations reproduce the defects above:** five Python/API/context probes and two JavaScript UI/editor probes. Their assertions intentionally capture current defective behavior; they are not seven product acceptance passes. Sources and results are in `evidence-0.7.5/` beside this review.
- All **116 Python files under `src/` parse with Python 3.11 grammar** using `ast.parse(feature_version=(3,11))`. The review interpreter is Python 3.12.14; this is not a native Python 3.11 import/runtime test or a packaged-release verification.
- Clear Preview/Save missing-field messages, the explicit newer-block-version prompt, navigation cleanup, block-condition compilation, faithful conversion fixture, new-format import protection, capacity sizing on placement/request, and successful sensor association/carry have passing targeted evidence. The provided bench-copy screenshots show the same drawing structure in both views; they are Claude's evidence, not an independently performed signed-in journey.
- Nutrient preparation has an explicit synthetic simulation label and a tested real-engine refusal. History/chart entry points and project exchange endpoints exist. Their existence does not close the specific defects above or establish complete feature acceptance.
- No second full suite was run during this review: targeted checks plus adversarial reproductions exposed concrete defects requiring changes first. No physical commands were issued.

At the last CI check of `7a70ee6`: **2 successes, 4 in progress, 30 queued**; no completed failure at that snapshot. Final CI is not yet confirmed green. PR #11 remains draft/unmerged.

Both bench health GETs failed from this session with a socket/connection error. This does **not** establish a bench outage. The running 0.7.5 build, release SHA-256, backups and the stated two-hour 0.7.4 Programs failure remain author-reported here. No signed-in real-bench walkthrough or manual sensor-drag browser check was performed by Codex.

## Owner decisions — not assumed or changed by this review

### New block safety conditions

Current behavior copies conditions from the program's selected block version. A newer validated block does not update or block an already compiled program automatically. This is a real policy choice, not just a display difference.

**Recommendation for the owner to confirm:** keep the explicit “Review and use vN” step and immutable validated programs. Separately decide whether programs using older safety settings may continue, or must become unavailable for new starts until their new draft is reviewed, validated and retested. I recommend the latter for safety-setting changes. Do not silently rewrite active validated programs, and do not implement this recommendation as an approved decision without the owner's answer. Also ensure the form shows the selected version's safety/runtime details: `blockOf` currently resolves by ID only and may describe the newer version while the form remains pinned to an older one.

### Standby after a running lead fails

Current behavior chooses a standby only before a start; a fault during an operation follows its existing stop path. The owner must decide whether automatic mid-run failover is wanted. **Recommendation pending that answer:** retain stop/alarm and next-start selection; do not add immediate failover as an incidental bug fix. Automatic failover needs explicit rules for verified shutdown, path/valve state, tank limits, unknown feedback, retry limits and what happens if the standby also fails. This review does not establish the physical configuration needed to approve it.

## Concise next action for Claude

Read this review first. Fix C075-01 through C075-07 using the existing layers; do not reread or resend the historical package. Convert the included observations to expected-correct regressions in `tests/`, including UI configuration, malformed/stale imports and rejected gestures. Complete a usable project proposal journey. Preserve the passing owner corrections and safety invariants. Keep the two policy questions pending until the owner answers.

Before another deployment, test the packaged release with the bench's supported Python 3.11 interpreter and dependencies, including importing program authoring and authenticated Preview/Save on an isolated copy. Health returning 200 did not catch the 0.7.4 outage. Run the required full checks once after fixes stabilize, inspect final CI, then use the existing approved deployment/backup procedure; no hardware operation is authorized by this review.

Update `CLAUDE_UPDATE.md` with C075 IDs, exact implementation commit, actual test/CI results, screenshots and actual deployed identities. Keep this dated review and its baseline observations unchanged. Do not report the cycle complete while these findings or required browser/release acceptance remain unresolved.
