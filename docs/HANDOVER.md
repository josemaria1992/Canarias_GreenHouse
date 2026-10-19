# Handover: AquaCrop platform 0.3.4, the owner demo release (2026-10-19)

**0.3.4, owner feedback on 0.3.3 and reviewer corrections (2026-10-19):** O-01 to O-05 (white link
text, consistent equipment graphics with flow direction and tank trends, plain Digital Twin examples,
operating equipment and asking for readings from the diagram, sector data on the Overview) and R33-01 to
R33-03 (pinned Stop per tank tab, recipe errors at their field, neutral reset wording). Equipment is
operated only through its process's existing bounded operations and the real controller; real equipment
profiles stay read only. Per item: `operator/owner-tour/issues.md`. Owner results NOT RUN.

**0.3.3, corrections for a first-time operator (2026-10-19):** the owner reviewed 0.3.2 as a first-time
operator (F-01 to F-09, CHANGES REQUESTED). The Windows ZIP now carries its own Python (`runtime/`,
CPython 3.14.8), the first administrator is guided, the pages use the owner's plain names (process card
and Stop first, problems with their next step and a link to the process, History, fertiliser tank
preparation, narrow screens, Getting started), and the tour has a result form that saves only by
download. What changed per finding: `operator/owner-tour/issues.md`. Display, guidance and Windows start
only; internal ids, roles, algorithms and limits unchanged. Owner results NOT RUN.

**0.3.2, owner demo and acceptance tour (2026-10-13):** the release ZIP is what the owner runs on a
Windows PC (`README-FIRST.md` inside it). The normal demo is `installation-80` (six modules, 80
points, SIMULATED); `installation-48`, `demo-48` and `demo-24` are legacy test fixtures. The
owner's acceptance tour is `operator/walkthrough.md` (UAT-01 to UAT-16) with an illustrated offline
guide (`owner-tour/index.html` in the ZIP), an editable checklist (`owner-checklist.csv`) and a
feedback template. Every owner result is NOT RUN until the owner tests it; Claude's rehearsal of the
tour and CI's checks are separate records, never owner results. The launcher names each environment
after the exact program file, so an earlier 0.3.1 environment can no longer run a newer build, and
Settings, *Versions* shows the release, source commit and whether the installed program is exactly
the one in the folder. No control algorithm, production mapping, retention, cadence or budget
changed. Delivery issues found and fixed while rehearsing the tour: `operator/owner-tour/issues.md`.

**Owner inventory update (2026-10-13):** two IO 8CH (SKU 26244) added, for 80 points across six
modules; the launcher default is `installation-80` and the 32 added channels are testable but stay
unassigned by owner decision (`model/digital_io_8ch.md`, ADR-0016). The owner review findings R1-R4 of the same day
are fixed (`ACCEPTANCE.md`, "Owner review of 13 October 2026"). 48-point sizing figures below are
kept for the record. 80 points measured on the desktop: 15.0 GiB for a year with every process in
AUTO. The owner set a 17 GiB planning/warning database budget for 80 points, with backups and
restore drills on the SD card (warning only, never deletes inside the 365-day window;
`operations/measurements/2026-10-13-measurements.md`, RELEASE_READINESS item 24).

This release is software and simulation only. What is still needed before each physical step, item
by item, is in `RELEASE_READINESS.md`; the evidence for every gate is in `ACCEPTANCE.md`.

## What works (software, verified on a desktop)

- **Simulation**: the 80-point `installation-80` demo (the 48- and 24-point fixtures are legacy) with a causal water model, two-sector
  AUTO, faults and lockout, 17 scenario presets, pause/step/speed/replay, persistent alarms,
  full history with provenance, seven-view browser UI behind individual accounts.
- **Real modules, read-only**: `serve` runs a `HARDWARE_READ_ONLY` installation, polling each
  module independently with health and backoff; `diagnostics read` inspects modules without
  writing. Tested only against a simulated serial bus under the real pymodbus client.
- **Bench writes**: a commissioning user can arm a short, allowlisted, session-bound lease on an
  isolated bench; every write is preceded by a mode check and followed by a readback.
- **Operations**: compressed verified backups, restore to a new instance, migrations (backup
  first), v0.1 import, archives, storage alarms, systemd units, hash-locked installs, offline
  wheelhouse.
