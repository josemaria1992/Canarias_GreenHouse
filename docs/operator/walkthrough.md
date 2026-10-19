# Operator walkthrough and owner acceptance tour

This guide has two parts:

- **Part A: orientation.** What the screen tells you, roles, simulation speed and time, start conditions.
- **Part B: the owner acceptance tour.** Sixteen cases, UAT-01 to UAT-16, each with the exact start
  condition, the buttons to press and the expected result. Every result starts as **NOT RUN**.

Record your results in the result form, `owner-tour/results.html` in the release folder. Pressing
**Download results** on that page saves a CSV file that you send back; the page itself keeps nothing.
You can use `owner-checklist.csv` (it opens in Excel) instead.

**Getting started**, the first item of the menu in AquaCrop, is something else: four short tasks with
pictures for a first-time operator (read the tank level, water one sector, stop watering, recover from
a simulated problem). It is kept separate from this tour, and the result form has its own part for it.

Everything in the tour is the **simulated** installation (`installation-80`, profile SIMULATED). No serial
port is opened and nothing physical can move. A PASS here accepts the software behaviour in
simulation, not the wiring, the probes or the drive.

The illustrated version of this guide, with screenshots of the steps, is `owner-tour/index.html` in the
release folder. It works offline.

To report something, copy `owner-feedback-template.md` (in the release folder) once per finding. The
six review questions of the brief, with Claude's answers for you to judge, are in
`docs/operator/owner-tour/review-notes.md`; the delivery issues Claude found and fixed while rehearsing
are in `docs/operator/owner-tour/issues.md`.

---

## Part A: orientation

### The banner

| Banner | Meaning |
|---|---|
| `SIMULATION — DEMO MODEL` (blue) | Everything is simulated. No serial port is open. Nothing physical can change |
| `REAL — READ ONLY` (dark) | Real modules are polled. The software sends no write frames |
| `REAL — BENCH WRITES ARMED (user, until …)` (red) | A commissioning lease allows the listed relay outputs on an isolated bench |
| grey "Operating mode is shown after sign-in" | Not signed in yet |

If the server stops answering, the red banner `CONNECTION LOST — values below are STALE (n s old).
Commands disabled.` appears, and the buttons stop sending. Old data is never shown as a green "normal".

### The status line

Under the menu, one line says in plain words what is happening, for example:

`Digital Twin · Watering stopped · Main tank 60% · Last reading 1 second ago`

When there are open problems, the line ends with a link such as `2 problems` that opens Alarms & events.

Press **Details** at the end of the line to see the technical line, for example:

`AquaCrop 0.3.4 · Profile SIMULATED · config installation-80@2026-10-13.80.1 · processes STOPPED · recipe demo-sim@v1 · twin RUN 1× · simulated time 0:00:01 since the run started · source TWIN · newest data 0.3 s old · site clock 2026-10-13 20:13:44 Atlantic/Canary`

- **simulated time** is the simulation clock since the current run started. At 10× it runs ten times
  faster than your watch.
- **site clock** is the real time in the configured site time zone: `Atlantic/Canary` for the
  installation, `Europe/Stockholm` for the Malmö bench configuration.

Press **Hide details** to close it again.

### Which build is running

Settings, card **Versions**:

| Row | What it shows |
|---|---|
| Software | the installed package version (0.3.4) |
| Release | release version, source commit, build time, and a green **wheel verified** mark when the program installed on this PC is exactly the one in the release folder you started it from |
| Config | `installation-80@2026-10-13.80.1 (SIMULATED)` |
| Inventory | `6 modules: 16 AI + 32 DI + 32 DO = 80 points` |
| Data folder | where accounts, history and recipes are kept |

The launcher window prints the same information when it starts.

### The pages

The menu lists **Getting started**, **Overview**, **Watering**, **Sector 1**, **Sector 2**, **Main tank**,
**Nutrients & acid**, **Drainage return**, **Recipes**, **Digital Twin**, **I/O test bench**, **History**,
**Alarms & events** and **Settings**. On a narrow screen the menu is behind the **Menu** button.

Each process has a card on its page. The card's heading is the process name (for example
**Watering** or **Main tank refill**), followed by its state, one sentence saying what it is doing,
and its buttons. The start button names what it does (for example **Start automatic watering** or
**Switch on automatic refill**), **Manual mode** offers a **Run once** form, and the red stop button
names the process (for example **Stop watering**). The identifiers, the checks before starting and the
history of each process are under the card's **Details**.

### Roles

| Role | Can |
|---|---|
| viewer | read everything except operator audit exports |
| operator | command the **simulation**, change and check recipes, set or override virtual signals, mark problems as seen, clear stops after fixing the problem |
| admin | everything an operator can, plus put recipes in use and restore the previous settings, manage users, export audit, backups |
| commissioning (separate flag) | arm a bench lease on a real read-only installation. An admin without the flag cannot |

A first-time or reset password must be changed before anything else works.

### Simulation speed and the 60-second history

- **Speed** (Digital Twin page: **1×**, **10×**, **60×**) changes how fast the simulated plant and
  controllers run. A 300 s watering step takes 30 s of your time at 10×.
- **History** records one complete snapshot every **60 s of wall-clock time**, whatever the speed, and
  keeps 365 days. Events (alarms, commands, batches) are recorded at once.
- So at 60× one history snapshot covers about an hour of simulated time, and a chart of a fast run
  looks coarse. That is expected, not a recording fault. The speed never changes the recording cadence.
