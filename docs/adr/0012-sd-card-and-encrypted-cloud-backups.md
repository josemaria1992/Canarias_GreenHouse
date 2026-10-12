# ADR-0012 SD-card daily backups and encrypted OneDrive copies
Status: proposed 2026-10-12 (owner request of 2026-10-12 16:01 UTC); implemented and shipped
**disabled**. Owner choices recorded 2026-10-12 19:19 UTC: a 64 GB ext4 card, the folder
`AQUACROP_DATOS_CANARIAS` in a colleague's personal OneDrive shared with the owner's account, a sealed
printed copy of the crypt keys, and the proposed copy policy. Activation still needs the card's
enrolment and the rclone remote on the controller (`docs/operations/offsite-backups.md` sections 1-5).
Resolves the "off-device backup destination TBD" item of ADR-0008.

## Context

ADR-0008 keeps seven compressed backups on the eMMC next to the live database: they do not survive
loss of the controller, and the 32 GB eMMC has no room for a full restore drill. The owner asked for:

1. the working SQLite database on the eMMC, 60 s recording, 365-day history (unchanged);
2. an SD card in a USB reader, whose mounted identity is verified before writing; never a fallback to
   the eMMC when the card is missing;
3. daily consistent compressed backups with checksums and manifests, earlier good backups preserved
   if creation or verification fails;
4. uploads with rclone and client-side encryption, copy operations (no mirror synchronization), the
   cloud content verified;
5. failed uploads queued and retried when connectivity returns; recording continues without internet;
6. monthly restore drills from a downloaded, decrypted cloud backup into isolated SD-card scratch
   space, preflighting at least 10 GiB plus headroom, never overwriting production data or activating
   restored hardware permissions;
7. proposed retention of 7 local daily copies and 30 daily plus 12 monthly cloud copies, shown for
   confirmation before pruning is enabled;
8. documented account authorization, protected credentials and recovery keys stored away from the
   controller; clear backup, upload and restore status;
9. the failure tests and recovery requirements of the detailed brief, and confirmation of the actual
   account, folder and card before activation.

The "detailed brief" with its own backup failure tests was not supplied; the original implementation
brief (§8, §11: consistent snapshots, manifests and checksums, restore to a separate instance,
disk-full behaviour, crash recovery, backup scheduling without cloud dependency) and the tests below
are used instead.

## Decision

### Separate processes, state in files

Backup, upload, pruning and drills run as short CLI processes started by systemd timers
(`aquacrop offsite backup|upload|drill`), one at a time (a lock file). They never run inside the
server's recording path, so a slow or missing card, rclone, or no network cannot delay a snapshot.
State lives in `<state>/offsite/` (queue entries, the ledger of verified cloud copies, a status file,
an append-only journal, the confirmed policy, drill reports). The server reads these files only: it
shows them in Settings (`GET /api/offsite`), copies each journal entry once into the events table
(`OFFSITE_*`, de-duplicated by entry id across restarts) and raises `OFFSITE_BACKUP` alarms.

### Card identity (fail closed)

Before any write the configured mount path must (1) be a mount point, (2) be on a different
filesystem from the live database and state directory, (3) be the block device whose filesystem
UUID the owner confirmed (`/dev/disk/by-uuid`, compared by device number), (4) be mounted
read-write (`/proc/self/mountinfo`) and (5) carry the enrolment marker with the confirmed volume id
(`offsite enroll-card` writes it once). Any failure stops the operation with `SD_CARD_MISSING`,
`SD_CARD_WRONG` or `SD_CARD_READ_ONLY`, records it and raises the alarm. Nothing falls back to another
location. When the card is absent its mount point is an empty eMMC directory; check (1) prevents
filling the eMMC through it.

### Daily backup

The ADR-0008 backup (one read transaction, gzip SQL dump, manifest with SHA-256, row counts,
fingerprints and schema digest, verified before an atomic publish) is written straight to the card,
after a free-space preflight on the card. It is then read back from the card and verified again; a
read-back failure unlists it (`*.bad` kept for inspection). Every failure leaves earlier backups
untouched. A verified backup is queued for upload.

### Encrypted copy with rclone

- The remote must be an rclone `crypt` remote wrapping the OneDrive folder: contents and file names
  are encrypted on the controller. This is checked (`listremotes --long`) before every upload batch;
  a non-crypt remote is refused (`NOT_ENCRYPTED`).
