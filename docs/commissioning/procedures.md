# Milestone 5: hardware arrival procedures

**None of these procedures has been performed.** They need the hardware on an isolated bench, a
qualified operator, and the owner's written authorization of the *specific* setup (template field
"Authorization"). Completing them does not release live field control, and passing them is not a
safety assessment of the greenhouse.

Ground rules for every procedure:

- No pumps, dosing equipment or field loads connected. DO tests use dummy loads only (indicator
  lamps or LEDs within the relay rating, on a separately fused bench supply).
- One module on the bus while its address is being proven.
- The software never writes addresses, baud rates, parity or modes. If a module must be
  reconfigured, that is a separate manual vendor-tool step, one device at a time, with before/after
  evidence recorded in CP-01, and never done with this application.
- Record actual values, not expected ones. Leave a field blank rather than guess. Attach logs
  (`diagnostics read` JSON lines, exported CSV, event export) and photos.
- A passive bus monitor (the USB TO RS485 (C) adapter on a second PC, receive only) is recommended
  for CP-05 so "zero write frames" is evidenced on the wire, not only by the software's own count.

| ID | Purpose | Needs lease? | Template |
|---|---|---|---|
| CP-00 | Platform record: OS image, UART/tty mapping, RTC, performance | no | `templates/CP-00_platform_record.md` |
| CP-01 | Read-only identification of each module | no | `templates/CP-01_module_identification.md` |
| CP-02 | AI channel stimulus with the FNIRSI SG-003A | no | `templates/CP-02_ai_stimulus.md` |
| CP-03 | DI stimulus | no | `templates/CP-03_di_stimulus.md` |
| CP-04 | DO on dummy loads under a bench lease, with teardown | **yes** | `templates/CP-04_do_dummy_load.md` |
| CP-05 | Fault and zero-write checks (disconnect, restart, power loss) | no | `templates/CP-05_faults_zero_writes.md` |
| CP-06 | Drive Modbus interface: status bits, run/stop, comm loss (motor disconnected) | reads no; run/stop **yes** | `templates/CP-06_drive_modbus.md` |

Order: CP-00 → CP-01 (each module) → CP-05 read-only parts → CP-02 / CP-03 → CP-04 → CP-05 lease
parts → CP-06. Stop at the first unexpected result and record it. CP-01 to CP-06 run on the Malmö
bench (`configs/bench-malmo.example.yaml`, Europe/Stockholm) before the hardware goes to the
installation (Atlantic/Canary).

## CP-00 Platform record
Flash and configure per `docs/installation/cm4.md`. Fill the UART table by reading one known module
per port with `diagnostics read`. Record OS image checksum, `uname -a`, Python version,
`pip freeze`, RTC check, and measure: poll cycle time per bus (`/api/status`), CPU load at
48 points / 3 s polling, snapshots per hour (60 expected), database growth over 24 h and the
365-day projection (`aquacrop storage report`), free disk after installation, and the time and size
of one backup and of `backup verify --full` (ADR-0008 budget check).

## CP-01 Module identification (read-only)
For each module alone on the bus: photograph labels (model, SKU, revision), record the expected
address and serial settings, run
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

## CP-05 Faults and zero writes
Read-only profile, passive monitor on the bus. Record frames while: starting the service,
unplugging one module's RS485 pair (only its points go UNAVAILABLE, `DEVICE_COMM` alarm for it
alone, backoff visible), reconnecting it, restarting the service, stopping it, and cutting CM4 power.
Expected: zero FC05/FC06/FC0F/FC10 frames throughout; relays keep their state across all of it.

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