- **Pause** stops the simulated clock (history still records the paused state every 60 s). **Step**
  advances one tick while paused. **Run** continues.

### Starting conditions: presets and DEMO values

The Digital Twin page has a card called **Examples**: "These examples set up the simulated greenhouse so
you can try different situations." Pick one under **Choose an example**. Under the list you see a
one-sentence preview, what loading it does, and a **Details** panel with its id and UAT number.

- **Set up a situation (nothing starts)**. These set synthetic starting levels and start nothing: you
  press the buttons yourself. Their ids start with `tour_`.
- **Run a situation (it starts by itself)**. These press buttons on a timer and are used by the
  automated tests. The tour uses only **See a refill stop when EC is lost** of this kind.

**Load this example** and **Reset run** start a new simulation run: levels and the plant return
to the start condition, every process is stopped, simulated faults and hand-set readings are cleared.
History, accounts, recipes and tank confirmations are kept, and the speed stays as set.

All quantities below are **DEMO / synthetic**. They exist so the simulation does something visible.
They are not production settings, they are not your illustrative refill quantities, and none of them
is an approved recipe.

| Where | DEMO value |
|---|---|
| Normal start (Reset run) | main tank 12,000 L, fertiliser tank A 3,000 L, fertiliser tank B 3,000 L, acid tank 2,000 L, drainage tank 1,200 L, main tank pH 6.1, EC 1.6 mS/cm |
| **Try refilling the main tank** (`tour_low_main`) | main tank 7,600 L (below the 8,000 L refill start level) |
| **Try acid dosing** (`tour_ph_high`) | main tank pH 6.8 (above the 6.0 target) |
| **Try returning drainage water** (`tour_sump_high`) | drainage tank 2,700 L (above the 2,500 L return start) |
| **See the return wait for a full main tank** (`tour_main_full_sump_high`) | main tank 18,800 L, drainage tank 4,700 L (main tank above the 18,500 L hold level) |
| **See a refill stop when EC is lost** (`tour_ec_lost_at_valve_opening`) | main tank 7,600 L, all tanks confirmed prepared, automatic refill switched on at 1 s, EC input forced to raw 0 at 13.5 s |
| Recipe `demo-sim@v1` (DEMO_PRESET) | refill starts below 8,000 L, fills to 15,000 L, EC target 1.8 ± 0.1 mS/cm, A:B 1:1, 20 L A+B per step, at most 150 L A and 150 L B per refill, 300 L always kept in each fertiliser tank |
| | watering: sector 1 300 s then sector 2 300 s; Run once at most 1,800 s |
| | acid: pH target 6.0 ± 0.2, 2 L per dose, 600 s wait after a dose, at most 30 L per batch and 60 L per 24 h |
| | drainage return: starts at 2,500 L, stops at 800 L (drainage tank); held above 18,500 L, resumes below 18,000 L (main tank); the return pumps take turns at each start |
| Tour edits (UAT-05, UAT-06) | sector 1 120 s (`demo-sim@v2`); recipe `tour-fixed@v1` with "Add a set amount of water" and "Add set amounts of nutrients": 1,000 L water, 15 L A, 15 L B, EC afterwards 1.4 to 2.2 mS/cm. Both are undone at the end of their case |

### Returning to a known state

At any point:

1. **Overview**, press **Stop all processes** and confirm.
2. **Digital Twin**, press **Reset run** and confirm. Press **1×**.
3. **Recipes**: the recipe marked **In use** should be `demo-sim@v1`. If it is not, open
   **Restore previous settings**, write why, and press **Restore previous settings** (each press is
   recorded) until it is.
4. **Alarms & events**: under **Problems now**, press **Mark as seen** on anything left open.

