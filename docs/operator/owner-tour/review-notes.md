# Owner review questions: Claude's answers, for the owner to judge

The owner's brief asks six review questions. These are Claude's answers after rehearsing the tour on the
delivered build (updated for the 0.3.3 screens), with the case where the owner can judge each one. They
are not owner results: the owner decides, and records the decision in `owner-tour/results.html`
(comments) or a finding (`feedback-template.md`).

**1. Is it immediately clear which tank, sector, pump or valve a control affects?**
Mostly yes. Each process page opens with its process card: what is running (its "Equipment in use"
line), what happens next and its Stop button, named after the process
(Stop watering, Stop refill, Stop return pump). The diagram, folded under the card, highlights that
page's equipment. The manual forms name pump and sector. Weak spot: the Watering page controls the whole
watering process (both sectors), which is right for the shared drive but could surprise. Judge in UAT-02,
UAT-03 and UAT-04.

**2. Are manual/automatic/stop, the active recipe, a start that is held back and the next required action easy to find?**
Yes: the plain status line at the top of every page says what the watering is doing; each process card
shows its state in words (for example "Stopped because of a problem"), the recipe in use and what happens
next. A start that is held back shows under "Checks before starting" with the failing check in words; a
stop because of a problem shows the cause and the next step. Judge in UAT-04 (automatic watering refused
during manual mode), UAT-08 (pH reading not valid) and UAT-10.

**3. Does the diagram explain actual flow and measured feedback rather than only button requests?**
Yes, with a legend under the diagram: the outline is what was asked, the fill is what the equipment
reports, a hatched symbol has no position signal, and flows, levels, pressures, pH and EC are the values
the controllers read with their quality and age. Main pumps show their selection contactor (K1, K2) under
Technical details; whether the motor runs is shown on the shared drive. Judge in UAT-02, UAT-03 and UAT-09.

**4. Are alarm cause, affected equipment and recovery sequence understandable without opening logs?**
Yes for the faults rehearsed: a problem shows a headline in words ("Watering stopped: the pump drive
reports a fault"), the equipment, one next step and a link to its process, with Mark as seen and Clear
stop after fixing the problem as separate buttons. A step that is not available yet says why (for example
that the drive still reports the fault). None of these buttons starts equipment; starting again is its
own button. The alarm code and audit are under Details. Judge in UAT-10 and UAT-11.

**5. Can the owner change permitted settings from the UI and see when they take effect?**
Recipe values, yes: an administrator chooses how to refill, sets amounts or targets, checks the changes
(a value outside its range is shown beside its field, with the typed value kept) and uses these settings;
the page says that a running step keeps the old version and the next cycle or batch uses the new one.
Restore previous settings is one action. Not editable from the UI by design: hardware mapping, retention,
cadence and storage budget. Judge in UAT-05.

**6. Are low levels, old or missing readings and unused channels visually distinct?**
Yes: a reading that is not valid carries a mark in words (Reading is old, with the time of the last update;
No reading; Reading outside the allowed range); an invalid level draws the tank hatched instead of filled;
a low level is a valid reading with its own alarm and a held-back start on the process; unused channels
show a neutral "Unused channel" mark, not a fault colour; and a lost connection marks every value old with
commands disabled. Judge in UAT-11, UAT-12 and UAT-16.
