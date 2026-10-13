# Causal process twin: equations, assumptions, AUTO sequence

> **Scope:** this document describes the legacy `demo` topology (the 24/48-point demo fixtures kept
> from v0.1). The confirmed installation (20,000 L main tank, three stock tanks, sump, shared drive,
> 13 channels) is the `installation` topology: see `installation_model.md`, `signal_budget.md`,
> `cause_and_effect.md`, ADR-0013 and ADR-0014.

**Every number in this document is a DEMO proposal** (brief §7). None is an engineering fact about
the AquaCrop greenhouse. No pump curve, pressure, pipe, chemistry, EC/pH or crop-uptake model
exists. The production topology is `TBD — requires operator confirmation`.

Code: `src/aquacrop/twin/plant.py` (physics), `observe.py` (sensors), `runtime.py` (tick order,
journal, replay), `control/auto.py` (AUTO), `twin/params.py` (parameters).

## Topology (demo fixture)

```text
 external supply --FILL valve--> [reservoir 1000 L] --P1|P2|P3 (one selected)--+--S1 valve--> channels 1..6
                                        ^                                       +--S2 valve--> channels 7..13
                                        |                                                      |
                              return transfer 12 L/min <-- [return tank 200 L] <-- 10 s transit <-- 80 % return
                                                                                  20 % consumption (leaves the system)
 logical tanks A / B / acid (20 L each): stored, no active edges
```

## Units and integration

Volume in L, flow in L/min, time in s. Fixed step `dt = 0.1` simulated s. For every tank:

```text
delta_V_L = (sum(Q_in_L_min) - sum(Q_out_L_min)) * dt_s / 60
```

Speeds 1×/10×/60× only change how many fixed ticks run per wall second; the integration step and
the event order never change, so results are identical at every speed (test AT-TWN-03).

## Actuators

First-order response toward the commanded position (0 or 1):

```text
x(t+dt) = target + (x(t) - target) * exp(-dt / tau)      tau = 1 s (demo); tau = 0 means x = target
```

Faults change the target: pump `NO_RUN` (target 0), `STUCK_ON` (target 1), `NO_FLOW` (spins, so run
feedback is true, but contributes no flow); valve `STUCK_CLOSED` / `STUCK_OPEN`. Commanded state and
actual state are separate fields and can disagree.

## Flow and allocation

```text
drive        = min(1, sum(pump position, excluding NO_FLOW pumps))
demand_s     = rated_flow_s (12 L/min) * valve_position_s * drive
demand_vol   = sum(demand_s) * dt / 60
factor       = 1                         if demand_vol <= reservoir volume
             = reservoir / demand_vol    otherwise   (every outlet scaled by the same factor)
actual_s     = demand_s * factor
channel flow = actual_s / number of channels in sector s     (channels sum exactly to the sector)
unmet demand = demand_vol - extracted                        (recorded, never hidden)
```

Of the extracted water, `return_fraction` (0.8) enters a 10 s transit delay line and then the
return tank; the rest is recorded as external consumption. When enabled, the return tank transfers
up to 12 L/min back to the reservoir. The FILL valve adds `30 L/min * position` of external supply.

## No water is created or destroyed

A tank never clamps: water beyond capacity goes to a per-tank cumulative spill ledger, and
extraction is bounded by what is present. The invariant checked every tick:

```text
stored tanks + transit buffer + logical tanks + cumulative consumption + cumulative spill
  - cumulative external supply  ==  initial accounted water
```

Tolerance `1e-6 L + 1e-9 L * ticks` (floating point only). A violation is recorded as an invariant
failure and fails `demo verify`.

## Observations (virtual sensors)

The observation layer turns plant state into raw module values exactly as a real module would
report them: AI in µA (4–20 mA loop, `4000 + 16000 * fraction`, saturating at 20500 µA), DI as
booleans. Engineering values come from the versioned DEMO scaling profiles, the same code path a
real channel uses. Unmapped points stay `UNAVAILABLE / UNMAPPED`. Noise is optional and seeded; the
default is zero for deterministic acceptance.

Signal precedence per point: explicit override (owner, reason, base value, optional expiry in
simulated time) > base source (`TWIN` observation or `IO_TEST` injection). A sensor override
changes what the controller sees; it never changes tank volume. An actuator fault changes the
plant.

## AUTO sequence (simulation only)

```text
IDLE -> PRECHECK -> OPEN_SELECTED_VALVE -> START_SELECTED_PUMP -> VERIFY_FLOW -> IRRIGATE
     -> STOP_PUMP -> CLOSE_VALVE -> PAUSE -> NEXT_SECTOR -> PRECHECK ...
any fault or STOP -> stop sequence (pumps off, then valves after 1 s) -> FAULT_LOCKOUT or STOPPED
```

| Parameter (DEMO) | Value |
|---|---|
| Irrigate / pause | 15 s / 10 s per sector |
| Flow verification grace | 3 s after pump start |
| Minimum observed flow / sustained failure | 6 L/min / 2 s |
| Valve open timeout / pump run-feedback timeout | 5 s / 3 s |
| Minimum reservoir for a start | 50 L |
| Sector order / selected pump | S1, S2 / P1 |

Precheck requires a valid configuration, healthy fresh observations, no blocking or latched alarm,
one pump owner, no conflicting sector, enough volume and durable audit storage. Faults are evaluated
before normal timing transitions on every tick. Irrigated time and delivered volume come from the
observed flow, not from timers. Lockout recovery needs: condition cleared, alarm acknowledged,
lockout reset, then a deliberate new AUTO request. Clearing an alarm never starts AUTO.

## Cadences

- Physics: 0.1 s simulated.
- Simulation history: a complete snapshot every `recording_interval_s` (60 s) of **wall time**, the
  same recorder and the same slot labels as on real hardware (ADR-0008, revised after the owner
  review). Each snapshot stores the run id and the simulated time as provenance, so 1×, 10× and 60×
  produce the same number of snapshots per wall hour. A paused (or stalled) run keeps recording:
  after 3 s without progress its frozen state is stored as `STALE` with the last good value kept,
  never as a fresh reading and never back-filled. Readings older than the controller's 0.5 s staleness limit
  are stored as `STALE`. Alarm transitions, commands and DI/DO edges are written when they happen.
- Simulation-time history (optional, off by default): `historian.sim_time_recording_s` adds a
  separately labelled series (`cadence_basis = SIMULATION_TIME`, cycle ids `simtime/...`) every that
  many simulated seconds, never more often than `sim_time_recording_min_wall_s` of wall time. It is
  counted separately in the storage report and selected in trends with `basis=SIMULATION_TIME`.
- Real hardware: `poll_interval_s` of wall time per bus for acquisition and alarms; the historian
  snapshots every 60 s of wall time, labelled with the wall-clock boundary (ADR-0008).

## Replay

A run is identified by seed, configuration version and a tick-indexed journal of every input
(commands, overrides, faults, state requests). Replay rebuilds the run from the journal; same-tick
inputs apply in journal order. The state hash after replay equals the original (AT-TWN-04).

## What the twin is not

Not calibrated, not a prediction of the real greenhouse, not a hydraulic design tool. The
`HYBRID_SHADOW` comparison with real telemetry is not available until a physical mapping is
approved (ADR-002).
