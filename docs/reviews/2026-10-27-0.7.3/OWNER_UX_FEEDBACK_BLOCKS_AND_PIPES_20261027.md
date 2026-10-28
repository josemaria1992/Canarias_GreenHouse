# Owner feedback: Blocks, flow meters and imported drawings

**Read this delta first.** The owner cannot complete ordinary installation setup without guessing. This is an unfinished UX journey, not something to close by explaining internal data models or adding a manual. Improve the product flow using the existing pages and editor. Existing C075 findings and approved owner decisions remain applicable; do not reread/reprint the whole historical package.

## Owner feedback and verified explanation

- **Blocks:** “Water comes from” and “Water goes to” have nothing selectable; “Flow meter for measured litres” is also empty. The owner asks whether equipment needs defining first and does not understand the dependency.
- **Digital Twin:** flow meters are difficult to place/connect and should be **in series with the water pipe**. After importing the Overview, pipes and tanks appear dashed, so the owner cannot tell whether the drawing imported properly or is usable.
- **Overall:** the owner asks whether creating a new irrigation system is actually clear and easy. Codex agrees that it currently is not: creation, placement, connection, publication and measurement readiness are separate stages, but the UI conceals the missing stage and leaves controls apparently broken.

Verified at branch `0417bc2` (application code unchanged since reviewed 0.7.5):

1. `web/blocks.js:116–151` takes all these choices from `topo.graph.nodes`, not directly from Equipment. Source choices are source/tank nodes; destination choices are sector/tank nodes; meter choices are meter nodes. `api/blocks.py:73` returns the stored installation graph. Equipment creation alone is therefore insufficient; an item must appear in the applicable installation graph. A Twin-only draft does not silently become the real installation's published drawing.
2. A fake-installation check created **six equipment items**. Before saving the drawing, source/destination/meter choices were **0/0/0**; afterwards they were **2/2/1**. The empty-field dependency is reproduced, not an assumption about the owner's account.
3. In the supplied legacy bench fixture, conversion produces **33 linked symbols**, including seven meters, but the **five tanks, two sectors and one source remain eight unmapped symbols**. Fifteen pipes remain reference sketches and four have real routes. This explains how the picture can contain tanks and pipes while source/destination choices remain unavailable. These are fixture counts, not a fresh signed-in inventory of the real bench.
4. `topology/definitions.py:52` already models an inline meter with inlet/outlet ports. `hydraulic-editor.js:201` places equipment without connecting it; `joinHere` inserts a junction, not a meter. No direct meter-insertion action was found. The model supports series connection, but the placement journey does not make it easy.
5. Dashed elements have distinct meanings: `app.css:434–436` styles old pipe sketches and unmapped equipment; `app.css:385` separately styles sensor measurement associations. A sketch is **not a water connection**; a dashed tank may be an unlinked old symbol; an instrument association carries no water. The owner should not have to discover these meanings through trial and error.

## UX-01 — explain and resolve empty Blocks choices

Keep Blocks focused on associations and safety. Use clear labels/help: **Water source** (e.g. a main tank), **Watering destination** (e.g. a sector); for a tank-filling purpose, explain the destination tank. These are examples, not installation assignments. A valve controlling a sector is not itself the sector object.

- Before presenting an unusable form, show its specific missing prerequisite in one compact inline status. Distinguish **not created**, **created but not placed**, **present only in a Twin draft**, **unlinked imported symbol**, and **no connected path**. Show relevant equipment names where available.
- An empty select must say why and what to do next: for example, “No water source is available in the published installation drawing. Create a tank or water source on Equipment, place and connect it in Digital Twin, then export and approve the drawing.” Use only the steps actually missing; do not repeat generic instructions when equipment already exists.
- Preserve form values across navigation/refresh. Refresh available inventory without requiring destructive Overview re-import or losing a Twin draft. Do not silently promote unpublished equipment/connections into the live installation.
- **Do not restore the removed Equipment / Design the drawing shortcuts or an editor/library on Blocks.** Use the existing global navigation and concise contextual guidance; avoid extra competing buttons/pages.
- For measured litres, explain whether no meter exists, it is unplaced/unpublished, it is on another path, or its measurement channel/scaling is not ready. Offer meters suitable for the chosen path and identify why others are unavailable. Preserve planned/unassigned design support; drawing a meter does not make measured irrigation ready. Never substitute elapsed time or pump rating for measured litres.

## UX-02 — insert a flow meter into a pipe deliberately

