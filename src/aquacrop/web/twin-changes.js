/* Equipment and programs changed in the digital twin, and their export to the real system (0.6.0).
 * Changes are made here, run in the twin, and sent with their proof (the twin runs of every program version).
 * The real dashboard shows the differences; an administrator or an operator with commissioning approves them.
 * Results and refusals are text beside the buttons (owner preference). Loaded after twin.js. */
"use strict";
(function () {
  const T = window.AqTwin, { h, api, can } = T;
  const C = { card: null, body: null, data: null, twin: null, msg: {}, open: null, last: 0, prog: null, progOpen: false,
    eqEl: null, progEl: null, expEl: null };
  const ICONS = ["pump", "valve", "tank", "level", "flow", "pressure", "temperature", "button", "lamp", "fan", "heater", "motor", "sensor", "switch", "drop", "gauge", "plug", "generic"];

  function say(k, text, bad) { C.msg[k] = { text, bad }; draw(); }
  const msg = k => C.msg[k] ? h("span", { class: C.msg[k].bad ? "error small" : "muted small", role: "status" }, C.msg[k].text) : null;
  async function call(k, path, body, ok) {
    try { const r = await api("POST", path, body); C.msg[k] = { text: ok || "done" }; await load(); draw(); return r; }
    catch (e) { say(k, String(e.message || e), true); return null; }
  }
  // the program editor is drawn apart: the equipment and export parts refresh every few seconds without touching it
  function sayProg(text, bad) { C.msg.prog = { text, bad }; drawProg(); }
  async function load() { C.data = await api("GET", "api/twin/changes"); }

  function card() {
    if (!C.card) {
      C.eqEl = h("div"); C.progEl = h("div"); C.expEl = h("div");
      C.body = h("div", null, C.eqEl, C.progEl, C.expEl);
      C.card = h("div", { class: "card", id: "twin-changes" }, C.body);
    }
    return C.card;
  }
  async function update(v) {
    C.twin = v;
    const ae = document.activeElement;
    const first = !C.data;
    if (C.open || (ae && (C.eqEl.contains(ae) || C.expEl.contains(ae)) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName))) return;
    if (Date.now() - C.last < 3000) return;
    C.last = Date.now();
    try { await load(); } catch (e) { return; }
    draw();
    if (first) drawProg();
  }

  // ---------------------------------------------------------------- equipment
  function typeOf(i) { return C.data.types.find(t => t.type_id === i.type_id && t.version === i.type_version); }
  function itemForm(i) {
    const ed = can("equipment_edit"), isNew = !i;
    const types = C.data.types.filter(t => t.status === "ACTIVE");
    const id = h("input", { maxlength: 48, value: i ? i.item_id : "", disabled: !isNew, placeholder: "pump-3", "aria-label": "Equipment id" });
    const name = h("input", { maxlength: 80, value: i ? i.name : "", placeholder: "Pump 3", "aria-label": "Name" });
    const type = h("select", { "aria-label": "Type" }, (i ? C.data.types.filter(t => t.type_id === i.type_id || t.status === "ACTIVE") : types)
      .map(t => h("option", { value: `${t.type_id}@${t.version}`, selected: i && t.type_id === i.type_id && t.version === i.type_version }, `${t.name} v${t.version}`)));
    const icon = h("select", { "aria-label": "Icon" }, ICONS.map(x => h("option", { selected: (i ? i.icon : "generic") === x }, x)));
    type.addEventListener("change", () => { const t = C.data.types.find(t => `${t.type_id}@${t.version}` === type.value); if (t && isNew) icon.value = t.default_icon; });
    const save = () => { const [tid, tv] = type.value.split("@");
      call(isNew ? "new" : "it-" + i.item_id, "api/twin/equipment/save", { item_id: id.value.trim(), name: name.value, type_id: tid, type_version: Number(tv), icon: icon.value, body: i ? i.body : {} },
        isNew ? "added in the twin" : "changed in the twin").then(r => { if (r) C.open = null; draw(); }); };
    return h("div", { class: "row" }, h("label", { class: "small" }, "Id ", id), h("label", { class: "small" }, "Name ", name),
      h("label", { class: "small" }, "Type ", type), h("label", { class: "small" }, "Icon ", icon),
      h("button", { class: "small", disabled: !ed, onclick: save }, isNew ? "Add in the twin" : "Save in the twin"),
      h("button", { class: "small ghost", onclick: () => { C.open = null; draw(); } }, "Cancel"), msg(isNew ? "new" : "it-" + i.item_id));
  }
  function channelSelect(i, role) {
    const cur = i.bindings[role.role] || "";
    const used = new Set(C.data.items.flatMap(x => Object.entries(x.bindings).filter(([r]) => !(x.item_id === i.item_id && r === role.role)).map(([, p]) => p)));
    const fits = role.points || C.data.points;            // only the channels whose kind and interface suit the role
    return h("select", { "aria-label": `${i.name} ${role.label} channel`, disabled: !can("equipment_edit") || i.status === "RETIRED",
      onchange: ev => call("it-" + i.item_id, "api/twin/equipment/bind", { item_id: i.item_id, role: role.role, point: ev.target.value || null }, "channel changed in the twin") },
      h("option", { value: "" }, "— no channel —"),
      fits.filter(p => !used.has(p) || p === cur).map(p => h("option", { value: p, selected: p === cur }, p)),
      cur && !fits.includes(cur) ? h("option", { value: cur, selected: true }, `${cur} (does not suit)`) : null);
  }
  function equipmentTable() {
    const ed = can("equipment_edit");
    const starter = h("div", { class: "row" },
      h("button", { class: C.data.items.length < 5 ? "" : "ghost", disabled: !ed,
        onclick: () => call("starter", "api/twin/starter", undefined, "").then(r => { if (r) say("starter",
          `${r.created.length} part(s) created${r.skipped ? `, ${r.skipped} already there` : ""}; ${r.links} symbol(s) and pipe(s) linked in the drawing. Assign channels below, then try it and send it to the real system.`); }) },
        "Create the installation's equipment"),
      h("span", { class: "muted small" }, "Every pump, valve and sensor of the standard drawing, linked to its symbols and pipes. Parts that exist stay as they are."),
      msg("starter"));
    return h("div", null, h("h3", null, "Equipment"), starter,
      h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Equipment", "Type", "Channels", "", ""].map(x => h("th", null, x)))),
        h("tbody", null, C.data.items.map(i => {
          const t = typeOf(i), retired = i.status === "RETIRED";
          return [h("tr", { class: retired ? "muted" : "" },
            h("td", null, h("strong", null, i.name), " ", h("span", { class: "muted small" }, i.item_id)),
            h("td", { class: "small" }, t ? `${t.name} v${t.version}` : i.type_id),
            h("td", { class: "small" }, retired ? "retired in the twin" : (t ? t.roles : []).map(r => h("div", null, `${r.label}: `, channelSelect(i, r)))),
            h("td", null, i.changed ? h("span", { class: "pill warn" }, retired ? "RETIRE" : "CHANGED") : null),
            h("td", null,
              !retired ? h("button", { class: "small ghost", disabled: !ed, onclick: () => { C.open = "it-" + i.item_id; draw(); } }, "Edit") : null,
              !retired ? h("button", { class: "small ghost", disabled: !ed, onclick: () => call("it-" + i.item_id, "api/twin/equipment/retire", { item_id: i.item_id }, "retired in the twin") }, "Retire") : null,
              i.changed ? h("button", { class: "small ghost", disabled: !ed, onclick: () => call("it-" + i.item_id, "api/twin/equipment/revert", { item_id: i.item_id }, "back to the real system's") }, "Undo in twin") : null,
              msg("it-" + i.item_id))),
          C.open === "it-" + i.item_id ? h("tr", null, h("td", { colspan: 5 }, itemForm(i))) : null];
        }))),
      C.open === "new" ? itemForm(null) : h("button", { class: "small", disabled: !ed, onclick: () => { C.open = "new"; draw(); } }, "Add equipment"),
      h("p", { class: "muted small" }, "Changes here run in the twin at once. Channels assigned here arrive on the real system as proposed: they are verified at the bench before a physical run."));
  }

  // ---------------------------------------------------------------- programs
  function programEditor() {
    const ed = can("equipment_edit");
    const progs = (C.twin && C.twin.programs) || [];
    const latest = {};
    for (const p of progs) if (!latest[p.program_id] || latest[p.program_id].version < p.version) latest[p.program_id] = p;
    const pick = h("select", { "aria-label": "Program to edit" }, h("option", { value: "" }, "— new program —"),
      Object.values(latest).map(p => h("option", { value: p.program_id, selected: C.prog && C.prog.program_id === p.program_id }, `${p.name} (v${p.version})`)));
    const cur = C.prog || { program_id: "", name: "", body: { trigger: { type: "MANUAL" }, action: { type: "RUN_FOR", item: (C.data.items[0] || {}).item_id, duration_s: 60 } } };
    const id = h("input", { maxlength: 48, value: cur.program_id, placeholder: "water-am", "aria-label": "Program id" });
    const name = h("input", { maxlength: 80, value: cur.name, placeholder: "Morning watering", "aria-label": "Program name" });
    const ta = h("textarea", { rows: 10, spellcheck: "false", "aria-label": "Program definition (JSON)" });
    ta.value = JSON.stringify(cur.body, null, 2);
    pick.addEventListener("change", () => { const p = latest[pick.value]; C.prog = p ? { program_id: p.program_id, name: p.name, body: p.body } : null; drawProg(); });
    const save = async () => {
      let body; try { body = JSON.parse(ta.value); } catch (e) { sayProg("not valid JSON: " + e.message, true); return; }
      let r;
      try { r = await api("POST", "api/twin/programs/draft", { program_id: id.value.trim(), name: name.value, body }); }
      catch (e) { sayProg(String(e.message || e), true); return; }
      C.prog = { program_id: r.program_id, name: r.name, body: r.body };
      sayProg(r.issues.length ? "saved; problems: " + r.issues.join("; ") : `saved as v${r.version} and validated: activate it under Programs and run it`, !!r.issues.length);
      C.last = 0;                                        // the export part shows the new version at its next refresh
    };
    return h("details", { class: "small", open: C.progOpen || null, ontoggle: ev => { C.progOpen = ev.target.open; } },
      h("summary", null, "Change or add a program"),
      h("div", { class: "row" }, h("label", null, "Edit ", pick), h("label", null, "Id ", id), h("label", null, "Name ", name)),
      ta, h("div", { class: "row" }, h("button", { class: "small", disabled: !ed, onclick: save }, "Save and validate in the twin"), msg("prog")),
      h("p", { class: "muted" }, "A saved version gets the twin's next version number. Run it in the twin (Programs below: Activate in twin, then Run now in MANUAL) so the export carries its proof."));
  }

  // ---------------------------------------------------------------- the export
  function exportBox() {
    const pv = C.data.preview, ed = can("equipment_edit");
    const note = h("input", { maxlength: 1000, placeholder: "what changed and why", "aria-label": "Note for the approver" });
    const opWord = { create: "new", update: "changed", retire: "retired" };
    return h("div", { class: "card" }, h("h3", null, "For the real system"),
      pv.empty ? h("p", { class: "muted" }, "No changes against the real system.") : h("div", null,
        pv.items.length ? h("ul", { class: "small" }, pv.items.map(i => h("li", null, `${i.name} (${i.item_id}): ${opWord[i.op]}`,
          i.op !== "retire" ? (i.exercised_by.length ? h("span", { class: "muted" }, ` · run in the twin by ${i.exercised_by.join(", ")}`) : h("span", { class: "warn" }, " · not exercised in the twin")) : null))) : null,
        pv.bindings.length ? h("ul", { class: "small" }, pv.bindings.map(b => h("li", null, `${b.item_id}.${b.role}: ${b.base_point || "no channel"} → ${b.point || "no channel"}`))) : null,
        pv.programs.length ? h("ul", { class: "small" }, pv.programs.map(p => h("li", null, `${p.name}: twin v${p.twin_version}${p.base_version ? ` replaces v${p.base_version}` : " (new)"} · `,
          p.passed ? h("span", { class: "pill ok" }, `${p.runs.filter(r => r.result === "DONE").length} run(s) DONE`) : h("span", { class: "pill bad" }, "no DONE run yet"),
          p.failed ? h("span", { class: "warn" }, ` ${p.failed} not DONE`) : null))) : null,
        pv.missing.length ? h("ul", { class: "bad small" }, pv.missing.map(x => h("li", null, x))) : null,
        h("div", { class: "row" }, note,
          h("button", { disabled: !ed || pv.missing.length > 0, onclick: () => call("export", "api/twin/export", { note: note.value }, "sent: an administrator or an operator with commissioning approves it on the real dashboard (Digital Twin page)") }, "Send to the real system"),
          h("button", { class: "small ghost", disabled: !ed, onclick: () => { if (C.msg.discard && !C.msg.discard.bad) { call("discard", "api/twin/changes/discard", undefined, "equipment changes dropped"); } else say("discard", "Press again to drop every equipment change in the twin."); } }, "Drop equipment changes"),
          msg("export"), msg("discard"))),
      h("p", { class: "muted small" }, "On the real system: equipment becomes a new version, channels arrive as proposed, programs arrive as drafts with this proof. Validating and activating them stays the normal step there. The latest approved export can be undone."));
  }

  function draw() {
    if (!C.body || !C.data) return;
    C.eqEl.replaceChildren(h("h2", null, "Equipment and programs"),
      h("p", { class: "muted small" }, `Started from the real system's copy of ${C.data.snapshot_utc ? new Date(C.data.snapshot_utc).toLocaleString() : "—"}. Import from the real system again to start from its latest state; your changes here are kept.`),
      equipmentTable());
    C.expEl.replaceChildren(exportBox());
  }
  function drawProg() { if (C.progEl && C.data) C.progEl.replaceChildren(programEditor()); }
  window.AqTwinChanges = { card, update };
})();
