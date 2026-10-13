# Cause and effect: installation process controllers (simulation)

Source of truth: `src/aquacrop/control/processes.py`, `supervisor.py`, `process_base.py`. Every
threshold named below is a **recipe field** (DEMO preset values in simulation); production values are
TBD. "Lockout" means FAULT_LOCKOUT: the stop sequence runs, a latched CRITICAL alarm
`PROC_<reason>` is raised on `process.<PROCESS>`, and recovery is acknowledge, then reset (condition
cleared, outputs verified off), then a deliberate AUTO or MANUAL request.

## Installation-wide

| Cause | Effect |
|---|---|
| Safety chain reported open (`safety.chain_ok` false) | Every process in AUTO or MANUAL locks out (`SAFETY_CHAIN_OPEN`); the twin removes power from all motors. Closing the chain restarts nothing; reset requires the chain reported closed. |
| Safety chain feedback unbound or unusable | No process may start or enter AUTO/MANUAL. |
| Storage cannot accept writes (historian degraded, disk below reserve) | Every ON command is refused; no new start, step, batch or dose begins. OFF commands still go through. |
| Signal source is the I/O test source | Processes cannot start; I/O test DO commands need every process STOPPED and idle. |
| Raw operator DO command on the twin | Refused: outputs belong to the process controllers. |
| A process commands equipment owned by another process | Refused and logged (`PROCESS_COMMAND_REFUSED`). |
| Recipe activated | Applies from each process's next cycle, batch or dose; running operations keep their version. |

## Start interlocks (AUTO request, every start in AUTO, or a MANUAL operation)

A process in AUTO checks these again before each step, batch, run, dose or fill it starts, not only when AUTO
is requested; while one is active it waits with the reason shown.

| Condition | Blocks |
|---|---|
| `drive.MAIN.fault` active | IRRIGATION |
| `tank.MAIN.high_high` active | REFILL, RETURN, ACID |
| `tank.MAIN.low_low` active | IRRIGATION, ACID |
| `tank.SUMP.low` active | RETURN |
| The process's own fault alarms active or awaiting reset | that process |
| No active recipe | every process |

## IRRIGATION

| Cause | Effect |
|---|---|
| AUTO request with drive running, a pump selected, a sector valve open, a required observation unusable, or main level below the dry-run guard | Request refused with the reason. |
| Between steps (PRECHECK): storage inhibit, safety chain, main level below the dry-run guard | Waits; the inhibit reason is shown; resumes when it clears. |
| Between steps: drive running, pump selected or valve open unexpectedly | Lockout `PRECHECK_FAILED`. |
| Selection feedback (K aux) not seen in `contactor_timeout_s` | Lockout `SELECTION_TIMEOUT`. |
| Open limit not seen in `valve_open_timeout_s` | Lockout `VALVE_OPEN_TIMEOUT`; the drive never starts. |
| Open limit lost before the pump starts | Lockout `VALVE_LOST_OPEN`. |
| During the valve lead (LEAD): main low-low switch or level below the dry-run guard | Lockout `MAIN_LOW_LOW` in that scan; the drive is never started. |
| End of the valve lead, in the scan that would request RUN: the start permissives checked again (storage inhibit, safety chain, start interlocks) | Any one lost: lockout `START_INHIBITED`; RUN is not requested. Recovery only by acknowledge, reset and a deliberate restart. |
| No running feedback in `drive_start_timeout_s` | Lockout `DRIVE_START_TIMEOUT`. |
| Running feedback lost while pumping | Lockout `DRIVE_STOPPED`. |
| Flow below `min_flow_L_min` for `no_flow_sustain_s` after `flow_grace_s` | Lockout `NO_FLOW`. |
| Drive fault input | Lockout `DRIVE_FAULT`. |
| Drive run over Modbus not completed (exception, no echo, wrong echo; ADR-0015) | Lockout `COMMAND_REJECTED`; nothing is resent. An echo is never taken as running: the running input decides. A stop that is not completed is caught by the running input (`STOP_NOT_VERIFIED`). |
| Drive's Modbus link silent while it runs | The drive stops itself after its comm-loss timeout (drive setting) and its fault input locks the process out (`DRIVE_FAULT`); `DRIVE_COMM` alarm after three unreadable status reads. |
| Drive's status word disagrees with the hardwired running or fault input for 10 s | `DRIVE_STATUS_MISMATCH` alarm (warning); the hardwired input keeps deciding. |
| Both K1 and K2 report closed, or the other pump than the owned one | Lockout `BOTH_PUMPS_SELECTED` / `UNEXPECTED_PUMP_SELECTED`. |
| Drive running while no pumping phase is active | Lockout `UNCOMMANDED_DRIVE_RUN`. |
| Main low-low switch or level below the dry-run guard while pumping (or during the lead, above) | Lockout `MAIN_LOW_LOW`. |
| Drive still running `drive_stop_timeout_s` after the stop command | Lockout `STOP_NOT_VERIFIED`; the sector valve and the pump selection are **held** (no switching under a possibly running motor) until stop is verified on site. |
| Valve not verified closed in `valve_close_timeout_s` | Lockout `VALVE_CLOSE_TIMEOUT`. |
| Selection still reported after release | Lockout `DESELECT_TIMEOUT`. |
| Any required observation unusable while active (bad quality, stale, module timeout) | Lockout `OBSERVATION_UNAVAILABLE`. |

