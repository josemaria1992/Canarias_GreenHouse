# CP-05 Faults and writes on the wire

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

Profile: HARDWARE_READ_ONLY, no lease. Passive bus monitor: device ______, capture files attached.
Configuration (tick one; fill one copy per configuration): ☐ read-only (no `safe_state`) ☐ generated bench (`hw make-config`, safe-state OFF at START, STOP, LEASE_END, MODULE_RETURN)

Expected. Read-only: zero FC05/06/0F/10 frames in every row. Generated bench: no FC06/0F/10 and no FC05 ON
in any row; FC05 OFF only at start, stop and module return, one per output that read ON just before (none
when all were OFF), each with an FC01 read and a `SAFE_STATE_OFF` event. Any automatic ON is a FAIL.

| Action | Write frames on monitor (FC05 ON / FC05 OFF / FC06 / 0F / 10) | Software `writes_sent`, `SAFE_STATE_OFF` events | Points affected | Output states as expected? |
|---|---|---|---|---|
| Service start | | | | |
| Steady polling 10 min | | | | |
| Unplug one module's RS485 pair | | | only that module UNAVAILABLE? | |
| DEVICE_COMM alarm only for that module? | | | | |
| Reconnect | | | recovered? | |
| `systemctl restart` | | | | |
| `systemctl stop` | | | | |
| CM4 power cut and restore | | | | |
| After restart: banner READ ONLY, no lease? | | | | |

## Result

PASS / FAIL / INCOMPLETE: ______  Deviations and notes:

| Role | Name | Signature | Date |
|---|---|---|---|
| Operator | | | |
| Witness | | | |
| Owner acceptance | | | |

