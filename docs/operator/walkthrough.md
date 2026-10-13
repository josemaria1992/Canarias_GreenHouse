# Operator walkthrough and owner acceptance tour

This guide has two parts:

- **Part A: orientation.** What the screen tells you, roles, simulation speed and time, presets.
- **Part B: the owner acceptance tour.** Sixteen cases, UAT-01 to UAT-16, each with the exact preset,
  buttons and expected result. Record your results in `owner-checklist.csv` (it opens in Excel) or in
  the "Your result" line under each case. Every result starts as **NOT RUN**.

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

If the server stops answering, a **CONNECTION LOST — values below are STALE** banner shows the age of
the last good data, and command buttons stop sending. Stale data is never shown as a green "normal".

### The status line

Under the menu, one line says what is running, for example:

`AquaCrop 0.3.2 · Profile SIMULATED · config installation-80@2026-10-13.80.1 · processes STOPPED · recipe demo-sim@v1 · twin RUN 1× · simulated time 0:00:01 since the run started · source TWIN · newest data 0.3 s old · site clock 2026-10-13 20:13:44 Atlantic/Canary`

- **simulated time** is the simulation clock since the current run started. At 10× it runs ten times
  faster than your watch.
- **site clock** is the real time in the configured site time zone: `Atlantic/Canary` for the
  installation, `Europe/Stockholm` for the Malmö bench configuration.

### Which build is running

Settings, card **Versions**:

| Row | What it shows |
|---|---|
| Software | the installed package version (0.3.2) |
| Release | release version, source commit, build time, and a green **wheel verified** mark when the program installed on this PC is exactly the one in the release folder you started it from |
| Config | `installation-80@2026-10-13.80.1 (SIMULATED)` |
| Inventory | `6 modules: 16 AI + 32 DI + 32 DO = 80 points` |
| Data folder | where accounts, history and recipes are kept |

The launcher window prints the same information when it starts.

### Roles

| Role | Can |
|---|---|
| viewer | read everything except operator audit exports |
| operator | command the **simulation**, edit and validate recipes, inject or override virtual signals, acknowledge alarms, reset lockouts |
| admin | everything an operator can, plus activate and roll back recipes, manage users, export audit, backups |
| commissioning (separate flag) | arm a bench lease on a real read-only installation. An admin without the flag cannot |

A first-time or reset password must be changed before anything else works.

### Simulation speed and the 60-second history

- **Speed** (Digital twin: 1×, 10×, 60×) changes how fast the simulated plant and controllers run.
  A 300 s irrigation step takes 30 s of your time at 10×.
- **History** records one complete snapshot every **60 s of wall-clock time**, whatever the speed, and
  keeps 365 days. Events (alarms, commands, batches) are recorded at once.
- So at 60× one history snapshot covers about an hour of simulated time, and a trend of a fast run
  looks coarse. That is expected, not a recording fault. The speed never changes the recording cadence.
- **Pause** stops the simulated clock (history still records the paused state every 60 s). **Step**
  advances one tick while paused. **Run** continues.

### Starting conditions: presets and DEMO values

The Digital twin page has a **Scenario preset** list in two groups. Selecting a preset shows what it
does under the list.

- **Owner tour presets (simulation only)**, names starting with `tour_`. They set synthetic starting
  levels and start nothing: you press the buttons yourself.
- **Scripted test scenarios (they run by themselves)**. They press buttons on a timer and are used by
  the automated tests. The tour uses only `tour_ec_lost_at_valve_opening` of this kind.

**Load scenario (new run)** and **Reset run** start a new simulation run: levels and the plant return
to the start condition, every process is STOPPED, actuator faults and sensor overrides are cleared.
History, accounts, recipes and stock acknowledgements are kept, and the speed stays as set.

All quantities below are **DEMO / synthetic**. They exist so the simulation does something visible.
They are not production settings, they are not your illustrative refill quantities, and none of them
is an approved recipe.

