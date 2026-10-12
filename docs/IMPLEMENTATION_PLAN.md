# AquaCrop greenhouse platform — milestone 0 implementation plan

Date: 2026-10-11. Status: **living document**. Milestones 0–4 implemented; milestone 5 documents only. Results: `ACCEPTANCE.md`.

## 0.1 Inputs actually inspected

| Input | Status |
|---|---|
| Owner brief, 11 Oct 2026 (project thread) | Read in full; this plan is derived from it |
| `AquaCrop_Greenhouse_Control_v0.1.zip` / `greenhouse_control_v0_1` | Received 2026-10-11 18:46 UTC. SHA-256 `fa118eb2…6dd8`, kept byte-identical in `legacy/`. Baseline 9 passed. Findings: `docs/architecture/v0_1_findings.md` |
| `V0_1_CODE_AUDIT.md`, `ACCEPTANCE_TEST_PLAN.md`, `CLAUDE_IMPLEMENTATION_MILESTONES.md`, `PROJECT_FACTS_AND_UNCERTAINTIES.md`, `OPEN_DECISIONS.md` | **NOT RECEIVED.** Acceptance IDs below are this plan's own provisional IDs (`AT-…`). When the owner's acceptance plan arrives they will be cross-mapped, not silently renamed |

Because v0.1 is absent, work starts with the parts of the specification that do not depend on it
(inventory/config, domain contracts, virtual I/O, causal twin, controller, alarms, historian,
Modbus adapter tested against doubles). v0.1 is folded in afterwards with a backup-first DB
migration and a `python -m greenhouse` compatibility launcher (AT-MIG-*).

## 0.2 Facts supplied by the owner (not to be asked again)

- Site ≈ 90 × 45 m, 13 hydroponic channels, two sectors.
- Hardware list per brief §2 (CM4 carrier SKU 22555, CM4004032, Relay (D) 26517, AI 8CH 25821,
  USB TO RS485 (C) 34620, enclosure, NDR-240-24, FNIRSI SG-003A).
- Module quantity: owner text "two of each I/O type (four units, 16 AI + 16 DI + 16 DO = 48)".
  Recorded as **2 × Relay (D) + 2 × AI 8CH**. Inventory stays generic
  (`points = 8 × AI_count + 16 × RelayD_count`); 24- and 48-point fixtures both exist.

## 0.3 Explicitly unresolved (kept `TBD — requires operator confirmation`)

Hydraulic topology, actuator wiring, pump/VFD interface, probe inventory, calibration, production
recipe, safe-state / cause-and-effect matrix, alarm severities for physical equipment, off-device
backup destination, role assignments for physical rights, OS image choice for the CM4. (History
retention was set by the owner on 2026-10-12: 60 s snapshots, rolling 365 days, ADR-0008.)
None of these block milestones 1–4; they block milestone 5 and any live release.

## 0.4 Milestone → acceptance mapping

### Milestone 1 — desktop platform
| ID | Acceptance criterion | Evidence |
|---|---|---|
| AT-INV-01 | demo-48 and demo-24 validate; 48 / 24 unique point IDs, no global AI1..AI16 arrays | unit tests |
| AT-INV-02 | arbitrary extra module (e.g. 3 × AI, 1 × Relay) validates with no code change | unit test |
| AT-INV-03 | `(bus_id,address)` uniqueness; same address on different buses OK; addresses 1–247 only | unit tests |
| AT-INV-04 | profile rules: SIMULATED ⇒ virtual buses only; real profiles start read-only; `COMMISSIONED_LIVE_CONTROL` rejected; DEMO scaling refused in real profiles | unit tests |
| AT-IO-01 | every AI/DI injectable and every DO commandable individually in IO_TEST | unit + API tests |
| AT-IO-02 | single virtual module failure invalidates only its points | unit test |
| AT-IO-03 | simulation never imports/opens serial (`serial`, `pymodbus` not in `sys.modules`) | subprocess test |
| AT-HIS-01 | every acquisition records all configured points, unchanged digitals included, one transaction per cycle | integration |
| AT-AUD-01 | command intent persisted before side effect; outcome after; rejected commands audited | integration |
| AT-SEC-01 | login, roles viewer/operator/admin, CSRF + origin check on every mutation, no default credentials | API tests |
| AT-UI-01 | seven views served from packaged assets, no CDN | API/static test |