For a completely fresh start, use a separate trial data folder (README-FIRST, "A separate trial data
folder"). Never delete the normal data folder.

---

## Part B: owner acceptance tour

**Before you start**

- Start AquaCrop with `AquaCrop-Demo.cmd` and sign in as the administrator you created.
- Work through the cases in order. Each takes 2 to 10 minutes; the whole tour about 90 minutes.
- Each case starts from "Returning to a known state" unless it says otherwise.
- Speeds are suggestions. Slower is fine; faster skips things you may want to see.
- If something differs from "Expected", note what you saw and take a screenshot (Windows: Win+Shift+S).
  Keep passwords and e-mail addresses out of screenshots and comments.

---

### UAT-01 Sign in and confirm the build

| | |
|---|---|
| Goal | You are on the simulated installation, and you can see which build and configuration run |
| Page | top of every page; Settings |
| Role | admin |
| Start | AquaCrop running, browser at <http://127.0.0.1:8000/> |
| Load | none |
| Speed | any |
| Duration | 2 min |

Steps:
1. On the sign-in page, type your **Login name** and **Password** and press **Log in**.
2. Read the banner and the status line under the menu.
3. Press **Details** at the end of the status line and read the technical line. Press **Hide details**.
4. Open **Settings** and find the **Versions** card.

Expected:
- Banner `SIMULATION — DEMO MODEL`.
- Status line `Digital Twin · Watering stopped · Main tank 60% · Last reading 1 second ago` (the age
  may read a few seconds).
- Details: the technical line starts `AquaCrop 0.3.4 · Profile SIMULATED · config installation-80@2026-10-13.80.1`.
- Versions: Inventory `6 modules: 16 AI + 32 DI + 32 DO = 80 points`; Release shows `0.3.4`, a source
  commit, a build time and **wheel verified**; Data folder is `%LOCALAPPDATA%\AquaCrop\demo` (shown
  in full).

Fail if: the banner says REAL, the version is not 0.3.4, the inventory is not 80 points, or Release
shows MISMATCH (or "not started from a release folder" although you used `AquaCrop-Demo.cmd`).

Return: nothing to undo.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-02 Find your way from the diagram

| | |
|---|---|
| Goal | The Overview diagram matches the installation, and every part shows its state and opens its page |
| Page | Overview, then each process page |
| Role | any |
| Start | normal start |
| Load | Digital Twin, **Reset run** |
| Speed | 1× |
| Duration | 3 min |

Steps:
1. Open **Overview** and scroll to the card **Greenhouse diagram**.
2. Click, in turn: the tank **Fertiliser A**, the **Main tank**, the pump **Main pump 1**, the box
   **Sector 1**, the box **Sector 2** and the **Drainage tank**. Each click selects the part and shows
   it in the panel beside the diagram (below it on a narrow screen). In the panel, press the link
   **Open …** and then use the browser's Back button. Hovering over a part names it with its state.
3. On **Main tank**, in the card **Main tank (20,000 L)**, read the rows **Water level** and
   **Acidity (pH)**.

Expected:
- The panel names the part. For a tank or a sensor it shows a new reading with its value, unit, time
  and validity. For a pump or a valve it shows the state asked for and the state reported.
- The **Open …** links open **Nutrients & acid**, **Main tank**, **Watering**, **Sector 1**,
  **Sector 2** and **Drainage return**.
- Water level reads `12,000 L Reading valid 1 second ago`: value, unit, quality and age. Other
  readings show value, unit and age (for example `6.1 pH 1 second ago`); hovering over the value shows
  where it comes from and `Reading valid`. A reading that is not valid always shows its quality in
  words, for example `No reading` or `Reading is old`.
- The diagram shows 2 main pumps on one shared pump drive, 2 return pumps, 3 tanks (Fertiliser A,
  Fertiliser B, Acid) with transfer pumps, sector 1 with channels 1 to 6 and sector 2 with channels 7
  to 13. There is no stirrer or air pump.
- Each pump and valve shows one state in words and colour: stopped or closed, running or open,
  starting or opening, stopping or closing, fault, or not known. The legend under the diagram explains
  them. Water moves and arrows show the flow direction only where a reading reports flow.

Fail if: a part opens the wrong page or nothing, a value lacks its unit, quality or age, or the layout
differs from the installation.

Return: nothing to undo.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-03 Automatic watering alternates pump and sector

| | |
|---|---|
| Goal | Automatic watering runs sector 1 with main pump 1, changes over through the shared drive, then sector 2 with main pump 2 |
| Page | Watering, Sector 2 |
| Role | operator or admin |
| Start | normal start, recipe `demo-sim@v1` |
| Load | Digital Twin, **Reset run**, then **10×** |
| Speed | 10× |
| Duration | 5 min |

Steps:
1. **Watering**, card **Watering**, press **Start automatic watering**.
2. In the card **Sectors**, read the **Valve** column of Sector 1. Open **Technical details** further
   down and read the row **Selector K1 (main pump 1)**. Each shows what the program asked for and what
   the equipment reports separately.
3. After 300 simulated seconds (about 30 s at 10×) watch the sentence on the card: the pump drive
   stops and is confirmed stopped, the valve closes, then the pump selection changes.
4. Open **Sector 2**: sector 2 is watered with main pump 2.
5. Back on **Watering**, press **Stop watering**.
6. On the card **Watering**, open **Details: checks before starting …, history and identifiers** and
   read **History of this process**.

Expected:
- Step 1: the card says `Automatic watering: Sector 1 with main pump 1, … of 300 s watered.`
- Step 2: Sector 1 valve `Open asked: open · reports: open`; Selector K1 `Running asked: on · reports:
  closed`.
- Step 3: the sentence names each stage in brackets (`stopping the pump drive`, `pausing after the
  stop`, `closing the valve`, `releasing the pump`), then sector 2 with main pump 2.
- Step 5: the stop sequence runs to all equipment off and the card says `Watering is stopped.`; under
  **Technical details**, **Completed steps** lists round 1, Sector 1, MP1, 300 s.
- Step 6: each start reads `SELECT`, `OPEN`, `LEAD` (the valve is open before the pump starts),
  `START`, `VERIFY_FLOW`, `IRRIGATE`; each changeover reads `STOP_DRIVE`, `STOP_SETTLE`, `CLOSE`,
  `DESELECT` before the next `SELECT`.

Fail if: both pumps are selected at once, the drive runs during the pump changeover, a sector runs
with the other sector's pump, or Stop watering leaves any equipment switched on.

Pause and step: on Digital Twin, **Pause** freezes the run during the changeover and **Step** advances
one tick.

Return: Watering stopped; press **1×**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-04 Run once is bounded, and raw outputs are refused

| | |
|---|---|
| Goal | Run once waters one sector once with the pump you choose; out-of-range requests and raw output commands are refused with the reason |
| Page | Watering; I/O test bench |
| Role | operator or admin |
| Start | normal start |
| Load | Digital Twin, **Reset run**, then **10×** |
| Speed | 10× |
| Duration | 4 min |

Steps:
1. **Watering**, card **Watering**, press **Manual mode**.
2. Under **Run once**: **Sector** `Sector 1`, **Main pump** `Main pump 2`, **Watering time (s)**
   **99999**. Press **Run once**.
3. Change **Watering time (s)** to **60**. Press **Run once**.
4. While it runs, press **Start automatic watering**.
5. Open **I/O test bench**, row `relay_A.DO3`, press **ON**.
6. Back on **Watering**, press **Stop watering**.

Expected:
- Step 1: the card says `Manual mode: nothing is running. Set the values below and press Run once.`
- Step 2: refused: `The run time must be between 1 and 1,800 seconds.`
- Step 3: the card says `Running once: Sector 1 with main pump 2, 60 s (watering).`; **Equipment
  switched on** lists main pump 2 selected, the pump drive and the valve of Sector 1 (under
  **Details**: `contactor.MP2.cmd`, `drive.MAIN.run`, `valve.S1.cmd`).
- Step 4: refused with the reason: `Automatic mode starts only when the process is stopped (now: Manual
  mode). Press Stop first.`; the card stays in manual mode.
- Step 5: `REJECTED (raw output commands are not available on the installation twin: outputs belong to the process controllers; …)`.
- Step 6: the stop sequence ends with all equipment off and the card says `Watering is stopped.`

Fail if: the 99999 s request starts anything, main pump 1 runs instead of main pump 2, the raw ON
switches the output, or Stop watering leaves any equipment switched on.

Return: Watering stopped; press **1×**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-05 Recipe change: check, use, next cycle, restore

| | |
|---|---|
| Goal | A value out of range is refused with the reason; a valid change applies from the next cycle only; Restore previous settings puts the previous version back |
| Page | Recipes; Watering |
| Role | admin (operators change and check; administrators put settings in use) |
| Start | recipe `demo-sim@v1` in use |
| Load | Digital Twin, **Reset run**, then **10×** |
| Speed | 10× |
| Duration | 8 min |

Steps:
1. **Recipes**, on the row marked **In use** (`demo-sim@v1`), press **Change settings**.
2. In the section **Main tank levels**, field **Refill starts below**: type **25000**. Press
   **Check settings** (under "3. Check the changes").
3. **Watering**, press **Start automatic watering**. Wait until sector 1 is watered.
4. Back on **Recipes** (your changes are still open), set **Refill starts below** back to **8000**.
5. Open the section **Watering**. Under **Watering sequence**, step 1 (Sector 1), set the watering
   time to **120**. Press **Check settings**.
6. On the row `demo-sim@v2`, open **Use these settings**, type why in **Why are you changing the
   settings? (recorded)** and press **Use these settings**. Press **Discard changes** to close the
   editor.
7. Watch **Watering** until sector 1 is watered again.
8. Press **Stop watering**. On **Recipes**, open **Restore previous settings**, type why and press
   **Restore previous settings**.

Expected:
- Step 2: the draft is saved and checked. Beside the field, in red: `Refill starts below: at most 20000
  L`; the typed 25000 stays in the field. The row `demo-sim@v2` reads **Draft, not checked** with the
  number of problems found, and offers no **Use these settings**.
- Step 5: `✓ demo-sim@v2 passed the check`; the row reads **Checked, not in use**.
- Step 6: `demo-sim@v2` is **In use**; the step already running keeps `demo-sim@v1` (card **Details**,
  row Recipe).
- Step 7: the next cycle uses `demo-sim@v2` and sector 1 is watered for 120 s.
- Step 8: `demo-sim@v1` is **In use** again; **History of settings in use** lists every change with
  user and reason.

Fail if: the draft with the value out of range can be put in use, the running step changes its
watering time, the next cycle does not use 120 s, or Restore previous settings does not bring back
`demo-sim@v1`.

Return: Watering stopped, `demo-sim@v1` in use, press **1×**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-06 Main tank refill: EC target and set amounts

| | |
|---|---|
| Goal | A refill adds water and nutrients in measured steps up to the EC target and the fill level; a recipe with set amounts adds fixed amounts and checks EC afterwards |
| Page | Digital Twin; Nutrients & acid; Main tank; Recipes |
| Role | admin |
| Start | `demo-sim@v1` in use |
| Load | Digital Twin, example **Try refilling the main tank**, **Load this example** |
| Speed | 60× |
| Duration | 10 min |

Steps:
1. **Nutrients & acid**, card **Fertiliser and acid tanks**: on the row **Fertiliser tank A**, open
   **Confirm tank A is prepared**, type what was prepared and press **Confirm prepared**. Do the same
   for **Fertiliser tank B**.
2. **Main tank**, card **Main tank refill**, press **Switch on automatic refill**. Press **60×** on
   Digital Twin.
3. Watch **Nutrients & acid**, card **Refill now**: the water fill and the staged A and B additions.
   Meanwhile press **Start automatic watering** on **Watering**, see it water, then press **Stop
   watering**.
4. Wait for the refill to end (about 1.5 min at 60×). On **Main tank**, open the refill card's
   **Details** and read **Last result**.
5. Press **Stop refill**.
6. **Recipes**, on the row marked **In use**, press **Copy to a new recipe**. **Recipe id**
   **tour-fixed**, **Name** **Tour FIXED_VOLUMES (synthetic)**. Under "1. Choose how to refill" choose
   **Add a set amount of water** and **Add set amounts of nutrients**. In the section **Refill**:
   **Water to add** **1000**, **Fertiliser A to add** **15**, **Fertiliser B to add** **15**,
   **Nutrient strength (EC) afterwards: at least** **1.4**, **at most** **2.2**; open **Advanced
   settings: timing and tolerances** and set **Wait after adding nutrients before checking EC**
   **600**. Press **Check settings**; on the row `tour-fixed@v1` open **Use these settings**, type
   why and press **Use these settings**. Press **Discard changes**.
7. Load **Try refilling the main tank** again, press **Switch on automatic refill** on **Main tank**, press
   **60×**, wait for the refill to end.
8. Press **Stop refill**, then on **Recipes** use **Restore previous settings** with a reason.

Expected:
- Step 1: both rows read **Prepared**, with your name, the time and your note.
- Step 3: watering runs on its own checks while the refill continues.
- Step 4: `result COMPLETED`, `EC_TARGET_REACHED` at about 1.75 mS/cm, end level about 15,000 L, A and
  B measured and equal (61.5 L each in the rehearsal).
- Step 6: `✓ tour-fixed@v1 passed the check`, then `tour-fixed@v1` is **In use**.
- Step 7: `result COMPLETED`, `EC_VALID` at about 1.83 mS/cm, about 1,000 L water and 15 L each of A and
  B, measured.
- Step 8: `demo-sim@v1` **In use**.

Fail if: a transfer pump runs before the tanks are confirmed prepared, the level overshoots the fill
level noticeably, A and B differ beyond the meter tolerance, or a refill is COMPLETED with EC out of
range.

Return: refill stopped, `demo-sim@v1` in use, press **1×**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-07 A water fill means a prepared tank must be prepared again

| | |
|---|---|
| Goal | Adding water to a prepared fertiliser tank marks it as needing preparation again |
| Page | Nutrients & acid |
| Role | operator or admin |
| Start | normal start |
| Load | Digital Twin, **Reset run** |
| Speed | 10× |
| Duration | 3 min |

Steps:
1. **Nutrients & acid**, row **Fertiliser tank A**: **Confirm tank A is prepared**, type what was
   prepared, press **Confirm prepared**.
2. Tab **Fertiliser tank A**, card **Water fill of fertiliser tank A**, press **Manual mode**. Under
   **Run once**, **Fill up to (L)** **3100**, press **Run once**.
3. Press **10×** and wait until the fill ends.
4. Press **Stop filling tank A**.

Expected:
- Step 1: fertiliser tank A reads **Prepared**.
- Step 3: the fill card's **Details**, **Last result**: `result COMPLETED · reason TARGET_LEVEL_REACHED (3100 L)`.
  Fertiliser tank A reads **Needs preparing again** and `Tank A needs preparing again before dosing.`

Fail if: the tank stays Prepared after the water fill, or the fill overshoots.

Return: press **1×**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-08 Acid dosing

| | |
|---|---|
| Goal | Acid is added in doses with a wait after each until pH is in range; an unusable pH probe holds dosing with the reason |
| Page | Nutrients & acid (tab Acid tank); I/O test bench |
| Role | admin |
| Start | `demo-sim@v1` in use |
| Load | Digital Twin, example **Try acid dosing**, **Load this example** |
| Speed | 60× |
| Duration | 6 min |

Steps:
1. **Nutrients & acid**, row **Acid tank**: **Confirm the acid tank is prepared**, type what was
   prepared, press **Confirm prepared**. Open the tab **Acid tank**.
2. Under **Settings in use**, read **Acidity (pH) target**, **Acid in each dose**, **Wait after a
   dose** and the limits.
3. Card **Acid dosing**, press **Switch on automatic acid dosing**. Press **60×**.
4. Watch 2 L doses, each followed by `waiting for the water to mix`. Wait for the batch to end (about
   1.5 min at 60×).
5. Press **1×**. **I/O test bench**, row `analog_A.AI6` (pH): type **0**, press **Override**, give a
   reason.
6. Back on the tab **Acid tank**, read the card **Acid dosing**. Then on I/O test bench press
   **Clear** on `analog_A.AI6`.
7. Press **Stop acid dosing**.

Expected:
- Step 2: `Acidity (pH) target 6 (acid is added above 6.20)`, `Acid in each dose 2 L`, `Wait after a
  dose 600 s`, `Most acid in one batch 30 L`, `Most acid in 24 hours 60 L`.
- Step 4: card **Details**, **Last result**: `PH_IN_BAND` after about 8 doses, about 16.8 L in total,
  pH from 6.80 to about 6.20.
- Step 6: dosing waits: `Waiting: No usable acidity (pH) reading. (INVALID: raw 0.0 uA outside valid
  range)` (under **Details**, as recorded: `pH unavailable (INVALID: raw 0.0 uA outside valid range)`).

Fail if: a dose is larger than the dose set, doses do not wait for mixing, dosing continues with an
unusable pH, or base is dosed (there is no base).

Return: override cleared, acid dosing stopped, press **1×**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-09 Drainage return: pumps take turns, and the return holds when the main tank is full

| | |
|---|---|
| Goal | The return pumps take turns at each start, and the return holds while the main tank is full |
| Page | Drainage return; Watering; Overview |
| Role | operator or admin |
| Start | `demo-sim@v1` in use |
| Load | example **Try returning drainage water**, later example **See the return wait for a full main tank** |
| Speed | 60×, then 1× |
| Duration | 6 min |

Steps:
1. Load **Try returning drainage water**. **Drainage return**, card **Drainage return**, press **Switch on
   automatic return**.
2. **Watering**, press **Start automatic watering** (water drains from the sectors into the drainage
   tank). Press **60×**.
3. Watch **Drainage return** until two runs are listed under **Technical details**, **Recent runs**.
4. **Overview**, press **Stop all processes**. Press **1×**.
5. Load **See the return wait for a full main tank**. **Drainage return**, press **Switch on automatic return**.

Expected:
- Step 3: the first run uses return pump 1 (RP1) and the next return pump 2 (RP2).
- Step 5: the card says `Drainage return: automatic, on hold.` with `Waiting: Main tank full (18800 L):
  return held until it is below 18000 L; …`. Under **Other problems on this page** a **Serious**
  problem reads `Sump high (4700 L) while the main tank is full (18800 L): return is held. …` (its
  **Details** show the code `SUMP_HIGH_MAIN_FULL`). No return pump runs: both read `Stopped asked:
  off · reports: stopped`.

Fail if: both return pumps run together, the same pump starts twice in a row, or water is returned
while the main tank is above the hold level.

Return: drainage return stopped (**Stop return pump**); on **Alarms & events** press **Mark as seen**.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-10 Equipment fault and deliberate recovery

| | |
|---|---|
| Goal | A drive trip or a stuck valve stops watering with the cause; Mark as seen restarts nothing; recovery is deliberate |
| Page | Digital Twin (Simulated faults); Watering; Alarms & events; Sector 1 |
| Role | operator or admin |
| Start | normal start |
| Load | Digital Twin, **Reset run**; 10× until watering, then 1× |
| Speed | 10×, then 1× |
| Duration | 6 min |

Steps:
1. **Watering**, press **Start automatic watering**. When sector 1 is watered, press **1×**.
2. **Digital Twin**, card **Simulated faults**, row **Pump drive**: `Pump drive (MAIN)`, `trips
   (drive fault)`, press **Apply**.
3. **Watering**: read the card and its **What to do** steps.
4. Digital Twin, row **Pump drive**: `no fault (working)`, **Apply**. **Alarms & events**, under
   **Problems now**: press **Mark as seen** on each problem.
5. **Watering**: read the card, press **Clear stop after fixing the problem**, then **Start automatic
   watering**, then **Stop watering**.
6. Digital Twin, **Reset run**. Row **Valve**: `Sector 1 valve`, `stays closed`, **Apply**.
   **Watering**, press **Start automatic watering**.
7. Open **Sector 1** and read the card. Remove the fault (`no fault (working)`, **Apply**), mark the
   problems as seen, and on **Watering** press **Clear stop after fixing the problem**.

Expected:
- Step 3: `Watering stopped because of a problem: the pump drive reports a fault.` The steps are
  **Mark as seen**; **Fix the problem:** `Find and fix the cause at the pump drive, then reset the drive
  there.` and `The program still sees the problem.`; **Clear stop after fixing the problem**, not
  available yet, with the reason beside it (while the drive still reports the fault: `Not available yet:
  the pump drive still reports a fault: reset it at the drive.`; later `Not available yet: mark the
  problem as seen first (step 1).`); then **Go to Watering**.
- Step 4: marking as seen leaves watering **Stopped because of a problem**; nothing restarts.
- Step 5: the button now reads `Ready. Clearing the stop does not start anything.` Clearing gives
  **Stopped**, not running; watering starts only when you press **Start automatic watering**.
- Step 7: `Watering stopped because of a problem: the valve for Sector 1 did not open.`; the drive
  never ran (card **Details**: `VALVE_OPEN_TIMEOUT: S1 open limit not seen in 5 s`).

Fail if: marking as seen restarts anything, the stop can be cleared while the fault is present, or the
drive runs while the valve is not open.

Return: faults removed, problems marked as seen, watering stopped.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-11 Reading quality: outside the range, no reading, old, and EC lost at valve opening

| | |
|---|---|
| Goal | Each kind of bad reading is shown as what it is and stops what depends on it; EC lost while a nutrient valve opens never lets a transfer pump start |
| Page | I/O test bench; Main tank; Digital Twin; Nutrients & acid; Alarms & events |
| Role | admin |
| Start | `demo-sim@v1` in use |
| Load | **Reset run** for parts 1 to 3; example **See a refill stop when EC is lost** for part 4 |
| Speed | 10×, then 1× |
| Duration | 8 min |

Steps:
1. **Outside the range.** **Watering**, **Start automatic watering**; when it waters, press **1×**.
   **I/O test bench**, row `analog_A.AI1` (main tank level): type **0**, press **Override**, give a
   reason. Open **Main tank**. Then **Clear** the override.
2. **No reading.** **Reset run**. I/O test bench, module card `analog_B`, press **Simulate timeout**.
   Read the quality of the `analog_B` and `analog_A` points. Press **Restore**.
3. **Old readings.** **Reset run**. Digital Twin, press **Stall acquisition**; wait 5 s and open
   **Overview**. Then on Digital Twin press **Resume acquisition**.
4. **EC lost at valve opening.** Press **1×**. Load **See a refill stop when EC is lost** (it presses its
   own buttons). Wait 20 s. Open **Nutrients & acid**, then **Alarms & events** and open **Commands
   sent to the equipment (including refused ones)** and **Event log (newest first)**. On I/O test
   bench press **Clear** on `analog_A.AI7` and wait 60 s.
5. **Overview**, press **Stop all processes**.

Expected:
- Step 1: `Watering stopped because of a problem: a reading this process needs is missing.`; on Main
  tank the Water level reads `-5,000 L Reading outside the allowed range 1 second ago set by hand`
  (the value is not used); the card **Details** read
  `OBSERVATION_UNAVAILABLE: required observations unusable: ['tank.MAIN.level (INVALID: raw 0.0 uA outside valid range)']`.
- Step 2: `analog_B` points read `No reading`; `analog_A` points still read `Reading valid`.
- Step 3: the status line's `Last reading` and the age of the readings keep growing while acquisition
  is stalled.
- Step 4: the refill's nutrient step is `STOPPED (EC unavailable during step 1: INVALID: raw 0 uA
  outside valid range)`; **Problems now** lists `There is no usable nutrient strength (EC) reading`
  (code `REFILL_EC_UNAVAILABLE`); the commands list has no transfer pump A or B ON. After the probe
  returns, the stopped step does not resume; only the water fill continues.

Fail if: a bad value is shown as `Reading valid`, one module's failure marks another module bad, or a
transfer pump A or B is switched on while EC is unusable.

Return: overrides cleared, all processes stopped, problems marked as seen.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-12 I/O test bench: 80 points, relay and transistor outputs

| | |
|---|---|
| Goal | All six modules and 80 points can be exercised in IO_TEST mode; the 32 added channels stay unused in plant mode |
| Page | I/O test bench |
| Role | admin |
| Start | every process stopped |
| Load | **Reset run**, then **Stop all processes** |
| Speed | 1× |
| Duration | 5 min |

Steps:
1. **I/O test bench**, press **Use IO_TEST (raw injection)**.
2. Row `analog_A.AI1`: **12000**, **Inject**. Row `digital_A.DI1`: **Inject 1**.
3. Row `relay_A.DO1`: **ON**. Row `digital_B.DO1`: **ON**.
4. Read the module cards: relay contact outputs versus transistor (sink) outputs.
5. Module card `digital_A`: **Simulate timeout**; look at the other cards; **Restore**.
6. Switch `relay_A.DO1` and `digital_B.DO1` **OFF**. Press **Use TWIN (process model)**.
7. Read the `digital_A` and `digital_B` rows.

Expected:
- Steps 2 and 3: the raw values and states appear on the rows.
- Step 4: the two Relay (D) modules are labelled relay contacts; the two IO 8CH modules transistor
  sinks (500 mA max).
- Step 5: only `digital_A` goes offline; the other five stay online.
- Step 7: the added channels read **Unused channel**, "nothing is connected to this channel (not a
  fault)". No plant signal is bound to `digital_A` or `digital_B`.

Fail if: a module or point is missing, an added channel shows a plant signal, or a timeout on one
module affects another.

Return: test outputs OFF, source TWIN.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-13 History and Download readings

| | |
|---|---|
| Goal | History shows recorded readings with names, units and the site time zone; the download is complete and says so |
| Page | History |
| Role | any |
| Start | AquaCrop has run at least 3 minutes (history records every 60 s of wall time) |
| Load | none |
| Speed | any |
| Duration | 3 min |

Steps:
1. **History**. Press **Last hour** (or **Today** after the earlier cases).
2. Under **Readings to show**, group **Tank levels**, make sure **Main tank water level**
   (`analog_A.AI1`) and **Drainage tank water level** (`analog_A.AI2`) are ticked (they are ticked at
   the start, with **Pump drive running signal**). Up to 8 readings can be ticked.
3. Press **Download readings** and open the downloaded CSV file (`aquacrop-readings.csv`).

Expected:
- The chart says the times are site time (`Atlantic/Canary`), the legend gives names and units
  (`Main tank water level [L] (SIM)`), times with no valid reading are shaded and never bridged, and
  run events are marked.
- The CSV's last line is `# COMPLETE: <n> rows` (or `# TRUNCATED …` for a very long range).