| Where | DEMO value |
|---|---|
| Normal start (Reset run) | main 12,000 L, stock A 3,000 L, stock B 3,000 L, acid 2,000 L, sump 1,200 L, main pH 6.1, EC 1.6 mS/cm |
| `tour_low_main` | main 7,600 L (below the 8,000 L refill trigger) |
| `tour_ph_high` | main pH 6.8 (above the 6.0 target) |
| `tour_sump_high` | sump 2,700 L (above the 2,500 L return start) |
| `tour_main_full_sump_high` | main 18,800 L, sump 4,700 L (main above the 18,500 L hold level) |
| `tour_ec_lost_at_valve_opening` | main 7,600 L, all stocks acknowledged, refill AUTO at 1 s, EC input forced to raw 0 at 13.5 s |
| Recipe `demo-sim@v1` (DEMO_PRESET) | refill trigger 8,000 L, final level 15,000 L, EC target 1.8 ± 0.1 mS/cm, A:B 1:1, 20 L A+B per step, at most 150 L A and 150 L B per batch, stock reserve 300 L |
| | irrigation sector 1 300 s then sector 2 300 s; MANUAL at most 1,800 s |
| | acid pH target 6.0 ± 0.2, 2 L per dose, 600 s settling, at most 30 L per batch and 60 L per 24 h |
| | return start 2,500 L, stop 800 L (sump); hold above 18,500 L, resume below 18,000 L (main); pumps alternate at each start |
| Tour edits (UAT-05, UAT-06) | sector 1 120 s (`demo-sim@v2`); FIXED_VOLUMES recipe `tour-fixed@v1`: 1,000 L water, 15 L A, 15 L B, EC check 1.4 to 2.2 mS/cm. Both are rolled back at the end of their case |

### Returning to a known state

At any point:

1. **Overview**, *Stop all processes* (confirm).
2. **Digital twin**, *Reset run* (confirm). Set the speed to 1×.
3. **Recipes**: the active recipe should be `demo-sim@v1`. If it is not, press *Roll back to the
   previous version* (each press is recorded) until it is.
4. **Alarms & events**: acknowledge anything left open.

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
1. Sign in.
2. Read the banner and the status line.
3. Open **Settings** and find the **Versions** card.

Expected:
- Banner `SIMULATION — DEMO MODEL`.
- Status line starts `AquaCrop 0.3.2 · Profile SIMULATED · config installation-80@2026-10-13.80.1`.
- Versions: Inventory `6 modules: 16 AI + 32 DI + 32 DO = 80 points`; Release shows `0.3.2`, a source
  commit, a build time and **wheel verified**; Data folder is `%LOCALAPPDATA%\AquaCrop\demo` (shown
  in full).

Fail if: the banner says REAL, the version is not 0.3.2, the inventory is not 80 points, or Release
shows MISMATCH (or "not started from a release folder" although you used `AquaCrop-Demo.cmd`).

Return: nothing to undo.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-02 Find your way from the diagram

| | |
|---|---|
| Goal | The Overview diagram matches the installation and every part opens its page |
| Page | Overview, then each process page |
| Role | any |
| Start | normal start |
| Load | Digital twin, *Reset run* |
| Speed | 1× |
| Duration | 3 min |

Steps:
1. Open **Overview**.
2. Click, in turn: *Stock A*, *Main tank*, *Main pump MP1*, *Sector S1*, *Sector S2*, *Sump*. Use the
   browser's Back button between clicks.
3. On **Main tank**, read the level and pH lines.

Expected:
- The clicks open Nutrients & acid, Main tank, Main pumps, Sector 1, Sector 2 and Drainage.
- Every value shows value, unit, quality and age, for example `12000 L GOOD SIM 0.4 s`.
- The diagram shows 2 main pumps on one shared drive, 2 return pumps, 3 stock tanks with transfer
  pumps, sector 1 with channels 1 to 6 and sector 2 with channels 7 to 13. There is no agitator.
- The requested state (outline) is drawn apart from the observed feedback (fill).

Fail if: a part opens the wrong page or nothing, a value lacks unit, quality or age, or the layout
differs from the installation.

Return: nothing to undo.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-03 Irrigation AUTO alternates pump and sector

