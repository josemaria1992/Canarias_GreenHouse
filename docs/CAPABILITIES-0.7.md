# AquaCrop 0.7.x: what each capability is today (updated for 0.7.1, 27 October 2026)

Status words: **implemented** (works in the software, tested), **simulation only** (works in the digital twin;
not for real equipment), **bench only** (real IO 8CH test loads under an armed lease), **configured** (needs the
owner's real values first), **commissioned** (proven on the real installation), **open** (agreed, not built yet),
**unavailable** (not in this release). Nothing here is commissioned: no physical result was produced for 0.7.x.

## Pages (owner decisions of 26 October 2026)

| Page | What it is for | Status |
|---|---|---|
| Overview | The applied installation drawing with each item's state in words; alarms, modules not answering, write state | implemented (ADR-0024 amendment) |
| Equipment | The one place equipment is created and configured: Name → ID, type, icon, channels, scaling, limits | implemented |
| Blocks | Members by type, read-only drawing to find them, start/stop plans, safety settings (maximum runtime) | implemented |
| Digital Twin | Installation design (import from Overview, new design, edit, save, simulate, export to Overview), simulated modules | implemented / simulation only |
| Add Module | Real modules: the address wizard (administrators, commissioning operators) and tracked requests | implemented (wizard: needs the controller's USB adapter) |
| Programs | Water for a time or deliver litres; windows, wait after each cycle; conditions; Name → ID | implemented (ADR-0025 amendment) |

## Capabilities

| Capability | Status | Notes |
|---|---|---|
| Role navigation (viewer / operator / administrator) and server-side checks | implemented | ADR-0023 and its 0.7.1 amendment |
| Editable public IDs over stable keys (equipment, programs), Name → ID | implemented | ADR-0027; uniqueness ignoring case, revision-checked, logged |
| Equipment from forms: types, items, typed properties, scaling, alarm limits | implemented | Type definitions stay an administrator's JSON editor (advanced) |
| Passive equipment (tanks, water sources, sectors) without channels | implemented | Shown without a state on the Overview |
| Planned equipment (drawn, no channel yet) | implemented | PLANNED · UNASSIGNED on the Overview; cannot run |
| Channel proposal by operators; verification at the bench | implemented / bench only | Verification needs the commissioning flag |
| Explicit hydraulic graph, ports, junctions, sensor associations | implemented | ADR-0024 |
| Orthogonal routing, segment and symbol moves, junctions, crossing gaps | implemented | |
| Port alignment (level connection heights), Straighten pipes, editable port anchors | implemented | Logic tested headless (tests/js/editor_align.test.js) and by browser gestures on a private test instance |
| Migration of the 0.6 drawing into a design proposal | implemented | In the twin's Import from Overview; ambiguities listed, nothing saved by looking |
| Blocks (purpose, members, path, plan, safety) with validation | implemented | Plans from templates are examples to check; no runtime value is proposed |
| Programs: time or litres per cycle, windows, wait, conditions | implemented | Adjacent-window handoff waits for a verified stop (tests/unit/test_windows_071.py) |
| Stop plans hold on an unverified or lost OFF (RR-01) | implemented | Logic tests in simulation; no physical result |
| Equipment limits before every ON, fault inputs, start/stop conditions | implemented | Logic tests in simulation |
| Digital twin: graph water model with ledger and NOT_MODELLED | simulation only | ADR-0026; pressure, chemistry, suction not modelled |
| Twin proof bound to run and context; export, approval, undo, durable apply | implemented | RR-02..04 corrected (ADR-0026 amendment) |
| Simulated modules in the twin | simulation only | Their channels stay in the twin |
| Module requests (all operators); module preparation (administrators, commissioning operators) | implemented | Applying the configuration proposal stays a maintenance step |
| Build identity, main/twin mismatch warning | implemented | ADR-0027 |
| Item alarm limits (delay, deadband, quality) | implemented | Real readings of VERIFIED channels, and in the twin |
| Operation History: runs, timeline, CSV | implemented | Recorded events and commands only |
| Physical program runs on IO 8CH test loads | bench only | Unchanged 0.5 path: lease, allowlist, accepted twin evidence |
| Project import/export (JSON/YAML project exchange with preview, IMPORT-01) | **open** | Only equipment type import exists; whole-project exchange is not built |
| Drainage lead alternation and standby response (PROG-09) | **open** | Not built in the block/program model |
| Simulated nutrient preparation with labelled chemistry (PROG-10) | **open** (template only) | The NUTRIENT_PREPARATION block template exists; no labelled mixing model; real dosing stays blocked |
| Historical context reconstruction of a run (HIST-03) | **open** | Bindings and versions are kept; a run view of the executed configuration is not built |
| Equipment/run charts with quality gaps and digital lanes (DATA-01) | partly | Trends exist; run-annotated equipment charts are not built |
| Real irrigation, relays, drive, dosing in production | unavailable | Needs commissioning evidence (OPEN_QUESTIONS) |
| Predictive shadow | unavailable | `SHADOW—NOT CONFIGURED` |
