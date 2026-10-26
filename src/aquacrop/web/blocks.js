/* AquaCrop Blocks (0.7.0): the operator's place for equipment, the installation drawing and functional blocks.
   Loaded after app.js (window.AqUI), router.js and hydraulic-editor.js.

   * Blocks: each block names its supply, destination, members and operating plan. "New block" goes purpose ->
     supply and destination -> the connected path and an example plan -> edit -> save -> validate. A shared pump
     shows the other blocks that use it; it is one item, never a copy.
   * Drawing: the editor for the explicit graph and its orthogonal drawing. While it has unsaved changes the page
     is not rebuilt by the once-a-second refresh.
   * Equipment: the equipment library (typed forms), the same pages administrators reach from Equipment.
   Messages appear beside the button that caused them. Nothing here starts equipment. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, can, mount, S, VIEWS } = U;
  const KIND_WORD = { source: "water source", tank: "tank", pump: "pump", valve: "valve", meter: "flow meter", sensor: "sensor", sector: "sector", junction: "junction" };
  const B = S.blocksUi = S.blocksUi || { tab: "blocks", msg: {}, form: null, editor: null, loaded: null };

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
  function tabs() {
    const t = (k, label) => h("button", { class: "tab" + (B.tab === k ? " active" : ""), role: "tab", "aria-selected": B.tab === k ? "true" : "false",
      onclick: () => {
        if (B.tab === "drawing" && B.editor && B.editor.dirty() && k !== "drawing") { tell("tabs", "The drawing has unsaved changes: save or cancel them first.", true); B.reload = false; return; }
        if (k === "drawing") B.reload = true;
        B.tab = k; U.refresh();
      } }, label);
    return h("div", { class: "row tabs", role: "tablist", "aria-label": "Blocks sections" }, t("blocks", "Blocks"), t("drawing", "Installation drawing"), t("equipment", "Equipment library"), note("tabs"));
  }

  // ---------------------------------------------------------------- blocks list and form
  function blockCard(T, b) {
    const body = b.body, v = b.validation;
    const st = b.status === "VALIDATED" ? (b.stale ? U.pill("REVALIDATE", "warn") : U.pill("VALIDATED", "ok")) : b.status === "DRAFT" ? U.pill("DRAFT", "warn") : U.pill(b.status, "");
    const shared = Object.entries(b.shared || {});
    return h("div", { class: "card block-card", "data-block": b.block_id },
      h("div", { class: "row" }, h("h3", null, b.name), st, h("span", { class: "muted small" }, `${T.templates[body.template] || body.template} · v${b.version}`)),
      h("p", { class: "small" }, `From ${b.names[body.supply] || body.supply || "—"} to ${b.names[body.destination] || body.destination || "—"}`,
        b.path ? ` through ${b.path.nodes.map(n => b.names[n] || n).join(", ") || "a direct pipe"}` : " — no connected path yet",
        body.meter ? ` · measured by ${b.names[body.meter] || body.meter}` : " · no flow meter: timed watering only"),
      shared.length ? h("p", { class: "small" }, U.pill("SHARED", ""), " ", shared.map(([m, others]) => `${b.names[m] || m} is also in ${others.join(", ")}`).join("; "),
        h("span", { class: "muted" }, " — one item, one owner at a time: an operation claims it while it runs.")) : null,
      b.stale ? h("p", { class: "warn small" }, "The installation drawing changed its connections since this block was validated: validate it again before using it.") : null,
      v && v.warnings && v.warnings.length ? issueList(v.warnings) : null,
      h("div", { class: "row" },
        h("button", { class: "small", disabled: !can("equipment_edit"), onclick: () => { B.form = formFrom(b); B.issues = null; U.refresh(); } }, b.status === "DRAFT" ? "Edit" : "Change (new version)"),
        b.status === "DRAFT" ? h("button", { class: "small", disabled: !can("equipment_edit"), onclick: async () => {
          const r = await call("v:" + b.block_id, "POST", "/api/blocks/validate", { block_id: b.block_id, version: b.version }, `${b.name} validated`); if (r) U.refresh(); else U.refresh();
        } }, "Validate") : null,
        h("a", { class: "small", href: "#programs", onclick: () => { S.programsBlock = b.block_id; } }, "Programs for this block"),
        can("equipment_activate") && b.status !== "RETIRED" ? h("button", { class: "small ghost", onclick: async () => {
          if (B.retire !== b.block_id) { B.retire = b.block_id; tell("v:" + b.block_id, "Press Retire again to retire it; its history is kept."); U.refresh(); return; }
          B.retire = null; await call("v:" + b.block_id, "POST", "/api/blocks/retire", { block_id: b.block_id, version: b.version }, "retired"); U.refresh();
        } }, "Retire") : null,
        note("v:" + b.block_id)),
      B.issueFor === b.block_id ? issueList(B.issues) : null);
  }
  function formFrom(b) {
    return { block_id: b.block_id, name: b.name, expected_version: b.version, body: JSON.parse(JSON.stringify(b.body)), path: b.path };
  }
  function emptyForm() {
    return { block_id: "", name: "", expected_version: null, body: { template: "IRRIGATION_SECTOR", description: "", members: [], supply: null, destination: null, meter: null,
      plan: { startup: [], normal_stop: [], fault_stop: [], provenance: "TEMPLATE_EXAMPLE" } }, path: null };
  }
  function stepRows(F, phase, nodes, names) {
    const steps = F.body.plan[phase];
    const ACTIONS = [["OPEN", "Open valve"], ["CLOSE", "Close valve"], ["START", "Start pump"], ["STOP", "Stop pump"], ["WAIT", "Wait"]];
    const rows = steps.map((s, i) => {
      const act = h("select", { "aria-label": `${phase} step ${i + 1} action`, onchange: ev => { s.do = ev.target.value; if (s.do === "WAIT") { s.node = null; s.seconds = s.seconds || 5; } else s.seconds = null; U.refresh(); } },
        ACTIONS.map(([v, t]) => h("option", { value: v, selected: s.do === v ? true : null }, t)));
      const fits = s.do === "OPEN" || s.do === "CLOSE" ? "valve" : "pump";
      const eq = s.do === "WAIT" ? h("label", null, h("input", { type: "number", min: 1, max: 3600, step: 1, class: "w-num", value: s.seconds || "", "aria-label": `${phase} step ${i + 1} seconds`,
        onchange: ev => { s.seconds = Number(ev.target.value) || null; } }), " s")
        : h("select", { "aria-label": `${phase} step ${i + 1} equipment`, onchange: ev => { s.node = ev.target.value; } },
          h("option", { value: "" }, "— choose —"), nodes.filter(n => n.kind === fits && F.body.members.includes(n.id)).map(n => h("option", { value: n.id, selected: s.node === n.id ? true : null }, names[n.id] || n.id)));
      const verify = s.do === "START" || s.do === "OPEN" ? h("label", { class: "small" }, h("input", { type: "checkbox", checked: s.verify ? true : null, onchange: ev => { s.verify = ev.target.checked; } }), " wait for its feedback") : null;
      const mv = (d) => { const j = i + d; if (j < 0 || j >= steps.length) return; [steps[i], steps[j]] = [steps[j], steps[i]]; U.refresh(); };
      return h("li", { class: "row step" }, h("span", { class: "muted small" }, `${i + 1}.`), act, eq, verify,
        h("button", { class: "small ghost", "aria-label": `Move ${phase} step ${i + 1} up`, onclick: () => mv(-1) }, "↑"),
        h("button", { class: "small ghost", "aria-label": `Move ${phase} step ${i + 1} down`, onclick: () => mv(1) }, "↓"),
        h("button", { class: "small ghost", "aria-label": `Remove ${phase} step ${i + 1}`, onclick: () => { steps.splice(i, 1); U.refresh(); } }, "Remove"));
    });
    return h("ol", { class: "steps" }, rows, h("li", null, h("button", { class: "small", onclick: () => { steps.push({ do: "WAIT", node: null, seconds: 5, verify: false, timeout_s: 30 }); U.refresh(); } }, "Add step")));
  }
  function blockForm(T, topo) {
    const F = B.form, body = F.body;
    const nodes = topo.graph.nodes, names = {};
    for (const n of nodes) names[n.id] = (topo.items[n.item] || {}).name || n.id;
    const pick = (label, field, kinds) => h("label", null, label + " ", h("select", { "aria-label": label, onchange: ev => { body[field] = ev.target.value || null; } },
      h("option", { value: "" }, "— choose —"), nodes.filter(n => kinds.includes(n.kind)).map(n => h("option", { value: n.id, selected: body[field] === n.id ? true : null }, `${names[n.id]} (${KIND_WORD[n.kind]})`))));
    const tmpl = h("fieldset", null, h("legend", null, "Purpose"), Object.entries(T.templates).map(([k, t]) =>
      h("label", { class: "radio" }, h("input", { type: "radio", name: "block-template", value: k, checked: body.template === k ? true : null, onchange: () => { body.template = k; } }), " ", t)));
    const findPath = async () => {
      if (!body.supply || !body.destination) { tell("path", "Choose where the water comes from and where it goes.", true); U.refresh(); return; }
      const r = await call("path", "POST", "/api/blocks/template-plan", { template: body.template, supply: body.supply, destination: body.destination, travel_s: B.travel || null });
      if (r) {
        body.members = r.members; body.meter = r.meter; body.plan = r.plan; F.path = r.path;
        tell("path", `Path found through ${r.path.nodes.map(n => names[n] || n).join(", ") || "a direct pipe"}. ${r.note}`);
      }
      U.refresh();
    };
    const memberBoxes = h("div", { class: "row wrap" }, nodes.filter(n => n.kind !== "junction").map(n => h("label", { class: "small" },
      h("input", { type: "checkbox", checked: body.members.includes(n.id) ? true : null, onchange: ev => { if (ev.target.checked) body.members.push(n.id); else body.members = body.members.filter(x => x !== n.id); } }),
      ` ${names[n.id]}`)));
    const meters = nodes.filter(n => n.kind === "meter");
    const save = async () => {
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
      h("div", { class: "row wrap" }, h("label", null, "Name ", h("input", { maxlength: 80, value: F.name, "aria-label": "Block name", oninput: ev => { F.name = ev.target.value; } })),
        h("label", null, "Id ", h("input", { maxlength: 48, value: F.block_id, disabled: F.expected_version ? true : null, "aria-label": "Block id", placeholder: "sector-1", oninput: ev => { F.block_id = ev.target.value; } }))),
      tmpl,
      h("div", { class: "row wrap" }, pick("Water comes from", "supply", ["source", "tank"]), pick("Water goes to", "destination", ["sector", "tank"]),
        h("label", null, "Valve travel to wait (s) ", h("input", { type: "number", min: 0, max: 600, step: 1, class: "w-num", value: B.travel || "", "aria-label": "Valve travel seconds", oninput: ev => { B.travel = Number(ev.target.value) || null; } })),
        h("button", { onclick: findPath }, "Find the path and an example plan"), note("path")),
      F.path ? h("p", { class: "small" }, "Path: ", [body.supply, ...F.path.nodes, body.destination].map(n => names[n] || n).join(" → ")) : null,
      h("h3", null, "Members"), memberBoxes,
      h("label", null, "Flow meter for measured volume ", h("select", { "aria-label": "Flow meter", onchange: ev => { body.meter = ev.target.value || null; } },
        h("option", { value: "" }, "— none: timed watering only —"), meters.map(n => h("option", { value: n.id, selected: body.meter === n.id ? true : null }, names[n.id])))),
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

  // ---------------------------------------------------------------- drawing tab
  function drawingTab(topo, dia) {
    const box = h("div", { class: "hed-host" });
    const ro = !can("equipment_edit");
    const items = topo.items;
    const ed = window.AqHydraulicEditor.create(box, { symbols: topo.symbols, items, graph: topo.graph, diagram: dia.diagram, readOnly: ro,
      serverIssues: topo.issues, onChange: () => {} });
    B.editor = ed; B.loaded = { trev: topo.rev, drev: dia.rev };
    const save = async () => {
      const cur = ed.get();
      const r = await call("draw", "POST", "/api/drawing/installation", { graph: cur.graph, diagram: cur.diagram, expected_topology_rev: B.loaded.trev, expected_diagram_rev: B.loaded.drev });
      if (r) {
        B.loaded = { trev: r.topology.rev, drev: r.diagram.rev }; ed.clean();
        tell("draw", r.semantic_change ? `Saved. The connections changed (graph revision ${r.topology.rev}): blocks and twin results that used the old connections must be checked again.`
          : "Saved. Only the drawing changed; the connections are the same.");
        ed.set(cur.graph, cur.diagram, r.issues);
      } else if (B.issues) ed.say(B.issues.map(i => i.message).slice(0, 3).join("; "), true);
      footer();
    };
    const cancel = () => {
      if (B.cancelArmed) { B.cancelArmed = false; B.reload = true; tell("draw", "Changes dropped."); U.refresh(); return; }
      B.cancelArmed = true; tell("draw", "Press Cancel changes again to drop every unsaved change."); footer();
    };
    const migrate = async () => {
      const m = await call("draw", "GET", "/api/topology/migration");
      if (!m) return footer();
      ed.set(m.topology, Object.assign({}, m.diagram), []);
      B.migration = m; tell("draw", `The old drawing's explicit links are loaded as a draft (not saved). ${m.ambiguous.length} ambiguous and ${m.unresolved.length} unlinked part(s) are listed below for you to decide.`);
      footer();
    };
    const foot = h("div", null);
    function footer() {
      const m = B.migration;
      foot.replaceChildren(h("div", { class: "row" }, h("button", { disabled: ro, onclick: save }, "Save drawing"), h("button", { class: "ghost", disabled: ro, onclick: cancel }, "Cancel changes"),
        !topo.graph.nodes.length && !ro ? h("button", { class: "ghost", onclick: migrate }, "Start from the old drawing") : null, note("draw")),
        m ? h("div", { class: "card" }, h("h3", null, "From the old drawing: decide these"),
          h("p", { class: "muted small" }, `Source: ${m.from}, digest ${m.source.digest.slice(0, 12)}. It stays unchanged as a reference. ${m.meaning}.`),
          m.ambiguous.length ? h("ul", { class: "small" }, m.ambiguous.map(a => h("li", null, `${a.legacy}: ${a.why}. ${a.fix}.`))) : h("p", { class: "small" }, "Nothing ambiguous."),
          m.unresolved.length ? h("ul", { class: "small" }, m.unresolved.map(a => h("li", null, `${a.label}: ${a.why}. ${a.fix}.`))) : null) : null,
        h("p", { class: "muted small" }, topo.meaning));
    }
    footer();
    return [box, foot];
  }

  VIEWS.blocks = async function () {
    // an open drawing is never rebuilt under the user (a drag, a selection, unsaved changes): it reloads only
    // when the tab is opened again or the user cancels the changes
    const host = document.querySelector("#v-blocks .hed-host");
    if (B.tab === "drawing" && B.editor && host && host.isConnected && !B.reload) return;
    B.reload = false;
    if (B.tab === "equipment") {
      mount("v-blocks", h("div", { class: "card" }, h("h2", null, "Blocks"), tabs()), h("div", { class: "card" },
        h("p", null, "The equipment library: create pumps, valves, tanks, sources, sectors and instruments with forms, set their channels and limits."),
        h("a", { class: "button", href: "#equipment" }, "Open the equipment library")));
      return;
    }
    const [T, topo] = await Promise.all([api("GET", "/api/blocks"), api("GET", "/api/topology")]);
    if (B.tab === "drawing") {
      const dia = await api("GET", "/api/diagram/installation");
      B.editor = null;
      mount("v-blocks", h("div", { class: "card" }, h("h2", null, "Blocks"), tabs()), h("div", { class: "card" }, drawingTab(topo, dia)));
      return;
    }
    mount("v-blocks",
      h("div", { class: "card" }, h("h2", null, "Blocks"), tabs(),
        h("p", { class: "muted" }, "A block is one purpose — watering a sector, returning drainage, filling a tank — over equipment you already have. Programs water through blocks."),
        h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit") || !!B.form, onclick: () => { B.form = emptyForm(); B.issues = null; U.refresh(); } }, "New block"),
          topo.graph.nodes.length ? null : h("span", { class: "muted small" }, "Draw the installation first (Installation drawing): blocks follow its connections."))),
      B.form ? blockForm(T, topo) : null,
      T.blocks.length ? T.blocks.filter(b => b.status !== "RETIRED").map(b => blockCard(T, b)) : h("div", { class: "card" }, h("p", { class: "muted" }, "No block yet.")),
      T.blocks.some(b => b.status === "RETIRED") ? h("details", null, h("summary", { class: "small" }, "Retired blocks"), T.blocks.filter(b => b.status === "RETIRED").map(b => blockCard(T, b))) : null);
  };
})();
