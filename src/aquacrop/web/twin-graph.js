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
    if (!G.card) {
      G.body = h("div"); G.card = h("div", { class: "card", id: "twin-graph" }, G.body);
      G.host = h("div", { class: "hed-host" }); G.side = h("div", { class: "hed-side-twin" }); G.flow = h("div");
    }
    return G.card;
  }
  const revs = l => `${l.topo.rev}/${l.dia.rev}`;
  let loads = 0;
  async function load() {
    const n = ++loads;
    const [topo, dia, ch, props] = await Promise.all([api("GET", "api/topology"), api("GET", "api/diagram/installation"), api("GET", "api/twin/changes").catch(() => null),
      api("GET", "api/twin/design/proposals").catch(() => null)]);
    if (n === loads) { G.loaded = { topo, dia }; G.ch = ch; G.props = props ? props.drawings : []; }   // an earlier, slower answer never replaces a later one
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
    return h("span", { class: "pill warn" }, hyd.graph_changed ? "connections differ from the Overview" : "drawing differs from the Overview");
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
      // 0.7.3: saved in the twin as drawn; placeholders and reference sketches stay until they are resolved
      const p = r.proposal, k = p.kept || {};
      G.migration = p; G.editing = false; G.draft = null;
      build(v);
      G.msg.flow = { text: `The earlier Overview drawing is now the twin's saved design, as drawn: ${k.symbols || 0} linked symbol(s), ${k.placeholders || 0} placeholder(s), ${k.sketches || 0} pipe sketch(es). ${p.ambiguous.length} ambiguous and ${p.unresolved.length} unlinked part(s) are listed below; nothing is connected by guesswork.` };
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
      (m.to_create || []).length ? h("div", { "data-to-create": "" }, h("strong", { class: "small" }, "Create these on the Equipment page (main dashboard), with these names, then press Import from Overview again:"),
        h("ul", { class: "small" }, m.to_create.map(c => h("li", null, `${c.name} — a ${c.kind}${c.shown_by ? " (the drawing showed it with " + c.shown_by + ")" : ""}`)))) : null,
      (m.matched || []).length ? h("details", { class: "small" }, h("summary", null, `${m.matched.length} symbol(s) matched by name: check them`),
        h("ul", null, m.matched.map(x => h("li", null, `${x.label}: ${x.why}`)))) : null,
      m.unresolved.filter(a => !(m.to_create || []).some(c => c.legacy === a.legacy)).length
        ? h("ul", { class: "small" }, m.unresolved.filter(a => !(m.to_create || []).some(c => c.legacy === a.legacy)).map(a => h("li", null, `${a.label}: ${a.why}. ${a.fix}.`))) : null);
  }

  // UX-04: what is left after an import, in the order to do it, each with a way to find it on the drawing
  function finishBox() {
    const R = G.ed && G.ed.remaining ? G.ed.remaining() : null;
    if (!R || !(R.links.length + R.pipes.length + R.meters.length)) return null;
    const show = sel => () => { G.ed.select(sel); G.host.scrollIntoView({ block: "nearest" }); };
    const item = (done, title, rest) => h("li", { class: done ? "muted" : "" }, h("strong", null, (done ? "Done: " : "") + title), rest);
    return h("div", { class: "card", "data-finish": "" }, h("h3", null, "Finish the imported drawing"),
      h("p", { class: "small" }, "Your drawing is preserved; some equipment and water connections still need confirmation."),
      h("ol", { class: "small" },
        item(!R.links.length, `Link equipment (${R.links.length} left)`, R.links.length ? [h("p", { class: "muted" }, "Select one, press Edit the drawing, then Place here with its Equipment item. If none is offered, create it on Equipment (main dashboard) with that name; it appears here on the next refresh."),
          h("div", { class: "row wrap" }, R.links.slice(0, 12).map(x => h("button", { class: "small ghost", onclick: show({ type: "placeholder", id: x.id }) }, `Show ${x.label}`)))] : null),
        item(!R.pipes.length, `Confirm the earlier pipes (${R.pipes.length} left)`, R.pipes.length ? [h("p", { class: "muted" }, "Select a dotted line: Connect these ends makes it a real pipe when its ends are at equipment; otherwise use Connect pipe. Crossings never join pipes."),
          h("button", { class: "small ghost", onclick: show({ type: "sketch", id: R.pipes[0].id }) }, "Show the next one")] : null),
        item(!R.meters.length, `Put flow meters into their pipes (${R.meters.length} not connected)`, R.meters.length ? [h("p", { class: "muted" }, "Select the pipe the meter belongs in, then Insert flow meter into this pipe."),
          h("div", { class: "row wrap" }, R.meters.map(x => h("button", { class: "small ghost", onclick: show({ type: "symbol", id: x.id }) }, `Show ${x.label}`)))] : null),
        item(false, "Save in the twin, run it, then Export to Overview", h("p", { class: "muted" }, "The main dashboard's Overview changes only when that export is approved there. Then Blocks can choose the connected equipment."))),
      h("p", { class: "muted small" }, "Setup order: Equipment → place and connect here → export → approve on the main dashboard → Blocks → Programs and twin testing."));
  }
  function proposalsBox(v) {
    if (!(G.props || []).length) return null;
    const open = async f => {
      const replace = G.arm === "prop:" + f.file; G.arm = null;
      try { const r = await api("POST", "api/twin/design/import-proposal", { file: f.file, replace }); await load(); build(v);
        G.msg.flow = { text: `The imported project's drawing is now the twin's design${r.conflicts.length ? "; " + r.conflicts.length + " record(s) of that project were not imported (conflicts)" : ""}. Finish it below, then Export to Overview.` }; }
      catch (e) { const m = String(e.message || e); if (/press it again/.test(m)) { G.arm = "prop:" + f.file; G.msg.flow = { text: "The twin's design has changes not exported. Press Open again to replace them.", bad: true }; } else G.msg.flow = { text: m, bad: true }; }
      draw(v);
    };
    return h("div", { class: "card", "data-project-drawings": "" }, h("h3", null, "Drawings from an imported project"),
      h("ul", { class: "small" }, G.props.map(f => h("li", null, `${f.symbols} symbol(s), ${f.placeholders} to link, ${f.connections} connection(s)${f.conflicts.length ? ", " + f.conflicts.length + " conflict(s)" : ""} `,
        h("button", { class: "small" + (G.arm === "prop:" + f.file ? " danger" : ""), disabled: !can("equipment_edit"), onclick: () => open(f) }, G.arm === "prop:" + f.file ? "Replace the twin's design" : "Open as the twin's design")))),
      h("p", { class: "muted small" }, "Opening it makes it the twin's design (the Overview is not changed); then it follows the normal path: finish, run, Export to Overview, approval."));
  }

  /** What the card's buttons and texts depend on: they are rebuilt only when it changes, so a click or a note being
   *  typed is never lost to the once-a-second refresh (the water panel beside the drawing still follows every second). */
  function signature() {
    const pv = G.ch && G.ch.preview;
    const R = G.ed && G.ed.remaining ? G.ed.remaining() : null;
    return JSON.stringify({ e: G.editing, a: G.arm, m: G.msg, d: G.draft, b: G.built, mig: !!G.migration, ld: !!G.loaded,
      fin: R ? [R.links.length, R.pipes.length, R.meters.length] : null, pr: (G.props || []).map(f => f.file),
      pv: pv ? [pv.empty, pv.hydraulics && [pv.hydraulics.graph_changed, pv.hydraulics.drawing_changed], pv.items.length, pv.bindings.length,
        pv.programs.length, pv.missing.length, (pv.twin_only || []).length] : null });
  }
  function draw(v) {
    if (!G.body) return;
    const sig = signature();
    if (sig === G.sig) return drawSide(v);
    G.sig = sig;
    const gw = v && v.graph_water;
    const ed = can("equipment_edit");
    put(G.flow, h("div", { class: "row wrap", role: "group", "aria-label": "Design workflow" },
      G.editing ? [h("button", { onclick: () => save(v) }, "Save in the twin"), h("button", { class: "ghost", onclick: async () => { G.editing = false; G.migration = null; G.draft = null; await load(); build(v); draw(v); } }, "Cancel")]
        : [h("button", { disabled: !ed, class: G.arm === "import" ? "danger" : "", onclick: () => importOverview(v) }, G.arm === "import" ? "Replace with the Overview's drawing" : "Import from Overview"),
          h("button", { class: G.arm === "new" ? "danger" : "ghost", disabled: !ed, onclick: () => newDesign(v) }, G.arm === "new" ? "Start an empty design" : "New design"),
          h("button", { class: "ghost", disabled: !ed || !G.loaded, onclick: async () => { await load(); G.editing = true; G.draft = null; build(v); draw(v); } }, "Edit the drawing")],
      note("flow"), note("save")));
    put(G.body, h("div", { class: "row" }, h("h2", null, "Installation design"), status()),
      h("p", { class: "muted small" }, "1 Import from Overview, or start a new design · 2 place the equipment from the Equipment page and connect it · 3 save, then run it below · 4 Export to Overview."),
      G.flow, h("div", { class: "hed-twin" }, G.host, G.side), finishBox(), proposalsBox(v), migrationBox(), exportBox(v),
      h("p", { class: "muted small" }, "The water follows only the connections drawn here; a crossing never joins pipes. Saving changes the twin, never the real system."));
    drawSide(v);
  }
  function drawSide(v) {
    const gw = v && v.graph_water;
    if (G.ed && !G.editing && gw) G.ed.setOverlay({ edges: gw.edges, tanks: gw.tanks, notModelled: gw.not_modelled });
    if (document.activeElement && G.side.contains(document.activeElement)) return;     // the scenario field is in use
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
    const none = !graph && !topo.graph.nodes.length && !((dia.diagram || {}).placeholders || []).length;
    if (none && !G.editing) {                             // no design yet: the Overview's drawing as the reference, read-only
      G.ed = null; G.rev = { t: topo.rev, d: dia.rev };
      if (G.refFor === revs(G.loaded) && G.host.childNodes.length) return;   // already shown for these revisions
      G.built = G.refFor = revs(G.loaded);
      api("GET", "api/twin/design/reference").then(R => {
        if (G.ed || G.editing) return;
        const box = h("div", { class: "hed-host hed-view" });
        window.AqHydraulicEditor.create(box, { symbols: topo.symbols, items: topo.items, graph: R.graph, diagram: R.diagram, readOnly: true, viewOnly: true });
        put(G.host, [h("p", { class: "small", "data-reference": "" }, h("span", { class: "pill warn" }, "REFERENCE"), " The twin has no design yet. This is the Overview's current drawing",
          R.source === "legacy" ? " (the earlier drawing, as Import from Overview would load it)" : "", ". Press Import from Overview to make it the twin's design, or New design to start empty."), box]);
      }).catch(() => put(G.host, h("p", { class: "muted small" }, "The twin has no design yet; the Overview's drawing could not be loaded as a reference.")));
      return;
    }
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
