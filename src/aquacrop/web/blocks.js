/* AquaCrop Blocks (0.7.0; page structure by the owner's decisions of 26 October 2026, 0.7.1). Loaded after app.js
   (window.AqUI), router.js and hydraulic-editor.js.

   A block associates existing equipment that works together for one purpose and holds the conditions that make
   watering safe: its startup, normal stop and fault stop plans, and (0.7.1) its safety settings with the maximum
   runtime of one operation, configured once here for every program over the block.
   * The installation drawing here is read-only, for orientation: selecting a symbol shows which blocks use it. The
     drawing is designed in Digital Twin and exported to the Overview; equipment is created on Equipment.
   * Members are chosen by equipment type (Valves -> Valve_1, Valve_2). A shared pump is one item in two blocks, never
     a copy: one identity, one channel, one history.
   Messages appear beside the button that caused them. Nothing here starts equipment. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, can, mount, S, VIEWS } = U;
  const KIND_WORD = { source: "water source", tank: "tank", pump: "pump", valve: "valve", meter: "flow meter", sensor: "sensor", sector: "sector", junction: "junction" };
  const GROUPS = [["source", "Water sources"], ["tank", "Tanks"], ["pump", "Pumps"], ["valve", "Valves"], ["meter", "Flow meters"], ["sector", "Sectors"], ["sensor", "Sensors"]];
  const B = S.blocksUi = S.blocksUi || { msg: {}, form: null, sel: null, groupOpen: {} };

  function note(key) { const m = B.msg[key]; return m ? h("span", { class: "inline-msg " + (m.bad ? "bad" : "ok"), role: "status" }, m.text) : null; }
  function tell(key, text, bad) { B.msg[key] = { text, bad: !!bad }; }
  async function call(key, method, path, body, ok) {
    try { const r = await api(method, path, body); if (ok) tell(key, ok); return r; }
    catch (e) {
      const d = e.detail;
      tell(key, String(e.message || e), true);
      if (d && typeof d === "object" && d.issues) B.issues = d.issues;
      return null;
    }
  }
  function issueList(list) {
    if (!list || !list.length) return null;
    return h("ul", { class: "hed-issues" }, list.map(i => h("li", { class: i.severity === "ERROR" ? "bad" : "warn" }, `${i.message}. ${i.fix}.`)));
  }
  const minutes = s => s == null ? null : Math.round(s / 6) / 10;

  // ---------------------------------------------------------------- blocks list
  function blockCard(T, b) {
    const body = b.body, v = b.validation, safe = (body.safety || {}).max_runtime_s;
    const st = b.status === "VALIDATED" ? (b.stale ? U.pill("REVALIDATE", "warn") : U.pill("VALIDATED", "ok")) : b.status === "DRAFT" ? U.pill("DRAFT", "warn") : U.pill(b.status, "");
    const shared = Object.entries(b.shared || {});
    return h("div", { class: "card block-card" + (B.sel && body.members.includes(B.sel) ? " selected-card" : ""), "data-block": b.block_id },
      h("div", { class: "row" }, h("h3", null, b.name), st, h("span", { class: "muted small" }, `${T.templates[body.template] || body.template} · v${b.version}`)),
      h("p", { class: "small" }, `From ${b.names[body.supply] || body.supply || "—"} to ${b.names[body.destination] || body.destination || "—"}`,
        b.path ? ` through ${b.path.nodes.map(n => b.names[n] || n).join(", ") || "a direct pipe"}` : " — no connected path yet",
        body.meter ? ` · measured by ${b.names[body.meter] || body.meter}` : " · no flow meter: timed watering only"),
      h("p", { class: "small", "data-safety": safe ? "set" : "missing" }, safe ? `Safety: one operation never runs longer than ${minutes(safe)} min (startup to verified stop).`
        : h("span", { class: "warn" }, "Safety: no maximum runtime set. Programs cannot be saved over this block until it is set (Change, then Safety settings).")),
      shared.length ? h("p", { class: "small" }, U.pill("SHARED", ""), " ", shared.map(([m, others]) => `${b.names[m] || m} is also in ${others.join(", ")}`).join("; "),
        h("span", { class: "muted" }, " — one item, one owner at a time: an operation claims it while it runs.")) : null,
      b.stale ? h("p", { class: "warn small" }, "The installation's connections changed since this block was validated: validate it again before using it.") : null,
      v && v.warnings && v.warnings.length ? issueList(v.warnings) : null,
      h("div", { class: "row" },
        h("button", { class: "small", disabled: !can("equipment_edit") || !!B.form, onclick: () => { B.form = formFrom(b); B.issues = null; U.refresh(); } }, b.status === "DRAFT" ? "Edit" : "Change (new version)"),
        b.status === "DRAFT" ? h("button", { class: "small", disabled: !can("equipment_edit"), onclick: async () => {
          await call("v:" + b.block_id, "POST", "/api/blocks/validate", { block_id: b.block_id, version: b.version }, `${b.name} validated`); U.refresh();
        } }, "Validate") : null,
        h("a", { class: "small", href: "#programs", onclick: () => { S.programsBlock = b.block_id; } }, "Programs for this block"),
        can("equipment_activate") && b.status !== "RETIRED" ? h("button", { class: "small ghost", onclick: async () => {
          if (B.retire !== b.block_id) { B.retire = b.block_id; tell("v:" + b.block_id, "Press Retire again to retire it; its history is kept."); U.refresh(); return; }
          B.retire = null; await call("v:" + b.block_id, "POST", "/api/blocks/retire", { block_id: b.block_id, version: b.version }, "retired"); U.refresh();
        } }, "Retire") : null,
        note("v:" + b.block_id)));
  }
  function formFrom(b) {
    const body = JSON.parse(JSON.stringify(b.body));
    body.safety = body.safety || { max_runtime_s: null };
    return { block_id: b.block_id, name: b.name, expected_version: b.version, body, path: b.path };
  }
  function emptyForm() {
    return { block_id: "", name: "", expected_version: null, body: { template: "IRRIGATION_SECTOR", description: "", members: [], supply: null, destination: null, meter: null,
      plan: { startup: [], normal_stop: [], fault_stop: [], provenance: "TEMPLATE_EXAMPLE" }, safety: { max_runtime_s: null } }, path: null };
  }

  // ---------------------------------------------------------------- the form
  function stepRows(F, phase, nodes, names) {
    const steps = F.body.plan[phase];
    const ACTIONS = [["OPEN", "Open valve"], ["CLOSE", "Close valve"], ["START", "Start pump"], ["STOP", "Stop pump"], ["WAIT", "Wait"]];
    const rows = steps.map((s, i) => {
      const act = h("select", { "aria-label": `${phase} step ${i + 1} action`, onchange: ev => { s.do = ev.target.value; if (s.do === "WAIT") { s.node = null; s.seconds = s.seconds || 5; } else s.seconds = null; U.refresh(); } },
        ACTIONS.map(([v, t]) => h("option", { value: v, selected: s.do === v ? true : null }, t)));
      const fits = s.do === "OPEN" || s.do === "CLOSE" ? "valve" : "pump";
      const eq = s.do === "WAIT" ? h("label", { class: "check" }, h("input", { type: "number", min: 1, max: 3600, step: 1, class: "w-num", value: s.seconds || "", "aria-label": `${phase} step ${i + 1} seconds`,
        onchange: ev => { s.seconds = Number(ev.target.value) || null; } }), "s")
        : h("select", { "aria-label": `${phase} step ${i + 1} equipment`, onchange: ev => { s.node = ev.target.value; } },
          h("option", { value: "" }, "— choose —"), nodes.filter(n => n.kind === fits && F.body.members.includes(n.id)).map(n => h("option", { value: n.id, selected: s.node === n.id ? true : null }, names[n.id] || n.id)));
      const verify = s.do === "START" || s.do === "OPEN" ? h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.verify ? true : null, onchange: ev => { s.verify = ev.target.checked; } }), "wait for its feedback") : null;
      const mv = (d) => { const j = i + d; if (j < 0 || j >= steps.length) return; [steps[i], steps[j]] = [steps[j], steps[i]]; U.refresh(); };
      return h("li", { class: "row step" }, h("span", { class: "muted small" }, `${i + 1}.`), act, eq, verify,
        h("button", { class: "small ghost", "aria-label": `Move ${phase} step ${i + 1} up`, onclick: () => mv(-1) }, "↑"),
        h("button", { class: "small ghost", "aria-label": `Move ${phase} step ${i + 1} down`, onclick: () => mv(1) }, "↓"),
        h("button", { class: "small ghost", "aria-label": `Remove ${phase} step ${i + 1}`, onclick: () => { steps.splice(i, 1); U.refresh(); } }, "Remove"));
    });
    return h("ol", { class: "steps" }, rows, h("li", null, h("button", { class: "small", onclick: () => { steps.push({ do: "WAIT", node: null, seconds: 5, verify: false, timeout_s: 30 }); U.refresh(); } }, "Add step")));
  }
  /** Members, one group per equipment type; each item once, whichever blocks it is also in. */
  function memberGroups(F, nodes, names, ids, usedBy) {
    const body = F.body;
    return h("div", { class: "member-groups" }, GROUPS.map(([kind, title]) => {
      const list = nodes.filter(n => n.kind === kind);
      if (!list.length) return null;
      const chosen = list.filter(n => body.members.includes(n.id)).length;
      const open = B.groupOpen[kind] != null ? B.groupOpen[kind] : chosen > 0;
      return h("details", { class: "member-group", open: open ? true : null, "data-kind": kind, ontoggle: ev => { B.groupOpen[kind] = ev.target.open; } },
        h("summary", null, `${title} `, h("span", { class: "muted small" }, `(${chosen} of ${list.length} chosen)`)),
        h("div", { class: "member-list", role: "group", "aria-label": title }, list.map(n => {
          const others = (usedBy[n.id] || []).filter(x => x !== F.block_id);
          return h("label", { class: "check member" },
            h("input", { type: "checkbox", checked: body.members.includes(n.id) ? true : null, disabled: can("equipment_edit") ? null : true,
              onchange: ev => { if (ev.target.checked) { if (!body.members.includes(n.id)) body.members.push(n.id); } else body.members = body.members.filter(x => x !== n.id); U.refresh(); } }),
            h("span", null, names[n.id] || n.id), h("span", { class: "mono small muted" }, ids[n.id] || ""),
            others.length ? h("span", { class: "small muted" }, `also in ${others.join(", ")}`) : null);
        })));
    }));
  }
  function blockForm(T, topo, usedBy) {
    const F = B.form, body = F.body;
    const nodes = topo.graph.nodes, names = {}, ids = {};
    for (const n of nodes) { const it = topo.items[n.item] || {}; names[n.id] = it.name || n.id; ids[n.id] = it.public_id || n.item || ""; }
    const pick = (label, field, kinds) => h("label", null, label + " ", h("select", { "aria-label": label, onchange: ev => { body[field] = ev.target.value || null; } },
      h("option", { value: "" }, "— choose —"), nodes.filter(n => kinds.includes(n.kind)).map(n => h("option", { value: n.id, selected: body[field] === n.id ? true : null }, `${names[n.id]} (${KIND_WORD[n.kind]})`))));
    const tmpl = h("fieldset", null, h("legend", null, "Purpose"), Object.entries(T.templates).map(([k, t]) =>
      h("label", { class: "radio" }, h("input", { type: "radio", name: "block-template", value: k, checked: body.template === k ? true : null, onchange: () => { body.template = k; } }), t)));
    const findPath = async () => {
      if (!body.supply || !body.destination) { tell("path", "Choose where the water comes from and where it goes.", true); U.refresh(); return; }
      const r = await call("path", "POST", "/api/blocks/template-plan", { template: body.template, supply: body.supply, destination: body.destination, travel_s: B.travel || null });
      if (r) {
        body.members = r.members; body.meter = r.meter; body.plan = r.plan; F.path = r.path;
        tell("path", `Path found through ${r.path.nodes.map(n => names[n] || n).join(", ") || "a direct pipe"}. ${r.note}`);
      }
      U.refresh();
    };
    const meters = nodes.filter(n => n.kind === "meter");
    const safety = body.safety = body.safety || { max_runtime_s: null };
    const save = async () => {
      if (dutyProblem(F)) { tell("save", dutyProblem(F), true); U.refresh(); return; }
      const r = await call("save", "POST", "/api/blocks", { block_id: F.block_id, name: F.name, body, expected_version: F.expected_version });
      if (r) { F.expected_version = r.version; B.issues = r.issues; tell("save", r.issues.filter(i => i.severity === "ERROR").length ? "Saved as a draft. Correct the problems below before it validates." : "Saved as a draft."); }
      U.refresh();
    };
    const validate = async () => {
      if (!F.expected_version) { tell("save", "Save the draft first.", true); U.refresh(); return; }
      const r = await call("save", "POST", "/api/blocks/validate", { block_id: F.block_id, version: F.expected_version }, "Validated. Programs can now use this block.");
      if (r) { B.form = null; B.issues = null; }
      U.refresh();
    };
    return h("div", { class: "card", "data-block-form": "" },
      h("h2", null, F.expected_version ? `${F.name} (editing)` : "New block"),
      h("div", { class: "row wrap" }, h("label", null, "Name ", h("input", { maxlength: 80, value: F.name, "aria-label": "Block name", oninput: ev => { F.name = ev.target.value; if (!F.expected_version && !B.idOwned) { F.block_id = window.AqIds.fromName(ev.target.value).toLowerCase(); const x = document.querySelector('[aria-label="Block id"]'); if (x) x.value = F.block_id; } } })),
        h("label", null, "ID ", h("input", { maxlength: 48, value: F.block_id, disabled: F.expected_version ? true : null, class: "mono", "aria-label": "Block id", placeholder: "sector_1", oninput: ev => { F.block_id = ev.target.value; B.idOwned = !!ev.target.value; } }))),
      tmpl,
      h("div", { class: "row wrap" }, pick("Water comes from", "supply", ["source", "tank"]), pick("Water goes to", "destination", ["sector", "tank"]),
        h("label", null, "Valve travel to wait (s) ", h("input", { type: "number", min: 0, max: 600, step: 1, class: "w-num", value: B.travel || "", "aria-label": "Valve travel seconds", oninput: ev => { B.travel = Number(ev.target.value) || null; } })),
        h("button", { onclick: findPath }, "Find the path and an example plan"), note("path")),
      F.path ? h("p", { class: "small" }, "Path: ", [body.supply, ...F.path.nodes, body.destination].map(n => names[n] || n).join(" → ")) : null,
      h("h3", null, "Members"), h("p", { class: "muted small" }, "Choose the equipment of this block by type. An item already in another block stays one item: it is shared, never copied."),
      memberGroups(F, nodes, names, ids, usedBy),
      h("label", null, "Flow meter for measured litres ", h("select", { "aria-label": "Flow meter", onchange: ev => { body.meter = ev.target.value || null; } },
        h("option", { value: "" }, "— none: timed watering only —"), meters.map(n => h("option", { value: n.id, selected: body.meter === n.id ? true : null }, names[n.id])))),
      h("fieldset", { "data-safety-settings": "" }, h("legend", null, "Safety settings"),
        h("label", null, "Maximum runtime of one operation (min) ", h("input", { type: "number", min: 0.1, max: 1440, step: "any", class: "w-num", value: minutes(safety.max_runtime_s) == null ? "" : minutes(safety.max_runtime_s),
          "aria-label": "Maximum runtime in minutes", oninput: ev => { safety.max_runtime_s = ev.target.value === "" ? null : Math.round(Number(ev.target.value) * 60); } })),
        h("p", { class: "muted small" }, "An independent bound for every program over this block, set once here: from the startup to the verified stop, one operation never runs longer. When it is reached the normal stop runs and every stop is still verified. No value is proposed: enter the one that is right for this installation."),
        blockConditions(safety), dutyPicker(F, body, nodes, names)),
      h("h3", null, "Startup"), stepRows(F, "startup", nodes, names),
      h("h3", null, "Normal stop"), stepRows(F, "normal_stop", nodes, names),
      h("h3", null, "Fault stop"), h("p", { class: "muted small" }, "Empty: every started item is switched off, the last started first, and each stop is verified."),
      stepRows(F, "fault_stop", nodes, names),
      h("p", { class: "muted small" }, body.plan.provenance === "TEMPLATE_EXAMPLE" ? "This plan is an example from the template. Check each step against the real installation before relying on it." : ""),
      issueList(B.issues),
      h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit"), onclick: save }, "Save draft"),
        h("button", { disabled: !can("equipment_edit"), onclick: validate }, "Validate"),
        h("button", { class: "ghost", onclick: () => { B.form = null; B.issues = null; U.refresh(); } }, "Close"), note("save")));
  }

  // ---------------------------------------------------------------- read-only installation picture
  function orientation(topo, dia, usedBy, blockNames) {
    if (!topo.graph.nodes.length) return h("div", { class: "card" }, h("h2", null, "Installation"),
      h("p", { class: "muted" }, "No installation drawing yet. It is designed in Digital Twin and exported to the Overview; blocks follow its connections."));
    const box = h("div", { class: "hed-host hed-view" });
    const side = h("div", { class: "small", role: "status", "aria-live": "polite" });
    const sel = B.sel && topo.graph.nodes.find(n => n.id === B.sel);
    const it = sel ? topo.items[sel.item] || {} : null;
    side.append(sel ? `${it.name || sel.id} (${KIND_WORD[sel.kind]})${(usedBy[sel.id] || []).length ? " is in: " + usedBy[sel.id].map(b => blockNames[b] || b).join(", ") : " is in no block yet"}.`
      : "Select a symbol to see the blocks it belongs to.");
    window.AqHydraulicEditor.create(box, { symbols: topo.symbols, items: topo.items, graph: topo.graph, diagram: dia.diagram, readOnly: true, viewOnly: true,
      highlight: B.form ? B.form.body.members : (B.sel ? [B.sel] : []),
      onSelect: id => { B.sel = B.sel === id ? null : id; U.refresh(); } });
    return h("details", { class: "card", open: B.viewOpen === false ? null : true, ontoggle: ev => { B.viewOpen = ev.target.open; } },
      h("summary", null, h("strong", null, "Installation (read-only)")),
      h("p", { class: "muted small" }, "For orientation only: nothing here moves, connects or removes equipment."),
      box, side);
  }

  VIEWS.blocks = async function () {
    const [T, topo, dia] = await Promise.all([api("GET", "/api/blocks"), api("GET", "/api/topology"), api("GET", "/api/diagram/installation")]);
    const usedBy = {}, blockNames = {};
    for (const b of T.blocks) if (b.status !== "RETIRED") { blockNames[b.block_id] = b.name; for (const m of b.body.members) (usedBy[m] = usedBy[m] || []).push(b.block_id); }
    mount("v-blocks",
      h("div", { class: "card" }, h("h2", null, "Blocks"),
        h("p", { class: "muted" }, "A block brings together equipment that works for one purpose — watering a sector, returning drainage, filling a tank — and holds the steps and safety settings that make it safe to run. Programs water through blocks."),
        h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit") || !!B.form, onclick: () => { B.form = emptyForm(); B.issues = null; B.idOwned = false; U.refresh(); } }, "New block"))),
      orientation(topo, dia, usedBy, blockNames),
      B.form ? blockForm(T, topo, usedBy) : null,
      T.blocks.length ? T.blocks.filter(b => b.status !== "RETIRED").map(b => blockCard(T, b)) : h("div", { class: "card" }, h("p", { class: "muted" }, "No block yet.")),
      T.blocks.some(b => b.status === "RETIRED") ? h("details", null, h("summary", { class: "small" }, "Retired blocks"), T.blocks.filter(b => b.status === "RETIRED").map(b => blockCard(T, b))) : null);
  };
  // 0.7.3: the block's own start and stop conditions, applied to every program over it with the program's own
  function blockConditions(safety) {
    safety.permissives = safety.permissives || []; safety.guards = safety.guards || [];
    if (!B.readings) { B.readings = []; api("GET", "/api/programs").then(r => { B.readings = r.readings || []; U.refresh(); }).catch(() => {}); }
    if (!window.AqRuleRows) return null;
    return h("div", { "data-block-conditions": "" },
      window.AqRuleRows(B.readings, safety.permissives, "Block start conditions", "Every program over this block starts only when all of these hold (a missing or stale reading counts as not holding), in addition to the program's own start conditions."),
      window.AqRuleRows(B.readings, safety.guards, "Block stop conditions", "Any one of these stops every program over this block through its normal stop. No threshold is proposed: enter the ones that are right for this installation."));
  }
  // 0.7.3 PROG-09: two member pumps sharing one duty; each start leads with the one that started least recently
  // 0.7.3 PROG-09: two member pumps sharing one duty; each start leads with the one that started least recently.
  // C075-02: the partial choice lives in the form (F.duty), each change reads the current state; save refuses a
  // half-chosen pair instead of dropping it.
  function dutyChoose(F, body, i, value) {
    F.duty = F.duty || (body.duty ? body.duty.pumps.slice() : ["", ""]);
    F.duty[i] = value;
    body.duty = F.duty[0] && F.duty[1] ? { pumps: F.duty.slice() } : null;
    return F.duty;
  }
  function dutyProblem(F) {
    const d = F.duty || ["", ""];
    if (!!d[0] !== !!d[1]) return "Lead alternation: choose the second pump too, or clear the first.";
    if (d[0] && d[0] === d[1]) return "Lead alternation needs two different pumps.";
    return null;
  }
  function dutyPicker(F, body, nodes, names) {
    const pumps = nodes.filter(n => n.kind === "pump" && (body.members || []).includes(n.id));
    if (pumps.length < 2 && !body.duty) return null;
    const cur = F.duty || (body.duty ? body.duty.pumps : ["", ""]);
    const sel = i => h("select", { "aria-label": i ? "Second pump" : "First pump", onchange: ev => { dutyChoose(F, body, i, ev.target.value); U.refresh(); } },
      h("option", { value: "" }, "—"), pumps.map(n => h("option", { value: n.id, selected: cur[i] === n.id ? true : null }, names[n.id] || n.id)));
    return h("div", { "data-duty": "" }, h("h4", null, "Lead alternation (two pumps, one duty)"),
      h("div", { class: "row wrap" }, sel(0), h("span", null, " and "), sel(1)),
      dutyProblem(F) ? h("p", { class: "bad small", role: "alert" }, dutyProblem(F)) : null,
      h("p", { class: "muted small" }, "The startup starts the first pump; each start the engine leads with the pump that started least recently. A lead that cannot start (a fault input, its limits, no channel) hands that start to the standby, and it is recorded. Leave both empty for one pump."));
  }
  window.AqBlocks = Object.assign(window.AqBlocks || {}, { dutyChoose, dutyProblem });
})();
