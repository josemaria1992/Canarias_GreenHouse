# CP-01 Module identification (read-only)

> **UNSIGNED TEMPLATE.** Not evidence until filled in on the actual hardware and signed. Leave unknown fields blank; never copy expected values into "actual".

| Field | Value |
|---|---|
| Authorization (owner, date, scope of this setup) | |
| Operator (qualified) | |
| Witness | |
| Date / time (Europe/Stockholm) | |
| Software version / git commit | |
| Config id @ version | |
| Location / bench description | |

One copy per module. Only this module is on the bus.

| Item | Actual |
|---|---|
| device_uid in config | |
| Label photo (model, SKU, revision) attached | |
| SKU on label | |
| Hardware variant (AI: standard / B) and evidence | |
| Bus / port / serial settings used | |
| Address used (proven alone on bus) | |
| `diagnostics read --cycles 5 --metadata` output attached | |
| Raw 0x8000 (software version, unverified) | |
| Raw 0x4000 (address register) | |
| Raw 0x2000 (UART parameters) | |
| Relay modes read (8 values) | |
| AI data types read (8 values) | |
| AI jumper positions (photo, per channel) | |
| Diagnostics summary `writes_sent` (must be 0) | |
| Any reconfiguration done with a vendor tool? before/after evidence | |

## Result

PASS / FAIL / INCOMPLETE: ______  Deviations and notes:

| Role | Name | Signature | Date |
|---|---|---|---|
| Operator | | | |
| Witness | | | |
| Owner acceptance | | | |