Stop sequence (stop request or lockout): drive stop, verify stopped, settle `stop_settle_s`, close
sector valves, verify, deselect pumps, verify.

## REFILL

| Cause | Effect |
|---|---|
| Main level at or below `refill_trigger_L` for `trigger_debounce_s` (armed) | One batch latches; re-arms only above trigger + `rearm_hysteresis_L`. |
| A deliberate AUTO request (from STOPPED) | The latch is armed again: an interrupted batch can be repeated on purpose. |
| Main level unavailable | No batch can latch (inhibit shown). |
| Water path: high-high switch, main level unavailable | Water path fails. |
| Water path: final level (`FINAL_LEVEL`) or fixed volume (`FIXED_ADDITIONS`) reached, or `fill_stop_L` | Water path ends normally. |
| MANUAL A or B transfer above the requested stock's quantity in the active nutrient mode (`fixed.a_L`/`fixed.b_L` in FIXED_VOLUMES, `ec.max_a_L`/`ec.max_b_L` in EC_TARGET) | Refused with the limit. |
| Nutrient path: stock at `stock_reserve_L`, stock not acknowledged as prepared | That path (and in EC_TARGET its partner, so the ratio is kept) does not start or stops; alarm `REFILL_PATH_NOT_STARTED`; batch `INCOMPLETE`. Water continues. |
| Any path: valve not open, pump not running, flow below minimum after grace, meter unusable, maximum time | Path fails (`PROC_PATH_FAULT` on `process.REFILL.<path>`); in EC_TARGET the partner stops; water continues; at batch end lockout `PATH_FAILED`. |
| FIXED_VOLUMES: EC outside the band after the settling window | Alarm `REFILL_EC_VALIDATION_FAILED`, batch `OUT_OF_SPEC`; no automatic correction. |
| EC_TARGET: EC above target + tolerance after water ends | Alarm `REFILL_EC_ABOVE_TARGET` (nutrients are never removed). |
| EC_TARGET: batch maxima reached below target (on measured delivery) | Alarm `REFILL_EC_LIMIT_REACHED`. |
| EC_TARGET: each step | `max_a_L`/`max_b_L` apply to the **measured** delivery: the flow meter integrated from valve open until the stop is verified, start-up and stopping coast included. The next step is shortened by the largest coast (measured minus commanded) seen in this batch for that stock (the stopping margin). Commanded and measured quantities are both recorded per step (`REFILL_EC_STEP_DELIVERED`). |
| EC_TARGET: measured total above a maximum (tolerance 0 L; possible on the first step, before any coast has been measured) | Dosing stops (`MAX_EXCEEDED`, measured totals shown), alarm `REFILL_EC_LIMIT_EXCEEDED`, batch `OUT_OF_SPEC`. |
| EC_TARGET: EC unusable (invalid, stale, module timeout) while an A/B step is opening, starting or running | Checked before the paths advance, so no transfer pump ON is sent in that scan: both nutrient paths are stopped (`EC_UNAVAILABLE`), the step's measured delivery is recorded, event `REFILL_EC_STEP_STOPPED`, alarm `REFILL_EC_UNAVAILABLE`, batch nutrients `STOPPED` (batch `INCOMPLETE`, or `FAILED` if a path also failed). No further nutrient step in this batch, even when the probe returns. Water, irrigation and return follow their own permissives. |
| EC_TARGET: EC unusable between steps | Nutrient dosing stops; alarm `REFILL_EC_UNAVAILABLE`. |
| FIXED_VOLUMES | Unchanged and distinct: the fixed A/B volumes are not EC-controlled; EC is only validated after the settling window. |