| | |
|---|---|
| Goal | AUTO runs sector 1 with MP1, changes over through the shared drive, then sector 2 with MP2 |
| Page | Main pumps, Sector 2 |
| Role | operator or admin |
| Start | normal start, recipe `demo-sim@v1` |
| Load | Digital twin, *Reset run*, then **10×** |
| Speed | 10× |
| Duration | 5 min |

Steps:
1. **Main pumps**, card **Irrigation**, press *AUTO*.
2. Watch the K1 (MP1) and valve S1 rows: each shows "requested" and "observed" separately.
3. After 300 simulated seconds (about 30 s at 10×) watch the phase: the drive stops and is verified
   stopped, the valve closes, then the pump selection changes.
4. Open **Sector 2**: sector 2 irrigates with MP2.
5. Back on **Main pumps**, press *STOP* on Irrigation.
6. On the Irrigation card open **Transition history**.

Expected:
- Step 1: sector 1 with MP1, phase `IRRIGATE`.
- Step 3: changeover with the drive stopped, then sector 2 with MP2.
- Step 5: the stop sequence runs to all outputs off; the cycle list shows `S1 MP1 300 s`.
- Step 6: each start reads `SELECT`, `OPEN`, `LEAD` (the valve is open before the pump starts),
  `START`, `VERIFY_FLOW`, `IRRIGATE`; each changeover reads `STOP_DRIVE`, `STOP_SETTLE`, `CLOSE`,
  `DESELECT` before the next `SELECT`.

Fail if: both pumps are selected at once, the drive runs during the pump changeover, a sector runs
with the other sector's pump, or STOP leaves an output on.

Pause/step: on Digital twin, *Pause* freezes the run during the changeover and *Step* advances one tick.

Return: Irrigation STOPPED; set 1×.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-04 MANUAL irrigation is bounded, and raw outputs are refused

| | |
|---|---|
| Goal | MANUAL runs one bounded step with the pump you choose; out-of-range requests and raw output commands are refused with the reason |
| Page | Main pumps; I/O test bench |
| Role | operator or admin |
| Start | normal start |
| Load | Digital twin, *Reset run*, then 10× |
| Speed | 10× |
| Duration | 4 min |

Steps:
1. **Main pumps**, Irrigation, press *MANUAL*.
2. In the form: Pump **MP2**, Sector **S1**, Duration **99999**. Press *Run one step*.
3. Change Duration to **60**. Press *Run one step*.
4. While the step runs, press *AUTO* on Irrigation.
5. Open **I/O test bench**, row `relay_A.DO3`, press *ON*.
6. Back on **Main pumps**, press *STOP* on Irrigation.

Expected:
- Step 2: refused: `duration must be 1..1800 s (recipe demo-sim@v1)`.
- Step 3: sector 1 irrigates with **MP2** (outputs `contactor.MP2.cmd`, `drive.MAIN.run`, `valve.S1.cmd`).
- Step 4: refused with the reason (MANUAL is active); the process stays in MANUAL.
- Step 5: `REJECTED (raw output commands are not available on the installation twin: outputs belong to the process controllers; …)`.
- Step 6: the stop sequence ends with all outputs off and the process STOPPED.

Fail if: the 99999 s request starts anything, MP1 runs instead of MP2, the raw ON switches the
output, or STOP leaves an output on.

Return: Irrigation STOPPED; set 1×.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-05 Recipe change: validate, activate, next cycle, roll back

| | |
|---|---|
| Goal | An invalid value is refused with the reason; a valid change applies from the next cycle only; rollback restores the previous version |
| Page | Recipes; Main pumps |
| Role | admin (operators edit and validate; administrators activate) |
| Start | active recipe `demo-sim@v1` |
| Load | Digital twin, *Reset run*, then 10× |
| Speed | 10× |
| Duration | 8 min |

Steps:
1. **Recipes**, row `demo-sim@v1`, press *Edit as next version*.
2. Main tank levels, *Refill trigger level*: type **25000**. Press *Save draft*.
3. Row `demo-sim@v2` (DRAFT), press *Validate*.
4. **Main pumps**, Irrigation, press *AUTO*. Wait until sector 1 irrigates.
5. **Recipes**, row `demo-sim@v2`, press *Edit draft*. Set *Refill trigger level* back to **8000**.
6. Open the **Irrigation** section of the editor. Under *Sequence*, row 1 (S1), set Duration **120**.
   Press *Save draft*.
