# Owner demo release: delivery issues

Issues found by Claude while preparing and rehearsing the owner tour of 0.3.2 (`../walkthrough.md`), all
fixed in 0.3.2 before the owner's first run. Each keeps its ID if it comes back. Owner findings have
their own IDs (F-01, F-02, …); the first nine, from the owner's first-time-operator review of 0.3.2, are
listed after them with what 0.3.3 changed for each. The owner feedback on 0.3.3 (O-01 to O-05) and the
reviewer's corrections (R33-01 to R33-03) are listed last, with what 0.3.4 changed.

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
| DEL-17 | UAT-01 | A release folder whose `RELEASE.json` had been deleted started as "not a release" instead of failing the release check (code review) | The launchers always check a release wheel against `RELEASE.json`; a missing file stops the start with MISSING | UAT-01 |
| DEL-18 | all | A button pressed while a page refresh was still fetching data could still be replaced before release (code review) | The pointer state is checked again just before the page is redrawn | all |

Not changed, by design: no control algorithm, production mapping, recipe value, retention, cadence or
storage budget was changed to make the demo look successful.

## Owner findings: first-time-operator review of 0.3.2 (2026-10-19)

The owner's verdict was CHANGES REQUESTED. 0.3.3 answers each finding as below; the owner decides whether
it is fixed. The owner's acceptance results stay NOT RUN until the owner runs the cases again.

