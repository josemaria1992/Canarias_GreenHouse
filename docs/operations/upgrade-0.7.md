# Upgrading to 0.7.x, and going back

## 0.7.1 (from 0.6.12, 0.6.13 or 0.7.0)

0.7.1 adds database schema 7 (editable public IDs and their log, module requests) on top of schema 6. From 0.6.x both
migrations run at the first start, each after the service's own safety copy. Nothing in the upgrade switches an output,
arms a lease, starts a program or enables AUTO; after the restart the installation is in RECOVERY as after every start.

### Before

1. Note what runs now, on the controller: `readlink -f /opt/aquacrop/current`, `systemctl status aquacrop-greenhouse
   aquacrop-twin`, and `curl -sk https://aquacrop-bench.local/api/health` (0.7.1 and later answer with their build).
2. Keep a copy of everything that is not code, off the controller: a consistent copy of the main database (SQLite
   online backup, never a raw copy of a database in use), `/var/lib/aquacrop/layout` (the accepted Overview drawing),
   `/var/lib/aquacrop/twin-changes`, the twin's state folder, the feed folder and `/etc/aquacrop`. Record their
   SHA-256. `docs/RESULTS-0.7.1.md` records the copy made for the bench.
3. Tell the users what changes for them (`CHANGELOG.md`, 0.7.1): Equipment is its own page; operators with
   commissioning can prepare modules again; programs are written with windows; the drawing is made in the twin.

### Upgrade (CM4), main and twin from one release

1. Use the release ZIP built from the reviewed commit (its `RELEASE.json` names the commit; the wheel carries the same
   identity). Unpack it on the controller in a new folder.
2. `sudo python3 deploy/aquacrop-cm4.py upgrade --release <folder>`: it makes and verifies a backup of the running
   database (`/var/lib/aquacrop/backups/pre-upgrade`), installs, switches `current`, and starts the service, which
   migrates the database (`pre-migrate-v5-...` or `pre-migrate-v6-...` safety copy).
3. Restart the twin from the same release: `sudo systemctl restart aquacrop-twin`.
4. Check: `/api/health` of `https://aquacrop-bench.local/` and `/twin/` report the **same build**; the page header
   shows it; no red "another build" banner. Sign in: the Overview shows the accepted drawing (the earlier drawing until
   a drawing is exported from the twin and applied), Equipment, Blocks and Programs list what was there, Add Module
   shows the wizard to administrators and commissioning operators.

### After: the installation drawing

The upgrade draws nothing. The accepted 0.6 drawing stays the Overview, read-only, with a note. To make its successor:
Digital Twin → Installation design → **Import from Overview**: the earlier drawing comes back as a design proposal
with every ambiguous or unlinked part listed. Decide each one, place what is missing (equipment is created on
Equipment), connect, **Straighten pipes**, Save in the twin, run it, **Export to Overview**. On the main dashboard,
Digital Twin → Changes from the twin shows the difference; an administrator or a commissioning operator approves it.
Undo takes it back while nothing it touched has changed since.

### Going back

* To 0.7.0: `deploy/aquacrop-cm4.py rollback` returns the code, but **0.7.0 refuses a schema 7 database**: restore the
  pre-upgrade backup (`backup restore --maintenance`, `service.md`). Records made under 0.7.1 are then not in the
  restored database.
* To 0.6.x: the same, with the backup taken before the 0.6 → 0.7 upgrade (schema 5).
* A restore never restores a bench lease, resumes an operation or enables AUTO. The tested restore on a copy is in
  `docs/RESULTS-0.7.1.md`.

## 0.7.0 (from 0.6.13)

0.7.0 adds database schema 6 (equipment usage, the hydraulic graph and its drawings, blocks, twin run records). The
upgrade path is the existing one (`deploy/aquacrop-cm4.py upgrade`, docs/operations/service.md); this page says what
is different this time. Nothing in the upgrade switches an output, changes a lease, or starts a program.

### Before

1. Note the running versions: `sudo python3 deploy/aquacrop-cm4.py status` (live and twin).
2. Read docs/CAPABILITIES-0.7.md and ADR-0023: **viewer accounts lose** history, events, equipment, module and
   twin access; **operators with the commissioning flag lose** writing a new module's address and modes (an
   administrator does it). Tell those users before you upgrade.

### Upgrade (CM4)

1. Build the release from the reviewed commit and unpack it (the deploy recipe in docs/operations/service.md).
2. `sudo python3 deploy/aquacrop-cm4.py upgrade --release ~/aquacrop-release/aquacrop-0.7.0`. It makes and
   verifies a backup of the running database first (`/var/lib/aquacrop/backups/pre-upgrade`), then installs, then the
   service migrates the database to schema 6 on start with its own safety copy (`pre-migrate-v5-...`).
3. Restart the twin from the same release (`... twin`), so live and twin run the same version and schema.
4. Check: `status` reports 0.7.0 healthy; the Overview opens; existing equipment, bindings, programs and history
   are there. Old programs show as "earlier definition" and keep running exactly as before.

### After: the installation graph

The upgrade does not draw anything. The 0.6 drawing stays as it was, read-only. To get the graph: Blocks →
Installation drawing → "Start from the old drawing" loads the old drawing's explicit links as a draft and lists every
ambiguity (equipment drawn on a pipe, pipes touching). Decide each one, place what is missing, Save. Then create the
blocks. Until then the twin's water keeps following the 0.6 drawing model, labelled as such.

### Going back to 0.6.13

`sudo python3 deploy/aquacrop-cm4.py rollback` returns the code. **0.6.13 refuses a schema 6 database by design**, so
going back also needs the pre-upgrade backup restored with `backup restore --maintenance` (service.md). Everything
recorded after the upgrade (history, events, edits, approvals) is then **not** in the restored database. Before
restoring, export what you need from 0.7.0 (Operation History CSV, equipment export) or keep the 0.7.0 database file
aside; there is no tested path that moves post-upgrade records into a 0.6.13 database. A restored database never
restores a bench lease, resumes an operation or enables AUTO.

### Tested here, and not

| What | Result |
|---|---|
| Migration 5 → 6 on a copy with data, interruption before commit leaves schema 5 | PASS (tests/integration/test_retention.py, test_historian.py) |
| Old program bodies keep their digest after the upgrade | PASS (tests/unit/test_programs_070.py) |
| CM4 upgrade, twin restart and rollback on the real controller | NOT RUN |