7. Row `demo-sim@v2`: press *Validate*, then *Activate*, and give a reason.
8. Watch Main pumps until sector 1 runs again.
9. Press *STOP* on Irrigation. On Recipes press *Roll back to the previous version* and give a reason.

Expected:
- Step 2: the draft is saved (saving checks types only).
- Step 3: a **Validation issues** card: `main.refill_trigger_L: Refill trigger level must not exceed 20000 L`.
  There is no *Activate* button for that draft.
- Step 7: `demo-sim@v2` is ACTIVE; the step already running keeps `demo-sim@v1`.
- Step 8: the next cycle uses `demo-sim@v2` and sector 1 irrigates 120 s.
- Step 9: `demo-sim@v1` is ACTIVE again; the Activation log lists every change with user and reason.

Fail if: the invalid draft can be activated, the running step changes duration, the next cycle does
not use 120 s, or rollback does not restore `demo-sim@v1`.

Return: Irrigation STOPPED, `demo-sim@v1` active, set 1×.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-06 Main-tank refill: EC_TARGET and FIXED_VOLUMES

| | |
|---|---|
| Goal | A refill batch adds water and nutrients in measured steps up to the EC target and final level; a FIXED_VOLUMES recipe adds fixed amounts and validates EC |
| Page | Digital twin; Nutrients & acid; Main tank; Recipes |
| Role | admin |
| Start | `demo-sim@v1` active |
| Load | Digital twin, preset **`tour_low_main`**, *Load scenario (new run)* |
| Speed | 60× |
| Duration | 10 min |

Steps:
1. **Nutrients & acid**: press *Acknowledge prepared* for **Stock A** and **Stock B**, each with a note.
2. **Main tank**, card **Main-tank refill**, press *AUTO*. Set 60× on Digital twin.
3. Watch Nutrients & acid: water fill and staged A/B additions (phase `BATCH`).
   Meanwhile press *AUTO* on Irrigation (Main pumps), see it irrigate, then press its *STOP*.
4. Wait for the batch to end (about 1.5 min at 60×). Read the last batch on Main tank.
5. Press *STOP* on Main-tank refill.
6. **Recipes**, row `demo-sim@v1` (ACTIVE), press *Copy to new recipe*. Recipe id **tour-fixed**, name
   **Tour FIXED_VOLUMES (synthetic)**. Set *Refill endpoint* `FIXED_ADDITIONS`, clear *Final operating
   level*, *Mains water addition* **1000**, *Nutrient mode* `FIXED_VOLUMES`, *Stock A addition* **15**,
   *Stock B addition* **15**, *EC validation minimum* **1.4**, *maximum* **2.2**, *settling window* **600**.
   Press *Save draft*; on row `tour-fixed@v1` press *Validate*, then *Activate*.
7. Load **`tour_low_main`** again, press *AUTO* on Main-tank refill, set 60×, wait for the batch to end.
8. Press *STOP*, then on Recipes *Roll back to the previous version*.

Expected:
- Step 3: irrigation runs on its own permissives while the refill batch continues.
- Step 4: result `COMPLETED`, `EC_TARGET_REACHED` at about 1.74 mS/cm, end level about 15,000 L, A and B
  measured and equal (61.5 L each in the rehearsal).
- Step 7: result `COMPLETED`, `EC_VALID` at about 1.83 mS/cm, about 1,000 L water and 15 L each of A and
  B, measured.
- Step 8: `demo-sim@v1` active.

Fail if: a nutrient pump runs before the stocks are acknowledged, the level overshoots the final level
noticeably, A and B differ beyond the meter tolerance, or a batch is COMPLETED with EC out of band.

Return: refill STOPPED, `demo-sim@v1` active, set 1×.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-07 A water fill invalidates a prepared stock

