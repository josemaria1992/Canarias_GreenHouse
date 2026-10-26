/* The digital twin's installation design (0.7.0; one workspace by the owner's decisions of 26 October 2026, 0.7.1).
   Loaded after twin.js, router.js and hydraulic-editor.js.

   The twin is where the installation is drawn. Two journeys, one card:
     * New design: place the equipment made on the Equipment page, connect it, save, simulate, Export to Overview.
     * Import from Overview: the approved drawing (or, before there is one, the earlier drawing converted with its
       ambiguous parts listed for you to decide) becomes the working design; edit, save, simulate, export again.
   Saving changes the twin only. Export to Overview sends the design with its proof; on the main dashboard it is
   reviewed, shown with its differences and applied by an administrator or an operator with commissioning. An export
   never verifies wiring, activates a program or switches anything. While the design is not edited, the simulated
   flows, tank levels and water ledger are drawn on it. Messages appear beside their button. */
"use strict";
(function () {
  const T = window.AqTwin, { h, api, can } = T;
  const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));   // never a "null" text
  const G = { card: null, body: null, ed: null, editing: false, msg: {}, loaded: null, last: 0, host: null, ch: null, migration: null, arm: null, draft: null };
  function note(k) { const m = G.msg[k]; return m ? h("span", { class: "inline-msg " + (m.bad ? "bad" : "ok"), role: "status" }, m.text) : null; }
  async function call(k, method, path, body, ok) {
    try { const r = await api(method, path, body); G.msg[k] = { text: ok || "done" }; return r; }
    catch (e) { G.msg[k] = { text: String(e.message || e), bad: true }; return null; }
  }
  function card() {
    if (!G.card) { G.body = h("div"); G.card = h("div", { class: "card", id: "twin-graph" }, G.body); }
    return G.card;
  }
  const revs = l => `${l.topo.rev}/${l.dia.rev}`;
  let loads = 0;
  async function load() {
    const n = ++loads;
    const [topo, dia, ch] = await Promise.all([api("GET", "api/topology"), api("GET", "api/diagram/installation"), api("GET", "api/twin/changes").catch(() => null)]);
    if (n === loads) { G.loaded = { topo, dia }; G.ch = ch; }   // an earlier, slower answer never replaces a later one
  }
  function ledgerTable(gw) {
    const l = gw.ledger;
    const row = (k, v) => h("tr", null, h("td", null, k), h("td", null, `${Number(v).toFixed(2)} L`));
    return h("table", { class: "compact small" }, h("tbody", null, row("At the start", l.initial_L), row("Adjusted by scenarios", l.adjustments_L),
      row("Supplied by sources", l.supplied_L), row("Stored in tanks now", l.stored_L), row("Draining (in transit)", l.in_transit_L),
      row("Used by sectors", l.used_by_sectors_L), row("Drained away", l.drained_away_L), row("Spilled", l.spilled_L),
      h("tr", null, h("td", null, "Balance residual"), h("td", null, `${l.residual_L.toExponential(1)} L`))));
  }
  function scenario(gw) {
    const tanks = Object.entries(gw.tanks).filter(([, t]) => t.capacity_L);
    if (!tanks.length || !can("sim_command")) return null;
    const sel = h("select", { "aria-label": "Tank" }, tanks.map(([k]) => h("option", { value: k }, k)));
    const litres = h("input", { type: "number", min: 0, step: "any", class: "narrow-num", "aria-label": "Litres" });
    return h("div", null, h("h3", null, "Scenario"), h("div", { class: "row" }, sel, litres, h("span", { class: "small" }, "L"),
      h("button", { class: "small", onclick: async () => { await call("lvl", "POST", "api/twin/graph/level", { node: sel.value, litres: Number(litres.value) }, "set; recorded as a mass adjustment"); } }, "Set the water"), note("lvl")),
      h("p", { class: "muted small" }, "A set level is an explicit adjustment in the ledger, never hidden water. Faults are injected on each equipment card below."));
  }

  // ---------------------------------------------------------------- the design's state against the Overview
  function status() {
    const pv = G.ch && G.ch.preview, hyd = pv && pv.hydraulics;
    if (G.editing) return h("span", { class: "pill warn" }, G.draft ? `${G.draft} — not saved` : "editing — not saved");
    if (!G.loaded || !G.loaded.topo.graph.nodes.length) return h("span", { class: "pill" }, "no design yet");
    if (!hyd) return h("span", { class: "pill ok" }, "same as the Overview");
    return h("span", { class: "pill warn" }, hyd.graph_changed ? "connections changed in the twin: not exported" : "drawing changed in the twin: not exported");
  }
  async function importOverview(v) {
    const replace = G.arm === "import";
    G.arm = null;
    let r;
    try { r = await api("POST", "api/twin/design/import", { replace }); }
    catch (e) {
      const m = String(e.message || e);
      if (/press it again/.test(m)) { G.arm = "import"; G.msg.flow = { text: "The twin's design has changes not exported to the Overview. Press Import from Overview again to replace them.", bad: true }; }
      else G.msg.flow = { text: m, bad: true };
      return draw(v);
    }
    await load();
    if (r.source === "legacy") {
      const p = r.proposal;
      G.migration = p; G.editing = true; G.draft = "the earlier Overview drawing, converted";
      build(v, p.topology, Object.assign({}, G.loaded.dia.diagram, p.diagram));
      G.msg.flow = { text: `The Overview still has the earlier drawing: it is loaded here as a design to review (not saved). ${p.ambiguous.length} ambiguous and ${p.unresolved.length} unlinked part(s) are listed below for you to decide; nothing is connected by guesswork.` };
    } else {
      G.migration = null; G.editing = false; G.draft = null;
      G.msg.flow = { text: r.replaced ? "Imported from the Overview: the twin's earlier design was replaced." : "Imported from the Overview: the twin's design is the approved drawing." };
      build(v);
    }
    draw(v);
  }
  function newDesign(v) {
    const has = G.loaded && G.loaded.topo.graph.nodes.length;
    if (has && G.arm !== "new") { G.arm = "new"; G.msg.flow = { text: "Press New design again to start an empty design. The twin's saved design stays until you save the new one; the Overview is not touched.", bad: true }; return draw(v); }
    G.arm = null; G.migration = null; G.editing = true; G.draft = "new design";
    const d = Object.assign({}, G.loaded.dia.diagram, { symbols: [], routes: [] });
    build(v, { nodes: [], edges: [], measures: [] }, d);
    G.msg.flow = { text: "An empty design: place the equipment from the list beside the drawing, then connect it." };
    draw(v);
  }
  async function exportOverview(v, noteEl) {
    const r = await call("export", "POST", "api/twin/export", { note: noteEl.value },
      "Sent to the main dashboard. There, Digital Twin → Changes from the twin shows the differences; an administrator or an operator with commissioning applies it, and the Overview then draws it.");
    if (r) { G.last = 0; await load(); }
    draw(v);
  }
  function exportBox(v) {
    const pv = G.ch && G.ch.preview;
    if (!pv || G.editing) return null;
    const noteEl = h("input", { maxlength: 1000, placeholder: "what changed and why", "aria-label": "Note for the approver" });
    const hyd = pv.hydraulics, opWord = { create: "new", update: "changed", retire: "retired" };
    const lines = [];
    if (hyd) lines.push(h("li", null, hyd.graph_changed ? "The connections of the installation changed (blocks and twin results that relied on the old ones are checked again)." : "The drawing changed; the connections are the same."),
      hyd.blocks.length ? h("li", null, `${hyd.blocks.length} block version(s): ${hyd.blocks.map(b => b.name).join(", ")}`) : null);
    for (const i of pv.items) lines.push(h("li", null, `${i.name}: ${opWord[i.op]}`));
    for (const b of pv.bindings) lines.push(h("li", null, `${b.item_id}.${b.role}: ${b.base_point || "no channel"} → ${b.point || "no channel"} (arrives as proposed: verified at the bench before any physical run)`));
    for (const p of pv.programs) lines.push(h("li", null, `${p.name}: twin v${p.twin_version}${p.passed ? "" : " — no run that ended DONE yet"}`));
    return h("div", { class: "card", "data-export": "" }, h("h3", null, "Export to Overview"),
      pv.empty ? h("p", { class: "muted small" }, "Nothing to export: the twin's design, equipment and programs are the same as the main dashboard's.")
        : [h("ul", { class: "small" }, lines),
          (pv.twin_only || []).length ? h("p", { class: "muted small" }, "Stays in the twin: ", pv.twin_only.join("; "), ".") : null,
          pv.missing.length ? h("ul", { class: "bad small" }, pv.missing.map(x => h("li", null, x))) : null,
          h("div", { class: "row" }, noteEl, h("button", { disabled: !can("equipment_edit") || pv.missing.length > 0, onclick: () => exportOverview(v, noteEl) }, "Export to Overview"), note("export"))],
      h("p", { class: "muted small" }, "Exporting sends the design with this twin run's proof. It changes nothing on the real system until it is approved there, and approving applies the drawing and equipment changes only: it does not verify wiring, activate a program or switch anything. Equipment without a channel arrives as planned and unassigned."));
  }
  function migrationBox() {
    const m = G.migration;
    if (!m) return null;
    return h("div", { class: "card", "data-migration": "" }, h("h3", null, "From the earlier Overview drawing: decide these"),
      h("p", { class: "muted small" }, `Source: ${m.source && m.source.digest ? "digest " + m.source.digest.slice(0, 12) : "the earlier drawing"}. It stays unchanged on the Overview as a reference. ${m.meaning || ""}`),
      m.ambiguous.length ? h("ul", { class: "small" }, m.ambiguous.map(a => h("li", null, `${a.legacy}: ${a.why}. ${a.fix}.`))) : h("p", { class: "small" }, "Nothing ambiguous."),
      m.unresolved.length ? h("ul", { class: "small" }, m.unresolved.map(a => h("li", null, `${a.label}: ${a.why}. ${a.fix}.`))) : null);
  }

  function draw(v) {
    if (!G.body) return;
    const gw = v && v.graph_water;
    const ed = can("equipment_edit");
    if (!G.host) { G.host = h("div", { class: "hed-host" }); G.side = h("div", { class: "hed-side-twin" }); G.flow = h("div"); }
    put(G.flow, h("div", { class: "row wrap", role: "group", "aria-label": "Design workflow" },
      G.editing ? [h("button", { onclick: () => save(v) }, "Save in the twin"), h("button", { class: "ghost", onclick: async () => { G.editing = false; G.migration = null; G.draft = null; await load(); build(v); draw(v); } }, "Cancel")]
        : [h("button", { disabled: !ed, class: G.arm === "import" ? "danger" : "", onclick: () => importOverview(v) }, G.arm === "import" ? "Replace with the Overview's drawing" : "Import from Overview"),
          h("button", { class: G.arm === "new" ? "danger" : "ghost", disabled: !ed, onclick: () => newDesign(v) }, G.arm === "new" ? "Start an empty design" : "New design"),
          h("button", { class: "ghost", disabled: !ed || !G.loaded, onclick: async () => { await load(); G.editing = true; G.draft = null; build(v); draw(v); } }, "Edit the drawing")],
      note("flow"), note("save")));
    put(G.body, h("div", { class: "row" }, h("h2", null, "Installation design"), status()),
      h("p", { class: "muted small" }, "1 Import from Overview, or start a new design · 2 place the equipment from the Equipment page and connect it · 3 save, then run it below · 4 Export to Overview."),
      G.flow, h("div", { class: "hed-twin" }, G.host, G.side), migrationBox(), exportBox(v),
      h("p", { class: "muted small" }, "The water follows only the connections drawn here; a crossing never joins pipes. Saving changes the twin, never the real system."));
    if (G.ed && !G.editing && gw) G.ed.setOverlay({ edges: gw.edges, tanks: gw.tanks, notModelled: gw.not_modelled });
    const nm = gw ? Object.entries(gw.not_modelled || {}) : [];
    put(G.side, G.editing ? null : gw ? [
      h("h3", null, "Water now"), h("p", { class: "small" }, `Model ${gw.model}: a bounded water balance over the explicit pipes. Pressure, chemistry and suction are NOT MODELLED.`),
      v.model_error ? h("p", { class: "bad" }, `The model stopped: ${v.model_error}. This run is not valid evidence; reset the twin.`) : null,
      ledgerTable(gw),
      nm.length ? h("div", null, h("strong", { class: "small" }, "NOT MODELLED"), h("ul", { class: "small" }, nm.map(([k, why]) => h("li", null, `${k}: ${why}`)))) : null,
      gw.notes.length ? h("ul", { class: "small warn" }, gw.notes.map(x => h("li", null, x))) : null,
      scenario(gw)] : h("p", { class: "muted small" }, "No design yet: import the Overview's drawing or start a new design."));
  }
  function build(v, graph, diagram) {
    if (!G.loaded) return;
    const { topo, dia } = G.loaded;
    G.ed = window.AqHydraulicEditor.create(G.host, { symbols: topo.symbols, items: topo.items, graph: graph || topo.graph, diagram: diagram || dia.diagram,
      readOnly: !G.editing, serverIssues: topo.issues });
    G.rev = { t: topo.rev, d: dia.rev };
    G.built = revs(G.loaded);
  }
  async function save(v) {
    const cur = G.ed.get();
    const r = await call("save", "POST", "api/drawing/installation", { graph: cur.graph, diagram: cur.diagram, expected_topology_rev: G.rev.t, expected_diagram_rev: G.rev.d, note: G.draft || "" },
      "Saved in the twin. Run it below, then Export to Overview.");
    if (r) { G.editing = false; G.migration = null; G.draft = null; await load(); build(v); }
    else if (G.ed && G.msg.save) G.ed.say(G.msg.save.text, true);
    draw(v);
  }
  async function update(v) {
    if (!G.body) return;
    if (G.editing) return;                                // never rebuilt under the editor
    const now = Date.now();
    // loaded again when the snapshot names another graph or drawing revision (an import, a reset, an edit), and
    // every 15 s anyway (the export preview follows the twin's runs)
    const gw = v.graph_water;
    if (!G.loaded || gw && gw.revs !== revs(G.loaded) || now - G.last > 15000) {
      G.last = now;
      try { await load(); } catch (e) { return; }
      build(v);
    } else if (!G.ed) build(v);
    draw(v);
  }
  window.AqTwinGraph = { card, update };
})();
