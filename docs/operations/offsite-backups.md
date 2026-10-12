# Operations: SD-card backups and encrypted OneDrive copies (ADR-0012)

**Status: implemented, not activated.** Every shipped configuration has `offsite_backup.enabled:
false`. The owner chose the card, the account, the folder, the key escrow and the copy policy on
2026-10-12 (section 1); the card's UUID and the rclone remote are set up on the controller at
activation (sections 2, 3, 5). Until then the eMMC backups of ADR-0008 continue and every `offsite`
command answers "not activated". Nothing here has been tried with a physical card, a USB reader on the CM4 or a real
OneDrive account; the tests use a simulated card and a local rclone crypt remote.

Application backups are not motor safety. Nothing here stops equipment.

## 1. Owner decisions before activation

Recorded from the owner's messages of 2026-10-12 19:19, 19:20 and 19:38 UTC. Values that identify people
or grant access (the account's address, the sharing link, the drive id) are kept in the site record
outside this public repository.

| Item | Owner decision (2026-10-12) | Still open, done on site at activation |
|---|---|---|
| SD card | 64 GB card, ext4 | which physical card; its filesystem UUID (`blkid`) at enrolment |
| USB reader | not specified | any USB mass-storage reader; record `lsusb` and `/dev/disk/by-id` on CP-00 |
| Filesystem | ext4 | label `AQUACROP-BK` (proposal) |
| Mount path | not specified | `/media/aquacrop-sd` (proposal) |
| OneDrive account | the owner's **personal** Microsoft account (signed in with the owner's institutional e-mail address; address in the site record) | none: a personal account can reach the shared folder (section 3.1, way 1 or 2) |
| Folder | `AQUACROP_DATOS_CANARIAS` in a colleague's **personal** OneDrive (onedrive.live.com), shared with the owner | the colleague shares it with the owner's address with edit rights; set the remote to it (section 3.1) |
| Who authorizes | the account holder (the owner) signs in once | the sign-in itself |
| Recovery key escrow | a printed copy of the crypt password and salt in a sealed envelope | print it when the keys are generated; store it away from the controller (section 4) |
| Copy policy | 7 daily on the card; 30 daily + 12 monthly in the cloud (confirmed) | an administrator confirms the same policy in Settings after activation; that confirmation is what lets the software prune |

Space: 7 copies (about 3.5 GiB at one year) plus 10 GiB drill space plus 1 GiB headroom fit the
64 GB card with room to spare. In the cloud, 42 copies need about 20 GiB at one year, counted
against the **folder owner's** OneDrive quota, not the owner's. The folder owner's plan has 1 TB
(owner, 2026-10-12), so the copies use about 2 % of it.

## 2. SD card

```text
lsblk -f                               # identify the card: check size and model, never guess
sudo mkfs.ext4 -L AQUACROP-BK /dev/sdX1 # ONLY on the confirmed, empty card: this erases it
sudo blkid /dev/sdX1                    # note the UUID
sudo mkdir -p /media/aquacrop-sd
# add deploy/fstab-aquacrop-sd.example to /etc/fstab with that UUID (nofail: boots without the card)
sudo mount /media/aquacrop-sd
sudo chown aquacrop:aquacrop /media/aquacrop-sd && sudo chmod 0700 /media/aquacrop-sd
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop offsite enroll-card \
     --config ${AQUACROP_CONFIG} --state-dir /var/lib/aquacrop --mount /media/aquacrop-sd --uuid <UUID>
```

`enroll-card` checks that the path is a mounted, writable filesystem with that UUID and not the
eMMC, writes `.aquacrop-backup-volume.json` on the card and prints the three values for the
configuration (`mount_path`, `filesystem_uuid`, `volume_id`). Before every write the software checks
all of them again; a missing, replaced, read-only or reformatted card stops the job with an alarm and
nothing is written anywhere else. To replace a card, enrol the new card and update the configuration;
keep the old card until the new one holds a verified backup.

## 3. OneDrive authorization and the encrypted remote

The CM4 has no browser. rclone's OneDrive authorization is done once on another computer:

1. On a trusted computer with a browser and the same rclone version: `rclone authorize "onedrive"`.
   The account owner signs in (with MFA) and approves. rclone prints a token (JSON).