| | |
|---|---|
| Goal | Adding water to a prepared stock tank marks it INVALIDATED, so it must be prepared again |
| Page | Nutrients & acid |
| Role | operator or admin |
| Start | normal start |
| Load | Digital twin, *Reset run* |
| Speed | 10× |
| Duration | 3 min |

Steps:
1. **Nutrients & acid**: *Acknowledge prepared* for **Stock A**.
2. Card **Stock A water fill**, press *MANUAL*. *Fill to level (L)* **3100**, press *Fill*.
3. Set 10× and wait until the fill ends.
4. Press *STOP* on Stock A water fill.

Expected:
- Step 1: Stock A `PREPARED`.
- Step 3: fill `COMPLETED`, `TARGET_LEVEL_REACHED (3100 L)`; Stock A `INVALIDATED` by the controller.

Fail if: the stock stays PREPARED after the water fill, or the fill overshoots.

Return: set 1×.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-08 Acid pH dosing

| | |
|---|---|
| Goal | Acid doses in steps with settling until pH is in band; an unusable pH probe stops dosing with the reason |
| Page | Nutrients & acid (Acid tab); I/O test bench |
| Role | admin |
| Start | `demo-sim@v1` active |
| Load | Digital twin, preset **`tour_ph_high`**, *Load scenario (new run)* |
| Speed | 60× |
| Duration | 6 min |

Steps:
1. **Nutrients & acid**: *Acknowledge prepared* for **Acid**. Open the **Acid** tab.
2. Read the recipe values shown: pH target 6 ± 0.2, dose step, settling window, limits.
3. Card **Acid pH dosing**, press *AUTO*. Set 60×.
4. Watch 2 L doses, each followed by `SETTLING`. Wait for the batch to end (about 1.5 min at 60×).
5. Set 1×. **I/O test bench**, row `analog_A.AI6` (pH): type **0**, press *Override*, give a reason.
6. Back on the Acid tab, read the process line. Then on I/O test bench press *Clear* on `analog_A.AI6`.
7. Press *STOP* on Acid pH dosing.

Expected:
- Step 4: `PH_IN_BAND` after about 8 doses, about 16.8 L in total, pH from 6.80 to about 6.20.
- Step 6: dosing inhibited: `pH unavailable (INVALID: raw 0.0 uA outside valid range)`.

Fail if: a dose is larger than the step, doses do not wait for settling, dosing continues with an
invalid pH, or base is dosed (there is no base).

Return: override cleared, acid STOPPED, set 1×.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-09 Drainage return: alternation and destination-full hold

| | |
|---|---|
| Goal | The return pumps alternate at each start, and the return holds while the main tank is full |
| Page | Drainage; Main pumps; Overview |
| Role | operator or admin |
| Start | `demo-sim@v1` active |
| Load | preset **`tour_sump_high`**, later preset **`tour_main_full_sump_high`** |
| Speed | 60×, then 1× |
| Duration | 6 min |

Steps:
1. Load **`tour_sump_high`**. **Drainage**, card **Drainage return**, press *AUTO*.
2. **Main pumps**, Irrigation, press *AUTO* (drainage from irrigation refills the sump). Set 60×.
3. Watch Drainage until two return runs are listed.
4. **Overview**, *Stop all processes*. Set 1×.
5. Load **`tour_main_full_sump_high`**. **Drainage**, press *AUTO* on Drainage return.

Expected:
- Step 3: the first run uses RP1 and the next RP2.
- Step 5: phase `HOLD` with alarm `SUMP_HIGH_MAIN_FULL`; no return pump runs.

Fail if: both return pumps run together, the same pump starts twice in a row, or water is returned
while the main tank is above the hold level.

Return: Drainage return STOPPED; acknowledge the alarm.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-10 Equipment fault and deliberate recovery

| | |
|---|---|
| Goal | A drive trip or a stuck valve locks irrigation out with the cause; acknowledging restarts nothing; recovery is deliberate |
| Page | Digital twin (Actuator faults); Main pumps; Alarms & events; Sector 1 |
| Role | operator or admin |
| Start | normal start |
| Load | Digital twin, *Reset run*; 10× until irrigating, then 1× |
| Speed | 10×, then 1× |
| Duration | 6 min |

