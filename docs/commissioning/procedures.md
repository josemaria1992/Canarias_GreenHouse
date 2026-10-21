# Milestone 5: hardware arrival procedures

**None of these procedures has been performed.** They need the hardware on an isolated bench, a
qualified operator, and the owner's written authorization of the *specific* setup (template field
"Authorization"). Completing them does not release live field control, and passing them is not a
safety assessment of the greenhouse.

Ground rules for every procedure:

- No pumps, dosing equipment or field loads connected. DO tests use dummy loads only (indicator
  lamps or LEDs within the relay rating, on a separately fused bench supply).
- One module on the bus while its address is being proven.
- Addresses and channel modes are written only by `aquacrop hw setup` (0.4.0, ADR-0017), with the
  service stopped and one module on the wire, never to broadcast, with before/after read back in the
  CP-01 record. Baud rate and parity are never written. The bus cannot tell a Relay (D) (SKU 26517)
  from an IO 8CH (SKU 26244), nor one AI 8CH (SKU 25821) from another: the installer reads the SKU
  from the label and marks the module's name on it before setup, and both go into the record.
- Record actual values, not expected ones. Leave a field blank rather than guess. Attach logs
  (`diagnostics read` JSON lines, exported CSV, event export) and photos.
- A passive bus monitor (the USB TO RS485 (C) adapter on a second PC, receive only) is recommended
  for CP-05 so the write frames are evidenced on the wire, not only by the software's own count.
- Expected writes depend on the configuration (`RELEASE_READINESS.md`, "What AquaCrop writes on the
  wire"): the read-only configuration writes nothing on its own; the generated bench configuration
  writes only FC05 OFF at start, stop, session end and module return, for outputs that read ON. An
  automatic ON is a fault on any configuration.

| ID | Purpose | Needs lease? | Template |
|---|---|---|---|
| CP-00 | Platform record: OS image, UART/tty mapping, RTC, performance | no | `templates/CP-00_platform_record.md` |
| CP-01 | Identification of each module (label SKU, marked name) and its setup with `hw setup` | no | `templates/CP-01_module_identification.md` |
| CP-02 | AI channel stimulus with the FNIRSI SG-003A | no | `templates/CP-02_ai_stimulus.md` |
| CP-03 | DI stimulus | no | `templates/CP-03_di_stimulus.md` |
| CP-04 | DO on dummy loads under a bench lease, with teardown | **yes** | `templates/CP-04_do_dummy_load.md` |
| CP-05 | Fault and write checks (disconnect, restart, power loss) per configuration | no | `templates/CP-05_faults_zero_writes.md` |
| CP-06 | Drive Modbus interface: status bits, run/stop, comm loss (motor disconnected) | reads no; run/stop **yes** | `templates/CP-06_drive_modbus.md` |

Order: CP-00 → CP-01 (each module) → CP-05 read-only parts → CP-02 / CP-03 → CP-04 → CP-05 lease
parts → CP-06. Stop at the first unexpected result and record it. CP-01 to CP-06 run on the Malmö
bench (`configs/bench-malmo.example.yaml`, Europe/Stockholm) before the hardware goes to the
installation (Atlantic/Canary).

Tools since 0.4.0 (ADR-0017, `docs/installation/bench-bringup.md`): `aquacrop hw setup` writes the CP-01
setup record of each module (address, modes, ranges, read back); the I/O board's guided check steps through
CP-02 to CP-04 channel by channel with a manual Next and records each result; the board's export (YAML or
JSON) lists every channel with its latest check and assignment and is attached to CP-02 to CP-04. The
template values (stimulus points, observations, photographs) are still recorded by hand.

## CP-00 Platform record
Flash and configure per `docs/installation/cm4.md`. Fill the UART table by reading one known module
per port with `diagnostics read`. Record OS image checksum, `uname -a`, Python version,
`pip freeze`, RTC check, and measure: poll cycle time per bus (`/api/status`), CPU load at
48 points / 3 s polling, snapshots per hour (60 expected), database growth over 24 h and the
365-day projection (`aquacrop storage report`), free disk after installation, and the time and size
of one backup and of `backup verify --full` (ADR-0008 budget check).

