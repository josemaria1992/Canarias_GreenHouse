# Two added Waveshare digital I/O modules

Owner update, 13 October 2026: two **Modbus RTU IO 8CH, SKU 26244**, in addition to two Relay (D)
and two AI boards. Six modules provide **16 AI + 32 DI + 32 DO = 80 points**. The 32 DO comprise
16 relay contacts and 16 transistor sink outputs, distinguished in the catalogue and API.

## Identity and protocol

The retailer lists WS26244 and links the manufacturer IO 8CH wiki. Its indexed V2 table specifies
FC02 inputs and FC01 output states at 0x0000–0x0007, FC05 explicit ON/OFF and FC03 control modes at
0x1000–0x1007. Both exact digital models share this protocol; the new SKU is a distinct model.
The manufacturer describes isolated Darlington/open-drain outputs, maximum 500 mA per channel.
This is not approval of a coil/load or simultaneous bank current. They are not dry contacts or AC
motor outputs. Verify actual supply/output voltage, wiring/common, suppression, bank limits and
firmware for the received revision; retailer/manufacturer voltage wording differs.

References: [manufacturer product](https://www.waveshare.com/product/iot-communication/modbus-rtu-io-8ch.htm),
[manufacturer V2 protocol](https://www.waveshare.com/wiki/Modbus_RTU_IO_8CH),
[owner retailer](https://elektroshoppen.dk/produkt/waveshare-industrial-8ch-digital-input-output-modul-dc-736v-modbus-rtu-protokol/).
The wiki was read through indexed content when direct requests returned 403. Tests use a fake wire
under the real Modbus client; physical firmware is not verified.

## Included software

Package version is 0.3.1 so the release launcher prepares a fresh environment rather than reusing
the earlier 0.3.0 environment that cannot recognize the new model/configuration.

- Launcher default `installation-80`; `digital_A/B` use proposed addresses 5/6. All 80 points can
  be tested independently. New channels remain unallocated in plant mode; no field feedback is invented.
- Legacy `installation-48` retained. Canary read-only and Malmö bench examples contain six devices,
  with unverified addresses/ports and no physical mapping or configured VFD write interface.
- Reads, output readback, mode checks, diagnostics, fault isolation, API/I/O display and historian
  support the new model. Its outputs are labelled transistor sinks, not relay contacts.
- FC05 requires an isolated lease and exactly eight NORMAL modes. Short/empty replies and
  linkage/toggle/edge modes refuse writes. No broadcast, toggle/flash, mode/ID/baud change, incidental
  startup/reconnect/shutdown write or physical AUTO was added.
- The shared bus rechecks authorization inside its lock before every FC05/FC06 frame, after
  preflight/connection waits. Expiry/revocation stops remaining frames; a partial VFD sequence is
  uncertain (review R1; the controller findings R2-R4 are fixed in the process controllers).

Extra DO remove the numerical capacity shortage. They do not prove an electrical match to the VFD
terminal. Preserve the simulated Modbus drive and NOT_CONFIGURED physical adapter until the actual
interface is verified; terminal control or additional feedback assignments need a concrete reviewed map.

## History and commissioning

Keep full 60-second snapshots and 365 days. Old 48-point sizing and the 10 GiB warning budget do not
size 80 points. The owner set 17 GiB (`db_budget_bytes: 18253611008`) as the planning/warning budget
in `installation-80` and the bench and read-only examples (2026-10-13); legacy configurations keep
theirs. A budget is a warning only and cannot prune in-window history.
The benchmark supports `--inventory 80`; reassess DB/WAL/events/export/restore margins on the CM4
and SD card before deployment, without silently changing cadence, retention or database format.

One synthetic Windows benchmark recorded 1,440 complete snapshots (115,200 samples), 200 commands,
24 alarms and 20 edges per digital point over one simulated day. The closed SQLite file was
38,850,560 bytes; measured growth projects approximately 13.2 GiB over 365 days, before WAL,
free-space, export and restore margins. This is an extrapolation, not a year-long CM4 capacity
test. A compressed one-day backup was 2,141,916 bytes and its local restore drill passed; it is
not evidence of a OneDrive upload/restore or of the eventual full-year archive size.
Keep the budget warning (17 GiB, owner 2026-10-13); CP-00 measures the deployment's real growth and
full-size restore working space on the CM4. The earlier 10 GiB scratch figure is a minimum, not a guarantee
that the larger inventory fits.

**Re-measured 2026-10-13** (400 days, 80 points, every process in AUTO): 15.02 GiB at day 365, a
0.82 GiB compressed backup, and a full restore drill that passed needing 18.05 GiB of scratch
(19.9 GiB on the card with the downloaded copy and headroom). Layout and the owner's 17 GiB budget:
`operations/measurements/2026-10-13-measurements.md`, section "80 points".

On arrival: identify units separately, verify unique addresses manually, stimulate 16 added DI,
test 16 added DO using suitable dummy loads under a lease, verify modes/firmware and loss/restart/
expiry/revocation behavior. Electrical protections remain independent. Final field mapping and
cause-and-effect are still unverified.

Tests: `tests/hardware/test_digital_io_8ch.py` covers mixed inventory, protocol, channel isolation,
mode/lease guards, virtual I/O, 80-point history and API metadata. New per-frame VFD lease tests are
in `tests/hardware/test_vfd_modbus.py`. Update acceptance and measured storage evidence after the
remaining controller review fixes are integrated.
