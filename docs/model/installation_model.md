# Installation twin: topology, equations, ledger, chemistry fixture, scan timing

Topology `installation` (`twin.topology` in the configuration; ADR-0013). Code:
`src/aquacrop/twin/installation.py` (plant), `installation_params.py` (facts and DEMO dynamics),
`chemistry.py` (synthetic fixture), `observe.py::observe_installation` (signals),
`installation_runtime.py` (scan, routing, journal) and `control/` (process controllers, ADR-0014).

Two kinds of numbers appear below and are never mixed:

* **OWNER_CONFIRMED** facts (owner answers through 2026-10-12, approximate where the owner said so):
  tank sizes, equipment counts, sectors and channel geometry.
* **DEMO** values: every flow rate, delay, switch level, transmitter span and chemical constant. They
  exist so the controllers can be exercised. None is an engineering fact, and a real profile refuses
  a twin marked DEMO.

## Topology

```text
 mains --FILL_MAIN--> [MAIN 20,000 L] --K1|K2 select, one shared drive--> header
                          ^                 --> S1 valve --> supply transit --> channels 1-6  (4 outlets each)
                          |                 --> S2 valve --> supply transit --> channels 7-13 (4 outlets each)
                          |                                                     |
                          |              retained depth kept; water above it drains
                          |                                                     v
                          +---- RP1|RP2 <---- [SUMP ~5,000 L] <---- drain transit (gravity)
 mains --FILL_A|B|ACID--> [A | B | ACID 5,000 L each] --TP_x pump + OUT_x valve--> MAIN
 plants take water (and part of A/B) from the channels: consumption, leaves the system
```

| Fact (OWNER_CONFIRMED) | Value |
|---|---|
| Main tank | 20,000 L |
| Stock tanks A, B, acid | 5,000 L each, prepared by hand |
| Sump | about 5,000 L |
| Main pumps | MP1, MP2 on one shared drive; K1/K2 select one motor at a time |
| Sectors (AUTO pairing) | S1 = channels 1-6 served by MP1; S2 = channels 7-13 served by MP2 |
| Channels | 13, about 85 x 0.30 x 0.15 m, 0.05 m retained (1,275 L retained, 3,825 L capacity each) |
| Outlets | 4 per channel (52), adjusted by hand; not electric valves |
| Return pumps | RP1, RP2 (sump to main tank) |
| Dosing/transfer pumps | TP_A, TP_B, TP_ACID |
| Agitator, air pump, base dosing | none |

The 13 channels retain about 16,575 L between them. That follows from the stated dimensions and is
unverified.

## Units and integration

Volume L, flow L/min, time s, pressure bar. One `step()` advances one fixed tick `dt_s` (0.1 s
default; long scenarios use 0.5 s). For every store, `delta_V = (sum Q_in - sum Q_out) * dt / 60`.
Speed (1x, 10x, 60x) only changes how many ticks run per wall second, so results are identical at
every speed.

## Equipment

| Equipment | Model (DEMO constants in `InstallationDynamics`) |
|---|---|
| Valves (sector, outlet, fill) | first-order position toward 0/1, `valve_tau_s` 1 s; faults `STUCK_CLOSED`, `STUCK_OPEN` |
| Direct-on-line pumps (RP, TP) | first-order speed, `pump_tau_s` 1 s; no power when the safety chain is open; faults `NO_RUN`, `STUCK_ON`, `NO_FLOW` |
| Shared drive | ramps 0 to 1 in `drive_accel_s` / `drive_decel_s` (3 s); a trip or an open safety chain removes the output at once (motor coasts); faults `TRIP`, `NO_RUN`, `STUCK_ON` |
| Contactors K1/K2 | change state `contactor_s` (0.1 s) after the request; faults `WELDED`, `NO_CLOSE`; the proposed hardwired mutual interlock blocks closing one while the other is closed |
| Main pump motors | speed = drive speed when its contactor is closed; faults `NO_RUN`, `NO_FLOW` |

Synthetic drive protection, modelled because the brief requires the sequence to avoid it: switching a
contactor while the drive output is above 5 % speed, or two motors closed on a running drive, trips
the drive (`OUTPUT_SWITCHED_UNDER_LOAD`, `TWO_MOTORS_ON_DRIVE`). Trips and interlock blocks are recorded
as hazards; the PC scenarios assert there are none in normal operation.

