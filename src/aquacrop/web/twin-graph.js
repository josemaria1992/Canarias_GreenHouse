/* The digital twin's water over the installation graph (0.7.0). Loaded after twin.js, router.js and
   hydraulic-editor.js. Shows the twin's explicit graph with the simulated flows, tank levels, the water ledger and
   what is NOT MODELLED; "Edit the drawing" opens the same orthogonal editor as Blocks (saved in the twin only);
   the scenario part sets a tank's water as an explicit, recorded adjustment. Messages appear beside their button. */
"use strict";
(function () {
  const T = window.AqTwin, { h, api, can } = T;
  const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));   // never a "null" text
  const G = { card: null, body: null, ed: null, editing: false, msg: {}, loaded: null, last: 0, host: null };
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
    const [topo, dia] = await Promise.all([api("GET", "api/topology"), api("GET", "api/diagram/installation")]);
    if (n === loads) G.loaded = { topo, dia };          // an earlier, slower answer never replaces a later one
  }
  function ledgerTable(gw) {
    const l = gw.ledger;
    const row = (k, v) => h("tr", null, h("td", null, k), h("td", null, `${Number(v).toFixed(2)} L`));
    return h("table", { class: "compact small" }, h("tbody", null, row("At the start", l.initial_L), row("Adjusted by scenarios", l.adjustments_L),
      row("Supplied by sources", l.supplied_L), row("Stored in tanks now", l.stored_L), row("Draining (in transit)", l.in_transit_L),
      row("Used by sectors", l.used_by_sectors_L), row("Drained away", l.drained_away_L), row("Spilled", l.spilled_L),
      h("tr", null, h("td", null, "Balance residual"), h("td", null, `${l.residual_L.toExponential(1)} L`))));
  }
  function draw(v) {
    const gw = v.graph_water;
    if (!gw) {
      put(G.body, h("h2", null, "Installation graph"),
        h("p", { class: "muted" }, "This twin has no explicit graph yet: its water still follows the older drawing below (a reference)."),
        can("equipment_edit") ? h("button", { onclick: async () => { await load(); G.editing = true; G.ed = null; build(v); } }, "Draw the installation graph") : null);
      return;
    }
    // drawn again whenever the graph or drawing it shows is not the one loaded last (an import, a twin edit)
    if (!G.ed || (!G.editing && G.loaded && revs(G.loaded) !== G.built)) build(v);
    if (G.ed && !G.editing) G.ed.setOverlay({ edges: gw.edges, tanks: gw.tanks, notModelled: gw.not_modelled });
    const nm = Object.entries(gw.not_modelled || {});
    put(G.side, 
      h("h3", null, "Water now"), h("p", { class: "small" }, `Model ${gw.model}: a bounded water balance over the explicit pipes. Pressure, chemistry and suction are NOT MODELLED.`),
      v.model_error ? h("p", { class: "bad" }, `The model stopped: ${v.model_error}. This run is not valid evidence; reset the twin.`) : null,
      ledgerTable(gw),
      nm.length ? h("div", null, h("strong", { class: "small" }, "NOT MODELLED"), h("ul", { class: "small" }, nm.map(([k, why]) => h("li", null, `${k}: ${why}`)))) : null,
      gw.notes.length ? h("ul", { class: "small warn" }, gw.notes.map(x => h("li", null, x))) : null,
      scenario(v, gw));
  }
  function scenario(v, gw) {
    const tanks = Object.entries(gw.tanks).filter(([, t]) => t.capacity_L);
    if (!tanks.length || !can("sim_command")) return null;
    const sel = h("select", { "aria-label": "Tank" }, tanks.map(([k]) => h("option", { value: k }, k)));
    const litres = h("input", { type: "number", min: 0, step: "any", class: "narrow-num", "aria-label": "Litres" });
    return h("div", null, h("h3", null, "Scenario"), h("div", { class: "row" }, sel, litres, h("span", { class: "small" }, "L"),
      h("button", { class: "small", onclick: async () => { await call("lvl", "POST", "api/twin/graph/level", { node: sel.value, litres: Number(litres.value) }, "set; recorded as a mass adjustment"); } }, "Set the water"), note("lvl")),
      h("p", { class: "muted small" }, "A set level is an explicit adjustment in the ledger, never hidden water. Faults are injected on each equipment card below."));
  }
  function build(v) {
    if (!G.loaded) return;
    const host = h("div", { class: "hed-host" }), side = h("div", { class: "hed-side-twin" });
    G.side = side;
    const { topo, dia } = G.loaded;
    G.ed = window.AqHydraulicEditor.create(host, { symbols: topo.symbols, items: topo.items, graph: topo.graph, diagram: dia.diagram,
      readOnly: !G.editing, serverIssues: topo.issues });
    G.rev = { t: topo.rev, d: dia.rev };
    G.built = revs(G.loaded);
    const save = async () => {
      const cur = G.ed.get();
      const r = await call("save", "POST", "api/drawing/installation", { graph: cur.graph, diagram: cur.diagram, expected_topology_rev: G.rev.t, expected_diagram_rev: G.rev.d },
        "saved in the twin");
      if (r) { G.editing = false; await load(); build(v); }
      else bar();
    };
    const bar = () => put(G.bar, 
      G.editing ? [h("button", { onclick: save }, "Save in the twin"), h("button", { class: "ghost", onclick: async () => { G.editing = false; await load(); build(v); } }, "Cancel")]
        : can("equipment_edit") ? h("button", { onclick: async () => { await load(); G.editing = true; build(v); } }, "Edit the drawing") : null,
      note("save"));
    G.bar = h("div", { class: "row" });
    bar();
    put(G.body, h("h2", null, "Installation graph"), G.bar, host, side,
      h("p", { class: "muted small" }, "The water follows only the connections drawn here; a crossing never joins pipes. Saving here changes the twin, not the real system."));
  }
  async function update(v) {
    if (!G.body) return;
    if (G.editing) return;                                // never rebuilt under the editor
    const now = Date.now();
    // loaded again when the snapshot names another graph or drawing revision (an import, a reset, an edit), and
    // every 30 s anyway
    const gw = v.graph_water;
    if (!G.loaded || gw && gw.revs !== revs(G.loaded) || now - G.last > 30000) {
      G.last = now;
      try { await load(); } catch (e) { return; }
    }
    if (!G.side) G.side = h("div");
    draw(v);
  }
  window.AqTwinGraph = { card, update };
})();