## ACID (lowering pH only; no base dosing exists)

| Cause | Effect |
|---|---|
| pH at or below target + tolerance | No dose; an open batch ends `PH_IN_BAND`. |
| pH below target − undershoot margin | Lockout `PH_UNDERSHOOT`; operator action (for example dilution by a refill), then reset. |
| pH, main level or acid level unavailable; main below `min_dosing_L` or at `high_inhibit_L`; acid stock not acknowledged or at reserve; any addition within `settle_s` or commanded in the same scan (a refill or transfer starting); 24 h limit reached | Waits with the reason shown. |
| Settled dose: pH rose by more than the tolerance | Lockout `PH_WRONG_RESPONSE`. |
| `max_no_response` consecutive settled doses below `min_response_ph` | Lockout `PH_NO_RESPONSE`. |
| Batch maximum dosed with pH still high | Lockout `ACID_BATCH_LIMIT`. |
| Dose path fails (flow, run feedback, pH or acid stock level lost during the dose, time) | Lockout `DOSE_FAILED`. |
| Acid stock reaches `stock_reserve_L` during a dose | The dose ends; the next one waits (stock at reserve). |

## RETURN

| Cause | Effect |
|---|---|
| Sump at `sump_start_L` (or high switch) and the main tank below `main_inhibit_L` | Start the pump chosen by `duty_policy` (no implicit rotation or failover). |
| Main tank at `main_inhibit_L` | Hold; resumes below `main_resume_L`. If the sump high switch is also active: CRITICAL alarm `SUMP_HIGH_MAIN_FULL` (the main tank is never filled above the inhibit to hide it). |
| Main high-high switch active while running (even with the analog level below `main_inhibit_L`) | Stop, verify, hold; no new run while the switch is active. |
| Sump at `sump_stop_L` | Stop, verify, record the run. |
| Sump low switch while running | Lockout `SUMP_DRY_RUN`. |
| No running feedback, feedback lost, no flow, `max_runtime_s` | Lockout `RUN_FEEDBACK_TIMEOUT` / `PUMP_STOPPED` / `NO_FLOW` / `MAX_RUNTIME`. |

## STOCK_FILL (A, B, ACID)

| Cause | Effect |
|---|---|
| Fill starts | The stock's preparation acknowledgement is invalidated (water never asserts readiness). |
| Level unavailable | Fill fails. |
| Level at target or `high_inhibit_L` | Fill ends. |
| Level rose less than `min_rise_L` in `rise_window_s`, or `max_time_s` | Lockout `FILL_FAILED`. |
| AUTO: level at `auto_trigger_L` | Fill to `target_L`. |
