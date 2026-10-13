# Signal budget: 48 points for the installation (proposal)

Configuration: `configs/installation-48.yaml` (SIMULATED). Hardware: two Waveshare Modbus RTU
Relay (D) modules (8 DO + 8 DI each) and two AI 8CH modules (8 AI each): **16 DO, 16 DI, 16 AI**.
Every allocation below is a **proposal** for the owner to confirm or change; the physical wiring is
TBD and nothing has been wired or commissioned (ADR-0013).

## What the processes need

| Need | Count | Kind |
|---|---|---|
| Main pump selection K1/K2 (one shared drive) | 2 | DO |
| Shared drive run | 1 | DO **or drive interface** |
| Sector valves S1/S2 | 2 | DO |
| Return pumps RP1/RP2 | 2 | DO |
| Transfer/dosing pumps A, B, acid | 3 | DO |
| Stock outlet valves A, B, acid | 3 | DO |
| Mains fill valves: main, A, B, acid | 4 | DO |
| **Outputs** | **17** | 16 DO available |

The drive run command is the 17th output. It is bound to a **drive adapter** (`drive_adapters`), a
separate audited command path that is `SIMULATED` in the twin and `NOT_CONFIGURED` on hardware until
the VFD interface is chosen: a Modbus register map from the drive's manual, a digital input on the
drive driven by an extra relay, or a third relay module. No register map is assumed.

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
| Drive speed or current | Running feedback only (DI); no load-based diagnostics | — |
| Mixing (no agitator, no recirculation pump) | Mixing readiness is a SIMULATED settling window; `NOT_VERIFIED` on any physical profile | none |

## Options for the owner

1. Keep 48 points and confirm the VFD interface (Modbus or terminal) so the drive adapter can be
   configured; accept the weaker evidence above.
2. Add a third relay module (8 DO + 8 DI): drive run, sector valve closed limits and stock valve
   feedbacks fit, and one spare. This changes the configuration only, not the controllers.
3. Change the allocation (for example trade the sector pressures for valve feedback). Every point
   is a configuration entry; the controllers use signal names.
