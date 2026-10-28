# AGENTS.md: AquaCrop greenhouse platform (0.7.x; 0.7.1)

Guidance for Codex when reviewing this repository, including the stop-time review of a session's
changes. Read this first; judge every change against the whole system, not the file it touches.

## What this is

Desktop simulation, causal process twin, historian and browser UI for a greenhouse (Malmö), plus
read-only Modbus RTU acquisition and a guarded bench-write lease on a Raspberry Pi CM4. It is **not**
a commissioned control system: physical AUTO and `COMMISSIONED_LIVE_CONTROL` do not exist. Hardware
topology, wiring, VFD interface, calibration, recipes and the safe-state matrix are
`TBD — requires operator confirmation`; never invent them.

## Architecture (src/aquacrop)

| Package | Role |
|---|---|
| `config/` | Inventory/profile schema (`schema.py`) and packaged catalog; one validated source of truth for modules, points and profiles |
| `domain/` | Shared model types used by every other layer |
| `io/` | Backend abstraction (`backend.py`): `virtual.py` (simulation) and `modbus_backend.py` (real bus), plus `sampling.py` |
| `drivers/` | Serial/Modbus RTU drivers (`rtu.py`, `waveshare.py`, `vfd.py`), setup, diagnostics, port checks, error taxonomy |
| `twin/` | Causal water/plant/chemistry model, clock, scenarios, installation runtime; the equipment twin service, the graph water model (`graph_water.py`, 0.7) and simulated modules (`virtual_modules.py`). Simulation only |
| `control/` | Processes, recipes, AUTO, alarms, supervisor, assignments, commands, and the bench lease (`bench.py`) |
| `historian/` | SQLite storage, recording, retention (365-day window), trends, backups, offsite upload, SD card, timestamp quarantine, v0.1 import |
| `equipment/` | User-defined equipment (0.5): types, items, append-only channel bindings; typed properties, scaling and alarm limits (0.7, `views.py` for design views) |
| `topology/` | The explicit hydraulic graph (0.7, ADR-0024): nodes, ports, edges, junctions, sensor associations, validation, migration from the 0.6 drawing |
| `diagram.py` | Drawings of the graph (0.7): symbols and orthogonal routes, route rules, crossings; presentation only |
| `blocks/` | Blocks (0.7): purpose, members, process path over the graph, startup/stop plans; store for graph, drawings and blocks |
| `programs/` | User programs (0.5): validated declarative operations, weekly schedules, the engine (only actor, through the audited command path), AUTO/MANUAL mode, conflicts, restart recovery; `authoring.py` (0.7) compiles the no-code form to a SEQUENCE |
| `commissioning/` | Dashboard module wizard and discovery (0.5): set a new module's address/modes on a separate USB-RS485 adapter, then find it on the bus |
| `security/` | Individual accounts (argon2), sessions, roles and permissions (`auth.py`) |
| `api/` | FastAPI app and service wiring; hardware routes in `hardware.py` |
| `web/` | Static browser UI; no build step. `router.js` (orthogonal router, also run by `tests/js`) and `hydraulic-editor.js` (0.7) |
| `ids.py`, `build.py` | (0.7.1) Editable public IDs over stable keys; the build identity (version, commit, web digest) used as cache key and shown, main/twin mismatch (ADR-0027) |
| `cli.py`, `launcher.py` | `python -m aquacrop ...` commands and the Windows/desktop launcher |

Also: `src/greenhouse` (v0.1 compatibility launcher, simulation only), `legacy/` (frozen v0.1 with
source hashes, never edit), `deploy/` (systemd units, nftables, fstab examples), `configs/`,
`scripts/` (release build/verify, measurements), `tests/` (unit, integration, acceptance, hardware
fakes), `docs/` (ADRs, model, operations, commissioning, acceptance).

## Invariants: a change that breaks one is a defect

1. **Real profiles are always `HARDWARE_READ_ONLY` at boot.** Bench writes exist only as an in-memory,
   session-bound lease (ADR-0003). No file, env var or restart may produce a write-capable state.
2. **No automatic ON on the wire, with one gated exception.** Read-only config writes nothing. The
   generated bench config sends only FC05 OFF (`SAFE_STATE_OFF`) at start, stop, session end and module
   return. An ON comes from a person inside a test session, or (0.5, `bench_tests.enabled` only) from an
   active, twin-accepted program to an allowlisted IO 8CH output inside an armed session, never in
   RECOVERY/STOPPING (`docs/RELEASE_READINESS.md`, "The one exception"). Every write is preceded by a
   mode check and followed by a readback. Every stop path sends OFF until verified, using the channels
   held when the item was switched ON. No FC0F (ADR-0005).
3. **Real profile has no controller** (ADR-0009); equipment is operated only through each process's
   bounded operations.
4. **Hardware libraries (`pymodbus`, `pyserial`) are never imported by simulation code paths.**
5. **One database per profile** (ADR-0004); retention never deletes inside the 365-day window.
6. **Provenance and honesty:** results are `PASS/FAIL/BLOCKED/NOT RUN`. Owner and hardware results
   stay `NOT RUN` until the owner or the bench produces them. Never claim a test passed that did not
   run, and never mark documentation as verified without evidence.
