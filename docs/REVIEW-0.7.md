# AquaCrop 0.7.0: review brief for the architect

What changed from 0.6.13 to 0.7.0, where it lives, how it was checked, and what is still open. The detailed record,
with each acceptance case marked PASS / FAIL / BLOCKED / NOT RUN, is `docs/RESULTS-0.7.md`. Read that file for
evidence. This brief is the map.

- Repository `josemaria1992/Canarias_GreenHouse`. Branch `feat/operator-upgrade-0.7`. Base
  `feat/equipment-programs-twin` at `ffdc7bd` (0.6.13).
- Draft PR #11: https://github.com/josemaria1992/Canarias_GreenHouse/pull/11. It is a draft and is not merged.
  Nothing was deployed, installed or energized.
- Scope: the owner's upgrade package of 25 October 2026 (READ_FIRST, CLAUDE_CODE_MASTER_PROMPT,
  ACCEPTANCE_TEST_PLAN). It covers milestones M0 to M7.
- Size: 105 files changed, about +8,300 / −400 lines.

## Commits

| Commit | Content |
|---|---|
| `4560471` | M0: review defects F01–F05, R34-01 and FB-07 (durable export apply). M1: role matrix (ADR-0023) |
| `a850471` | M2–M4: explicit topology, blocks, orthogonal router and editor, program forms, graph water model, item alarms |
| `c3e7a1e` | Ports entered directly, editor adopted only when shown, panels without nulls, full twin evidence for operators, operators propose channels |
| `c5ac7cb` | M5–M7: history runs, simulated modules, graph export, module requests, browser journey, docs |
| `d3ef78e` | Test record (suite, browser journey, release) |
| `66ac861` | CI fix: a run's timeline starts at its own `PROGRAM_OP_STARTED`; UI-01 nav label |
| `bd6ab4e` | This brief; CM4 OS recorded; results branch name corrected. The release ZIP is built from this commit |
| (next commit) | New release SHA-256 recorded |

## M0: review defects (`4560471`)

- **F01. Twin proof is bound to its context.** Proof counts only from the current twin run. It is also tied to the
  tested context digest: program body, item type, settings and channels, water network, and model. A reset, a
  rebinding or a context change makes the proof stale. Code: `twin/equipment_service.py`, `programs/store.py`.
  Tests: `tests/hardware/test_twin_proof_070.py`.
- **F02. Equipment limits are checked before every ON.** The limits are minimum OFF time and starts per hour. The
  check covers later sequence steps too. Usage persists across restarts in schema 6 `equipment_usage`, per twin
  run. Code: `programs/engine.py`, `historian/schema.py`.
- **F03. FAULT inputs.** A fault input inhibits a start and its re-arm, and stops a running operation with a
  lasting `EQUIPMENT_FAULT` alarm. Inverted inputs, stale inputs and inputs required but unbound are handled. The
  twin has a TRIP fault. Tests: `tests/unit/test_feedback_070.py`.
- **F04.** One fixed causal step at every batch size. Water readings are published each step (`twin/water.py`).
- **F05.** Pump rate is part of the flow-cache key.
- **R34-01. Preview precondition.** A process-state or manual request carries the state its preview showed, and is
  refused if that state changed. When another session changes the process, an open confirmation is dropped.
  Tests: `tests/integration/test_r34_01_preview_precondition.py`.
- **FB-07. Durable apply of twin exports.** The apply journals before every change and reports partial failures
  honestly. An interrupted apply is settled at start. Concurrent approvals apply once. Code: `api/changes.py`,
  `twin/changes.py`. Tests: `tests/hardware/test_changes_durable_070.py`.

## M1: roles (ADR-0023)

- **Navigation per role.**
  - Viewer: Overview, Programs, Alarms.
  - Operator: Overview, Blocks, Digital Twin, Add Module, Programs, Operation History, Alarms & Events.
  - Administrator: everything.
- **Server checks.** `READ`, `OPS_READ` and `ADMIN_READ` are enforced per route, so the server refuses what the
  menu hides. Viewers are refused by the twin.
- **Module configuration.** Writing a module's address and modes is administrator-only, even for an operator with
  the commissioning flag. An operator's module request is a record, never configuration. Operators get bounded
  discovery (`MODULE_REGISTER`), and their channel bindings stay PROPOSED.
- Code: `security/auth.py`, `api/app.py`, `web/app.js`. Tests: `tests/integration/test_rbac_070.py`.

## M2–M3: explicit graph, blocks, orthogonal drawings (ADR-0024)

- **Topology.** The topology is explicit: nodes and edges only. Its digest is separate from the drawing's.
  Moving a symbol or a segment changes the drawing, never the connections. Code: `topology/definitions.py`,
  `topology/validation.py`, `diagram.py`.
- **Router.** Routing is orthogonal, including while dragging. Crossing pipes show a gap and stay unconnected.
  Code: `web/router.js`. Tests: `tests/js/router.test.js` (run in Node via `tests/unit/test_router_js.py`).
