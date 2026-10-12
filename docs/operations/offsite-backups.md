# Operations: SD-card backups and encrypted OneDrive copies (ADR-0012)

**Status: implemented, not activated.** Every shipped configuration has `offsite_backup.enabled:
false`, and the card, the OneDrive account and the folder are TBD. Until the owner confirms them
(section 1) the eMMC backups of ADR-0008 continue and every `offsite` command answers "not
activated". Nothing here has been tried with a physical card, a USB reader on the CM4 or a real
OneDrive account; the tests use a simulated card and a local rclone crypt remote.

Application backups are not motor safety. Nothing here stops equipment.

## 1. Owner decisions before activation

| Item | Needed | Proposal (not a decision) |
|---|---|---|
| SD card | make, capacity, which physical card | 32 GB or larger, high-endurance type; 7 backups (about 3.5 GiB at one year) plus 10 GiB drill space plus 1 GiB headroom |
| USB reader | which reader, which CM4 port | any USB mass-storage reader; record `lsusb` and `/dev/disk/by-id` on CP-00 |
| Filesystem | format and label | ext4, label `AQUACROP-BK` |
| Mount path | where it mounts | `/media/aquacrop-sd` |
| OneDrive account | which account (personal or Microsoft 365 business), who owns it | an AquaCrop-owned account, not a personal one |
| Folder | which folder | a dedicated folder, e.g. `AquaCrop/greenhouse-backups` |
| Who authorizes | who signs in once to authorize rclone | the account owner, on their own computer |
| Recovery key escrow | where the crypt password and salt are kept | the owner's password manager plus a sealed printed copy in a safe |
| Copy policy | confirm or change the counts | 7 daily on the card; 30 daily + 12 monthly in the cloud |

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
   - `n` new remote `aquacrop-crypt`, type `crypt`, remote `aquacrop-onedrive:AquaCrop/greenhouse-backups`
     (the confirmed folder), `filename_encryption = standard`, `directory_name_encryption = true`,
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

What the token can do: rclone's default OneDrive scopes allow reading and writing the account's
files, not only the backup folder. A dedicated AquaCrop account limits what a stolen controller can
reach. To withdraw access, revoke the app's consent in the Microsoft account's privacy/app
permissions page (or the Microsoft 365 admin portal) and delete the remote from `rclone.conf`.
Refresh tokens expire after a period without use; the daily upload keeps them in use, and an expired
token shows as `AUTHORIZATION` in Settings and in the `OFFSITE_BACKUP` alarm: repeat steps 1 and 2.

## 4. Credentials and recovery keys (kept away from the controller)

| Secret | Where it lives | Where the recovery copy lives |
|---|---|---|
| Crypt password and salt (password2), plain values | only obscured inside `rclone.conf` on the controller | owner's password manager **and** a sealed printed copy in a safe; never on the controller in plain text, never in OneDrive, never in git |
| OneDrive account password and MFA | the account owner | the owner's normal account recovery |
| OAuth token | `rclone.conf` (0600, user `aquacrop`) | not needed: re-authorize (section 3) |
| `rclone.conf` itself | controller only | not copied off the controller; it can be rebuilt from the escrowed password and salt plus a new authorization |

Without the crypt password and salt every cloud copy is unreadable, also to AquaCrop. Encryption
protects the cloud copy against the cloud account and the provider; it does not protect against
someone with root access to the controller, who can read `rclone.conf`.

**Escrow test (yearly, and after any change of keys):** on a computer that is not the controller,
build a fresh `rclone.conf` from the escrowed password and salt plus a new authorization, download one
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
