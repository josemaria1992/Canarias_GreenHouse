/* AquaCrop installation diagram from a layout (0.5.6). One drawing for the digital twin (live, replayed from its
 * timeline, or being edited) and for the real system's Overview (read-only, the layout accepted from the twin).
 * A symbol shows the state of the equipment item it is linked to: what the item's own channels report, never
 * a guess. An unlinked symbol says "not linked"; UNKNOWN is never drawn as OFF. All text uses textContent. */
"use strict";
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const FLOW_ON = 0.05;
  const WORDS = {
    pump: { on: "Running", up: "Starting…", off: "Stopped", fault: "Fault", unk: "Not known" },
    valve: { on: "Open", up: "Opening…", off: "Closed", fault: "Fault", unk: "Not known" },
    box: { on: "Running", up: "Starting…", off: "Stopped", fault: "Fault", unk: "Not known" },
  };
  const CLS = { on: "st-on", up: "st-move", off: "st-off", fault: "st-fault", unk: "st-unk" };
  const fixed = (v, d) => (Math.round(v * 10 ** d) / 10 ** d).toFixed(d);

  /** State of a pump, valve or box: fault, then what the item reports, then what was asked. */
  function eqState(R, n) {
    const kind = WORDS[n.kind] ? n.kind : "box";
    if (!n.item) return { st: "unk", cls: CLS.unk, word: n.planned ? "planned" : "Not known", why: n.planned ? "planned, not installed yet" : "not linked to equipment" };
    const r = R && R[n.item];
    if (!r) return { st: "unk", cls: CLS.unk, word: "Not known", why: `${n.item} is not in the equipment list` };
    let rep = null;
    if (r.feedback === "ON" || r.feedback === "OFF") rep = r.feedback === "ON";
    else if (r.feedback === "NONE" && (r.coil === "ON" || r.coil === "OFF")) rep = r.coil === "ON";
    const st = r.fault ? "fault" : rep === true ? "on" : r.commanded ? "up" : rep === false ? "off" : "unk";
    const why = r.fault ? `simulated fault: ${r.fault}` : rep === null ? "no feedback and no readback of the output" :
      r.feedback === "NONE" ? "from the output's readback (the item has no feedback signal)" : "from the item's feedback signal";
    return { st, cls: CLS[st], word: WORDS[kind][st], why, r };
  }
  /** A sensor's or tank's reading: the linked item's input role. */
  function reading(R, n) {
    if (!n.item) return { ok: false, txt: n.planned ? "planned" : "not linked", none: true };
    const r = R && R[n.item];
    if (!r) return { ok: false, txt: "not in the equipment", none: true };
    const roles = Object.keys(r.values || {});
    const v = r.values[n.role || roles.find(k => typeof r.values[k].value === "number") || roles[0]];
    if (!v) return { ok: false, txt: "no input", none: true };
    if (v.value == null) return { ok: false, txt: v.quality === "NO CHANNEL" ? "no channel" : "no reading", q: v.quality };
    const ok = v.quality === "GOOD" || v.quality === "OVERRIDDEN";
    const txt = typeof v.value === "boolean" ? (v.value ? "on" : "off") : `${fixed(v.value, Math.abs(v.value) < 10 ? 2 : 1)}${v.unit ? " " + v.unit : ""}`;
    return { ok, v: v.value, unit: v.unit, q: v.quality, max: v.max, txt: ok ? txt : `${txt} (${String(v.quality).toLowerCase()})` };
  }
  function pipeState(R, p) {
    if (p.flow_item) {
      const f = reading(R, { item: p.flow_item, role: p.flow_role });
      if (!f.none) return f.ok && typeof f.v === "number" ? (f.v > FLOW_ON ? "flow" : "") : "unk";
    }
    if (p.item) { const s = eqState(R, { kind: "pump", item: p.item }).st; return s === "on" ? "flow" : s === "up" ? "asked" : ""; }
    return "";
  }

  const n0 = v => Math.round(v).toLocaleString("en-US").replace(/,/g, " ");
  let seq = 0;
  /** opts: { edit, selected, onSelect(kind, id), onMove(id, x, y), onPoint(pipeId, index, x, y), onCanvas(x, y) } */
  function render(lay, R, opts) {
    opts = opts || {};
    const uid = "lay" + (++seq), sel = opts.selected, W = opts.water || null;   // W: the twin's simulated water
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${lay.width} ${lay.height}`);
    svg.setAttribute("class", "schematic diagram" + (opts.edit ? " editing" : ""));
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", `${lay.title}: tanks, pumps, valves and sensors with their state` +
      (opts.edit ? " (editing)" : opts.readOnly ? " (read-only)" : ""));
    let parent = svg;
    function e(tag, a, txt) {
      const x = document.createElementNS(NS, tag);
      for (const k in a) if (a[k] != null) x.setAttribute(k, a[k]);
      if (txt != null) x.textContent = txt;
      parent.appendChild(x); return x;
    }
    const defs = e("defs", {}); parent = defs;
    e("marker", { id: uid + "-arrow", viewBox: "0 0 10 10", refX: "5", refY: "5", markerWidth: "4", markerHeight: "4", orient: "auto-start-reverse" });
    parent = defs.lastChild; e("path", { d: "M0 0 L10 5 L0 10 z", class: "arrowhead" }); parent = svg;
    if (opts.edit) {                                      // a light grid to place symbols on
      parent = defs;
      e("pattern", { id: uid + "-grid", width: 20, height: 20, patternUnits: "userSpaceOnUse" });
      parent = defs.lastChild; e("path", { d: "M20 0 H0 V20", class: "grid-line" }); parent = svg;
      const bg = e("rect", { x: 0, y: 0, width: lay.width, height: lay.height, fill: `url(#${uid}-grid)`, class: "canvas" });
      bg.addEventListener("click", ev => { const p = toSvg(ev); opts.onCanvas && opts.onCanvas(Math.round(p.x), Math.round(p.y)); });
    }
    function toSvg(ev) {
      const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      return pt.matrixTransform(svg.getScreenCTM().inverse());
    }
    const snap = v => Math.round(v / 5) * 5;
    function draggable(g, onDrop) {
      if (!opts.edit) return;
      g.addEventListener("pointerdown", ev => {
        ev.preventDefault(); ev.stopPropagation();
        const start = toSvg(ev); let dx = 0, dy = 0, moved = false;
        g.setPointerCapture(ev.pointerId);
        const move = m => { const p = toSvg(m); dx = snap(p.x - start.x); dy = snap(p.y - start.y); moved = moved || dx || dy;
          g.setAttribute("transform", `translate(${dx} ${dy})`); };
        const up = () => { g.removeEventListener("pointermove", move); g.removeEventListener("pointerup", up);
          if (moved) onDrop(dx, dy); else g.dispatchEvent(new Event("pick")); };
        g.addEventListener("pointermove", move); g.addEventListener("pointerup", up);
      });
    }
    function hot(kind, id, label, draw) {
      const g = e("g", { class: "hot" + (sel === id ? " selected" : ""), tabindex: "0", role: "button", "aria-label": label,
        "aria-pressed": sel === id ? "true" : "false", "data-id": id, "data-kind": kind });
      const prev = parent; parent = g; draw(); parent = prev;
      const pick = () => opts.onSelect && opts.onSelect(kind, id);
      if (opts.drawing) g.addEventListener("click", ev => { ev.stopPropagation(); const p = toSvg(ev); opts.onCanvas(Math.round(p.x), Math.round(p.y)); });
      else if (opts.edit) g.addEventListener("pick", pick); else g.addEventListener("click", pick);
      g.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      return g;
    }

    // ---- pipes first, idle under moving
    const wpipe = p => {                                 // with simulated water: moving where it moves, else asked
      if (W.pipes[p.id] > 0) return "flow";
      return p.item && eqState(R, { kind: "pump", item: p.item }).st === "up" ? "asked" : "";
    };
    const pipes = lay.pipes.map(p => [p, W ? wpipe(p) : pipeState(R, p)]).sort((a, b) => (a[1] ? 1 : 0) - (b[1] ? 1 : 0));
    for (const [p, st] of pipes) {
      const d = "M" + p.points.map(q => q.join(" ")).join(" L");
      const g = hot("pipe", p.id, `pipe ${p.id}${st === "flow" ? ", water moving" : ""}`, () => {
        e("path", { d, class: "pipe-hit" });
        e("path", { d, class: "pipe" + (st ? " " + st : "") + (sel === p.id ? " hl" : ""), "marker-end": st === "flow" ? `url(#${uid}-arrow)` : null, "data-route": st || "idle" });
        if (opts.edit) {                                   // an attached end shows a dot
          if (p.from_node) e("circle", { cx: p.points[0][0], cy: p.points[0][1], r: 4, class: "joint" });
          if (p.to_node) { const q = p.points[p.points.length - 1]; e("circle", { cx: q[0], cy: q[1], r: 4, class: "joint" }); }
        }
      });
      if (opts.edit && !opts.drawing) {
        g.addEventListener("click", () => opts.onSelect && opts.onSelect("pipe", p.id));
        if (sel === p.id) p.points.forEach((q, i) => {
          const hnd = e("circle", { cx: q[0], cy: q[1], r: 7, class: "handle", "data-point": i });
          draggable(hnd, (dx, dy) => opts.onPoint && opts.onPoint(p.id, i, q[0] + dx, q[1] + dy));
        });
      }
    }
    // ---- symbols
    for (const n of lay.nodes) {
      const x = n.x, y = n.y;
      let g;
      if (n.kind === "label") {
        g = hot("node", n.id, n.label, () => e("text", { x, y, class: n.label.length > 20 ? "small muted-t" : "th" }, n.label));
      } else if (n.kind === "source") {
        const q = W && n.id in (W.sources || {}) ? W.sources[n.id] : null;
        g = hot("node", n.id, `${n.label || "Water source"}`, () => {
          e("circle", { cx: x, cy: y, r: 12, class: "source hl" });
          e("text", { x, y: y + 5, "text-anchor": "middle", class: "tagt source-t" }, "≈");
          e("text", { x: x + 16, y: y - 8, class: "small" }, n.label || "Water source");
          if (q != null) e("text", { x: x + 16, y: y + 16, class: "small flow-t" }, `${q} L/min`);
        });
      } else if (n.kind === "tank" && W && W.tanks[n.id]) {
        const w = n.w || 100, hh = n.h || 100, t = W.tanks[n.id], f = Math.max(0, Math.min(1, t.pct / 100));
        const trend = t.in_L_min - t.out_L_min;
        g = hot("node", n.id, `${n.label}: ${n0(t.L)} of ${n0(t.capacity_L)} L (simulated)`, () => {
          e("rect", { x, y, width: w, height: hh, rx: 6, class: "tank-bg" });
          e("rect", { x: x + 2, y: y + 2 + (hh - 4) * (1 - f), width: w - 4, height: (hh - 4) * f, class: "water" });
          e("rect", { x, y, width: w, height: hh, rx: 6, class: "tank-wall hl" });
          e("text", { x: x + w / 2, y: y - 6, "text-anchor": "middle", class: "th" }, n.label);
          e("text", { x: x + w / 2, y: y + hh + 14, "text-anchor": "middle", class: "val" }, `${n0(t.L)} / ${n0(t.capacity_L)} L · ${t.pct.toFixed(0)} %`);
          if (Math.abs(trend) > 0.01) e("text", { x: x + w / 2, y: y + hh + 28, "text-anchor": "middle", class: "val trend-t" },
            `${trend > 0 ? "▲" : "▼"} ${Math.abs(trend).toFixed(1)} L/min`);
        });
      } else if (n.kind === "tank") {
        const w = n.w || 100, hh = n.h || 100, r = reading(R, n);
        g = hot("node", n.id, `${n.label}: ${r.txt}`, () => {
          e("rect", { x, y, width: w, height: hh, rx: 6, class: "tank-bg" });
          if (r.ok && typeof r.v === "number") {
            // the reading that means full: set on the tank, else 100 % for a percentage, else the capacity in litres,
            // else the top of the level sensor's scaling (its 20 mA)
            const full = n.full_at || (r.unit === "%" ? 100 : r.unit === "L" ? n.capacity_L : r.max || null);
            const f = full ? Math.max(0, Math.min(1, r.v / full)) : 0;
            e("rect", { x: x + 2, y: y + 2 + (hh - 4) * (1 - f), width: w - 4, height: (hh - 4) * f, class: "water" });
          } else e("rect", { x: x + 3, y: y + 3, width: w - 6, height: hh - 6, class: "unknown" });
          e("rect", { x, y, width: w, height: hh, rx: 6, class: "tank-wall hl" + (n.planned ? " planned" : "") });
          e("text", { x: x + w / 2, y: y - 6, "text-anchor": "middle", class: "th" }, n.label);
          e("text", { x: x + w / 2, y: y + hh + 14, "text-anchor": "middle", class: "val" }, n.item ? `level ${r.txt}` : r.none && !n.planned ? "level not linked" : r.txt);
        });
      } else if (n.kind === "sensor") {
        const ws = W && W.sensors[n.id];
        const r = ws ? { ok: true, txt: `${ws.value} ${ws.unit}`, v: ws.value } : reading(R, n);
        g = hot("node", n.id, `${n.label || n.tag}: ${r.txt}`, () => {
          e("circle", { cx: x, cy: y, r: 11, class: "sensor hl " + (r.none ? "s-none" : r.ok ? "s-ok" : "s-old") });
          e("text", { x, y: y + 4, "text-anchor": "middle", class: "tagt" }, n.tag || "?");
          if (n.item) e("text", { x: x + 15, y: y + 4, class: "val" + (r.ok ? "" : " old-t") }, r.txt);
        });
      } else if (n.kind === "valve" || n.kind === "pump") {
        const s = eqState(R, n);
        g = hot("node", n.id, `${n.label}: ${s.word}`, () => {
          const gg = e("g", { class: "eq " + s.cls + (n.planned ? " planned" : ""), transform: n.kind === "valve" && n.vertical ? `rotate(90 ${x} ${y})` : null });
          const prev = parent; parent = gg;
          if (n.kind === "valve") {
            e("polygon", { points: `${x - 13},${y - 9} ${x - 13},${y + 9} ${x},${y}`, class: "body hl" });
            e("polygon", { points: `${x + 13},${y - 9} ${x + 13},${y + 9} ${x},${y}`, class: "body hl" });
            e("line", { x1: x, y1: y, x2: x, y2: y - 13, class: "stem" });
            e("rect", { x: x - 5, y: y - 18, width: 10, height: 5, class: "stem-head" });
          } else {
            const tri = { right: `${x - 6},${y - 8} ${x - 6},${y + 8} ${x + 9},${y}`, left: `${x + 6},${y - 8} ${x + 6},${y + 8} ${x - 9},${y}`,
              down: `${x - 8},${y - 6} ${x + 8},${y - 6} ${x},${y + 9}`, up: `${x - 8},${y + 6} ${x + 8},${y + 6} ${x},${y - 9}` }[n.dir || "right"];
            e("circle", { cx: x, cy: y, r: 16, class: "body hl" });
            e("polygon", { points: tri, class: "pump-tri" });
          }
          parent = prev;
          const v = n.kind === "valve" && n.vertical;
          const lx = x + (v ? 26 : 0), anchor = v ? "start" : "middle";
          e("text", { x: lx, y: v ? y + 1 : n.kind === "pump" ? y + 31 : y - 22, "text-anchor": anchor, class: "small" }, n.label);
          e("text", { x: lx, y: v ? y + 14 : n.kind === "pump" ? y + 44 : y + 23, "text-anchor": anchor, class: "small st-t " + s.cls }, s.word);
        });
      } else {                                          // sector or box
        const w = n.w || 140, hh = n.h || 50, sector = n.kind === "sector";
        const wet = sector && W && W.sectors[n.id] > 0, s = wet ? { st: "on", cls: "st-on", word: "being watered" } : eqState(R, n);
        g = hot("node", n.id, `${n.label}: ${n.item ? s.word : n.sub}`, () => {
          e("rect", { x, y, width: w, height: hh, rx: 6, class: "box hl " + (sector ? "sector-box" + (s.st === "on" ? " watering" : "") : "eqbox " + s.cls) + (n.planned ? " planned" : "") });
          e("text", { x: x + w / 2, y: y + (sector ? 19 : 19), "text-anchor": "middle", class: sector ? "th" : "small" }, n.label);
          if (n.sub) e("text", { x: x + w / 2, y: y + 35, "text-anchor": "middle", class: "small" }, n.sub);
          e("text", { x: x + w / 2, y: y + (n.sub ? 53 : 36), "text-anchor": "middle", class: "small st-t " + (n.item ? s.cls : "muted-t") },
            sector ? (wet ? `being watered · ${W.sectors[n.id]} L/min` : n.item ? (s.st === "on" ? "being watered" : s.word) : "not linked") : s.word);
        });
      }
      if (opts.edit && !opts.drawing) draggable(g, (dx, dy) => opts.onMove && opts.onMove(n.id, x + dx, y + dy));
    }
    return svg;
  }
  function legend(h) {
    const sw = (cls, word, what) => h("span", { class: "lg" }, h("span", { class: "pill " + cls }, word), " ", what);
    return h("div", { class: "muted small legend-box" },
      h("p", null, "Pumps and valves: ", sw("st-on", "Running / Open", "the equipment reports it"), " ", sw("st-move", "Starting… / Opening…", "asked for, not yet reported"),
        " ", sw("st-off", "Stopped / Closed", ""), " ", sw("st-fault", "Fault", ""), " ", sw("st-unk", "Not known", "no feedback, not linked, or its reading is old")),
      h("p", null, "In the digital twin, water is simulated along the drawing's pipes (demo flows): a water source fills through open valves, running pumps move water from tank to tank, sectors drain back by gravity. ",
        "Pipes: moving water and arrows while the linked flow meter measures flow (or, without one, while the linked pump or valve reports running or open); dashed where it was asked for but not reported yet. Round tags are sensors (L level, F flow, P pressure, pH, EC, T temperature)."));
  }
  window.AqLayout = { render, legend, eqState, reading };
})();
