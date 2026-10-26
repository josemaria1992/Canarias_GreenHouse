/* AquaCrop Programs (0.7.0, simplified in 0.7.1 by the owner's decisions): watering programs from forms, on the real
   dashboard. Loaded after app.js (AqUI) and ids.js.

   New program, in six short steps with a summary that follows every choice:
     1 Where?      a validated block (its safety settings hold the maximum runtime, configured once on Blocks)
     2 How much?   water for a time, or deliver litres (measured): one endpoint per cycle
     3 When?       run now, or days with watering windows (start and finish) and the wait after each cycle
     4 Conditions  start conditions and stop conditions as rows, with units and persistence
     5 Preview the exact sequence the engine will run (compiled by the server from this form)
     6 Save the draft (runs nothing), validate, and test it in the digital twin.
   Name then ID: the ID follows the name (Morning 1 -> Morning_1) until edited, and stays editable after creation.
   Programs written before the forms keep their definition: they open read-only and are never converted silently.
   A program written with the 0.7.0 form opens in this form as a new draft to review (windows from its start times and
   guard). Messages appear beside the button that caused them; no pop-ups. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, pill, can, mount, S, VIEWS } = U;
  const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  const ST = { VALIDATED: "ok", DRAFT: "warn", INVALID: "bad" };
  const STATE_WORD = { WATERING: ["Watering", "ok"], STOPPING: ["Stopping", "warn"], STOP_UNVERIFIED: ["Stop not verified: holding", "bad"],
    WAITING: ["Waiting", "warn"], RESTING: ["Resting before the next cycle", ""], IDLE: ["Waiting for its next window", ""] };
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
    return { program_id: "", public_id: "", name: "", version: null, form: { form: "WATERING", form_version: 2, block_id: b ? b.block_id : "", block_version: b ? b.version : 1,
      endpoint: { kind: "TIME", minutes: null, litres: null, no_flow_s: 20, min_flow_L_min: 0.5 },
      schedule: { kind: "WEEKLY", days: DAYS.slice(), windows: [], rest_minutes: null, max_cycles: null, from_date: null, to_date: null },
      permissives: [], guards: [], description: "" } };
  }
  const hhmm = m => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const mins = t => { const [a, b] = t.split(":").map(Number); return a * 60 + b; };
  /** A 0.7.0 form (start times, a guard per program) as this form: a draft the owner reviews before saving. */
  function fromV1(f) {
    const g = JSON.parse(JSON.stringify(f)), e = g.endpoint, s = g.schedule;
    const guard = e.max_minutes || 60;
    delete e.max_minutes;
    if (e.kind === "MANUAL") { e.kind = "TIME"; e.minutes = null; }
    if (s.kind === "WEEKLY") {
      s.windows = s.repeat ? [{ start: s.times[0], finish: s.repeat.window_end }] : (s.times || []).map(t => ({ start: t, finish: hhmm(mins(t) + Math.ceil(guard)) }));
      s.rest_minutes = s.repeat ? s.repeat.rest_minutes : 60;
      s.max_cycles = s.repeat ? s.repeat.max_count : 1;
    }
    delete s.times; delete s.repeat;
    g.form_version = 2;
    return g;
  }
  function num(v) { return v === "" || v == null ? null : Number(v); }
  function field(label, input, key) {
    return h("label", { class: P0.badField === key ? "bad-field" : null }, label, " ", input);
  }
  function radio(name, value, cur, label, on) {
    return h("label", { class: "radio" }, h("input", { type: "radio", name, value, checked: cur === value ? true : null, onchange: () => { on(value); U.refresh(); } }), label);
  }
  function blockOf(P, F) { return P.blocks.find(b => b.block_id === F.form.block_id); }
  function safetyText(blk) {
    const s = blk && blk.body && blk.body.safety && blk.body.safety.max_runtime_s;
    return s ? `Safety (set on the block): one operation never runs longer than ${Math.round(s / 6) / 10} min, startup to verified stop.`
      : null;
  }
  function blockStep(P, F) {
    const f = F.form;
    const usable = P.blocks.filter(b => b.status === "VALIDATED");
    const others = P.blocks.filter(b => b.status !== "VALIDATED" && b.status !== "RETIRED");
    const blk = blockOf(P, F);
    return h("fieldset", null, h("legend", null, "1. Where?"),
      usable.length ? usable.map(b => radio("prog-block", b.block_id, f.block_id, `${b.name} — ${b.summary}`, v => { f.block_id = v; f.block_version = b.version; }))
        : h("p", { class: "warn small" }, "No validated block yet. Create and validate one on Blocks: a program waters through a block."),
      blk ? (safetyText(blk) ? h("p", { class: "muted small" }, safetyText(blk))
        : h("p", { class: "warn small" }, `${blk.name} has no maximum runtime yet. Set it once in its safety settings on Blocks; a program cannot be saved over it until then.`)) : null,
      others.length ? h("p", { class: "muted small" }, "Not available yet: ", others.map(b => `${b.name} (${b.status === "DRAFT" ? "draft: validate it on Blocks" : b.status})`).join("; ")) : null);
  }
  function endpointStep(P, F) {
    const e = F.form.endpoint, blk = blockOf(P, F);
    const hasMeter = blk && blk.body.meter;
    const n = (key, label, unit, min, max, step) => field(`${label}${unit ? " (" + unit + ")" : ""}`, h("input", { type: "number", min, max, step: step || "any", class: "w-num", value: e[key] == null ? "" : e[key],
      "aria-label": label, oninput: ev => { e[key] = num(ev.target.value); } }), "endpoint." + key);
    return h("fieldset", null, h("legend", null, "2. How much?"),
      h("div", { class: "row wrap" },
        radio("prog-end", "TIME", e.kind, "Water for", v => { e.kind = v; e.litres = null; }),
        e.kind === "TIME" ? n("minutes", "Minutes per cycle", "min", 0.1, 1440) : null),
      h("div", { class: "row wrap" },
        radio("prog-end", "VOLUME", e.kind, hasMeter ? "Deliver" : "Deliver (this block has no flow meter)", v => { e.kind = v; e.minutes = null; }),
        e.kind === "VOLUME" ? [n("litres", "Litres per cycle", "L", 0.1, 1000000), n("no_flow_s", "Stop if no flow is measured for", "s", 1, 600, 1)] : null),
      e.kind === "VOLUME" && !hasMeter ? h("p", { class: "warn small" }, "Litres are measured by the block's inline flow meter. Add one to the block, or water for a time.") : null,
      h("p", { class: "muted small" }, e.kind === "VOLUME" ? "A litre cycle ends when the meter has measured the litres. One cut short by its window or a stop is reported as incomplete, never as done."
        : "The time is watering time after the block's startup steps."));
  }
  function scheduleStep(P, F) {
    const s = F.form.schedule;
    const st = h("input", { type: "time", "aria-label": "New window start" }), fi = h("input", { type: "time", "aria-label": "New window finish" });
    return h("fieldset", null, h("legend", null, "3. When?"),
      radio("prog-when", "NOW_ONLY", s.kind, "Only when someone starts it", v => { s.kind = v; s.days = []; s.windows = []; s.rest_minutes = null; s.max_cycles = null; }),
      radio("prog-when", "WEEKLY", s.kind, "In watering windows on chosen days", v => { s.kind = v; if (!s.days.length) s.days = DAYS.slice(); }),
      s.kind === "WEEKLY" ? [
        h("div", { class: "row wrap", role: "group", "aria-label": "Days" }, DAYS.map(d => h("label", { class: "check" },
          h("input", { type: "checkbox", checked: s.days.includes(d) ? true : null, onchange: ev => { s.days = ev.target.checked ? DAYS.filter(x => x === d || s.days.includes(x)) : s.days.filter(x => x !== d); } }), d.slice(0, 1) + d.slice(1).toLowerCase()))),
        h("table", { class: "compact", "aria-label": "Watering windows" }, h("thead", null, h("tr", null, ["Window start", "Window finish", ""].map(x => h("th", null, x)))),
          h("tbody", null, s.windows.map((w, i) => h("tr", null,
            h("td", null, h("input", { type: "time", value: w.start, "aria-label": `Window ${i + 1} start`, onchange: ev => { w.start = ev.target.value; } })),
            h("td", null, h("input", { type: "time", value: w.finish, "aria-label": `Window ${i + 1} finish`, onchange: ev => { w.finish = ev.target.value; } })),
            h("td", null, h("button", { class: "small ghost", "aria-label": `Remove window ${i + 1}`, onclick: () => { s.windows.splice(i, 1); U.refresh(); } }, "Remove")))),
            h("tr", null, h("td", null, st), h("td", null, fi), h("td", null, h("button", { class: "small", onclick: () => {
              if (!st.value || !fi.value) { tell("win", "Give the start and the finish of the window.", true); U.refresh(); return; }
              s.windows = s.windows.concat([{ start: st.value, finish: fi.value }]).sort((a, b) => a.start.localeCompare(b.start)); tell("win", null); delete P0.msg.win; U.refresh();
            } }, "Add window"), note("win"))))),
        h("div", { class: "row wrap" },
          field("Wait after each completed cycle (min)", h("input", { type: "number", min: 1, max: 1440, class: "w-num", value: s.rest_minutes == null ? "" : s.rest_minutes, "aria-label": "Wait after each cycle in minutes", oninput: ev => { s.rest_minutes = num(ev.target.value); } }), "schedule.rest_minutes"),
          field("At most cycles per window (optional)", h("input", { type: "number", min: 1, max: 96, step: 1, class: "w-num", value: s.max_cycles == null ? "" : s.max_cycles, "aria-label": "Most cycles per window", oninput: ev => { s.max_cycles = num(ev.target.value); } }), "schedule.max_cycles")),
        h("div", { class: "row wrap" }, field("From date (optional)", h("input", { type: "date", value: s.from_date || "", onchange: ev => { s.from_date = ev.target.value || null; } }), "schedule.from_date"),
          field("To date (optional)", h("input", { type: "date", value: s.to_date || "", onchange: ev => { s.to_date = ev.target.value || null; } }), "schedule.to_date")),
        h("p", { class: "muted small" }, "Each window starts its first cycle at once; the next cycle starts the wait after the previous one ended verified. At the finish a running cycle is stopped. A window right after another starts its own first cycle as soon as the earlier one is verified stopped, without the earlier wait."),
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
      ruleRows(P, F.form.permissives, "Start conditions", "All must hold when a cycle starts. A reading that is missing or stale counts as not holding: the cycle does not start."),
      ruleRows(P, F.form.guards, "Stop conditions", "Any one stops the watering through the block's stop plan and raises an alarm. A missing or stale reading also stops it."));
  }
  function previewBox(P, F) {
    const pv = P0.preview;
    if (!pv) return h("p", { class: "muted small" }, "Press Preview to see exactly what will run.");
    return h("div", { class: "preview", "data-preview": "" },
      h("p", null, h("strong", null, pv.summary.text)),
      pv.summary.next.length ? h("p", { class: "small" }, "Next windows: ", pv.summary.next.join(" · ")) : null,
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
    const nameIn = h("input", { maxlength: 80, value: F.name, "aria-label": "Program name", oninput: ev => { F.name = ev.target.value; } });
    const idIn = h("input", { maxlength: 48, value: F.public_id || "", class: "mono", placeholder: "Morning_1", "aria-label": "Program ID",
      oninput: ev => { F.public_id = ev.target.value; } });
    if (!F.version && !F.program_id) {
      if (F.idOwned) idIn.dataset.owned = "1";
      window.AqIds.follow(nameIn, idIn, () => { F.public_id = idIn.value; F.idOwned = idIn.dataset.owned === "1"; });
    }
    const preview = async () => {
      P0.badField = null;
      const r = await call("pv", "POST", "/api/programs/preview", { form: f });
      P0.preview = r; if (r) tell("pv", "Preview up to date.");
      U.refresh();
    };
    const save = async () => {
      P0.badField = null;
      const bad = !F.name.trim() ? "Give the program a name." : !F.program_id ? window.AqIds.problem(F.public_id) : null;
      if (bad) { tell("save", bad, true); U.refresh(); return; }
      const r = await call("save", "POST", "/api/programs/form", { program_id: F.program_id || F.public_id, name: F.name, form: f, version: F.version });
      if (r) { F.version = r.version; F.status = r.status; F.program_id = r.program_id; F.public_id = r.public_id; F.id_rev = r.id_rev; tell("save", "Saved as a draft. Nothing runs from a draft."); }
      U.refresh();
    };
    const rename = async () => {
      const bad = window.AqIds.problem(F.public_id);
      if (bad) { tell("id", bad, true); U.refresh(); return; }
      const r = await call("id", "POST", "/api/programs/rename", { program_id: F.program_id, public_id: F.public_id, expected_rev: F.id_rev || 0 },
        `ID is now ${F.public_id}. Its versions, schedule, activation and twin results are unchanged.`);
      if (r) F.id_rev = r.rev;
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
      F.converted ? h("p", { class: "warn small" }, "Opened from the earlier form: its start times became windows (each as long as its old maximum) and its maximum now comes from the block's safety settings. Check the windows and the wait, then save it as a new version.") : null,
      h("div", { class: "row wrap" }, field("Name", nameIn, "name"), field("ID", idIn, "program_id"),
        F.program_id ? [h("button", { class: "small", disabled: !can("equipment_edit"), onclick: rename }, "Change ID"), note("id")] : null),
      blockStep(P, F), endpointStep(P, F), scheduleStep(P, F), conditionsStep(P, F),
      h("fieldset", null, h("legend", null, "5. Preview"), h("button", { onclick: preview }, "Preview"), note("pv"), previewBox(P, F)),
      h("fieldset", null, h("legend", null, "6. Save, validate, test"),
        h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit"), onclick: save }, "Save draft"),
          h("button", { disabled: !can("equipment_edit"), onclick: validate }, "Validate"),
          h("button", { class: "ghost", onclick: () => { P0.form = null; P0.preview = null; U.refresh(); } }, "Close"), note("save")),
        h("p", { class: "muted small" }, "Then open the Digital Twin, run this version there and accept its result. Real runs also need the bench session or an approved activation; this page never switches anything.")));
  }

  // ---------------------------------------------------------------- the list
  function stateLine(O, v) {
    const st = O && O.programs ? O.programs[v.program_id] : null;
    if (!st) return null;
    const [word, cls] = STATE_WORD[st.state] || [st.state, ""];
    const extra = st.why ? `: ${st.why}` : st.next_cycle_in_s != null ? ` (next cycle in ${Math.ceil(st.next_cycle_in_s / 60)} min)` : "";
    return h("p", { class: "small", "data-program-state": st.state }, pill(word, cls), extra);
  }
  function card(P, O, v) {
    const val = v.validation || {}, ctx = val.context || {}, sim = v.simulation;
    const legacy = !v.form;
    const open = P0.open[v.program_id];
    const edit = () => {
      const v1 = v.form.form_version === 1;
      P0.form = { program_id: v.program_id, public_id: v.public_id || v.program_id, id_rev: v.id_rev || 0, name: v.name,
        version: v.status === "DRAFT" && !v1 ? v.version : null, status: v.status, converted: v1,
        form: v1 ? fromV1(v.form) : JSON.parse(JSON.stringify(v.form)) };
      P0.preview = null; U.refresh();
    };
    return h("div", { class: "card", "data-program": v.program_id },
      h("div", { class: "row" }, h("h3", null, v.name), h("span", { class: "mono small" }, v.public_id || v.program_id), pill(`${v.status} v${v.version}`, ST[v.status]),
        sim && sim.passed && sim.current !== false ? pill("TWIN PASSED", "ok") : sim && sim.passed ? pill("TWIN TEST OUTDATED", "warn") : pill("NOT TESTED IN TWIN", "warn"),
        legacy ? pill("EARLIER DEFINITION", "") : null, v.active_on && v.active_on.length ? pill("ACTIVE", "ok") : null),
      h("p", null, v.summary ? v.summary.text : v.describe),
      stateLine(O, v),
      v.next && v.next.length ? h("p", { class: "small" }, "Next windows: ", v.next.join(" · ")) : null,
      val.issues && val.issues.length ? h("ul", { class: "bad small" }, val.issues.map(x => h("li", null, x))) : null,
      ctx.physical_needs && ctx.physical_needs.length && can("ops_read") ? h("details", null, h("summary", { class: "small" }, `Before a physical test (${ctx.physical_needs.length})`), h("ul", { class: "small" }, ctx.physical_needs.map(x => h("li", null, x)))) : null,
      sim && sim.passed ? h("p", { class: "muted small" }, `Twin result accepted by ${sim.accepted_by} (${new Date(sim.accepted_utc).toLocaleString()}): ${sim.meaning}.`) : null,
      sim && sim.passed && sim.current === false ? h("p", { class: "small warn" }, sim.why_not_current) : null,
      can("equipment_edit") ? h("div", { class: "row" },
        !legacy ? h("button", { class: "small", onclick: edit }, v.status === "DRAFT" && v.form.form_version !== 1 ? "Edit" : "Change (new version)") : null,
        h("button", { class: "small ghost", onclick: () => { P0.open[v.program_id] = !open; U.refresh(); } }, open ? "Hide definition" : "Show definition"),
        h("a", { class: "small", href: "#operation" }, "Run or stop on Operation")) : null,
      legacy && can("equipment_edit") ? h("p", { class: "muted small" }, "Written before the forms: it keeps its definition and runs as before. It is shown here read-only; a form version is a new program you write and test.") : null,
      open ? h("pre", { class: "small mono" }, JSON.stringify(v.body, null, 2)) : null);
  }

  VIEWS.programs = async function () {
    const [P, O] = await Promise.all([api("GET", "/api/programs"), can("ops_read") ? api("GET", "/api/operation").catch(() => null) : null]);
    const latest = {};
    for (const v of P.programs) if (!latest[v.program_id] || latest[v.program_id].version < v.version) latest[v.program_id] = v;
    mount("v-programs",
      h("div", { class: "card" }, h("h2", null, "Programs"),
        h("p", { class: "muted" }, "Watering by time or litres, in windows, through a block. Saving runs nothing; each version is tested in the digital twin first."),
        can("equipment_edit") ? h("div", { class: "row" }, h("button", { disabled: !!P0.form || !P.blocks, onclick: () => { P0.form = emptyForm(P); P0.preview = null; U.refresh(); } }, "New program")) : null,
        P.live_activation && can("ops_read") ? h("p", { class: "warn small" }, P.live_activation) : null),
      P0.form && can("equipment_edit") ? programForm(P) : null,
      Object.values(latest).filter(v => v.status !== "RETIRED").sort((a, b) => a.name.localeCompare(b.name)).map(v => card(P, O && O.available ? O : null, v)),
      Object.keys(latest).length ? null : h("div", { class: "card" }, h("p", { class: "muted" }, "No program yet.")));
  };
})();