| ID | Finding | What 0.3.3 changes | Repeat |
|---|---|---|---|
| F-01 (P1) | README-FIRST asked for a "latest Python 3.12 installer" that python.org no longer publishes; no single tested route | The Windows ZIP carries its own Python (`runtime/`, CPython 3.14.8 for 64-bit Windows from python.org's NuGet package, SHA-256 pinned): double-click `AquaCrop-Demo.cmd`, nothing to install or download. CI starts it with every Python hidden from PATH, in a folder with a space, brackets and an accent, and upgrades over 0.3.1 and 0.3.2. The Windows dialogs are described in words in README-FIRST, not photographed | UAT-01, UAT-15 |
| F-02 | Stop off-screen on the Main pumps page; status buried under the diagram | Each process page opens with the process card: state, what runs, what happens next, the checks before starting and its Stop near the top. The diagram is folded by default; motor registers, contactors and selectors are under Technical details. The software Stop is not called an emergency stop | UAT-02, UAT-03, UAT-04 |
| F-03 | Engineering vocabulary everywhere | The owner's display names from 14 Oct; a plain status line on every page; what the Digital Twin is; version, configuration, ids and phases under Details. Internal ids, audit and permissions unchanged | all |
| F-04 | Recipe editor: unlabelled inputs, raw enums | Choose how to refill, set amounts or targets, check changes, use these settings. Only the chosen way's fields; timing and tolerances under Advanced settings; label, unit, range and example per field; a problem beside its field with the typed value kept; when changes take effect. Activation stays administrator-only, the rules and limits are unchanged | UAT-05, UAT-06 |
| F-05 | Alarms hard to act on | Plain headline, equipment, one next step and a link to the process; Mark as seen, Clear stop after fixing the problem and Start are separate; an unavailable step says why; marking or clearing never starts equipment; code, target and audit under Details | UAT-10, UAT-11 |
| F-06 | First administrator: `José` ended the launcher; rules not stated; wrong log path | Rules and an example before typing; a refused name or password is explained and asked again, keeping what was accepted (`José` suggests `jose`); a failed start names the data folder and log file in use. Account rules unchanged | UAT-01, UAT-14 |
| F-07 | Trends hard to use | History: grouped checkboxes with human names (addresses as secondary labels), Today / Yesterday / Last 7 days, Download readings; units, timestamps, gaps and the complete-export check kept | UAT-13 |
| F-08 | Stock preparation wording; a synthetic action among real ones | "Confirm tank A is prepared" / "Mark tank A not ready" with what each means; "needs preparing again before dosing" after a water fill; adding simulated fertiliser is on the Digital Twin page | UAT-06, UAT-07 |
| F-09 | Narrow screens | Menu button, stacked cards, the current process and its Stop near the top, the whole diagram as its own view with "Scroll sideways to see the whole greenhouse"; no page-wide sideways scroll | UAT-16 |

Also added: Getting started in the app (four pictured tasks for a first-time operator) and
`owner-tour/results.html`, which saves results only when Download results is pressed and keeps a
first-time operator's tasks apart from the owner's cases.

## Owner feedback on 0.3.3 and the reviewer's corrections (2026-10-19)

O-01 to O-05 are the owner's own feedback after looking at 0.3.3. They are not tour results. R33-01 to
R33-03 are the external reviewer's corrections to 0.3.3. 0.3.4 answers each as below; the owner and the
reviewer decide whether it is fixed. The 16 owner results stay NOT RUN.

| ID | Feedback | What 0.3.4 changes | Repeat |
|---|---|---|---|
| O-01 | Blue text is hard to read | Links and link buttons are white on green, with a darker green on hover and when selected, and a yellow focus ring. Origin badges are white on their colour. Only water keeps a water colour | all |
| O-02 | Equipment graphics | One diagram drawn in the direction the water goes, with symbols for valves, pumps and sensors. Moving water and arrows only where a reading reports flow; dashed where a route is asked for without measured flow; dotted where the flow reading is old. Tanks show their level, percent and ▲ rising or ▼ falling from the last 20 s of readings. Pumps and valves show Stopped/Closed, Running/Open, Starting/Opening, Stopping/Closing, Fault or Not known, in words and colour, the same on every page. The animation follows readings, never commands | UAT-02, UAT-03, UAT-06 |
| O-03 | Digital Twin examples | Card **Examples** with the explanation, **Choose an example**, plain names in two groups (nothing starts / it starts by itself), a one-sentence preview, what loading resets and whether anything starts, and **Load this example**. Ids, UAT numbers and the technical description under Details | UAT-06, UAT-08, UAT-09, UAT-11 |
| O-04 | Operate from the diagram | A click selects the part and shows it beside the diagram. A pump or valve is operated through the process that owns it, with that process's bounded one-time operation; nothing is switched directly. In Manual mode the action goes straight through. When the process is Stopped or Automatic, a confirmation first names the equipment and the effect and says that it switches to Manual mode; Cancel changes nothing. The panel shows the request being sent, then what the equipment reports, or the controller's refusal in words. A sensor or tank asks for the next acquired reading and shows its value, unit, time and validity; without a new one in 3 seconds it shows the newest with its real age. Links to the page and to History stay. Real equipment profiles stay read only | UAT-02, UAT-03, UAT-04 |
| O-05 | Sector data out of sight | The Overview has a **Sectors** card with each sector's valve, flow and pressure, **Open Sector N** and the history of its readings | UAT-03 |
| R33-01 | The pinned Stop on Nutrients & acid did not follow the selected tank tab | The pinned bar follows the selected tank tab: refill or acid dosing plus that tank's fertiliser or acid fill, each with its own Stop | UAT-07, UAT-08 |
| R33-02 | Some recipe errors did not reach their field | A wrong kind of value (for example 1.5 for a count, text for a number, a name with spaces as the id) comes back beside its field, nothing is saved, and the count field accepts whole numbers only | UAT-05, UAT-06 |
| R33-03 | After a fault the reset checks cannot see, the page said the problem was gone | For a valve that did not open, the page says the program cannot check it while the process is stopped and asks you to check the valve yourself. A drive fault, which the program can see, still says "still sees" or "no longer sees" | UAT-10 |

EC keeps the name **Nutrient strength (EC)**. Its help now says that EC is electrical conductivity, that
dissolved salts raise it, and that it is not a direct measure of how much fertiliser is in the water.