2. On the controller, as the `aquacrop` user, create the configuration file in the state directory
   (rclone writes refreshed tokens back to it, so it must be writable by the service, and
   `/etc` is read-only for the units):

   ```text
   sudo -u aquacrop install -d -m 0700 /var/lib/aquacrop/rclone
   sudo -u aquacrop rclone config --config /var/lib/aquacrop/rclone/rclone.conf
   ```

   - `n` new remote `aquacrop-onedrive`, type `onedrive`, paste the token when asked
     ("use auto config? n"), choose the drive the owner confirmed.
   - Point `aquacrop-onedrive` at the shared folder `AQUACROP_DATOS_CANARIAS` (section 3.1), so
     that `aquacrop-onedrive:` is the folder itself.
   - `n` new remote `aquacrop-crypt`, type `crypt`, remote `aquacrop-onedrive:` (the shared folder),
     `filename_encryption = standard`, `directory_name_encryption = true`,
     password: `g` (generate, 256 bits), salt (password2): `g` (generate, 256 bits).
   - **Write the generated password and salt down now** (section 4). rclone stores them only
     obscured in the file; obscuring is not encryption.
3. `chmod 0600 /var/lib/aquacrop/rclone/rclone.conf` (owner `aquacrop`). Never commit it, never copy
   it into a ticket, chat or the OneDrive folder.
4. In the configuration: `offsite_backup.cloud.remote: "aquacrop-crypt:"`,
   `rclone_config: /var/lib/aquacrop/rclone/rclone.conf`, and `account_note` naming the account and
   folder (no secrets).
5. `python -m aquacrop offsite check --config ... --state-dir /var/lib/aquacrop` must report the card
   verified and the remote as `crypt`.

What the token can do: rclone's OneDrive token is not limited to one folder. Because the folder
lives in a colleague's OneDrive, the token needs `Files.ReadWrite.All`, which reaches every file the
signed-in account can read or write, including its own files and other folders shared with it.
Someone who steals the controller and its `rclone.conf` can reach those files until consent is
revoked. Set `access_scopes = Files.Read Files.ReadWrite Files.Read.All Files.ReadWrite.All
offline_access` on `aquacrop-onedrive` to leave out SharePoint site browsing (`Sites.Read.All`), keep
the controller physically secured, and revoke at once if it is lost. (A dedicated account holding
only this folder would limit it further.)

To withdraw access, revoke the app's consent in the Microsoft account's privacy/app
permissions page (or the Microsoft 365 admin portal) and delete the remote from `rclone.conf`.
Refresh tokens expire after a period without use; the daily upload keeps them in use, and an expired
token shows as `AUTHORIZATION` in Settings and in the `OFFSITE_BACKUP` alarm: repeat steps 1 and 2.

### 3.1 The shared folder

The folder belongs to a colleague's personal OneDrive, so rclone has to be told which drive and
which folder; a plain `aquacrop-onedrive:` would otherwise be the signed-in account's own OneDrive.
Neither way below has been tried with these accounts yet (CP-00); use the first that passes the
check.

The account that signs in for rclone must be able to open the folder in onedrive.live.com:

- **A personal Microsoft account** (the owner's address opens onedrive.live.com and the folder is
  under *Shared*): use way 1 or 2 with that account.
