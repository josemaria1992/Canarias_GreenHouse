# ADR-0025: no-code watering programs and the engine's conditions (0.7.0)

Status: proposed for 0.7.0 (owner brief of 25 October 2026; PROGRAM_AND_RULE_ENGINE). Extends ADR-0018 §3.

## Context

Operators wrote programs as JSON. The brief asks for forms (where, how much, when, conditions, preview, save) with
no code, compiled into the existing engine rather than a second "UI program", and for limits, faults and
conditions the engine did not yet evaluate.

## Decision

1. **Form** (`programs/authoring.py`, `WateringForm` v1): a validated block version; an endpoint (TIME after the
   block's startup steps, measured VOLUME, or MANUAL within a bound) with a maximum for the whole operation; a
   schedule (run now, or weekdays × start times, an optional local date range, an optional repeat inside a window
   with rest after each verified completion, a count and a hard window end); start and stop conditions as rows
   (reading, comparison, value with the reading's own unit, persistence).
2. **Compilation**: the server compiles the form into a `SEQUENCE` (the block's startup, then `RUN` or `DELIVER`,
   then the block's normal stop) and the matching trigger. The body stores the form (`authoring`) and the timing basis,
   so it reopens in the same form; the digest covers both. A volume needs the block's inline meter in L/min; a
   startup step that waits for feedback needs a feedback channel; a command output is never accepted as evidence.
   Programs written before 0.7.0 are unchanged, shown read-only with their definition, never converted silently.
3. **Engine** (`programs/engine.py`): equipment limits (minimum OFF, starts per hour) checked before every ON and kept
   across restarts (schema 6 `equipment_usage`); FAULT inputs inhibit starts and re-arm and stop a running
   operation with a persistent `EQUIPMENT_FAULT` alarm; `permissives` must hold for their persistence at start
   (UNKNOWN prevents it); `guards` stop through the stop path when one holds for its persistence (UNKNOWN stops it
   too), result `GUARD_STOPPED`; trigger `WINDOW_REPEAT` starts the next cycle after the previous ended verified
   plus the rest, at most the count, never after the window end, and stops a running cycle at the end as
   `WINDOW_ENDED`; a cycle that does not complete ends the window's repeats. Entering AUTO skips open repeat
   windows like weekly ones. Weekly entries take an optional local date range (inclusive, by start date).
4. New body fields are left out of the serialized body while they hold their defaults, so every earlier digest and
   twin proof stays valid.

## Consequences

* `DONE` still means the endpoint was reached and stopped verified; a guard, a window end or a runtime guard is
  never DONE. A twin pass is a logic test of exactly this version on the tested context (ADR-0026).
* Tests: `tests/unit/test_programs_070.py`, `tests/unit/test_feedback_070.py`, `tests/integration/test_program_forms_070.py`.
