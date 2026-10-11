# Handover: AquaCrop platform 0.2.0.dev0 (2026-10-11)

## What works (software, verified on a desktop)

- **Simulation**: 48-point (and 24-point) desktop demo with a causal water model, two-sector
  AUTO, faults and lockout, 17 scenario presets, pause/step/speed/replay, persistent alarms,
  full history with provenance, seven-view browser UI behind individual accounts.
- **Real modules, read-only**: `serve` runs a `HARDWARE_READ_ONLY` installation, polling each
  module independently with health and backoff; `diagnostics read` inspects modules without
  writing. Tested only against a simulated serial bus under the real pymodbus client.
- **Bench writes**: a commissioning user can arm a short, allowlisted, session-bound lease on an
  isolated bench; every write is preceded by a mode check and followed by a readback.
- **Operations**: backups, verified restore to a new instance, migrations (backup first), v0.1
  import, archive/retention, storage alarms, systemd units, hash-locked installs, offline wheelhouse.

## What changed from v0.1

All 12 findings in the brief were reproduced on the original code and are corrected (table in
`architecture/v0_1_findings.md`). The prototype is kept unchanged in `legacy/`. `python -m
greenhouse` still starts the simulation and refuses the old hardware/bench environment switches.

## Test results

183 tests pass on Python 3.13, and the suite is also green on 3.11 and 3.12. `demo verify` gives
21/21 on both fixtures, and the installed wheel passes 13/13 from outside the repository. Item by
item: `ACCEPTANCE.md`. Windows, macOS and ARM64 have **not run**: the CI workflow needs a GitHub
repository.

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

1. **GitHub repository** for the code (then CI runs on Windows/macOS/ARM64).
2. **Storage**: the demo retention (7 days) does not fit the demo budget (2 GiB) at 48 points / 3 s
   (measured ≈ 445 MiB/day). Pick a budget, a retention, a slower cadence, or schema compaction
   work (ADR-0008).
3. Production facts still `TBD`: hydraulic topology, wiring/physical mapping (also unblocks shadow
   mode), VFD interface, probes and calibration, recipes, safe-state/cause-and-effect matrix,
   physical alarm severities, role assignments and remote rights, backup destination and
   retention policy, OS update policy.
4. The supporting files named in the brief (`ACCEPTANCE_TEST_PLAN.md` and others) were never
   supplied. The acceptance IDs here are this project's own.

## Physical tests still required (milestone 5, none performed)

CP-00 platform record (OS image, UART/tty mapping, RTC, CM4 performance); CP-01 read-only
identification of each module; CP-02 AI stimulus; CP-03 DI stimulus; CP-04 DO on dummy loads under
a bench lease with teardown; CP-05 disconnect/restart/power-loss with a passive bus monitor proving
zero write frames. Each needs the hardware on an isolated bench and your written authorization of
that setup. Procedures and unsigned templates: `commissioning/`.

Nothing in this release is a safety function, and passing these tests does not make the system
ready for production or live control.
