# UX-07 — equipment types without code

## Owner request and verified current problem

Owner: “When creating an equipment type, there are still templates. I don't want the operator to have to understand the code. Is there any other way to create the types much more simple and intuitive?”

**Confirmed requirement:** an operator must be able to create and edit supported equipment types without reading/writing JSON, schema keys or programming enums. Existing type availability and editor-persistence fixes do not satisfy this requirement.

Verified at `8dc47b6` (application implementation unchanged since `de3088a`): `src/aquacrop/web/equipment.js:304–321` implements `typeEditor` as a JSON textarea, `JSON.parse` and an instruction listing OUTPUT/INPUT, DIGITAL/ANALOG and COMMAND/RUNNING/FAULT. Both New type and Edit call it. Type cards at `:340–341` also display internal operation/simulation enums. The interface still expects technical schema knowledge.

## Proposed form for owner review

This is Codex's recommended interaction, **not a claim that the owner has approved every screen or new operating policy**. The owner has approved the no-code goal. Resolve material remaining choices with the owner; ordinary visual implementation can follow the existing UI conventions.

1. **Choose what the equipment is.** Visual choices with names: Pump, Valve, Tank, Flow meter, Level sensor, other supported sensors, Water source, Sector, or a custom type. Offer “Copy an existing type” as a convenient starting point. Built-in definitions may remain internally; choosing Pump must not open its JSON or require the operator to learn what a software template means.
2. **Name and appearance.** Name, short optional description and icon picker with capitalized names. Generate internal keys; do not expose schema syntax or require users to invent role identifiers. Keep hydraulic function separate from the decorative icon. A type describes a reusable kind; a named equipment item remains on Equipment.
3. **Ask only relevant questions.** Pump example: Start/stop control; Does it report running? Does it report a fault? Valve example: Open control, any supported close control and position feedback. Sensor example: What does it measure? Which unit? What signal does it use? Use checkboxes, dropdowns and labelled number fields. Show physical signal choices such as 4–20 mA only where relevant, with a short explanation. Never invent wiring, active contact polarity, calibration or feedback that is not confirmed.
4. **Relevant properties and limits.** Show units and explain each required value next to its field. Tank capacity belongs to each tank item; defining a Tank type must not force all its equipment to share the same capacity. If a custom type needs additional properties or signals, provide an “Add property / Add signal” form with suitable validation; unsupported hardware must be explained rather than simulated as working.
5. **Review in ordinary language, then save a draft.** Example: “Irrigation pump — Start/stop output; running signal; fault signal; maximum running time: [entered value].” Name missing fields and how to fix them. Keep actual channel assignment/verification in the equipment/commissioning journey. A type draft must not assign channels, verify wiring, activate itself, enable a program or operate equipment.

Apply the same form to **editing existing types**, not only creation. Put uncommon technical settings in a clearly labelled expandable section using normal controls. JSON must never be a required workaround for custom supported types. If a developer-only raw view is retained, its existence/access is a separate product choice; do not expose it as the operator's normal editor or change permissions without agreement.

## Implementation boundaries and acceptance

- Reuse the existing TypeBody model, draft/version endpoints, built-in definitions, validation and permission checks. Generate the validated definition from the form; do not create a parallel schema or a second type store. Preserve administrator activation and immutable versions; do not silently upgrade existing items.
- Load all supported fields into the form and preserve fields a simpler view does not display. Do not lose custom roles, property definitions, simulation configuration or limits on a no-change edit. Explain unsupported legacy settings before saving rather than dropping them. Never create fabricated simulation behaviour; retain NOT MODELLED with a plain explanation when no model exists.
- Keep unsaved entries, selection, focus and the open editor across refreshes and validation failures. Retain the UX-06 stable editor identity fix. Use field-level messages; remove raw enum/schema help from the everyday flow.
- Demonstrate in a browser, on copied data: create/edit a Pump with optional running/fault signals; a passive Tank with no channel; a Flow meter and Level sensor with the right measurement choices; a custom supported type with an extra property/signal. No JSON input in any journey. Reopen drafts and verify meaning is preserved; verify a second edit does not erase hidden supported fields, and activation still follows current permissions/version rules.
- Keep scope focused: this improves Equipment types; it does not reintroduce equipment creation in Blocks or duplicate the Digital Twin drawing workspace. Avoid a long wizard if a short conditional form is clearer. The exact screen arrangement remains a proposal for owner review.

## Related flowmeter feedback is already recorded

[C077-07](CODEX_REVIEW_0.7.7_20261027.md#c077-07--p2-inline-meter-insertion-ignores-grid-snapping) requires in-series insertion, grid/pipe-centreline alignment, correct orientation and rendered-browser evidence. [C077-08](CODEX_REVIEW_0.7.7_20261027.md#c077-08--p2--unfinished-owner-journey-legacy-import-is-not-simulation-ready) covers importing all known pipes/connections and reconciling actual legacy ambiguities. These remain open; this new type-form request does not replace the safety/recovery findings.

Claude: address UX-07 in the existing `CLAUDE_UPDATE.md` with implementation commit, short browser evidence, what remains unsupported and any actual owner decision still needed. Read this file and the affected UI/model/API only; do not recopy the historical review packages or rerun unaffected tests repeatedly.