Steps:
1. **Main pumps**, Irrigation *AUTO*. When sector 1 irrigates, set 1×.
2. **Digital twin**, Actuator faults, row *drive*: `MAIN`, `TRIP`, *Apply*.
3. **Main pumps**: read the lockout. Press *Reset lockout*.
4. Digital twin, row *drive*: `no fault`, *Apply*. **Alarms & events**: press *Ack* on each alarm.
5. Main pumps: read the state, press *Reset lockout*, then *AUTO*, then *STOP*.
6. Digital twin, *Reset run*. Row *valve*: `S1`, `STUCK_CLOSED`, *Apply*. Main pumps, Irrigation *AUTO*.
7. Open **Sector 1** and read the lockout. Clear the fault (`no fault`, *Apply*), acknowledge the
   alarms, press *Reset lockout*.

Expected:
- Step 3: lockout `DRIVE_FAULT: drive fault input active`; the reset is refused: `fault condition still
  present: drive fault input still active (reset the drive locally)`.
- Step 4: acknowledging leaves Irrigation in `FAULT_LOCKOUT`; nothing restarts.
- Step 5: Reset gives `STOPPED`, not running; AUTO starts only when you press it.
- Step 7: lockout `VALVE_OPEN_TIMEOUT: S1 open limit not seen in 5 s`; the drive never ran.

Fail if: acknowledging restarts anything, reset works while the fault is present, or the drive runs
while the valve is not open.

Return: faults cleared, alarms acknowledged, Irrigation STOPPED.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-11 Signal quality: invalid, unavailable, stale, and EC lost at valve opening

| | |
|---|---|
| Goal | Each kind of bad signal is shown as what it is and stops what depends on it; EC lost while a nutrient valve opens never lets a nutrient pump start |
| Page | I/O test bench; Main tank; Digital twin; Nutrients & acid; Alarms & events |
| Role | admin |
| Start | `demo-sim@v1` active |
| Load | *Reset run* for parts 1 to 3; preset **`tour_ec_lost_at_valve_opening`** for part 4 |
| Speed | 10×, then 1× |
| Duration | 8 min |

Steps:
1. **Invalid.** Irrigation *AUTO*; when it irrigates, set 1×. **I/O test bench**, row `analog_A.AI1`
   (main level): **0**, *Override*, give a reason. Open **Main tank**. Then *Clear* the override.
2. **Unavailable.** *Reset run*. I/O test bench, module card `analog_B`, *Simulate timeout*. Read the
   quality of the `analog_B` and `analog_A` points. Press *Restore*.
3. **Stale.** *Reset run*. Digital twin, *Stall acquisition*; wait 5 s and open Overview. Then Digital
   twin, *Resume acquisition*.
4. **EC lost at valve opening.** Set 1×. Load **`tour_ec_lost_at_valve_opening`** (it presses its own
   buttons). Wait 20 s. Open **Nutrients & acid**, then **Alarms & events** (events and command
   history). On I/O test bench press *Clear* on `analog_A.AI7` and wait 60 s.
5. **Overview**, *Stop all processes*.

Expected:
- Step 1: Irrigation `FAULT_LOCKOUT`, `OBSERVATION_UNAVAILABLE: required observations unusable: ['tank.MAIN.level (INVALID: raw 0.0 uA outside valid range)']`.
- Step 2: `analog_B` points `UNAVAILABLE`; `analog_A` points still `GOOD`.
- Step 3: the age of the newest data keeps growing while acquisition is stalled.
- Step 4: the nutrient step is `STOPPED (EC unavailable during step 1: INVALID: raw 0 uA outside valid
  range)`, alarm `REFILL_EC_UNAVAILABLE`, and no transfer pump A or B ON in the command history. After
  the probe returns, the stopped step does not resume; only the water fill continues.

Fail if: a bad value is shown as GOOD, one module's failure marks another module bad, or a transfer
pump A/B is commanded ON while EC is unusable.

Return: overrides cleared, all processes stopped, alarms acknowledged.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-12 I/O test bench: 80 points, relay and transistor outputs