## CP-01 Module identification and setup
For each module alone on the bus: photograph labels (model, SKU, revision) and the name you marked on
it, then run `hw setup` with that SKU (`--sku`) and name (`--label`); its JSON record is attached. The
bus cannot prove the SKU or the name, so the photo is the evidence for them. Then, with the
configuration made by `hw make-config`, run
`python -m aquacrop diagnostics read --config <cfg> --device <uid> --cycles 5 --metadata`.
Record raw `0x8000/0x4000/0x2000` values (unverified meaning), relay modes or AI data types, and
for AI boards the hardware variant (standard or B) and each channel's jumper position (photo).
A relay module whose modes are not all NORMAL is recorded as found and is not used in CP-04.

## CP-02 AI stimulus
Per channel to be used, with its observed data type and jumper verified: apply the source at each
point in the template (for 4–20 mA: 0, 4, 8, 12, 16, 20 mA and one over-range point if the source
allows, plus open loop), wait two poll intervals, record raw value, quality and the source's own
reading. This characterises the board channel only. It is not sensor calibration; scaling profiles
stay PROPOSED until sensor calibration evidence exists. The FNIRSI is a manual stimulus tool and is
never represented to the software as an output.

## CP-03 DI stimulus
With every relay mode NORMAL (a Linkage-mode channel would switch its relay when its DI is
stimulated). For each DI: apply the stimulus the module documentation requires, observe the point
change in the I/O view and the `DI_EDGE` event (with its observation window), remove, observe
again. Also confirm that no relay changed (coil readback and physical indication).

## CP-04 DO on dummy loads (bench lease)
Preconditions checklist in the template (loads disconnected, dummy loads fitted, owner
authorization, lease user has the commissioning flag). Arm a lease with only the outputs under test,
duration as short as practical. For each output: ON, observe coil readback and the dummy load;
OFF, observe both. Record any `OUTCOME_UNKNOWN` and resolve it with a fresh read and visual check,
never by resending. Then teardown: all tested outputs commanded OFF and confirmed by readback *and*
visually, lease ended, `REAL — READ ONLY` banner confirmed, event export attached.

## CP-05 Faults and writes on the wire
Passive monitor on the bus, no test session. Record frames while: starting the service,
unplugging one module's RS485 pair (only its points go UNAVAILABLE, `DEVICE_COMM` alarm for it
alone, backoff visible), reconnecting it, restarting the service, stopping it, and cutting CM4 power.
Run it once with the read-only configuration and once with the generated bench configuration.
Expected, read-only configuration: zero FC05/FC06/FC0F/FC10 frames throughout; relays keep their
state across all of it.
Expected, generated bench configuration: no FC06/FC0F/FC10 and no FC05 ON; FC05 OFF only at start,
stop and module return, one per output that read ON just before (none when all outputs are OFF),
each followed by an FC01 read and matched by a `SAFE_STATE_OFF` event. To see one, switch an output ON
in a test session, end the session, and confirm the OFF frame and event at LEASE_END.

## CP-06 Drive Modbus interface (bench, motor disconnected)
Only with the drive's manual at hand and the drive parameters of `docs/model/vfd_modbus.md` §4 set
by a qualified person: command source fieldbus, speed reference not fieldbus, comm-loss timeout and
action, no auto-restart. Enter the map from the manual in the bench configuration as `PROPOSED`, with
every field referenced to a page. Then: read the status word with `diagnostics read --device
drive:MAIN` and compare each bit with the keypad (stopped, ready, and a harmless fault provoked and
reset at the keypad); arm a lease with only `drive:MAIN`; run and stop twice, comparing the
`ECHO_CONFIRMED` outcome and status word with the keypad and output frequency; confirm a run is
refused while a fault is active; unplug the drive's RS485 pair while it runs and time the stop;
power-cycle the drive and confirm it comes up stopped; and check that status reads alone keep a
running drive's comm-loss timer from expiring. Resolve any `OUTCOME_UNKNOWN` by reading, never by
resending. Only a signed CP-06 lets the map become `VERIFIED` and move to the installation's
configuration.
