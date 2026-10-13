# Owner demo release: delivery issues

Issues found by Claude while preparing and rehearsing the owner tour of 0.3.2 (`../walkthrough.md`), all
fixed in 0.3.2 before the owner's first run. Each keeps its ID if it comes back. Owner findings get
their own IDs (F-01, F-02, …) in `feedback-template.md`; none exist yet, because no owner case has run.

"Repeat" names the owner cases to run again after a fix. All owner results are NOT RUN, so nothing needs
repeating yet; the column says what a later fix to the same area would affect.

| ID | Found in | What was wrong | Fix in 0.3.2 | Repeat |
|---|---|---|---|---|
| DEL-01 | UAT-01, UAT-15 | A Windows PC that had run the provisional 0.3.1 demo could keep using the 0.3.1 program environment for a newer build, because the environment was named after the version only | The launchers name each environment after the SHA-256 of the exact program file; the release check compares `RELEASE.json` with the installed wheel and stops on MISMATCH | UAT-01, UAT-15 |
| DEL-02 | UAT-01 | The version, source commit and configuration in use were hard to confirm | Status line shows version, profile, config and recipe; Settings, *Versions* shows release, commit, build time, "wheel verified", config and the 80-point inventory; the launcher prints the release line | UAT-01 |
| DEL-03 | README-FIRST | A 32-bit Python 3.12 was accepted, although the bundled packages are 64-bit only | The launcher only accepts a 64-bit Python; README-FIRST gives the official 64-bit installer steps | UAT-01 |
| DEL-04 | UAT-01, docs | Several documents still called the 48-point configuration the normal demo | `installation-80` is the normal demo everywhere; the 48/24-point configurations are labelled legacy test fixtures | none |
| DEL-05 | UAT-12 | The 32 unassigned channels looked like faults on the Digital twin page | They show a neutral UNASSIGNED pill | UAT-12 |
| DEL-06 | UAT-06..11 | Scenario presets had no description, so the tour's start states were hard to pick | Tour presets in their own group, labelled "Owner tour (UAT-nn)" with DEMO values and what to do next | UAT-06, UAT-08, UAT-09, UAT-11 |
| DEL-07 | UAT-11 | No visible control to stall acquisition for the stale-data case | Digital twin page: stall/resume acquisition buttons (simulation only) | UAT-11 |
| DEL-08 | UAT-04, UAT-05 | Choices made in a form (for example pump MP2 in the MANUAL form) were reset by the page's refresh every second | Changed fields keep their value until the action succeeds | UAT-04, UAT-05 |
| DEL-09 | UAT-02 | Only the outline of a tank in the diagram opened its page; the inside did not react to a click | The whole tank, pump and valve symbol is clickable | UAT-02 |
| DEL-10 | UAT-05 | Recipe editor sections collapsed every second | Open sections stay open; the recipes page only redraws when a recipe changes | UAT-05 |
| DEL-11 | all | A click could be lost when the page redrew under the pointer | No redraw while a button is pressed or a field is being typed in | all |
| DEL-12 | all | After switching page, the previous page could be drawn briefly under the new name | A redraw for a page that is no longer shown is dropped | all |
| DEL-13 | UAT-08 | With the pH probe invalid, the Acid pH dosing card still showed the last "Next: pH 6.19 within target…" line beside the reason it was waiting | The plan line is cleared while the process waits; the in-band line now reads "pH 6.19 at or below 6.2 (target 6 + 0.2): no dose" | UAT-08 |
| DEL-14 | UAT-08, UAT-09 | "Last result" and the batch/run tables were printed as `key=value` developer text | Readable labels (for example "pH start 6.80 · total (L) 16.79 · pH end 6.20") | UAT-08, UAT-09 |
| DEL-15 | UAT-03, UAT-10 | In the diagram, main pump MP1 read "req ON · run" while the drive was stopped after a trip: the feedback shown is the selection contactor K1, not the motor | The label reads "req ON · K1 closed"; whether the motor runs is shown on the drive symbol | UAT-03, UAT-10 |
| DEL-16 | illustrated guide | In `owner-tour/index.html`, 21 list items that wrap onto a second line were split into a stray paragraph, and the header showed the rehearsal machine's folder path | Wrapped items stay one item (`tests/unit/test_tour_guide.py`); the header names the release launcher only | none (reading only) |

Not changed, by design: no control algorithm, production mapping, recipe value, retention, cadence or
storage budget was changed to make the demo look successful.