| | |
|---|---|
| Goal | All six modules and 80 points can be exercised in IO_TEST mode; the 32 added channels stay unassigned in plant mode |
| Page | I/O test bench |
| Role | admin |
| Start | every process STOPPED |
| Load | *Reset run*, then *Stop all processes* |
| Speed | 1× |
| Duration | 5 min |

Steps:
1. **I/O test bench**, press *Use IO_TEST (raw injection)*.
2. Row `analog_A.AI1`: **12000**, *Inject*. Row `digital_A.DI1`: *Inject 1*.
3. Row `relay_A.DO1`: *ON*. Row `digital_B.DO1`: *ON*.
4. Read the module cards: relay contact outputs versus transistor (sink) outputs.
5. Module card `digital_A`: *Simulate timeout*; look at the other cards; *Restore*.
6. Switch `relay_A.DO1` and `digital_B.DO1` *OFF*. Press *Use TWIN (process model)*.
7. Read the `digital_A` and `digital_B` rows.

Expected:
- Steps 2 and 3: the raw values and states appear on the rows.
- Step 4: the two Relay (D) modules are labelled relay contacts; the two IO 8CH modules transistor
  sinks (500 mA max).
- Step 5: only `digital_A` goes offline; the other five stay online.
- Step 7: the added channels read **UNASSIGNED**, "no plant signal: unassigned test channel (not a
  fault)". No plant signal is bound to `digital_A` or `digital_B`.

Fail if: a module or point is missing, an added channel shows a plant signal, or a timeout on one
module affects another.

Return: test outputs OFF, source TWIN.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-13 Trends and CSV export

| | |
|---|---|
| Goal | Trends show recorded history with units and the site time zone; the export is complete and says so |
| Page | Trends |
| Role | any |
| Start | AquaCrop has run at least 3 minutes (history records every 60 s of wall time) |
| Load | none |
| Speed | any |
| Duration | 3 min |

Steps:
1. **Trends**. Under *Points*, select `analog_A.AI1` and `analog_A.AI2` (Ctrl+click).
2. Choose a *Range* (10 min, or 2 h after the earlier cases).
3. Click *Export CSV (complete, paginated)* and open the file.

Expected:
- The chart's time axis is in `Atlantic/Canary`, the legend gives units (`analog_A.AI1 [L] (SIM)`),
  gaps are shaded and never bridged, and run events are marked.
- The CSV's last line is `# COMPLETE: <n> rows` (or `# TRUNCATED …` for a very long range).

Fail if: the time axis is in another zone without saying so, lines bridge gaps, or the CSV has no
completeness line.

Return: nothing to undo.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

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
   characters, role **viewer**, *Create user*. Repeat for **tour-operator** with role **operator**.
2. Open a **private/incognito** window (so your admin session stays signed in) at
   <http://127.0.0.1:8000/> and sign in as `tour-viewer`.
3. Change the password when asked. Open **Main pumps** and hover over *AUTO*.
4. *Log out*, sign in as `tour-operator`, change the password, open **Main pumps** and **Recipes**.

Expected:
- The first sign-in of each new user asks for a new password before anything else.
- Viewer: control buttons are disabled, with "needs the process control permission".
- Operator: AUTO, MANUAL and STOP work; recipes can be edited and validated, but *Activate* and
  *Roll back* need an administrator; creating users is refused.

Fail if: a new user can work without changing the password, a viewer can command anything, or an
operator can activate a recipe or create accounts.

Return: keep the tour users or disable them under Settings; they are kept across restarts.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-15 Backup, stop and restart: nothing is lost, nothing restarts

| | |
|---|---|
| Goal | A local backup is made; after a stop and restart, accounts, recipes and history are kept and every process starts STOPPED |
| Page | Settings, Backups; the launcher window; Trends |
| Role | admin |
| Start | after the earlier cases |
| Load | none |
| Speed | any |
| Duration | 5 min |

Steps:
1. **Settings**, card **Backups**, press *Create backup now*. Wait until *Local copies* shows 1 or more.
2. Note the users (Settings) and the recipes (Recipes).
3. In the launcher window press **Ctrl+C** (or close the window). Wait for "AquaCrop stopped cleanly".
4. Double-click `AquaCrop-Demo.cmd` again. It does not ask for a password this time. Sign in.
5. Check Settings (users), Recipes, Overview and Trends.