### Milestone 2 — twin / control
| ID | Criterion |
|---|---|
| AT-TWN-01 | water ledger invariant holds every tick within 1e-9 L·(ticks) over long runs incl. spill & starvation |
| AT-TWN-02 | MANUAL and AUTO drive the same plant via virtual DO points |
| AT-TWN-03 | determinism: same seed/config/inputs ⇒ identical state hash; speed 1×/10×/60× identical |
| AT-TWN-04 | pause/step/reset/replay semantics; replay reproduces a recorded run hash |
| AT-CTL-01 | two-sector AUTO: precheck, open, start, verify flow, irrigate, stop, close, pause, alternate |
| AT-CTL-02 | no-flow, low reservoir, overflow, stuck valve, failed DI ⇒ latched FAULT_LOCKOUT, recovery needs condition clear + ack + reset + deliberate restart |
| AT-CTL-03 | conflicting MANUAL command during AUTO rejected and audited |
| AT-ALM-01 | alarm identity = rule + target; value change updates occurrence; ack ≠ clear ≠ reset; survives restart without duplicates |
| AT-SCN-01 | every mandatory scenario preset (brief §7) has a scripted acceptance test |
| AT-DEMO-01 | `python -m aquacrop demo verify --seed 42` headless, nonzero on failed invariant |

### Milestone 3 — Modbus
| ID | Criterion |
|---|---|
| AT-MB-01 | brief §6 request vectors reproduced byte-exact by the real pymodbus 3.11.3 RTU framer |
| AT-MB-02 | FC01/02/03/04/05 (+ optional 0F) through real client + fake serial transport |
| AT-MB-03 | short/malformed/CRC/exception/wrong-unit/wrong-FC replies rejected |
| AT-MB-04 | mode-register gate: exactly 8 normal modes else writes denied |
| AT-MB-05 | read-only/shadow/start/reconnect/shutdown ⇒ zero write frames on the wire |
| AT-MB-06 | bench lease: expiry/revocation/allowlist/config change deny writes; uncertain write ⇒ OUTCOME_UNKNOWN, no retransmit |
| AT-MB-07 | per-device health and backoff; one failing node does not invalidate others |

### Milestone 4 — packaging / security / operations
AT-PKG-01 wheel installed outside tree serves UI offline · AT-PKG-02 lockfiles, `pip check` ·
AT-SEC-02 session expiry/revocation, rate limits, CSV formula escaping, XSS-safe labels ·
AT-OPS-01 backup, verify, restore to new instance · AT-OPS-02 migrations incl.
v0.1 backup-first · AT-OPS-03 disk budget / full disk ⇒ degraded health, inhibit starts ·
AT-OPS-04 acquisition-task supervision · AT-OPS-05 systemd unit, CM4/eMMC/UART guide, rollback doc.

### Milestone 5 — procedures and templates only
Read-only identification, isolated AI/DI/dummy-load DO procedures and unsigned evidence templates.
Cannot be marked PASS from a desktop.

## 0.5 v0.1 findings

All 12 brief findings reproduced against the original source with file:line references, plus nine
more: `docs/architecture/v0_1_findings.md`. Results per acceptance ID: `docs/ACCEPTANCE.md`.

## 0.6 Repository delivery

Since 2026-10-12 the code lives at `github.com/josemaria1992/Canarias_GreenHouse` (ADR-0007):
the delivered history is `main`, later work arrives as pull requests. The shared project folder
`aquacrop/` (working tree + `git bundle`) is kept as a convenience copy.

### 2026-10-12 historian update (ADR-0008)
AT-HIS-02 … AT-HIS-09 recording interval, boundaries, separate timing, immediate events, stale
readings, restart, simulation, no migration · AT-RET-01 … AT-RET-05 rolling window, batches, no
deletion inside the window, clock guard, one-year steady state · AT-CAP-01/02 low/full disk and
visible capacity · AT-BAK-01 … AT-BAK-06 compressed backups · AT-STO-01 measured growth (desktop),
AT-STO-02 on the CM4 (blocked until hardware).

### 2026-10-12 owner review of ADR-0008 (R1 to R5)
AT-HIS-10 … AT-HIS-13 durable labels, clock steps, simulation pause, optional simulation-time
series (R2; AT-HIS-03 and AT-HIS-08 revised for wall time) · AT-RET-04 revised, AT-RET-06 …
AT-RET-08 clock trust, administrator release, protection of the current window, registers (R4) ·
AT-WAL-01 … AT-WAL-05 WAL reporting, bounded writers and readers, acquisition responsiveness,
concurrency stress (R1) · AT-BAK-07 … AT-BAK-10 publication after verification, failures and space,
restore drill, migration safety copy (R3) · AT-STO-01 revised with environment and workload (R5).
