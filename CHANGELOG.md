# Changelog

## 0.5.4 (2026-10-24): users and roles (owner feedback)

- An administrator has every right, commissioning included (owner decision; supersedes ADR-0017's rule that an
  administrator without the flag cannot arm a bench lease). The commissioning flag now matters for operators only.
- Settings, Users and roles: each user has Save (role, commissioning), Disable or Enable, Reset password (a temporary
  password the user must change at the next login) and Delete (pressed twice; the name stays in the history). The
  last administrator cannot be deleted, demoted or disabled, and nobody can delete or demote themselves.
- Creating a user shows only the chosen role's rights, in plain words, and what Commissioning means.

## 0.5.3 (2026-10-24): a simpler Add module wizard (owner feedback)

- Start opens the adapter and reads the module at once. No confirmation phrase is typed: pressing Start is the
  user's statement that only the new module is wired to the adapter, and the record says so. The server still
  refuses when more or fewer than one module answers.
- One Configuration panel: name, SKU (analog version and range for an AI 8CH) and address, with Suggest and
  Reserve beside each other. Suggest works before the module is named.
- Every result and refusal is shown next to the button that caused it, not as a pop-up. Discovery's Scan too.
- The page starts with the owner's six steps for adding a module.

## 0.5.2 (2026-10-24)

- Equipment: each item has an "Edit details" editor for its properties, analog scaling, alarm limits and twin
  links (a pump driving a flow meter in the twin). Saving makes a new equipment version.

## 0.5.1 (2026-10-24): fixes found by CI on the 0.5.0 pull request

- The Operation page script had a syntax error, so the page did not load (0.5.0 on the bench CM4 had it). Every
  packaged browser script is now parsed by a test.
- The CM4 installer's source no longer names the bench host in a message (installer policy test).

## 0.5.0 (2026-10-24): equipment, programs, the module wizard and a digital twin beside the real dashboard

For the owner's brief of 24 October 2026 and the owner's answers the same day (ADR-0018). Tested on the PC with
the in-memory RS485 buses, virtual modules and a local browser check. **Owner-performed bench tests are NOT RUN**
(docs/commissioning/bench-tests-0.5.md). Relay outputs, the drive, pumps, valves and dosing stay out of scope.

### Equipment
- Equipment types (versioned definitions with channel roles, typed properties, limits, permitted operations and
  a simulation behaviour), built-in editable templates (pump, valve, level sensor, flow meter, button, test LED),
  individual equipment with a built-in icon, properties, analog scaling and alarm limits.
- Channel bindings per role, PROPOSED then VERIFIED with the I/O board's check plus your observation (or citing a
  VERIFIED I/O assignment); one channel per role; electrical compatibility (relay contact vs transistor sink,
  analog range) checked; a type upgrade or an item change sends affected channels back for verification.

### Hardware
- Hardware → Add module: prepares a new module on the controller's USB-to-RS485 adapter as a persisted
  transaction (read, identity, address reservation, preview, write and verify, move, detect), never touching the
  installation buses; an unclear write is UNCERTAIN with recovery steps; detection writes a configuration proposal.
- Discovery: read-only, one address per poll inside each bus's worker, only when the poll has time to spare;
  KNOWN, NEW, MISSING and CHANGED.

### Programs and operation
- Program editor: run now, weekly windows (overnight, several per day), measured volume, keep between limits,
  sensor-triggered runs and ordered sequences with verified steps; immutable versions; validation; next windows.
- Europe/Stockholm schedules with the owner's daylight-saving policy; missed windows are skipped, never replayed.
- Installation-level AUTO/MANUAL per runtime, conflicts with an explicit choice, restart recovery with a checklist
  and approval by an operator or administrator.

### Digital twin
- `aquacrop twin-serve` and `aquacrop-twin.service`: a second service with virtual buses only, no device nodes,
  its own database and login cookie, capped CPU and memory; fed one way by a snapshot of the real system; the same
  logins work; accepted twin results become evidence for exactly the same program version.

### Physical bench tests
- `bench_tests` in the configuration: programs may switch allowlisted IO 8CH outputs only, only inside an armed
  bench lease, only for a version that passed in the twin; the lease's end stops them; the engine is checked again
  under the bus lock before each frame. Without `bench_tests` the read-only profile writes exactly as before.

### Storage
- Schema 5 (equipment, bindings, programs, activations, occurrences, conflicts, mode, recovery, commissioning
  transactions), migrated after the usual verified backup.

## 0.4.0 (2026-10-20): bench bring-up of the CM4 box and the six modules, the I/O board

For the owner's hardware bring-up brief of 2026-10-20 and the owner's answers (ADR-0017). Tested on the in-memory
RS485 bus and the simulation; **nothing was run on the CM4 or the modules**. Physical AUTO still does not
exist, the drive interface stays NOT_CONFIGURED, `physical_mapping` stays empty, off-device backups stay
disabled, retention (365 d), cadence (60 s) and the 17 GiB budget are unchanged, and the 16 owner tour
results stay NOT RUN. No control algorithm or production mapping changed.

### Module setup and the bench configuration
- `aquacrop hw scan` lists the modules that answer on a port (reads only).
- `aquacrop hw setup` gives one module its address, Normal channel modes (Relay (D), IO 8CH) or analog
  ranges (AI 8CH), with only that module on the wire: refuses two answering modules, an analog module
  given as digital (or the reverse) or a port in use; never broadcasts, never writes the UART register;
  reads back from the new address and checks the old one is silent; always writes a JSON record.
