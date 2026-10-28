/* AquaCrop display wording (F-03, F-05, F-08): the plain names an operator reads for states, readings,
 * equipment, processes and problems, with the owner-confirmed vocabulary of 14 Oct 2026. Display text only:
 * the identifiers (process, alarm, signal, point, recipe field) stay what they are, and the pages show them
 * under Details. Loaded before app.js; everything here is plain data and string functions. */
"use strict";
(function () {
  const TANK = { MAIN: "Main tank", A: "Fertiliser tank A", B: "Fertiliser tank B", ACID: "Acid tank", SUMP: "Drainage tank" };
  const lower = s => s.charAt(0).toLowerCase() + s.slice(1);
  const sectorName = id => /^S\d+$/.test(id) ? "Sector " + id.slice(1) : id;

  // ------------------------------------------------------------------ processes
  // start: the AUTO button. `starts` says whether AUTO begins work at once (watering) or only switches the
  // process to wait for its own condition (then the button names the mode, and `auto` says what it waits for).
  const PROC = {
    IRRIGATION: { name: "Watering", page: "#main-pumps", start: "Start automatic watering", starts: true, stop: "Stop watering",
      auto: "Waters the sectors in turn, for the times set in the recipe, until you stop it." },
    REFILL: { name: "Main tank refill", page: "#main-tank", start: "Switch on automatic refill", stop: "Stop refill",
      auto: "Refills the main tank by itself when its level falls to the start level of the recipe." },
    ACID: { name: "Acid dosing", page: "#nutrients", tab: "ACID", start: "Switch on automatic acid dosing", stop: "Stop acid dosing",
      auto: "Adds acid in small doses when the water is less acidic than the target (pH above it). Acid can only lower pH." },
    RETURN: { name: "Drainage return", page: "#drainage", start: "Switch on automatic return", stop: "Stop return pump",
      auto: "Pumps water from the drainage tank back to the main tank when the drainage tank reaches its start level." },
    STOCK_FILL_A: { name: "Water fill of fertiliser tank A", page: "#nutrients", tab: "A", start: "Switch on automatic filling", stop: "Stop filling tank A",
      auto: "Adds water to fertiliser tank A when it falls to its fill level. Adding water does not prepare the fertiliser." },
    STOCK_FILL_B: { name: "Water fill of fertiliser tank B", page: "#nutrients", tab: "B", start: "Switch on automatic filling", stop: "Stop filling tank B",
      auto: "Adds water to fertiliser tank B when it falls to its fill level. Adding water does not prepare the fertiliser." },
    STOCK_FILL_ACID: { name: "Water fill of the acid tank", page: "#nutrients", tab: "ACID", start: "Switch on automatic filling", stop: "Stop filling the acid tank",
      auto: "Adds water to the acid tank when it falls to its fill level." },
  };
  function proc(pid) { return PROC[pid] || { name: pid, page: "#overview", start: "Automatic mode", stop: "Stop", auto: "" }; }
  const STATE = { AUTO: "Automatic", MANUAL: "Manual mode", STOPPED: "Stopped", FAULT_LOCKOUT: "Stopped because of a problem" };
  function state(s) { return STATE[s] || s; }
  const PHASE = {
    IDLE: "", PRECHECK: "checking before starting", SELECT: "selecting the pump", OPEN: "opening the valve",
    LEAD: "valve open, the pump starts next", START: "starting the pump", STARTING: "starting", VERIFY_FLOW: "checking the water flow",
    IRRIGATE: "watering", RUNNING: "running", STOPPING: "stopping safely", STOPPING_RUN: "stopping the pump", STOP_DRIVE: "stopping the pump drive",
    CLOSE: "closing the valve", DESELECT: "releasing the pump", STOP_SETTLE: "pausing after the stop", BATCH: "refilling",
    MONITOR: "watching the level", HOLD: "on hold", SETTLING: "waiting for the water to mix", DOSING: "adding acid", FILLING: "filling",
  };
  function phase(p) { return p in PHASE ? PHASE[p] : lower(String(p || "").replace(/_/g, " ")); }

  // ------------------------------------------------------------------ equipment and signals
  const EQUIP = {
    MP1: "main pump 1", MP2: "main pump 2", RP1: "return pump 1", RP2: "return pump 2",
    TP_A: "transfer pump of fertiliser tank A", TP_B: "transfer pump of fertiliser tank B", TP_ACID: "transfer pump of the acid tank",
    FILL_MAIN: "water inlet valve of the main tank", FILL_A: "water inlet valve of fertiliser tank A", FILL_B: "water inlet valve of fertiliser tank B",
    FILL_ACID: "water inlet valve of the acid tank", OUT_A: "outlet valve of fertiliser tank A", OUT_B: "outlet valve of fertiliser tank B",
    OUT_ACID: "outlet valve of the acid tank", MAIN: "pump drive", K1: "pump 1 selector (K1)", K2: "pump 2 selector (K2)",
  };
  function equip(id) {
    if (EQUIP[id]) return EQUIP[id];
    if (/^S\d+$/.test(id)) return `valve of ${sectorName(id)}`;
    return id;
  }
  /** A signal id (tank.MAIN.level, flow.S1, …) as an operator says it. */
  function signal(id) {
    const p = String(id).split("."), [a, b, c] = p;
    const tank = TANK[b] || b;
    if (a === "tank") return { level: `${tank} water level`, high_high: `${tank} very high level switch`, low_low: `${tank} very low level switch`,
      high: `${tank} high level switch`, low: `${tank} low level switch` }[c] || id;
    if (a === "flow") return b === "FILL_MAIN" ? "Mains water flow into the main tank" : b === "RETURN" ? "Drainage return flow"
      : /^S\d+$/.test(b) ? `${sectorName(b)} water flow` : b.startsWith("TP_") ? `${b === "TP_ACID" ? "Acid" : "Fertiliser " + b.slice(3)} transfer flow` : id;
    if (a === "pressure") return `${sectorName(b)} water pressure`;
    if (a === "main") return { ph: "Acidity (pH) in the main tank", ec: "Nutrient strength (EC) in the main tank", temperature: "Main tank water temperature" }[b] || id;
    if (a === "pump") { const n = equip(b); return c === "running" ? `${cap(n)} running signal` : `${cap(n)} switched on (requested)`; }
    if (a === "contactor") return c === "closed" ? `${cap(equip(b))} selected (signal)` : `${cap(equip(b))} selected (requested)`;
    if (a === "drive") return { run: "Pump drive run command", running: "Pump drive running signal", fault: "Pump drive fault signal" }[c] || id;
    if (a === "valve") {
      const n = /^S\d+$/.test(b) ? `${sectorName(b)} valve` : cap(equip(b));
      return { cmd: `${n} opened (requested)`, open_limit: `${n} open signal`, closed_limit: `${n} closed signal` }[c] || id;
    }
    if (a === "safety") return "Safety circuit healthy";
    return id;
  }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  /** Groups for the History page, in reading order. */
  function signalGroup(id) {
    const a = String(id).split(".")[0], c = String(id).split(".")[2];
    if (a === "tank" && c === "level") return "Tank levels";
    if (a === "flow" || a === "pressure") return "Water flow and pressure";
    if (a === "main") return "Acidity and nutrient strength";
    if (a === "tank" || a === "safety") return "Level switches and safety";
    return "Pumps and valves";
  }
  const GROUP_ORDER = ["Tank levels", "Water flow and pressure", "Acidity and nutrient strength", "Pumps and valves", "Level switches and safety"];

  // ------------------------------------------------------------------ reading quality
  const QUALITY = { GOOD: "Reading valid", STALE: "Reading is old", UNAVAILABLE: "No reading", INVALID: "Reading outside the allowed range",
    UNASSIGNED: "Unused channel", OVERRIDDEN: "Set by hand (test)", UNSCALED: "Not calibrated", PARTIAL: "Some readings missing" };
  const QUALITY_HELP = {
    GOOD: "The reading arrived correctly and in time. This is not a promise that the plant value is normal.",
    STALE: "No new reading arrived recently; the value shown is the last one received.",
    UNAVAILABLE: "There is no reading for this value at the moment.",
    INVALID: "The sensor sent a value outside its allowed range, so it is not used.",
    UNASSIGNED: "Nothing is connected to this channel. This is not a fault.",
    OVERRIDDEN: "Someone set this value by hand on the I/O test bench; it is not the sensor's reading.",
    UNSCALED: "The channel has no approved calibration, so the value has no unit yet.",
  };
  function quality(q) { return QUALITY[q] || q; }

  const HELP = {
    twin: "The Digital Twin is a computer model of the greenhouse. Everything shown and done in this program happens in the model; no real pump or valve moves.",
    bench: "The I/O test bench shows every input and output channel of the I/O modules. In the Digital Twin the channels are simulated; it is used to test wiring and signals.",
    ph: "Acidity (pH) says how acidic the water is. Lower is more acidic. Plants usually want about 6; acid lowers it.",
    ec: "Nutrient strength (EC) is the water's electrical conductivity, in mS/cm. Dissolved salts raise it, fertiliser among them, so it rises as fertiliser is added. It is not a direct measure of how much fertiliser is in the water: salts already in the water count too.",
    stop: "This stops the process in the program and switches its equipment off in a safe order. It is not an emergency stop: use the emergency stop on the equipment for that.",
  };

  // ------------------------------------------------------------------ problems
  // reason (the PROC_<reason> alarm) -> [what happened, what to check]. `who` is the equipment named in the
  // detail text when there is one (a sector, a pump).
  function whoIn(detail) {
    const d = String(detail || "");
    const m = d.match(/\b(S\d+)\b/) || d.match(/\b(MP\d|RP\d|TP_[A-Z]+)\b/);
    return m ? m[1] : null;
  }
  const FAULT = {
    VALVE_OPEN_TIMEOUT: w => [`the valve for ${sectorName(w || "the sector")} did not open`, `Check the valve of ${sectorName(w || "the sector")} and its open signal.`],
    VALVE_LOST_OPEN: w => [`the valve for ${sectorName(w || "the sector")} closed before the pump started`, `Check the valve of ${sectorName(w || "the sector")} and its open signal.`],
    VALVE_CLOSE_TIMEOUT: w => [`the valve for ${sectorName(w || "the sector")} did not confirm that it closed`, `Check the valve of ${sectorName(w || "the sector")}.`],
    NO_FLOW: w => [w && /^S\d+$/.test(w) ? `no water flow was measured in ${sectorName(w)}` : "no water flow was measured", "Check the pump, the valves and the flow meter."],
    DRIVE_FAULT: () => ["the pump drive reports a fault", "Find and fix the cause at the pump drive, then reset the drive there."],
    DRIVE_START_TIMEOUT: () => ["the main pump did not start", "Check the pump drive and the pump."],
    DRIVE_STOPPED: () => ["the main pump stopped unexpectedly", "Check the pump drive and the pump."],
    STOP_NOT_VERIFIED: () => ["the equipment did not confirm that it stopped", "Check that the pumps have stopped and the valves are closed."],
    SAFETY_CHAIN_OPEN: () => ["the safety circuit is open", "Find out why the safety circuit opened (for example an emergency stop) and close it."],
    SELECTION_TIMEOUT: w => [`${equip(w || "the main pump")} could not be selected`, "Check the pump selector."],
    DESELECT_TIMEOUT: w => [`${equip(w || "the main pump")} stayed selected after it was released`, "Check the pump selector."],
    BOTH_PUMPS_SELECTED: () => ["both main pumps are selected at once", "Check the pump selectors."],
    UNEXPECTED_PUMP_SELECTED: () => ["the wrong main pump is selected", "Check the pump selectors."],
    UNCOMMANDED_DRIVE_RUN: () => ["the pump drive runs although it was not asked to", "Check the pump drive."],
    MAIN_LOW_LOW: () => ["the main tank is too low for watering", "Fill the main tank above the lowest level for watering."],
    OBSERVATION_UNAVAILABLE: () => ["a reading this process needs is missing", "Check the sensor and its connection; see the readings on this page."],
    COMMAND_REJECTED: () => ["a command to the equipment was refused", "Look at the latest commands under Alarms & events."],
    START_INHIBITED: () => ["the pump could not be started", "See the checks before starting on this page."],
    PRECHECK_FAILED: () => ["a check before starting failed", "See the checks before starting on this page."],
    NO_RECIPE: () => ["there are no settings (recipe) in use", "Use a recipe on the Recipes page."],
    PATH_FAILED: () => ["a step of the refill failed", "Look at the refill batch on the Main tank page."],
    PH_UNDERSHOOT: () => ["the acidity (pH) fell too low", "Wait until the pH is back above the stop margin; there is no base dosing."],
    PH_WRONG_RESPONSE: () => ["the pH rose after adding acid", "Check the acid tank and the pH probe."],
    PH_NO_RESPONSE: () => ["the pH did not drop after adding acid", "Check the acid tank, its transfer pump and the pH probe."],
    ACID_BATCH_LIMIT: () => ["the most acid allowed in one batch was reached", "Check the acid settings and the pH probe."],
    DOSE_FAILED: () => ["an acid dose failed", "Check the acid tank and its transfer pump."],
    SUMP_DRY_RUN: () => ["the drainage tank ran dry while the return pump was running", "Check the drainage tank level and its low switch."],
    RUN_FEEDBACK_TIMEOUT: w => [`${equip(w || "the return pump")} did not start`, "Check the return pump."],
    PUMP_STOPPED: w => [`${equip(w || "the return pump")} stopped unexpectedly`, "Check the return pump."],
    MAX_RUNTIME: w => [`${equip(w || "the return pump")} ran too long without emptying the drainage tank`, "Check the return pump and the drainage tank level."],
    FILL_FAILED: () => ["filling the tank with water failed", "Check the water inlet valve and the level reading."],
  };
  // other alarms: rule id -> [what is happening, what to check]
  const RULE = {
    MAIN_LEVEL_LOW: ["The main tank level is low", "Refill the main tank."], MAIN_LEVEL_HIGH: ["The main tank level is high", "Stop adding water to the main tank."],
    SUMP_LEVEL_HIGH: ["The drainage tank level is high", "Start the drainage return, or check the return pump."],
    STOCK_A_LEVEL_LOW: ["Fertiliser tank A is low", "Fill and prepare fertiliser tank A."], STOCK_B_LEVEL_LOW: ["Fertiliser tank B is low", "Fill and prepare fertiliser tank B."],
    ACID_LEVEL_LOW: ["The acid tank is low", "Fill the acid tank."],
    MAIN_HIGH_HIGH_SWITCH: ["The main tank very high level switch is active", "Stop all water going into the main tank and check the level."],
    MAIN_LOW_LOW_SWITCH: ["The main tank very low level switch is active", "Fill the main tank; watering cannot run."],
    SUMP_HIGH_SWITCH: ["The drainage tank high level switch is active", "Start the drainage return."],
    SUMP_HIGH_MAIN_FULL: ["The drainage tank is high while the main tank is full, so the return pump waits",
      "Find out why the main tank is full (the refill stop level, or a fill valve stuck open) and reduce the drainage from watering. Do not force the return pump on."],
    SUMP_LOW_SWITCH: ["The drainage tank low level switch is active", "Nothing to do: the return pump cannot run dry."],
    // the demo configurations' reservoir and return tank (owner, 28 Oct 2026: every alarm says what to do)
    RESERVOIR_LEVEL_LOW: ["The reservoir level is low", "Refill the reservoir; watering that needs it waits."],
    RESERVOIR_LEVEL_HIGH: ["The reservoir level is high", "Stop adding water to the reservoir and check its fill valve."],
    RESERVOIR_LOW_SWITCH: ["The reservoir low level switch is active", "Refill the reservoir: pumps fed from it cannot run dry."],
    RESERVOIR_HIGH_SWITCH: ["The reservoir high level switch is active", "Stop all water going into the reservoir and check its level."],
    RETURN_TANK_HIGH_SWITCH: ["The return tank high level switch is active", "Start the return pump, or check why it does not empty the tank."],
    DRIVE_FAULT_INPUT: ["The pump drive reports a fault", "Find and fix the cause at the pump drive, then reset the drive there."],
    SAFETY_CHAIN_OPEN: ["The safety circuit is open", "Find out why the safety circuit opened (for example an emergency stop) and close it."],
    ACQUISITION_STALE: ["New readings stopped arriving", "Check the connection to the I/O modules."],
    AI_SIGNAL_INVALID: ["A sensor sends a value outside its allowed range", "Check the sensor and its wiring."],
    DEVICE_COMM: ["An I/O module does not answer", "Check the module's power and its cable."],
    DRIVE_COMM: ["The pump drive does not answer", "Check the pump drive's power and its cable."],
    FLOW_PLAUSIBILITY: ["A flow reading does not match what the equipment is doing", "Check the flow meter and the valves."],
    REFILL_EC_ABOVE_TARGET: ["The nutrient strength (EC) is above the target", "Check the fertiliser amounts in the recipe."],
    REFILL_EC_LIMIT_EXCEEDED: ["The refill added more fertiliser than allowed", "Check the fertiliser settings and the EC probe."],
    REFILL_EC_LIMIT_REACHED: ["The most fertiliser allowed in one refill was reached", "Check the fertiliser settings and the EC probe."],
    REFILL_EC_UNAVAILABLE: ["There is no usable nutrient strength (EC) reading", "Check the EC probe."],
    REFILL_EC_VALIDATION_FAILED: ["The nutrient strength (EC) after the refill is outside its range", "Check the fertiliser amounts and the EC probe."],
    REFILL_PATH_NOT_STARTED: ["A refill step could not start", "Look at the refill batch on the Main tank page."],
    RETENTION_HELD: ["Old history is not being removed (held)", "An administrator checks the date and time under Settings."],
    STORAGE_WRITE_FAILED: ["History could not be written", "Check the free disk space; new starts are refused until it works."],
    HISTORY_CLOCK_BEHIND: ["The clock is behind the stored history", "An administrator checks the date and time under Settings."],
    HISTORY_QUARANTINE_REVIEW_DUE: ["Quarantined history needs a review", "An administrator reviews it under Settings."],
  };
  // alarms that a simulated fault on the Digital Twin page can cause (a level alarm cannot)
  const SIM_FAULT_RULES = new Set(["DRIVE_FAULT_INPUT", "SAFETY_CHAIN_OPEN", "ACQUISITION_STALE", "AI_SIGNAL_INVALID", "DEVICE_COMM",
                                   "DRIVE_COMM", "FLOW_PLAUSIBILITY"]);
  /** An open alarm as a problem: { title, cause, fix, equipment, proc, page, simFault }. */
  function problem(o) {
    const target = String(o.target || ""), rule = String(o.rule_id || "");
    if (rule.startsWith("PROC_") && target.startsWith("process.")) {
      const pid = target.slice(8).split(".")[0], p = proc(pid), reason = rule.slice(5);
      const detail = String(o.message || "").split(": ").slice(2).join(": ");
      const w = whoIn(detail), f = FAULT[reason];
      const [what, fix] = f ? f(w) : [lower(reason.replace(/_/g, " ")), "See the details below."];
      return { title: `${p.name} stopped: ${what}`, cause: what, fix, equipment: w ? cap(equip(w)) : null, proc: pid, page: p.page, latchedProcess: true, simFault: true };
    }
    const r = RULE[rule], name = target.startsWith("process.") ? proc(target.slice(8)).name : null;
    const pageOf = { MAIN: "#main-tank", SUMP: "#drainage", STOCK: "#nutrients", ACID: "#nutrients", DRIVE: "#main-pumps", SAFETY: "#main-pumps", REFILL: "#main-tank" };
    const key = Object.keys(pageOf).find(k => rule.startsWith(k));
    const simFault = SIM_FAULT_RULES.has(rule);
    if (r) return { title: r[0] + (name ? ` (${name})` : ""), cause: lower(r[0]), fix: r[1], equipment: null, proc: null, page: key ? pageOf[key] : null, simFault };
    return { title: plain(o.message || rule), cause: plain(o.message || rule), fix: "See the details below.", equipment: null, proc: null, page: null, simFault };
  }
  const SEVERITY = { CRITICAL: "Serious", WARNING: "Warning", INFO: "Information" };

  // ------------------------------------------------------------------ messages from the controllers
  const n0 = x => Number(x).toLocaleString("en-GB", { maximumFractionDigits: 1 });
  /** A reading with exactly `d` decimals and thousands separators (pH 6.19, 0.35 bar, 12,345.0 L). */
  const fixed = (x, d) => Number(x).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  const PHRASES = [
    [/^batch latches when the main level falls to ([\d.]+) L \(now ([\d.]+) L\)$/, (m, a, b) => `Refill starts when the main tank falls to ${n0(a)} L (now ${n0(b)} L).`],
    [/^batch latches if the level stays at or below ([\d.]+) L for (.*)$/, (m, a, b) => `Refill starts if the main tank stays at or below ${n0(a)} L for ${b}`],
    [/^re-arms when the main level is above ([\d.]+) L(.*)$/, (m, a, b) => `Another refill becomes possible once the main tank is above ${n0(a)} L${b}`],
    [/^starts when the sump reaches ([\d.]+) L \(now ([\d.]+) L\)$/, (m, a, b) => `The return pump starts when the drainage tank reaches ${n0(a)} L (now ${n0(b)} L).`],
    [/^stops at sump ([\d.]+) L \(now ([\d.]+) L\)$/, (m, a, b) => `Stops when the drainage tank is down to ${n0(a)} L (now ${n0(b)} L).`],
    [/^auto-fills at ([\d.]+) L \(now ([\d.]+) L\)$/, (m, a, b) => `Adds water when the tank falls to ${n0(a)} L (now ${n0(b)} L).`],
    [/^filling to ([\d.]+) L \(\+([\d.]+) L\)$/, (m, a, b) => `Filling to ${n0(a)} L (${n0(b)} L added so far).`],
    [/^stop (S\d+) in ([\d.]+) s$/, (m, a, b) => `${sectorName(a)} stops in ${b} s.`],
    [/^pH ([\d.]+) at or below ([\d.]+) \(target ([\d.]+) \+ ([\d.]+)\): no dose$/, (m, a, b, c) => `Acidity (pH) ${a} is at or below ${b} (target ${c}): no acid needed.`],
    [/^dosing: ([\d.]+) of ([\d.]+) L$/, (m, a, b) => `Adding acid: ${a} of ${b} L.`],
    [/^no active recipe$/, () => "No settings (recipe) in use: use one on the Recipes page."],
    [/^mixing\/settling: (.*)$/, (m, a) => `Waiting for the water to mix: ${a}`],
    [/^24 h acid limit reached \(([\d.]+) of ([\d.]+) L\)$/, (m, a, b) => `The most acid allowed in 24 hours is reached (${a} of ${b} L).`],
    [/^pH unavailable.*$/, () => "No usable acidity (pH) reading."],
    [/^main tank full \(([\d.]+) L\): return held until it is below ([\d.]+) L.*$/,
      (m, a, b) => `The main tank is full (${n0(a)} L): the return pump waits until it is below ${n0(b)} L.`],
  ];
  // a refused command, in plain words (the controller's own text stays in the audit and the API)
  const REFUSALS = [
    [/^AUTO only from STOPPED \(current (\w+)\).*$/, (m, a) => `Automatic mode starts only when the process is stopped (now: ${state(a)}). Press Stop first.`],
    [/^MANUAL only from STOPPED \(current (\w+)[^)]*\).*$/, (m, a) => `Manual mode starts only when the process is stopped (now: ${state(a)}). Press Stop first.`],
    [/^in FAULT_LOCKOUT.*$/, () => "Stopped because of a problem: mark the problem as seen and clear the stop first."],
    [/^stop sequence still running$/, () => "The equipment is still stopping. Try again in a moment."],
    [/^duration must be 1\.\.([\d.]+) s.*$/, (m, a) => `The run time must be between 1 and ${n0(a)} seconds.`],
    [/^duration_s is required.*$/, () => "Enter a run time in seconds."],
    [/^volume_L is required.*$/, () => "Enter an amount in litres."],
    [/^volume must be 0\.\.([\d.]+) L.*$/, (m, a) => `The amount must be between 0 and ${n0(a)} L (the room left below the refill stop level).`],
    [/^a manual operation is already running; stop it first$/, () => "Something is already running in manual mode: stop it first."],
    [/^bounded manual operations need MANUAL \(current (\w+)\)$/, (m, a) => `Switch to Manual mode first (now: ${state(a)}).`],
    [/^no active recipe.*$/, () => "No settings (recipe) in use: use one on the Recipes page."],
    [/^(virtual )?outputs still (commanded )?ON.*$/, () => "Some equipment is still switched on: stop it before starting automatic mode."],
    [/^a manual (\w+) transfer is limited to ([\d.]+) L.*$/, (m, a, b) => `A manual transfer from ${a === "WATER" ? "the water supply" : "fertiliser tank " + a} is limited to ${n0(b)} L by the recipe.`],
    [/^stock (\w+) not ready: (.*)$/, (m, a, b) => `${TANK[a] || "Tank " + a} is not confirmed as prepared: ${lower(plain(b))}`],
    [/^a manual acid dose is limited to the recipe dose step ([\d.]+) L$/, (m, a) => `A manual acid dose is limited to ${a} L (the recipe's dose step).`],
    [/^24 h acid limit ([\d.]+) L would be exceeded \(([\d.]+) L dosed\)$/, (m, a, b) => `That would pass the most acid allowed in 24 hours (${a} L; ${b} L added so far).`],
    [/^main high-high switch active$/, () => "The main tank very high level switch is active."],
    [/^drive fault input still active.*$/, () => "The pump drive still reports a fault: reset it at the drive."],
    [/^safety chain still open$/, () => "The safety circuit is still open."],
    [/^pH unavailable$/, () => "No usable acidity (pH) reading."],
  ];
  function refusal(text) {
    const t = String(text == null ? "" : text).trim();
    for (const [re, fn] of REFUSALS) if (re.test(t)) return t.replace(re, fn);
    return plain(t);
  }
  /** A controller's message in plain words: known sentences rephrased, signal ids and sectors named. */
  function plain(text) {
    let t = String(text == null ? "" : text).trim();
    if (!t) return "";
    for (const [re, fn] of PHRASES) if (re.test(t)) { t = t.replace(re, fn); break; }
    t = t.replace(/\b(tank|flow|pressure|pump|valve|contactor|drive|main|safety)\.[A-Za-z0-9_]+(\.[a-z_]+)?\b/g, m => lower(signal(m)));
    t = t.replace(/\b(S\d+)\b/g, (m, s) => sectorName(s));
    return cap(t);
  }


  // ------------------------------------------------------------------ Digital Twin examples (O-03)
  // preset id -> [plain name, one-sentence preview]. The preview says what the example sets up and, for the
  // ones with a script, what it does by itself; the id and the owner-tour case stay under Details.
  const EXAMPLES = {
    tour_low_main: ["Try refilling the main tank", "Sets the main tank level low (7,600 L) so you can try a refill; nothing starts until you start it."],
    tour_ph_high: ["Try acid dosing", "Sets the main tank's acidity high (pH 6.8) so you can try acid dosing; nothing starts until you start it."],
    tour_sump_high: ["Try returning drainage water", "Fills the drainage tank above its start level (2,700 L) so you can try the drainage return; nothing starts until you start it."],
    tour_main_full_sump_high: ["See the return wait for a full main tank", "Sets a full main tank and a high drainage tank so you can see the drainage return hold; nothing starts until you start it."],
    tour_ec_lost_at_valve_opening: ["See a refill stop when EC is lost", "Starts a refill by itself and loses the nutrient strength (EC) reading as fertiliser A's valve opens, so you can see both fertilisers stop."],
    irrigation_alternation: ["Watch automatic watering take turns", "Starts automatic watering by itself: Sector 1 with main pump 1, then Sector 2 with main pump 2, twice."],
    recipe_edit_next_cycle: ["Watch a recipe change apply at the next step", "Starts watering by itself and puts new settings in use halfway, so you can see them apply from the next sector."],
    manual_irrigation_operator_pump: ["Watch watering once by hand", "Switches watering to Manual mode by itself, waters Sector 1 once with main pump 2 for 60 s, then starts automatic watering."],
    manual_bounds_rejected: ["See out-of-limit manual requests refused", "Asks by itself for manual operations outside the recipe's limits, so you can see them refused."],
    raw_command_refused: ["See a direct output command refused", "Sends a direct output command by itself, so you can see that the greenhouse model refuses it."],
    vfd_trip_recovery: ["Recover from a pump drive fault", "Starts watering by itself, then the pump drive trips, so you can follow the recovery steps."],
    vfd_stop_feedback_missing: ["See a pump drive that does not report stopping", "Starts watering by itself and the drive keeps reporting running after the stop, so the valve and pump stay as they are."],
    drive_no_run: ["See a pump drive that does not start", "Starts watering by itself and the drive never reports running."],
    drive_modbus_comm_loss: ["See the pump drive stop answering", "Starts watering by itself and the drive's data link goes quiet, so you can see the drive stop and watering lock out."],
    drive_modbus_refused: ["See the pump drive refuse commands", "Starts watering by itself and the drive answers every command with an error, so nothing runs."],
    drive_modbus_ignores_writes: ["See the pump drive ignore commands", "Starts watering by itself and the drive repeats the commands but does not start."],
    main_pump_no_flow: ["See a main pump with no water flow", "Starts watering by itself and main pump 1 runs without delivering water."],
    contactor_no_close: ["See a pump selector that does not close", "Starts watering by itself and main pump 1's selector (K1) never closes."],
    contactor_welded: ["See a pump selector stuck closed", "Starts watering by itself and main pump 1's selector (K1) stays closed after the step."],
    sector_valve_stuck_closed: ["See a sector valve that does not open", "Starts watering by itself and Sector 1's valve stays closed, so the pump never starts."],
    sector_valve_stuck_open: ["See a sector valve that does not close", "Starts watering by itself and Sector 1's valve stays open after the step."],
    safety_chain_open: ["See the safety circuit open", "Starts processes by itself and opens the safety circuit, so every running process locks out."],
    main_low_low: ["See watering refused with a nearly empty main tank", "Sets the main tank nearly empty (2,000 L) and asks for automatic watering by itself, which does not start."],
    main_source_empty: ["See watering stop as the main tank runs dry", "Starts watering by itself from a low main tank, which falls below the dry-run limit."],
    main_level_open_loop: ["See the main tank level reading fail", "Starts watering by itself and the main tank level sensor's wire breaks."],
    stale_acquisition: ["See readings stop arriving", "Starts watering by itself and the readings stop updating, so watering stops."],
    module_timeout: ["See an input module stop answering", "Starts watering by itself and one input module stops answering, so the sector flow is lost."],
    refill_ec_target: ["Watch a refill to a nutrient strength (EC) target", "Starts a refill by itself that adds water and fertilisers A and B until the EC target is reached."],
    refill_fixed_volumes: ["Watch a refill with set amounts", "Starts a refill by itself that adds set amounts of water and fertilisers A and B, then checks the EC."],
    refill_fixed_ec_out_of_band: ["See a refill end outside the EC limits", "Starts a refill by itself whose set amounts are too small, so the EC check fails."],
    refill_stock_not_prepared: ["See a refill wait for unprepared tanks", "Starts a refill by itself without confirmed fertiliser tanks, so no fertiliser is added."],
    refill_stock_empty: ["See a refill with an empty fertiliser tank", "Starts a refill by itself with fertiliser tank A at its reserve, so no fertiliser is added."],
    refill_transfer_no_flow: ["See a transfer pump with no flow", "Starts a refill by itself and fertiliser A's transfer pump runs without water flow."],
    refill_transfer_timeout: ["See a fertiliser transfer take too long", "Starts a refill by itself and one fertiliser transfer runs past its longest time."],
    stock_fill_invalidates: ["See a water fill undo a tank's preparation", "Fills fertiliser tank A with water by itself, so the tank must be confirmed prepared again."],
    refill_concurrency: ["Watch several processes run together", "Starts refill, watering, drainage return and acid dosing by itself at the same time."],
    acid_to_target: ["Watch acid dosing reach its target", "Sets a high pH and starts acid dosing by itself, in doses with waits, until the target is reached."],
    acid_already_in_band: ["See acid dosing with pH already right", "Starts acid dosing by itself with pH already at its target, so no acid is added."],
    acid_undershoot: ["See pH below its lower limit", "Sets the pH too low (5.3) and starts acid dosing by itself, which stops with a problem."],
    acid_no_response: ["See acid dosing with a frozen pH probe", "Starts acid dosing by itself while the pH reading does not change, so it stops after the set number of doses."],
    acid_batch_limit: ["See acid dosing reach its batch limit", "Starts acid dosing by itself until the most acid allowed in one batch is reached."],
    acid_ph_unavailable: ["See acid dosing without a pH reading", "Starts acid dosing by itself and the pH sensor's wire breaks, so no dose starts."],
    return_alternation: ["Watch the return pumps take turns", "Starts the drainage return by itself; the two return pumps take turns at each start."],
    sump_high_main_full: ["See the return hold for a full main tank", "Sets a full main tank and a high drainage tank and starts the return by itself, which holds."],
    return_pump_no_flow: ["See a return pump with no water flow", "Starts the drainage return by itself and the pump runs without water flow."],
  };
  function example(id, description) {
    const x = EXAMPLES[id];
    return x ? { name: x[0], preview: x[1] } : { name: id.replace(/_/g, " "), preview: description || "" };
  }
  window.AqWords = { EXAMPLES, example, TANK, PROC, proc, state, phase, equip, signal, signalGroup, GROUP_ORDER, quality, QUALITY_HELP, HELP,
                     problem, SEVERITY, plain, refusal, sectorName, n0, fixed, cap };
})();
