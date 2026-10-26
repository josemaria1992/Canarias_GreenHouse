# Architecture decision records

| ADR | Title | Status |
|---|---|---|
| 0001 | Module quantity and generic inventory | accepted |
| 0002 | HYBRID_SHADOW not served in this release | accepted, revisit |
| 0003 | Bench writes are a lease on HARDWARE_READ_ONLY | accepted |
| 0004 | One database per profile | accepted |
| 0005 | FC0F not implemented | accepted |
| 0006 | All eight relay modes must be NORMAL | accepted |
| 0007 | Repository location (Canarias_GreenHouse on GitHub) | accepted |
| 0008 | Historian: 60 s snapshots, rolling 365 days, storage budget | accepted |
| 0009 | No controller or twin on a real profile | accepted |
| 0010 | Polling instead of streams | accepted |
| 0011 | Timestamp quarantine after a clock correction | accepted |
| 0012 | SD-card daily backups and encrypted OneDrive copies | accepted, not activated |
| 0013 | Full-installation twin, 48-point signal budget and the drive adapter | proposed |
| 0014 | One controller per process, an arbiter, recipes with versions | proposed |
| 0015 | The shared drive's run command over its Modbus interface | proposed |
| [0016](0016-added-digital-io-modules.md) | Two additional SKU 26244 modules, 80-point inventory | owner-confirmed inventory; physical allocation pending |
| [0017](0017-bench-bring-up-safe-state-and-io-board.md) | Bench bring-up: module setup, safe-state OFF, I/O board, assignment and check records | owner-accepted; not yet run on the hardware |
| [0018](0018-equipment-programs-and-parallel-twin.md) | User-defined equipment, programs, the module wizard and a twin service beside the live one | proposed (0.5.0) |
| [0023](0023-role-matrix-and-navigation.md) | Role matrix and role navigation; the server enforces every right | proposed (0.7.0) |
| [0024](0024-explicit-topology-blocks-and-orthogonal-drawings.md) | Explicit hydraulic graph, blocks, and orthogonal drawings that never connect anything | proposed (0.7.0) |
| [0025](0025-no-code-programs-and-engine-conditions.md) | No-code program forms compiled to sequences; start and stop conditions in the engine | proposed (0.7.0) |
| [0026](0026-graph-water-model-proof-context-and-durable-apply.md) | Graph water model, twin proof bound to its context, durable apply of approved exports | proposed (0.7.0) |