7. **No default credentials.** First admin is bootstrapped explicitly; serve refuses non-loopback
   plain HTTP.
8. Decisions live in `docs/adr/`. A change that departs from an ADR needs a new or amended ADR.
8a. **Program versions:** DRAFT is editable; VALIDATED is immutable; a VALIDATED version that fails
   revalidation becomes INVALID (still immutable) and is deactivated everywhere. Only VALIDATED bodies run.
   Twin evidence counts only from the current twin run.
8b. **Connections are explicit edges (0.7, ADR-0024).** Moving, aligning, rerouting or a crossing in a drawing
   never joins pipes; only an edge or a junction does. Every route is horizontal and vertical segments
   only, in the browser and on the server (`diagram.route_issues`); a drawing change never changes the
   graph's digest.
8c. **Twin proof is bound to its context (0.7, ADR-0026).** Accepted evidence names the twin run and the
   digest of the program, its items, the water network and the model; any of them changing makes it stale.
   An approved export applies step by step through the durable journal and recovers after a restart.
8d. **IDs (0.7.1, ADR-0027).** Keys (`item_id`, `program_id`) never change and every reference uses them; the public
   ID people see and edit is separate, unique per kind ignoring case, renamed under a revision check and logged.
   Never rewrite references, versions or history to rename something.
8e. **One drawing workspace (0.7.1).** The installation is drawn in the twin and reaches the Overview only through an
   approved export; Blocks and the Overview draw it read-only. A stop keeps every item it verified OFF as its duty
   until the operation ends (RR-01).

## Owner decisions (0.5)

9. **Never switch on unapproved equipment**, and nothing is merged without the owner's approval.
10. **Daylight saving:** a watering must never be skipped across a DST change, and timing stays
    consistent. Site timezone is `Europe/Stockholm`.
11. **Roles (ADR-0023, 0.7; amended 0.7.1):** Viewer sees Overview, Programs and Alarms only (no history,
    equipment, modules or twin). Operator runs daily work, creates equipment (Equipment page), blocks and programs,
    draws in the twin, proposes channels and sends module requests. Administrator has every right. Commissioning is
    an extra flag for operators at the bench: module preparation with the address wizard, bench arm, I/O assign and
    channel verification. Restart, mode and conflict approvals: administrator or operator, never
    viewer. The server enforces every right; the menu only follows it.
12. **Users:** you cannot delete yourself or remove/demote/disable the last administrator. When
    creating a user, the UI shows only the chosen role's rights.
13. **UI messages** appear next to the buttons that caused them, not as pop-ups (module wizard, Discovery,
    and in 0.7 the equipment, drawing, block and program forms).

## Deployed bench (site facts, for context)

- Raspberry Pi CM4 in a Waveshare CM4-ETH-RS485-BOX-B (`aquacrop-bench`), Raspberry Pi OS Bookworm
  64-bit, Python 3.11, running 0.5.4 (0.5.7 in this branch). Config `/etc/aquacrop`, data `/var/lib/aquacrop`. The twin service
  (`aquacrop-twin.service`) runs from the same release and moves with every upgrade and rollback.
- Bus wiring as installed on 23 Oct 2026: box Port1 = `/dev/ttyAMA3` (Relay (D) at addresses 1 and 2,
  IO 8CH at 5 and 6); Port2 = `/dev/ttyAMA4` (AI 8CH 4-20 mA at 3 and 4); Port3 = `/dev/ttyAMA5`
  (VFD). Note `drivers/ports.py` maps COM1..COM3 to `/dev/ttyAMA1..3`; treat any mismatch with the
  device as a finding.
- RS485 ports are isolated: every module needs a GND wire (on analog modules, to the module's
  power negative).
- Physical digital-output tests are the owner's and stay `NOT RUN` until performed.

## How to review

- Prioritise: safety invariants above, then data integrity (historian, backups, restore, migrations,
  timestamps), then robustness (bus errors, timeouts, partial reads, backoff, input validation at the
  API and CLI boundary), then correctness of the twin and controllers, then everything else.
- Check the change against its neighbours: config schema, both IO backends, API, UI (`web/`), CLI,
  docs and tests must stay consistent. A new behaviour with no test, or a doc that contradicts code,
  is a finding.
- Prefer simple, integrated solutions. Flag duplicated logic, a second way of doing something the
  codebase already does, new abstractions with one caller, and features that bypass the existing
  layers (for example UI or API reaching past `control/` to the driver).
- Report findings by severity with `file:line`, a concrete failure scenario, and the smallest fix.
  Do not report style nits. Do not modify files in a review.

## Commands

```text
python -m pytest                                   # unit, integration, acceptance
python -m aquacrop config validate --config demo-48
python -m aquacrop demo verify --config demo-48 --seed 42   # deterministic, no serial port
python scripts/verify_release.py
node tests/js/router.test.js                       # the orthogonal router (pytest runs it too when Node exists)
python scripts/ui_check/run_journey_070.py --out ui-evidence-0.7   # 0.7 browser journey (needs Playwright)
```

Python 3.11 to 3.14. Install with `pip install --require-hashes -r requirements.lock` then
`pip install --no-deps .`.