- The bus cannot tell a Relay (D) from an IO 8CH, nor one AI 8CH from another, so `hw setup` requires
  `--sku` (26517, 26244 or 25821, from the label, matching `--model`) and `--label` (the name marked on the
  module, equal to `--name`). It shows them in the confirmation and keeps them in the record as the
  installer's statement (`provable_from_bus: false`). `hw make-config` refuses a record without them
  (owner's reviewer, 2026-10-21).
- `aquacrop hw make-config` builds a HARDWARE_READ_ONLY configuration from the records (two ports: digital
  modules on one, analog on the other), validated before it is written.
- `aquacrop hw ports` checks COM1 to COM3 of the RS485 box (boot overlays, device nodes, permissions, port
  owner) and changes nothing.

### Safety on the bench
- Safe-state OFF (`safe_state.outputs_off_on`): at service start and stop, at the end of a bench session
  (only its outputs) and when a module answers again, every digital output that reads ON gets one OFF with
  readback, audited with origin SAFE_STATE. OFF only, digital outputs only, never a drive. Empty by default;
  `hw make-config` sets all four moments. The poller notices an expired session with no page open.
- Writes on the wire now depend on the configuration (`RELEASE_READINESS.md`, "What AquaCrop writes on the
  wire"; CP-05 updated): the read-only configuration still writes nothing on its own; the generated bench
  configuration writes only these FC05 OFF frames; no configuration ever sends an ON automatically.
- A bench session can be renewed by its holder; "All outputs OFF" needs no session.
- A bus poller that fails restarts after 30 s; port locks use the device's real path (a symlink and its
  target are one port).

### I/O board (developer page)
- One card per module: output switches with asked and reported lights, input lights with the changes
  seen, analog readings in mA or V with a bar, the raw value, quality and age, the module's answering state
  and the port's polling time. Virtual modules on the simulation (I/O test mode), real ones on the bench.
- Guided check: every channel in turn with a manual Next; an output is switched ON during its own step,
  an input waits for its change, an analog input shows its reading; Works / Doesn't work / skipped is
  recorded with what the server saw.
- I/O assignment: a channel gets an installation signal (PROPOSED), and VERIFIED after the server-side
  check and the user's observation; export as YAML or JSON. Records only: nothing is bound or controlled.
- Schema 4 (`io_assignments`, `io_checks`, an index on output commands), migrated after a safety copy.

### Release
- Offline packages for the CM4 on Python 3.11 (Bookworm) and 3.13 (Trixie); CI stages the installer on both.
- New guide `docs/installation/bench-bringup.md`; ADR-0017.

## 0.3.4 (2026-10-19): owner feedback on 0.3.3 and the reviewer's corrections

The owner's ad hoc feedback on 0.3.3 (O-01 to O-05, 2026-10-19) and the reviewer's findings R33-01 to R33-03.
This is feedback, not the formal tour: the 16 owner results stay NOT RUN. No control algorithm, production
mapping, recipe limit, retention (365 d), cadence (60 s), storage budget (17 GiB), role or permission gate,
bench lease policy, restart behaviour or hardware profile changed; physical AUTO still does not exist, the
drive interface stays NOT_CONFIGURED, off-device backups stay disabled and the 32 added channels stay
unassigned. Internal identifiers are unchanged.

### Owner feedback
- O-01: links and link-like text are white on a dark green ground, also when hovered, pressed, opened or
  focused (focus ring amber), in the light and the dark colour scheme. No text is blue any more; chart
  legends name their series in the text colour beside a coloured mark. Water keeps its water colour.
- O-02: one style and one state vocabulary for pumps, valves, tanks, sensors and pipes, the same in the
  diagram, its panel, the Sectors card and the process pages: Running / Open (the equipment reports it),
  Starting… / Opening… and Stopping… / Closing… (asked for, not yet reported; amber, dashed, blinking),
  Stopped / Closed, Fault, Not known (no feedback, or its reading is old). Water moves along a route, with
  arrows in its direction, only where a flow is measured on it; a route asked for without measured flow is
  dashed, one whose flow reading is old or missing dotted. Tanks show ▲ rising / ▼ falling with L/min from
  the readings of the last 20 s. A valve without a position signal is confirmed by the measured flow on its
  route, a tank inlet by the measured level rise; a command alone never shows as movement.
- O-03: the Digital Twin's examples: "Choose an example", "Load this example" and "These examples set up the
  simulated greenhouse so you can try different situations." Each example has a plain name and a one-sentence
  preview; the page says what loading resets and whether the example only sets up a situation or also starts
  operations, confirms tanks, puts its own recipe in use or applies simulated faults (read from the
  example's own script). Example ids and owner-tour case numbers are under Details.
- O-04: equipment and sensors on the Overview diagram (and the full and page diagrams) can be selected. A
  panel beside the diagram shows the state, what was asked for, what is reported and the process that runs
  it. A pump or valve is operated through that process, never directly: in Manual mode the action is sent at
  once as the process's bounded operation (for example "water Sector 1 once with main pump 1 for 300 s"); in
  automatic mode, or when the process is stopped, a confirmation names the equipment and the action, says how
  automatic operation is affected and states any switch to Manual mode; Cancel sends nothing. The panel then
  shows the request as pending, the controller's refusal in plain words, or what the equipment reports.
  Amounts come from the recipe in use where it sets them. A sensor asks for a new reading
  (`GET /api/plant/reading`, which waits for the next acquisition and sends nothing to any device) and shows
  its value, unit, time measured, age and validity; if no new reading arrives within 3 s, the newest is shown
  as such with its real age. Hardware profiles show readings only.
- O-05: a Sectors card on the Overview keeps each sector's name, valve state, flow, pressure, its page and the
  history of its readings; the sector boxes in the diagram keep their readings and say when being watered.
- Nutrient strength (EC) is explained as electrical conductivity: dissolved salts raise it, fertiliser among
  them, and it is not a direct measure of the fertiliser quantity.

### Reviewer corrections
- R33-01: on a narrow screen, the pinned Stop bar of Nutrients & acid shows the chosen tank's processes (refill
  or acid dosing, and that tank's own water fill), each with its state and its own correctly named Stop.
- R33-02: a recipe value of the wrong kind (1.5 doses, a text in a number, a bad recipe id or name) is refused
  with each problem's field and a short message (`422 {"detail": {"message", "fields": [{path, message}]}}`);
  the editor shows it beside the field, marks the field `aria-invalid` and keeps the typed value. Whole-number
  fields say so and step by 1. Server validation is unchanged.
- R33-03: when the reset checks cannot see a fault's cause while the process is stopped (a valve that did not
  open, missing flow), the recovery step says so instead of "The program no longer sees the problem". Each
  process view now reports `fault_checkable`; no check or permissive changed, and none reads the twin.

### Release checks
- CI's Windows upgrade test now goes 0.3.1 → 0.3.2 → 0.3.3 → this release.

## 0.3.3 (2026-10-19): corrections for a first-time operator

The owner's first-time-operator review of 0.3.2 (2026-10-19, findings F-01 to F-09, CHANGES REQUESTED).
Display, guidance and Windows start only. No control algorithm, production mapping, recipe limit, retention
(365 d), cadence (60 s), storage budget (17 GiB), role or permission gate, bench lease policy, restart
behaviour or hardware profile changed; physical AUTO still does not exist, the drive interface stays
NOT_CONFIGURED, off-device backups stay disabled and the 32 added channels stay unassigned. Internal
identifiers (process, alarm, point and recipe field ids) are unchanged and stay visible under Details.

### Windows start (F-01, F-06)
- The Windows release carries its own Python in `runtime/`: CPython 3.14.8 for 64-bit Windows, python.org's
  build as published by the Python Software Foundation (NuGet package `python`, SHA-256 pinned in
  `scripts/build_release.py`), with AquaCrop and the locked packages installed at build time.
  `AquaCrop-Demo.cmd` uses it first and installs and downloads nothing; no Python is needed on the PC.
  Python 3.14 joins the tested range (CI test matrix 3.11 to 3.14).
- First administrator: the login name and password rules are shown with an example before typing; a name
  or password that breaks them is explained and asked again (an accented name gets a suggestion, `José` ->
  `jose`); a failed start names the data folder and log file actually in use. The account and password
  rules are unchanged.

### Pages for a first-time operator (F-02 to F-05, F-07 to F-09)
- Display names confirmed by the owner on 14 Oct: Watering, Fertiliser tank A/B, Drainage tank, History,
  Start automatic watering, Stop watering / Stop refill / Stop return pump, Stopped because of a problem,
  Checks before starting, Mark as seen, Clear stop after fixing the problem, Reading valid / Reading is old /
  No reading / Reading outside the allowed range / Unused channel, Acidity (pH), Nutrient strength (EC),
  Check settings / Use these settings / Restore previous settings, Download readings. The words live in one
  place (`web/words.js`); the internal ids beside them are unchanged.
- A plain status line at the top of every page ("Digital Twin · Watering stopped · Main tank 60% · Last
  reading 1 second ago"), with a short explanation of the Digital Twin; version, configuration, ids and
  phases under Details.
- Process pages open with one card: what is running, what happens next, the checks before starting and the
  process's Stop. The diagram is folded away by default; motor registers, contactors and selectors are under
  Technical details. The software Stop is not called an emergency stop.
- Recipes: choose how to refill, set amounts or targets (only the chosen way's fields; timing and tolerances
  under Advanced settings), check the changes, use these settings. Every field has a label, unit, allowed
  range and example; a problem shows beside its field and the typed value stays; the page says when a change
  takes effect. Only administrators can use new settings, and the validation rules and limits are unchanged.
- Problems (Alarms): a plain headline, the equipment, one next step and a link to its process, with separate
  Mark as seen, Clear stop after fixing the problem and Start buttons; an unavailable step says why (the
  process reports the reason its reset is blocked, read-only). Marking, clearing or resetting never starts
  equipment. Code, target and audit under Details.
- History (was Trends): readings grouped under human names with checkboxes, Today / Yesterday / Last 7 days,
  Download readings. Units, timestamps, gaps, point addresses (as secondary labels) and the complete-export
  check are kept.
- Fertiliser tanks: "Confirm tank A is prepared" and "Mark tank A not ready" with what each means; after a
  water fill the page says the tank needs preparing again before dosing. Adding simulated fertiliser moved to
  the Digital Twin page.
- Narrow screens: a Menu button, stacked cards, the current process and its Stop near the top, and the whole
  diagram as its own view ("Scroll sideways to see the whole greenhouse") without page-wide sideways scroll.
- Getting started (first menu item): four pictured tasks, one action per step with the exact button names:
  read the tank level, water one sector, stop watering, recover from a simulated problem.

### Owner tour and results
- The tour (UAT-01 to UAT-16) and its illustrated guide use the new screens and names; it starts with the
  steps to start AquaCrop and the text a first start prints (recorded through the release launcher).
- `owner-tour/results.html`: a results form that saves only when you press Download results (a CSV file),
  every owner case preset NOT RUN, and a separate part for a first-time operator's four tasks. The guide no
  longer prints result boxes that suggest it saves anything.
- The release CI check records whether the checkout was clean before the build writes its output.

## 0.3.2 (2026-10-13): owner demo release and acceptance tour

The owner's brief of 2026-10-13 18:56 UTC: a tested Windows/CM4 release ZIP and a guided acceptance tour.
Software and simulation only. No control algorithm, production mapping, recipe value, retention (365 d),
cadence (60 s), storage budget (17 GiB) or hardware profile changed; physical AUTO still does not exist,
the drive interface stays NOT_CONFIGURED, off-device backups stay disabled and the 32 added channels stay
unassigned.

### Added
- Owner acceptance tour UAT-01 to UAT-16 in `docs/operator/walkthrough.md`: goal, page, role, start
  state, load (DEMO presets), numbered steps, expected result, failure criteria, duration, speed,
  reset and a result line, NOT RUN. Five simulation-only tour presets labelled "Owner tour (UAT-nn)".
- Illustrated offline guide `owner-tour/index.html` in the ZIP (`scripts/ui_check/make_tour_guide.py`)
  from Claude's browser rehearsal through the release launcher (`scripts/ui_check/run_tour_rehearsal.py`,
  `tour_rehearsal.cjs`); `owner-checklist.csv`, `owner-feedback-template.md`, review notes and the
  delivery issue list (`docs/operator/owner-tour/`).
- `scripts/verify_release.py` (the extracted ZIP against its manifest and the packaged configuration)
  and `scripts/release_smoke.py` (a session through the release launcher: cold start, restart, upgrade).
- CI: the release is built in two passes with the same wheel (rehearsal, then guide and wheelhouses)
  and verified; the Windows job runs a cold start, a restart and an upgrade over the 0.3.1 demo ZIP
  through `AquaCrop-Demo.cmd`; the CM4 staging verifies `installation-80`.
- Settings, *Versions*: release, source commit, build time, "wheel verified", config and inventory.

### Changed
- Launchers name each environment after the SHA-256 of the wheel, so an older environment never runs a
  newer build; the release check stops on MISMATCH. Only a 64-bit Python is accepted on Windows.
- `README-FIRST.md` rewritten: official Python 3.12 (64-bit) installation, data and log locations,
  stop and restart, upgrading from 0.3.1, a separate trial folder, troubleshooting.
- `installation-80` is the normal demo in every document; `installation-48`, `demo-48` and `demo-24`
  are labelled legacy test fixtures.

### Fixed (delivery and usability, `docs/operator/owner-tour/issues.md`)
- DEL-01 to DEL-18: among them form choices reset by the refresh, collapsing recipe sections, lost
  clicks during a redraw, the tank interior not clickable, unassigned channels looking like faults, a
  stale acid "Next" line beside its waiting reason, developer-style result text, and the main pump
  label showing the contactor as "run", and list items split apart in the illustrated guide.

## 0.3.1 (2026-10-13): two IO 8CH modules, 80 points (ADR-0016)

The owner's inventory update of 2026-10-13: two Waveshare Modbus RTU IO 8CH (SKU 26244) beside the
two Relay (D) and two AI 8CH boards. Integrated from the owner-supplied `waveshare_io_80.patch` and
reviewed. Software and simulation only: no module has been connected.

### Added
- Catalogue model `WAVESHARE_MODBUS_RTU_IO_8CH` (SKU 26244): 8 DI and 8 open-drain sink outputs,
  500 mA per channel, with `output_type` (`RELAY_CONTACT` or `OPEN_DRAIN_SINK`) and the current limit
  carried through the configuration, API, diagnostics and dashboard ("TRANSISTOR SINK, not relay
  contacts"). Same V2 protocol as the Relay (D): FC01/FC02 reads, FC05 writes, FC03 output modes.
- Both I/O pages (simulated and real) label each module's outputs: "relay contacts" or "TRANSISTOR
  SINK, not relay contacts, max 500 mA"; the browser check covers both (`ui_check`, 60 checks).
- `installation-80` (SIMULATED, launcher default): the installation model on six modules, the 32
  added channels unallocated test points. `installation-48` and the 24/48-point demos are kept.
- Six-module read-only and Malmö bench examples (proposed addresses 5 and 6, unverified).
- `measure_storage.py --inventory 80`, ADR-0016 and `docs/model/digital_io_8ch.md`. Owner decision:
  all 32 added channels stay unassigned and all 16 added outputs (DO1 included) are spare/test-only;
  the table in `docs/model/signal_budget.md` stays a proposal, not fitted devices.

### Changed
- Database budget 17 GiB (`db_budget_bytes: 18253611008`, owner 2026-10-13) in `installation-80`
  (version `2026-10-13.80.1`) and the Malmö bench and read-only examples (version `UNVERIFIED-1`),
  packaged copies identical; a planning/warning budget only, never pruning inside the 365-day
  window. Legacy configurations keep 10 GiB. The measurement note separates measured (15.0 GiB) from
  planned (17 GiB), leaves about 7.8 GiB of the eMMC for OS, application, logs and filesystem
  overhead, and the off-device backup runbook carries the 80-point sizing (card about 6.6 GiB of
  copies and 19.9 GiB of drill scratch, cloud about 34.4 GiB for 42 copies).
- The point count is the sum of the catalogue channels of every device, not a 24/48 formula.
- Package version 0.3.1, so the Windows launcher prepares a fresh environment.

## 0.3.0 (2026-10-13): drive run over Modbus and site time zones (ADR-0015)

The owner's answers of 2026-10-13, part of the 0.3.0 release. Software and simulation only: no drive,
manual or register map has been seen, and the installation's drive interface stays `NOT_CONFIGURED`.

### Added
- `MODBUS_RTU` drive adapters with a register map from the drive's manual (`VfdModbusMap`: unit,
  control register, run sequence of 1 to 3 values, stop value, status word and masks, optional
  frequency/current/fault code, the drive's comm-loss timeout and action, provenance, evidence).
  `DEMO` is refused on a real profile, `VERIFIED` needs CP-06 evidence, `PROPOSED` is for the bench.
- FC06 to the configured control register only: `RtuBus` refuses every other unit and register
  before sending, one frame per value, never retried or repeated. New outcome `ECHO_CONFIRMED`
  (not actuator proof).
- Simulated drive answering a DEMO map in `installation-48`, with its own comm-loss stop and the
  faults `NO_RESPONSE`, `EXCEPTION` and `IGNORES_WRITES` (twin page). Scenarios
  `drive_modbus_comm_loss` (PC-12), `drive_modbus_refused` and `drive_modbus_ignores_writes` (PC-02);
  alarms `DRIVE_COMM` and `DRIVE_STATUS_MISMATCH` (status word against the hardwired inputs).
- Real profiles: the drive's status word is polled with the modules (reads only), shown on the
  Overview and I/O pages, `DRIVE_STATUS_EDGE` and `OBSERVATION_GAP` events, `diagnostics read
  --device drive:<id>`. A bench lease may allowlist `drive:<id>`: Run/Stop buttons, a fresh status
  read before arming and before each run, refused while the drive reports a fault or not-ready.
- `configs/bench-malmo.example.yaml` (Europe/Stockholm), the CP-06 procedure and template,
  `docs/model/vfd_modbus.md` (wiring, drive settings, reading the manual, open points), ADR-0015.

### Changed
- Site time zones: `Atlantic/Canary` for the installation, the twin and the demos; a real profile
  must name its zone and an unknown zone is refused.
- All 16 DI and 16 DO stay allocated, no third relay module (ADR-0013 addendum, signal budget,
  release readiness items 1, 2, 9, 10 and 20).
- The Modbus error types live in `drivers/rtu_errors.py` so the simulation never imports pymodbus.

### Fixed (owner review of 2026-10-13)
- R1: the bench authorization (lease, allowlist, configuration version, command expiry) is checked
  again inside the bus lock immediately before every FC05 and FC06 frame, after the preflight reads
  and any wait for the bus. Expiry or revocation stops the remaining frames: nothing is sent, a run
  sequence cut after its first value is `OUTCOME_UNKNOWN` with the echoed count audited, and nothing
  is retried or compensated.

## 0.3.0 (2026-10-13): pre-hardware release

Stage 4 of the pre-hardware release, and the release itself: it contains the three 0.3.0 sections
below as well. Software and simulation only: nothing has run on the CM4, the modules, the card or the
OneDrive account, and no setting enables physical AUTO. What is still needed before each physical
step: `docs/RELEASE_READINESS.md`.

### Added
- Release ZIP (`scripts/build_release.py`): the wheel, `AquaCrop-Demo.cmd` and `aquacrop-demo.sh`,
  `README-FIRST.md`, the CM4 installer, configurations, guides, `requirements.lock`, offline
  wheelhouses for Windows (Python 3.12) and the CM4 (aarch64, Python 3.11), `RELEASE.json`
  (version, commit, `physical_verification: false`), `MANIFEST.sha256` of every file, the ZIP's own
  SHA-256, and `acceptance-manifest.json` (the owner's 45 gates with status and evidence, generated
  from `docs/ACCEPTANCE.md`). Private files (keys, databases, `rclone.conf`, `.env`) are refused.
- Desktop launcher `python -m aquacrop launch`: prerequisite checks, the full installation demo
  (`installation-48`, simulated) with its state under `%LOCALAPPDATA%\AquaCrop` (or the platform
  equivalent), a guided first administrator (no default password), a log file, the browser opened
  after the health check, and a clean stop; on a port other than 8000 it also allows its own
  loopback origin. `AquaCrop-Demo.cmd` prepares a private environment from
  the bundled wheelhouse, offline.
- Guided CM4 installer `deploy/aquacrop-cm4.py` (standard library only): `check`, `install`,
  `upgrade` (verified backup with the running version first), `rollback` (with the previous
  release's service units), `tls`, `status`;
  `--dry-run` and a staging `--root`. It verifies the release manifest, installs offline with hashes,
  switches releases atomically and never formats a disk, edits the boot configuration or creates an
  account with a default password.
- Remote access through the owner's OpenVPN: `docs/security/remote-access.md` (routes, return path,
  name, profiles), a Caddy TLS proxy on 443 with the application on 127.0.0.1, a firewall example,
  and the route test `python -m aquacrop netcheck --url https://... --ca-file ...`.
- Quarantine review before pruning: an administrator keeps quarantined records or releases them to
  retention, with a reason (`HISTORY_QUARANTINE_REVIEWED`, Settings, `POST
  /api/history/quarantine/review`). Unreviewed quarantines are never pruned; once older than the
  window they raise `HISTORY_QUARANTINE_REVIEW_DUE`.
- Cloud completion receipt (`aquacrop-<time>.receipt.json`), uploaded last after `cryptcheck` and
  checked the same way; pruning removes it first and the drill checks it. A replacement controller
  with no ledger picks the newest copy that has one.
- Replacement-controller recovery procedure (`offsite-backups.md` section 8.1), rehearsed by a test
  with the real rclone crypt remote, test-only keys and a local folder in place of OneDrive.
- Trends over a whole year: ranges up to 365 days; a point with more samples than fit is answered
  per bucket of 10 min, 1 h or 1 day with mean, min, max and sample counts, drawn as a solid mean
  with dashed min and max and labelled as aggregated. The CSV export (paged, complete) keeps every
  sample. The server keeps the buckets that can no longer change, so a refresh reads only the first
  and the newest ones, and the page fetches ranges over a day once a minute. A bucket never mixes
  simulation runs, origins, suspect stamps or recorders; without a chosen cadence basis the chart is
  drawn from the wall-clock snapshots and says how many simulation-time recorder samples it left out.
- `scripts/measure_process_workload.py` and `measure_storage.py --process-workload`: the storage
  measurement with the event and command rates of all processes running in AUTO;
  `scripts/measure_trend_cache.py` times a year chart with and without the kept buckets.
- `docs/RELEASE_READINESS.md`; acceptance rows for OP-01 to OP-06, BK-01 to BK-07, DL-01 to DL-05
  and UI-06; tests for 144 observations from three snapshots, kept configuration records, the year
  chart and paged export, no bus frame from backup, drill and restore, and the gate manifest.
- CI jobs `launcher` (Windows, macOS, Ubuntu), `release` (builds the ZIP), `release-windows`
  (offline first run from the ZIP) and `release-cm4-staging` (installer from the ZIP on ARM64).

### Changed
- Retention keeps the newest `CONFIG_LOADED` event of each distinct configuration, which defines
  the configuration and scaling that retained samples refer to, also past the window.
- `docs/installation/cm4.md` and `docs/operations/service.md` use the guided installer for install,
  upgrade and rollback; `service.md` now says this release migrates schema 2 to 3.
- The Tailscale section of `docs/security/security.md` is replaced by the OpenVPN route.
- Version 0.3.0.

## 0.3.0 (2026-10-12): process pages and installation diagram

Pre-hardware release, stage 3. Process control is simulation only; on a hardware profile the diagram
and the process pages are read-only observation and every signal stays UNBOUND.

### Added
- A clickable installation diagram on the Overview: mains, stock tanks A, B and acid with their fill
  and outlet valves and transfer pumps, the main tank with the proposed pH/EC probe point, both main
  pumps with K1/K2 on the shared drive, sectors 1 and 2, the sump and the return pumps. Every value is
  what the controllers read (value, unit, quality, age); requested state (outline) and observed
  feedback (fill) are drawn apart, and missing feedback is shown as such. Parts open their page by
  mouse or keyboard.
- Process pages: Nutrients & acid (tabs for stock A, stock B and acid, with the active recipe's pH
  target, EC target, A:B ratio or fixed volumes and dose bounds), Main tank, Main pumps, Sector 1,
  Sector 2, Drainage and Recipes. Each page has the diagram with its own parts highlighted, its
  measured and requested values, its alarms with Ack and what to do next, and a chart of the last
  hour of its signals. Each process panel shows state, phase, waiting reason, next step, owned
  equipment, outputs, last result, permissives and history, with AUTO, MANUAL, STOP and lockout
  reset, bounded manual forms and stock acknowledgements. The recipe page lists versions with
  validate, activate and roll back, and edits a new version from the server's form schema (choices
  for enumerated fields, the irrigation sequence editor, validation issues).
- On a hardware profile the Overview diagram and the process pages render read-only: parts only
  navigate, controls are replaced by the reason they are not offered, unmapped values read "not
  measured", and observations are labelled REAL or SIM (ADR-0009 addendum).
- `GET /api/plant`: the installation as the controllers see it (every logical signal with its
  binding, sample and age, the owner layout, the process summary, open alarms). Served on hardware
  profiles too, read-only and all UNBOUND (ADR-0009 addendum).
- `scripts/ui_check/`: browser checks in headless Chromium (every page, the diagram, the owner
  walkthrough with a restart, viewer refusals, offline and reconnect, phone, tablet and PC
  viewports, and the read-only pages on the hardware profile over the in-memory fake bus with zero
  write frames), run by the new CI job `ui`, which keeps screenshots as an artifact.
- Tests for the plant view on both profiles, the recipe form choices and the role table across
  every process, stock and recipe mutation including forged requests.

### Changed
- Stopping a process clears its planned next step and inhibit, so a lockout no longer shows a stale
  "next: stop S1 in ... s".
- The interim installation Overview from stage 2 is replaced by the diagram page; the Control view
  is hidden for the installation topology (its processes have their own pages).
- `/api/plant` returns full alarm occurrences (latch and reset state included) so pages can say what
  to do next.
- The page refresh no longer undoes a choice made while it was fetching (for example a fault picked on
  the twin page just before Apply), and a page change or an action that arrives during a refresh is
  drawn right after it instead of at the next tick.
- On a hardware profile a `NOT_CONFIGURED` drive adapter is shown UNBOUND like every other signal.

## 0.3.0 (2026-10-12): installation model and process controllers (ADR-0013, ADR-0014)

Pre-hardware release, stages 1 and 2. Simulation only: no physical AUTO exists and no flag enables it.

### Added
- `installation` twin topology and the `installation-48` configuration: the owner's installation
  (20,000 L main tank, stock tanks A, B and acid of 5,000 L, a sump of about 5,000 L, two main pumps
  on one shared drive selected by K1/K2, two return pumps, three transfer pumps, nine valve duties,
  13 channels with retained water and 52 hand-adjusted outlets). Facts are OWNER_CONFIRMED; every
  dynamic, switch level and span is DEMO.
- A volume ledger over every tank, channel and pipe (additions, consumption, spill, unmet demand)
  checked every scan, and a labelled synthetic pH/EC fixture (`synthetic-carbonate-ec-v1`) for
  qualitative controller tests. It is not a calibrated chemical model.
- A proposed 48-point allocation (`docs/model/signal_budget.md`): the drive run command is bound to
  a drive adapter (SIMULATED in the twin, NOT_CONFIGURED on hardware) because 16 DO are one short;
  missing feedbacks are handled explicitly and listed with owner options.
- Seven process controllers (irrigation, return, refill, acid, three stock fills) with their own
  modes, phases, permissives and fault latches; a supervisor and an equipment arbiter; separate
  acknowledge, reset and restart; bounded MANUAL operations; raw DO commands refused on the twin.
- Irrigation alternates P1/S1 and P2/S2 with the owner's changeover sequence and holds the valve and
  selection when the drive does not confirm stop. Refill latches one batch per low level and runs
  water and nutrients concurrently (fixed volumes with EC validation, or EC target keeping the A:B
  ratio). Acid lowers pH only, in bounded steps with settling and response checks. Return follows
  sump thresholds and the main-tank inhibit, with an alarm when the sump is high and the main tank
  full.
- Versioned recipes (draft, validate, activate, roll back) with units in field names; a labelled
  DEMO preset for simulation only (deliberately not the owner's illustrative example). Operator
  stock-preparation acknowledgements, invalidated by a fill or an uncovered level rise.
- Historian schema v3 (`recipes`, `recipe_activations`, `stock_readiness`), migrated after a verified
  backup. API: `/api/processes`, `/api/stocks`, `/api/recipes`; permissions `PROCESS_CONTROL`,
  `RECIPE_EDIT` (operator) and `RECIPE_ACTIVATE` (administrator, a changeable default).
- 40 installation scenarios; `demo verify --config installation-48` (42 checks); PH and PC
  acceptance tests; CI and the installed-wheel check run the installation verification.
- Docs: ADR-0013, ADR-0014, `model/installation_model.md`, `model/signal_budget.md`,
  `model/cause_and_effect.md`.

### Changed
- The simulation runtime acquires once per control period for the installation topology (the demo
  topology still scans every tick); configuration lookups are cached.

### Fixed (owner review of 2026-10-13)
- R3: irrigation checks the main level (low-low switch and the dry-run guard) during the valve lead
  and re-checks the start permissives in the scan that requests drive RUN. A loss there locks out
  (`MAIN_LOW_LOW` or `START_INHIBITED`) without the drive ever being started.
- R4: EC-target batch maxima count measured delivery, not commanded step sizes. Paths now integrate
  the meter for as long as they hold the valve (start-up flow was previously not counted); each step
  keeps commanded and measured quantities; the next step is shortened by the largest coast measured in
  the batch; a measured total above a maximum (tolerance 0 L) is an overrun: dosing stops, alarm
  `REFILL_EC_LIMIT_EXCEEDED`, batch `OUT_OF_SPEC`.
- R2: an unusable EC probe during an active EC-target A/B step stops both nutrient paths in the same
  scan (previously only after the step completed), records the measured delivery and the reason, and
  no further step runs in that batch even if the probe returns. The batch keeps its first stop reason.
  Second review: the EC check runs before the paths advance, so a step whose valve is still opening
  never sends its pump ON (the audit showed ON then OFF in the same scan); regressions read the
  command audit.

## 0.3.0 (2026-10-12): timestamp quarantine (ADR-0011) and off-device backups (ADR-0012)

### Added
- Timestamp quarantine: when records are stamped later than the clock (a clock that ran ahead and
  was corrected), Settings proposes which records and why; an administrator who checked the date
  quarantines them (`HISTORY_TIMESTAMPS_QUARANTINED`). Records, values and stamps are kept and
  flagged; recording resumes at once in a new clock epoch (`slot/60s/e<n>/<label>`); retention stays
  held until the date and cutoff are confirmed separately. No schema migration.
- Suspect stamps are visible everywhere: a dashed grey series with rings in trends,
  `timestamp_suspect`/`quarantine_id` (and `snapshot_seq`/`seq`) in the sample and audit exports,
  `SUSPECT TIME` on events, alarm occurrences and commands.
- Clock events carry a watermark (the newest id of each table) so a quarantine maps exactly to records.
- Off-device backups, **not activated**: `offsite_backup` configuration; `offsite enroll-card`,
  `check`, `backup`, `upload`, `plan`, `drill`, `status`; SD-card identity checks (mount, UUID,
  read-write, enrolment marker, never the eMMC); daily verified backups on the card; encrypted
  copies with rclone `copyto` through a crypt remote, verified with `cryptcheck`; a persistent
  upload queue with backoff; monthly restore drills from the cloud copy into SD scratch space with a
  10 GiB-plus-headroom preflight; a copy policy (proposed 7 local, 30 daily + 12 monthly in the
  cloud) that prunes only after an administrator confirms it; `OFFSITE_*` events and the
  `OFFSITE_BACKUP` alarm; a Settings card; systemd units and an fstab example;
  `docs/operations/offsite-backups.md` (authorization, credentials, recovery keys, activation).
- CI installs rclone on Linux and requires the real rclone round trip there.
- ADR-0008, ADR-0011 and ADR-0012 accepted by the owner (2026-10-12 20:04 UTC).
- Review fixes: a quarantine whose records retention deleted is retired (`HISTORY_QUARANTINE_RETIRED`)
  so reused ids are neither flagged nor deleted; alarm occurrences refreshed by the wrong clock are
  quarantined too; activation requires the cloud remote; a read-back error from the card is recorded
  as `VERIFY_FAILED`.
- Owner choices for off-device backups recorded in `docs/operations/offsite-backups.md`: 64 GB ext4
  card, the shared folder in a colleague's personal OneDrive (with setup steps for a shared folder),
  a sealed printed key copy, and the 7 / 30 + 12 copy policy. Still not activated.

### Changed
- A confirmed quarantine supersedes the clock hold it answers (`TIMESTAMPS_QUARANTINED`); a hold
  that was in force at shutdown is restored at start.

## 0.3.0 (2026-10-12): historian at 60 s, rolling 365 days (ADR-0008, revised after review)

### Changed
- History is a complete snapshot of every point every 60 s of wall time (`historian.recording_interval_s`,
  60 or 300; 60 stays active until the owner asks for 300), in real and simulation profiles alike;
  a simulation's run id and simulated time are stored with each snapshot. Bus polling (3 s), alarm
  evaluation, edges and the dashboard keep their timing, and polls no longer write history rows.
- The recorder schedules on the monotonic clock and labels each snapshot with its wall-clock
  boundary (hh:mm:00; hh:00, hh:05, ... for 300 s). A label is written at most once per database,
  checked inside the writing transaction, across restarts and clock corrections. A clock set back
  holds snapshots until it passes the newest label (`HISTORY_CLOCK_BEHIND`); missed labels are
  logged with their cause and never back-filled.
- Readings older than three polls are recorded as `STALE` (null value, last good value kept);
  points not yet acquired are `UNAVAILABLE`; a paused simulation's frozen state is `STALE`.
- Alarm transitions (raise, clear, ack, reset) are written immediately; DI/DO edges and commands
  already were. New `CONFIG_LOADED` / `CONFIG_CHANGED` start events with a diff.
- Retention is a rolling 365-day window applied hourly in bounded runs (5,000 rows per transaction,
  100 transactions per run, waits while the WAL is large). It deletes only while the clock is
  trusted (restart within 1 h, no step over 5 min); otherwise it holds (`RETENTION_HELD`) until an
  administrator confirms the proposed cutoff (`RETENTION_CLOCK_CONFIRMED`). Acknowledging the alarm
  no longer releases it. No size limit deletes history inside the window.
- The 2 GiB demo budget is replaced by a 10 GiB database budget and free-space thresholds
  (warning 4 GiB, reserve 2 GiB, snapshot floor 512 MiB) with defined alarms and effects.
- Backups are gzip-compressed SQL dumps of one consistent snapshot (`*.sql.gz`) with the schema
  objects and versions in the manifest. They are verified before an atomic publish, removed cleanly
  on failure (earlier backups kept), and abort below the reserve, above a 1 GiB WAL or after 3 h.
  Earlier `.sqlite3` backups still verify and restore.
- Status reads used by the acquisition loop use their own database connection, so a long export
  never delays a poll cycle.

### Added
- WAL size, checkpoint progress and a WAL warning (`wal_warning_bytes`) in the storage report,
  Overview and Settings.
- Restore drill: `backup verify --full` restores to a separate scratch directory and checks
  integrity, foreign keys, schema, counts, fingerprints and the application's own start-up and
  queries, with a JSON report; monthly `aquacrop-restore-drill.timer`; Settings shows the drill
  and the off-device copy as not configured.
- Settings: retention hold, proposed cutoff and an administrator release; snapshots not written
  during a low-disk pause; the DEMO no-deletion note.
- Optional simulation-time series (`historian.sim_time_recording_s`, off by default), labelled
  separately and counted separately.
- `aquacrop storage report` (growth, 365-day projection, thresholds, database and WAL separately,
  series and registers); `scripts/measure_storage.py` (commands, alarms, faults, environment) and
  `scripts/stress_concurrency.py` (WAL and responsiveness under long readers).

### Fixed
- Login and request rate limits use a sliding 60 s window. The fixed per-minute window allowed up
  to twice the limit across a minute boundary, and made the login test fail when it straddled one.
- The capacity check on every poll read the oldest and newest sample times with one full index
  scan (2.4 s on a one-year database), which kept the WAL from resetting and deferred retention;
  it now uses two indexed lookups. Found by the concurrency stress run.
- Text files are read and written as UTF-8 on every platform (Windows CI failure on `main`), and
  backups keep CR/LF inside text values unchanged.

### Unchanged
- Database schema (version 2) and existing history: no migration. No hardware change.

## 0.2.0.dev0 (2026-10-11)

Replaces the v0.1 prototype (kept unchanged in `legacy/`, SHA-256 `fa118eb2…6dd8`).

### Added
- Generic inventory: any number of Relay (D) and AI 8CH modules on any number of buses; stable
  point IDs (`relay_A.DO1`); `demo-48` and `demo-24` fixtures; strict profile validation.
- Causal process twin with a closed water ledger, actuator dynamics and faults, 17 scenario
  presets, pause/step/speed/replay, deterministic seeds.
- Two-sector simulation AUTO with prechecks, flow verification, latched lockout and deliberate
  recovery.
- Persistent alarm occurrences (rule + target identity, ack ≠ clear ≠ reset, latch, hysteresis,
  debounce) restored after restart.
- SQLite historian: full provenance on every sample, every point every cycle, batched writer,
  migrations (backup first), backups with manifests, archive and retention, storage alarms.
- Authenticated API and seven-view UI: Argon2id accounts, roles, commissioning flag, server-side
  sessions, CSRF and origin checks, rate limits, strict CSP, stale-data handling.
- Read-only Modbus RTU on a pinned pymodbus 3.11.3 adapter with strict reply validation,
  per-device health and backoff, physical edge events, and `diagnostics read`.
- Bench write lease (commissioning only, isolated bench): exact allowlist, typed confirmation,
  mode preflight, FC05 + FC01 readback, never restored after restart.
- v0.1 database import (`migrate-v01`) and `python -m greenhouse` compatibility launcher.
- Hash-locked requirements, wheelhouse script, installed-wheel check, systemd units, CI workflow.
- Documentation: model, operator, installation (desktop, CM4), security, operations, vendor
  protocol record, ADRs, acceptance matrix, milestone 5 procedures and unsigned templates.

### Not in this release
Physical AUTO / live control, HYBRID_SHADOW, FC0F, production topology, calibration and recipes.
