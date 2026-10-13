# AquaCrop greenhouse platform

Desktop simulation, causal process twin, historian and browser UI for the AquaCrop greenhouse
(Malmö), plus read-only Modbus RTU acquisition and a guarded bench-write lease for isolated
commissioning on a Raspberry Pi CM4.

**What this is not.** It is not a commissioned control system. Physical AUTO and
`COMMISSIONED_LIVE_CONTROL` do not exist in this release. The demo model, its parameters and its
tag map are synthetic proposals. Production topology, wiring, VFD interface, probes, calibration,
recipes and the safe-state matrix are `TBD — requires operator confirmation`. Software is not a
substitute for emergency stops, motor protection, dry-run trips or independent overfill protection.

## Quick start (desktop simulation)

The owner's updated inventory is **80 points**: two Relay (D) 26517, two AI 8CH 25821 and two
Modbus RTU IO 8CH 26244. The added boards have transistor sink outputs, distinct from relay contacts.
Run `python -m aquacrop launch` for the complete `installation-80` desktop fixture. Legacy 24/48-point
fixtures remain regression examples. Physical profiles include all six modules and start read-only.
See `docs/model/digital_io_8ch.md` for protocol, commissioning and capacity follow-up.

Python 3.11–3.13. No PowerShell activation is needed on Windows.

| OS | Commands |
|---|---|
| Windows | `py -m venv .venv` · `.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock` · `.venv\Scripts\python.exe -m pip install --no-deps .` |
| macOS / Linux | `python3 -m venv .venv` · `.venv/bin/python -m pip install --require-hashes -r requirements.lock` · `.venv/bin/python -m pip install --no-deps .` |

Then (shown with `python`; use the venv interpreter path from the table):

```text
python -m aquacrop config validate --config demo-48
python -m aquacrop users bootstrap --config demo-48 --username admin --generate   # one-time password, must be changed
python -m aquacrop serve --config demo-48 --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/> and sign in. `demo-48` and `demo-24` are packaged fixtures, so the
names work from an installed wheel; a file path works too (`configs/demo-48.yaml`).

Headless verification (deterministic, never opens a serial port, nonzero exit on any failure):

```text
python -m aquacrop demo verify --config demo-48 --seed 42
```

## Commands

| Command | Purpose |
|---|---|
| `config validate --config C` | Validate an inventory/profile file |
| `users bootstrap --config C [--generate \| --password-stdin]` | First admin; no default credentials exist |
| `serve --config C [--host 127.0.0.1] [--port 8000]` | Serves `SIMULATED` or `HARDWARE_READ_ONLY`; refuses non-loopback plain HTTP |
| `demo verify --config C --seed 42` | Every scenario preset, determinism, replay, no-serial check |
| `diagnostics read --config C --device relay_A --cycles 5` | Read-only module inspection on a real bus |
| `backup create --config C --destination D [--keep 7]` / `backup verify --backup B [--full]` / `backup restore ...` | Compressed consistent snapshot with manifest, verified before old copies are rotated; restore to a new instance first |
| `storage report --config C` | Measured growth, 365-day projection and free-space thresholds (read-only, server may run) |
| `archive create ...` / `archive verify ...` / `retention apply [--dry-run]` | Checksummed exports; the rolling 365-day window (the server also applies it hourly) |
| `migrate-v01 --config C --legacy-db greenhouse.sqlite3` | Backup-first import of a v0.1 database |
| `python -m greenhouse` | v0.1 compatibility launcher (simulation only) |

All commands take `--state-dir` (default `var`) for the database, secret key, locks and backups.

## Documentation

- `docs/IMPLEMENTATION_PLAN.md`, `docs/ACCEPTANCE.md`: plan and acceptance matrix (PASS/FAIL/BLOCKED/NOT RUN)
- `docs/model/`: twin equations, assumptions, AUTO sequence
- `docs/operator/`: walkthrough of the seven views
- `docs/installation/`: desktop OS setup, offline wheelhouse, CM4 eMMC/UART guide
- `docs/security/`: accounts, sessions, TLS proxy, Tailscale Serve
- `docs/operations/`: service unit, backups, retention, storage sizing, upgrade and rollback
- `docs/commissioning/`: milestone 5 procedures and unsigned evidence templates
- `docs/vendor_protocol.md`: Modbus register sources and their limits
- `docs/adr/`: decisions that depart from or interpret the brief
- `docs/architecture/v0_1_findings.md`: audit of the v0.1 prototype
