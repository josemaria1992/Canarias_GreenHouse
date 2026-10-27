/* UX-07 (owner, 27 October 2026): equipment types without code. A guided form replaces the JSON editor, for new types
 * and for editing existing ones: choose what the equipment is (with its drawing symbol shown), name it and choose its
 * icon from a gallery, answer only the questions that kind needs, add a feature when a need is missing, read the
 * result in ordinary words, then save a draft. The form writes the same validated type definition (TypeBody) through
 * the same draft endpoint; it never assigns channels, verifies wiring, activates a type or operates anything.
 *
 * Every field the form does not show is kept as it was (a no-change edit saves the same definition). Nothing is
 * invented: a contact's polarity is asked, never assumed, and what the software cannot do is said, with a place to
 * describe the need for review (drafted here only: nobody is notified).
 *
 * The model functions (fromBody, toBody, problems, summary, startFrom) are pure and run under Node for the tests. */
"use strict";
(function () {
  const W = typeof window !== "undefined" ? window : globalThis;
  const clone = x => JSON.parse(JSON.stringify(x));
  const cap = s => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1).replace(/_/g, " ");

  // ---------------------------------------------------------------- vocabulary (what the operator reads)
  const KINDS = [
    { id: "pump", word: "Pump", template: "pump", hydraulic: "pump", icon: "pump", asks: ["RUNNING", "FAULT"], what: "moves water while its output is on" },
    { id: "valve", word: "Valve", template: "valve", hydraulic: "valve", icon: "valve", asks: ["OPEN", "CLOSED"], what: "lets water through while its output is on" },
    { id: "tank", word: "Tank", template: "tank", hydraulic: "tank", icon: "tank", asks: [], what: "stores water; no channel of its own (its level sensor is separate equipment)" },
    { id: "flow", word: "Flow meter", template: "flow", hydraulic: "meter", icon: "flow", measure: "flow", asks: [], what: "fitted in a pipe; water flows through it and it reads the flow" },
    { id: "level", word: "Level sensor", template: "level", hydraulic: "sensor", icon: "level", measure: "level", asks: [], what: "reads the level of a tank" },
    { id: "pressure", word: "Pressure sensor", template: "pressure", hydraulic: "sensor", icon: "pressure", measure: "pressure", asks: [], what: "reads the pressure in a pipe" },
    { id: "ph", word: "pH sensor", template: "ph", hydraulic: "sensor", icon: "sensor", measure: "ph", asks: [], what: "reads the pH of the water" },
    { id: "ec", word: "EC sensor", template: "ec", hydraulic: "sensor", icon: "sensor", measure: "ec", asks: [], what: "reads the conductivity of the water" },
    { id: "temperature", word: "Temperature sensor", template: "temperature", hydraulic: "sensor", icon: "temperature", measure: "temperature", asks: [], what: "reads a temperature" },
    { id: "water_source", word: "Water source", template: "water_source", hydraulic: "source", icon: "drop", asks: [], what: "where water comes from (mains, a well); no channel of its own" },
    { id: "sector", word: "Sector", template: "sector", hydraulic: "sector", icon: "drop", asks: [], what: "where water is delivered; no channel of its own" },
    { id: "button", word: "Button or switch", template: "button", hydraulic: null, icon: "button", asks: [], what: "a contact read on a digital input (a push button, a float switch)" },
    { id: "custom", word: "Something else", template: null, hydraulic: null, icon: "generic", asks: [], what: "start empty and add what it has" },
  ];
  const HYDRAULIC_WORD = { "": "Not part of the water system (not drawn in the water diagram)", pump: "Moves water (drawn as a pump)",
    valve: "Opens and closes a pipe (drawn as a valve)", meter: "Water flows through it and it is measured (drawn as a flow meter, in a pipe)",
    sensor: "Measures something (drawn as a sensor, associated with a pipe or tank)", tank: "Stores water (drawn as a tank)",
    source: "Supplies water (drawn as a water source)", sector: "Receives water (drawn as a sector)" };
  const PASSIVE = ["source", "tank", "sector"];
  const MEASURES = {
    flow: { word: "Flow rate", units: ["L/min", "L/h", "m³/h"] }, level: { word: "Level", units: ["cm", "m", "%", "L"] },
    pressure: { word: "Pressure", units: ["bar", "kPa", "psi"] }, ph: { word: "pH", units: ["pH"] },
    ec: { word: "Conductivity (EC)", units: ["mS/cm", "µS/cm"] }, temperature: { word: "Temperature", units: ["°C"] },
    other: { word: "Something else", units: [] } };
  const SIGNALS = [["CURRENT_4_20", "4–20 mA", "the usual for transmitters: 4 mA is the bottom of the range, 20 mA the top; a broken wire reads below 4 mA"],
    ["CURRENT_0_20", "0–20 mA", "0 mA is the bottom of the range: a broken wire looks like a real zero"],
    ["VOLTAGE_0_5", "0–5 V", "a voltage signal"], ["VOLTAGE_1_5", "1–5 V", "a voltage signal with an offset, like 4–20 mA"],
    ["", "Not decided yet", "any analog input fits; choose when the transmitter is known"]];
  const DRIVES = [["", "Any output that fits"], ["RELAY_CONTACT", "Relay contact (dry contact)"], ["OPEN_DRAIN_SINK", "Transistor output (500 mA)"]];
  const STATUS = {
    RUNNING: { word: "It is running", ask: "Does it report that it is running?", use: "a program waits for it after a start and stops the equipment if it is lost", timed: true },
    FAULT: { word: "It has a fault", ask: "Does it report a fault?", use: "a fault stops it and keeps it from starting until it clears" },
    OPEN: { word: "It is open (position switch)", ask: "Does it report that it is open?", use: "a program can wait for it after opening", timed: true },
    CLOSED: { word: "It is closed (position switch)", ask: "Does it report that it is closed?", use: "shown with the equipment; programs do not use it yet", timed: true },
    STATE: { word: "Something else that is on or off", ask: "", use: "programs can start or stop on it (a button, a float switch)" } };
  const COMMAND_WORD = { pump: "Start/stop output (on = running)", valve: "Open output (on = open)" };
  const OPS = { RUN_FOR: "run for a set time", SCHEDULE: "run on a weekly schedule", DELIVER_VOLUME: "deliver a measured volume",
    KEEP_BETWEEN: "switch to keep a reading between limits", TRIGGERED: "start when a condition is met",
    SEQUENCE_STEP: "be a step in a sequence", MEASURE: "give its reading to programs and alarms" };
  const ACTUATING = ["RUN_FOR", "SCHEDULE", "DELIVER_VOLUME", "KEEP_BETWEEN", "TRIGGERED", "SEQUENCE_STEP"];
  const SIM = { NOT_MODELLED: "Not simulated (the twin shows NOT MODELLED)", FOLLOW_COMMAND: "Its feedback follows the output after a delay",
    VALVE_TRAVEL: "Its position switches follow the output after a travel time", PUMP_FLOW: "Running follows the output; a linked flow meter reads a demo flow",
    TANK_LEVEL: "Its level follows the named inflows and outflows (demo)", MANUAL_INPUT: "Set by hand in the twin",
    MANUAL_ANALOG: "Its reading is set by hand in the twin" };
  const PROP_KIND = { NUMBER: "a number", INTEGER: "a whole number", TEXT: "text", BOOLEAN: "yes or no", CHOICE: "one of a list" };

  // ---------------------------------------------------------------- model
  function slug(label, taken) {
    let base = String(label || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "x";
    if (!/^[a-z0-9]/.test(base)) base = "x" + base;
    let k = base, n = 1;
    while (taken.includes(k)) k = `${base}_${++n}`;
    return k;
  }
  function kindOf(body) {
    const hy = body.hydraulic || null;
    const meas = (body.roles || []).find(r => r.purpose === "MEASUREMENT");
    if (hy === "pump" || hy === "valve" || hy === "tank" || hy === "sector") return hy;
    if (hy === "meter") return "flow";
    if (hy === "source") return "water_source";
    if (hy === "sensor") return meas && KINDS.some(k => k.measure === meas.role) ? meas.role : "sensor";
    if ((body.roles || []).length && body.roles.every(r => r.purpose === "STATE")) return "button";
    return "custom";
  }
  /** The form for a stored definition: everything it holds is kept; only presentation fields are added. */
  function fromBody(body, meta) {
    const b = clone(body || {});
    return { base: b, kind: kindOf(b), name: meta && meta.name || "", type_id: meta && meta.type_id || null,
      version: meta && meta.version || null, status: meta && meta.status || null,
      description: b.description || "", icon: b.default_icon || "generic", category: b.category || "",
      hydraulic: b.hydraulic || null,
      roles: (b.roles || []).map(r => Object.assign(clone(r), { pol: r.inverted ? "open" : "closed", known: true,
        measure: r.purpose === "MEASUREMENT" ? (MEASURES[r.role] ? r.role : "other") : undefined })),
      props: clone(b.properties || []), limits: clone(b.limits || {}), ops: clone(b.operations || []),
      sim: clone(b.simulation || { behavior: "NOT_MODELLED" }), requests: [], adding: null, errors: {} };
  }
  /** A new type of a kind: the built-in definition is the starting point, without the optional feedback signals,
   *  which the form asks about (their wiring is never assumed). */
  function startFrom(kindId, templates) {
    const k = KINDS.find(x => x.id === kindId) || KINDS[KINDS.length - 1];
    const t = k.template && templates && templates[k.template];
    const body = t ? clone(t.body) : { category: "Other", default_icon: "generic", roles: [], operations: [], limits: {},
      simulation: { behavior: "NOT_MODELLED" } };
    body.roles = (body.roles || []).filter(r => r.required !== false || !["RUNNING", "FAULT", "OPEN", "CLOSED"].includes(r.purpose));
    if (k.hydraulic && !body.hydraulic) body.hydraulic = k.hydraulic;
    const f = fromBody(body, null);
    f.kind = k.id;
    f.name = "";
    f.template = k.template;
    return f;
  }
  function addStatus(f, purpose, label) {
    const taken = f.roles.map(r => r.role);
    const word = { RUNNING: "running", FAULT: "fault", OPEN: "is_open", CLOSED: "is_closed" }[purpose];
    f.roles.push({ role: word && !taken.includes(word) ? word : slug(label || STATUS[purpose].word, taken),
      label: label || { RUNNING: "Running", FAULT: "Fault", OPEN: "Open switch", CLOSED: "Closed switch", STATE: "State" }[purpose],
      direction: "INPUT", signal: "DIGITAL", purpose, required: false,
      feedback_timeout_s: STATUS[purpose].timed ? (purpose === "RUNNING" ? 5 : 10) : undefined, pol: null, known: false });
  }
  function addReading(f, label, measure, unit, electrical) {
    const taken = f.roles.map(r => r.role);
    const key = MEASURES[measure] && measure !== "other" && !taken.includes(measure) ? measure : slug(label, taken);
    f.roles.push({ role: key, label: label || MEASURES[measure].word, direction: "INPUT", signal: "ANALOG", purpose: "MEASUREMENT",
      required: true, electrical: electrical ? [electrical] : [], unit: unit || "", measure, pol: "closed", known: true });
    if (!f.ops.includes("MEASURE")) f.ops.push("MEASURE");
  }
  function addOutput(f, label) {
    f.roles.unshift({ role: slug(label || "on", f.roles.map(r => r.role)), label: label || "On", direction: "OUTPUT", signal: "DIGITAL",
      purpose: "COMMAND", required: true, pol: "closed", known: true });
    for (const o of ["RUN_FOR", "SCHEDULE", "SEQUENCE_STEP"]) if (!f.ops.includes(o)) f.ops.push(o);
  }
  function addProperty(f, p) {
    f.props.push(Object.assign({ key: slug(p.label, f.props.map(x => x.key)), label: p.label, kind: p.kind || "NUMBER" },
      p.unit ? { unit: p.unit } : {}, p.min != null && p.min !== "" ? { min: Number(p.min) } : {}, p.max != null && p.max !== "" ? { max: Number(p.max) } : {},
      p.kind === "CHOICE" ? { choices: p.choices } : {}, p.required ? { required: true } : {}));
  }
  /** The validated definition the form stands for: the stored one with only what the form controls replaced. */
  function toBody(f) {
    const b = clone(f.base || {});
    b.category = f.category || (KINDS.find(k => k.id === f.kind) || {}).word || "Other";
    b.description = f.description || "";
    b.default_icon = f.icon || "generic";
    if (f.hydraulic) b.hydraulic = f.hydraulic; else delete b.hydraulic;
    b.roles = f.roles.map(r => {
      const o = {};
      for (const k of Object.keys(r)) if (!["pol", "known", "measure", "_removed"].includes(k) && r[k] !== undefined) o[k] = clone(r[k]);
      if (r.signal === "DIGITAL" && r.direction === "INPUT") o.inverted = r.pol === "open";
      if (o.unit === "") delete o.unit;
      return o;
    });
    const hasMeasure = b.roles.some(r => r.purpose === "MEASUREMENT"), hasCommand = b.roles.some(r => r.purpose === "COMMAND");
    let ops = f.ops.filter(o => hasCommand || !ACTUATING.includes(o));
    if (hasMeasure && !ops.includes("MEASURE")) ops.push("MEASURE");
    if (!hasMeasure) ops = ops.filter(o => o !== "MEASURE");
    b.operations = ops;
    b.properties = clone(f.props);
    const lim = {};
    for (const [k, v] of Object.entries(f.limits || {})) if (v !== null && v !== undefined && v !== "" && !Number.isNaN(Number(v))) lim[k] = Number(v);
    b.limits = lim;
    b.simulation = clone(f.sim);
    return b;
  }
  /** What blocks saving, each with the field it belongs to and how to fix it. */
  function problems(f) {
    const out = [];
    if (!String(f.name || "").trim()) out.push({ field: "name", text: "Give the type a name (for example Irrigation pump)." });
    const cmds = f.roles.filter(r => r.purpose === "COMMAND");
    if (cmds.length > 1) out.push({ field: "roles", text: `Only one control output per type is supported: ${cmds.map(r => r.label).join(", ")}. Remove the extra one, or describe the need below for review.` });
    for (const r of f.roles) {
      if (!String(r.label || "").trim()) out.push({ field: "role:" + r.role, text: "Every signal needs a name." });
      if (r.direction === "INPUT" && r.signal === "DIGITAL" && !r.pol)
        out.push({ field: "role:" + r.role, text: `Say how “${r.label}” signals: does its contact close or open when it is active? If the wiring is not confirmed yet, leave this signal out for now.` });
      if (r.purpose === "MEASUREMENT" && !String(r.unit || "").trim()) out.push({ field: "role:" + r.role, text: `Choose the unit of “${r.label}”.` });
    }
    const labels = f.roles.map(r => String(r.label || "").trim().toLowerCase()).filter(Boolean);
    const dupL = labels.filter((x, i) => labels.indexOf(x) !== i);
    if (dupL.length) out.push({ field: "roles", text: `Two signals have the same name (${dupL[0]}): rename one.` });
    const pl = f.props.map(p => String(p.label || "").trim().toLowerCase());
    if (pl.some((x, i) => pl.indexOf(x) !== i)) out.push({ field: "props", text: "Two details have the same name: rename one." });
    for (const p of f.props) {
      if (!String(p.label || "").trim()) out.push({ field: "prop:" + p.key, text: "Every detail needs a name." });
      if (p.kind === "CHOICE" && !(p.choices || []).length) out.push({ field: "prop:" + p.key, text: `List the choices of “${p.label}”.` });
      if (p.min != null && p.max != null && Number(p.min) > Number(p.max)) out.push({ field: "prop:" + p.key, text: `“${p.label}”: the lowest value is above the highest.` });
    }
    if (!f.roles.length && !PASSIVE.includes(f.hydraulic)) out.push({ field: "roles", text: "This kind needs at least one connection to the controller: answer the questions above or Add a feature. Only tanks, water sources and sectors have none." });
    const b = toBody(f);
    if (b.roles.some(r => r.purpose === "COMMAND") && b.operations.some(o => ACTUATING.includes(o)) && !(b.limits.max_runtime_s > 0))
      out.push({ field: "limits", text: "Enter the maximum running time: equipment that can be switched on needs one." });
    return out;
  }
  function roleWords(r) {
    if (r.purpose === "COMMAND") return `${r.label} (control output${(r.electrical || []).length ? ", " + DRIVES.find(d => d[0] === r.electrical[0])[1].toLowerCase() : ""})`;
    if (r.purpose === "MEASUREMENT") {
      const sig = SIGNALS.find(s => s[0] === ((r.electrical || [])[0] || ""));
      return `${r.label} reading in ${r.unit || "?"} (${sig ? sig[1] : "analog"}${r.required === false ? ", optional" : ""})`;
    }
    return `${r.label} signal (${(STATUS[r.purpose] || STATUS.STATE).word.toLowerCase()}; contact ${r.pol === "open" ? "opens" : r.pol === "closed" ? "closes" : "?"} when active${r.required ? "" : ", optional"})`;
  }
  /** The definition in ordinary words, as the owner asked: what it is, its signals, limits and details. */
  function summary(f) {
    const b = toBody(f), parts = [];
    for (const r of f.roles) parts.push(roleWords(r));
    const l = b.limits;
    if (l.max_runtime_s) parts.push(`maximum running time ${fmtDur(l.max_runtime_s)}`);
    if (l.min_off_s) parts.push(`at least ${fmtDur(l.min_off_s)} off between starts`);
    if (l.max_starts_per_hour) parts.push(`at most ${l.max_starts_per_hour} starts per hour`);
    if (l.max_volume_L) parts.push(`at most ${l.max_volume_L} L per run`);
    if (b.properties.length) parts.push("details asked on each item: " + b.properties.map(p => `${p.label}${p.unit ? " (" + p.unit + ")" : ""}${p.required ? ", required" : ""}`).join(", "));
    if (!b.roles.length) parts.push("no connection to the controller (a passive water part)");
    return `${f.name || "(no name yet)"} — ${parts.join("; ")}.`;
  }
  function fmtDur(s) { s = Number(s); return s >= 3600 && s % 3600 === 0 ? `${s / 3600} h` : s >= 60 && s % 60 === 0 ? `${s / 60} min` : `${s} s`; }
  const Model = { KINDS, MEASURES, SIGNALS, STATUS, OPS, SIM, slug, kindOf, fromBody, startFrom, addStatus, addReading, addOutput,
    addProperty, toBody, problems, summary, roleWords };
  W.AqTypeModel = Model;
  if (typeof module !== "undefined") module.exports = Model;
  if (typeof document === "undefined" || !W.AqUI) return;

  // ---------------------------------------------------------------- the form (browser)
  const U = W.AqUI, { h, api, can, S } = U;
  const TF = S.typeForms = S.typeForms || {};     // editor key -> its form, kept through refreshes and failed saves
  const icon = (n, z) => W.AqIcons.icon(n, z);
  let SYMBOLS = null;
  async function symbols() {                       // fallback only: /api/equipment carries them
    if (SYMBOLS) return SYMBOLS;
    try { SYMBOLS = (await api("GET", "/api/overview/diagram")).symbols || null; } catch (e) { SYMBOLS = null; }
    return SYMBOLS;
  }
  const redraw = () => U.refresh();
  // a change re-checks what was reported missing, so a fixed problem stops being shown
  const set = (f, fn) => ev => { fn(ev); f.dirty = true; if (f.shown) f.shown = problems(f); redraw(); };
  const val = (f, fn) => ev => { fn(ev.target.value); f.dirty = true; ev.target.dataset.dirty = "1"; };

  function stage(key, E, t) {
    if (!TF[key]) TF[key] = t ? fromBody(t.body, t) : { pick: true };
    return TF[key];
  }
  function err(f, field) { const e = (f.shown || []).filter(p => p.field === field); return e.length ? h("p", { class: "bad small", role: "alert" }, e.map(p => p.text).join(" ")) : null; }

  function kindPicker(key, E, f) {
    const tpl = E.template_bodies || {};
    const choose = id => { TF[key] = Object.assign(startFrom(id, tpl), { dirty: true }); redraw(); };
    const copies = E.types.filter(t => t.status === "ACTIVE" || t.status === "DRAFT");
    return h("div", { "data-kind-picker": "" }, h("h4", null, "1. What is it?"),
      h("div", { class: "tf-grid", role: "list" }, KINDS.map(k => h("button", { type: "button", class: "tf-tile", role: "listitem", "data-kind": k.id,
        "aria-label": `${k.word}: ${k.what}`, onclick: () => choose(k.id) }, icon(k.icon, 32), h("strong", null, k.word), h("span", { class: "muted small" }, k.what)))),
      copies.length ? h("p", { class: "small" }, "Or start from a copy of an existing type: ",
        h("select", { "aria-label": "Copy an existing type", onchange: ev => {
          const t = copies.find(x => `${x.type_id}:${x.version}` === ev.target.value); if (!t) return;
          const nf = fromBody(t.body, null); nf.name = `Copy of ${t.name}`; nf.dirty = true; TF[key] = nf; redraw();
        } }, h("option", { value: "" }, "choose a type"), copies.map(t => h("option", { value: `${t.type_id}:${t.version}` }, `${t.name} (v${t.version})`)))) : null);
  }

  function preview(f) {
    const hy = f.hydraulic;
    const sym = SYMBOLS && hy ? W.AqHydraulicEditor && W.AqHydraulicEditor.symbolPreview(SYMBOLS, hy) : null;
    return h("div", { class: "tf-preview", "data-preview": f.icon },
      h("div", null, h("div", { class: "tf-big" }, icon(f.icon, 64)), h("p", { class: "small" }, h("strong", null, cap(f.icon)), " — the icon on Equipment cards and lists.")),
      h("div", null, sym || h("p", { class: "muted small" }, hy ? "The drawing symbol could not be loaded here." : "Not drawn in the water diagram."),
        hy ? h("p", { class: "small" }, `In the Overview and the Digital Twin it is drawn as a ${{ meter: "flow meter", source: "water source" }[hy] || hy}`,
          SYMBOLS && SYMBOLS[hy] && SYMBOLS[hy].ports.length ? ` with its connection points (${SYMBOLS[hy].ports.map(p => p.label).join(", ")}); it can be turned in the drawing.` : " (no pipe connections: it is associated with what it measures).",
          " The symbol follows what the equipment is; the icon is decoration only and never adds connections or changes behaviour.",
          hy === "tank" ? " This preview is representative: each tank's drawn size follows the capacity entered for that tank." : "") : null));
  }

  function look(key, f, E) {
    const icons = E.icons || [];
    return h("div", null, h("h4", null, "2. Name and appearance"),
      h("div", { class: "row wrap" },
        h("label", null, "Name ", h("input", { maxlength: 80, value: f.name, "aria-label": "Type name", placeholder: "for example Irrigation pump", oninput: val(f, v => { f.name = v; }) })),
        h("label", null, "Group on the type list ", h("input", { maxlength: 40, value: f.category, "aria-label": "Group", oninput: val(f, v => { f.category = v; }) }))),
      err(f, "name"),
      h("label", { class: "row" }, "Short description (optional) ", h("input", { maxlength: 500, value: f.description, class: "wide", "aria-label": "Description", oninput: val(f, v => { f.description = v; }) })),
      f.kind === "custom" || f.kind === "sensor"
        ? h("label", { class: "row" }, "How does it take part in the water system? ",
          h("select", { "aria-label": "Water role", onchange: set(f, ev => { f.hydraulic = ev.target.value || null; }) },
            Object.entries(HYDRAULIC_WORD).map(([k, w]) => h("option", { value: k, selected: (f.hydraulic || "") === k }, w)))) : null,
      h("p", { class: "small" }, "Icon (choose one; the preview shows it):"),
      h("div", { class: "tf-icons", role: "radiogroup", "aria-label": "Icon" }, icons.map(n => h("button", { type: "button", class: "tf-icon" + (f.icon === n ? " sel" : ""),
        role: "radio", "aria-checked": f.icon === n ? "true" : "false", "aria-label": cap(n), "data-icon": n, onclick: set(f, () => { f.icon = n; }) }, icon(n, 28), h("span", { class: "small" }, cap(n))))),
      preview(f));
  }

  function polarity(f, r) {
    const name = `${f.key || ""}:pol-${r.role}`;           // one radio group per form: several editors can be open
    return h("span", { class: "row wrap small" }, "When active, its contact ",
      ["closed", "open"].map(p => h("label", { class: "inline" }, h("input", { type: "radio", name, checked: r.pol === p, "aria-label": `${r.label}: contact ${p === "closed" ? "closes" : "opens"} when active`,
        onchange: set(f, () => { r.pol = p; }) }), p === "closed" ? " closes (signal 1)" : " opens (signal 0, a normally-closed contact)")),
      r.pol ? null : h("span", { class: "warn" }, " — not chosen: say which, as wired; it is never assumed."));
  }
  function statusRow(f, r) {
    const st = STATUS[r.purpose] || STATUS.STATE;
    return h("div", { class: "tf-row", "data-role": r.role },
      h("div", { class: "row wrap" }, h("label", null, "Name ", h("input", { maxlength: 60, value: r.label, "aria-label": `Name of signal ${r.role}`, oninput: val(f, v => { r.label = v; }) })),
        h("span", { class: "muted small" }, `${st.word}: ${st.use}.`)),
      polarity(f, r),
      h("div", { class: "row wrap small" },
        h("label", { class: "inline" }, h("input", { type: "checkbox", checked: !!r.required, "aria-label": `${r.label} always wired`, onchange: set(f, ev => { r.required = ev.target.checked; }) }),
          " Always wired (an item without this channel cannot run)"),
        st.timed ? h("label", null, " Time allowed to report after the output changes ", h("input", { type: "number", min: 1, max: 600, step: 1, class: "narrow-num", value: r.feedback_timeout_s || "",
          "aria-label": `${r.label} time allowed`, oninput: val(f, v => { r.feedback_timeout_s = v === "" ? undefined : Number(v); }) }), " s") : null),
      err(f, "role:" + r.role));
  }
  function readingRow(f, r) {
    const m = MEASURES[r.measure] || MEASURES.other;
    const sig = SIGNALS.find(s => s[0] === ((r.electrical || [])[0] || "")) || SIGNALS[4];
    return h("div", { class: "tf-row", "data-role": r.role },
      h("div", { class: "row wrap" },
        h("label", null, "What it measures ", h("select", { "aria-label": `${r.label}: what it measures`, onchange: set(f, ev => {
          r.measure = ev.target.value; const mm = MEASURES[r.measure]; if (mm.units.length) r.unit = mm.units[0]; if (r.measure !== "other") r.label = mm.word; }) },
          Object.entries(MEASURES).map(([k, x]) => h("option", { value: k, selected: r.measure === k }, x.word)))),
        h("label", null, "Name ", h("input", { maxlength: 60, value: r.label, "aria-label": `Name of reading ${r.role}`, oninput: val(f, v => { r.label = v; }) })),
        h("label", null, "Unit ", m.units.length ? h("select", { "aria-label": `${r.label} unit`, onchange: set(f, ev => { r.unit = ev.target.value; }) },
          [...new Set([...m.units, r.unit].filter(Boolean))].map(u => h("option", { value: u, selected: r.unit === u }, u)))
          : h("input", { maxlength: 16, value: r.unit || "", class: "narrow", "aria-label": `${r.label} unit`, oninput: val(f, v => { r.unit = v; }) })),
        h("label", null, "Signal ", h("select", { "aria-label": `${r.label} signal`, onchange: set(f, ev => { r.electrical = ev.target.value ? [ev.target.value] : []; }) },
          SIGNALS.map(s => h("option", { value: s[0], selected: sig[0] === s[0] }, s[1]))))),
      h("p", { class: "muted small" }, `${sig[1]}: ${sig[2]}. The measuring range (scaling) and calibration are entered on each item, where the transmitter's datasheet is known.`),
      err(f, "role:" + r.role));
  }
  function signals(key, f) {
    const k = KINDS.find(x => x.id === f.kind) || {};
    const cmd = f.roles.find(r => r.purpose === "COMMAND");
    const rows = [];
    if (cmd) rows.push(h("div", { class: "tf-row", "data-role": cmd.role }, h("div", { class: "row wrap" },
      h("label", null, "Control output ", h("input", { maxlength: 60, value: cmd.label, "aria-label": "Name of the control output", oninput: val(f, v => { cmd.label = v; }) })),
      h("span", { class: "muted small" }, COMMAND_WORD[f.hydraulic] || "On while the equipment should be on"))));
    for (const p of k.asks || []) {
      const r = f.roles.find(x => x.purpose === p);
      rows.push(h("div", { class: "tf-q", "data-ask": p }, h("label", { class: "inline" }, h("input", { type: "checkbox", checked: !!r, "aria-label": STATUS[p].ask,
        onchange: set(f, ev => { if (ev.target.checked) addStatus(f, p); else f.roles = f.roles.filter(x => x !== r); }) }), " ", STATUS[p].ask),
        r ? statusRow(f, r) : null));
    }
    for (const r of f.roles) {
      if (r === cmd || (k.asks || []).includes(r.purpose) && f.roles.find(x => x.purpose === r.purpose) === r) continue;
      rows.push(h("div", { class: "tf-feature" }, r.purpose === "MEASUREMENT" ? readingRow(f, r) : r.purpose === "COMMAND" ? h("p", { class: "small" }, roleWords(r)) : statusRow(f, r),
        h("button", { type: "button", class: "ghost small", "aria-label": `Remove ${r.label}`, onclick: () => removeRole(f, r) }, "Remove")));
    }
    return h("div", null, h("h4", null, "3. Connections to the controller"),
      rows.length ? rows : h("p", { class: "muted small" }, PASSIVE.includes(f.hydraulic) ? "None: this is a passive water part. Its level, if measured, is a separate level sensor associated with it in the drawing." : "None yet: use Add a feature."),
      err(f, "roles"));
  }
  function removeRole(f, r) {
    const used = f.users || 0;
    const why = used && r.known ? `${used} item(s) use this type. They keep their current version; upgrading one to the new version drops the channel chosen for “${r.label}”. Remove it from the new version?` : `Remove “${r.label}”?`;
    if (!confirm(why)) return;
    f.roles = f.roles.filter(x => x !== r); f.dirty = true; redraw();
  }
  function details(f) {
    const rows = f.props.map(p => h("div", { class: "tf-row", "data-prop": p.key }, h("div", { class: "row wrap" },
      h("label", null, "Detail ", h("input", { maxlength: 60, value: p.label, "aria-label": `Detail ${p.key} name`, oninput: val(f, v => { p.label = v; }) })),
      h("span", { class: "small" }, PROP_KIND[p.kind] || p.kind, p.unit ? ` in ${p.unit}` : "", p.min != null || p.max != null ? ` (${p.min ?? "…"} to ${p.max ?? "…"})` : ""),
      h("label", { class: "inline small" }, h("input", { type: "checkbox", checked: !!p.required, "aria-label": `${p.label} required`, onchange: set(f, ev => { p.required = ev.target.checked; }) }), " must be filled in"),
      h("button", { type: "button", class: "ghost small", "aria-label": `Remove detail ${p.label}`, onclick: () => {
        if (confirm(f.users ? `${f.users} item(s) use this type; the value they hold for “${p.label}” is not used by the new version. Remove it?` : `Remove “${p.label}”?`)) { f.props = f.props.filter(x => x !== p); f.dirty = true; redraw(); }
      } }, "Remove")), err(f, "prop:" + p.key)));
    return h("div", null, h("h4", null, "4. Details asked on each item"),
      h("p", { class: "muted small" }, f.hydraulic === "tank" ? "Each tank has its own capacity: it is entered on the tank, not here, and its drawn size follows it." : "These are filled in on each item of this type; a detail is information, not a live reading or an automatic limit."),
      rows.length ? rows : h("p", { class: "muted small" }, "None."), err(f, "props"));
  }
  function limits(f) {
    if (!f.roles.some(r => r.purpose === "COMMAND")) return null;
    const L = f.limits;
    const num = (k, label, unit, help, step) => h("label", { class: "row wrap" }, `${label} `, h("input", { type: "number", min: 0, step: step || 1, class: "narrow-num", value: L[k] ?? "",
      "aria-label": label, oninput: val(f, v => { L[k] = v === "" ? null : Number(v); }) }), ` ${unit}`, h("span", { class: "muted small" }, ` — ${help}`));
    const mins = L.max_runtime_s ? L.max_runtime_s / 60 : "";
    return h("div", null, h("h4", null, "5. Limits"),
      h("label", { class: "row wrap" }, "Maximum running time ", h("input", { type: "number", min: 0.1, step: "any", class: "narrow-num", value: mins, "aria-label": "Maximum running time in minutes",
        oninput: val(f, v => { L.max_runtime_s = v === "" ? null : Math.round(Number(v) * 60); }) }), " min", h("span", { class: "muted small" }, " — required: no program or manual run keeps it on longer")),
      num("min_off_s", "Minimum time off between starts", "s", "protects motors from short cycling"),
      num("max_starts_per_hour", "Maximum starts per hour", "starts", "optional"),
      num("max_volume_L", "Maximum volume per run", "L", "optional; needs a flow meter on its path", "any"),
      err(f, "limits"));
  }
  function addFeature(key, f) {
    const a = f.adding;
    if (!a) return h("div", { class: "row" }, h("button", { type: "button", class: "ghost", onclick: set(f, () => { f.adding = { what: null }; }) }, "+ Add a feature"),
      h("span", { class: "muted small" }, "a reading, a status signal, a detail, or something the form does not ask about"));
    const opt = (id, word) => h("label", { class: "inline" }, h("input", { type: "radio", name: `add-${key}`, checked: a.what === id, onchange: set(f, () => { a.what = id; }) }), " ", word);
    const done = fn => () => { fn(); f.adding = null; f.dirty = true; redraw(); };
    // typing does not redraw the form, so an Add button checks when pressed and says what is missing
    const need = (check, fn) => () => { const m = check(); if (m) { a.msg = m; redraw(); return; } a.msg = null; fn(); };
    const named = what => () => {
      const n = String(a.label || "").trim().toLowerCase();
      if (!n) return `Enter the name of the ${what}.`;
      const taken = what === "detail" ? f.props.map(x => x.label) : f.roles.map(x => x.label);
      return taken.some(x => String(x || "").trim().toLowerCase() === n) ? `This type already has a ${what === "detail" ? "detail" : "signal"} called “${a.label.trim()}”: choose another name.` : null;
    };
    const cancel = h("button", { type: "button", class: "ghost small", onclick: set(f, () => { f.adding = null; }) }, "Cancel");
    const field = (k, label, extra) => h("label", null, label + " ", h("input", Object.assign({ maxlength: 60, value: a[k] || "", "aria-label": `New feature: ${label}`, oninput: ev => { a[k] = ev.target.value; ev.target.dataset.dirty = "1"; } }, extra || {})));
    let body = null;
    if (a.what === "reading") {
      const m = MEASURES[a.measure || "other"];
      body = [h("div", { class: "row wrap" },
        h("label", null, "What it measures ", h("select", { "aria-label": "New reading: what it measures", onchange: set(f, ev => { a.measure = ev.target.value; a.unit = (MEASURES[a.measure].units || [])[0] || ""; if (a.measure !== "other") a.label = MEASURES[a.measure].word; }) },
          Object.entries(MEASURES).map(([k, x]) => h("option", { value: k, selected: (a.measure || "other") === k }, x.word)))),
        field("label", "Name"), m.units.length ? h("label", null, "Unit ", h("select", { "aria-label": "New reading: unit", onchange: ev => { a.unit = ev.target.value; } }, m.units.map(u => h("option", { value: u, selected: a.unit === u }, u)))) : field("unit", "Unit", { maxlength: 16 }),
        h("label", null, "Signal ", h("select", { "aria-label": "New reading: signal", onchange: ev => { a.sig = ev.target.value; } }, SIGNALS.map(s => h("option", { value: s[0], selected: (a.sig || "") === s[0] }, s[1]))))),
        h("p", { class: "muted small" }, "A reading is a live measurement on an analog input. It appears with the item's readings, in programs' conditions and in alarm limits."),
        h("div", { class: "row" }, h("button", { type: "button", onclick: need(() => named("reading")() || (String(a.unit || (m.units[0] || "")).trim() ? null : "Enter its unit."),
          done(() => addReading(f, a.label, a.measure || "other", a.unit || m.units[0], a.sig))) }, "Add this reading"), cancel)];
    } else if (a.what === "status") {
      body = [h("div", { class: "row wrap" }, field("label", "Name"),
        h("label", null, "What it means ", h("select", { "aria-label": "New signal: meaning", onchange: ev => { a.purpose = ev.target.value; } },
          ["RUNNING", "FAULT", "OPEN", "CLOSED", "STATE"].map(p => h("option", { value: p, selected: (a.purpose || "STATE") === p }, STATUS[p].word))))),
        h("p", { class: "muted small" }, `${STATUS[a.purpose || "STATE"].word}: ${STATUS[a.purpose || "STATE"].use}.` +
          (a.purpose && f.roles.some(r => r.purpose === a.purpose) && ["RUNNING", "OPEN"].includes(a.purpose) ? " This type already has one; programs use the first one wired." : "") + " You choose its polarity next; nothing is assumed."),
        h("div", { class: "row" }, h("button", { type: "button", onclick: need(named("signal"), done(() => addStatus(f, a.purpose || "STATE", a.label))) }, "Add this signal"), cancel)];
    } else if (a.what === "detail") {
      body = [h("div", { class: "row wrap" }, field("label", "Name"),
        h("label", null, "Value ", h("select", { "aria-label": "New detail: value type", onchange: set(f, ev => { a.kind = ev.target.value; }) },
          Object.entries(PROP_KIND).map(([k, w]) => h("option", { value: k, selected: (a.kind || "NUMBER") === k }, w)))),
        (a.kind || "NUMBER") === "NUMBER" || a.kind === "INTEGER" ? [field("unit", "Unit", { maxlength: 16 }), field("min", "Lowest", { type: "number", class: "narrow-num" }), field("max", "Highest", { type: "number", class: "narrow-num" })] : null,
        a.kind === "CHOICE" ? field("choices", "Choices (comma-separated)", { maxlength: 400 }) : null,
        h("label", { class: "inline" }, h("input", { type: "checkbox", checked: !!a.required, onchange: ev => { a.required = ev.target.checked; } }), " must be filled in")),
        h("p", { class: "muted small" }, "A detail is entered by hand on each item (a manufacturer, a rated flow). It is not a live reading, and a maximum written here does not stop anything: an enforced limit is set in Limits, or as an alarm or stop condition."),
        h("div", { class: "row" }, h("button", { type: "button", onclick: need(named("detail"), done(() => addProperty(f, { label: a.label, kind: a.kind || "NUMBER", unit: a.unit, min: a.min, max: a.max,
          choices: String(a.choices || "").split(",").map(s => s.trim()).filter(Boolean), required: a.required }))) }, "Add this detail"), cancel)];
    } else if (a.what === "output") {
      const has = f.roles.some(r => r.purpose === "COMMAND");
      body = has ? unsupported(f, a, "A second control output (for example close, reverse or a second speed) is not supported: programs switch one output per item and have no logic for a second one. Describe what it should do and it is kept here for review.", cancel)
        : [h("div", { class: "row wrap" }, field("label", "Name of the output")), h("p", { class: "muted small" }, "One digital output that is on while the equipment should be on. A maximum running time will be asked."),
          h("div", { class: "row" }, h("button", { type: "button", onclick: need(named("output"), done(() => addOutput(f, a.label))) }, "Add this output"), cancel)];
    } else if (a.what === "other") {
      body = unsupported(f, a, "Describe the need. The form keeps what you have entered; this description is a request for a software change, kept here for review. It does not make the equipment do anything.", cancel);
    }
    return h("div", { class: "tf-add", "data-adding": a.what || "" }, h("h4", null, "Add a feature"),
      h("div", { class: "row wrap" }, opt("reading", "A reading (temperature, pressure, flow…)"), opt("status", "A status signal (running, fault, position, on/off)"),
        opt("detail", "A detail (manufacturer, rated flow…)"), opt("output", "A control output"), opt("other", "Something the form cannot do")),
      body || h("p", { class: "muted small" }, "Choose what to add."), a.msg ? h("p", { class: "bad small", role: "alert" }, a.msg) : null);
  }
  function unsupported(f, a, text, cancel) {
    return [h("p", { class: "small warn" }, text),
      h("textarea", { rows: 3, cols: 70, "aria-label": "Describe the missing need", value: a.need || "", oninput: ev => { a.need = ev.target.value; ev.target.dataset.dirty = "1"; } }, a.need || ""),
      h("div", { class: "row" }, h("button", { type: "button", onclick: () => {
        if (!String(a.need || "").trim()) { a.msg = "Describe the need first."; redraw(); return; }
        a.msg = null;
        f.requests.push({ need: a.need.trim(), what: a.what, type: f.name || "(unnamed type)", at: new Date().toISOString() }); f.adding = null; f.dirty = true; redraw();
      } }, "Keep this request"), cancel)];
  }
  function advanced(key, f) {
    const cmd = f.roles.find(r => r.purpose === "COMMAND");
    return h("details", { class: "tf-adv", "data-state-key": key + ":adv" }, h("summary", null, "Less common settings"),
      h("p", { class: "small" }, "What programs may do with it:"),
      h("div", { class: "row wrap" }, Object.entries(OPS).filter(([o]) => o === "MEASURE" ? f.roles.some(r => r.purpose === "MEASUREMENT") : !!cmd).map(([o, w]) =>
        h("label", { class: "inline small" }, h("input", { type: "checkbox", checked: f.ops.includes(o), "aria-label": w, onchange: set(f, ev => {
          f.ops = ev.target.checked ? [...f.ops, o] : f.ops.filter(x => x !== o); }) }), " ", w))),
      cmd ? h("label", { class: "row wrap small" }, "Output that can drive it ", h("select", { "aria-label": "Output that can drive it", onchange: set(f, ev => { cmd.electrical = ev.target.value ? [ev.target.value] : []; }) },
        DRIVES.map(d => h("option", { value: d[0], selected: ((cmd.electrical || [])[0] || "") === d[0] }, d[1])))) : null,
      h("label", { class: "row wrap small" }, "In the Digital Twin ", h("select", { "aria-label": "Twin behaviour", onchange: set(f, ev => { f.sim.behavior = ev.target.value; }) },
        Object.entries(SIM).map(([k, w]) => h("option", { value: k, selected: f.sim.behavior === k }, w)))),
      ["FOLLOW_COMMAND", "VALVE_TRAVEL", "PUMP_FLOW"].includes(f.sim.behavior) ? h("label", { class: "row wrap small" }, "Delay in the twin ",
        h("input", { type: "number", min: 0, max: 600, step: "any", class: "narrow-num", value: f.sim.delay_s ?? 1, "aria-label": "Twin delay", oninput: val(f, v => { f.sim.delay_s = Number(v); }) }), " s") : null,
      f.sim.behavior === "PUMP_FLOW" ? h("label", { class: "row wrap small" }, "Demo flow in the twin ",
        h("input", { type: "number", min: 0.1, step: "any", class: "narrow-num", value: f.sim.demo_flow_L_min ?? "", "aria-label": "Twin demo flow", oninput: val(f, v => { f.sim.demo_flow_L_min = v === "" ? undefined : Number(v); }) }), " L/min (labelled DEMO)") : null,
      h("p", { class: "muted small" }, "A behaviour the twin has no model for stays NOT MODELLED: the twin never shows a plausible value it cannot compute."));
  }
  function requests(f) {
    if (!f.requests.length) return null;
    const text = f.requests.map(r => `Type “${r.type}” — ${r.need}`).join("\n");
    return h("div", { class: "tf-requests", "data-requests": String(f.requests.length) }, h("h4", null, "Needs described for review"),
      h("ul", { class: "small" }, f.requests.map(r => h("li", null, r.need))),
      h("p", { class: "small warn" }, "Kept on this page only: not sent, and nobody has been notified. Where such requests should go, and who reviews them, is still to be decided by the owner. Copy the text to pass it on."),
      h("button", { type: "button", class: "ghost small", onclick: () => { try { navigator.clipboard.writeText(text); U.toast("Copied"); } catch (e) { U.toast(text); } } }, "Copy the text"));
  }

  /** The editor for a type (t) or a new one (t null), keyed like UX-06 so it survives refreshes. */
  function editor(E, t, key) {
    if (E.symbols) SYMBOLS = E.symbols;
    else symbols().then(s => { if (s && !editor.loaded) { editor.loaded = true; redraw(); } });
    const f = stage(key, E, t);
    f.key = key;
    if (f.pick) return kindPicker(key, E, f);
    f.users = t ? E.items.filter(i => i.type_id === t.type_id && i.status === "ACTIVE").length : 0;
    const startedFrom = t ? f.version : null, moved = t && startedFrom !== t.version;
    const save = async () => {
      f.shown = problems(f);
      if (f.shown.length) { redraw(); return; }
      const types = E.types.map(x => x.type_id);
      const type_id = t ? t.type_id : (f.type_id || slug(f.name, types));
      try {
        await api("POST", "/api/equipment/types/draft", { type_id, name: f.name.trim(), body: toBody(f), version: t && t.status === "DRAFT" ? t.version : null });
        delete TF[key];
        U.toast(`${f.name.trim()}: draft saved. An administrator activates it; nothing else changes until then.`);
        redraw();
      } catch (e) {
        f.shown = [{ field: "save", text: `Not saved: ${String(e.message || e)}. Your entries are kept.` }];
        redraw();
      }
    };
    const issues = problems(f);
    return h("div", { class: "tf", "data-type-form": t ? t.type_id : "new" },
      moved ? h("p", { class: "warn small", role: "status" }, `${t.name} changed to v${t.version} while you were editing v${startedFrom}. Your entries are kept; check them against v${t.version} before saving.`) : null,
      !t ? h("p", { class: "small" }, `Kind: ${(KINDS.find(k => k.id === f.kind) || {}).word || "custom"} · `, h("button", { type: "button", class: "linkbtn small", onclick: () => { if (!f.dirty || confirm("Choose another kind? Your entries are discarded.")) { TF[key] = { pick: true }; redraw(); } } }, "choose another kind")) : null,
      look(key, f, E), signals(key, f), details(f), limits(f), addFeature(key, f), advanced(key, f),
      h("div", { class: "tf-summary" }, h("h4", null, "In words"), h("p", { "data-summary": "" }, summary(f)),
        issues.length ? h("ul", { class: "small", "data-missing": String(issues.length) }, issues.map(p => h("li", { class: "warn" }, p.text))) : h("p", { class: "ok small" }, "Nothing missing."),
        err(f, "save"),
        h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit"), onclick: save }, t && t.status === "DRAFT" ? "Save draft" : t ? "Save as new draft version" : "Save as a draft type"),
          h("span", { class: "muted small" }, "A draft is not used until an administrator activates it. Channels are chosen and verified on each item, on Equipment."))),
      requests(f));
  }
  W.AqTypeForm = { editor, forget: key => { delete TF[key]; }, dirty: key => !!(TF[key] && TF[key].dirty), summaryOf: (body, meta) => summary(Object.assign(fromBody(body, meta), { name: meta && meta.name })) };
})();