- **History (2026-10-12)**: a complete snapshot of every point every 60 s of wall time (simulation
  included), each label stored once; a rolling 365-day window deleted only while the clock is
  trusted, otherwise held until an administrator confirms the cutoff; edges, alarm transitions,
  commands and configuration changes recorded when they happen; WAL, capacity, snapshot gaps,
  retention and backup state visible in the UI and `storage report`; backups published only after
  verification, with a restore drill. Measured 8.11 GiB for one year of 48 points under a
  workload with commands, alarms and faults (ADR-0008, revised after the owner's review).
- **Timestamp quarantine (2026-10-12)**: after a clock correction an administrator can quarantine
  records stamped later than the clock. Records, values and stamps are kept and flagged
  `SUSPECT` in trends, exports, events, alarms and commands; recording resumes at once in a new
  clock epoch without duplicates; retention stays held until the date and cutoff are confirmed
  separately (ADR-0011).
- **Off-device backups (2026-10-12, not activated)**: daily backups to a verified SD card (never the
  eMMC as fallback), encrypted copies to OneDrive with rclone (copy and cryptcheck, no mirror sync),
  a persistent upload queue that retries when the connection returns, monthly restore drills from
  the cloud copy into SD scratch space, and pruning only after an administrator confirms the copy
  policy. Shipped disabled until the card and the account/folder are confirmed (ADR-0012,
  `operations/offsite-backups.md`).

- **Installation model and process controllers (2026-10-12, pre-hardware stages 1 and 2,
  simulation only)**: `installation-48` simulates the confirmed installation (20,000 L main tank,
  stock tanks A, B and acid, the sump, two main pumps on one shared drive, return and transfer
  pumps, 13 channels with retained water and hand-set outlets) with a volume ledger and a labelled
  synthetic pH/EC fixture. Seven process controllers run together under a supervisor and an
  equipment arbiter: irrigation alternates P1/S1 and P2/S2 with the verified changeover, refill
  latches one batch per low level and runs water and nutrients concurrently, acid lowers pH in
  bounded steps, return follows the sump, and stock tanks fill on request. Recipes are versioned and
  activated by an administrator; stock preparation is an operator acknowledgement. 40 scenarios
  cover normal operation and faults (ADR-0013, ADR-0014, `model/installation_model.md`,
  `model/signal_budget.md`, `model/cause_and_effect.md`). There is no physical AUTO.

- **Process pages and installation diagram (2026-10-12, pre-hardware stage 3)**: the Overview of
  `installation-48` is a clickable diagram of the installation showing what the controllers read
  (value, unit, quality, age), with requested and observed state drawn apart. Nutrients & acid, Main
  tank, Main pumps, Sector 1, Sector 2, Drainage and Recipes pages run each process (AUTO, MANUAL,
  STOP, lockout reset, bounded manual forms, stock acknowledgements, page alarms, a last-hour trend)
  and edit, validate, activate and roll back recipe versions. On a hardware profile the same pages
  are read-only observation and every signal is UNBOUND until a mapping is approved.
  `scripts/ui_check/run_ui_check.py` repeats the owner walkthrough in headless Chromium.

- **Release, installers and operations (2026-10-13, pre-hardware stage 4)**: a release ZIP with a
  SHA-256 manifest, the wheel, offline wheelhouses for Windows and the CM4 and a machine-readable
  acceptance manifest (`scripts/build_release.py`); a Windows demo launcher that prepares itself
  offline, guides the first administrator and keeps its state between runs (`AquaCrop-Demo.cmd`,
  `python -m aquacrop launch`); a guided CM4 installer with checksum verification, pre-upgrade
  backup, atomic switch and rollback (`deploy/aquacrop-cm4.py`); access from home through the
  owner's OpenVPN to a TLS proxy on the controller, with `aquacrop netcheck`
  (`security/remote-access.md`); a review date for quarantined history, which is kept until an
  administrator decides; year-long trend ranges drawn from per-bucket minimum, mean and maximum;
  configuration records kept while history refers to them; a completion receipt next to every
  cloud copy; and a rehearsed recovery on a replacement controller with only the escrowed keys
  (`operations/offsite-backups.md` 8.1).

## What changed from v0.1

All 12 findings in the brief were reproduced on the original code and are corrected (table in
`architecture/v0_1_findings.md`). The prototype is kept unchanged in `legacy/`. `python -m
greenhouse` still starts the simulation and refuses the old hardware/bench environment switches.

## Test results

The figures in this section are those of 0.3.0; the results of 0.3.3 (tests, `demo verify`, the
browser check, the installed wheel, the release check, the Windows cold start, restart and upgrade
over 0.3.1, and the CM4 staging) are in the verification report delivered with the ZIP.

410 tests pass on Python 3.11, 3.12 and 3.13. Two of them run the real rclone binary against a
local encrypted remote: the round trip and the replacement-controller recovery; they are skipped where
rclone is not installed and required on CI's Linux jobs. `demo verify` gives 21/21 on both demo
fixtures and 45/45 on `installation-48`. The browser check (`scripts/ui_check/`) passes 58/58 in
headless Chromium, and the wheel from the built release ZIP passes 16/16 from outside the
repository. A simulated 400-day history at 60 s with the event and command rates of every process
running in AUTO measured the one-year size, the WAL, the bytes written, the compressed backup and
the restore drill (OP-06, desktop figures, `operations/measurements/2026-10-13-measurements.md`).
GitHub CI runs Ubuntu, Windows, macOS and ARM64 on every pull request, builds the release ZIP, runs
its Windows launcher offline and the CM4 installer from it in a staging root on ARM64. Item by
item: `ACCEPTANCE.md`.

## Launch (desktop)

From the release ZIP on Windows: double-click `AquaCrop-Demo.cmd`; the ZIP carries its own Python
(`runtime/`, CPython 3.14.8 for 64-bit Windows), so nothing is installed first (`README-FIRST.md`,
`installation/desktop.md`). It serves `installation-80`. From a
source checkout:

```text
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m aquacrop users bootstrap --config installation-80 --username admin --generate
.venv/bin/python -m aquacrop serve --config installation-80 --host 127.0.0.1 --port 8000
```

On Windows, use `py -m venv .venv` and `.venv\Scripts\python.exe` instead. More detail is in
`installation/desktop.md`.

## Decisions needed from the owner

Resolved on 2026-10-12: the repository is `github.com/josemaria1992/Canarias_GreenHouse`
(ADR-0007), and history is 60 s snapshots kept for a rolling 365 days within the existing 32 GB
eMMC and SQLite database (ADR-0008).

ADR-0008 (revised), ADR-0011 (timestamp quarantine) and ADR-0012 (SD card and OneDrive) were
accepted by the owner on 2026-10-12 20:04 UTC. The pre-hardware brief of 2026-10-12 settled the
installation (tanks, pumps, valves, channels, modules, AUTO alternation, pH by acid only, roles,
OpenVPN); the full list of physical configuration still open is section 3 of `RELEASE_READINESS.md`.

1. **Activate off-device backups**: the owner chose a 64 GB ext4 card, the shared folder
   `AQUACROP_DATOS_CANARIAS` (a colleague's personal OneDrive), a sealed printed key copy and the
   7 / 30 + 12 copy policy; rclone signs in with the owner's personal Microsoft account; the folder
   owner's plan has 1 TB (2026-10-12). Left: the colleague shares the folder by name with edit
   rights, then card enrolment and rclone setup on the controller (on site, with the hardware).
   `operations/offsite-backups.md` sections 1 and 3.1.
2. **Backward clock policy**: snapshots are still held while the clock is behind trusted labels;
   future-dated records are now resolved by the administrator's quarantine (ADR-0011).
3. Production facts still `TBD`: wiring/physical mapping (also unblocks shadow mode), VFD
   register map (from its manual, CP-06), probes and calibration, production recipe values, mixing method, physical alarm
   severities, role assignments and remote rights, OS update policy. The installation's sizes and
   equipment are confirmed (2026-10-12).
4. **Point allocation (ADR-0013, ADR-0016, `model/signal_budget.md`)**: decided 2026-10-13: all 16
   relay-module DI and DO in use, no third relay module, and the 17th output (the shared drive's run) over the drive's
   Modbus interface (ADR-0015, `model/vfd_modbus.md`). Simulation and bench software are done; the
   real interface stays `NOT_CONFIGURED` until the drive's manual is supplied and CP-06 passes on the
   Malmö bench. Later that day the owner added two IO 8CH modules (80 points) and decided that their
   16 DI and 16 transistor DO all stay unassigned test points (DO1 included) until hardware and
   wiring are verified; the table in `model/signal_budget.md` is a proposal for later, not fitted
   devices. Still to confirm: the relay/analog per-channel table and the cause-and-effect table
   (`model/cause_and_effect.md`).
5. **Site time zone**: decided 2026-10-13: `Atlantic/Canary` for the installation (and the twin and
   demos), `Europe/Stockholm` for the Malmö test bench (`configs/bench-malmo.example.yaml`).
6. The original brief's `ACCEPTANCE_TEST_PLAN.md` was never supplied, so the milestone 1-4 IDs are
   this project's own. The pre-hardware gates (PH, PC and the later UI, OP, BK, DL) come from the
   owner's `PRE_HARDWARE_ACCEPTANCE_PLAN.md` of 12 Oct 2026.

## Physical tests still required (milestone 5, none performed)

CP-00 platform record (OS image, UART/tty mapping, RTC, CM4 performance, storage growth, WAL
behaviour, backup time and free space on the eMMC, the SD card in its USB reader, the first
OneDrive upload and drill, the recovery-key escrow test); CP-01 read-only
identification of each module; CP-02 AI stimulus; CP-03 DI stimulus; CP-04 DO on dummy loads under
a bench lease with teardown; CP-05 disconnect/restart/power-loss with a passive bus monitor proving
zero write frames; CP-06 the drive's Modbus interface (status bits, run/stop under a lease, comm loss,
power cycle) with the motor disconnected. Each needs the hardware on an isolated bench and your written authorization of
that setup. Procedures and unsigned templates: `commissioning/`.

Nothing in this release is a safety function, and passing these tests does not make the system
ready for production or live control.