Fail if: the times are in another zone without saying so, lines bridge gaps, or the CSV has no
completeness line.

Return: nothing to undo.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-14 Users and roles

| | |
|---|---|
| Goal | You can add a viewer and an operator; each must set their own password; each can do only what the role allows |
| Page | Settings, Users and roles; a private browser window |
| Role | admin, then the new users |
| Start | signed in as admin |
| Load | none |
| Speed | any |
| Duration | 6 min |

Steps:
1. **Settings**, card **Users and roles**: user name **tour-viewer**, an initial password of 12 or more
   characters, role **viewer**, **Create user**. Repeat for **tour-operator** with role **operator**.
2. Open a **private/incognito** window (so your admin session stays signed in) at
   <http://127.0.0.1:8000/> and sign in as `tour-viewer`.
3. Change the password when asked. Open **Watering** and hover over **Start automatic watering**.
4. Press **Log out**, sign in as `tour-operator`, change the password, open **Watering** and
   **Recipes**.

Expected:
- The first sign-in of each new user asks for a new password before anything else.
- Viewer: the buttons are disabled; hovering shows `needs an operator or administrator account`, and
  the card says `Starting and stopping needs an operator or administrator account.`
- Operator: **Start automatic watering**, **Manual mode** and **Stop watering** work; recipes can be
  changed and checked, but a checked recipe shows `An administrator can use these settings.` and
  **Restore previous settings** is not offered; creating users is refused.

