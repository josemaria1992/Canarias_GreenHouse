# CP-00 Platform record

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

## OS and software

| Item | Actual |
|---|---|
| Image file name / release date | |
| Image SHA-256 (downloaded) / published | |
| Flash method (rpiboot version, Imager version) | |
| `uname -a` | |
| Python version | |
| `pip freeze` attached (file name) | |
| Wheelhouse MANIFEST.sha256 attached | |

## Serial ports (one known module per port, `diagnostics read`)

| Carrier port | Overlay lines in config.txt | Actual tty | Module used | Read OK? | Termination fitted? | Transceiver mode |
|---|---|---|---|---|---|---|
| COM0 | | | | | | |
| COM1 | | | | | | |
| COM2 | | | | | | |
| COM3 | | | | | | |
| USB TO RS485 (C) by-id path | n/a | | | | | |

Serial console disabled on Modbus ports (evidence): ______

## RTC and time

| Check | Actual |
|---|---|
| `i2cdetect -y 10` shows 0x51? | |
| `timedatectl` output attached | |
| Time correct after power cycle with network unplugged? | |

## Performance (48 points polled every 3 s, snapshots every 60 s, read-only)

| Measure | Actual |
|---|---|
| Poll cycle time per bus (s) | |
| CPU load average (1 h) | |
| Snapshots in 1 h (expect 60, `recorder.snapshots` in `/api/status`) | |
| Database growth over 24 h (MiB; desktop measurement ≈ 22 MiB) | |
| `storage report`: 365-day projection (GiB) | |
| Free disk after OS and install (`df -h /var/lib/aquacrop`) | |
| Backup: time and size; `backup verify --full` time | |
| `/api/health` all true after 1 h? | |

## Result

PASS / FAIL / INCOMPLETE: ______  Deviations and notes:

| Role | Name | Signature | Date |
|---|---|---|---|
| Operator | | | |
| Witness | | | |
| Owner acceptance | | | |

