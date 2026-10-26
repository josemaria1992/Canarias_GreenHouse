# ADR-0023: role matrix and navigation (0.7.0)

Status: proposed for 0.7.0 (owner upgrade brief of 25 October 2026, UI_UX_SPECIFICATION). Narrows owner decision
11 of AGENTS.md ("the digital twin is usable from any login") and the 0.5 meaning of the commissioning flag.

## Context

Until 0.6.13 a viewer could read every page and API (history, events, equipment, the I/O board, the twin), and an
operator with the commissioning flag could write a new module's address and modes through the dashboard wizard.
The owner's brief asks for role-specific navigation and for dangerous low-level physical configuration to be
administrator-only, enforced by the backend, while routine module registration stays available to operators.

## Decision

1. **Navigation** (`security/auth.py: NAVIGATION`, returned by `/api/auth/session`):
   * viewer: Overview, Programs, Alarms;
   * operator: Overview, Blocks, Digital Twin, Add Module, Programs, Operation History, Alarms & Events;
   * administrator: the operator's pages plus Equipment (and Equipment types), Hardware (Operation, I/O test bench,
     I/O board, Discovery, Control, the installation's process pages) and Settings (with Getting started).
   Other pages stay reachable by their old links where the role allows them; otherwise the page says which role it
   needs. Hidden navigation is never the protection: every route checks its permission on the server.
2. **Read permissions** (new): `READ` is the viewer's scoped set (status, the read-only plant/Overview, processes
   and controller summaries, alarms, programs, operation view, layout); `OPS_READ` (operator) covers history,
   events, trends, CSV export, analytics, equipment, blocks and topology, the twin and its hand-off ticket,
   commissioning and discovery views, the bench state and twin-change review; `ADMIN_READ` covers settings,
   users and roles, retention, quarantine, backups, off-site copies and the API schema.
3. **Twin**: a viewer cannot sign in to the twin service (password or hand-off ticket); every twin read needs
   `OPS_READ`. Live accounts still synchronise one way to the twin, and a changed role ends twin sessions.
4. **Modules**: `MODULE_SETUP` (reserve and write an address or modes, the wizard's transaction steps) is
   administrator-only, including through the 0.5 wizard routes, and is no longer a commissioning permission.
   `MODULE_REGISTER` (operator, no flag needed) covers bounded read-only discovery sweeps and registration
   proposals. The commissioning flag still gives an operator `IO_ASSIGN` (verify wiring) and `BENCH_ARM`.
5. Activation of a real program or configuration revision stays an administrator's review behind the existing
   readiness gates; operators request it and run versions that are already approved.

## Consequences

* Existing viewer accounts lose history, events, equipment and twin access at upgrade; this is the requested
  policy, recorded in the release notes. Their sessions keep working for the remaining pages.
* An operator with commissioning who used the dashboard wizard needs an administrator for a new module's address.
* Tests: `tests/integration/test_rbac_070.py` sweeps every GET route as a viewer and checks the matrix.