Fail if: a new user can work without changing the password, a viewer can command anything, or an
operator can put a recipe in use or create accounts.

Return: keep the tour users or disable them under Settings; they are kept across restarts.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-15 Backup, stop and restart: nothing is lost, nothing restarts

| | |
|---|---|
| Goal | A local backup is made; after a stop and restart, accounts, recipes and history are kept and every process starts stopped |
| Page | Settings, Backups; the launcher window; History |
| Role | admin |
| Start | after the earlier cases |
| Load | none |
| Speed | any |
| Duration | 5 min |

Steps:
1. **Settings**, card **Backups**, press **Create backup now**. Wait until **Local copies** shows 1 or
   more.
2. Note the users (Settings) and the recipes (Recipes).
3. In the launcher window press **Ctrl+C** (or close the window). Wait for "AquaCrop stopped cleanly".
4. Double-click `AquaCrop-Demo.cmd` again. It does not ask for a password this time. Sign in.
5. Check Settings (users), Recipes, Overview and History.

Expected:
- Step 1: the backup is listed with its size; **Off-device** says NOT ACTIVATED (the SD card and
  OneDrive copies wait for the on-site setup).
- Step 4: no first-start questions; the launcher prints the same data folder.
- Step 5: the same users and recipes; every process **Stopped**; nothing restarts by itself; the
  History chart shows no data for the minutes AquaCrop was stopped.

