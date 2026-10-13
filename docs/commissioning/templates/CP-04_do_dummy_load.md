# CP-04 DO on dummy loads (bench lease)

> **UNSIGNED TEMPLATE.** Not evidence until filled in on the actual hardware and signed. Leave unknown fields blank; never copy expected values into "actual".

| Field | Value |
|---|---|
| Authorization (owner, date, scope of this setup) | |
| Operator (qualified) | |
| Witness | |
| Date / time (site time zone: Europe/Stockholm on the Malmö bench, Atlantic/Canary at the installation) | |
| Software version / git commit | |
| Config id @ version | |
| Location / bench description | |

## Preconditions (all must be YES before arming)

| Check | YES / NO |
|---|---|
| Every production load (pumps, valves, dosing, field wiring) physically disconnected from this module | |
| Dummy loads fitted (type, rating) and separately fused | |
| Relay modes all NORMAL (fresh `diagnostics read` attached) | |
| Owner authorization for this exact setup recorded above | |
| Lease user has commissioning flag; second person present | |

## Lease

| Item | Actual |
|---|---|
| Lease id | |
| Allowlist | |
| Duration (s) | |
| Banner showed `REAL — BENCH WRITES ARMED` | |

## Per output

| Output | ON outcome | ON readback | Dummy load lit? | OFF outcome | OFF readback | Dummy load off? | OUTCOME_UNKNOWN? how resolved |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
| | | | | | | | |

Coil readback is not actuator proof; the "Dummy load" columns are the physical indication.

## Teardown

| Step | Done |
|---|---|
| All tested outputs OFF by readback | |
| All dummy loads visibly off | |
| Lease ended (End lease / expiry); banner back to `REAL — READ ONLY` | |
| Event and command export attached (`/api/export/audit.csv`) | |
| Dummy loads removed; module state left as found and recorded | |

## Result

PASS / FAIL / INCOMPLETE: ______  Deviations and notes:

| Role | Name | Signature | Date |
|---|---|---|---|
| Operator | | | |
| Witness | | | |
| Owner acceptance | | | |

