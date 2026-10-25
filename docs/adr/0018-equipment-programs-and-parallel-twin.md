# ADR-0018: user-defined equipment, programs, the module wizard and a twin beside the live service

Status: proposed for 0.5.0 (owner brief of 24 October 2026; owner answers in the build thread the same day).
Partly supersedes ADR-0002, ADR-0004 (narrowed), ADR-0009 (narrowed) and ADR-0015 §8 (time zone of the bench).

## Context

The owner wants to define equipment types and individual equipment from the dashboard, prepare new modules
from the dashboard with a separate USB-to-RS485 adapter, build schedules, measured-volume operations, sensor
rules and sequences, exercise them in a digital twin that is always available beside the real dashboard, and
then test the same logic on approved test loads on the IO 8CH outputs. The installation process controllers
of ADR-0014 are specific to the 80-point hydraulic model and stay unchanged.

## Decision

1. **Equipment** (`equipment/`). Types are versioned data (roles with direction, signal, purpose and allowed
   electrical interfaces; typed properties; limits; permitted operations; a simulation behaviour); items are
   made from one type version; channel bindings are append-only records PROPOSED → VERIFIED (the I/O board's
   server-side check plus the user's observation, or a cited VERIFIED I/O assignment) → CLEARED. One channel
   carries one role of one item. Icons are a fixed built-in set and decide nothing. A binding is not an output
   permission. Templates arrive as drafts.
2. **Module wizard** (`commissioning/`). Runs on the controller's USB adapter only, as a persisted transaction
   with an atomic address reservation (partial unique index). It writes what `hw setup` writes (ADR-0017 §1),
   unicast, readback-verified, never broadcast, never the UART register, never an installation bus; a write
   without a clear outcome is UNCERTAIN and keeps its address until re-checked. Destination detection and
   discovery read through each bus's own worker, one address per poll, only when the poll has time to spare.
   The wizard writes a configuration proposal; it does not change the running configuration.
3. **Programs** (`programs/`). A program is a trigger (MANUAL, WEEKLY, CONDITION, CONTINUOUS) and an action
   (RUN_FOR, DELIVER_VOLUME, KEEP_BETWEEN, SEQUENCE) validated as data; no user code exists. Versions are
   immutable once validated. The engine acts only through the runtime's audited CommandService, holds items
   through the same `Arbiter` class as the process controllers, never retries an unconfirmed ON, and always
   sends OFF on a stop path, repeating OFF while a stop is unverified.
4. **Mode, conflicts, restart.** Each runtime has its own installation mode: RECOVERY after every start,
   MANUAL, AUTO, and STOPPING until every automatic stop is verified. Restart abandons every operation and
   needs an operator's or administrator's approval against a checklist; entering AUTO skips windows already
   open. A request for an item in use opens a conflict that holds the request until a choice or the end of
   its window. Who: operators and administrators approve restarts, change the mode and resolve conflicts
   (owner answer 4); viewers watch.
5. **Schedules** use the site time zone (Europe/Stockholm for the bench; this supersedes ADR-0015 §8 for the
   bench). Owner answer 3, "the program continues": a start in the spring gap runs at the first valid minute
   with its full length; a start in the repeated autumn hour runs once; lengths are written lengths; a clock
   correction inside a window still runs it; occurrence keys make a repeat impossible.
6. **Measured volume** integrates fresh flow-rate readings of an analog meter through the item's scaling.
   A gap stops the operation and the volume it did not see is reported as uncertain. The IO 8CH and Relay (D)
   have no pulse counter, so a pulse flow meter cannot measure a physical volume; the twin still exercises it.
7. **The twin is a second service** (`aquacrop twin-serve`, `aquacrop-twin.service`; owner answer 1). Its
   configuration is the live configuration made SIMULATED, so every bus is virtual (a SIMULATED configuration
   cannot name a port); it runs as its own user with no device nodes, its own database and its own login
   cookie, capped in CPU, memory and I/O. The live service publishes a snapshot (configuration, equipment,
   programs, accounts) one way; the twin writes evidence files the other way, accepted for a version only when
   the digest matches. Every login works in the twin with the same password (owner answer 2). Its runtime id
   is required on every state-changing twin request.
8. **ADR-0004 narrowed**: still one database per runtime; the twin's database is its own and refuses a real
   profile's. **ADR-0009 narrowed**: a HARDWARE_READ_ONLY server now serves the equipment, wizard, discovery and
   program pages; program activation and every engine action remain unavailable on it. **ADR-0002**: the twin
   beside the live service is a hypothetical sandbox started from a copy, not a predictive shadow.
9. **Physical digital outputs** (later in 0.5.0) need a separate, explicit bench-test profile with an IO 8CH
   allowlist, verified bindings, Normal modes, an armed and renewable 30-minute test session (owner answer 5),
   and live activation of a version that passed in the twin. HARDWARE_READ_ONLY keeps its exact write guarantees.

## Consequences

* Schema 5 adds equipment, binding, program, occurrence, conflict, mode, recovery and commissioning tables,
  migrated after the usual verified backup.
* A twin pass is a logic test on virtual modules with DEMO dynamics. It is never physical verification.
* Relay outputs, the drive, pumps, valves and dosing stay out of physical scope.

## Addendum (2026-10-24, 0.5.4): administrators have every right

Owner decision: an administrator has every permission, commissioning included. The commissioning flag is an
operator's additional right to work on the real modules. This supersedes ADR-0017's rule that an administrator
without the flag cannot arm a bench lease. Every other gate (the lease's confirmation, allowlist, preflight and
expiry; Normal modes; the bench-test allowlist) is unchanged.
