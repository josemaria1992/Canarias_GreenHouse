# Upgrading to 0.7.0, and going back

0.7.0 adds database schema 6 (equipment usage, the hydraulic graph and its drawings, blocks, twin run records). The
upgrade path is the existing one (`deploy/aquacrop-cm4.py upgrade`, docs/operations/service.md); this page says what
is different this time. Nothing in the upgrade switches an output, changes a lease, or starts a program.

## Before

1. Note the running versions: `sudo python3 deploy/aquacrop-cm4.py status` (live and twin).
2. Read docs/CAPABILITIES-0.7.md and ADR-0023: **viewer accounts lose** history, events, equipment, module and
   twin access; **operators with the commissioning flag lose** writing a new module's address and modes (an
   administrator does it). Tell those users before you upgrade.

## Upgrade (CM4)

1. Build the release from the reviewed commit and unpack it (the deploy recipe in docs/operations/service.md).
2. `sudo python3 deploy/aquacrop-cm4.py upgrade --release ~/aquacrop-release/aquacrop-0.7.0`. It makes and
   verifies a backup of the running database first (`/var/lib/aquacrop/backups/pre-upgrade`), then installs, then the
   service migrates the database to schema 6 on start with its own safety copy (`pre-migrate-v5-...`).
3. Restart the twin from the same release (`... twin`), so live and twin run the same version and schema.
4. Check: `status` reports 0.7.0 healthy; the Overview opens; existing equipment, bindings, programs and history
   are there. Old programs show as "earlier definition" and keep running exactly as before.

## After: the installation graph

The upgrade does not draw anything. The 0.6 drawing stays as it was, read-only. To get the graph: Blocks →
Installation drawing → "Start from the old drawing" loads the old drawing's explicit links as a draft and lists every
ambiguity (equipment drawn on a pipe, pipes touching). Decide each one, place what is missing, Save. Then create the
blocks. Until then the twin's water keeps following the 0.6 drawing model, labelled as such.

## Going back to 0.6.13

`sudo python3 deploy/aquacrop-cm4.py rollback` returns the code. **0.6.13 refuses a schema 6 database by design**, so
going back also needs the pre-upgrade backup restored with `backup restore --maintenance` (service.md). Everything
recorded after the upgrade (history, events, edits, approvals) is then **not** in the restored database. Before
restoring, export what you need from 0.7.0 (Operation History CSV, equipment export) or keep the 0.7.0 database file
aside; there is no tested path that moves post-upgrade records into a 0.6.13 database. A restored database never
restores a bench lease, resumes an operation or enables AUTO.

## Tested here, and not

| What | Result |
|---|---|
| Migration 5 → 6 on a copy with data, interruption before commit leaves schema 5 | PASS (tests/integration/test_retention.py, test_historian.py) |
| Old program bodies keep their digest after the upgrade | PASS (tests/unit/test_programs_070.py) |
| CM4 upgrade, twin restart and rollback on the real controller | NOT RUN |