- **Editor.** Works with mouse, touch and keyboard (`web/hydraulic-editor.js`). It has two guards:
  - `grab()` keeps focus on the canvas.
  - The page redraw never interrupts focus inside `[role=application]` or a recent choice (`web/app.js`).
- **0.6 migration.** `topology/migrate.py` turns a 0.6 drawing into a reviewed proposal:
  - Only explicit links become edges.
  - INLINE, JOIN and PIPE_END cases and unlinked symbols are listed as questions, never connected.
  - The old drawing is referenced by its digest and left unchanged.
  - Tests: `tests/unit/test_migration_070.py` (TOPO-06, DEP-02).
- **Blocks.** A block is one purpose (source, destination, path along drawn pipes, meter on that path) with an
  example start/stop plan. Code: `blocks/`, `api/blocks.py`, `web/blocks.js`. Tests:
  `tests/integration/test_blocks_070.py`.

## M4: no-code programs (ADR-0025)

- **Program forms.** A program is defined by where, how much (time, measured volume, or manual within a maximum),
  when (days and times, a date range, a repeat inside a window) and start/stop conditions. It is compiled to the
  engine's plan, and the preview shows the exact steps. There is no JSON. Older programs run unchanged. Code:
  `programs/authoring.py`, `programs/definitions.py`, `programs/schedule.py`, `web/programs.js`. Tests:
  `tests/integration/test_program_forms_070.py`, `tests/unit/test_programs_070.py`.
- **Equipment forms.** Typed properties with units, scaling and alarm limits (with delay and deadband). Tanks,
  sources and sectors need no channel. Limits are evaluated only on real readings of verified channels, and in
  the twin. Code: `equipment/definitions.py`, `equipment/views.py`, `control/alarms.py`. Tests:
  `tests/unit/test_item_alarms_070.py`.

## M5: twin on the graph (ADR-0026)

- **Graph water model.** Water moves along the explicit graph (`twin/graph_water.py`). A ledger balances every
  step, and a missing number is shown as NOT MODELLED. Flows and tank levels are drawn on the twin's graph
  (`web/twin-graph.js`).
- **Graph refresh.** The twin snapshot publishes the topology/drawing revisions (`graph_water.revs`). The graph
  card reloads when they change; a sequence counter discards stale responses.
- **Simulated modules.** Can be added in the twin and are never exported (`twin/virtual_modules.py`,
  `web/twin-modules.js`).
- **Exports.** An export now carries the graph, the drawing and the blocks. Tests:
  `tests/hardware/test_twin_graph_070.py`, `tests/hardware/test_twin_modules_export_070.py`.

## M6: history and alarms

- **Operation History.** Program runs with result and volume, a timeline of recorded steps, audited commands and
  alarms, and a CSV export. Code: `historian/runs.py`, `api/runs.py`.
- **CI fix in `66ac861`.** The timeline starts at the run's own start event. An alarm cleared in the same
  instant, recorded first, no longer leads the timeline.
- **Schema 6.** Adds equipment usage, graph and drawing revisions, blocks, and twin runs. 0.6.13 refuses a
  schema 6 database by design; going back needs the pre-upgrade backup (`docs/operations/upgrade-0.7.md`).

## M7: release and documents

- ADR-0023 to ADR-0026 (`docs/adr/`) and the ADR index.
- Documents: `docs/CAPABILITIES-0.7.md`, `docs/operations/upgrade-0.7.md`, `docs/RESULTS-0.7.md`, CHANGELOG,
  AGENTS.md (invariants 8b/8c, role rule 11, rule 13), HANDOVER.
- Walkthrough with real screenshots from the journey: `docs/operator/walkthrough-0.7.md` and
  `walkthrough-0.7/` (8 PNGs, `checks.json`).
- **Browser journey.** `scripts/ui_check/run_journey_070.py` + `journey_070.cjs`. It drives headless Chromium with
  real mouse drags and API read-backs, across three roles, at desktop and tablet width, and through the twin.

## Corrections after the independent review of 26 October 2026

The review of head `7132198` asked for changes (R07-01 to R07-04). All four were reproduced from its probes and
corrected. Each probe is now an expected-correct regression test. The design is recorded in the ADR-0026
amendment.

