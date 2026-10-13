# ADR-0016: two additional Waveshare IO 8CH modules

Status: owner-confirmed inventory; physical channel allocation and electrical verification pending.
Date: 2026-10-13.

The owner purchased two of the retailer's WS26244 Modbus RTU digital I/O module. This supersedes
the earlier statement that only two Relay (D) and two analog input boards are available.
The complete inventory is two SKU 26517 Relay (D), two SKU 25821 analog input and two SKU 26244
IO 8CH modules: 16 AI, 32 DI, 32 DO, 80 points across six devices.

Add a distinct catalogue model, expose open-drain sink output metadata and provide an 80-point
installation configuration. Preserve legacy 24/48-point configurations and keep all newly added
plant duties unallocated. Addresses 5/6 are proposals requiring verification, not discovered IDs.
Apply the existing read-only acquisition and isolated bench lease rules to the new modules.
Preserve bench Europe/Stockholm and final-installation Atlantic/Canary time zones.

The extra 16 outputs increase capacity but have a different electrical interface from relay
contacts. They do not establish that a particular VFD terminal or field coil can be driven.
Retain NOT_CONFIGURED for physical VFD control until its actual interface is verified.
Do not invent feedback sensors or enable physical AUTO. Reassess the 80-point storage budget;
do not shorten 365-day retention or slow 60-second recording without a further owner decision.

Protocol, commissioning limitations and sizing evidence are in
[digital_io_8ch.md](../model/digital_io_8ch.md).
