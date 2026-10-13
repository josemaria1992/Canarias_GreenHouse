# CP-06 Drive Modbus interface (bench, motor disconnected)

> **UNSIGNED TEMPLATE.** Not evidence until filled in on the actual hardware and signed. Leave unknown fields blank; never copy expected values into "actual".

| Field | Value |
|---|---|
| Authorization (owner, date, scope of this setup) | |
| Operator (qualified; drive parameters set by) | |
| Witness | |
| Date / time (site time zone: Europe/Stockholm on the Malmö bench) | |
| Software version / git commit | |
| Config id @ version (map entered as `PROPOSED`) | |
| Location / bench description | |

## Drive and manual

| Item | Actual |
|---|---|
| Make, model, firmware (label photo) | |
| Manual: document, edition, Modbus section / pages | |
| RS485 port on the CM4 carrier, bus id, tty | |
| Serial settings and unit address (as set in the drive) | |
| Command source parameter (fieldbus / serial) | |
| Speed reference source (not fieldbus) and the speed set | |
| Comm-loss timeout and action, as set | |
| What resets the comm-loss timer (manual + observed in step 6) | |
| Power-up behaviour of the drive and of the control register | |
| Auto-restart after fault: off? | |
| Control register stored in non-volatile memory? (must be NO) | |

## Preconditions (all must be YES before arming)

| Check | YES / NO |
|---|---|
| Motor disconnected from the drive output (or drive output isolated per the manual) | |
| Emergency stop / STO wired and tested independently of Modbus | |
| No pump, contactor K1/K2 or field load connected | |
| Map in the configuration copied from the manual, provenance `PROPOSED`, every field referenced to a page | |
| Owner authorization for this exact setup recorded above | |
| Lease user has the commissioning flag; second person present | |

## 1. Read only (no lease)

`python -m aquacrop diagnostics read --config <bench cfg> --device drive:MAIN --cycles 5`

| Check | Keypad / display shows | Software shows | Match? |
|---|---|---|---|
| Status word while stopped and ready | | | |
| Running bit while stopped (expect 0) | | | |
| Fault bit while healthy (expect 0) | | | |
| Ready bit (if configured) | | | |
| Control register read back (if the drive allows) | | | |
| Frequency / current / fault code (if configured) | | | |
| Fault bit after provoking a harmless drive fault (method per manual), then reset at the keypad | | | |

## 2. Lease

| Item | Actual |
|---|---|
| Lease id | |
| Allowlist (`drive:MAIN` only) | |
| Duration (s) | |
| Banner showed `REAL — BENCH WRITES ARMED` | |

## 3. Run and stop

| Step | Outcome | Frames echoed | Status word after | Keypad shows | Output frequency rises/falls? | OUTCOME_UNKNOWN? how resolved |
|---|---|---|---|---|---|---|
| Run | | | | | | |
| Stop | | | | | | |
| Run again | | | | | | |
| Stop again | | | | | | |
| Run refused while a fault is active (expect REJECTED, nothing sent) | | | | | | |

An echo proves the drive received the value; the keypad and the output frequency are the physical
indication. Resolve any `OUTCOME_UNKNOWN` by reading the status and the keypad, never by resending.

## 4. Comm loss

| Step | Observed |
|---|---|
| Drive running (motor disconnected); unplug the drive's RS485 pair | |
| Seconds until the drive stopped (vs the configured timeout) | |
| Action taken by the drive (coast / ramp / trip) and fault shown | |
| Software: `DEVICE_COMM` for drive:MAIN, status UNAVAILABLE, nothing else affected | |
| Reconnect: status readable again; drive stays stopped until a new run command | |

## 5. Power cycle

| Step | Observed |
|---|---|
| Drive powered off and on while the control value was the run sequence | |
| Drive comes up stopped? | |
| Control register value after power-up | |

## 6. Comm-loss timer reset by reads

| Step | Observed |
|---|---|
| Drive running, service polling only (no writes) for 3 x the timeout: did the drive keep running? | |

If it stopped, the drive's timer counts writes only: record it and stop; this design does not rewrite
the control value (ADR-0015, `docs/model/vfd_modbus.md` §6).

## Teardown

| Step | Done |
|---|---|
| Drive stopped (keypad) and status word shows not running | |
| Lease ended; banner back to `REAL — READ ONLY` | |
| Event and command export attached (`/api/export/audit.csv`) | |
| `diagnostics read` output and photos attached | |

## Result

PASS / FAIL / INCOMPLETE: ______  If PASS, the map may be set to `VERIFIED` with this record as
`evidence` and copied to the installation's configuration. Deviations and notes:

| Role | Name | Signature | Date |
|---|---|---|---|
| Operator | | | |
| Witness | | | |
| Owner acceptance | | | |