Fail if: an account, recipe or history is missing, a process restarts without being asked, or the
launcher asks to create the administrator again.

Return: nothing to undo.

Your result: record it in the result form, owner-tour/results.html.

---

### UAT-16 Narrow screen and lost connection

| | |
|---|---|
| Goal | The pages are usable on a narrow screen with Stop in reach, and a lost connection is shown clearly with commands disabled |
| Page | every page; Watering |
| Role | any |
| Start | AquaCrop running |
| Load | none |
| Speed | 1× |
| Duration | 4 min |

Steps:
1. Make the browser window narrow, or use the browser's device view (F12, then the phone icon). Use
   **Menu** to open Overview, Watering, Sector 1, Sector 2, Main tank, Nutrients & acid, Drainage
   return, Recipes, Alarms & events, History and Digital Twin.
2. On **Watering**, look at the top of the screen, then scroll down.
3. Restore the window. Open **Watering**.
4. Stop AquaCrop in the launcher window (Ctrl+C) while the page stays open. Wait 10 s.
5. Press **Start automatic watering**.
6. Start AquaCrop again and reload the page.

Expected:
- Step 1: everything readable; no page as a whole scrolls sideways (a wide table or the diagram may
  scroll inside its own box).
- Step 2: a bar with **Watering**, its state and **Stop watering** is in the first screen and stays at
  the top while you scroll.
