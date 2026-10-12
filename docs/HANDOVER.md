# Handover: AquaCrop platform 0.2.0.dev0 (2026-10-11, historian update and review revisions 2026-10-12)

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

## What changed from v0.1

All 12 findings in the brief were reproduced on the original code and are corrected (table in
`architecture/v0_1_findings.md`). The prototype is kept unchanged in `legacy/`. `python -m
greenhouse` still starts the simulation and refuses the old hardware/bench environment switches.

## Test results

252 tests pass on Python 3.11, 3.12 and 3.13. `demo verify` gives 21/21 on both fixtures, and
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

1. **Accept the revised ADR-0008** (R1 to R5 of the owner's review answered; status "proposed").
2. **Off-device backup destination**: the device keeps 7 compressed daily backups, which do not
   protect against losing the eMMC. The same destination (or a USB disk) is needed as scratch
   space for the monthly restore drill, which cannot run on the eMMC beside a full year.
3. **A clock set back**: snapshots are held until the clock passes the newest stored label (no
   duplicates, nothing deleted). After a large backward correction that can mean a long gap; an
   administrator-authorized resume path can be added if wanted.
4. Production facts still `TBD`: hydraulic topology, wiring/physical mapping (also unblocks shadow
   mode), VFD interface, probes and calibration, recipes, safe-state/cause-and-effect matrix,
   physical alarm severities, role assignments and remote rights, OS update policy.
5. The supporting files named in the brief (`ACCEPTANCE_TEST_PLAN.md` and others) were never
   supplied. The acceptance IDs here are this project's own.

## Physical tests still required (milestone 5, none performed)

CP-00 platform record (OS image, UART/tty mapping, RTC, CM4 performance, storage growth, WAL
behaviour, backup time and free space on the eMMC); CP-01 read-only
identification of each module; CP-02 AI stimulus; CP-03 DI stimulus; CP-04 DO on dummy loads under
a bench lease with teardown; CP-05 disconnect/restart/power-loss with a passive bus monitor proving
zero write frames. Each needs the hardware on an isolated bench and your written authorization of
that setup. Procedures and unsigned templates: `commissioning/`.

Nothing in this release is a safety function, and passing these tests does not make the system
ready for production or live control.
