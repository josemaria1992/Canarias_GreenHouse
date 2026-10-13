# Signal budget: 80 points for the installation (proposal)

**Inventory (owner, 2026-10-13):** two Waveshare Modbus RTU Relay (D) modules, SKU 26517 (8 relay
DO + 8 DI each), two AI 8CH modules, SKU 25821 (8 AI each), and two Modbus RTU IO 8CH modules, SKU
26244 (8 DI + 8 **open-drain transistor sink** DO each, 500 mA maximum per output, not relay
contacts): **16 AI + 32 DI + 32 DO = 80 points** (ADR-0016, `digital_io_8ch.md`). The two Relay (D)
boards stay; no further module is required.

Configurations: `configs/installation-80.yaml` (SIMULATED, launcher default) and the earlier
`configs/installation-48.yaml`. The 48 relay/analog channels carry the allocation below, decided in
principle on 2026-10-13 (all 16 DI and 16 DO in use) but still a **per-channel proposal**. The 32
IO 8CH channels are **unassigned test points by owner decision (2026-10-13 evening)**: the plant
model reads and drives none of them, and the last section lists possible future uses only as a
proposal, not as devices known to be fitted. Physical wiring is TBD and nothing has been wired or
commissioned (ADR-0013).

## What the processes need

| Need | Count | Kind |
|---|---|---|
| Main pump selection K1/K2 (one shared drive) | 2 | DO |
| Shared drive run | 1 | **drive's Modbus interface** (owner, 2026-10-13) |
| Sector valves S1/S2 | 2 | DO |
| Return pumps RP1/RP2 | 2 | DO |
| Transfer/dosing pumps A, B, acid | 3 | DO |
| Stock outlet valves A, B, acid | 3 | DO |
| Mains fill valves: main, A, B, acid | 4 | DO |
| **Outputs** | **17** | 16 DO available |

The drive run command is the 17th output. It is bound to a **drive adapter** (`drive_adapters`), a
separate audited command path. **Owner decision, 2026-10-13: use all 16 DI and 16 DO as allocated and
send the drive's run command over its own Modbus interface** (ADR-0015,
`vfd_modbus.md`). In the twin the adapter is `MODBUS_RTU` against a simulated drive with a DEMO map;
on hardware it stays `NOT_CONFIGURED` until the real drive's map, copied from its manual, passes CP-06
on the Malmö bench. No real register map is assumed.

## Allocation

