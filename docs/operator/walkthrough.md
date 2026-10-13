# Operator walkthrough

The banner at the top always says what you are connected to:

| Banner | Meaning |
|---|---|
| `SIMULATION — DEMO MODEL` (blue) | Everything is simulated. No serial port is open. Nothing physical can change |
| `REAL — READ ONLY` (dark) | Real modules are polled. The software sends no write frames |
| `REAL — BENCH WRITES ARMED (user, until …)` (red) | A commissioning lease allows the listed relay outputs on an isolated bench |
| grey "Operating mode is shown after sign-in" | Not signed in yet |

If the server stops answering, a **STALE** banner shows the age of the last good data, values are
marked stale and command buttons stop sending. Stale data is never shown as a green "normal".

## Roles

| Role | Can |
|---|---|
| viewer | read everything except operator audit exports |
| operator | command the **simulation**, inject/override virtual signals, acknowledge alarms, reset lockouts |
| admin | manage users, export audit, backups |
| commissioning (separate flag) | arm a bench lease on a real read-only installation. An admin without the flag cannot |

A first-time or reset password must be changed before anything else works.

## The installation demo (installation-48): owner walkthrough

Start it with `AquaCrop-Demo.cmd` (Windows) or `python -m aquacrop launch` (any PC). The first start
asks for the administrator password. Everything below is the SIMULATED twin of the confirmed
installation: 20,000 L main tank, stock tanks A, B and acid, sump, two main pumps on one drive, two
return pumps, three transfer pumps, sectors 1 and 2. The same steps are run automatically in a
headless browser by `scripts/ui_check/run_ui_check.py` (CI job `ui`).

1. **Sign in and look around.** The Overview is a diagram of the installation.
   - Each part shows what the controllers read: value, unit, quality and age. Requested state is shown
     apart from observed feedback.
   - Click a part, or Tab to it and press Enter, to open its page: Nutrients & acid, Main tank,
     Main pumps, Sector 1, Sector 2, Drainage. Each page has its processes, its alarms and a last-hour
     trend.
   - The pH/EC probe point is drawn as proposed. There is no agitator or air pump.
2. **Edit a recipe.** Recipes, *Edit as next version*.
   - Change the sector 1 duration, *Save draft*, *Validate*, then *Activate* (administrator).
   - Running steps keep their version; the next cycle uses the new one. *Roll back to the previous
     version* undoes an activation.
3. **Prepare the stocks.** Nutrients & acid: *Acknowledge prepared* for stock A, stock B and acid. This
   records that you prepared them; filling a stock with water invalidates it.
4. **Run everything together.** On the Overview, press AUTO for Irrigation, Drainage return, Main-tank
   refill and Acid pH dosing. Set 10x on the Digital twin page to see it faster.
   - Irrigation alternates pump 1 / sector 1 and pump 2 / sector 2, with the drive stopped and verified
     between them.
   - The refill starts at the trigger level while irrigation and the return continue.
   - Acid doses in steps towards pH 6, waiting for mixing after each addition. The chemistry is a
     labelled synthetic model, not a calibrated one.
5. **Inject a fault.** Digital twin, actuator faults: drive TRIP while a sector is irrigating.
   - Irrigation locks out with a CRITICAL alarm. The other processes keep running.
   - Main pumps lists the alarm with *Ack* and the next step.
6. **Recover deliberately.**
   1. Clear the fault.
   2. Acknowledge the alarm. Reset is refused until you do.
   3. *Reset lockout*. The process stays STOPPED.
   4. Press AUTO again.
7. **History.** Trends: pick points and a range, then *Export CSV*. The file ends with a COMPLETE or
   TRUNCATED marker. Snapshots are written every 60 s of wall time.
8. **Backups and clock.** Settings shows:
   - storage use and the 365-day projection;
   - backups (create one with *Back up now*);
   - the clock and timestamp state. If a quarantine exists, it is listed with its review status.
9. **Restart.** Stop the launcher (Ctrl+C), start it again and sign in.
   - Accounts, recipes, stock acknowledgements and history are kept.
   - Every process starts STOPPED, and nothing restarts by itself.

## Simulation: a five-minute tour (demo-48)

1. **Overview.** System status, simulated water (tanks, sector flow, pumps, ledger residual),
   active alarms and per-device health.
2. **Digital twin.** The schematic shows requested versus actual pump/valve state, flows, tank
   levels and channel watering. Use Run/Pause/Step/Reset and 1×/10×/60×. Load a scenario preset to
   start a new run (for example `pump_no_flow`). Actuator faults change the plant; sensor overrides
   change only what the controller sees.
3. **Control.** Choose MANUAL to switch virtual pumps and valves, or AUTO (simulation) to run the
   two-sector sequence. The permissives table shows why a start is allowed or refused. STOP runs the
   stop sequence. After a fault: fix the condition, acknowledge the alarm, press *Reset lockout*,
   then request AUTO again.
4. **I/O test bench.** Every point of every module with its zero-based address, raw value, scaled
   value, quality and origin. In `IO_TEST` mode you inject raw values and switch DO points directly
   (process control is inactive). In `TWIN` mode inputs come from the model and you can override
   them with a recorded reason. *Simulate timeout* fails one virtual module; only its points go
   UNAVAILABLE.
5. **Trends.** Pick up to eight points and a time range. The X axis is time; gaps in data are left
   as gaps. Exports are complete CSV files with a TRUNCATED/COMPLETE marker.
6. **Alarms & events.** Open occurrences and history. Acknowledging never clears a condition and
   never resets a lockout. Rejected and uncertain commands are listed with their reason.
7. **Settings.** Inventory, scaling, thresholds, mappings (all labelled DEMO/SIMULATED), versions,
   storage and user management.

Headless check of the same behaviour: `python -m aquacrop demo verify --config demo-48 --seed 42`.

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
