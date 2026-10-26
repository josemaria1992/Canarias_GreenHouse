# ADR-0026: graph water model, twin proof context, durable export apply, item alarms (0.7.0)

Status: proposed for 0.7.0 (DIGITAL_TWIN_SPECIFICATION, FEEDBACK_FOR_CLAUDE F01/F04/F05, FB-07, ALM-01).

## Decision

1. **Graph water model** (`twin/graph_water.py`, `graph-water-1`, now `graph-water-2`: see the amendment): when the twin has an explicit graph (ADR-0024),
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

## Amendment after the independent review of 26 October 2026 (R07-01 to R07-04)

1. **One proof context for both sides** (R07-01). The context digest is defined once in `programs/proof.py` and
   computed by the twin (when a run starts and ends, written into accepted evidence as `context`) and by the real
   system from its own records. The real system checks it when it lists a program (an outdated result is shown as
   "twin test outdated", kept as history), at activation, and at every new start of an active program (the live
   engine's `program_refusal`). A changed channel, item setting, water network or model parameter refuses
   activation and new starts until the version is tested again; an operation already running keeps the channels
   it switched on. Evidence written before this amendment has no `context` and therefore counts nowhere.
2. **Stop plans are executable** (R07-02). A block's `normal_stop` and `fault_stop` are compiled into the program's
   SEQUENCE (`normal_stop`, `fault_stop`; left out of the body while empty, so older digests do not change). The
   engine follows the normal plan when a user stops the operation, its runtime guard or its window ends, or the
   mode changes; the fault plan after a failure, a stop condition or a stop of everything (an empty plan: every item
   still on goes off, last first). Each OFF is verified (output and feedback) before the next step; an OFF not
   verified within its timeout holds the rest of the plan (STOP_UNVERIFIED, OFF retried, never an ON). Owner
   decision 2026-10-26: hold, because a pump must not pump against a closed valve and a bypass is not confirmed. What a plan
   leaves on is switched off after it, last first. The preview names the actual plans.
3. **Shared drivers are capacities** (R07-03, `graph-water-2`). Every running pump and every source is one capacity
   shared by all active paths through it; paths are filled together, max-min fair, until a capacity they use is
   full. A pump feeding two branches never moves more than its rated flow in all. Changing the model id makes
   every graph-model proof a new context.
4. **Journal write failures** (R07-04). A failed apply is journaled COMPENSATING before its undo; a record that
   cannot be written is kept in memory (listed `journal_unsaved`) and reported in the answer; a record that cannot
   be created before anything changes refuses the approval with nothing changed. At start every step recorded DONE
   is checked again against the real system: a step already taken back is UNDONE, one that is neither as applied
   nor as before is UNKNOWN and blocks undo. On Windows a replace refused by a passing reader lock is retried for
   about a second (`twin/feed.replace`), also for the twin feed files.

Tests: `tests/hardware/test_proof_context_r07.py`, `tests/unit/test_stop_plans_r07.py`,
`tests/unit/test_graph_water_r07.py`, `tests/hardware/test_journal_failures_r07.py`.

## Consequences

Tests: `tests/unit/test_graph_water.py`, `tests/hardware/test_twin_graph_070.py`, `tests/hardware/test_twin_proof_070.py`,
`tests/hardware/test_changes_durable_070.py`, `tests/unit/test_feedback_070.py`.
