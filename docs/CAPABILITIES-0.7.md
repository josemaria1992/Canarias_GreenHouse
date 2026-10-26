# AquaCrop 0.7.0: what each capability is today

Status words: **implemented** (works in the software, tested), **simulation only** (works in the digital twin;
not for real equipment), **bench only** (real IO 8CH test loads under an armed lease), **configured** (needs the
owner's real values first), **commissioned** (proven on the real installation), **unavailable** (not in this release).
Nothing here is commissioned: no physical result was produced for 0.7.0.

| Capability | Status | Notes |
|---|---|---|
| Role navigation (viewer / operator / administrator) and server-side checks | implemented | ADR-0023; viewers cannot open the twin |
| Equipment from forms: types, items, typed properties, scaling, alarm limits | implemented | Type definitions stay an administrator's JSON editor (advanced) |
| Passive equipment (tanks, water sources, sectors) without channels | implemented | Their numbers carry a provenance; unknown stays unset |
| Channel proposal by operators; verification at the bench | implemented / bench only | Verification needs the commissioning flag |
| Explicit hydraulic graph, ports, junctions, sensor associations | implemented | ADR-0024 |
| Orthogonal routing, segment and symbol moves, junctions, crossing gaps | implemented | Browser-tested in headless Chromium with real mouse drags, clicks and arrow keys (walkthrough-0.7/checks.json) |
| Migration from the 0.6 drawing | implemented | Explicit links only; ambiguities listed for review |
| Blocks (purpose, members, path, plan) with validation | implemented | Plans from templates are examples to check |
| No-code programs: time, measured volume, manual; weekly, date range, repeat in window; conditions | implemented | ADR-0025 |
| Equipment limits before every ON, fault inputs, start/stop conditions | implemented | Logic tests in simulation; no physical result |
| Digital twin: graph water model with ledger and NOT_MODELLED | simulation only | ADR-0026; pressure, chemistry, suction not modelled |
| Twin proof bound to run and context; export, approval, undo, durable apply | implemented | Twin edits of the graph, drawing and blocks travel with the export |
| Simulated modules in the twin | simulation only | Cannot be exported |
| Module registration requests (operators); module preparation (administrators) | implemented | The USB wizard is unchanged; requests change no configuration |
| Item alarm limits (delay, deadband, quality) | implemented | Real readings of VERIFIED channels, and in the twin |
| Operation History: runs, timeline, CSV | implemented | Recorded events and commands only |
| Physical program runs on IO 8CH test loads | bench only | Unchanged 0.5 path: lease, allowlist, accepted twin evidence |
| Real irrigation, relays, drive, dosing in production | unavailable | Needs commissioning evidence (OPEN_QUESTIONS) |
| Predictive shadow | unavailable | `SHADOW—NOT CONFIGURED` |