| Point | Proposed label | Signal |
|---|---|---|
| `relay_A.DO1` | K1 main pump 1 selection | `contactor.MP1.cmd` |
| `relay_A.DO2` | K2 main pump 2 selection | `contactor.MP2.cmd` |
| `relay_A.DO3` | Sector 1 valve | `valve.S1.cmd` |
| `relay_A.DO4` | Sector 2 valve | `valve.S2.cmd` |
| `relay_A.DO5` | Return pump 1 | `pump.RP1.cmd` |
| `relay_A.DO6` | Return pump 2 | `pump.RP2.cmd` |
| `relay_A.DO7` | Mains fill valve, main tank | `valve.FILL_MAIN.cmd` |
| `relay_A.DO8` | Mains fill valve, stock A | `valve.FILL_A.cmd` |
| `relay_B.DO1` | Transfer pump A | `pump.TP_A.cmd` |
| `relay_B.DO2` | Transfer pump B | `pump.TP_B.cmd` |
| `relay_B.DO3` | Transfer pump acid | `pump.TP_ACID.cmd` |
| `relay_B.DO4` | Stock A outlet valve | `valve.OUT_A.cmd` |
| `relay_B.DO5` | Stock B outlet valve | `valve.OUT_B.cmd` |
| `relay_B.DO6` | Acid outlet valve | `valve.OUT_ACID.cmd` |
| `relay_B.DO7` | Mains fill valve, stock B | `valve.FILL_B.cmd` |
| `relay_B.DO8` | Mains fill valve, acid tank | `valve.FILL_ACID.cmd` |
| `relay_A.DI1` | Drive running feedback | `drive.MAIN.running` |
| `relay_A.DI2` | Drive fault | `drive.MAIN.fault` |
| `relay_A.DI3` | K1 auxiliary contact | `contactor.MP1.closed` |
| `relay_A.DI4` | K2 auxiliary contact | `contactor.MP2.closed` |
| `relay_A.DI5` | Sector 1 valve open limit | `valve.S1.open_limit` |
| `relay_A.DI6` | Sector 2 valve open limit | `valve.S2.open_limit` |
| `relay_A.DI7` | Return pump 1 running | `pump.RP1.running` |
| `relay_A.DI8` | Return pump 2 running | `pump.RP2.running` |
| `relay_B.DI1` | Transfer pump A running | `pump.TP_A.running` |
| `relay_B.DI2` | Transfer pump B running | `pump.TP_B.running` |
| `relay_B.DI3` | Transfer pump acid running | `pump.TP_ACID.running` |
| `relay_B.DI4` | Main tank high-high switch | `tank.MAIN.high_high` |
| `relay_B.DI5` | Main tank low-low switch | `tank.MAIN.low_low` |
| `relay_B.DI6` | Sump high switch | `tank.SUMP.high` |
| `relay_B.DI7` | Sump low switch (return dry run) | `tank.SUMP.low` |
| `relay_B.DI8` | Safety chain healthy | `safety.chain_ok` |
| `analog_A.AI1` | Main tank level | `tank.MAIN.level` |
| `analog_A.AI2` | Sump level | `tank.SUMP.level` |
| `analog_A.AI3` | Stock A level | `tank.A.level` |
| `analog_A.AI4` | Stock B level | `tank.B.level` |
| `analog_A.AI5` | Acid tank level | `tank.ACID.level` |
| `analog_A.AI6` | Main tank pH | `main.ph` |
| `analog_A.AI7` | Main tank EC | `main.ec` |
| `analog_A.AI8` | Mains water flow to the main tank | `flow.FILL_MAIN` |
| `analog_B.AI1` | Sector 1 flow | `flow.S1` |
| `analog_B.AI2` | Sector 2 flow | `flow.S2` |
| `analog_B.AI3` | Sector 1 pressure | `pressure.S1` |
| `analog_B.AI4` | Sector 2 pressure | `pressure.S2` |
| `analog_B.AI5` | Return flow | `flow.RETURN` |
| `analog_B.AI6` | Stock A transfer flow | `flow.TP_A` |
| `analog_B.AI7` | Stock B transfer flow | `flow.TP_B` |
| `analog_B.AI8` | Acid transfer flow | `flow.TP_ACID` |

All analog spans and scaling profiles are DEMO (4-20 mA, linear). Real spans come from the
instruments' data sheets at commissioning.

## What is not observed, and what the software does instead

| Missing observation | Consequence in the controllers | Evidence quality |
|---|---|---|
| Sector valve closed limits | Closure = open limit released **and** sector flow below 0.5 L/min | weaker than a closed limit; shown as such |
| Position feedback on outlet and fill valves | A path waits 2 s after the open command, then proves itself by flow (transfers) or by level rise (stock fills) | indirect |
| Flow meters on stock fills | Stock fill verified by level rise (recipe: rise per window); fails `NO_FILL` otherwise | indirect, slow |
| Stock low switches | Stock reserve from the level transmitter only; a failed transmitter stops the path | single channel |
| Main tank temperature | No pH/EC temperature compensation; commissioning item | — |
| Return pressure, main-pump pressure feedback | Return proven by run feedback + flow only | — |
| Drive speed or current | Running feedback (DI) is the proof; once the Modbus map is verified, frequency and current can be read from the drive as information only | drive status registers |
| Mixing (no agitator, no recirculation pump) | Mixing readiness is a SIMULATED settling window; `NOT_VERIFIED` on any physical profile | none |

## Options for the owner

**Chosen 2026-10-13 (morning): option 1, with the drive's run command over Modbus.** Later the same day
the owner reported two IO 8CH modules, which supply the extra inputs option 2 asked for without a
third relay module; the owner then decided to leave all 32 of them unassigned (below). The options
are kept for the record.

1. Keep 48 points and confirm the VFD interface (Modbus or terminal) so the drive adapter can be
   configured; accept the weaker evidence above.
2. Add a third relay module (8 DO + 8 DI): drive run, sector valve closed limits and stock valve
   feedbacks fit, and one spare. This changes the configuration only, not the controllers.
3. Change the allocation (for example trade the sector pressures for valve feedback). Every point
   is a configuration entry; the controllers use signal names.

## The 32 added channels: unassigned (owner decision, 2026-10-13)