- Step 4: banner `CONNECTION LOST — values below are STALE (n s old). Commands disabled.`
- Step 5: nothing is sent.
- Step 6: normal display; all processes stopped.

Fail if: a page needs sideways scrolling as a whole, Stop watering is not in the first screen of
Watering, old values look normal, or a command is sent while the connection is lost.

Note: a narrow browser window is not a test of a phone over your OpenVPN. That is a separate check
at commissioning.

Return: nothing to undo.

Your result: record it in the result form, owner-tour/results.html.

---

## Other references

- Headless check of the same behaviour: `python -m aquacrop demo verify --config installation-80 --seed 42`.
- Claude's browser rehearsal of this tour: `scripts/ui_check/run_tour_rehearsal.py`. Its results are a
  separate record and never count as an owner PASS.
- The legacy demos (`installation-48`, `demo-48`, `demo-24`) are kept as test fixtures for the earlier
  48- and 24-point layouts. They are not the demo to accept.

## Real installation, read-only

There is no controller and no twin on a real profile in this release (ADR-0009). The installation
pages are shown read-only:
- Overview, Nutrients & acid, Main tank, Watering, Sector 1, Sector 2 and Drainage return show the
  diagram and the real readings, labelled `REAL`.
- Signals without an approved physical mapping read "not measured".
- Instead of the start, Manual mode and stop buttons, each page says why control is not offered.
  Recipes and the Digital Twin are not shown.