## Hydraulics

```text
capacity_L_min   = main_pump_rated_L_min * min(1, sum of motor speeds without NO_FLOW)
demand(sector)   = sector_rated_L_min * valve position * mean outlet opening of the sector
Q_total          = min(capacity, sum demand)              split between sectors by demand
pressure(sector) = shutoff_bar * speed^2 * max(0, 1 - 0.5 * load^2)    (0 when the valve is closed)
```

Supply and drain pipes are transit queues of parcels (`supply_transit_s` 10 s, `drain_transit_s`
60 s). In a channel, plant uptake (`channel_uptake_L_min`, 0.25 per channel) leaves first; water
above the retained depth drains with `channel_drain_tau_s` (120 s); water above the channel capacity
spills. A sector's water is shared among its channels by the sum of their outlet openings.

Return: `Q = return_pump_rated_L_min * pump speed` per running pump, sump to main tank. Transfers:
`Q = transfer_rated_L_min * pump speed * outlet valve position`, stock to main tank. Mains fills:
`Q = fill_rated_L_min * fill valve position`, only while mains water is available. A pump asks for
water; if the tank cannot supply it, the shortfall is recorded as **unmet demand**, never invented.

## Ledger

Every store (5 tanks, 13 channels, supply and drain transit) is counted. Each tick:

```text
residual_L = stored + consumption + spill - external supply - initial stored     (must stay ~0)
```

External supply is mains water into MAIN, A, B and acid. Stock preparation adds species but no water.
Spill is explicit per tank and per channel. The runtime checks the residual at every scan and the
scenarios assert it at the end. Species are conserved the same way (CO2 lost to degassing is a ledger
term). The ledger also reports delivered water per sector and per channel, drained, returned and
transferred volumes.

## Chemistry fixture `synthetic-carbonate-ec-v1`

**Synthetic and uncalibrated.** It exists so the controllers can be tested qualitatively: direction
of response, settling, no response, undershoot. It prescribes no physical amounts and must not be
read as a nutrient or acid model.

* Water carries species: A and B (litres of prepared stock), alkalinity (meq; acid adds negative
  alkalinity), total inorganic carbon (mmol), background EC from mains water and the acid anion. Every
  transfer moves them in proportion, so mixing adds amounts and never averages pH.
* pH comes from one carbonate equilibrium (pKa 6.35) solved from alkalinity and total carbon, valid
  below about pH 8.3. EC is linear in the species concentrations.
* The probe sees a lagging local composition: `tau` is 180 s while water flows in or out of the main
  tank and 1,800 s when it is still. This stands in for mixing; no mixing actuator exists.
* Excess CO2 degasses slowly (6 h time constant), so pH drifts up over hours.
* Plants take A and B at 70 % of the channel water's concentration.
* "Prepare stock" (simulation only) restores a stock tank to its synthetic strength. It does not
  acknowledge readiness; that stays an operator action (ADR-0014).

A calibrated model can replace the fixture through the `ChemistryModel` interface once measured data
exist.

## Observation layer

`observe_installation` turns plant state into the 48 signals of the allocation (plus signals no
point carries yet, such as sector closed limits, stock low switches and temperature, which stay
unbound). Switches compare against DEMO levels in `LevelSwitches`. Transmitters produce raw 4-20 mA
microamps over DEMO spans with optional noise. pH and EC come from the probe composition, not the
bulk. Sensor faults are observation overrides and never change volumes.

## Scan timing

The plant integrates every `dt_s`. The runtime acquires all bound points and runs the supervisor
once per `control_period_s` (0.5 s, the configured bus poll), so the controllers see what a real
scan would. An observation is unusable when it is unbound, has no sample, has bad quality, or is
older than `max(0.5 s, 2 x control_period_s)` (STALE). Desktop throughput is about 1,300 to 4,000
ticks per second, enough for 60x. Throughput on a CM4 has not been measured.

## What is not modelled

Pump curves, pipe friction, water hammer, valve characteristics, real stock concentrations, nutrient
chemistry beyond the fixture, temperature effects, probe drift and fouling, evaporation and
crop-specific uptake. The VFD interface, wiring and all production numeric values are TBD.
