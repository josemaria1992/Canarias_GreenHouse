# ADR-0011 Timestamp quarantine after a clock correction
Status: accepted by the owner 2026-10-12 20:04 UTC (owner request of 2026-10-12 16:01 UTC).
Builds on ADR-0008 (wall-time labels, clock holds, retention release).

## Context

ADR-0008 stores each 60 s snapshot under its wall-clock label at most once per database, and holds
new snapshots while the clock is behind the newest stored label (`HISTORY_CLOCK_BEHIND`). That rule
has a gap: if the clock ran ahead (a forward step, or a controller that booted with a wrong date)
and is then corrected, the future-dated labels block every new snapshot until real time passes them,
possibly for days. Deleting or rewriting those records would hide what happened.

The owner asked for an audited administrator action that:

1. preserves the original records, values and timestamps and flags the affected records as having
   suspect timestamps;
2. resumes correctly timestamped recording without old future-dated labels blocking new snapshots;
3. prevents genuine duplicates with a recording-session/clock-epoch identifier and a sequence;
4. keeps retention held until an administrator separately confirms the date and the cutoff;
5. distinguishes suspect records clearly in charts and exports;
6. is tested with clock corrections, quarantine, restart and retention together;
7. never authorizes deletion or rewrites historical timestamps by itself.

No schema migration and no new hardware are authorized (ADR-0008).

## Decision

### The quarantine is an event, not a schema change

An administrator's confirmation writes one `HISTORY_TIMESTAMPS_QUARANTINED` event. Its payload
holds the id ranges it covers per table (`cycles.cycle_pk`, `samples.id`, `events.id`,
`commands.rowid`), the alarm occurrences raised in that range or stamped at or after its earliest suspect stamp (an
alarm that was already active is refreshed in place, without a new `ALARM_*` event), the process sessions involved, the
counts, the first and last suspect stamps, the start and end anchors (below), the new clock epoch,
who confirmed it and when, and `clock_checked_by_administrator: true`. Nothing else is written:
no row is changed, moved or deleted. The events of this kind are a register: retention never
deletes them, and every process loads them at start (`QuarantineRegister`).

Rows are matched to a quarantine by id range, so flagging costs one range check per row and no
index or column is added. The ids are the record order the database already keeps.

### What is proposed

`GET /api/history/quarantine` proposes a quarantine whenever some record outside an existing
quarantine is stamped more than 5 minutes later than the current clock:

- **Start.** The process session that wrote the first future-dated record. If that session recorded
  a forward clock step (`WALL_CLOCK_STEP`, `HISTORY_SNAPSHOTS_MISSED` or a `RETENTION_HELD` for a
  forward step) before it, the quarantine starts at that marker: the records before the step were
  stamped correctly. Otherwise the session's clock is taken as wrong from its start.
- **End.** The first marker of a backward correction after the start (`HISTORY_CLOCK_BEHIND`,
  `RETENTION_HELD` for a clock behind the stored records, `WALL_CLOCK_STEP` backward), capped before
  the first records of a later session; otherwise up to the newest record.
- Clock markers carry a **watermark**, the newest id of each table when they were written (taken in
  the writer's transaction), so the anchors map exactly to id ranges.

The proposal shows the counts and stamped ranges per table and has an id (`proposal_id`, a hash of
its content). Confirming requires that id and the administrator's statement that the current date
and time were checked against an independent source; a proposal that changed in between is
refused (409) with the new one.

### Clock epochs keep identities unique

Each quarantine starts a new clock epoch. Snapshot ids carry it: `slot/60s/<label>` in epoch 0,
`slot/60s/e<n>/<label>` from epoch n on. The duplicate check (one label per database, ADR-0008)
compares a new label with the newest *trusted* wall-time snapshot, walking back over quarantined id
ranges (one index lookup per range). Recording therefore resumes at the next boundary after the
quarantine, and the corrected-clock labels never collide with the suspect ones: a snapshot is
identified by epoch plus label, and ordered by its sequence (`cycle_pk`). The recorder re-reads its
high-water mark (`rebase`), clears the clock-behind hold and logs `HISTORY_CLOCK_RESUMED`. Nothing
is back-filled.

### Retention stays held

Confirming a quarantine holds retention (`TIMESTAMPS_QUARANTINED`, replacing a clock hold it
answers) and the hold survives restarts: at start the newest of `RETENTION_HELD`,
`RETENTION_CLOCK_CONFIRMED` and quarantine events decides. Only the separate administrator
confirmation of the date and the proposed cutoff (ADR-0008) releases it.

Quarantined records are never judged by their suspect stamps: label- and time-based pruning skips
their id ranges, and they are deleted whole only when the quarantine's own confirmation time (a
trusted time) is older than the retention cutoff. Then retention writes
`HISTORY_QUARANTINE_RETIRED` for it: SQLite can hand out the highest ids again once they are deleted,
so a retired quarantine's ranges no longer flag or delete anything (its clock epoch still counts).
Range deletion runs from the lowest id up, so the top of a range is the last row to go. The newest-record checks that decide whether the
clock is trusted ignore quarantined rows and sessions.

### Visible everywhere

- Trends: every row carries `timestamp_suspect` and `clock_epoch`; the chart draws suspect points as
  a separate dashed grey series with ring markers, never joined to trusted points, and says so in the
  caption and legend.
- Sample export: `snapshot_seq`, `timestamp_suspect` (`SUSPECT`) and `quarantine_id`, plus a header
  note when quarantines exist. Audit export: `seq`, `timestamp_suspect`, `quarantine_id`.
- Events, alarm occurrences and command history carry the flags; the browser shows a
  `SUSPECT TIME` marker. The stored time is always shown unchanged.
- Settings, "Clock and timestamps": the epoch, the recorder's state, quarantines in force, the
  proposal with its anchors and counts, and (administrators only) the date-check box and button.

## Consequences

- Recording recovers right after a confirmed quarantine instead of waiting for real time to pass
  the wrong labels; history keeps every record with its original stamp.
- A quarantine cannot delete or authorize deletion; quarantined records live one retention window
  past the confirmation time, then go whole.
- Readers that ignore the new columns still see the original stamps; the flags are additive.
- The anchors depend on clock markers written by this software version; a database from before
  ADR-0011 has no watermarks, so its quarantine starts at the session start (a wider, still
  explicit, range).
- Not physically tested: no RTC or NTP behaviour of the CM4 has been observed (CP-00).

## Evidence

`tests/integration/test_quarantine.py` (AT-QUA-01 to AT-QUA-07); headless Chromium check of the
Settings card, the trends chart and the `SUSPECT TIME` markers on 2026-10-12.
