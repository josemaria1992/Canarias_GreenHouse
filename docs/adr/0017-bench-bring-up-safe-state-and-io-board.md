# ADR-0017: bench bring-up: module setup, safe-state OFF, the I/O board and I/O assignment records

Status: accepted by the owner on 2026-10-20 (answers 1 to 6 in the build thread); not yet run on the hardware.
Date: 2026-10-20.

## Context

The owner will connect the CM4-ETH-RS485-BOX-B to two Relay (D), two Analog Input 8CH (version A) and two
IO 8CH modules, install AquaCrop, test every channel from a developer page, and then record which channel
carries which installation signal. All six modules ship at address 1. Waveshare documents the address change
as a raw FC06 frame to the broadcast address, which renumbers every module on the wire. Before 0.4.0 nothing
switched outputs OFF when a bench lease ended, and nothing recorded which channel had been checked.

## Decision

1. **Module setup (`aquacrop hw setup`)**, owner answer 1. One module on the wire at a time. It reads the
   address, version and UART registers, refuses when more than one module answers, when the model does not
   match, or when another AquaCrop process owns the port. It writes only the mode registers (Normal for
   Relay (D) and IO 8CH; the requested range for AI 8CH) and the address register, each to the module's
   current unit, never to broadcast, never the UART register. Everything is read back from the new address,
   and the old address must no longer answer. A JSON record is always written (DONE or STOPPED).
   `aquacrop hw make-config` turns the records into a HARDWARE_READ_ONLY configuration and sends nothing.
   The bus tells an analog module from a digital one, but not a Relay (D) (SKU 26517, relay contacts) from
   an IO 8CH (SKU 26244, 500 mA open-drain transistor outputs), nor one AI 8CH (SKU 25821) from the other.
   So `--sku` (from the label) and `--label` (the name marked on the module, equal to `--name`) are
   required, shown in the confirmation, and kept in the record as the installer's statement
   (`provable_from_bus: false`); make-config refuses a record without them (owner's reviewer, 2026-10-21).
2. **Safe-state OFF**, owner answer 2. `safe_state.outputs_off_on` lists the moments: START, STOP,
   LEASE_END, MODULE_RETURN. At each one, every digital output that reads ON gets one FC05 OFF with an
   FC01 readback, through its own audited command path (origin SAFE_STATE) whose policy accepts OFF to a
   digital output only. LEASE_END covers only the outputs of that lease. A module whose modes are not all
   NORMAL is not written; the result says INCOMPLETE. Empty by default (configs before 0.4.0 keep their
   behaviour); `hw make-config` sets all four. The simulation refuses the setting. The lease expiry is
   noticed by the poller, so it applies with no browser open. On the wire: the read-only profile (no
   moments) writes nothing; the generated bench profile writes only these FC05 OFF frames (none when all
   outputs are OFF). Nothing ever sends an ON automatically: an ON is a user's switch or guided-check step
   inside a test session.
3. **Test session (bench lease)**, owner answer 3. One session may cover all outputs, for up to 30 minutes,
   renewable by the holder from the same browser login (BENCH_LEASE_RENEWED). "All outputs OFF" needs no
   session and switches only OFF.
4. **Ports**, owner answer 4: COM1 (/dev/ttyAMA1) carries the two Relay (D) and the two IO 8CH, COM2
   (/dev/ttyAMA2) the two AI 8CH, COM3 stays free for the pump drive's Modbus link (CP-06), COM0 (the
   mini-UART, ttyS0) is not used. `aquacrop hw ports` checks the boot overlays and the device nodes and
   changes nothing; the CM4 installer still never edits the boot configuration.
5. **Analog modules**, owner answer 5: version A (0-5 V / 1-5 V / 0-20 mA / 4-20 mA), tested at 4-20 mA on
   every channel; the per-channel jumpers stay fitted (current), as shipped.
6. **Access**, owner answer 6: from the owner's PC on the local network over HTTPS (the existing `tls` step).
7. **I/O board** (`#board`): one card per module; outputs as switches with two lights (asked = the last
   command, reported = the coil readback), inputs as lights with the changes seen, analog inputs in mA or V
   (the module's own measurement, raw µA or mV / 1000) with a bar, the raw value, the quality and the age;
   per-module answering state and, on the bench, each port's polling time. On the simulation the modules
   are virtual (I/O test mode); on the bench a switch works only inside the session.
8. **Guided check** (owner request, 2026-10-20): every channel in turn with a manual Next. An output is
   switched ON at the start of its step (the previous one OFF first); an input waits for its change; an
   analog input shows its reading. Works / Doesn't work is the user's judgement, recorded in `io_checks`
   with what the server saw during the step (a confirmed ON, an input change, a fresh reading). Next
   without an answer records SKIPPED.
9. **I/O assignment records** (`io_assignments`): PROPOSED when a commissioning user picks an installation
   signal for a channel (kinds must match; one channel per signal), VERIFIED after the server-side check
   (DO: ON and OFF confirmed by readback, by this user; DI: a change both ways; AI: a fresh reading within
   the user's tolerance of the applied reference) plus the user's observation, CLEARED when freed.
   Append-only. The export (YAML or JSON) lists every channel with its assignment and latest check.

## Consequences

- Assignments and checks are records, not bindings. `physical_mapping` stays empty, the hardware profile
  keeps every installation signal UNBOUND, and nothing here starts a process. Importing the verified
  assignments into the irrigation system is a later, separately authorized step (live control), which also
  needs the cause-and-effect table, setpoints and the drive interface (CP-06).
- Schema 4 adds `io_assignments`, `io_checks` and an index on `commands(target, intent_utc)`; the migration
  takes the usual consistent backup first.
- A readback, an input change or a reading proves the module and its wiring as far as the module sees it.
  Only the user's observation covers the device itself, and the record says so.
- Everything above was tested on the in-memory RS485 bus and the simulation, not on the modules.
