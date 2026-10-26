/* AquaCrop hydraulic drawing editor (0.7.0; PIPE_ROUTING_AND_DIAGRAM_LAYOUT.md). Used by Blocks on the real
   dashboard and by the digital twin page. Needs router.js and icons.js.

   What it edits: the explicit graph (nodes, ports, edges, junctions, sensor associations) and one drawing of it
   (symbols and routes). Connections exist only as edges: dragging, aligning, rerouting or a crossing never joins
   two pipes. A crossing is drawn with a gap in the pipe whose id sorts later, a connected junction with a dot.
   Every pipe on screen, during a drag too, is horizontal and vertical segments only; when no route fits, the last
   committed drawing stays and the reason is written beside the toolbar. Nothing is sent until Save.

   Gestures work with mouse, touch and keyboard: tap to select, drag a symbol or a segment handle, arrow keys move
   the selection by one grid step (Shift: five), Delete removes it, Escape cancels, Ctrl+Z / Ctrl+Y undo and redo. */
"use strict";
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const R = window.AqRouter;
  const ROT_SIDE = { 0: "NESW", 90: "ESWN", 180: "SWNE", 270: "WNES" };
  const KIND_ICON = { source: "drop", tank: "tank", pump: "pump", valve: "valve", meter: "flow", sensor: "sensor", sector: "drop", junction: null };
  const KIND_WORD = { source: "Water source", tank: "Tank", pump: "Pump", valve: "Valve", meter: "Flow meter", sensor: "Sensor", sector: "Sector", junction: "Junction" };

  function svg(tag, attrs, ...kids) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) { const v = attrs[k]; if (v == null || v === false) continue; if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v); }
    for (const k of kids.flat(Infinity)) if (k != null && k !== false) e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    return e;
  }
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const k in attrs || {}) { const v = attrs[k]; if (v == null || v === false) continue; if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v); }
    for (const k of kids.flat(Infinity)) if (k != null && k !== false) e.appendChild(typeof k === "string" || typeof k === "number" ? document.createTextNode(String(k)) : k);
    return e;
  }
  const clone = x => JSON.parse(JSON.stringify(x));

  /** Box (x, y, w, h) as drawn, and port anchors: the same rules as aquacrop/diagram.py. */
  function geometry(symbols) {
    function box(kind, s) { const d = symbols[kind]; return s.rot === 90 || s.rot === 270 ? { x: s.x, y: s.y, w: d.h, h: d.w } : { x: s.x, y: s.y, w: d.w, h: d.h }; }
    function anchor(kind, s, port) {
      const p = symbols[kind].ports.find(q => q.id === port);
      if (!p) return null;
      const moved = (s.ports || {})[port];
      const side0 = moved ? moved.side : p.side, off = moved ? moved.offset : p.offset;
      const side = ROT_SIDE[s.rot || 0]["NESW".indexOf(side0)];
      const b = box(kind, s);
      let x, y;
      if (kind === "junction") { x = b.x; y = b.y; }
      else if (side === "N") { x = b.x + off * b.w; y = b.y; }
      else if (side === "E") { x = b.x + b.w; y = b.y + off * b.h; }
      else if (side === "S") { x = b.x + (1 - off) * b.w; y = b.y + b.h; }
      else { x = b.x; y = b.y + (1 - off) * b.h; }
      return { x: R.snap(x), y: R.snap(y), side };
    }
    return { box, anchor };
  }

  /**
   * create(root, { symbols, items, graph, diagram, readOnly, onChange(dirty), names })
   * -> { get(): {graph, diagram}, set(graph, diagram), dirty(), select(id), issues(), destroy() }
   */
  function create(root, opt) {
    const SYM = opt.symbols, G = geometry(SYM);
    const st = { graph: clone(opt.graph), diagram: clone(opt.diagram), undo: [], redo: [], sel: null, tool: "select",
      pending: null, msg: null, zoom: 1, snap: true, dirty: false, drag: null };
    const names = () => Object.assign({}, opt.names || {});
    const nameOf = id => names()[id] || (opt.items[(node(id) || {}).item] || {}).name || id;
    function node(id) { return st.graph.nodes.find(n => n.id === id); }
    function sym(id) { return st.diagram.symbols.find(s => s.node === id); }
    function edge(id) { return st.graph.edges.find(e => e.id === id); }
    function route(id) { return st.diagram.routes.find(r => r.edge === id); }
    const grid = () => st.diagram.grid || 10;
    const snapG = v => st.snap ? Math.round(v / grid()) * grid() : R.snap(v);

    // ---------------------------------------------------------------- history
    function checkpoint() { st.undo.push(clone({ graph: st.graph, diagram: st.diagram })); if (st.undo.length > 100) st.undo.shift(); st.redo = []; }
    function changed() { st.dirty = true; if (opt.onChange) opt.onChange(true); draw(); }
    function undo() { if (!st.undo.length) return say("Nothing to undo."); st.redo.push(clone({ graph: st.graph, diagram: st.diagram })); Object.assign(st, st.undo.pop()); st.sel = null; changed(); say("Undone."); }
    function redo() { if (!st.redo.length) return say("Nothing to redo."); st.undo.push(clone({ graph: st.graph, diagram: st.diagram })); Object.assign(st, st.redo.pop()); st.sel = null; changed(); say("Redone."); }
    function say(text, bad) { st.msg = text ? { text, bad: !!bad } : null; drawStatus(); }

    // ---------------------------------------------------------------- routing
    function obstacles(except) {
      return st.diagram.symbols.filter(s => node(s.node) && node(s.node).kind !== "junction" && !(except || []).includes(s.node))
        .map(s => Object.assign({ id: s.node }, G.box(node(s.node).kind, s)));
    }
    function endpoints(e) {
      const a = node(e.src.node), b = node(e.dst.node), sa = sym(e.src.node), sb = sym(e.dst.node);
      if (!a || !b || !sa || !sb) return null;
      const pa = G.anchor(a.kind, sa, e.src.port), pb = G.anchor(b.kind, sb, e.dst.port);
      if (!pa || !pb) return null;
      return { start: Object.assign(pa, { owner: a.kind === "junction" ? null : a.id }), end: Object.assign(pb, { owner: b.kind === "junction" ? null : b.id }) };
    }
    function routeEdge(e, r, extra) {
      const ends = endpoints(e);
      if (!ends) return { ok: false, reason: `${e.id}: both ends must be drawn in this view` };
      const others = st.diagram.routes.filter(x => x.edge !== e.id && x.points && x.points.length >= 2).map(x => x.points);
      return R.route(Object.assign({ start: ends.start, end: ends.end, obstacles: obstacles(), others,
        pins: r && r.mode === "MANUAL" ? r.pins : [], previous: r && r.points && r.points.length ? r.points : null,
        bounds: { x: -60, y: -60, w: st.diagram.width + 120, h: st.diagram.height + 120 }, clearance: 8, stub: 20 }, extra || {}));
    }
    /** Reroute the given edges in place. All or nothing: a failure leaves every route as it was. */
    function reroute(edgeIds) {
      const before = new Map(st.diagram.routes.map(r => [r.edge, r.points]));
      const notes = [];
      for (const id of edgeIds) {
        const e = edge(id), r = route(id);
        if (!e || !r) continue;
        const res = routeEdge(e, r);
        if (!res.ok) { st.diagram.routes.forEach(x => { x.points = before.get(x.edge); }); return { ok: false, reason: `${pipeName(e)}: ${res.reason}` }; }
        r.points = res.points;
        if (res.pinsDropped) notes.push(`${pipeName(e)}: your bend could not be kept here`);
      }
      return { ok: true, notes };
    }
    function edgesOf(nodeId) { return st.graph.edges.filter(e => e.src.node === nodeId || e.dst.node === nodeId).map(e => e.id); }
    function pipeName(e) { return `pipe ${nameOf(e.src.node)} → ${nameOf(e.dst.node)}`; }

    // ---------------------------------------------------------------- operations
    function placeItem(itemId) {
      const it = opt.items[itemId];
      if (!it || !it.kind) return say("This equipment has no water kind: it is not part of the graph.", true);
      if (st.graph.nodes.some(n => n.item === itemId)) return say(`${it.name} is already in the graph: one item is one symbol.`, true);
      checkpoint();
      st.graph.nodes.push({ id: itemId, kind: it.kind, item: itemId });
      const spot = freeSpot(it.kind);
      st.diagram.symbols.push({ node: itemId, x: spot.x, y: spot.y, rot: 0, label: "", ports: {} });
      st.sel = { type: "symbol", id: itemId };
      changed(); say(`${it.name} placed. Drag it where it belongs, then connect its ports.`);
    }
    function freeSpot(kind) {
      const d = SYM[kind];
      for (let y = 40; y < st.diagram.height - d.h; y += 60) for (let x = 40; x < st.diagram.width - d.w; x += 60) {
        const b = { x, y, w: d.w, h: d.h };
        if (!obstacles().some(o => b.x < o.x + o.w + 20 && b.x + b.w + 20 > o.x && b.y < o.y + o.h + 20 && b.y + b.h + 20 > o.y)) return { x, y };
      }
      return { x: 40, y: 40 };
    }
    function portFree(n, port) {
      const p = SYM[n.kind].ports.find(q => q.id === port);
      return p && (p.many || !st.graph.edges.some(e => (e.src.node === n.id && e.src.port === port) || (e.dst.node === n.id && e.dst.port === port)));
    }
    function connect(a, b) {
      const na = node(a.node), nb = node(b.node);
      const pa = SYM[na.kind].ports.find(q => q.id === a.port), pb = SYM[nb.kind].ports.find(q => q.id === b.port);
      if (na.id === nb.id) return say("A pipe joins two different pieces of equipment.", true);
      if (!["out", "both"].includes(pa.flow)) return say(`Water cannot leave ${nameOf(na.id)} by its ${pa.label.toLowerCase()}: start from an outlet.`, true);
      if (!["in", "both"].includes(pb.flow)) return say(`Water cannot enter ${nameOf(nb.id)} by its ${pb.label.toLowerCase()}.`, true);
      if (!portFree(na, a.port) || !portFree(nb, b.port)) return say("That port already has its pipe. Add a junction to branch.", true);
      if (st.graph.edges.some(e => e.src.node === a.node && e.src.port === a.port && e.dst.node === b.node && e.dst.port === b.port)) return say("These ports are already connected.", true);
      checkpoint();
      let n = st.graph.edges.length + 1, id;
      do { id = `e-${n++}`; } while (edge(id));
      st.graph.edges.push({ id, src: { node: a.node, port: a.port }, dst: { node: b.node, port: b.port }, medium: "WATER" });
      st.diagram.routes.push({ edge: id, points: [], mode: "AUTO", pins: [] });
      const r = reroute([id]);
      if (!r.ok) { Object.assign(st, st.undo.pop()); draw(); return say(`Not connected: ${r.reason}`, true); }
      st.sel = { type: "route", id };
      changed(); say(`Connected ${nameOf(a.node)} ${pa.label.toLowerCase()} to ${nameOf(b.node)} ${pb.label.toLowerCase()}.`);
    }
    /** Split a pipe at a point of one of its segments with an explicit junction (a deliberate Join). */
    function joinHere(edgeId, seg, pt) {
      const e = edge(edgeId), r = route(edgeId);
      const a = r.points[seg], b = r.points[seg + 1];
      const horiz = a[1] === b[1];
      const x = horiz ? snapG(Math.min(Math.max(pt[0], Math.min(a[0], b[0]) + 5), Math.max(a[0], b[0]) - 5)) : a[0];
      const y = horiz ? a[1] : snapG(Math.min(Math.max(pt[1], Math.min(a[1], b[1]) + 5), Math.max(a[1], b[1]) - 5));
      checkpoint();
      let n = 1, jid; do { jid = `j-${n++}`; } while (node(jid));
      const dirIn = horiz ? (b[0] > a[0] ? "w" : "e") : (b[1] > a[1] ? "n" : "s");
      const dirOut = { w: "e", e: "w", n: "s", s: "n" }[dirIn];
      st.graph.nodes.push({ id: jid, kind: "junction", item: null });
      st.diagram.symbols.push({ node: jid, x, y, rot: 0, label: "", ports: {} });
      st.graph.edges = st.graph.edges.filter(x2 => x2.id !== edgeId);
      st.diagram.routes = st.diagram.routes.filter(x2 => x2.edge !== edgeId);
      const e1 = { id: edgeId + "-a", src: e.src, dst: { node: jid, port: dirIn }, medium: e.medium, lineage: edgeId };
      const e2 = { id: edgeId + "-b", src: { node: jid, port: dirOut }, dst: e.dst, medium: e.medium, lineage: edgeId };
      st.graph.edges.push(e1, e2);
      st.diagram.routes.push({ edge: e1.id, points: [], mode: "AUTO", pins: [] }, { edge: e2.id, points: [], mode: "AUTO", pins: [] });
      const res = reroute([e1.id, e2.id]);
      if (!res.ok) { Object.assign(st, st.undo.pop()); draw(); return say(`No junction: ${res.reason}`, true); }
      st.sel = { type: "symbol", id: jid };
      changed(); say(`Junction added: ${pipeName(e)} now joins here. Connect its free branch with Connect pipe.`);
    }
    function removeSelection() {
      if (!st.sel) return;
      checkpoint();
      if (st.sel.type === "route") {
        st.graph.edges = st.graph.edges.filter(e => e.id !== st.sel.id);
        st.diagram.routes = st.diagram.routes.filter(r => r.edge !== st.sel.id);
        st.graph.measures = (st.graph.measures || []).filter(m => m.edge !== st.sel.id);
      } else {
        const id = st.sel.id, gone = edgesOf(id);
        st.graph.nodes = st.graph.nodes.filter(n => n.id !== id);
        st.diagram.symbols = st.diagram.symbols.filter(s => s.node !== id);
        st.graph.edges = st.graph.edges.filter(e => !gone.includes(e.id));
        st.diagram.routes = st.diagram.routes.filter(r => !gone.includes(r.edge));
        st.graph.measures = (st.graph.measures || []).filter(m => m.sensor !== id && m.node !== id && !gone.includes(m.edge));
      }
      st.sel = null; changed(); say("Removed. Undo puts it back.");
    }
    function moveSymbol(id, x, y, commit) {
      const s = sym(id), old = { x: s.x, y: s.y };
      s.x = x; s.y = y;
      const r = reroute(edgesOf(id));
      if (!r.ok) { s.x = old.x; s.y = old.y; reroute(edgesOf(id)); say(`Not moved there: ${r.reason}`, true); return false; }
      if (commit && r.notes.length) say(r.notes.join("; "));
      return true;
    }
    function rotate(id) {
      const s = sym(id); if (!s || node(id).kind === "junction") return;
      checkpoint();
      s.rot = ((s.rot || 0) + 90) % 360;
      const r = reroute(edgesOf(id));
      if (!r.ok) { Object.assign(st, st.undo.pop()); draw(); return say(`Not turned: ${r.reason}`, true); }
      changed(); say(`${nameOf(id)} turned a quarter.`);
    }
    function rerouteSelected() {
      if (!st.sel || st.sel.type !== "route") return say("Select a pipe first.", true);
      checkpoint();
      const r = route(st.sel.id); r.mode = "AUTO"; r.pins = []; r.points = [];
      const res = reroute([st.sel.id]);
      if (!res.ok) { Object.assign(st, st.undo.pop()); draw(); return say(res.reason, true); }
      changed(); say("Rerouted automatically; your bends were removed.");
    }
    function setMeasure(sensorId, target) {
      checkpoint();
      st.graph.measures = (st.graph.measures || []).filter(m => m.sensor !== sensorId);
      if (target) st.graph.measures.push(target.startsWith("edge:") ? { sensor: sensorId, edge: target.slice(5), mode: "TAP" } : { sensor: sensorId, node: target, mode: "TAP" });
      changed();
    }

    // ---------------------------------------------------------------- drawing
    const wrap = el("div", { class: "hed" });
    const bar = el("div", { class: "row hed-bar", role: "toolbar", "aria-label": "Drawing tools" });
    const status = el("p", { class: "hed-msg", role: "status", "aria-live": "polite" });
    const canvas = svg("svg", { class: "hed-canvas", role: "application", tabindex: "0", "aria-label": "Hydraulic drawing editor" });
    const side = el("div", { class: "hed-side" });
    wrap.append(bar, status, el("div", { class: "hed-body" }, el("div", { class: "hed-wrap diagram-wrap" }, canvas), side));
    root.replaceChildren(wrap);

    function toolButton(label, on, active, disabled, title) {
      return el("button", { class: "small" + (active ? "" : " ghost"), "aria-pressed": active ? "true" : null, disabled: !!disabled, title, onclick: on }, label);
    }
    function drawBar() {
      const ro = opt.readOnly;
      bar.replaceChildren(
        toolButton("Select", () => { st.tool = "select"; st.pending = null; draw(); }, st.tool === "select"),
        toolButton("Connect pipe", () => { st.tool = "connect"; st.pending = null; draw(); say("Choose an outlet port, then the port it feeds."); }, st.tool === "connect", ro),
        toolButton("Junction", () => { st.tool = "junction"; st.pending = null; draw(); say("Choose the point of a pipe where a branch joins it."); }, st.tool === "junction", ro),
        toolButton("Undo", undo, false, ro || !st.undo.length), toolButton("Redo", redo, false, ro || !st.redo.length),
        toolButton("Reroute selected", rerouteSelected, false, ro || !(st.sel && st.sel.type === "route")),
        toolButton("Turn", () => st.sel && st.sel.type === "symbol" && rotate(st.sel.id), false, ro || !(st.sel && st.sel.type === "symbol")),
        toolButton("Remove", removeSelection, false, ro || !st.sel),
        toolButton("Zoom in", () => { st.zoom = Math.min(4, st.zoom * 1.25); draw(); }), toolButton("Zoom out", () => { st.zoom = Math.max(0.25, st.zoom / 1.25); draw(); }),
        toolButton("Fit", () => { st.zoom = 1; draw(); }),
        el("label", { class: "small" }, el("input", { type: "checkbox", checked: st.snap ? true : null, onchange: ev => { st.snap = ev.target.checked; } }), " Snap to grid"));
    }
    function drawStatus() {
      status.textContent = st.msg ? st.msg.text : "";
      status.className = "hed-msg " + (st.msg && st.msg.bad ? "bad" : "");
    }
    function gapsPath(points, gaps) {
      // a polyline with a short rectangular gap at each crossing it yields (no curved bridge)
      const G2 = 5;
      let d = "";
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1], b = points[i];
        const cuts = gaps.filter(g => (a[1] === b[1] && g[1] === a[1] && Math.min(a[0], b[0]) < g[0] && g[0] < Math.max(a[0], b[0])) ||
          (a[0] === b[0] && g[0] === a[0] && Math.min(a[1], b[1]) < g[1] && g[1] < Math.max(a[1], b[1])));
        const horiz = a[1] === b[1], dir = horiz ? Math.sign(b[0] - a[0]) : Math.sign(b[1] - a[1]);
        cuts.sort((p, q) => horiz ? dir * (p[0] - q[0]) : dir * (p[1] - q[1]));
        let cur = a;
        d += `M${cur[0]} ${cur[1]}`;
        for (const g of cuts) {
          const before = horiz ? [g[0] - dir * G2, g[1]] : [g[0], g[1] - dir * G2];
          const after = horiz ? [g[0] + dir * G2, g[1]] : [g[0], g[1] + dir * G2];
          d += `L${before[0]} ${before[1]}M${after[0]} ${after[1]}`;
          cur = after;
        }
        d += `L${b[0]} ${b[1]}`;
      }
      return d;
    }
    function draw() {
      drawBar(); drawStatus();
      const W = st.diagram.width, H = st.diagram.height;
      canvas.setAttribute("viewBox", `-40 -40 ${W + 80} ${H + 80}`);
      canvas.setAttribute("width", Math.round((W + 80) * st.zoom)); canvas.setAttribute("height", Math.round((H + 80) * st.zoom));
      const kids = [svg("defs", null, svg("pattern", { id: "hed-grid", width: grid(), height: grid(), patternUnits: "userSpaceOnUse" },
        svg("path", { d: `M ${grid()} 0 L 0 0 0 ${grid()}`, class: "hed-gridline" }))),
        svg("rect", { x: -40, y: -40, width: W + 80, height: H + 80, fill: "url(#hed-grid)", class: "hed-bg", "data-bg": "1" })];
      const routes = {};
      for (const r of st.diagram.routes) if (r.points && r.points.length >= 2) routes[r.edge] = r.points;
      const gaps = R.gaps(routes);
      for (const r of st.diagram.routes) {
        const e = edge(r.edge); if (!e) continue;
        const selected = st.sel && st.sel.type === "route" && st.sel.id === r.edge;
        if (!r.points || r.points.length < 2) continue;
        const desc = `${pipeName(e)}` + (gaps[r.edge] ? `; crosses ${gaps[r.edge].length} other pipe(s) without connecting` : "");
        const q = st.overlay && st.overlay.edges ? st.overlay.edges[r.edge] : null;
        kids.push(svg("path", { d: gapsPath(r.points, gaps[r.edge] || []), class: "hed-pipe" + (selected ? " sel" : "") + (e.medium === "DRAIN" ? " drain" : "") + (q ? " flowing" : ""),
          "data-edge": r.edge, tabindex: "0", role: "button", "aria-label": desc, onpointerdown: ev => pipeDown(ev, r.edge) }, svg("title", null, desc)));
        // a wide transparent twin makes thin pipes easy to tap
        kids.push(svg("path", { d: gapsPath(r.points, []), class: "hed-hit", "data-edge-hit": r.edge, onpointerdown: ev => pipeDown(ev, r.edge) }));
        if (q) {                                            // simulated flow: a number, never an animation of unknown flow
          const m = r.points[Math.floor((r.points.length - 1) / 2)], n2 = r.points[Math.floor((r.points.length - 1) / 2) + 1];
          kids.push(svg("text", { x: (m[0] + n2[0]) / 2 + 4, y: (m[1] + n2[1]) / 2 - 4, class: "hed-flow" }, `${q.toFixed(1)} L/min`));
        }
        if (selected && !opt.readOnly) for (let i = 0; i < r.points.length - 1; i++) {
          const a = r.points[i], b = r.points[i + 1], mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
          kids.push(svg("rect", { x: mx - 7, y: my - 7, width: 14, height: 14, class: "hed-handle", "data-seg": i, tabindex: "0",
            "aria-label": `Move ${a[1] === b[1] ? "horizontal" : "vertical"} segment ${i + 1}`, onpointerdown: ev => segDown(ev, r.edge, i) }));
        }
      }
      for (const s of st.diagram.symbols) {
        const n = node(s.node); if (!n) continue;
        const selected = st.sel && st.sel.type === "symbol" && st.sel.id === s.node;
        if (n.kind === "junction") {
          kids.push(svg("circle", { cx: s.x, cy: s.y, r: 5, class: "hed-junction" + (selected ? " sel" : ""), "data-node": s.node, tabindex: "0", role: "button",
            "aria-label": `Connected junction ${s.node}`, onpointerdown: ev => symDown(ev, s.node) }, svg("title", null, "connected junction")));
        } else {
          const b = G.box(n.kind, s);
          const label = s.label || nameOf(n.id);
          const g = svg("g", { class: "hed-sym" + (selected ? " sel" : ""), "data-node": s.node, tabindex: "0", role: "button",
            "aria-label": `${KIND_WORD[n.kind]} ${label}`, onpointerdown: ev => symDown(ev, s.node) },
            svg("rect", { x: b.x, y: b.y, width: b.w, height: b.h, rx: n.kind === "tank" ? 4 : 8, class: "hed-box k-" + n.kind }),
            iconAt(KIND_ICON[n.kind], b), svg("text", { x: b.x + b.w / 2, y: b.y + b.h + 14, class: "hed-label", "text-anchor": "middle" }, label));
          if (st.overlay && st.overlay.tanks && st.overlay.tanks[n.id]) {
            const tk = st.overlay.tanks[n.id];
            if (tk.pct != null) {
              const hgt = Math.max(0, Math.min(1, tk.pct / 100)) * (b.h - 4);
              g.insertBefore(svg("rect", { x: b.x + 2, y: b.y + b.h - 2 - hgt, width: b.w - 4, height: hgt, class: "hed-water" }), g.childNodes[1]);
            }
            g.appendChild(svg("text", { x: b.x + b.w / 2, y: b.y + 14, class: "hed-label", "text-anchor": "middle" },
              tk.pct == null ? "NOT MODELLED" : `${tk.L.toFixed(0)} L (${tk.pct}%)`));
          }
          if (st.overlay && st.overlay.notModelled && st.overlay.notModelled[n.id]) g.appendChild(svg("title", null, `NOT MODELLED: ${st.overlay.notModelled[n.id]}`));
          kids.push(g);
        }
        const showPorts = st.tool === "connect" || selected;
        if (showPorts) for (const p of SYM[n.kind].ports) {
          const a = G.anchor(n.kind, s, p.id);
          const ok = st.tool !== "connect" ? true : st.pending ? (["in", "both"].includes(p.flow) && st.pending.node !== n.id && portFree(n, p.id)) : (["out", "both"].includes(p.flow) && portFree(n, p.id));
          kids.push(svg("circle", { cx: a.x, cy: a.y, r: st.tool === "connect" ? 7 : 4, class: "hed-port" + (ok ? " ok" : " no") + (st.pending && st.pending.node === n.id && st.pending.port === p.id ? " pend" : ""),
            "data-port": `${n.id}.${p.id}`, tabindex: st.tool === "connect" ? "0" : null, role: st.tool === "connect" ? "button" : null,
            "aria-label": `${nameOf(n.id)} ${p.label}${ok ? "" : " (not available)"}`, onpointerdown: ev => portDown(ev, n.id, p.id, ok) }, svg("title", null, `${p.label} (${p.flow})`)));
        }
      }
      for (const m of st.graph.measures || []) {                // a dashed line from a sensor to what it measures
        const s = sym(m.sensor), n = node(m.sensor); if (!s || !n) continue;
        const b = G.box(n.kind, s);
        let to = null;
        if (m.node && sym(m.node)) { const t = G.box(node(m.node).kind, sym(m.node)); to = [t.x + t.w / 2, t.y + t.h / 2]; }
        if (m.edge && routes[m.edge]) { const p = routes[m.edge]; to = p[Math.floor(p.length / 2)]; }
        if (to) kids.push(svg("path", { d: `M${b.x + b.w / 2} ${b.y + b.h / 2}L${to[0]} ${b.y + b.h / 2}L${to[0]} ${to[1]}`, class: "hed-tap" }, svg("title", null, `${nameOf(m.sensor)} measures this (a tap: it carries no water)`)));
      }
      canvas.replaceChildren(...kids);
      drawSide();
    }
    function iconAt(name, b) {
      if (!name || !window.AqIcons) return null;
      const i = window.AqIcons.icon(name, 24);
      const g = svg("g", { transform: `translate(${b.x + b.w / 2 - 12} ${b.y + b.h / 2 - 12})`, class: "hed-icon" });
      for (const c of [...i.childNodes]) g.appendChild(c);
      return g;
    }

    // ---------------------------------------------------------------- side panel: palette, properties, checks
    function drawSide() {
      const parts = [];
      const placed = new Set(st.graph.nodes.map(n => n.item).filter(Boolean));
      const free = Object.entries(opt.items).filter(([id, it]) => it.kind && !placed.has(id));
      parts.push(el("h3", null, "Equipment"), free.length ? el("ul", { class: "hed-palette" }, free.map(([id, it]) =>
        el("li", null, `${it.name} (${KIND_WORD[it.kind].toLowerCase()}) `, opt.readOnly ? null : el("button", { class: "small", onclick: () => placeItem(id) }, "Place"))))
        : el("p", { class: "muted small" }, "Every water item is in the drawing. Create more in the equipment library."));
      if (st.sel && st.sel.type === "symbol") {
        const n = node(st.sel.id), s = sym(st.sel.id);
        const conns = st.graph.edges.filter(e => e.src.node === n.id || e.dst.node === n.id);
        parts.push(el("h3", null, n.kind === "junction" ? "Junction" : nameOf(n.id)),
          el("p", { class: "small" }, `${KIND_WORD[n.kind]}${n.item ? " · item " + n.item : ""} · position ${s.x}, ${s.y}${s.rot ? " · turned " + s.rot + "°" : ""}`),
          el("ul", { class: "small" }, SYM[n.kind].ports.map(p => {
            const c = conns.filter(e => (e.src.node === n.id && e.src.port === p.id) || (e.dst.node === n.id && e.dst.port === p.id));
            return el("li", null, `${p.label}: `, c.length ? c.map(e => pipeName(e)).join("; ") : el("span", { class: "muted" }, "not connected"));
          })));
        if (n.kind === "sensor") {
          const cur = (st.graph.measures || []).find(m => m.sensor === n.id);
          const sel = el("select", { "aria-label": `What ${nameOf(n.id)} measures`, disabled: opt.readOnly ? true : null, onchange: ev => setMeasure(n.id, ev.target.value) },
            el("option", { value: "" }, "— measures nothing yet —"),
            st.graph.nodes.filter(x => x.kind === "tank" || x.kind === "sector").map(x => el("option", { value: x.id, selected: cur && cur.node === x.id ? true : null }, `level of ${nameOf(x.id)}`)),
            st.graph.edges.map(e => el("option", { value: "edge:" + e.id, selected: cur && cur.edge === e.id ? true : null }, `tap on ${pipeName(e)}`)));
          parts.push(el("label", { class: "small" }, "Measures ", sel), el("p", { class: "muted small" }, "A sensor is associated, not piped: it carries no water. An inline flow meter is placed on the pipe itself."));
        }
      } else if (st.sel && st.sel.type === "route") {
        const e = edge(st.sel.id), r = route(st.sel.id);
        const others = st.diagram.routes.filter(x => x.edge !== r.edge && x.points && x.points.length >= 2);
        const cross = others.filter(x => R.crossings(r.points, x.points).length).map(x => pipeName(edge(x.edge)));
        parts.push(el("h3", null, "Pipe"), el("p", { class: "small" }, pipeName(e)),
          el("p", { class: "small" }, `${r.mode === "MANUAL" ? "Routed by you (" + r.pins.length + " kept bend" + (r.pins.length === 1 ? "" : "s") + ")" : "Routed automatically"} · ${r.points.length - 1} segment(s)`),
          cross.length ? el("p", { class: "small" }, `Crosses ${cross.join(", ")}: pipes cross; not connected.`) : null,
          el("p", { class: "muted small" }, "Drag a square handle to move that segment; the ends stay on their ports. Use Junction to join pipes deliberately."));
      }
      const checks = issues();
      parts.push(el("h3", null, "Checks"), checks.length ? el("ul", { class: "hed-issues" }, checks.map(i => el("li", { class: i.severity === "ERROR" ? "bad" : "warn" },
        `${i.message}. ${i.fix}. `, objectOf(i) ? el("button", { class: "small ghost", onclick: () => { st.sel = objectOf(i); draw(); } }, "Show on diagram") : null)))
        : el("p", { class: "muted small" }, "No problem found in the drawing. Save, then validate the blocks that use it."));
      side.replaceChildren(...parts);
    }
    function objectOf(i) {
      const o = (i.object || "").split(".")[0];
      if (node(o)) return { type: "symbol", id: o };
      if (edge(o)) return { type: "route", id: o };
      return null;
    }
    /** Checks the editor can make itself (the server repeats them on Save, with the graph's own rules). */
    function issues() {
      const out = (opt.serverIssues || []).filter(i => node((i.object || "").split(".")[0]) || edge((i.object || "").split(".")[0]));
      for (const r of st.diagram.routes) {
        if (!edge(r.edge)) continue;
        if (!r.points || r.points.length < 2) out.push({ severity: "ERROR", object: r.edge, message: `${pipeName(edge(r.edge))} is not routed`, fix: "select it and press Reroute selected" });
        else if (!R.orthogonal(R.canonical(r.points))) out.push({ severity: "ERROR", object: r.edge, message: `${pipeName(edge(r.edge))} has a diagonal`, fix: "reroute it" });
      }
      for (const n of st.graph.nodes) {
        if (n.kind === "junction") continue;
        const c = edgesOf(n.id).length;
        if (!c && n.kind !== "sensor") out.push({ severity: "WARNING", object: n.id, message: `${nameOf(n.id)} has no pipe`, fix: "connect it or remove it" });
        if (n.kind === "sensor" && !(st.graph.measures || []).some(m => m.sensor === n.id)) out.push({ severity: "WARNING", object: n.id, message: `${nameOf(n.id)} measures nothing`, fix: "choose what it measures in its panel" });
      }
      return out;
    }

    // ---------------------------------------------------------------- pointer and keyboard
    function docPoint(ev) {
      const m = canvas.getScreenCTM(); if (!m) return [0, 0];
      const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
      return [p.x, p.y];
    }
    let frame = 0;
    function onFrame(fn) { if (frame) cancelAnimationFrame(frame); frame = requestAnimationFrame(() => { frame = 0; fn(); }); }
    function symDown(ev, id) {
      ev.stopPropagation(); canvas.focus();
      if (st.tool === "connect") return;
      st.sel = { type: "symbol", id };
      if (opt.readOnly) return draw();
      const s = sym(id), p0 = docPoint(ev);
      st.drag = { kind: "symbol", id, dx: p0[0] - s.x, dy: p0[1] - s.y, snap: clone({ graph: st.graph, diagram: st.diagram }), moved: false };
      canvas.setPointerCapture(ev.pointerId);
      draw();
    }
    function pipeDown(ev, id) {
      ev.stopPropagation(); canvas.focus();
      const r = route(id);
      if (st.tool === "junction" && !opt.readOnly) {
        const p = docPoint(ev);
        let best = 0, bd = Infinity;
        for (let i = 0; i < r.points.length - 1; i++) {
          const a = r.points[i], b = r.points[i + 1];
          const x = Math.max(Math.min(a[0], b[0]), Math.min(Math.max(a[0], b[0]), p[0])), y = Math.max(Math.min(a[1], b[1]), Math.min(Math.max(a[1], b[1]), p[1]));
          const d = Math.abs(x - p[0]) + Math.abs(y - p[1]);
          if (d < bd) { bd = d; best = i; }
        }
        st.tool = "select";
        return joinHere(id, best, p);
      }
      st.sel = { type: "route", id }; draw();
    }
    function segDown(ev, id, seg) {
      ev.stopPropagation(); canvas.focus();
      const r = route(id), p0 = docPoint(ev);
      st.drag = { kind: "segment", id, seg, p0, orig: clone(r.points), snap: clone({ graph: st.graph, diagram: st.diagram }), moved: false };
      canvas.setPointerCapture(ev.pointerId);
    }
    function portDown(ev, nodeId, port, ok) {
      if (st.tool !== "connect") return;
      ev.stopPropagation();
      if (!ok) return say(st.pending ? "That port cannot take this pipe." : "Start from an outlet that is still free.", true);
      if (!st.pending) { st.pending = { node: nodeId, port }; draw(); return say(`From ${nameOf(nodeId)}: now choose the port it feeds.`); }
      const a = st.pending; st.pending = null;
      connect(a, { node: nodeId, port });
    }
    canvas.addEventListener("pointermove", ev => {
      const d = st.drag; if (!d) return;
      const p = docPoint(ev);
      onFrame(() => {
        if (d.kind === "symbol") {
          const x = snapG(p[0] - d.dx), y = snapG(p[1] - d.dy), s = sym(d.id);
          if (x === s.x && y === s.y) return;
          if (moveSymbol(d.id, x, y, false)) { d.moved = true; say(null); }
          draw();
        } else if (d.kind === "segment") {
          const a = d.orig[d.seg], b = d.orig[d.seg + 1];
          const horiz = a[1] === b[1];
          const delta = horiz ? snapG(p[1]) - a[1] : snapG(p[0]) - a[0];
          const m = R.dragSegment(d.orig, d.seg, delta);
          const e = edge(d.id), ends = endpoints(e);
          const others = st.diagram.routes.filter(x => x.edge !== d.id && x.points && x.points.length >= 2).map(x => x.points);
          const q = { start: ends.start, end: ends.end, obstacles: obstacles(), others, clearance: 8,
            bounds: { x: -60, y: -60, w: st.diagram.width + 120, h: st.diagram.height + 120 } };
          if (R.orthogonal(m.points) && R.valid(m.points, q)) {
            const r = route(d.id); r.points = m.points; r.mode = "MANUAL";
            r.pins = (r.pins || []).filter(x => x.axis !== m.pin.axis).concat([m.pin]).slice(-4);
            d.moved = true; say(null);
          } else say("Not there: the pipe would cross equipment or run along another pipe.", true);
          draw();
        }
      });
    });
    function endDrag(cancel) {
      const d = st.drag; if (!d) return;
      st.drag = null;
      if (cancel) { Object.assign(st, d.snap); draw(); return say("Cancelled."); }
      if (d.moved) { st.undo.push(d.snap); st.redo = []; changed(); }
    }
    canvas.addEventListener("pointerup", () => endDrag(false));
    canvas.addEventListener("pointercancel", () => endDrag(true));
    canvas.addEventListener("pointerdown", ev => { if (ev.target.dataset && ev.target.dataset.bg) { st.sel = null; st.pending = null; draw(); } });
    canvas.addEventListener("keydown", ev => {
      if (ev.key === "Escape") { if (st.drag) endDrag(true); st.pending = null; st.tool = "select"; draw(); return; }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") { ev.preventDefault(); return undo(); }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "y") { ev.preventDefault(); return redo(); }
      if (opt.readOnly || !st.sel) return;
      if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); return removeSelection(); }
      const step = grid() * (ev.shiftKey ? 5 : 1);
      const v = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[ev.key];
      if (!v) return;
      ev.preventDefault();
      if (st.sel.type === "symbol") {
        const s = sym(st.sel.id), snap0 = clone({ graph: st.graph, diagram: st.diagram });
        if (moveSymbol(st.sel.id, s.x + v[0], s.y + v[1], true)) { st.undo.push(snap0); st.redo = []; changed(); } else draw();
      } else {                                              // the selected pipe's middle segment moves by a step
        const r = route(st.sel.id), i = Math.floor((r.points.length - 1) / 2);
        const a = r.points[i], b = r.points[i + 1];
        const delta = a[1] === b[1] ? v[1] : v[0];
        if (!delta) return;
        const snap0 = clone({ graph: st.graph, diagram: st.diagram });
        const m = R.dragSegment(r.points, i, delta);
        const e = edge(st.sel.id), ends = endpoints(e);
        const q = { start: ends.start, end: ends.end, obstacles: obstacles(), others: st.diagram.routes.filter(x => x.edge !== r.edge && x.points.length >= 2).map(x => x.points), clearance: 8 };
        if (R.valid(m.points, q)) { r.points = m.points; r.mode = "MANUAL"; r.pins = (r.pins || []).filter(x => x.axis !== m.pin.axis).concat([m.pin]).slice(-4); st.undo.push(snap0); st.redo = []; changed(); }
        else say("Not there: the pipe would cross equipment or run along another pipe.", true);
      }
    });

    /** Route every pipe that has no points yet (a migrated or imported drawing). */
    function routeMissing() {
      const missing = st.diagram.routes.filter(r => !r.points || r.points.length < 2).map(r => r.edge);
      const failed = [];
      for (const id of missing) { const r = reroute([id]); if (!r.ok) failed.push(r.reason); }
      if (missing.length) { st.dirty = true; if (opt.onChange) opt.onChange(true); }
      return failed;
    }
    const failedAtStart = routeMissing();
    draw();
    if (failedAtStart.length) say(`${failedAtStart.length} pipe(s) could not be routed yet: ${failedAtStart[0]}`, true);

    return {
      get: () => ({ graph: clone(st.graph), diagram: clone(st.diagram) }),
      /** Simulated values drawn on top (the twin): {edges: {id: L/min}, tanks: {id: {L, pct}}, notModelled}. */
      setOverlay(ov) { st.overlay = ov; if (!st.drag) draw(); },
      set(graph, diagram, serverIssues) { st.graph = clone(graph); st.diagram = clone(diagram); st.undo = []; st.redo = []; st.dirty = false; st.sel = null; opt.serverIssues = serverIssues || []; routeMissing(); draw(); },
      dirty: () => st.dirty,
      clean() { st.dirty = false; if (opt.onChange) opt.onChange(false); },
      say, select(id) { st.sel = node(id) ? { type: "symbol", id } : edge(id) ? { type: "route", id } : null; draw(); },
      issues, placeItem, connect, joinHere, moveSymbol, rotate, removeSelection, reroute, rerouteSelected, undo, redo,
      state: st,
    };
  }
  window.AqHydraulicEditor = { create, geometry };
})();
