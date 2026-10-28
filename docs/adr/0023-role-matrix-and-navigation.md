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


## Amendment 0.7.1 (owner decisions of 26 October 2026)

These owner answers replace points 1 and 4 above where they conflict.

* **Navigation.** Operators and administrators have a top-level **Equipment** page: equipment is created and
  configured there (`EQUIPMENT_EDIT`, as before), not under Blocks and not in the twin. Equipment types stay an
  administrator page (`equipment-types`), because activating a type is `EQUIPMENT_ACTIVATE`. The operator's menu is
  Overview, Equipment, Blocks, Digital Twin, Add Module, Programs, Operation History, Alarms & Events.
* **Modules.** `MODULE_SETUP` (the address wizard) is for administrators **and operators with the commissioning flag**:
  it is a commissioning permission again (`COMMISSIONING_PERMS`), checked on the server for every wizard step and for
  closing or rejecting a request. Operators without the flag send a **module request**. Changing an account's role or
  flag ends its sessions, so its next request is refused.
* **Module requests** are a workflow stored on the bench (`module_requests`, schema 7): requester, model, purpose,
  status (REQUESTED, PREPARING, AWAITING_APPLICATION, ADDED, REJECTED, CLOSED), handler and reason. The requester sees
  their own requests; handlers see all. ADDED is shown only when the running configuration contains the module; a
  rejection or a closure never adds anything. Nothing is emailed or sent outside the bench. Applying the configuration
  proposal stays the existing maintenance step by an administrator.
* Unchanged: the viewer scope; `IO_ASSIGN` and `BENCH_ARM` need the flag; activating programs and types is an
  administrator's. Tests: `tests/integration/test_rbac_070.py`, `tests/integration/test_modules_071.py`,
  `tests/hardware/test_commissioning_api.py`.