Expected:
- Step 1: the backup is listed with its size; *Off-device* says NOT ACTIVATED (the SD card and OneDrive
  copies wait for the on-site setup).
- Step 4: no first-start questions; the launcher prints the same data folder.
- Step 5: the same users and recipes; every process STOPPED; nothing restarts by itself; the trend
  shows no data for the minutes AquaCrop was stopped.

Fail if: an account, recipe or history is missing, a process restarts without being asked, or the
launcher asks to create the administrator again.

Return: nothing to undo.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

---

### UAT-16 Narrow screen and lost connection

| | |
|---|---|
| Goal | The pages are usable on a narrow screen, and a lost connection is shown clearly with commands disabled |
| Page | Overview, Main pumps, Nutrients & acid, Alarms & events |
| Role | any |
| Start | AquaCrop running |
| Load | none |
| Speed | 1× |
| Duration | 4 min |

Steps:
1. Make the browser window narrow, or use the browser's device view (F12, then the phone icon). Open
   Overview, Main pumps, Nutrients & acid and Alarms & events.
2. Restore the window. Open **Main pumps**.
3. Stop AquaCrop in the launcher window (Ctrl+C) while the page stays open. Wait 10 s.
4. Press *AUTO* on Irrigation.
5. Start AquaCrop again and reload the page.

Expected:
- Step 1: everything readable; the page as a whole never scrolls sideways (a wide table may scroll
  inside its own box).
- Step 3: banner `CONNECTION LOST — values below are STALE (n s old). Commands disabled.`
- Step 4: nothing is sent.
- Step 5: normal display; all processes STOPPED.

Fail if: a page needs sideways scrolling as a whole, stale values look normal, or a command is sent
while the connection is lost.

Note: a narrow browser window is not a test of a phone over your OpenVPN. That is a separate check
at commissioning.

Return: nothing to undo.

Your result: ☐ PASS ☐ FAIL ☐ NOT RUN — Comments: ______ — Screenshot: ______

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
- Overview, Nutrients & acid, Main tank, Main pumps, Sector 1, Sector 2 and Drainage show the diagram
  and the real observations, labelled `REAL`.
- Signals without an approved physical mapping read "not measured".
- Instead of AUTO, MANUAL or STOP, each page says why control is not offered. Recipes and the
  Digital twin are not shown.

Overview also shows per-device health and the relay channel modes read from each module.
The I/O view shows live values with origin `REAL`. Channels whose configured mode is UNKNOWN, or
whose module reports a different mode than configured, show `UNSCALED` with the raw value only.

Polling stops, restarts, reconnects and shutdowns never write to a module. Relays keep whatever
state they had; the Pi cannot de-energise anything when it loses power or communication.

## Bench lease (isolated bench only)

Only with every production load disconnected and an owner-authorised setup (see
`docs/commissioning/`). Steps:

1. Sign in as a user with the commissioning flag. Open **I/O test bench**.
2. Tick the exact relay outputs to allow, type the confirmation phrase exactly
   (`PRODUCTION LOADS DISCONNECTED`), describe the setup, choose a duration (1–30 min), press
   *Arm lease*. The server re-reads all relay modes; anything other than eight NORMAL modes refuses
   the lease.
3. ON/OFF buttons appear only on allowlisted outputs. Each command re-reads the modes, sends one
   FC05 frame, then reads the coils back. The toast shows the outcome:
   `READBACK_CONFIRMED` (coil state matches; not proof the load moved), `FAILED` (module refused,
   or readback differs) or `OUTCOME_UNKNOWN` (no valid answer; the relay may or may not have
   switched; check with a fresh read and your own eyes, never by resending).
4. End the lease with *End lease now*, or let it expire. Logging out, password change, revoking the
   user's sessions, disabling the user or removing the flag also end it. **Ending a lease sends
   nothing**: relays stay as they are. Use the teardown checklist in the commissioning procedure.

A restart never restores a lease, and no configuration file or environment variable can arm one.