- Upload is `rclone copyto` of the backup and of its manifest to `<remote>/backups/<name>`. The
  wrapper only allows `version`, `listremotes`, `copyto`, `cryptcheck`, `lsjson` and `deletefile`:
  no `sync`, `bisync`, `move` or `purge`, so nothing local (an empty card, a pruned copy) can delete
  cloud data by mirroring.
- Verification: `rclone cryptcheck --one-way` of both files against the local ones, through the
  provider's checksums. Only then does the copy enter the ledger and leave the queue.
- Failures stay queued with exponential backoff (5 min doubling to 1 h); the upload timer runs every
  15 min. "Not now" failures (offline, authorization, card missing) stop the batch; the first success
  after a failure drains the queue in the same run. Queue entries are files, so they survive restarts
  and power loss; an entry is removed only after its cryptcheck passed.
- An alarm is raised when uploads have waited longer than 36 h, or at once for failures that need a
  person (authorization expired, remote not encrypted, local file gone or changed, cloud full).

### Copy policy (pruning) needs an administrator

Proposed and configurable: 7 daily copies on the card; 30 daily plus 12 monthly copies in the
cloud (`offsite_backup.policy`). "Daily" keeps the newest copy of each of the newest N UTC days;
"monthly" keeps the first copy of each of the newest M months; the newest copy is always kept.
Settings shows the policy, its hash and the latest plan of what it would delete; nothing is deleted
anywhere until an administrator confirms that exact policy (`OFFSITE_POLICY_CONFIRMED`). Changing
the policy pauses pruning until it is confirmed again. Pruning never removes a backup still waiting
for upload, never deletes a cloud file that is not in the ledger of verified copies, and refuses to
run when any backup name is later than the clock (a wrong clock would otherwise select the wrong
copies). Cloud deletions are one `deletefile` per selected copy.

### Monthly restore drill from the cloud

Download the newest verified cloud copy and its manifest (rclone decrypts) into a fresh directory on
the verified card, compare the SHA-256 with the ledger and the manifest, then run the ADR-0008 full
restore check in that directory (integrity, foreign keys, schema version and objects, row counts,
first/last-row fingerprints, application smoke test). Preflight: free space on the card of at least
`max(10 GiB, download + 1.2 × database)` plus the headroom (1 GiB), which must also stay free while
restoring. The drill refuses scratch space that is not on the verified card or that lies inside the
live state directory, deletes login sessions and bench leases in the restored copy before the smoke
test, and removes the scratch directory afterwards (and any left by a crashed drill). It never
writes the production database and never starts a server or a backend on the copy. A report is
kept in `<state>/offsite/drills/`.

### Activation

`offsite_backup.enabled` is false in every shipped configuration and requires the card's mount
path, UUID and volume id; the cloud remote and account are TBD. While disabled the earlier eMMC
backups continue (ADR-0008) and every offsite command exits "not activated". Activation steps,
account authorization, credentials and recovery keys: `docs/operations/offsite-backups.md`.

## Consequences

- One daily backup of about 0.5 GiB at one year (ADR-0008 measurements) goes to the card and the
  cloud; seven local copies need about 3.5 GiB plus the 10 GiB drill space on the card, so a card of
  at least 32 GB is proposed (owner to confirm).
- The eMMC no longer stores backups once activated (the old backup and drill timers are disabled).
- Anyone with root on the controller can read the rclone configuration, including the crypt keys:
  encryption protects the cloud copy against the cloud account, not against a compromised controller.
- Losing the crypt password and salt makes every cloud copy unreadable; they are escrowed away from
  the controller (one sealed printed copy) and tested (operations doc).
- The cloud copies live in a colleague's personal OneDrive and count against that account's quota
  (about 20 GiB at one year for 42 copies). The folder owner can delete them, and they disappear with
  that account; copy-only uploads protect them from the controller, not from the folder owner.
- Not physically tested: the USB reader and card on the CM4, OneDrive throughput and token refresh,
  and power loss during a write remain checks for CP-00.

## Evidence

`tests/integration/test_offsite.py` (AT-OFF-01 to AT-OFF-12), including a round trip through the
real rclone binary (1.60.1) and a local crypt remote; headless Chromium check of the Settings card
and the policy confirmation on 2026-10-12.