- **A work or school account** (the address signs in to the institution's Microsoft 365): rclone
  cannot reach a folder in a personal OneDrive from it. Then either the colleague who owns the folder
  signs in for rclone (the token then reaches the colleague's whole OneDrive, as above), or the
  folder is created in the owner's institutional OneDrive instead (then `drive_type = business`, and
  the tenant may require IT approval for rclone, "Need admin approval").

1. **Shortcut.** Signed in at onedrive.live.com, *Shared* → `AQUACROP_DATOS_CANARIAS` → *Add
   shortcut to My files*; then `aquacrop-onedrive:AQUACROP_DATOS_CANARIAS` is the folder, and the
   crypt remote points there (`aquacrop-onedrive:AQUACROP_DATOS_CANARIAS` instead of
   `aquacrop-onedrive:` in section 3).
2. **Drive and folder ids.** In [Graph Explorer](https://developer.microsoft.com/graph/graph-explorer),
   signed in with the same account, run `GET https://graph.microsoft.com/v1.0/me/drive/sharedWithMe`.
   In the entry named `AQUACROP_DATOS_CANARIAS`, `remoteItem.parentReference.driveId` is the
   colleague's drive and `remoteItem.id` is the folder. In the `aquacrop-onedrive` remote set
   `drive_type = personal`, `drive_id` to the drive and `root_folder_id` to the folder.

Check: `rclone lsf aquacrop-onedrive:` lists the folder's contents (and nothing of the owner's own
files), then `rclone copyto` a small test file into it, `rclone lsf` shows it, and `rclone
deletefile` removes it. The colleague sees only encrypted names and contents; they cannot read the
backups.

The colleague owns these copies: they can delete them or stop sharing, and the folder disappears if
their account is closed (for example when they leave the institution). AquaCrop only uses `copyto`,
so nothing on the controller can delete cloud copies by mirroring, but it cannot protect them from
the folder owner. Uploads would then fail (`AUTHORIZATION` or `RCLONE_FAILED`) and the alarm shows it;
before the colleague leaves, move the folder to a long-lived account. Share the folder with named
people, not as "anyone with the link": with an edit link anyone who has it can delete the copies
(they still cannot read them).

## 4. Credentials and recovery keys (kept away from the controller)

| Secret | Where it lives | Where the recovery copy lives |
|---|---|---|
| Crypt password and salt (password2), plain values | only obscured inside `rclone.conf` on the controller | a printed copy in a sealed envelope (owner decision, 2026-10-12), stored away from the controller's site; never on the controller in plain text, never in OneDrive, never in git, never in a chat or ticket |
| OneDrive account password and MFA | the account owner | the owner's normal account recovery |
| OAuth token | `rclone.conf` (0600, user `aquacrop`) | not needed: re-authorize (section 3) |
| `rclone.conf` itself | controller only | not copied off the controller; it can be rebuilt from the escrowed password and salt plus a new authorization |

Print the two values when rclone generates them in section 3, on a printer that does not keep
copies, and check the printout against the screen before sealing it. With one paper copy, a lost or
damaged envelope makes the cloud copies unreadable once the controller is also lost: replace it at
once (new keys, new uploads) if it is ever missing.

Without the crypt password and salt every cloud copy is unreadable, also to AquaCrop. Encryption
protects the cloud copy against the cloud account and the provider; it does not protect against
someone with root access to the controller, who can read `rclone.conf`.

**Escrow test (yearly, and after any change of keys):** on a computer that is not the controller,
open the envelope, build a fresh `rclone.conf` from the printed password and salt (typed in by hand,
which also proves the printout is legible), reseal it in a new envelope plus a new authorization, download one
cloud copy with `rclone copyto aquacrop-crypt:backups/<name> ./<name>` and run
`python -m aquacrop backup verify --backup ./<name> --full --scratch-dir <dir>`. The monthly drill
only proves the keys on the controller; this proves the escrowed ones.

## 5. Activation

1. Owner confirmations of section 1 recorded (who, when).
2. Card enrolled (section 2) and remote configured (section 3); `offsite check` passes.
3. Configuration: `offsite_backup.enabled: true` with the card values; `python -m aquacrop config
   validate --config ...`; restart the service.
4. Timers: disable the eMMC ones and enable the offsite ones.

   ```text
   sudo install -m 0644 deploy/aquacrop-offsite-*.service deploy/aquacrop-offsite-*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl disable --now aquacrop-backup.timer aquacrop-restore-drill.timer
   sudo systemctl enable --now aquacrop-offsite-backup.timer aquacrop-offsite-upload.timer aquacrop-offsite-drill.timer
   sudo systemctl start aquacrop-offsite-backup.service    # first backup now
   sudo systemctl start aquacrop-offsite-drill.service     # first drill once the upload verified
   ```

5. Settings, *Off-device backups*: card verified, backup published, upload verified (cryptcheck),
   drill passed. Then an administrator reviews the copy policy and confirms it; pruning starts with
   the next daily run. Until then nothing is deleted, so watch the card's free space.
6. Existing eMMC backups in `/var/lib/aquacrop/backups` stay where they are; remove them deliberately
   once the card and cloud copies are verified.

## 6. Status and alarms

- Settings, *Off-device backups*: activation, card identity and free space, last backup, copies on
  the card, upload queue (attempts, next try, last error), verified cloud copies, last upload, last
  drill and whether one is due, copy policy and the latest pruning plan.
- `python -m aquacrop offsite status|check|plan` on the controller.
- Events: `OFFSITE_BACKUP_CREATED|FAILED`, `OFFSITE_UPLOADED`, `OFFSITE_UPLOAD_FAILED`,
  `OFFSITE_PRUNED`, `OFFSITE_PRUNE_FAILED`, `OFFSITE_DRILL_PASSED|FAILED`, `OFFSITE_POLICY_CONFIRMED`.
- Alarm `OFFSITE_BACKUP` (WARNING), one occurrence per target:
  - `sd_card`: the last backup failed, or no verified backup for 36 h;
  - `cloud_upload`: uploads waiting more than 36 h, or a failure needing a person
    (authorization, remote not encrypted, local file missing or changed, cloud full, rclone missing);
  - `restore_drill`: the last drill failed, or none passed in 35 days.
- Exit codes: 0 done, 1 failed (recorded), 2 not activated, 75 another offsite job was running.

## 7. Failures and what happens

| Failure | Effect | What to do |
|---|---|---|
| Card or reader missing | `SD_CARD_MISSING`; nothing written, no fallback to the eMMC; recording continues | reinsert, `sudo mount /media/aquacrop-sd`, run the backup unit |
| Another card mounted | `SD_CARD_WRONG` | insert the enrolled card, or enrol the new one and update the configuration |
| Card read-only | `SD_CARD_READ_ONLY` | check the write-protect switch and `dmesg`; `fsck` with the card unmounted |
| Card full | `NO_SPACE` before writing; earlier backups kept | confirm the policy (pruning) or use a larger card |
| Backup fails verification | not published; on read-back failure set aside as `*.bad`; earlier kept | check the card (`dmesg`, `fsck`); replace a failing card |
| Offline | upload queued, retried every 15 min with backoff; recording unaffected | nothing; the queue drains when the connection returns |
| Authorization expired | `AUTHORIZATION`, alarm at once | re-authorize (section 3) |
| Remote not a crypt remote | `NOT_ENCRYPTED`, nothing uploaded | fix `rclone.conf` |
| Cloud full | `CLOUD_FULL` | free space in the account or confirm the policy |
| Drill preflight fails | `NO_SCRATCH_SPACE`, nothing downloaded | free space on the card (pruning) or a larger card |
| Drill fails | report `ok: false`, alarm | keep all copies; investigate before trusting the cloud copies |
| Power loss during a job | partial backup removed by the next run; an upload stays queued until cryptcheck passed; a drill's scratch directory removed by the next drill; a cut journal line ignored | nothing |

## 8. Restoring

- **From the card** (controller intact): `python -m aquacrop backup restore --backup /media/aquacrop-sd/aquacrop/backups/<file> --target /tmp/restore-test.sqlite3`
  first, then the maintenance restore of `service.md` with the server stopped.
- **From the cloud** (controller lost): install the software on the replacement, rebuild
  `rclone.conf` from the escrowed keys plus a new authorization (sections 3 and 4), list the copies
  with `rclone lsjson aquacrop-crypt:backups`, download one with `rclone copyto`, then
  `backup verify --full` and `backup restore` to a new instance as above. A restored database holds
  the user accounts (password hashes) and old login sessions, which still expire by their idle and
  absolute limits; bench leases from an earlier process are closed at start, never restored, and the
  restored installation starts read-only. (The monthly drill goes further: it deletes sessions and
  leases from its throw-away copy.)

## 9. Not yet verified

The USB reader and card on the CM4 (mount at boot, hot-plug, write speed, power loss), the 0.5 GiB
daily upload over the site's connection, OneDrive token refresh over weeks, and the escrow test.
These belong to CP-00 and need the hardware and the owner's account.
