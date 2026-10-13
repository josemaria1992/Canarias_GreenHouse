# ADR-0015 The shared drive's run command over its Modbus interface
Status: proposed (owner direction 2026-10-13; the real interface stays unconfigured until CP-06).

## Context

The 48-point budget (ADR-0013) is one output short: the shared drive's run command is the 17th
actuator. The owner chose, on 2026-10-13, to keep the two relay and two AI modules, use all 16 DI and
16 DO, and investigate the drive's own Modbus interface for the run command, keeping it unconfigured
until verified and finishing the simulation and bench software meanwhile. No drive model, manual or
register map has been supplied.

## Decision

1. **A `MODBUS_RTU` drive adapter** with a register map (`VfdModbusMap`) entered from the drive's
   manual: unit address, control register, run sequence (1 to 3 values), stop value, status register
   and function, running/fault/ready masks, optional diagnostics, the drive's comm-loss timeout and
   action, provenance and evidence. Nothing in the code knows a vendor's addresses.
2. **Provenance gates.** `DEMO` is the simulated drive's map and a real profile refuses it.
   `PROPOSED` is accepted on a real profile for the bench (reads always; writes only under a bench
   lease). `VERIFIED` requires the CP-06 evidence. The installation configuration stays
   `NOT_CONFIGURED` until then.
3. **One register write, nothing else.** FC06 to the control register only, one frame per value,
   never retried, never periodic. `RtuBus` holds the set of (unit, register) pairs it may write,
   built from the configuration, and refuses anything else before sending. No speed reference,
   parameter, fault reset or broadcast is written. Diagnostics (`diagnostics read`) cannot write.
4. **New outcome `ECHO_CONFIRMED`.** A complete run or stop sequence whose every frame came back as a
   matching echo. An exception on the first frame is `FAILED`; a missing or wrong echo, or an
   exception after the first frame, is `OUTCOME_UNKNOWN` (the drive may have applied it). On the
   bench a status read follows and is reported, but neither the echo nor the status word is motor
   proof.
5. **The hardwired inputs stay the proof.** The processes keep using `drive.MAIN.running` and
   `drive.MAIN.fault` (DI). The status word is read every poll as a second opinion; in the twin a
   10 s disagreement raises `DRIVE_STATUS_MISMATCH`, unreadable status raises `DRIVE_COMM` after
   three failures. On hardware the status word's running and fault changes are events
   (`DRIVE_STATUS_EDGE`, `OBSERVATION_GAP`).
6. **The drive stops itself on comm loss.** The drive's own comm-loss timeout and action are part of
   the map, must be at least twice the bus poll interval, and are tested in CP-06. The simulated drive
   does the same (`drive_modbus_comm_loss`).
7. **Bench path.** A bench lease may allowlist `drive:<adapter_id>`. Arming runs a fresh status read
   and refuses a faulted or silent drive; each run command re-reads the status and refuses while the
   drive reports a fault or not-ready (a stop is always allowed).
8. **Site time zones** (same owner message): the installation is `Atlantic/Canary`, the Malmö bench
   `Europe/Stockholm`. A real profile must name its zone; the twin and demo configurations use the
   installation's.

## Consequences

* All 16 relay DO stay allocated as proposed in `docs/model/signal_budget.md`; no third relay module.
  The IO 8CH modules added later the same day (ADR-0016) do not change this decision. The owner
  decided (2026-10-13, 17:19 UTC) that all 16 of their transistor outputs stay spare/test-only,
  `digital_A.DO1` included: a hardwired run is a possible future option, not an approved assignment
  or a second command path.
* The installation cannot run its main pumps until CP-06 passes on the bench and the verified map is
  configured; that was already true (no physical AUTO in this release).
* If the real drive's comm-loss timer is reset only by writes, this design would stop a running drive
  after the timeout; periodic re-writes would need an addendum here (`docs/model/vfd_modbus.md` §6).
* The Modbus link is not a safety function. Emergency stop and STO stay hardwired; K1/K2 are never
  switched under load.

Details, wiring and drive settings: `docs/model/vfd_modbus.md`. Bench procedure: CP-06.
