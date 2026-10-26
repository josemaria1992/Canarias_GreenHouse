# ADR-0027: editable public IDs over stable keys, and one build identity (0.7.1)

Status: accepted for 0.7.1 (owner decisions of 26 October 2026).

## Context

The owner wants equipment and program IDs that follow the name (`Valve 1` → `Valve_1`, `Irrigation Pump 2` →
`IrrigationPump_2`), keep the case typed, stay editable after creation and remain unique. Every reference in the
system (graph nodes, block members, channel bindings, program bodies and their digests, schedule occurrences, equipment
usage, alarms, journal steps, history events) names the item or program by its id, which until 0.7.0 was lower case
and fixed. Rewriting those references on a rename would change validated program digests, break twin proof and make
history look as if it had been run with names it never had.

Separately, several different builds were all called "0.7.0": the release ZIP, a source tree being edited under a
running server, and older processes. Browsers cached every script for a year keyed only by the version, so a page
could run scripts of a different build than its server.

## Decision

1. **Stable key, editable public ID** (`aquacrop/ids.py`, schema 7 `public_ids` and `public_id_log`). An item's or
   program's key never changes and every reference keeps using it. What people see and edit as "ID" is the public ID:
   the key itself for every existing object (no row, nothing renamed), or what the owner typed, in that case.
   * A new object's key is derived from the ID typed (lower case, made unique): references keep the existing rules.
   * A rename changes only the public ID, in one transaction that checks the revision read and that no other object
     of that kind has the ID ignoring case; the log keeps every earlier ID with who and when. Nothing stops or starts,
     no version changes, no digest or proof context changes (they name keys, never IDs or names), and a running
     operation keeps its held channels.
   * The API accepts the key or the public ID (any case) where an existing object is named; creating with an ID that
     is already used (ignoring case) is refused with the name of the object that has it.
   * The browser fills the ID from the name with the same rule as the server (`web/ids.js`, tested against
     `ids.id_from_name` on shared cases) until the user edits the ID.
2. **Build identity** (`aquacrop/build.py`): `<version>+<commit>[.dirty].w<web digest>`. Release wheels carry
   `_build.json` (commit, clean tree, build time) written by `scripts/build_release.py`; a source checkout reads git.
   It is the asset cache key, the `X-AquaCrop-Version` header, shown in both page headers, returned by `/api/health`
   (no user data) and `/api/version` (with profile and project, no filesystem paths). The main dashboard and the twin
   write their identity into the shared feed folder and each reports a mismatch with the other in a red banner. A
   server whose web files changed on disk after it started says so instead of serving them as its build.

## Consequences

* Existing lower-case IDs are unchanged unless the owner edits them. History is never rewritten: earlier events name
  the key, and the ID log says which public ID it had when.
* Uniqueness is per kind (equipment, programs), across retired objects too, so an old record is never confused with a
  new object of the same ID.
* A release must be installed for main and twin together; a mismatch is visible, never assumed away.
* Tests: `tests/unit/test_ids_071.py`, `tests/js/ids.test.js`, `tests/integration/test_ids_071.py`,
  `tests/hardware/test_twin_service.py::test_pages_name_their_scripts_by_build_and_say_which_build_answers`.