| Finding | Correction | Code | Tests |
|---|---|---|---|
| R07-01 live activation accepted proof from another binding | One proof-context definition for the twin and the real system. The live side checks it when it lists programs, at activation and at every new start of an active version. A run in progress keeps its held channels | `programs/proof.py`, `api/app.py`, `api/operation.py`, `api/programs.py`, `programs/store.py`, `programs/live.py`, `programs/engine.py` | `tests/hardware/test_proof_context_r07.py` (real twin evidence, rebinding to DO2, activation 409, run-now 409 when already active, zero ON frames; binding, setting, topology and model changes; a moved symbol keeps the proof) |
| R07-02 the configured fault plan never ran | The block's normal and fault stop plans are compiled into the SEQUENCE and followed step by step with their waits. Each OFF is verified before the next step, and an unverified OFF holds the rest of the plan | `programs/definitions.py`, `programs/authoring.py`, `programs/engine.py`, `api/programs.py`, `web/programs.js` | `tests/unit/test_stop_plans_r07.py` (pump OFF at 0.5 s, valve still open at 9.5 s, closed after 10 s; user stop and runtime guard follow the normal plan; stuck pump holds the valve; no plan: all OFF) |
| R07-03 shared pump capacity counted twice | Every pump and source is one capacity over all its active paths (max-min fair fill). Model id `graph-water-2` | `twin/graph_water.py` | `tests/unit/test_graph_water_r07.py` (20 L/min main pump: 10 + 10; with a 5 L/min booster: 15 + 5; series; two sources; emptying tank; batch-independent) |
| R07-04 stale DONE after failed journal writes | Compensation is journaled (COMPENSATING). An unwritable record is kept in memory and reported. Recovery checks every DONE step against the real system and marks steps UNDONE or UNKNOWN; UNKNOWN blocks undo. Windows replace refusals are retried | `api/changes.py`, `twin/feed.py`, `twin/changes.py` | `tests/hardware/test_journal_failures_r07.py` (the review's two-denial probe, a journal that cannot be written at all, a record that cannot be created, an unknown step, a passing lock) |

One behaviour choice needs the owner's confirmation. When a stop plan's OFF cannot be verified (for example a pump
that keeps running), the engine holds the rest of the plan, so the valve stays open rather than closing on a
running pump. It sends the OFF again and raises an alarm. Session end still applies the configured LEASE_END
safe state to every output.

## Evidence

| What | Result |
|---|---|
| Full suite (Windows, Python 3.12) | 694 passed, 8 skipped (Linux/POSIX-only by design), 0 failed |
| Browser journey (simulation) | 29/29 checks |
| Existing UI check | 100/101 on CI before `66ac861` (label only); fixed |
| Release build + `verify_release.py` with its own Python | 28/28. ZIP sha256 `ee71716f9e93aca14cf821fc91c9eca34c67766f336516e0f5a61f7d909f327b`, 2265 files, built from a clean worktree of `bd6ab4e` |
| CI on PR #11 | failed on `d3ef78e` (timeline order, UI-01 label); fixed in `66ac861`, re-run pending |
| After the R07 corrections | 721 passed, 8 skipped, 0 failed (unit, hardware, integration, acceptance, legacy); browser journey and release rebuild NOT RUN (see `docs/RESULTS-0.7.md`) |

**Release ZIP.** It was rebuilt from `bd6ab4e` and includes the `66ac861` fix. The earlier build from `c5ac7cb`
(`fdd274b4…`) is superseded. The documents inside the ZIP still name the earlier build, because they were
written before this rebuild. Commits after `bd6ab4e` change documents only.

## Not done or not verified

- **NOT RUN cases:**
  - EQ-04 (pump shared by two blocks, rename and retire blockers).
  - IMPORT-01 (project JSON/YAML import is not in 0.7).
  - PROG-05, PROG-07, PROG-08.
  - PROG-09, PROG-10 (drainage alternation and nutrient preparation through blocks are not in 0.7).
  - CON-03, HIST-03 (rebuilding a run's configuration after later edits), DATA-01, DATA-03, DEP-05.
  - TWIN-09 and REL-04 (need the CM4).
- **Partial cases:** EQ-05, PROG-02, PROG-03, TIME-04, TIME-05, TWIN-07, TWIN-08, HIST-02, RTE-09 (no real touch
  device), RTE-10 (desktop only), REL-03 (no cold start through the launcher).
- **CM4.** The owner reports Raspberry Pi OS Lite 64-bit (arm64). The release has aarch64 wheelhouses for
  Python 3.11 (Bookworm) and 3.13 (Trixie). Not done on the CM4: reading the installed version, upgrade, twin
  restart, rollback, load.
- **Physical verification.** None. Every result is software or simulation.

## Suggested review focus

1. **Safety boundaries.** Check that no path lets the twin, an import, a viewer or an operator write to hardware or
   module configuration. See AGENTS.md invariants, `security/auth.py`, `api/changes.py` and `twin/changes.py`.
2. **Proof binding (F01).** Is the context digest complete, so that nothing can change without staling the proof?
3. **Engine limits and faults (F02, F03).** Are they enforced at every ON, including on restart and in later
   sequence steps?
4. **Durable apply (FB-07).** Check the journal ordering, crash recovery and the single apply under concurrent
   approvals.
5. **Separation of topology and drawing.** Check that no drawing edit can alter edges, and that the migration
   never infers a connection.
6. **Program compiler (`programs/authoring.py`).** Do the generated plans match the preview? How are feedback,
   meters and maximums refused?
7. **Schema 6 upgrade and rollback story** (`docs/operations/upgrade-0.7.md`).
8. **Honesty of `docs/RESULTS-0.7.md`.** Check every PASS against a named test or check.
