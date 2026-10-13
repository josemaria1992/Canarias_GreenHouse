# Owner review questions: Claude's answers, for the owner to judge

The owner's brief asks six review questions. These are Claude's answers after rehearsing the tour on the
delivered build, with the case where the owner can judge each one. They are not owner results: the owner
decides, and records the decision in `owner-checklist.csv` (comments) or a finding
(`feedback-template.md`).

**1. Is it immediately clear which tank, sector, pump or valve a control affects?**
Mostly yes. Each process page opens with the installation diagram with that page's equipment
highlighted, and every control sits on the page of the equipment it drives (Main pumps, Sector 1,
Sector 2, Nutrients & acid, Drainage). The MANUAL forms name pump and sector explicitly. Weak spot: the
Main pumps page controls the whole irrigation process (both sectors), which is right for the shared
drive but could surprise. Judge in UAT-02, UAT-03 and UAT-04.

**2. Are manual/AUTO/STOP, the active recipe, an inhibited start and the next required action easy to find?**
Yes for the state and recipe: each process card shows its state pill (AUTO, MANUAL, STOPPED,
FAULT_LOCKOUT), phase and recipe version, and the status line at the top shows the processes and
the active recipe on every page. A refused or inhibited start appears as a "Waiting" line and in the
"Permissives (n failing)" list; a lockout shows a red line with the cause and the recovery steps.
Judge in UAT-04 (AUTO refused during MANUAL), UAT-08 (pH invalid) and UAT-10.

**3. Does the diagram explain actual flow and measured feedback rather than only button requests?**
Yes, with a legend under the diagram: the outline is the requested state, the fill is the observed
feedback, a hatched symbol has no feedback point, and flows, levels, pressures, pH and EC are the
values the controllers read with quality and age. Main pumps show their selection contactor (K1, K2);
whether the motor runs is shown on the shared drive symbol (fixed in 0.3.2, DEL-15). The stock tanks
have no stock-flow-to-main measurement beyond the transfer pump flow. Judge in UAT-02, UAT-03 and UAT-09.

**4. Are alarm cause, affected equipment and recovery sequence understandable without opening logs?**
Yes for the faults rehearsed: the page's alarm table shows severity, the alarm, the process,
"Message and next step" (for example "drive fault input active. Acknowledge, clear the cause, then
Reset lockout"), and a reset refused while the cause is present says why ("drive fault input still
active (reset the drive locally)"). The process card repeats the cause. Judge in UAT-10 and UAT-11.

**5. Can the owner change permitted settings from the UI and see when they take effect?**
Recipe values, yes: an administrator edits a draft, validates it (invalid values are refused with the
reason), activates it, and the page says the running step keeps the old version and the next cycle or
batch uses the new one; rollback is one action. The recipe in force shows on each process card and in
the status line. Not editable from the UI by design: hardware mapping, retention, cadence and storage
budget. Judge in UAT-05.

**6. Are low levels, stale/unavailable readings and unknown/unassigned channels visually distinct?**
Yes: a value carries a quality pill (GOOD, STALE, UNAVAILABLE, INVALID) and its age; an invalid level
draws the tank hatched instead of filled; a low level is a normal GOOD value with its own alarm and a
"Waiting" reason on the process; unassigned channels show a neutral UNASSIGNED pill (0.3.2, DEL-05),
not a fault colour; and a lost connection turns every value STALE with commands disabled. Judge in
UAT-11, UAT-12 and UAT-16.
