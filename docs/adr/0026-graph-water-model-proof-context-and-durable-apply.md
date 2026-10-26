# ADR-0026: graph water model, twin proof context, durable export apply, item alarms (0.7.0)

Status: proposed for 0.7.0 (DIGITAL_TWIN_SPECIFICATION, FEEDBACK_FOR_CLAUDE F01/F04/F05, FB-07, ALM-01).

## Decision

1. **Graph water model** (`twin/graph_water.py`, `graph-water-1`): when the twin has an explicit graph (ADR-0024),
   water follows its paths only; a path is active when its valves are open and its pumps run; a tank gives water
   only through a running pump, a source pushes by itself, a sector's drain and a tank's overflow run by gravity;
   one rate per driver shared once; a tank never gives more than it holds; a full tank's excess goes to its overflow
   or is counted as spilled. A per-step ledger (initial + adjustments + supplied = stored + in transit + used +
   drained away + spilled) must balance within 1e-6 L per litre handled, else the run stops as invalid. Every
   number is an item property with its provenance; a missing one is NOT_MODELLED, never a default. Pressure,
   chemistry and suction are NOT_MODELLED. Without a graph the 0.6 drawing model still runs, labelled as such.
2. **Causal step** (F04): clock advance → water for that step → inputs published (with that step's readings) →
   engine tick. The same at every batch size; the page's picture is batched, the readings are not. The flow cache
   key includes pump rates (F05).
3. **Proof context** (F01): a twin result counts only from the current twin run and the context it started and
   ended in: program digest, every named item's type, settings and channels, and either the graph digest and model
   parameters (graph model) or the drawing digest (0.6 model). Moving a symbol in the graph model is not a new
   context; a connection change is. Earlier results stay visible as history.
4. **Durable apply** (FB-07): a twin export's approval writes its journal before anything changes, each step's
   intent before and result after, serializes approvals, undoes a failed apply and says "nothing was changed"
   only when the undo worked, and at start settles a record left mid-apply against the real system (INTERRUPTED).
5. **Item alarms** (ALM-01): item alarm limits (low, high, deadband, delay) are evaluated on the real readings of
   VERIFIED channels and in the twin; UNKNOWN neither raises nor clears.

## Consequences

Tests: `tests/unit/test_graph_water.py`, `tests/hardware/test_twin_graph_070.py`, `tests/hardware/test_twin_proof_070.py`,
`tests/hardware/test_changes_durable_070.py`, `tests/unit/test_feedback_070.py`.
