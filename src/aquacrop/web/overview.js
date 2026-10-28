/* AquaCrop Overview diagram (0.7.1, owner decisions of 26 October 2026). Loaded after app.js, layout.js, plant.js,
   router.js and hydraulic-editor.js.

   The Overview is the approved installation's drawing: the graph exported from Digital Twin and applied here, read
   only, with every item's state written on it (running, stopped, open, closed, UNKNOWN, a fault, or PLANNED ·
   UNASSIGNED for equipment drawn before it has a channel: such an item never looks ready or running). Until a graph
   has been exported, the earlier drawing is still the Overview, and the card says how its successor is made. Selecting
   a symbol shows that item's readings beside the drawing. Nothing here switches, moves or connects anything. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, S } = U;
  const O = S.overviewUi = S.overviewUi || { sel: null };

  /** The state of one node, in words and a class, from the item's readings. */
  function stateOf(D, n) {
    if (n.kind === "junction") return null;
    const it = n.item ? D.items[n.item] : null;
    if (!n.item || !it) return { word: "NOT IN EQUIPMENT", cls: "unk", why: "this symbol is not linked to an equipment item" };
    const r = D.readings[n.item];
    if (!r) return { word: "NOT IN EQUIPMENT", cls: "unk", why: `${n.item} is not an active equipment item` };
    const ch = (D.channels || {})[n.item] || { roles: 0, required: [], bound: [] };
    if (!ch.roles) return null;                                  // a passive part (pipe-side tank, sector): nothing to read
    if (!ch.bound.length) return { word: "PLANNED · UNASSIGNED", cls: "planned", why: "no channel yet: it cannot run and shows no state" };
    const missing = ch.required.filter(x => !ch.bound.includes(x));
    if (missing.length) return { word: "INCOMPLETE", cls: "planned", why: `no channel for ${missing.join(", ")}: it cannot run` };
    if (r.fault) return { word: `FAULT: ${r.fault}`, cls: "fault", why: "a fault input reports a fault" };
    if (["pump", "valve"].includes(n.kind)) {
      const s = window.AqLayout.eqState(D.readings, { kind: n.kind, item: n.item });
      return { word: String(s.word).toUpperCase(), cls: s.st, why: s.why };
    }
    const rd = window.AqLayout.reading(D.readings, { kind: n.kind, item: n.item });
    return { word: rd.txt, cls: rd.ok ? "on" : "unk", why: rd.ok ? "" : "missing, stale or not scaled: UNKNOWN, never zero" };
  }

  function sidePanel(D) {
    const n = O.sel && D.topology.graph.nodes.find(x => x.id === O.sel);
    if (!n) return h("div", { class: "card side-panel" }, h("h3", null, "Nothing selected"),
      h("p", { class: "muted" }, "Select a pump, valve, tank, sensor or sector in the drawing to see its state and readings here."));
    const it = D.items[n.item] || {}, r = n.item ? D.readings[n.item] : null, st = stateOf(D, n);
    return h("div", { class: "card side-panel" }, h("h3", null, it.name || n.id),
      h("dl", { class: "kv" },
        h("dt", null, "ID"), h("dd", { class: "mono" }, it.public_id || n.item || "—"),
        st ? [h("dt", null, "State"), h("dd", null, h("span", { class: "pill st-" + st.cls }, st.word), " ", h("span", { class: "muted small" }, st.why))] : null,
        r ? [h("dt", null, "Output read back"), h("dd", null, r.coil), h("dt", null, "Feedback"), h("dd", null, r.feedback)] : null,
        r ? Object.entries(r.values).flatMap(([k, x]) => [h("dt", null, x.label || k), h("dd", null, x.value == null ? (x.quality === "NO CHANNEL" ? "no channel" : "no reading (UNKNOWN)")
          : `${typeof x.value === "boolean" ? (x.value ? "on" : "off") : x.value}${x.unit ? " " + x.unit : ""} (${String(x.quality).toLowerCase()})`)]) : null));
  }

  /** The Overview's drawing card, or null when the server has none to give. */
  async function card() {
    const D = await api("GET", "/api/overview/diagram").catch(() => null);
    if (!D) return null;
    // 0.7.3: the earlier drawing is drawn by the same editor and the same conversion as the twin's Import from
    // Overview (everything kept: unmapped symbols as marked placeholders, unconnected pipes as sketches)
    const L = D.explicit ? null : D.legacy_view;
    if (!D.explicit && !L) return h("p", { class: "muted small", "data-migration": "" }, D.migration);
    const graph = L ? L.topology : D.topology.graph, diagram = L ? L.diagram : D.diagram.diagram;
    const host = h("div", { class: "hed-host hed-view" });
    const states = {};
    for (const n of graph.nodes) { const s = stateOf(D, n); if (s) states[n.id] = s; }
    for (const p of diagram.placeholders || []) {               // a placeholder shows the reading of the item it was drawn with
      const s = p.item ? stateOf(D, { id: p.id, kind: p.kind, item: p.item }) : null;
      if (s) states["ph:" + p.id] = s;
    }
    if (L) {
      const k = L.kept || {};
      return h("div", { class: "card", id: "overview-diagram", "data-overview": "legacy" },
        h("div", { class: "row" }, h("h2", null, "Installation"), h("span", { class: "muted small" }, "the earlier drawing, as drawn")),
        h("div", { class: "diagram-wrap" }, (() => { const ed = window.AqHydraulicEditor.create(host, { symbols: D.symbols, items: D.items, graph, diagram, readOnly: true, viewOnly: true }); ed.setOverlay({ states }); return host; })()),
        h("p", { class: "muted small" }, `${k.symbols || 0} symbol(s) linked to equipment, ${k.placeholders || 0} marked NOT MAPPED, ${k.sketches || 0} pipe(s) kept as drawn (not connections), ${k.texts || 0} label(s).`),
        h("p", { class: "muted small", "data-migration": "" }, D.migration));
    }
    const ed = window.AqHydraulicEditor.create(host, { symbols: D.symbols, items: D.items, graph, diagram,
      readOnly: true, viewOnly: true, highlight: O.sel ? [O.sel] : [], onSelect: id => { O.sel = O.sel === id ? null : id; U.refresh(); } });
    ed.setOverlay({ states });
    const planned = Object.values(states).filter(s => s.cls === "planned").length;
    const TC = U.can("ops_read") ? await api("GET", "/api/twin-changes").catch(() => null) : null;   // viewers never see it
    return h("div", { class: "card", id: "overview-diagram", "data-overview": "graph" },
      h("div", { class: "row" }, h("h2", null, "Installation"), h("span", { class: "muted small" }, `drawing revision ${D.diagram.rev}, connections revision ${D.topology.rev}`)),
      h("div", { class: "diagram-layout" }, h("div", { class: "diagram-wrap" }, host), sidePanel(D)),
      TC && TC.pending.length ? h("p", { class: "warn small" }, `${TC.pending.length} change(s) exported from the Digital Twin wait for approval: `,
        h("a", { href: "#twin" }, "review them on the Digital Twin page"), ". The drawing above stays as it is until one is applied.") : null,
      planned ? h("p", { class: "small" }, U.pill("PLANNED · UNASSIGNED", "warn"), ` ${planned} item(s) are drawn but have no channel yet: they cannot run and show no state.`) : null,
      h("p", { class: "muted small", "data-migration": "" }, D.migration, " The drawing is changed in the Digital Twin and exported here; it never changes equipment channels or runs anything."));
  }
  window.AqOverview = { card, stateOf };
})();