**Decision (owner, second re-review, 2026-10-13 17:19 UTC): all 32 added channels stay unassigned.**
`digital_A` and `digital_B` are unallocated test points with no bindings until the hardware and its
wiring are verified. All 16 added outputs, `digital_A.DO1` included, are spare/test-only. A hardwired
drive run is a possible future option, not an approved assignment and not a second command path:
the drive run stays on the simulated Modbus adapter, and the installation's physical drive interface
stays `NOT_CONFIGURED` (ADR-0015). No feedback device below is assumed fitted or purchased.

The table that follows is kept as a **proposal** for a later decision. It does not say that any of
these contacts or switches exist on the plant.

The IO 8CH inputs are dry-contact/voltage inputs and the outputs are DC open-drain sinks (500 mA
per channel; the bank total, supply voltage and suppression are still to be verified on the
received units). They must not switch mains voltage, AC contactor coils or motors directly.

The plant model has **no** sensors beyond those listed above, and this proposal adds none. Each
input row below names a device that might exist on the confirmed equipment (for example a
limit switch built into a motorised valve, or an auxiliary contact in a motor starter); it is used
**only if the owner confirms that the device is fitted and wired**. The signal names in the third
column already exist in the controllers and replace the weaker indirect evidence when they are
bound; rows marked "needs a controller change" do not exist yet.

| Point | Possible future use (proposal; not known to be fitted) | Signal | Effect on cause and effect when bound |
|---|---|---|---|
| `digital_A.DI1` | Sector 1 valve closed limit | `valve.S1.closed_limit` | Closure proven by the limit instead of "open limit released and flow below 0.5 L/min"; `VALVE_CLOSE_TIMEOUT` on the limit |
| `digital_A.DI2` | Sector 2 valve closed limit | `valve.S2.closed_limit` | as above |
| `digital_A.DI3` | Stock A outlet valve open limit | `valve.OUT_A.open_limit` | Path waits for the limit (else `VALVE_OPEN_TIMEOUT`) instead of a fixed 2 s |
| `digital_A.DI4` | Stock B outlet valve open limit | `valve.OUT_B.open_limit` | as above |
| `digital_A.DI5` | Acid outlet valve open limit | `valve.OUT_ACID.open_limit` | as above |
| `digital_A.DI6` | Mains fill valve, main tank, open limit | `valve.FILL_MAIN.open_limit` | as above |
| `digital_A.DI7` | Mains fill valve, stock A, open limit | `valve.FILL_A.open_limit` | as above |
| `digital_A.DI8` | Mains fill valve, stock B, open limit | `valve.FILL_B.open_limit` | as above |
| `digital_B.DI1` | Mains fill valve, acid tank, open limit | `valve.FILL_ACID.open_limit` | as above |
| `digital_B.DI2` | Stock A low switch | `tank.A.low` (needs a controller change) | Second, independent stop for transfer A at reserve (today: level transmitter only) |
| `digital_B.DI3` | Stock B low switch | `tank.B.low` (needs a controller change) | as above for B |
| `digital_B.DI4` | Acid tank low switch | `tank.ACID.low` (needs a controller change) | as above for acid |
| `digital_B.DI5` | Drive "ready" relay output | `drive.MAIN.ready` (needs a controller change) | Refuse RUN while not ready |
| `digital_B.DI6` | Return pumps: motor protection tripped (common contact) | `pump.RP.protection_trip` (needs a controller change) | Return lockout with the cause shown |
| `digital_B.DI7` | Transfer pumps: motor protection tripped (common contact) | `pump.TP.protection_trip` (needs a controller change) | Refill/acid/stock lockout with the cause shown |
| `digital_B.DI8` | Spare (test only) | — | — |
| `digital_A.DO1`-`DO8`, `digital_B.DO1`-`DO8` | Spare (test only), by owner decision | — | — |

A hardwired drive run (an interposing relay driven by a transistor output, or a sinking drive input
within the module's rating) remains a **future option** to consider only if CP-06 shows that the
drive's comm-loss timer is not reset by reads (ADR-0015 §Consequences). It would need a new owner
decision and an ADR, and would replace the Modbus run command rather than run beside it.

`installation-80` keeps the 32 channels unallocated and every controller uses the indirect evidence
described above. Any later binding needs the owner to confirm the device is fitted and wired.
