# v0.1 baseline audit

Source: `AquaCrop_Greenhouse_Control_v0.1.zip`, uploaded to the project 2026-10-11.
SHA-256 `fa118eb2677cf1f8a739a4e73d81eb15c8e6dcd6f6613c34fcb299a598216dd8`. The archive and its
18 files are kept byte-identical in `legacy/` with per-file hashes in `legacy/SOURCE_HASHES.sha256`.
No `V0_1_CODE_AUDIT.md` was supplied, so the line references below come from this audit.

**Baseline tests:** `9 passed` (Python 3.13.16, pymodbus 3.11.3, fastapi 0.143, Linux x86_64).
They cover the narrow prototype only. `tests/test_modbus_protocol.py` uses a hand-made client that
bypasses the pymodbus constructor, framer and CRC entirely, so it is not RTU evidence.

**Reproductions:** `tests/legacy/test_v0_1_findings.py` loads the vendored source as
`greenhouse_v01`. Each test passes when the defect is reproduced (12/12 reproduced).

## Brief §3 findings, verified

| # | Finding | Evidence (v0.1 file:line) | Repro test | Corrected in new platform by |
|---|---|---|---|---|
| 1 | One AI + one relay board, 24 points, one bus | `config.py:14-16`, `driver.py:9-14`, `storage.py:23-28`, `storage.py:83-87` | F01 | `config/schema.py` inventory; per-device point IDs (AT-INV-01/02) |
| 2 | AUTO only alternates labels; never commands anything | `controller.py:83-95`; baseline test asserts it, `tests/test_controller.py:34-57` | F02 | `control/auto.py` drives the twin via audited virtual DO (AT-CTL-01) |
| 3 | Alarm identity contains the value: every change gives clear+raise | `controller.py:52` (`f"... {value}"`), `controller.py:70-74` | F03 (3 raises, 2 clears for one condition) | `control/alarms.py` rule+target identity (AT-ALM-01) |
| 4 | Output applied, readback fails, no command event | `driver.py:101-104`; event only after success `controller.py:140-141` | F04 | intent audited before dispatch, `OUTCOME_UNKNOWN` (AT-AUD-01) |
| 5 | Short replies accepted; empty relay-mode reply permits writes | `driver.py:84`, `driver.py:99` (`any([])` is False), `[:8]` slicing | F05 | exact-length validation (AT-MB-03/04) |
| 6 | One failed module discards the whole snapshot | `driver.py:76-89` single try; `controller.py:63-67` | F06 | per-device acquisition and health (AT-IO-02, AT-MB-07) |
| 7 | Simulated limits persist into hardware mode | `controller.py:22-26` loads `settings` for any backend; `storage.py:34-36` single namespace | F07 | versioned YAML per profile; legacy settings never applied (see migration) |
| 8 | Single shared Basic credential; no roles/sessions/CSRF/rate limits/rejection audit | `app.py:15`, `app.py:61-67`; `config.py:12-13` | F08 | `security/auth.py`, auth gate (AT-SEC-01) |
| 9 | No origin/calibration/module/session metadata, edges, backups, migrations | `storage.py:22-37` | F09 | `historian/schema.py`, `historian/backup.py` |
| 10 | Static UI outside the package: a wheel has no dashboard | `app.py:16` (`parent.parent / "static"`), `pyproject.toml:23-24` | F10 (wheel built, no `index.html`) | `aquacrop/web` package data (AT-PKG-01) |
| 11 | Poll task failure is unsupervised; web stays up | `controller.py:196-199`, `controller.py:201-204` | F11 (task dies on disk error, nothing restarts or reports) | supervised driver + health (AT-OPS-04) |
| 12 | Chart spaced by sample index; drops non-OK rows and bridges gaps; stale values linger | `static/index.html:46` refresh catch keeps old DOM; `drawChart` `x=n=>...n/(values.length-1)` and `filter(row=>row.quality==='OK')` | F12 | timestamp axis, server gap flags, stale banner |

## Additional findings from this audit

| # | Finding | Evidence | Impact |
|---|---|---|---|
| A1 | Runtime signature guessing between `slave`/`device_id` | `driver.py:62-64` | Brief forbids guessing 3.x signatures; replaced by a pinned adapter |
| A2 | Library `retries=1` also applies to FC05 writes, so a timed-out write is blindly retransmitted | `driver.py:61` | Writes now use 0 library retries; reads use bounded driver retries |
| A3 | `pymodbus>=3.10,<4` unpinned, no lockfile | `pyproject.toml:13` | Pinned `pymodbus==3.11.3` |
| A4 | Bench writes armed by a persistent environment flag that survives restarts | `config.py:38`, `HARDWARE_COMMISSIONING.md` "Bench-only" | Replaced by an expiring server-side lease; the env flag is ignored by the launcher |
| A5 | README recommends `--host 0.0.0.0` with plain HTTP Basic on a LAN | `README.md` §3 | `serve` refuses non-loopback HTTP binds |
| A6 | Windows instructions need `Activate.ps1` (execution-policy change) | `README.md` §1 | Docs use `.venv\Scripts\python.exe` directly |
| A7 | systemd unit waits on `network-online.target` | `deploy/...service.example:7-8` | New unit doesn't require network for local functions |
| A8 | Inline script and `onclick` handlers require CSP `unsafe-inline`. Output is escaped through `esc()`, so no XSS was found | `static/index.html` | New UI: no inline script, strict CSP, `textContent` only |
| A9 | Simulated overflow/no-flow are flags with no plant | `controller.py:27`, `controller.py:40-46` | Causal twin with scenario presets |

## Preserved design

These carry over: startup without auto-resume, no boot/reconnect/shutdown output writes, all-normal
relay-mode check before any bench write, null values (not last-good) on failed polls,
complete snapshots including unchanged states, one worker, explicit unique device IDs, the
commissioning cautions in `HARDWARE_COMMISSIONING.md`, and the visual intent of the dashboard.