Provide one clear contextual **Insert flow meter into this pipe** journey using existing equipment, with an inlet/outlet preview. It may start from selecting a pipe or an explicit insertion mode. Moving a symbol near a pipe must not silently connect it.

The resulting hydraulic connection must be:

```text
Before:  upstream ─────────────── downstream
After:   upstream ── [flow meter] ── downstream
                       in → out
```

- Split the original hydraulic edge into two edges through the meter, align its ports with the pipe and retain 90-degree routing. A symbol drawn over an unchanged bypass pipe is not success. Do not treat the inline flow meter as a side/tap sensor.
- Preserve unaffected routes, geometry, stable item identity and relevant edge metadata/references. Handle existing sensors or references to the replaced edge explicitly. Apply insertion atomically as one undoable action; a failed insertion restores everything.
- Explain “already placed” and offer to locate/highlight that meter instead of making it seem missing or encouraging duplicates. Distinguish an actual inline flow-meter type from a similarly named/icon-labelled sensor.
- A dashed reference sketch is not an existing hydraulic edge to split. Guide the user through confirming its endpoints/connection first; do not pretend insertion has made an unresolved sketch functional. Keep green valid-connection hints and refuse invalid directions clearly.
- An inline meter drawn without a commissioned channel is allowed as planned equipment, clearly labelled. Its measurement is unavailable until its input/scaling is configured and valid.

## UX-03 — make dashed/reference states understandable and actionable

Use the same symbols, sizes and positions in Overview, Twin and Blocks. Preserve the imported drawing. State honestly: **“Your drawing is preserved; some equipment and water connections still need confirmation.”** Show the actual remaining counts and a single next action within Twin.

- Connected water pipes have a normal solid route, including when stopped. Do not use “dashed” to mean both stopped flow and missing connectivity. Show measured/simulated flow only when justified.
- Unresolved old pipes stay visible as labelled reference lines until reviewed. Clicking/selecting one must explain what is missing and let the user confirm endpoints and preview a valid pipe. Crossings alone never become junctions.
- An unresolved tank must remain recognisable as a tank with its preserved size/name and a small plain-language status such as **Needs equipment link**. Do not make the entire installation look like an unexplained disabled drawing. Registered tanks remain normal tank symbols; planned/unassigned I/O is a separate status.
- A level sensor inside its tank must be visibly associated without suggesting a water pipe. If instrument association lines are shown, distinguish them from unresolved pipe references, and explain “Measures tank level — no water connection.” Do not rely on colour/hover alone.
- Provide a compact legend and contextual explanation. Once a reference is resolved, show the resulting real connection rather than a confusing permanent duplicate; preserve the original in migration/history/reference data.

## UX-04 — guide completion inside the existing Twin

Offer a small, ordered **Finish imported drawing** checklist in the existing Twin panel: link each unresolved tank/source/sector to existing Equipment or identify what needs creating there; then confirm unresolved pipe endpoints and inline meters; then review connectivity and save/export. Highlight the object being addressed and keep progress/draft state. Do not mass-create items, guess capacities, choose plumbing from geometry, assign channels or infer physical verification.

Show the setup order clearly for a new installation: **Equipment → draw/connect in Twin → export/review the Overview → associate equipment in Blocks → Programs and Twin testing**. Make current-step status visible without adding another editor or equipment library. Successful import is not the same as a complete hydraulic design; the UI must make both states clear.

## Acceptance and token-efficient delivery

Use focused API/editor tests and one browser journey on an isolated project. Cover: equipment exists but graph absent; draft not published; legacy tank/sector symbols unresolved; meter absent/already placed/off-path/unassigned; actual inline insertion; reverse/vertical pipe orientation; failure rollback and undo; resolved sketch replacement; then import/save/reopen/export/apply with geometry retained. Verify selected block paths contain the meter and measured-litre configuration reports the correct next step. No physical output needs to operate.

A person using only the visible UI should create a tank/source, pump, valve, flow meter, sector and level sensor, connect and publish the drawing, then create a block **without encountering unexplained empty choices or learning the term topology**. Also complete the actual legacy-drawing fixture journey. Record browser evidence, including real pointer insertion/selection and undo; passing a helper-only test is insufficient.

Read only this delta, relevant code/tests and still-open review items. Reuse existing components and backend validation. Do not introduce a new framework, new design page, duplicate library or broad visual rewrite. Run affected checks while iterating and final required checks after stabilising. Reply in the existing `CLAUDE_UPDATE.md` with UX-01..UX-04 status, exact commit, concise evidence links and remaining blockers. Do not close this feedback with explanations alone: demonstrate the improved end-to-end journey.
