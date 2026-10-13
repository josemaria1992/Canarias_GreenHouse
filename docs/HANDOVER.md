# Handover: AquaCrop platform 0.2.0.dev0 (2026-10-11; historian, quarantine, off-device backups and the installation model 2026-10-12)

## What works (software, verified on a desktop)

- **Simulation**: 48-point (and 24-point) desktop demo with a causal water model, two-sector
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

## What changed from v0.1

All 12 findings in the brief were reproduced on the original code and are corrected (table in
`architecture/v0_1_findings.md`). The prototype is kept unchanged in `legacy/`. `python -m
greenhouse` still starts the simulation and refuses the old hardware/bench environment switches.

## Test results

271 tests pass on Python 3.11, 3.12 and 3.13 (one of them runs the real rclone binary against a
local encrypted remote; it is skipped where rclone is not installed and required on CI's Linux jobs). `demo verify` gives 21/21 on both fixtures, and
the installed wheel passes 13/13 from outside the repository. A simulated 400-day history at 60 s
with commands, alarms and device faults measured the one-year size, the rolling window, the WAL,
the compressed backup and the restore drill; a concurrency stress run measured the WAL and
responsiveness during a backup, year-range reads and a retention catch-up (desktop figures).
GitHub CI runs Ubuntu, Windows, macOS and ARM64 on every pull request. Item by item:
`ACCEPTANCE.md`.

## Launch (desktop)

```text
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m aquacrop users bootstrap --config demo-48 --username admin --generate
.venv/bin/python -m aquacrop serve --config demo-48 --host 127.0.0.1 --port 8000
```

On Windows, use `py -m venv .venv` and `.venv\Scripts\python.exe` instead. More detail is in
`installation/desktop.md`.

## Decisions needed from the owner

Resolved on 2026-10-12: the repository is `github.com/josemaria1992/Canarias_GreenHouse`
(ADR-0007), and history is 60 s snapshots kept for a rolling 365 days within the existing 32 GB
eMMC and SQLite database (ADR-0008).

ADR-0008 (revised), ADR-0011 (timestamp quarantine) and ADR-0012 (SD card and OneDrive) were
accepted by the owner on 2026-10-12 20:04 UTC.

1. **Activate off-device backups**: the owner chose a 64 GB ext4 card, the shared folder
   `AQUACROP_DATOS_CANARIAS` (a colleague's personal OneDrive), a sealed printed key copy and the
   7 / 30 + 12 copy policy; rclone signs in with the owner's personal Microsoft account; the folder
   owner's plan has 1 TB (2026-10-12). Left: the colleague shares the folder by name with edit
   rights, then card enrolment and rclone setup on the controller (on site, with the hardware).
   `operations/offsite-backups.md` sections 1 and 3.1.
2. **Backward clock policy**: snapshots are still held while the clock is behind trusted labels;
   future-dated records are now resolved by the administrator's quarantine (ADR-0011).
3. Production facts still `TBD`: wiring/physical mapping (also unblocks shadow mode), VFD
   interface, probes and calibration, production recipe values, mixing method, physical alarm
   severities, role assignments and remote rights, OS update policy. The installation's sizes and
   equipment are confirmed (2026-10-12).
4. **48-point allocation (ADR-0013, `model/signal_budget.md`)**: the processes need 17 outputs and
   the modules have 16. Choose one: confirm VFD control over Modbus or a drive terminal (the drive
   run command then goes through the drive adapter), or add a third relay module (drive run, sector
   valve closed limits and stock valve feedback). Confirm or change the proposed allocation and the
   cause-and-effect table (`model/cause_and_effect.md`).
5. **Site time zone**: the configurations say `Europe/Stockholm` (the company office in Malmö). If the
   controller sits in the Canary Islands it should be `Atlantic/Canary`. Not changed; please confirm.
6. The original brief's `ACCEPTANCE_TEST_PLAN.md` was never supplied, so the milestone 1-4 IDs are
   this project's own. The pre-hardware gates (PH, PC and the later UI, OP, BK, DL) come from the
   owner's `PRE_HARDWARE_ACCEPTANCE_PLAN.md` of 12 Oct 2026.

## Physical tests still required (milestone 5, none performed)

CP-00 platform record (OS image, UART/tty mapping, RTC, CM4 performance, storage growth, WAL
behaviour, backup time and free space on the eMMC, the SD card in its USB reader, the first
OneDrive upload and drill, the recovery-key escrow test); CP-01 read-only
identification of each module; CP-02 AI stimulus; CP-03 DI stimulus; CP-04 DO on dummy loads under
a bench lease with teardown; CP-05 disconnect/restart/power-loss with a passive bus monitor proving
zero write frames. Each needs the hardware on an isolated bench and your written authorization of
that setup. Procedures and unsigned templates: `commissioning/`.

Nothing in this release is a safety function, and passing these tests does not make the system
ready for production or live control.
