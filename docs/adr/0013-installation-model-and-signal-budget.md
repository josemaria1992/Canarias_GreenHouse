# ADR-0013 Full-installation twin, 48-point signal budget and the drive adapter
Status: proposed (pre-hardware release, stage 1). Facts from the owner's answers through 2026-10-12;
every dynamic, switch level, span and chemical constant is a labelled DEMO value.

## Context

Until now the twin was a 24-point demo (1000 L reservoir, three pumps, one return tank), kept from
v0.1. The owner's pre-hardware brief (12 Oct 2026) describes the real installation: a 20,000 L main
tank, three 5,000 L stock tanks (A, B, acid), a sump of about 5,000 L, two main pumps on one shared
drive (P1 serves sector S1 = channels 1-6, P2 serves S2 = channels 7-13 in AUTO), two return pumps,
three dosing/transfer pumps, nine valve duties, 13 channels of about 85 x 0.30 x 0.15 m retaining
0.05 m of water, 52 manually adjusted outlets and no agitator or air pump. The hardware is still
two Relay (D) modules and two AI 8CH modules: 16 DO, 16 DI and 16 AI.

## Decision

1. **A second twin topology, `installation`** (`twin/installation*.py`), selected by
   `twin.topology` in the configuration. The demo topology stays for the legacy fixtures and tests.
   Facts (`InstallationFacts`, provenance `OWNER_CONFIRMED`) and dynamics (`InstallationDynamics`,
   `ChemistryParams`, provenance `DEMO`) are separate models; a real profile still refuses DEMO twin
   parameters.
2. **Volume ledger over every store**: five tanks, 13 channels (retained depth is kept; only water
   above it drains), supply transit per sector, drain transit to the sump, external additions
   (mains, stock preparation as synthetic species), consumption and explicit spill. Residual is
   checked every scan; species are conserved the same way. Source-empty is unmet demand, never
   invented water.
3. **Synthetic chemistry fixture `synthetic-carbonate-ec-v1`**: species parcels (A, B, alkalinity,
   total carbonate, background EC, acid anion) mix by volume; pH from a carbonate charge balance, EC
   linear in species; the probe follows the bulk composition with a first-order lag (fast when water
   circulates, slow when still); excess CO2 degasses slowly. It exists only so controllers can be
   tested qualitatively (direction, settling, no-response, undershoot). It is not calibrated and must
   not be read as a nutrient or acid model.
4. **The 48-point allocation in `configs/installation-48.yaml` is a proposal** (labels say so). It is
   documented with its consequences in `docs/model/signal_budget.md`. The budget is short by one
   output and by several feedbacks; the software degrades explicitly:
   * the shared drive's run command is the 17th actuator. It is bound to a **drive adapter**
     (`drive_adapters` in the configuration) instead of a DO point. `SIMULATED` adapters exist only
     in SIMULATED profiles; a hardware profile has `NOT_CONFIGURED` until the VFD interface (Modbus
     register map, or a relay on an extra module) is confirmed. No register map is invented;
   * sector valves have open limits only: closure is "open limit released and no measured flow"
     (weaker evidence, shown as such);
   * stock outlet/fill valves have no position feedback: a path waits a fixed time, then proves itself
     by flow (transfers) or by level rise (stock fills, which have no meter);
   * no temperature input: pH/EC compensation is a commissioning item.
5. **Scan once per control period.** The installation runtime acquires all 48 points every
   `control_period_s` (0.5 s, the configured bus poll) while the plant integrates at `dt_s`; the demo
   topology keeps scanning every tick.

## Consequences

* Every new process can be exercised on the twin with all 48 points injectable on the I/O test source.
* The allocation, the adapter and the missing feedbacks are visible owner decisions, not hidden
  assumptions: adding a third relay module, or confirming VFD Modbus control, changes the
  configuration, not the controllers.
* The channel hold-up (about 16,575 L retained) follows from stated dimensions and is unverified.
* Nothing here is a physical verification; mocked tests stay mocked.

## Addendum 2026-10-13 (owner)

The owner chose option 1 of `docs/model/signal_budget.md`: all 16 DI and 16 DO are used as allocated
and no third relay module is added; the weaker valve evidence is accepted. The drive's run command
goes over the drive's own Modbus interface (ADR-0015): `MODBUS_RTU` against the simulated drive in
the twin, `NOT_CONFIGURED` on hardware until CP-06. The per-channel table remains a proposal.

## Addendum 2026-10-13, afternoon (owner)

The inventory is six modules, 80 points: the two Relay (D) and two AI boards above plus two IO 8CH
(SKU 26244) with 8 DI and 8 open-drain DO each (ADR-0016). "No third relay module" still holds; the
48-point allocation above is unchanged, and the 32 added channels stay unallocated until the owner
answers the proposal in `docs/model/signal_budget.md`.
