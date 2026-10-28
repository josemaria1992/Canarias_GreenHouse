# Owner feedback: missing Tank type and disappearing type editor

Short delta to UX-01..UX-04. **These two issues block basic setup; fix them before claiming the installation journey is usable.** Existing C075 findings and owner decisions remain open. Read the relevant code and this delta; do not reread/reprint the historical package.

## UX-05 — Tank cannot be found when creating equipment

The owner reports that Tanks is missing from the equipment-type selection.

Verified explanation: Tank is built in (`equipment/definitions.py:458`, `hydraulic: tank`, capacity properties, no I/O roles). However, `web/equipment.js:27,217–218` lists only **ACTIVE** types. `equipment/store.py:156–164` adds missing built-in templates as **DRAFT**, never overwriting existing types. Equipment types displays installed records, not every available template. Thus Tank can exist in the software while being absent from the dropdown because it has not been added, remains a draft, or is retired. The exact signed-in bench status was not inspected; do not assert one cause without checking it.

A focused fake-installation check confirmed: Tank template available → no installed Tank → add built-ins → Tank exists as DRAFT but remains absent from active choices → explicit activation makes it available. The existing active pump type was unchanged.

Required changes:

- Diagnose and display the actual Tank status. Clearly distinguish **available template / draft awaiting activation / active / retired** and the permitted next action. An ordinary operator should receive a clear administrator action needed message, not an apparently missing product feature.
- Make the built-in Tank discoverable through supported initial setup and upgrades, without requiring JSON editing. Keep explicit activation permissions and never silently reactivate retired/custom types, overwrite existing versions or alter existing equipment/bindings. The administrator's action must make Tank available to operators without restarting the dashboard.
- Tank is passive equipment; creating it must not require a module/address or an I/O channel. Its level sensor is separate, as already agreed. Keep capacity configuration and the schematic size preview clear.
- Explain the separate stages: activating Tank makes the **type** available; creating a tank makes the **equipment item**; placing/connecting/publishing it makes it available to the applicable Blocks selectors. Do not claim that adding templates alone resolves the earlier empty-block problem.

## UX-06 — Edit closes before the owner can make changes

The owner calls it the Edit dialog; in this source it is a `details` editor inside each type card (`web/equipment.js:287`). It closes by itself before the owner can edit.

**Concrete cause reproduced:** `web/app.js:65–74` preserves disclosure state in a Map keyed by summary **text**. Many type cards have the identical summary “Edit (creates the next draft version)” or “Edit draft”. A later closed card overwrites an earlier open card's state; the once-a-second refresh then closes it. Executing the actual `mount` function with two same-labelled cards changed open states from **[true, false] to [false, false]**. This is not intentional validation or a user timeout.

Required changes:

- Key editor/open/form state by stable **type ID + version + editor kind**, not label text, array position or a translated caption. Reuse the existing per-record edit-state patterns; avoid a new framework.
- Keep the editor open and preserve unsaved name/definition, focus/caret, scroll and validation messages across polling, blur and unrelated status changes. Different cards must retain independent state; never restore one type's entered values into another. Do not rely solely on “skip refresh while an input has focus”: the owner needs time to open and inspect the editor before typing.
- Keep background health/acquisition/alarm updates working. Fix the editor's lifecycle rather than globally disabling refresh. Never silently autosave or discard changes. Show save/refusal/conflict feedback inline; preserve immutable active versions and the existing Save as new draft version behavior.
- If the underlying type changes while editing, keep the local input and explain the conflict; do not silently replace it. Closing/cancelling must be deliberate and handle unsaved changes clearly.

## Focused acceptance and response

On a fake/isolated installation, demonstrate Tank absent/draft/active/retired states and role-appropriate guidance; activate through the authorized flow, create a tank, set capacity, and continue into the existing Twin placement workflow. Do not activate or modify the owner's live types merely to obtain evidence.

In a **real browser**, open the first of several equally labelled type editors, wait for multiple refresh cycles without focusing an input, type and blur, then verify it remains open with all edits intact. Check another card independently, reordered cards, failed save and save/reopen of the correct draft. A helper-only reproduction proves the current bug but is not acceptance of the fix.

Record UX-05/UX-06 results, implementation commit and concise browser evidence in `CLAUDE_UPDATE.md`. Use affected tests while iterating, then required final checks once after the patch stabilises. Do not close either issue with documentation alone.
