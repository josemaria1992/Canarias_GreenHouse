# CP-05 Faults and zero writes

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

Profile: HARDWARE_READ_ONLY, no lease. Passive bus monitor: device ______, capture files attached.

| Action | Write frames on monitor (FC05/06/0F/10) | Software `writes_sent`/events | Points affected | Relay states unchanged? |
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

