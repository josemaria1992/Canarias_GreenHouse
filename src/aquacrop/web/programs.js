/* AquaCrop Programs (0.7.0): watering programs from forms, on the real dashboard. Loaded after app.js (AqUI).

   New program, in six short steps with a summary that follows every choice:
     1 Where?  a validated block            2 How much?  time, measured volume, or by hand within a bound
     3 When?   run now, or days and times (date range, repeats within a window)
     4 Conditions  start conditions and stop conditions as rows, with units and persistence
     5 Preview the exact sequence the engine will run (compiled by the server from this form)
     6 Save the draft (runs nothing), validate, and test it in the digital twin.
   Programs written before 0.7.0 keep their definition: they open read-only, with their full definition shown, and
   are never converted silently. Messages appear beside the button that caused them; no pop-ups. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, pill, can, mount, S, VIEWS } = U;
  const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  const ST = { VALIDATED: "ok", DRAFT: "warn", INVALID: "bad" };
  const P0 = S.progUi = S.progUi || { form: null, msg: {}, preview: null, open: {} };

  function note(key) { const m = P0.msg[key]; return m ? h("span", { class: "inline-msg " + (m.bad ? "bad" : "ok"), role: "status" }, m.text) : null; }
  function tell(key, text, bad) { P0.msg[key] = { text, bad: !!bad }; }
  async function call(key, method, path, body, ok) {
    try { const r = await api(method, path, body); if (ok) tell(key, ok); return r; }
    catch (e) { tell(key, String(e.message || e), true); if (e.detail && e.detail.field) P0.badField = e.detail.field; return null; }
  }

  // ---------------------------------------------------------------- the form
  function emptyForm(P) {
    const b = P.blocks.find(x => x.status === "VALIDATED");
    return { program_id: "", name: "", version: null, form: { form: "WATERING", form_version: 1, block_id: b ? b.block_id : "", block_version: b ? b.version : 1,
      endpoint: { kind: "TIME", minutes: null, litres: null, max_minutes: null, no_flow_s: 20, min_flow_L_min: 0.5 },
      schedule: { kind: "NOW_ONLY", days: [], times: [], from_date: null, to_date: null, repeat: null },
      permissives: [], guards: [], description: "" } };
  }
  function num(v) { return v === "" || v == null ? null : Number(v); }
  function field(label, input, key) {
    return h("label", { class: P0.badField === key ? "bad-field" : null }, label, " ", input);
  }
  function radio(name, value, cur, label, on) {
    return h("label", { class: "radio" }, h("input", { type: "radio", name, value, checked: cur === value ? true : null, onchange: () => { on(value); U.refresh(); } }), " ", label);
  }
  function blockStep(P, F) {
    const f = F.form;
    const usable = P.blocks.filter(b => b.status === "VALIDATED");
    const others = P.blocks.filter(b => b.status !== "VALIDATED" && b.status !== "RETIRED");
    return h("fieldset", null, h("legend", null, "1. Where?"),
      usable.length ? usable.map(b => radio("prog-block", b.block_id, f.block_id, `${b.name} — ${b.summary}`, v => { f.block_id = v; f.block_version = b.version; }))
        : h("p", { class: "warn small" }, "No validated block yet. Create and validate one on Blocks: a program waters through a block."),
      others.length ? h("p", { class: "muted small" }, "Not available yet: ", others.map(b => `${b.name} (${b.status === "DRAFT" ? "draft: validate it on Blocks" : b.status})`).join("; ")) : null);
  }
  function endpointStep(P, F) {
    const e = F.form.endpoint, blk = P.blocks.find(b => b.block_id === F.form.block_id);
    const hasMeter = blk && blk.body.meter;
    const n = (key, label, unit, min, max, step) => field(`${label}${unit ? " (" + unit + ")" : ""}`, h("input", { type: "number", min, max, step: step || "any", class: "w-num", value: e[key] == null ? "" : e[key],
      "aria-label": label, oninput: ev => { e[key] = num(ev.target.value); } }), "endpoint." + key);
    return h("fieldset", null, h("legend", null, "2. How much?"),
      radio("prog-end", "TIME", e.kind, "Run for a time", v => { e.kind = v; }),
      radio("prog-end", "VOLUME", e.kind, hasMeter ? "Deliver a measured volume" : "Deliver a measured volume (this block has no flow meter)", v => { e.kind = v; }),
      radio("prog-end", "MANUAL", e.kind, "Run by hand until stopped, within a maximum", v => { e.kind = v; }),
      h("div", { class: "row wrap" },
        e.kind === "TIME" ? n("minutes", "Watering time after the startup steps", "min", 0.1, 1440) : null,
        e.kind === "VOLUME" ? n("litres", "Volume to deliver", "L", 0.1, 1000000) : null,
        e.kind === "VOLUME" ? n("no_flow_s", "Stop if no flow is measured for", "s", 1, 600, 1) : null,
        n("max_minutes", "Maximum for the whole operation", "min", 0.1, 1440)),
      e.kind === "VOLUME" && !hasMeter ? h("p", { class: "warn small" }, "A volume is measured by the block's inline flow meter. Add one to the block, or run for a time.") : null,
      h("p", { class: "muted small" }, "The maximum is a guard: the operation stops through the block's stop plan when it is reached, and a volume not reached is reported as incomplete, never as done."));
  }
  function scheduleStep(P, F) {
    const s = F.form.schedule;
    const timeIn = h("input", { type: "time", "aria-label": "Start time to add" });
    return h("fieldset", null, h("legend", null, "3. When?"),
      radio("prog-when", "NOW_ONLY", s.kind, "Only when someone starts it", v => { s.kind = v; s.days = []; s.times = []; s.repeat = null; }),
      radio("prog-when", "WEEKLY", s.kind, "On chosen days at chosen times", v => { s.kind = v; if (!s.days.length) s.days = DAYS.slice(); }),
      s.kind === "WEEKLY" ? [
        h("div", { class: "row wrap", role: "group", "aria-label": "Days" }, DAYS.map(d => h("label", { class: "radio" },
          h("input", { type: "checkbox", checked: s.days.includes(d) ? true : null, onchange: ev => { s.days = ev.target.checked ? DAYS.filter(x => x === d || s.days.includes(x)) : s.days.filter(x => x !== d); } }), " ", d.slice(0, 1) + d.slice(1).toLowerCase()))),
        h("div", { class: "row wrap" }, s.times.map(t => h("span", { class: "chip" }, t, " ", h("button", { class: "small ghost", "aria-label": `Remove start ${t}`, onclick: () => { s.times = s.times.filter(x => x !== t); U.refresh(); } }, "×"))),
          timeIn, h("button", { class: "small", onclick: () => { if (timeIn.value && !s.times.includes(timeIn.value)) { s.times = s.times.concat([timeIn.value]).sort(); U.refresh(); } } }, "Add start time")),
        h("div", { class: "row wrap" }, field("From date (optional)", h("input", { type: "date", value: s.from_date || "", onchange: ev => { s.from_date = ev.target.value || null; } }), "schedule.from_date"),
          field("To date (optional)", h("input", { type: "date", value: s.to_date || "", onchange: ev => { s.to_date = ev.target.value || null; } }), "schedule.to_date")),
        h("label", { class: "radio" }, h("input", { type: "checkbox", checked: s.repeat ? true : null, onchange: ev => { s.repeat = ev.target.checked ? { rest_minutes: 30, window_end: "18:00", max_count: 4 } : null; U.refresh(); } }),
          " Repeat inside a window: wait after each completed watering, then water again"),
        s.repeat ? h("div", { class: "row wrap" },
          field("Rest after each completion (min)", h("input", { type: "number", min: 1, max: 1440, class: "w-num", value: s.repeat.rest_minutes, oninput: ev => { s.repeat.rest_minutes = num(ev.target.value); } }), "schedule.repeat"),
          field("Window ends at", h("input", { type: "time", value: s.repeat.window_end, onchange: ev => { s.repeat.window_end = ev.target.value; } }), "schedule.repeat"),
          field("At most (times)", h("input", { type: "number", min: 1, max: 96, step: 1, class: "w-num", value: s.repeat.max_count, oninput: ev => { s.repeat.max_count = num(ev.target.value); } }), "schedule.repeat"),
          h("p", { class: "muted small" }, "Nothing starts after the window end, and a watering still running then is stopped through the block's stop plan.")) : null,
        h("p", { class: "muted small" }, `Times are ${P.timezone}. A start in the spring clock change gap starts at 03:00 with its full length; the repeated autumn hour runs once.`)] : null);
  }
  function ruleRows(P, list, title, help) {
    const sensors = P.readings;
    return h("div", null, h("h4", null, title), h("p", { class: "muted small" }, help),
      list.map((r, i) => {
        const opts = sensors.find(x => x.item_id === r.item && x.role === r.role);
        const analog = !opts || opts.signal === "ANALOG";
        return h("div", { class: "row wrap rule" },
          h("select", { "aria-label": `${title} ${i + 1} reading`, onchange: ev => { const [it, ro] = ev.target.value.split("|"); r.item = it; r.role = ro; const o = sensors.find(x => x.item_id === it && x.role === ro); r.unit = o && o.unit; r.op = o && o.signal === "ANALOG" ? "ABOVE" : "IS_ON"; r.value = o && o.signal === "ANALOG" ? r.value : null; U.refresh(); } },
            h("option", { value: "|" }, "— choose a reading —"), sensors.map(x => h("option", { value: `${x.item_id}|${x.role}`, selected: x.item_id === r.item && x.role === r.role ? true : null }, `${x.name}: ${x.label}${x.unit ? " (" + x.unit + ")" : ""}`))),
          h("select", { "aria-label": `${title} ${i + 1} comparison`, onchange: ev => { r.op = ev.target.value; } },
            (analog ? [["ABOVE", "is above"], ["BELOW", "is below"]] : [["IS_ON", "is on"], ["IS_OFF", "is off"]]).map(([v, t]) => h("option", { value: v, selected: r.op === v ? true : null }, t))),
          analog ? h("label", null, h("input", { type: "number", step: "any", class: "w-num", value: r.value == null ? "" : r.value, "aria-label": `${title} ${i + 1} value`, oninput: ev => { r.value = num(ev.target.value); } }), ` ${r.unit || ""}`) : null,
          h("label", null, "for ", h("input", { type: "number", min: 0, max: 3600, class: "w-num", value: r.persist_s, "aria-label": `${title} ${i + 1} persistence`, oninput: ev => { r.persist_s = num(ev.target.value) || 0; } }), " s"),
          h("button", { class: "small ghost", "aria-label": `Remove ${title} ${i + 1}`, onclick: () => { list.splice(i, 1); U.refresh(); } }, "Remove"));
      }),
      h("button", { class: "small", disabled: !sensors.length, onclick: () => { list.push({ item: "", role: "", op: "ABOVE", value: null, unit: null, persist_s: 5 }); U.refresh(); } }, `Add ${title.toLowerCase().replace(/s$/, "")}`));
  }
  function conditionsStep(P, F) {
    return h("fieldset", null, h("legend", null, "4. Conditions"),
      ruleRows(P, F.form.permissives, "Start conditions", "All must hold when it starts. A reading that is missing or stale counts as not holding: the watering does not start."),
      ruleRows(P, F.form.guards, "Stop conditions", "Any one stops the watering through the block's stop plan and raises an alarm. A missing or stale reading also stops it."));
  }
  function previewBox(P, F) {
    const pv = P0.preview;
    if (!pv) return h("p", { class: "muted small" }, "Press Preview to see exactly what will run.");
    return h("div", { class: "preview", "data-preview": "" },
      h("p", null, h("strong", null, pv.summary.text)),
      pv.summary.next.length ? h("p", { class: "small" }, "Next starts: ", pv.summary.next.join(" · ")) : null,
      pv.summary.start_conditions.length ? h("p", { class: "small" }, "Starts only if: ", pv.summary.start_conditions.join("; and ")) : null,
      pv.summary.stop_conditions.length ? h("p", { class: "small" }, "Stops if: ", pv.summary.stop_conditions.join("; or ")) : null,
      h("ol", { class: "small" }, pv.steps.map(s => h("li", null, s))),
      h("p", { class: "small" }, `If something fails: ${pv.fault_stop}`),
      pv.interrupted_stop ? h("p", { class: "small" }, `If it is stopped early: ${pv.interrupted_stop}`) : null,
      h("p", { class: "small" }, "Equipment it holds while running: ", pv.resources.join(", ") || "none"),
      pv.conflicts.length ? h("p", { class: "warn small" }, "Shares equipment with: ", pv.conflicts.join("; "), ". A conflict is resolved when it happens; nothing is taken over silently.") : null,
      h("p", { class: "muted small" }, `Before it can run on real equipment: ${pv.physical_needs.length ? pv.physical_needs.join("; ") : "nothing more from the program; the bench session and approvals still apply."}`));
  }
  function programForm(P) {
    const F = P0.form, f = F.form;
    const preview = async () => {
      P0.badField = null;
      const r = await call("pv", "POST", "/api/programs/preview", { form: f });
      P0.preview = r; if (r) tell("pv", "Preview up to date.");
      U.refresh();
    };
    const save = async () => {
      P0.badField = null;
      const r = await call("save", "POST", "/api/programs/form", { program_id: F.program_id, name: F.name, form: f, version: F.version });
      if (r) { F.version = r.version; F.status = r.status; tell("save", "Saved as a draft. Nothing runs from a draft."); }
      U.refresh();
    };
    const validate = async () => {
      if (!F.version) { tell("save", "Save it first.", true); U.refresh(); return; }
      const r = await call("save", "POST", "/api/programs/validate", { program_id: F.program_id, version: F.version });
      if (r) tell("save", r.issues.length ? "Not validated: " + r.issues.join("; ") : "Validated. Next: test it in the digital twin.", !!r.issues.length);
      U.refresh();
    };
    return h("div", { class: "card", "data-program-form": "" },
      h("h2", null, F.version ? `${F.name} (v${F.version})` : "New program"),
      h("div", { class: "row wrap" }, field("Name", h("input", { maxlength: 80, value: F.name, "aria-label": "Program name", oninput: ev => { F.name = ev.target.value; } }), "name"),
        field("Id", h("input", { maxlength: 48, value: F.program_id, disabled: F.version ? true : null, placeholder: "morning-sector-1", "aria-label": "Program id", oninput: ev => { F.program_id = ev.target.value; } }), "program_id")),
      blockStep(P, F), endpointStep(P, F), scheduleStep(P, F), conditionsStep(P, F),
      h("fieldset", null, h("legend", null, "5. Preview"), h("button", { onclick: preview }, "Preview"), note("pv"), previewBox(P, F)),
      h("fieldset", null, h("legend", null, "6. Save, validate, test"),
        h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit"), onclick: save }, "Save draft"),
          h("button", { disabled: !can("equipment_edit"), onclick: validate }, "Validate"),
          h("button", { class: "ghost", onclick: () => { P0.form = null; P0.preview = null; U.refresh(); } }, "Close"), note("save")),
        h("p", { class: "muted small" }, "Then open the Digital Twin, run this version there and accept its result. Real runs also need the bench session or an approved activation; this page never switches anything.")));
  }

  // ---------------------------------------------------------------- the list
  function card(P, v) {
    const val = v.validation || {}, ctx = val.context || {}, sim = v.simulation;
    const legacy = !v.form;
    const open = P0.open[v.program_id];
    return h("div", { class: "card", "data-program": v.program_id },
      h("div", { class: "row" }, h("h3", null, v.name), pill(`${v.status} v${v.version}`, ST[v.status]),
        sim && sim.passed && sim.current !== false ? pill("TWIN PASSED", "ok") : sim && sim.passed ? pill("TWIN TEST OUTDATED", "warn") : pill("NOT TESTED IN TWIN", "warn"),
        legacy ? pill("EARLIER DEFINITION", "") : null, v.active_on && v.active_on.length ? pill("ACTIVE", "ok") : null),
      h("p", null, v.summary ? v.summary.text : v.describe),
      v.next && v.next.length ? h("p", { class: "small" }, "Next starts: ", v.next.join(" · ")) : null,
      val.issues && val.issues.length ? h("ul", { class: "bad small" }, val.issues.map(x => h("li", null, x))) : null,
      ctx.physical_needs && ctx.physical_needs.length && can("ops_read") ? h("details", null, h("summary", { class: "small" }, `Before a physical test (${ctx.physical_needs.length})`), h("ul", { class: "small" }, ctx.physical_needs.map(x => h("li", null, x)))) : null,
      sim && sim.passed ? h("p", { class: "muted small" }, `Twin result accepted by ${sim.accepted_by} (${new Date(sim.accepted_utc).toLocaleString()}): ${sim.meaning}.`) : null,
      sim && sim.passed && sim.current === false ? h("p", { class: "small warn" }, sim.why_not_current) : null,
      can("equipment_edit") ? h("div", { class: "row" },
        !legacy ? h("button", { class: "small", onclick: () => { P0.form = { program_id: v.program_id, name: v.name, version: v.status === "DRAFT" ? v.version : null, status: v.status, form: JSON.parse(JSON.stringify(v.form)) }; P0.preview = null; U.refresh(); } },
          v.status === "DRAFT" ? "Edit" : "Change (new version)") : null,
        h("button", { class: "small ghost", onclick: () => { P0.open[v.program_id] = !open; U.refresh(); } }, open ? "Hide definition" : "Show definition"),
        h("a", { class: "small", href: "#operation" }, "Run or stop on Operation")) : null,
      legacy && can("equipment_edit") ? h("p", { class: "muted small" }, "Written before the forms: it keeps its definition and runs as before. It is shown here read-only; a form version is a new program you write and test.") : null,
      open ? h("pre", { class: "small mono" }, JSON.stringify(v.body, null, 2)) : null);
  }

  VIEWS.programs = async function () {
    const P = await api("GET", "/api/programs");
    const latest = {};
    for (const v of P.programs) if (!latest[v.program_id] || latest[v.program_id].version < v.version) latest[v.program_id] = v;
    mount("v-programs",
      h("div", { class: "card" }, h("h2", null, "Programs"),
        h("p", { class: "muted" }, "Watering by time or measured volume, on a schedule, through a block. Saving runs nothing; each version is tested in the digital twin first."),
        can("equipment_edit") ? h("div", { class: "row" }, h("button", { disabled: !!P0.form || !P.blocks, onclick: () => { P0.form = emptyForm(P); P0.preview = null; U.refresh(); } }, "New program")) : null,
        P.live_activation && can("ops_read") ? h("p", { class: "warn small" }, P.live_activation) : null),
      P0.form && can("equipment_edit") ? programForm(P) : null,
      Object.values(latest).filter(v => v.status !== "RETIRED").sort((a, b) => a.name.localeCompare(b.name)).map(v => card(P, v)),
      Object.keys(latest).length ? null : h("div", { class: "card" }, h("p", { class: "muted" }, "No program yet.")));
  };
})();
