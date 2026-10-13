# ADR-0014 One controller per process, an arbiter, recipes with versions
Status: proposed (pre-hardware release, stage 2). Simulation only; there is no physical AUTO.

## Context

The installation runs several processes at once: irrigation alternates sectors on a shared drive
while the main tank is refilled and drainage is returned (owner: "irrigation and drainage remain
enabled during replenishment"). Stock tanks are prepared by hand. pH is lowered with acid only; there
is no base dosing. One AUTO controller for everything (the demo design) cannot express this.

## Decision

1. **Seven process controllers** (`control/processes.py`): IRRIGATION, RETURN, REFILL, ACID and one
   STOCK_FILL per stock tank. Each has its own STOPPED / MANUAL / AUTO / FAULT_LOCKOUT state, phase,
   permissives, inhibit reason, next step, transition history and fault latch.
2. **A supervisor and an arbiter** (`control/supervisor.py`). A process can command only equipment it
   owns; ownership is taken on the first ON command and released when its stop sequence completes.
   A mode change of one process never starts or stops another. An open safety chain locks out every
   process in AUTO or MANUAL; restoring the chain restarts nothing. A storage inhibit refuses new
   starts. Processes tick in a fixed order every control period.
3. **Ack, reset and restart are separate actions.** An alarm is acknowledged; a lockout is reset only
   when its condition has cleared, its alarm is acknowledged and every owned output is verified off;
   the process then stays STOPPED until someone requests AUTO or MANUAL. A process restart (service
   restart) always begins STOPPED.
4. **AUTO never adopts MANUAL equipment.** AUTO is entered only from STOPPED with no owned outputs.
   MANUAL offers bounded operations only (one irrigation step with the operator's pump choice and a
   duration limit, one measured transfer, one acid dose of at most the recipe step, one return run,
   one stock fill), under the same interlocks as AUTO. Raw DO commands are refused on the twin.
5. **Bounded transfers measure what they deliver.** A path (pump, valve) ends at its measured target
   or a guard; if the meter or level becomes unusable the delivered volume is unverified and the path
   fails; it never counts as delivered.
6. **Irrigation changeover** (owner sequence): select the pump (K1/K2) and wait for its auxiliary
   contact, open the sector valve, wait at least 1 s, start the drive, verify running and flow,
   irrigate for verified-flow time, stop the drive, verify stopped, settle, close and verify, deselect
   and verify, then the next sector. If the drive does not confirm stop, the valve and the selection
   are held (no contactor switching under a possibly running motor) and the process locks out.
7. **Refill** latches one batch when the main level stays at the trigger for the debounce time and
   re-arms only above trigger + hysteresis. Water and nutrients run concurrently, each to its own
   measured endpoint. Nutrient modes: `FIXED_VOLUMES` (then an EC validation after the settling
   window, alarm if out of band, no automatic correction) or `EC_TARGET` (staged A:B steps keeping
   the ratio, both-or-neither starts, waits for settling, stops at the batch maxima, never removes
   nutrients). A failed, empty or unprepared stock stops only the affected paths (PC-09); a failed
   path locks the refill out at the end of the batch.
8. **Acid** doses only above target + tolerance, in steps of at most the recipe step, waits for the
   settling window after any acid, nutrient or water addition, checks the response (no response or a
   rise counts), stops at batch and 24 h limits, and locks out on undershoot (no base dosing exists).
9. **Mixing readiness** is a SIMULATED settling window. On any physical profile it is `NOT_VERIFIED`
   (no agitator or verified mixing method), so dosing cannot run there even if live control arrived.
10. **Stock readiness** is an operator acknowledgement with the level it covers. A stock fill, a level
    rise not covered by the acknowledgement or an operator invalidation revokes it. It is persisted
    (schema v3) and survives restart. Water filling never asserts chemical readiness.
11. **Recipes** (`control/recipes.py`) hold every operating setting with units in the field names.
    DRAFT is editable; validation checks ranges, required fields per mode and ordered thresholds;
    activation re-validates and is logged with actor, time and digest; ACTIVE and superseded versions
    are immutable (editing creates the next version); rollback re-activates the previous version.
    Processes snapshot the active version at the start of each cycle or batch, so a change applies to
    the next cycle and is recorded with every batch, transition and command. The only preset is a
    labelled DEMO preset, seeded only in SIMULATED profiles and deliberately different from the
    owner's illustrative example. Roles: operators edit and validate drafts; administrators activate
    and roll back (a default, changeable).

## Consequences

* The PC acceptance gates are executable as scenarios (`python -m aquacrop demo verify --config
  installation-48`) and as tests; none of them is physical verification.
* Schema v3 adds `recipes`, `recipe_activations` and `stock_readiness`; the migration takes a
  verified backup first.
* Production values (thresholds, volumes, EC/pH targets, dosing limits) remain TBD; the DEMO preset
  cannot be activated on a real profile.