Overview also shows per-device health and the relay channel modes read from each module.
The I/O view shows live values with origin `REAL`. Channels whose configured mode is UNKNOWN, or
whose module reports a different mode than configured, show `Not calibrated` with the raw value only.

Polling stops, restarts, reconnects and shutdowns never write to a module. Relays keep whatever
state they had; the Pi cannot de-energise anything when it loses power or communication.

## Bench lease (isolated bench only)

Only with every production load disconnected and an owner-authorised setup (see
`docs/commissioning/`). Steps:

1. Sign in as a user with the commissioning flag. Open **I/O test bench**.
2. Tick the exact relay outputs to allow, type the confirmation phrase exactly
   (`PRODUCTION LOADS DISCONNECTED`), describe the setup, choose a duration (1 to 30 min), press
   *Arm lease*. The server re-reads all relay modes; anything other than eight NORMAL modes refuses
   the lease.
3. ON/OFF buttons appear only on allowlisted outputs. Each command re-reads the modes, sends one
   FC05 frame, then reads the coils back. The message shows the outcome:
   `READBACK_CONFIRMED` (coil state matches; not proof the load moved), `FAILED` (module refused,
   or readback differs) or `OUTCOME_UNKNOWN` (no valid answer; the relay may or may not have
   switched; check with a fresh read and your own eyes, never by resending).
4. End the lease with *End lease now*, or let it expire. Logging out, password change, revoking the
   user's sessions, disabling the user or removing the flag also end it. **Ending a lease sends
   nothing**: relays stay as they are. Use the teardown checklist in the commissioning procedure.

A restart never restores a lease, and no configuration file or environment variable can arm one.
