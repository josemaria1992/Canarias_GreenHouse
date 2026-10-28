/* AquaCrop installation pages: process pages, problems and recovery, the installation diagram and the recipe
 * editor. Loaded after app.js and built on its helpers (window.AqUI) and wording (window.AqWords). Everything
 * shown is what the controllers observe (value, unit, quality, age, origin), never the twin's internal truth.
 * All text is inserted with textContent. Process control exists only in simulation; on a hardware profile the
 * same pages are read-only observation, every control is replaced by the reason it is unavailable, and every
 * signal stays UNBOUND (not measured) until a physical mapping is approved (ADR-0009 addendum).
 *
 * Page order (F-02): what the process is doing and its Stop come first, then problems and the next step, then
 * the page's own readings; the diagram is a collapsed, secondary view and the technical detail (motor
 * registers, contactors, identifiers, phases) sits under "Technical details". */
"use strict";
(function () {
  const U = window.AqUI, W = window.AqWords;
  const { h, api, act, pill, qBadge, oBadge, num, fmtTime, can, mount, toast, S, VIEWS } = U;
  const NS = "http://www.w3.org/2000/svg";
  const USABLE = ["GOOD", "OVERRIDDEN"];
  const PAGES = [["main-pumps", "Watering"], ["main-tank", "Main tank"], ["nutrients", "Nutrients & acid"]];

  // ---------------------------------------------------------------- signal reading
  function sig(P, name) {
    const s = P.signals[name];
    if (!s) return { bound: false, txt: "not in layout", ok: false };
    if (s.binding === "UNBOUND") return { bound: false, txt: "not measured", ok: false, why: "no channel carries this signal" };
    if (s.binding === "DRIVE_ADAPTER") {
      const a = s.adapter;
      return { bound: true, adapter: true, ok: a.requested != null, v: a.requested, txt: a.requested == null ? a.kind : (a.requested ? "on" : "off"), kind: a.kind };
    }
    const m = s.sample;
    if (!m) return { bound: true, ok: false, q: "UNAVAILABLE", txt: "no reading", point: s.point, label: s.label };
    const ok = USABLE.includes(m.quality) && m.value != null;
    return { bound: true, ok, v: m.value, unit: m.unit, q: m.quality, age: m.age_s, origin: m.origin, point: s.point, label: s.label,
      reason: m.reason, override: m.override, txt: m.value == null ? "—" : (typeof m.value === "boolean" ? (m.value ? "1" : "0") : "") };
  }
  function fmtVal(r, d, words) {
    if (!r.bound) return r.txt;
    if (r.adapter) return r.txt;
    if (r.v == null) return r.txt;
    if (r.q === "INVALID" || r.q === "UNAVAILABLE") return "—";      // a number outside the allowed range is not shown as a value
    if (typeof r.v === "boolean") return words ? (r.v ? words[0] : words[1]) : (r.v ? "on" : "off");
    return W.fixed(r.v, d == null ? 1 : d) + (r.unit ? " " + r.unit : "");
  }
  /** One reading: value with unit and its age; the quality in plain words when it is not valid (always with
   * `loud`, for the main readings of a page); the channel it comes from on hover. */
  function obs(P, name, d, words, loud) {
    const r = sig(P, name);
    if (!r.bound) return h("span", { class: "muted", title: r.why || "" }, r.txt);
    if (r.adapter) return h("span", null, h("strong", null, fmtVal(r, d, words)), " ", h("span", { class: "muted" }, "(over the drive's data link)"));
    const good = r.q === "GOOD";
    const got = r.q === "INVALID" && r.v != null && typeof r.v !== "boolean" ? ` · value received: ${W.fixed(r.v, d == null ? 1 : d)}${r.unit ? " " + r.unit : ""}` : "";
    return h("span", { class: "reading", title: `${W.signal(name)} · channel ${r.point}${r.label ? " — " + r.label : ""}${good ? " · " + W.quality("GOOD") : ""}${got}` },
      h("strong", null, fmtVal(r, d, words)), " ",
      good && !loud ? null : [qBadge(r.q || "UNAVAILABLE", r.age), " "], r.origin && r.origin !== "SIMULATED" ? [oBadge(r.origin), " "] : null,
      r.age != null && r.q !== "STALE" ? h("span", { class: "muted small" }, U.ago(r.age)) : null,
      r.override ? h("span", { class: "muted" }, " set by hand") : null);
  }
  const level = (P, t) => obs(P, `tank.${t}.level`, 0, null, true);
  function kvRows(rows) {
    return h("dl", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]));
  }
  /** What the program asked for (command) next to what the equipment reports (feedback): never merged. */
  function reqObs(P, cmd, fb, words, askWords) {
    const c = sig(P, cmd), asked = c.bound && c.ok && !c.adapter ? h("strong", null, fmtVal(c, 0, askWords || ["on", "off"])) : obs(P, cmd, 0, askWords || ["on", "off"]);
    const eq = Object.values(equipment(P)).find(x => x.cmd === cmd);       // the same state word as the diagram (O-02)
    return h("span", null, eq ? [stPill(eqState(P, S.procs || null, eq)), " "] : null, "asked: ", asked, fb ? [" · reports: ", obs(P, fb, 0, words)] : null);
  }
  // a selector (contactor) only selects its pump; the drive runs it
  const names = ids => ids && ids.length ? ids.map(x => { const id = x.replace(/^(valve|pump|contactor|drive)\./, "").replace(/\.(cmd|run)$/, "");
    return x.startsWith("contactor.") ? `${W.cap(W.equip(id))} selected` : x.startsWith("drive.") ? "Pump drive running" : W.cap(W.equip(id)); }).join(", ") : "none";
  const STOP_STAGE = { DRIVE: "stopping the pump", SETTLE: "waiting for the pump to stop", VALVES: "closing the valve", DESELECT: "releasing the pump selection" };

  // ---------------------------------------------------------------- equipment: one state vocabulary (O-02)
  // Each pump and valve has what the program asked for (its command channel) and what the equipment reports:
  // a feedback signal, or else the water flow measured on its route, or else the measured rise of the tank it
  // fills. A command is never shown as movement: it is "Opening…" until the equipment reports it.
  const ST = {
    on: { cls: "st-on", valve: "Open", pump: "Running" },
    off: { cls: "st-off", valve: "Closed", pump: "Stopped" },
    up: { cls: "st-move", valve: "Opening…", pump: "Starting…" },
    down: { cls: "st-move", valve: "Closing…", pump: "Stopping…" },
    fault: { cls: "st-fault", valve: "Fault", pump: "Fault" },
    unk: { cls: "st-unk", valve: "Not known", pump: "Not known" },
  };
  const FLOW_ON = 0.5;                       // L/min: the controllers' own "no flow" limit
  const TANKS = ["MAIN", "A", "B", "ACID", "SUMP"];
  const STOCK_OF = { A: "A", B: "B", ACID: "ACID" };
  /** The equipment of the layout: names, signals, the process that runs it and the one operation it is part of. */
  function equipment(P) {
    const L = P.layout, E = {};
    const add = e => { E[e.key] = e; };
    ["A", "B", "ACID"].forEach(s => {
      const T = W.TANK[s], tl = T.charAt(0).toLowerCase() + T.slice(1);
      add({ key: "valve.FILL_" + s, kind: "valve", name: `Water inlet valve, ${tl}`, cmd: `valve.FILL_${s}.cmd`, proof: "level", level: s,
        proc: "STOCK_FILL_" + s, page: "#nutrients", tab: s });
      const proc = s === "ACID" ? "ACID" : "REFILL";
      add({ key: "valve.OUT_" + s, kind: "valve", name: `Outlet valve, ${tl}`, cmd: `valve.OUT_${s}.cmd`, proof: "flow", flow: `flow.TP_${s}`, proc, page: "#nutrients", tab: s });
      add({ key: "pump.TP_" + s, kind: "pump", name: `Transfer pump, ${tl}`, cmd: `pump.TP_${s}.cmd`, fb: `pump.TP_${s}.running`, flow: `flow.TP_${s}`, proc, page: "#nutrients", tab: s });
    });
    add({ key: "valve.FILL_MAIN", kind: "valve", name: "Water inlet valve, main tank", cmd: "valve.FILL_MAIN.cmd", proof: "flow", flow: "flow.FILL_MAIN", proc: "REFILL", page: "#main-tank", water: true });
    L.main_pumps.forEach((mp, i) => add({ key: "pump." + mp, kind: "pump", name: `Main pump ${i + 1}`, cmd: `contactor.${mp}.cmd`, fb: `contactor.${mp}.closed`,
      run: `drive.${L.shared_drive}.running`, proc: "IRRIGATION", page: "#main-pumps", pump: mp }));
    L.sectors.forEach(s => add({ key: "valve." + s.sector_id, kind: "valve", name: `${W.sectorName(s.sector_id)} valve`, cmd: `valve.${s.sector_id}.cmd`,
      fb: `valve.${s.sector_id}.open_limit`, flow: `flow.${s.sector_id}`, proc: "IRRIGATION", page: "#sector-" + s.sector_id, sector: s.sector_id }));
    L.return_pumps.forEach(rp => add({ key: "pump." + rp, kind: "pump", name: W.cap(W.equip(rp)), cmd: `pump.${rp}.cmd`, fb: `pump.${rp}.running`, flow: "flow.RETURN",
      proc: "RETURN", page: "#drainage", pump: rp }));
    add({ key: "drive." + L.shared_drive, kind: "pump", name: "Pump drive (shared)", cmd: `drive.${L.shared_drive}.run`, fb: `drive.${L.shared_drive}.running`,
      fault: `drive.${L.shared_drive}.fault`, proc: "IRRIGATION", page: "#main-pumps", drive: true });
    return E;
  }
  // measured level history per tank, from the readings' own acquisition times (for "rising" and "falling")
  const levels = {};
  function noteLevels(P) {
    TANKS.forEach(t => {
      const r = sig(P, `tank.${t}.level`);
      if (!r.ok || r.age == null) return;
      const at = Date.now() - 1000 * r.age, h0 = levels[t] || (levels[t] = []);
      if (!h0.length || at - h0[h0.length - 1][0] > 200) h0.push([at, r.v]);
      while (h0.length && h0[0][0] < Date.now() - 60000) h0.shift();
    });
  }
  /** L/min from the last 20 s of measured levels; null without enough recent readings. */
  function levelRate(t) {
    const h0 = (levels[t] || []).filter(x => x[0] > Date.now() - 20000);
    if (h0.length < 3) return null;
    const [t0, v0] = h0[0], [t1, v1] = h0[h0.length - 1];
    return t1 - t0 < 4000 ? null : (v1 - v0) / ((t1 - t0) / 60000);
  }
  const RATE_MIN = 2;                         // L/min: below this a level counts as steady
  function trendOf(t) { const r = levelRate(t); return r == null ? null : r > RATE_MIN ? "rising" : r < -RATE_MIN ? "falling" : "steady"; }
  /** Asked / reported / state of one piece of equipment: the meaning shown in the diagram, its panel and the pages. */
  function eqState(P, PR, e) {
    const c = sig(P, e.cmd);
    const asked = c.adapter ? (c.v == null ? null : !!c.v) : c.bound && c.ok ? !!c.v : null;
    let rep = null, how = "", stale = false;
    if (e.fb) {
      const r = sig(P, e.fb);
      if (r.bound && r.ok && r.q !== "STALE") {
        rep = !!r.v; how = e.kind === "valve" ? "its open signal" : e.run ? "its selector signal and the drive's running signal" : "its running signal";
        if (e.run && rep) { const d = sig(P, e.run); rep = d.ok ? !!d.v : null; if (rep === null) stale = true; }
      } else if (r.bound) stale = true;
    }
    if (rep === null && !stale && e.proof === "flow") {
      const f = sig(P, e.flow);
      if (f.ok && f.q !== "STALE") { rep = f.v > FLOW_ON; how = "the water flow measured on its route (no position signal)"; } else if (f.bound) stale = true;
    }
    if (rep === null && !stale && e.proof === "level") {
      const tr = trendOf(e.level);
      if (tr) { rep = tr === "rising"; how = "the measured rise of the tank's level (no position signal or flow meter)"; }
    }
    let st;
    const pv = PR && PR.processes[e.proc], f = e.fault ? sig(P, e.fault) : null;
    if ((f && f.ok && f.v) || (pv && pv.state === "FAULT_LOCKOUT" && asked !== true && rep === true)) st = "fault";
    else if (rep === null) st = asked === true ? "up" : asked === false && !stale ? "off" : "unk";
    else if (asked === null) st = rep ? "on" : "off";
    else st = asked ? (rep ? "on" : "up") : (rep ? "down" : "off");
    if (stale && st !== "fault") st = "unk";
    return { st, asked, rep, how, stale, word: ST[st][e.kind], cls: ST[st].cls, pv };
  }
  function stPill(s) { return h("span", { class: "pill " + s.cls, "data-state": s.st }, s.word); }

  // ---------------------------------------------------------------- the installation diagram
  /** opts.readOnly: a hardware profile (no controls anywhere); opts.focus: the page whose parts are highlighted
   * (the others are dimmed). Clicking a part selects it: its panel beside the diagram shows its state, its
   * readings and, on a simulated profile, the operation it can be part of. */
  let svgSeq = 0;
  function diagram(P, opts, PR) {
    const readOnly = !!(opts && opts.readOnly), focus = opts && opts.focus, sel = S.diagSel && S.diagSel.key;
    const EQ = equipment(P);
    noteLevels(P);
    const svg = document.createElementNS(NS, "svg"), uid = "d" + (++svgSeq);
    svg.setAttribute("viewBox", "0 0 1100 700"); svg.setAttribute("class", "schematic diagram");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Greenhouse diagram: tanks, pumps, valves and sensors with their readings" + (readOnly ? " (read-only)" : ""));
    if (focus) svg.classList.add("compact");
    let parent = svg;
    function e(tag, a, txt) {
      const x = document.createElementNS(NS, tag);
      for (const k in a) if (a[k] != null) x.setAttribute(k, a[k]);
      if (txt != null) x.textContent = txt;
      parent.appendChild(x); return x;
    }
    const defs = e("defs", {});
    parent = defs;
    e("marker", { id: uid + "-arrow", viewBox: "0 0 10 10", refX: "5", refY: "5", markerWidth: "4", markerHeight: "4", orient: "auto-start-reverse" });
    parent = defs.lastChild; e("path", { d: "M0 0 L10 5 L0 10 z", class: "arrowhead" });
    parent = svg;
    function hot(page, key, type, label, draw) {
      const g = e("g", { class: "hot" + (focus ? (page === focus ? " focus" : " dim") : "") + (sel === key ? " selected" : ""), tabindex: "0", role: "button",
        "aria-label": label, "aria-pressed": sel === key ? "true" : "false", "data-key": key, "data-type": type });
      const prev = parent; parent = g; draw(); parent = prev;
      const pick = () => { if (!S.diagSel || S.diagSel.key !== key) S.diagConfirm = null; S.diagSel = { key, type }; S.diagPend = S.diagPend && S.diagPend.key === key ? S.diagPend : null;
        if (type === "sensor" || type === "tank") askReading(type === "tank" ? `tank.${key.slice(5)}.level` : key); U.refresh(); };
      g.addEventListener("click", pick);
      g.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      return g;
    }
    const v = n => sig(P, n);
    const short = (r, d, words) => !r.bound ? "not measured" : fmtVal(r, d, words) + (r.adapter || r.ok ? "" : " (" + W.quality(r.q || "UNAVAILABLE").toLowerCase() + ")");
    // a pipe drawn in the direction the water goes: "flow" (measured flow, moving water and arrows), "asked"
    // (the program asked for the route but no flow is measured), "unk" (the flow reading is old or unavailable)
    const pipes = [];
    const pipe = (d, state) => pipes.push([d, state || ""]);
    const flowState = n => { if (!n) return ""; const r = v(n); return !r.bound ? "" : !r.ok || r.q === "STALE" ? "unk" : r.v > FLOW_ON ? "flow" : ""; };
    function tag(x, y, txt, signal, page, label) {
      const r = v(signal);
      return hot(page, signal, "sensor", `${label || W.signal(signal)}: ${short(r, 2)}. Ask for a new reading`, () => {
        e("circle", { cx: x, cy: y, r: 11, class: "sensor " + (!r.bound ? "s-none" : r.ok && r.q !== "STALE" ? "s-ok" : "s-old") + " hl" });
        e("text", { x, y: y + 4, "text-anchor": "middle", class: "tagt" }, txt);
        if (r.bound && !(r.ok && r.q !== "STALE")) e("text", { x: x + 12, y: y - 8, class: "small old-t" }, "?");
      });
    }
    function tank(x, y, w, hh, name, t, page) {
      const cap = P.layout.tanks_L[t], r = v(`tank.${t}.level`), tr = r.ok ? trendOf(t) : null, rate = levelRate(t);
      hot(page, "tank." + t, "tank", `${name}: ${short(r, 0)} of ${W.n0(cap)} L${tr && tr !== "steady" ? ", " + tr : ""}`, () => {
        e("rect", { x, y, width: w, height: hh, rx: 6, class: "tank-bg" });
        if (r.ok) {
          const f = Math.max(0, Math.min(1, r.v / cap));
          e("rect", { x: x + 2, y: y + 2 + (hh - 4) * (1 - f), width: w - 4, height: (hh - 4) * f, class: "water" + (r.q === "STALE" ? " old" : "") });
        } else e("rect", { x: x + 3, y: y + 3, width: w - 6, height: hh - 6, class: "unknown" });
        e("rect", { x, y, width: w, height: hh, rx: 6, class: "tank-wall hl" });
        e("text", { x: x + w / 2, y: y - 6, "text-anchor": "middle", class: "th" }, name);
        e("text", { x: x + w / 2, y: y + hh + 14, "text-anchor": "middle", class: "val" }, r.ok ? `${W.n0(num(r.v, 0))} / ${W.n0(cap)} L · ${(100 * r.v / cap).toFixed(0)} %` : `level ${short(r, 0)}`);
        if (tr && tr !== "steady") e("text", { x: x + w / 2, y: y + hh + 28, "text-anchor": "middle", class: "val trend-t", "data-trend": tr },
          `${tr === "rising" ? "▲ rising" : "▼ falling"} ${W.n0(Math.abs(rate))} L/min`);
        else if (r.ok) e("text", { x: x + w / 2, y: y + hh + 28, "text-anchor": "middle", class: "val muted-t" }, U.ago(r.age));
      });
    }
    function valve(x, y, eq, label, vertical) {
      const s = eqState(P, PR, eq);
      hot(eq.page, eq.key, "equip", `${eq.name}: ${s.word}. Select for details`, () => {
        const g = e("g", { class: "eq " + s.cls, transform: vertical ? `rotate(90 ${x} ${y})` : null });
        const prev = parent; parent = g;
        e("polygon", { points: `${x - 13},${y - 9} ${x - 13},${y + 9} ${x},${y}`, class: "body hl" });
        e("polygon", { points: `${x + 13},${y - 9} ${x + 13},${y + 9} ${x},${y}`, class: "body hl" });
        e("line", { x1: x, y1: y, x2: x, y2: y - 13, class: "stem" });
        e("rect", { x: x - 5, y: y - 18, width: 10, height: 5, class: "stem-head" });
        parent = prev;
        e("text", { x: x + (vertical ? 26 : 0), y: vertical ? y + 1 : y - 22, "text-anchor": vertical ? "start" : "middle", class: "small" }, label);
        e("text", { x: x + (vertical ? 26 : 0), y: vertical ? y + 14 : y + 23, "text-anchor": vertical ? "start" : "middle", class: "small st-t " + s.cls }, s.word);
      });
    }
    function pump(x, y, eq, label, dir) {
      const s = eqState(P, PR, eq), tri = { right: `${x - 6},${y - 8} ${x - 6},${y + 8} ${x + 9},${y}`, left: `${x + 6},${y - 8} ${x + 6},${y + 8} ${x - 9},${y}`,
        down: `${x - 8},${y - 6} ${x + 8},${y - 6} ${x},${y + 9}` }[dir || "right"];
      hot(eq.page, eq.key, "equip", `${eq.name}: ${s.word}. Select for details`, () => {
        const g = e("g", { class: "eq " + s.cls });
        const prev = parent; parent = g;
        e("circle", { cx: x, cy: y, r: 16, class: "body hl" });
        e("polygon", { points: tri, class: "pump-tri" });
        parent = prev;
        e("text", { x, y: y + 31, "text-anchor": "middle", class: "small" }, label);
        e("text", { x, y: y + 44, "text-anchor": "middle", class: "small st-t " + s.cls }, s.word);
      });
    }
    const flowLabel = (x, y, n, anchor) => { const r = v(n); e("text", { x, y, "text-anchor": anchor || "start", class: "val" + (r.bound && !r.ok ? " old-t" : "") },
      r.bound ? fmtVal(r, 1) + (r.ok ? "" : " (" + W.quality(r.q || "UNAVAILABLE").toLowerCase() + ")") : "flow not measured"); };

    // ---- routes (drawn first, under the equipment)
    const L = P.layout, drv = L.shared_drive;
    const fillOn = s => eqState(P, PR, EQ["valve.FILL_" + s]).st === "on";
    const asked = k => eqState(P, PR, EQ[k]).asked === true;
    const fm = flowState("flow.FILL_MAIN");
    pipe("M20 40 H620", ["A", "B", "ACID"].some(fillOn) || fm === "flow" ? "flow" : "");
    pipe("M620 40 V210", fm || (asked("valve.FILL_MAIN") ? "asked" : ""));
    [["A", 60], ["B", 200], ["ACID", 340]].forEach(([s, x]) => {
      const c = x + 45, fs = flowState(`flow.TP_${s}`);
      pipe(`M${c} 40 V125`, fillOn(s) ? "flow" : asked("valve.FILL_" + s) ? "asked" : "");
      pipe(`M${c} 262 V430 H500`, fs || (asked("pump.TP_" + s) || asked("valve.OUT_" + s) ? "asked" : ""));
    });
    const sectorFlow = {};
    L.sectors.forEach(s => { sectorFlow[s.sector_id] = flowState("flow." + s.sector_id); });
    const pumpY = i => 290 + i * 120, secY = i => 200 + i * 240;
    L.main_pumps.forEach((mp, i) => {
      const ps = eqState(P, PR, EQ["pump." + mp]), yp = pumpY(i);
      // the water goes through this pump only while it is selected and the drive reports running
      const via = ps.rep === true;
      L.sectors.forEach((s, j) => {
        const fl = sectorFlow[s.sector_id], ys = secY(j);
        const st = via && fl === "flow" ? "flow" : via && fl === "unk" ? "unk" : ps.asked && asked("valve." + s.sector_id) ? "asked" : "";
        pipe(`M660 410 H700 V${yp} H744`, st);
        pipe(`M776 ${yp} H830 V${ys} H867`, st);
      });
    });
    L.sectors.forEach((s, j) => {
      const ys = secY(j), fl = sectorFlow[s.sector_id];
      pipe(`M893 ${ys} H930`, fl || (asked("valve." + s.sector_id) ? "asked" : ""));
      pipe(`M1090 ${ys + 30} H1097 V600 H960`, "");
    });
    const rf = flowState("flow.RETURN");
    pipe("M860 620 H478 V440 H500", rf || (L.return_pumps.some(rp => asked("pump." + rp)) ? "asked" : ""));
    // inactive under active, so a moving route is never hidden by a shared idle stretch
    pipes.sort((a, b) => (a[1] ? 1 : 0) - (b[1] ? 1 : 0)).forEach(([d, st]) =>
      e("path", { d, class: "pipe" + (st ? " " + st : ""), "marker-mid": null, "marker-end": st === "flow" ? `url(#${uid}-arrow)` : null, "data-route": st || "idle" }));

    // ---- mains and the fertiliser and acid tanks
    e("text", { x: 20, y: 26, class: "th" }, "Mains water");
    [["A", "Fertiliser A", 60], ["B", "Fertiliser B", 200], ["ACID", "Acid", 340]].forEach(([s, name, x]) => {
      const c = x + 45;
      valve(c, 80, EQ["valve.FILL_" + s], "water in", true);
      tank(x, 125, 90, 100, name, s, "#nutrients");
      tag(x + 78, 137, "L", `tank.${s}.level`, "#nutrients");
      valve(c, 290, EQ["valve.OUT_" + s], "outlet", true);
      pump(c, 352, EQ["pump.TP_" + s], `TP ${s === "ACID" ? "acid" : s}`, "down");
      tag(c + 22, 416, "F", `flow.TP_${s}`, "#nutrients");
      flowLabel(c + 36, 420, `flow.TP_${s}`);
    });
    e("text", { x: 20, y: 470, class: "small muted-t" }, "No stirrer or air pump: mixing is not checked.");
    // ---- main tank
    valve(620, 128, EQ["valve.FILL_MAIN"], "water in", true);
    tag(642, 180, "F", "flow.FILL_MAIN", "#main-tank");
    flowLabel(656, 184, "flow.FILL_MAIN");
    tank(500, 210, 160, 240, "Main tank", "MAIN", "#main-tank");
    tag(508, 232, "L", "tank.MAIN.level", "#main-tank");
    tag(530, 515, "pH", "main.ph", "#main-tank");
    tag(580, 515, "EC", "main.ec", "#main-tank");
    tag(630, 515, "T", "main.temperature", "#main-tank");
    const ph = v("main.ph"), ec = v("main.ec");
    e("text", { x: 505, y: 545, class: "small" }, `pH ${short(ph, 2)} · EC ${short(ec, 2)}`);
    e("text", { x: 505, y: 560, class: "small muted-t" }, "probe position proposed, not installed");
    // ---- main pumps on the shared drive, and the sectors
    L.main_pumps.forEach((mp, i) => pump(760, pumpY(i), EQ["pump." + mp], `Main pump ${i + 1}`, "right"));
    const ds = eqState(P, PR, EQ["drive." + drv]), drf = v(`drive.${drv}.fault`);
    hot("#main-pumps", "drive." + drv, "equip", `Pump drive: ${ds.word}${drf.ok && drf.v ? ", DRIVE FAULT" : ""}. Select for details`, () => {
      e("rect", { x: 690, y: 478, width: 140, height: 50, rx: 6, class: "box eqbox " + ds.cls + " hl" });
      e("text", { x: 760, y: 497, "text-anchor": "middle", class: "small" }, "Pump drive (shared)");
      e("text", { x: 760, y: 514, "text-anchor": "middle", class: "small st-t " + ds.cls }, drf.ok && drf.v ? "⚠ DRIVE FAULT" : ds.word);
    });
    L.sectors.forEach((s, j) => {
      const ys = secY(j), id = s.sector_id, page = "#sector-" + id;
      valve(880, ys, EQ["valve." + id], `valve ${id}`);
      tag(912, ys - 22, "F", "flow." + id, page);
      tag(912, ys + 24, "P", "pressure." + id, page);
      hot(page, "sector." + id, "sector", `${W.sectorName(id)}: flow ${short(v("flow." + id), 1)}, pressure ${short(v("pressure." + id), 2)}. Select for details`, () => {
        e("rect", { x: 930, y: ys - 52, width: 160, height: 104, rx: 6, class: "box sector-box hl" + (sectorFlow[id] === "flow" ? " watering" : "") });
        e("text", { x: 1010, y: ys - 33, "text-anchor": "middle", class: "th" }, W.sectorName(id));
        e("text", { x: 1010, y: ys - 17, "text-anchor": "middle", class: "small" }, `channels ${s.channels[0]}–${s.channels[s.channels.length - 1]} · ${s.channels.length * L.outlets_per_channel} outlets`);
        e("text", { x: 1010, y: ys + 1, "text-anchor": "middle", class: "small" }, `flow ${short(v("flow." + id), 1)}`);
        e("text", { x: 1010, y: ys + 17, "text-anchor": "middle", class: "small" }, `pressure ${short(v("pressure." + id), 2)}`);
        e("text", { x: 1010, y: ys + 35, "text-anchor": "middle", class: "small " + (sectorFlow[id] === "flow" ? "flow-t" : "muted-t") },
          sectorFlow[id] === "flow" ? "being watered" : `automatic: main pump ${s.auto_pump.slice(2)}`);
      });
    });
    // ---- drainage
    e("text", { x: 1032, y: 592, "text-anchor": "middle", class: "small muted-t" }, "by gravity, not measured");
    tank(860, 560, 100, 80, "Drainage tank", "SUMP", "#drainage");
    tag(946, 574, "L", "tank.SUMP.level", "#drainage");
    L.return_pumps.forEach((rp, i) => pump(770 - i * 100, 620, EQ["pump." + rp], `Return pump ${rp.slice(2)}`, "left"));
    tag(520, 640, "F", "flow.RETURN", "#drainage");
    flowLabel(536, 644, "flow.RETURN");
    const sc = v("safety.chain_ok");
    e("text", { x: 20, y: 690, class: sc.ok && !sc.v ? "fault-mark" : "small" }, `Safety circuit: ${short(sc, 0, ["healthy", "OPEN"])}`);
    return svg;
  }
  function legend(P) {
    const sw = (cls, word, what) => h("span", { class: "lg" }, h("span", { class: "pill " + cls }, word), " ", what);
    return h("div", { class: "muted small legend-box" },
      h("p", null, "Pumps and valves: ", sw("st-on", "Running / Open", "the equipment reports it"), " ", sw("st-move", "Starting… / Opening…", "asked for, not yet reported (also Stopping… and Closing…)"),
        " ", sw("st-off", "Stopped / Closed", ""), " ", sw("st-fault", "Fault", ""), " ", sw("st-unk", "Not known", "no feedback, or its reading is old")),
      h("p", null, "Pipes: moving water and arrows where water flow is measured; dashed where the program asked for a route but no flow is measured yet; dotted where the flow reading is old or missing. ",
        "Round tags are sensors (L level, F flow, P pressure, pH, EC, T temperature); a “?” marks an old or unavailable reading. ▲ and ▼ show a level the readings show rising or falling."),
      h("p", null, "Select a pump, valve, tank, sensor or sector to see it beside the diagram. ", provenance(P) + " Layout: " + P.layout.provenance + "."));
  }
  function scrollHint() { return h("p", { class: "scroll-hint muted" }, "Scroll sideways to see the whole greenhouse."); }

  // ---------------------------------------------------------------- the selected part: readings and actions (O-04)
  /** Asks the server for the next acquisition of one signal; the panel shows that sample with its own time. */
  async function askReading(signal) {
    const ask = S.reading = { signal, at: Date.now(), state: "asking" };
    try {
      const r = await api("GET", `/api/plant/reading?signal=${encodeURIComponent(signal)}`);
      if (S.reading === ask) S.reading = { signal, at: ask.at, state: "done", r, got: Date.now() };
    } catch (err) {
      if (S.reading === ask) S.reading = { signal, at: ask.at, state: "failed", msg: W.refusal(String(err.message || err)) };
    }
    U.refresh();
  }
  function readingBox(P, signal) {
    const R = S.reading && S.reading.signal === signal ? S.reading : null, name = W.signal(signal);
    const again = h("button", { class: "small ghost", onclick: () => { askReading(signal); U.refresh(); } }, "Ask for a new reading");
    let body;
    if (!R || R.state === "asking") body = h("p", { "data-reading": "asking" }, "Asking for a new reading…");
    else if (R.state === "failed") body = h("p", { class: "error", "data-reading": "failed" }, "No answer: ", R.msg);
    else if (!R.r.bound) body = h("p", { class: "muted", "data-reading": "unbound" }, R.r.why === "DRIVE_ADAPTER" ? "This is a command over the drive's data link, not a measurement." : "Not measured: no channel carries this signal on this profile.");
    else if (!R.r.sample) body = h("p", { class: "warn-t", "data-reading": "none" }, "No reading has arrived from this channel yet.");
    else {
      const sm = R.r.sample, ageNow = sm.age_s + (Date.now() - R.got) / 1000;
      const val = sm.value == null || ["INVALID", "UNAVAILABLE"].includes(sm.quality) ? "—" : typeof sm.value === "boolean" ? (sm.value ? "on (1)" : "off (0)") : `${W.fixed(sm.value, signal.startsWith("main.") ? 2 : 1)}${sm.unit ? " " + sm.unit : ""}`;
      body = [h("p", { class: R.r.fresh ? "" : "warn-t", "data-reading": R.r.fresh ? "fresh" : "old" },
        R.r.fresh ? "New reading, taken after you asked:" : "No new reading arrived within 3 seconds. This is the newest reading, not a new one:"),
        kvRows([["Value", h("strong", { "data-value": "" }, val)], ["Unit", sm.unit || "none"], ["Measured at", h("span", { "data-acquired": sm.acquired_utc }, fmtTime(sm.acquired_utc))],
          ["Age", U.ago(ageNow)], ["Validity", qBadge(sm.quality, ageNow)], ["You asked at", fmtTime(new Date(R.at).toISOString())],
          ["Channel", h("span", { class: "mono" }, `${R.r.point}${R.r.label ? " (" + R.r.label + ")" : ""}`)], ["Origin", sm.origin]])];
    }
    return h("div", { class: "reading-box" }, h("h3", null, name), body, h("div", { class: "row" }, again, historyLink(P, [signal])));
  }
  function historyLink(P, signals) {
    const pts = signals.map(n => P.signals[n] && P.signals[n].point).filter(Boolean);
    return pts.length ? h("a", { href: "#history", onclick: () => { S.trendSel = pts.slice(0, 8); } }, "History of these readings") : null;
  }
  // the operation a piece of equipment is part of, with its amounts from the recipe in use where it sets them
  function opOf(e, P, PR, form) {
    const rec = PR && PR.active_recipe_body, L = P.layout;
    const seqDur = sec => { const st = rec && (rec.irrigation.sequence || []).find(x => x.sector === sec); return st ? st.duration_s : null; };
    const val = (k, d) => form && form[k] != null && form[k] !== "" ? form[k] : d;
    if (e.proc === "IRRIGATION" && !e.drive) {
      const sector = e.sector || val("sector", (L.sectors.find(s => s.auto_pump === e.pump) || L.sectors[0]).sector_id);
      const pumpId = e.pump || val("pump", (L.sectors.find(s => s.sector_id === sector) || {}).auto_pump);
      const dur = val("duration_s", seqDur(sector));
      return { op: { pump: pumpId, sector, duration_s: Number(dur) }, what: `waters ${W.sectorName(sector)} once with ${W.equip(pumpId)} for ${dur == null || dur === "" ? "the time you set" : dur + " s"}: the valve opens first, then the pump starts`,
        fields: [e.sector ? ["pump", "Main pump", L.main_pumps.map(x => [x, W.cap(W.equip(x))]), pumpId] : ["sector", "Sector", L.sectors.map(s => [s.sector_id, W.sectorName(s.sector_id)]), sector],
          ["duration_s", "Watering time (s)", null, seqDur(sector)]] };
    }
    if (e.proc === "RETURN") return { op: { pump: e.pump, duration_s: Number(val("duration_s", null)) }, what: `runs ${W.equip(e.pump)} once for the time you set`, fields: [["duration_s", "Run time (s)", null, null]] };
    if (e.proc === "ACID") { const d = rec ? rec.acid.dose_step_L : null; return { op: { volume_L: Number(val("volume_L", d)) }, what: "adds one dose of acid to the main tank: the outlet valve opens, then the transfer pump runs", fields: [["volume_L", "Acid to add (L)", null, d]] }; }
    if (e.proc === "REFILL") {
      const path = e.water ? "WATER" : STOCK_OF[e.tab], r = rec && rec.refill;
      const d = !r ? null : path === "WATER" ? (r.water || {}).target_L : path === "A" ? (r.fixed || {}).a_L : (r.fixed || {}).b_L;
      return { op: { path, volume_L: Number(val("volume_L", d)) }, what: path === "WATER" ? "adds one amount of mains water to the main tank" : `adds one amount of fertiliser ${path} to the main tank: the outlet valve opens, then the transfer pump runs`,
        fields: [["volume_L", "Amount (L)", null, d]] };
    }
    if (e.proc.startsWith("STOCK_FILL_")) {
      const s = e.proc.slice(11), d = rec && rec.stock_fill && rec.stock_fill[s] ? rec.stock_fill[s].target_L : null;
      return { op: { target_L: Number(val("target_L", d)) }, what: `fills ${W.TANK[s].toLowerCase()} with mains water once, up to the level you set`, fields: [["target_L", "Fill up to (L)", null, d]] };
    }
    return null;
  }
  const verb = (e, on) => e.kind === "valve" ? (on ? "open" : "close") : (on ? "start" : "stop");
  /** What pressing the action does, before it is done: null when it can go straight to the controller (Manual mode), else the confirmation. */
  function plan(e, on, pv, o) {
    const w = W.proc(e.proc), pn = w.name.toLowerCase(), nm = e.name;
    if (pv.state === "FAULT_LOCKOUT") return { refuse: `${w.name} is stopped because of a problem. Clear the stop on its page first; nothing is sent from here.` };
    if (pv.stopping) return { refuse: `${w.name} is still stopping. Wait until it has stopped.` };
    if (!on) {
      if (pv.state === "STOPPED") return { refuse: `${w.name} is stopped; the program is not running ${nm}.` };
      if (pv.state === "MANUAL") return pv.manual_op ? { direct: true, tell: `Ends the run once: ${pn} stops in a safe order and goes to Stopped.` }
        : { refuse: `Nothing is running in Manual mode; the program is not running ${nm}.` };
      return { confirm: `${W.cap(verb(e, false))} ${nm}?`, body: `${w.name} is in automatic mode. To ${verb(e, false)} ${nm}, the program stops ${pn}: pumps stop first, then valves close. ` +
        `${w.name} then stays stopped until you start it again on its page.`, yes: `Stop ${pn} and ${verb(e, false)} ${nm}` };
    }
    if (e.drive) return { refuse: "The drive starts only as part of watering. Select a main pump or a sector valve to water a sector once." };
    if (pv.state === "MANUAL" && pv.manual_op) return { refuse: `${w.name} is already running once. Stop it before asking for something else.` };
    if (pv.state === "MANUAL") return { direct: true, tell: `In Manual mode: ${o.what}.` };
    if (pv.state === "AUTO") return { confirm: `${W.cap(verb(e, true))} ${nm}?`, body: `${w.name} is in automatic mode. To ${verb(e, true)} ${nm} now, the program stops ${pn} ` +
      `(anything running ends with a safe stop), switches ${pn} to Manual mode, then ${o.what}. Automatic operation of ${pn} stays off until you start it again on its page.`,
      yes: `Stop automatic, switch to Manual mode and ${verb(e, true)}` };
    return { confirm: `${W.cap(verb(e, true))} ${nm}?`, body: `${w.name} is stopped. To ${verb(e, true)} ${nm}, the program switches ${pn} to Manual mode, then ${o.what}. Nothing else changes.`,
      yes: `Switch to Manual mode and ${verb(e, true)}` };
  }
  async function waitState(pid, ok, ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const PR = await api("GET", "/api/processes");
      if (ok(PR.processes[pid])) return true;
      await new Promise(r => setTimeout(r, 400));
    }
    return false;
  }
  /** Sends the operation through the controller: stop (and wait for it), Manual mode, then the bounded operation. */
  async function runAction(e, on, op, shown) {
    const pid = e.proc, w = W.proc(pid);
    const pend = S.diagPend = { key: e.key, on, at: Date.now(), step: "Sending the request…", sent: [] };
    const send = async (path, body, step) => { pend.step = step; U.refresh(); pend.sent.push(path); return api("POST", path, body); };
    try {
      if (S.stale) throw new Error("Connection stale: nothing was sent");
      let PR = await api("GET", "/api/processes"), pv = PR.processes[pid];
      // R34-01 (0.7.0): act only on the situation the user saw; the server refuses too if it changed meanwhile
      if (shown && pv.state !== shown) throw new Error(`${w.name} changed from ${W.state(shown).toLowerCase()} to ${W.state(pv.state).toLowerCase()} since you looked; nothing was sent. Look again and confirm`);
      if (!on) await send(`/api/processes/${pid}/state`, { target: "STOPPED", expect: pv.state }, `Asking ${w.name.toLowerCase()} to stop…`);
      else {
        if (pv.state === "AUTO") {
          await send(`/api/processes/${pid}/state`, { target: "STOPPED", expect: "AUTO" }, `Stopping automatic ${w.name.toLowerCase()}…`);
          if (!await waitState(pid, x => x.state === "STOPPED" && !x.stopping, 60000)) throw new Error(`${w.name} did not finish stopping within 60 s; nothing more was sent`);
          pv = (await api("GET", "/api/processes")).processes[pid];
        }
        if (pv.state === "STOPPED") await send(`/api/processes/${pid}/state`, { target: "MANUAL", expect: "STOPPED" }, `Switching ${w.name.toLowerCase()} to Manual mode…`);
        await send(`/api/processes/${pid}/manual`, { op, expect: "MANUAL" }, "Sending the operation…");
      }
      pend.step = "sent"; pend.sentAt = Date.now();
    } catch (err) {
      pend.step = "refused"; pend.msg = W.refusal(String(err.message || err));
      toast(`${e.name}: ${pend.msg}`, true);
    }
    U.refresh();
  }
  function pendingBox(P, PR, e) {
    const p = S.diagPend;
    if (!p || p.key !== e.key) return null;
    const s = eqState(P, PR, e), pv = PR && PR.processes[e.proc], secs = Math.round((Date.now() - (p.sentAt || p.at)) / 1000);
    const want = p.on ? "on" : "off", w = ST[want][e.kind];
    let line, cls = "pending";
    if (p.step === "refused") { line = `Refused: ${p.msg}`; cls = "error"; }
    else if (p.step !== "sent") line = p.step;
    else if (s.st === want) { line = `Reported: ${s.word.toLowerCase()} (${s.how || "its command"}), ${secs} s after the request.`; cls = "done"; }
    else if (pv && pv.state === "FAULT_LOCKOUT") { line = `The controller stopped it: ${sentence(e.proc, pv, P)}`; cls = "error"; }
    else if (secs < 30) line = `Request accepted by the controller. Waiting for the equipment to report ${w.toLowerCase()}… (${secs} s)`;
    else { line = `No report from the equipment ${secs} s after the request. The program asked for it, but nothing confirms it is ${w.toLowerCase()}. See ${W.proc(e.proc).name} for the reason.`; cls = "warn-t"; }
    return h("div", { class: "pend " + cls, role: "status", "aria-live": "polite", "data-pending": p.step === "sent" ? (cls === "done" ? "reported" : cls === "pending" ? "waiting" : "no-report") : p.step === "refused" ? "refused" : "sending" },
      h("p", null, line), p.sent.length ? h("p", { class: "muted small" }, "Sent to the controller: ", p.sent.join(", ")) : null);
  }
  function equipPanel(P, PR, e, readOnly) {
    const s = eqState(P, PR, e), pv = s.pv, w = W.proc(e.proc);
    const rows = [["State", stPill(s)], ["Asked for", s.asked == null ? "not known" : ST[s.asked ? "on" : "off"][e.kind].toLowerCase()],
      ["Reported", s.rep == null ? (s.stale ? "reading old or unavailable" : "nothing reports it yet") : `${ST[s.rep ? "on" : "off"][e.kind].toLowerCase()}, from ${s.how}`],
      e.flow ? ["Water flow", obs(P, e.flow, 1)] : null, e.fault ? ["Drive fault", obs(P, e.fault, 0, ["FAULT", "none"])] : null,
      pv ? ["Run by", h("span", null, h("a", { href: w.page }, w.name), " ", statePill(pv.state))] : null];
    const kids = [h("h3", null, e.name), kvRows(rows)];
    if (readOnly || !PR) kids.push(h("p", { class: "muted" }, "Real equipment, read only: nothing can be operated from this release."));
    else if (!can("process_control")) kids.push(h("p", { class: "muted" }, "Operating equipment needs an operator or administrator account."));
    else {
      const on = !(s.asked === true || s.st === "on" || s.st === "down");
      const form = S.diagForm && S.diagForm.key === e.key ? S.diagForm.v : {};
      const o = on ? opOf(e, P, PR, form) : null, pl = plan(e, on, pv, o || {});
      const label = `${W.cap(verb(e, on))} ${e.kind === "valve" ? "valve" : e.drive ? "drive" : "pump"}`;
      const fields = on && o && !pl.refuse ? h("div", { class: "row" }, o.fields.map(([k, lab, opts, def]) => {
        const cur = form[k] != null ? form[k] : def;
        const inp = opts ? h("select", { "aria-label": lab }, opts.map(([x, t]) => h("option", { value: x, selected: x === cur }, t)))
          : h("input", { type: "number", min: 0, step: "any", class: "w-num", value: cur == null ? "" : String(cur), placeholder: def == null ? "required" : null, "aria-label": lab });
        inp.addEventListener("change", () => { S.diagForm = { key: e.key, v: { ...(S.diagForm && S.diagForm.key === e.key ? S.diagForm.v : {}), [k]: inp.value } }; });
        return h("label", null, lab + (def != null && opts == null ? " (from the recipe in use)" : ""), inp);
      })) : null;
      const go = () => {
        const op = on ? opOf(e, P, PR, S.diagForm && S.diagForm.key === e.key ? S.diagForm.v : {}).op : null;
        if (op && Object.values(op).some(x => typeof x === "number" && !isFinite(x))) { toast("Set the amount first.", true); return; }
        const shown = S.diagConfirm && S.diagConfirm.key === e.key ? S.diagConfirm.state : pv && pv.state;
        S.diagConfirm = null; runAction(e, on, op, shown);
      };
      let conf = S.diagConfirm && S.diagConfirm.key === e.key && S.diagConfirm.on === on ? S.diagConfirm : null;
      if (conf && pv && conf.state !== pv.state) {      // R34-01: another session changed it while this was open
        S.diagConfirm = null; conf = null;
        S.diagPend = { key: e.key, on, at: Date.now(), step: "refused", sent: [],
          msg: `${w.name} changed to ${W.state(pv.state).toLowerCase()} while you were confirming; nothing was sent. Look again.` };
      }
      if (pl.refuse) kids.push(h("p", { class: "warn-t", "data-refusal": "" }, pl.refuse));
      else if (conf) kids.push(h("div", { class: "confirm", role: "alertdialog", "aria-label": pl.confirm }, h("p", null, h("strong", null, pl.confirm)), h("p", null, pl.body), fields,
        h("div", { class: "row" }, h("button", { class: "ghost", "data-act": "cancel", onclick: () => { S.diagConfirm = null; U.refresh(); } }, "Cancel"),
          h("button", { class: on ? "" : "danger", "data-act": "confirm", onclick: go }, pl.yes))));
      else kids.push(h("p", { class: "muted small" }, pl.direct ? pl.tell : `${W.cap(w.name)} is ${W.state(pv.state).toLowerCase()}: you will be asked to confirm first.`), pl.direct ? fields : null,
        h("div", { class: "row" }, h("button", { class: "cmd" + (on ? "" : " danger"), "data-act": "equip", "data-on": on ? "1" : "0",
          onclick: () => { if (pl.direct) go(); else { S.diagConfirm = { key: e.key, on, state: pv && pv.state }; U.refresh(); } } }, label)));
    }
    kids.push(pendingBox(P, PR, e));
    kids.push(h("div", { class: "row" }, h("a", { href: e.page, onclick: () => { if (e.tab) S.stockTab = e.tab; } }, `Open ${pageName(e.page)}`),
      historyLink(P, [e.cmd, e.fb, e.flow, e.run].filter(Boolean))));
    return kids;
  }
  function sidePanel(P, PR, readOnly) {
    const sel = S.diagSel;
    let kids;
    if (!sel) kids = [h("h3", null, "Nothing selected"), h("p", { class: "muted" }, "Select a pump, valve, tank, sensor or sector in the diagram to see its state and readings here",
      readOnly ? "." : ", and to operate a pump or valve through its process.")];
    else if (sel.type === "equip") { const e = equipment(P)[sel.key]; kids = e ? equipPanel(P, PR, e, readOnly) : [h("p", null, "Not in this layout.")]; }
    else if (sel.type === "tank") {
      const t = sel.key.slice(5), tr = trendOf(t), rate = levelRate(t), page = { MAIN: "#main-tank", SUMP: "#drainage" }[t] || "#nutrients";
      const sw = Object.keys(P.signals).filter(n => n.startsWith(`tank.${t}.`) && n !== `tank.${t}.level`);
      kids = [h("h3", null, W.TANK[t]), kvRows([["Water level", level(P, t)], ["Capacity", W.n0(P.layout.tanks_L[t]) + " L"],
        ["Change", tr == null ? "not enough recent readings yet" : tr === "steady" ? "steady" : `${tr}, about ${W.n0(Math.abs(rate))} L/min (from the readings of the last 20 s)`],
        ...sw.map(n => [W.signal(n).replace(W.TANK[t] + " ", "").replace(/^./, c => c.toUpperCase()), obs(P, n, 0, ["ACTIVE", "clear"])])]),
        readingBox(P, `tank.${t}.level`), h("p", null, h("a", { href: page, onclick: () => { if (STOCK_OF[t]) S.stockTab = t; } }, `Open ${pageName(page)}`))];
    } else if (sel.type === "sector") {
      const id = sel.key.slice(7), e = equipment(P)["valve." + id];
      kids = [h("h3", null, W.sectorName(id)), kvRows([["Valve", e ? stPill(eqState(P, PR, e)) : "—"], ["Water flow", obs(P, "flow." + id, 1)], ["Pressure", obs(P, "pressure." + id, 2)]]),
        h("div", { class: "row" }, h("a", { href: "#sector-" + id }, `Open the ${W.sectorName(id)} page`), historyLink(P, ["flow." + id, "pressure." + id, `valve.${id}.open_limit`]))];
    } else kids = [readingBox(P, sel.key), h("p", null, h("a", { href: pageFor(sel.key) }, `Open ${pageName(pageFor(sel.key))}`))];
    return h("aside", { class: "side-panel", "aria-label": "Selected part of the diagram", "data-selected": sel ? sel.key : "" },
      sel ? h("button", { class: "small ghost close", onclick: () => { S.diagSel = null; S.diagConfirm = null; U.refresh(); } }, "Close") : null, kids);
  }
  function pageFor(signal) {
    const b = signal.split(".")[1] || "";
    if (/^S\d+$/.test(b)) return "#sector-" + b;
    if (signal.startsWith("main.") || b === "FILL_MAIN" || b === "MAIN") return "#main-tank";
    if (b === "RETURN" || b === "SUMP") return "#drainage";
    return "#nutrients";
  }
  /** The diagram with its side panel: the whole view of the Overview, the full diagram and the page diagrams. */
  function diagramView(P, PR, opts) {
    return h("div", { class: "diagram-layout" }, h("div", null, scrollHint(), h("div", { class: "diagram-wrap" }, diagram(P, opts, PR))), sidePanel(P, PR, opts && opts.readOnly));
  }

  // ---------------------------------------------------------------- processes: state, start and stop
  function statePill(s) { return pill(W.state(s), s === "FAULT_LOCKOUT" ? "bad" : s === "AUTO" ? "ok" : s === "MANUAL" ? "warn" : ""); }
  function pbtn(label, onclick, cls, perm, why) {
    const allowed = can(perm || "process_control");
    return h("button", { class: "cmd small " + (cls || ""), onclick, disabled: !allowed || !!why,
      title: !allowed ? "needs an operator or administrator account" : why || null }, label);
  }
  const setState = (pid, target, msg) => act("POST", `/api/processes/${pid}/state`, { target }, msg || `${W.proc(pid).name}: ${W.state(target).toLowerCase()} requested`);
  function opText(pid, op) {
    if (!op) return "";
    if (pid === "IRRIGATION") return `${W.sectorName(op.sector)} with ${W.equip(op.pump)}, ${op.duration_s} s`;
    if (pid === "REFILL") return `${op.path === "WATER" ? "water" : "fertiliser " + op.path}, ${op.volume_L} L`;
    if (pid === "ACID") return `${op.volume_L} L of acid`;
    if (pid === "RETURN") return `${W.equip(op.pump)}, ${op.duration_s} s`;
    return op.target_L != null ? `fill to ${W.n0(op.target_L)} L` : JSON.stringify(op);
  }
  /** What the process is doing, in one sentence. */
  function sentence(pid, pv, P) {
    const w = W.proc(pid), ph = W.phase(pv.phase);
    if (pv.state === "FAULT_LOCKOUT") {
      const o = lockAlarm(P, pid), pb = o ? W.problem(o) : null;
      return `${w.name} stopped because of a problem${pb ? ": " + pb.cause : ""}.`;
    }
    if (pv.stopping) return `${w.name}: stopping safely${ph && ph !== "stopping safely" ? " (" + ph + ")" : ""}.`;
    if (pv.state === "STOPPED") return `${w.name} is stopped.`;
    if (pv.state === "MANUAL") {
      if (pv.manual_op) return `Running once: ${opText(pid, pv.manual_op.op || pv.manual_op)}${ph ? " (" + ph + ")" : ""}.`;
      return "Manual mode: nothing is running. Set the values below and press Run once.";
    }
    if (pid === "IRRIGATION" && pv.sector) {
      return `Automatic watering: ${W.sectorName(pv.sector)} with ${W.equip(pv.pump)}` +
        (pv.duration_s ? `, ${num(pv.irrigated_s, 0)} of ${num(pv.duration_s, 0)} s watered` : "") + (ph && ph !== "watering" ? ` (${ph})` : "") + ".";
    }
    return `${w.name}: automatic${ph ? ", " + ph : ", waiting"}.`;
  }
  function lockAlarm(P, pid) {
    return (P.alarms || []).find(o => o.target === "process." + pid && String(o.rule_id).startsWith("PROC_") && !o.closed) || null;
  }
  function blockerText(b) {
    if (!b) return "";
    if (b === "acknowledge the fault alarm before reset") return "mark the problem as seen first (step 1)";
    if (b.startsWith("fault condition still present: ")) {
      const raw = b.slice(31), known = W.refusal(raw);
      return (known !== W.plain(raw) ? "" : "the problem is still there: ") + known.replace(/^./, c => c.toLowerCase());
    }
    if (b === "stop sequence still running") return "the equipment is still stopping";
    if (b.startsWith("outputs still commanded ON") || b.startsWith("equipment still reports activity")) return "some equipment still reports that it is running";
    return W.plain(b).replace(/^./, c => c.toLowerCase());
  }
  /** The recovery steps of a problem (F-05): mark as seen, fix, clear the stop, start again. Each is its own
   * action; none of them starts equipment, and a step that is not available yet says why. */
  function recovery(o, PR, P) {
    const pb = W.problem(o), sim = S.status && S.status.profile === "SIMULATED";
    const pid = pb.proc, pv = pid && PR ? PR.processes[pid] : null, w = pid ? W.proc(pid) : null;
    const seen = o.acknowledged;
    const steps = [];
    steps.push(h("li", null, seen ? [h("span", { class: "done" }, "✓ Marked as seen"), h("span", { class: "muted" }, ` by ${o.ack_user || "?"}`)]
      : [pbtn("Mark as seen", () => act("POST", "/api/alarms/ack", { rule_id: o.rule_id, target: o.target }, "marked as seen"), "", "alarm_ack"),
        h("span", { class: "muted" }, " This only records that you have seen it; it changes nothing on the equipment.")]));
    // R33-03: passing the reset checks shows the cause is gone only when those checks can see it while stopped
    // (a drive fault input, the safety circuit); a valve that did not open or a missing flow shows only on the next try
    const present = pv ? !!pv.fault_present : o.active;
    const unseen = pv && pv.state === "FAULT_LOCKOUT" && pv.fault_checkable === false && !present;
    steps.push(h("li", null, h("strong", null, "Fix the problem: "), pb.fix,
      sim && pb.simFault ? h("span", { class: "muted" }, " In the Digital Twin, a simulated fault is removed on the ", h("a", { href: "#twin" }, "Digital Twin"), " page.") : null,
      present ? h("span", { class: "warn-t" }, " The program still sees the problem.")
        : unseen ? h("span", { class: "muted", "data-check": "not-checkable" }, ` The program cannot check this while ${w.name.toLowerCase()} is stopped: `,
          "it shows again only if it happens at the next start. Check ", pb.equipment ? "the " + pb.equipment.replace(/^./, c => c.toLowerCase()) : "the equipment", " yourself before clearing the stop.")
        : h("span", { class: "muted" }, " The program no longer sees the problem; check the equipment yourself before starting again.")));
    if (pv && pv.state === "FAULT_LOCKOUT") {
      const why = pv.fault_present ? "fault condition still present: " + pv.fault_present : pv.reset_blocker;   // the cause first, then the rest
      steps.push(h("li", null, pbtn("Clear stop after fixing the problem", () => act("POST", `/api/processes/${pid}/reset`, undefined,
        `${w.name}: stop cleared; it stays stopped until you start it`), "ghost", "lockout_reset", why ? "Not yet: " + blockerText(why) : null),
        why ? h("span", { class: "muted" }, ` Not available yet: ${blockerText(why)}.`) : h("span", { class: "muted" }, " Ready. Clearing the stop does not start anything.")));
      steps.push(h("li", null, `Then start ${w.name.toLowerCase()} again on its page: `, h("a", { href: w.page }, `Go to ${w.name}`), ". Nothing starts by itself."));
    } else if (o.reset_required && !pid) {
      const why = !seen ? "mark it as seen first (step 1)" : o.active ? "the condition is still there" : null;
      steps.push(h("li", null, pbtn("Clear the alarm after fixing the problem", () => act("POST", "/api/alarms/reset", { rule_id: o.rule_id, target: o.target }, "alarm cleared"),
        "ghost", "lockout_reset", why ? "Not yet: " + why : null), why ? h("span", { class: "muted" }, ` Not available yet: ${why}.`) : null));
    } else if (!pid) {
      steps.push(h("li", { class: "muted" }, o.active ? "It clears by itself when the condition is gone." : "The condition is gone; marking it as seen closes it."));
    }
    return h("ol", { class: "steps" }, steps);
  }
  function problemCard(o, PR, P, compact) {
    const pb = W.problem(o), sev = W.SEVERITY[o.severity] || o.severity;
    const pv = pb.proc && PR ? PR.processes[pb.proc] : null;
    // a process's problem stays open until its stop is cleared: say that, not "still present"
    const now = pv ? (pv.state === "FAULT_LOCKOUT" ? (pv.reset_blocker && pv.reset_blocker.startsWith("fault condition") ? "still present" : "stopped until the stop is cleared") : "stop cleared")
      : o.active ? "still present" : "gone now";
    const link = pb.page ? h("a", { href: pb.page }, `Go to ${pb.proc ? W.proc(pb.proc).name : pageName(pb.page)}`) : null;
    return h("div", { class: "problem " + (o.severity === "CRITICAL" ? "bad" : "warn") },
      h("div", { class: "row" }, h("h3", null, pb.title), pill(sev, o.severity === "CRITICAL" ? "bad" : "warn"),
        h("span", { class: "muted" }, `since ${fmtTime(o.first_seen_utc)} · ${now} · ${o.acknowledged ? "seen" : "not yet seen"}`), link),
      pb.equipment ? h("p", null, "Equipment: ", h("strong", null, pb.equipment)) : null,
      compact ? null : recovery(o, PR, P),
      h("details", null, h("summary", null, "Details"), kvRows([["Code", h("span", { class: "mono" }, o.rule_id)], ["Where", h("span", { class: "mono" }, o.target)],
        ["Message", o.message || ""], ["Severity", o.severity], ["First seen", fmtTime(o.first_seen_utc)], ["Last seen", fmtTime(o.last_seen_utc)],
        o.value != null ? ["Value", String(o.value)] : null, ["Seen", o.acknowledged ? `by ${o.ack_user}` : "no"],
        o.latched ? ["Latched", o.reset_required ? "needs clearing" : "cleared " + (o.reset_user || "")] : null])));
  }
  function pageName(hash) {
    const all = PAGES.concat([["drainage", "Drainage return"], ["twin", "Digital Twin"], ["recipes", "Recipes"]]);
    const x = all.find(([k]) => "#" + k === hash);
    return x ? x[1] : hash.replace("#sector-S", "Sector ");
  }
  /** The start, mode and stop buttons and the one-operation form, in plain words (owner-confirmed). */
  function procCard(pid, PR, P, opts) {
    const pv = PR.processes[pid];
    if (!pv) return h("div", { class: "card" }, "Unknown process " + pid);
    const w = W.proc(pid), lock = pv.state === "FAULT_LOCKOUT", o = lock ? lockAlarm(P, pid) : null;
    const startWhy = lock ? "clear the stop first" : pv.state === "AUTO" ? "already automatic" : null;
    const next = pv.inhibit ? ["Waiting", W.plain(pv.inhibit)] : pv.next ? ["Next", W.plain(pv.next)] : null;
    const stopHelp = "stop-help-" + pid;
    return h("div", { class: "card proc" + (lock ? " lock" : ""), "data-process": pid },
      h("div", { class: "row" }, h("h2", null, w.name), statePill(pv.state)),
      h("p", { class: "lead" }, sentence(pid, pv, P)),
      kvRows([next, ["Equipment switched on", names(pv.outputs_on)]]),
      lock && o ? h("div", { class: "problem bad" }, h("h3", null, "What to do"), recovery(o, PR, P)) : null,
      h("div", { class: "row actions" },
        pv.auto_capable === false ? null : pbtn(w.start, () => setState(pid, "AUTO", w.starts ? `${w.name} started` : `${w.name}: automatic mode on`), "", null, startWhy),
        pbtn("Manual mode", () => setState(pid, "MANUAL", `${w.name}: manual mode`), "ghost", null, lock ? "clear the stop first" : pv.state === "MANUAL" ? "already in manual mode" : null),
        h("button", { class: "cmd danger stop", "aria-describedby": stopHelp, disabled: !can("process_control"),
          title: can("process_control") ? null : "needs an operator or administrator account",
          onclick: () => setState(pid, "STOPPED", `${w.name}: stop requested`) }, w.stop)),
      h("p", { class: "muted small", id: stopHelp }, W.HELP.stop),
      pv.auto_capable === false ? null : h("p", { class: "muted small" }, (w.starts ? "" : "Automatic mode: ") + w.auto),
      can("process_control") ? null : h("p", { class: "muted" }, "Starting and stopping needs an operator or administrator account."),
      manualForm(pid, pv, P.layout),
      opts && opts.extra || null,
      techDetails(pid, pv));
  }
  /** The identifiers, phases and checks behind the plain card (F-03): kept, under Details. */
  function techDetails(pid, pv) {
    const perms = Object.entries(pv.permissives || {});
    return h("details", { class: "tech" }, h("summary", null, `Details: checks before starting${perms.length ? ` (${perms.filter(([, x]) => !x.ok).length} not met)` : ""}, history and identifiers`),
      perms.length ? [h("h3", null, "Checks before starting"), table(["Check", "Result", "Detail"], perms.map(([k, x]) => h("tr", null, h("td", null, k.replace(/_/g, " ")),
        h("td", null, x.ok ? pill("met", "ok") : pill("not met", "bad")), h("td", { class: "muted" }, W.plain(x.detail || "")))))] : null,
      kvRows([["Process", h("span", { class: "mono" }, pid)], ["State / phase", h("span", { class: "mono" }, `${pv.state} / ${pv.phase} (since t = ${num(pv.phase_since_s, 1)} s)`)],
        ["Recipe", pv.recipe || "none (taken at the next start)"], ["Equipment in use", (pv.resources || []).join(", ") || "nothing"],
        ["Equipment switched on (ids)", (pv.outputs_on || []).join(", ") || "none"], pv.inhibit ? ["Waiting (as recorded)", pv.inhibit] : null,
        pv.next ? ["Next (as recorded)", pv.next] : null, pv.fault_reason ? ["Fault", `${pv.fault_reason}: ${pv.fault_detail || ""}`] : null,
        pv.last_result ? ["Last result", h("span", { class: "mono" }, summary(pv.last_result))] : null]),
      h("h3", null, "History of this process"),
      table(["sim s", "state", "phase", "reason", "by", "recipe"], pv.history.slice().reverse().map(t => h("tr", null, h("td", null, num(t.sim_s, 1)), h("td", null, t.to_state), h("td", null, t.to_phase),
        h("td", null, t.reason), h("td", null, t.actor), h("td", { class: "mono" }, t.recipe || "")))));
  }
  function manualForm(pid, pv, layout) {
    if (pv.state !== "MANUAL") return null;
    if (pv.manual_op) return h("p", null, "Running once: ", h("strong", null, opText(pid, pv.manual_op.op || pv.manual_op)));
    const go = op => act("POST", `/api/processes/${pid}/manual`, { op }, "running once");
    const inp = (ph, step) => h("input", { type: "number", min: 0, step: step || "any", placeholder: ph, class: "w-num" });
    const sel = opts => h("select", null, opts.map(([v, t]) => h("option", { value: v }, t)));
    let kids, what;
    if (pid === "IRRIGATION") {
      const p = sel(layout.main_pumps.map(x => [x, W.cap(W.equip(x))])), s = sel(layout.sectors.map(x => [x.sector_id, W.sectorName(x.sector_id)])), d = inp("e.g. 60");
      what = "Waters one sector once, with the pump you choose, for the time you set.";
      kids = [h("label", null, "Sector", s), h("label", null, "Main pump", p), h("label", null, "Watering time (s)", d),
        pbtn("Run once", () => go({ pump: p.value, sector: s.value, duration_s: Number(d.value) }))];
    } else if (pid === "REFILL") {
      const p = sel([["WATER", "Water"], ["A", "Fertiliser A"], ["B", "Fertiliser B"]]), vol = inp("litres");
      what = "Adds one amount of water or fertiliser to the main tank.";
      kids = [h("label", null, "What to add", p), h("label", null, "Amount (L)", vol), pbtn("Run once", () => go({ path: p.value, volume_L: Number(vol.value) }))];
    } else if (pid === "ACID") {
      const vol = inp("litres");
      what = "Adds one dose of acid to the main tank (at most the dose set in the recipe).";
      kids = [h("label", null, "Acid to add (L)", vol), pbtn("Run once", () => go({ volume_L: Number(vol.value) }))];
    } else if (pid === "RETURN") {
      const p = sel(layout.return_pumps.map(x => [x, W.cap(W.equip(x))])), d = inp("seconds");
      what = "Runs one return pump once for the time you set.";
      kids = [h("label", null, "Return pump", p), h("label", null, "Run time (s)", d), pbtn("Run once", () => go({ pump: p.value, duration_s: Number(d.value) }))];
    } else {
      const t = inp("litres");
      what = "Fills the tank with water once, up to the level you set.";
      kids = [h("label", null, "Fill up to (L)", t), pbtn("Run once", () => go({ target_L: Number(t.value) }))];
    }
    return h("div", { class: "subcard" }, h("h3", null, "Run once"), h("p", { class: "muted" }, what + " The same safety checks apply as in automatic mode."), h("div", { class: "row" }, kids));
  }
  /** On a narrow screen the page's main process and its Stop stay at the top of the screen (F-09). */
  /** The pinned bar of narrow screens: each process of the page as it is shown (on Nutrients & acid, the chosen
   * tank's processes), with its state and its own Stop, which sends the stop of that process. */
  function stopbar(PR, pids) {
    if (!PR || !can("process_control")) return null;
    const rows = pids.filter(pid => PR.processes[pid]).map(pid => {
      const pv = PR.processes[pid], w = W.proc(pid);
      return h("div", { class: "stoprow", "data-proc": pid },
        h("span", null, h("strong", null, w.name), " ", statePill(pv.state)),
        h("button", { class: "cmd danger stop", "data-proc": pid, onclick: () => setState(pid, "STOPPED", `${w.name}: stop requested`) }, w.stop));
    });
    return rows.length ? h("div", { class: "stopbar", role: "region", "aria-label": "State and stop of " + pids.map(p => W.proc(p).name).join(", ") }, rows) : null;
  }
  function summary(r) {
    // a readable line (the full record is in the batch table and Alarms & events)
    const skip = new Set(["events", "paths", "ec_steps", "doses", "kind", "batch_id", "run_id", "cycle_id"]);
    const label = k => k.replace(/^ph_/, "pH ").replace(/^ec_/, "EC ").replace(/_L$/, " (L)").replace(/_s$/, " (s)").replace(/_/g, " ");
    return Object.entries(r).filter(([k, x]) => !skip.has(k) && x != null && typeof x !== "object").slice(0, 8)
      .map(([k, x]) => `${label(k)} ${typeof x === "number" ? (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(2)) : x}`).join(" · ");
  }
  function table(hdr, rows) {
    return h("table", null, h("thead", null, h("tr", null, hdr.map(x => h("th", null, x)))), h("tbody", null, rows));
  }
  // ---------------------------------------------------------------- what each process page covers
  // procs: the processes it runs (the first is the page's main one); trend: the signals charted; sigs: signals
  // whose point alarms belong here; targets: other alarm targets (flow plausibility keys).
  const PAGE_DEF = {
    nutrients: { focus: "#nutrients", procs: ["REFILL", "ACID", "STOCK_FILL_A", "STOCK_FILL_B", "STOCK_FILL_ACID"],
      trend: ["tank.A.level", "tank.B.level", "tank.ACID.level", "main.ph", "main.ec"],
      sigs: ["pump.TP_A.running", "pump.TP_B.running", "pump.TP_ACID.running", "flow.TP_A", "flow.TP_B", "flow.TP_ACID"],
      targets: ["transfer.A", "transfer.B", "transfer.ACID"] },
    "main-tank": { focus: "#main-tank", procs: ["REFILL"], trend: ["tank.MAIN.level", "flow.FILL_MAIN", "flow.RETURN", "main.ec"],
      sigs: ["tank.MAIN.high_high", "tank.MAIN.low_low", "main.ph"], targets: [] },
    "main-pumps": { focus: "#main-pumps", procs: ["IRRIGATION"], trend: ["flow.S1", "flow.S2", "pressure.S1", "pressure.S2"],
      sigs: ["drive.MAIN.fault", "drive.MAIN.running", "safety.chain_ok", "contactor.MP1.closed", "contactor.MP2.closed"],
      targets: ["sector.S1", "sector.S2"] },
    drainage: { focus: "#drainage", procs: ["RETURN"], trend: ["tank.SUMP.level", "flow.RETURN", "tank.MAIN.level"],
      sigs: ["tank.SUMP.high", "tank.SUMP.low", "pump.RP1.running", "pump.RP2.running"], targets: ["return"] },
  };
  const sectorDef = id => ({ focus: "#sector-" + id, procs: ["IRRIGATION"], trend: ["flow." + id, "pressure." + id],
    sigs: [`valve.${id}.open_limit`, `valve.${id}.closed_limit`], targets: ["sector." + id] });

  function relevantAlarms(P, def) {
    const pts = new Set(def.sigs.concat(def.trend).map(n => P.signals[n] && P.signals[n].point).filter(Boolean));
    return P.alarms.filter(o => def.procs.some(pid => o.target === "process." + pid || o.target.startsWith("process." + pid + ".")) ||
      def.targets.includes(o.target) || pts.has(o.target));
  }
  /** Open problems with their recovery steps; a process lockout's steps are on its process card already. */
  function problemsCard(P, PR, def) {
    const list = (def ? relevantAlarms(P, def) : P.alarms).filter(o => !(def && PR && o.target.startsWith("process.") && String(o.rule_id).startsWith("PROC_") &&
      PR.processes[o.target.slice(8)] && PR.processes[o.target.slice(8)].state === "FAULT_LOCKOUT" && def.procs.includes(o.target.slice(8))));
    if (def && !list.length) return null;
    return h("div", { class: "card" }, h("h2", null, `${def ? "Other problems on this page" : "Problems"} (${list.length})`),
      list.length ? list.map(o => problemCard(o, PR, P)) : h("p", { class: "muted" }, "None."),
      h("p", null, h("a", { href: "#alarms" }, "All alarms and events")));
  }

  // Last hour of this page's recorded signals (60 s snapshots); refetched at most every 15 s.
  const trendCache = {};
  function trendCard(P, names_) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "chart chart-small");
    const leg = h("div", { class: "legend" });
    const card = h("div", { class: "card" }, h("h2", null, "Last hour"), svg, leg,
      h("p", { class: "muted" }, "Recorded readings of this page (one per minute). ", h("a", { href: "#history" }, "History and downloads")));
    const draw = async () => {
      const pts = names_.map(n => [n, P.signals[n]]).filter(([, x]) => x && x.point);
      if (!pts.length) { leg.replaceChildren(h("span", { class: "muted" }, "Nothing measured for this page on this profile.")); return; }
      const key = pts.map(([, x]) => x.point).join(","), now = Date.now();
      let c = trendCache[key];
      if (!c || now - c.at > 15000) {
        const start = new Date(now - 3600000), end = new Date(now);
        const tr = await api("GET", `/api/trends?points=${encodeURIComponent(key)}&start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}&limit=500`);
        c = trendCache[key] = { at: now, tr, start: start.getTime(), end: end.getTime() };
      }
      const meta = pid => { const e = pts.find(([, x]) => x.point === pid); return { kind: e[1].kind === "analog" ? "AI" : "DI", label: W.signal(e[0]) }; };
      const series = U.trendSeries(c.tr, pts.map(([, x]) => x.point), meta);
      const out = window.AqChart.render(svg, { series, events: c.tr.events.map(e => [Date.parse(e[0]), e[1] + (e[2] ? " by " + e[2] : "")]), tStart: c.start, tEnd: c.end, timezone: S.tz });
      leg.replaceChildren(...out.map(l => h("span", null, h("span", { style: "color:" + l.color, "aria-hidden": "true" }, l.suspect ? "◌ " : "■ "), l.name)));
    };
    return [card, () => draw().catch(e => leg.replaceChildren(h("span", { class: "error" }, "Chart unavailable: " + (e.message || e))))];
  }

  function provenance(P) {
    return P.read_only ? `Readings: REAL where a signal is mapped; ${P.mapping}.`
      : `Readings: from the Digital Twin (simulated, source ${S.status.twin ? S.status.twin.source : "?"}); ${P.mapping}.`;
  }
  /** The installation diagram with this page's parts highlighted: a secondary view, closed until opened (F-02). */
  function pageDiagram(P, PR, def) {
    const d = h("details", { class: "card", open: S.pageDiagram === true },
      h("summary", null, h("strong", null, "Greenhouse diagram"), h("span", { class: "muted" }, " (the parts of this page are highlighted)")),
      diagramView(P, PR, { readOnly: P.read_only, focus: def.focus }),
      h("p", { class: "muted" }, provenance(P), " ", h("a", { href: "#diagram" }, "Open the full diagram")));
    d.addEventListener("toggle", () => { S.pageDiagram = d.open; });
    return d;
  }
  /** On a hardware profile the controls are replaced by the reason they are not offered. */
  function noControl(P, what) {
    return h("div", { class: "card" }, h("h2", null, what || "Process control"),
      h("p", null, pill("Read only", "warn"), " Real equipment, read only: this release does not control real equipment, ",
        "so starting, stopping, Run once, tank confirmations and recipes are not offered here. The readings below are only shown."),
      h("p", { class: "muted" }, "No wiring has been approved yet, so no reading is connected (\u201cnot measured\u201d). Controlling the real ",
        "equipment needs approved wiring, checked sensors, a commissioned controller and the owner's written permission."),
      h("details", { class: "inline" }, h("summary", null, "Details"),
        h("p", { class: "muted small" }, `Profile ${S.status.profile} (ADR-0009: no process controller on real equipment). ${P.mapping}.`)));
  }
  async function load() {
    const P = await api("GET", "/api/plant");
    S.plant = P; S.plantAt = Date.now();          // also the status line's main tank level
    const PR = P.read_only ? null : await api("GET", "/api/processes");
    S.procs = PR; noteLevels(P);
    return [P, PR];
  }
  /** A process page: the main process's stop bar (narrow screens), the process cards with their state, Stop and
   * next step, the page's other problems, its own cards, the collapsed diagram and the last hour. */
  async function page(P, PR, def, top, cards, tech) {
    const [tc, draw] = trendCard(P, def.trend);
    mount("v-plant", stopbar(PR, def.stops || [def.procs[0]]), PR ? top : noControl(P), problemsCard(P, PR, def), cards,
      tech ? h("details", { class: "card tech" }, h("summary", null, h("strong", null, "Technical details")), tech) : null,
      pageDiagram(P, PR, def), tc);
    await draw();
  }
  const fmtv = (x, unit) => x == null ? "not set" : `${W.n0(x)}${unit ? " " + unit : ""}`;

  // ---------------------------------------------------------------- pages
  async function overview() {
    const [P, PR] = await load();
    const tanks = ["MAIN", "A", "B", "ACID", "SUMP"].map(t => h("tr", null, h("td", null, W.TANK[t]), h("td", null, level(P, t)),
      h("td", { class: "muted" }, (() => { const r = sig(P, `tank.${t}.level`); return r.ok ? `${(100 * r.v / P.layout.tanks_L[t]).toFixed(0)} % of ${W.n0(P.layout.tanks_L[t])} L` : ""; })())));
    const procs = PR.order.map(pid => {
      const pv = PR.processes[pid], w = W.proc(pid);
      return h("tr", null, h("td", null, h("a", { href: w.page }, w.name)), h("td", null, statePill(pv.state)), h("td", null, sentence(pid, pv, P)),
        h("td", null, pv.state === "STOPPED" && !pv.stopping ? null : h("button", { class: "cmd small danger", disabled: !can("process_control"),
          onclick: () => setState(pid, "STOPPED", `${w.name}: stop requested`) }, w.stop)));
    });
    // 0.7.1 (owner decision): the Overview is the diagram; the separate "what is happening now", tanks and sectors panels
    // are gone. Problems stay (they say what to do), and so does the one safety action, Stop all processes.
    const graph = window.AqOverview ? await window.AqOverview.card().catch(() => null) : null;
    const running = PR.order.filter(pid => PR.processes[pid].state !== "STOPPED" || PR.processes[pid].stopping);
    mount("v-overview",
      problemsCard(P, PR, null),
      graph && graph.dataset.overview === "graph" ? graph : h("div", { class: "card", id: "overview-diagram" }, h("h2", null, "Greenhouse diagram"), diagramView(P, PR, {}), legend(P),
        graph ? graph.querySelector("[data-migration]") : null),
      h("div", { class: "row" }, h("button", { class: "cmd danger", disabled: !can("process_control") || !running.length,
        onclick: () => confirm("Stop every process?") && act("POST", "/api/processes/stop-all", { reason: "operator stop all" }, "every process stopping") }, "Stop all processes"),
        h("span", { class: "muted small" }, running.length ? `${running.length} process${running.length === 1 ? "" : "es"} running or stopping; their state is on the diagram.` : "Nothing is running.")));
    void tanks; void procs;
  }
  /** O-05: each sector's name, valve, readings and its page and history, always visible on the Overview. */
  function sectorsCard(P, PR) {
    const EQ = equipment(P);
    return h("div", { class: "card", id: "overview-sectors" }, h("h2", null, "Sectors"), table(["Sector", "Valve", "Water flow", "Pressure", ""],
      P.layout.sectors.map(s => { const id = s.sector_id, e = EQ["valve." + id];
        return h("tr", { "data-sector": id }, h("td", null, h("strong", null, W.sectorName(id)), h("div", { class: "muted small" }, `channels ${s.channels[0]}–${s.channels[s.channels.length - 1]}`)),
          h("td", null, e ? stPill(eqState(P, PR, e)) : "—"), h("td", null, obs(P, "flow." + id, 1)), h("td", null, obs(P, "pressure." + id, 2)),
          h("td", null, h("a", { href: "#sector-" + id }, `Open ${W.sectorName(id)}`), " ", historyLink(P, ["flow." + id, "pressure." + id]))); })));
  }
  async function fullDiagram() {
    const P = await api("GET", "/api/plant");
    const PR = P.read_only ? null : await api("GET", "/api/processes");
    S.procs = PR; noteLevels(P);
    mount("v-plant", h("div", { class: "card" }, h("h2", null, "Greenhouse diagram"), diagramView(P, PR, { readOnly: P.read_only }), legend(P)));
  }
  /** 0.5.6: on the real system the Overview draws the layout accepted from the digital twin, read-only, with
   * each symbol showing its linked equipment item. Layout proposals from the twin are listed under it; an
   * administrator, or an operator with commissioning, accepts one (or restores the previous layout). */
  async function hwDiagram() {
    const Lv = await api("GET", "/api/layout"), A = window.AqLayout;
    const TC = await api("GET", "/api/twin-changes").catch(() => null);
    const st = S.lay = S.lay || { sel: null, preview: null, msg: {} };
    const prop = st.preview && Lv.proposals.find(x => x.digest === st.preview);
    const lay = prop ? prop.layout : Lv.layout;
    const say = (k, text, bad) => { st.msg[k] = { text, bad }; U.refresh(); };
    const msg = k => st.msg[k] ? h("span", { class: st.msg[k].bad ? "error small" : "muted small", role: "status" }, st.msg[k].text) : null;
    const send = async (k, path, body, ok) => { try { await api("POST", path, body); say(k, ok); } catch (e) { say(k, String(e.message || e), true); } };
    const svg = A.render(lay, Lv.readings, { readOnly: true, selected: st.sel, onSelect: (kind, id) => { st.sel = st.sel === id ? null : id; U.refresh(); } });
    const n = st.sel && lay.nodes.find(q => q.id === st.sel), r = n && n.item && Lv.readings[n.item];
    const s = n && ["pump", "valve", "box", "sector"].includes(n.kind) ? A.eqState(Lv.readings, n) : null;
    const sidePanel = h("div", { class: "card side-panel" }, n ? [h("h3", null, n.label || n.id), h("dl", { class: "kv" },
        h("dt", null, "Equipment"), h("dd", null, n.item ? (r ? `${r.name} (${n.item})` : `${n.item} (not in the equipment)`) : n.planned ? "planned, not installed yet" : "not linked"),
        s ? [h("dt", null, "State"), h("dd", null, h("span", { class: "pill " + s.cls }, s.word), " ", h("span", { class: "muted small" }, s.why))] : null,
        r ? [h("dt", null, "Output read back"), h("dd", null, r.coil), h("dt", null, "Feedback"), h("dd", null, r.feedback)] : null,
        r ? Object.entries(r.values).flatMap(([k, x]) => [h("dt", null, x.label || k), h("dd", null, x.value == null ? (x.quality === "NO CHANNEL" ? "no channel" : "no reading") : `${typeof x.value === "boolean" ? (x.value ? "on" : "off") : x.value}${x.unit ? " " + x.unit : ""} (${String(x.quality).toLowerCase()})`)]) : null)]
      : [h("h3", null, "Nothing selected"), h("p", { class: "muted" }, "Select a pump, valve, tank, sensor or sector in the diagram to see its state and readings here.")]);
    const list = (w, xs) => xs.length ? h("li", null, `${w}: ${xs.slice(0, 8).join(", ")}${xs.length > 8 ? ` and ${xs.length - 8} more` : ""}`) : null;
    const props = Lv.proposals.map(x => {
      const c = x.changes, note = h("input", { maxlength: 500, placeholder: "note (optional)", "aria-label": "Note" });
      return h("div", { class: "card" + (st.preview === x.digest ? " selected-card" : "") },
        h("strong", null, `From the twin, by ${x.by}, ${fmtTime(x.written_utc)}`), x.note ? h("p", null, x.note) : null,
        h("ul", { class: "small" }, list("added", c.added), list("removed", c.removed), list("moved", c.moved), list("changed", c.changed),
          list("pipes added", c.pipes_added), list("pipes removed", c.pipes_removed), list("pipes changed", c.pipes_changed)),
        x.issues.length ? h("p", { class: "warn small" }, `${x.issues.length} symbol(s) linked to equipment that is not on this system.`) : null,
        h("div", { class: "row" },
          h("button", { class: "small ghost", onclick: () => { st.preview = st.preview === x.digest ? null : x.digest; U.refresh(); } }, st.preview === x.digest ? "Show the current layout" : "Preview"),
          Lv.can_accept ? [note, h("button", { class: "small", onclick: () => { st.preview = null; send("acc-" + x.digest, "/api/layout/accept", { digest: x.digest, note: note.value }, "accepted: the Overview now draws this layout"); } }, "Accept this layout")]
            : h("span", { class: "muted small" }, "An administrator, or an operator with commissioning, accepts layouts."),
          msg("acc-" + x.digest)));
    });
    const m = Lv.meta;
    return h("div", { class: "card", id: "overview-layout" },
      h("div", { class: "row" }, h("h2", null, "Installation (read-only)"), prop ? h("span", { class: "pill warn" }, "PREVIEW of a proposal") : null),
      TC && TC.pending.length ? h("p", { class: "warn" }, `${TC.pending.length} equipment and program change(s) from the twin wait for approval: `, h("a", { href: "#twin" }, "review them on the Digital Twin page")) : null,
      h("div", { class: "diagram-layout" }, h("div", { class: "diagram-wrap" }, svg), sidePanel),
      A.legend(h),
      h("p", { class: "muted small" }, m.source === "default" ? "Standard drawing. " : `Layout accepted by ${m.accepted_by} on ${fmtTime(m.accepted_utc)} (${m.source}). `,
        "The layout is edited in the Digital Twin and accepted here; it changes the drawing only, never equipment, channels or programs. No process control exists on a hardware profile in this release.",
        Lv.previous && Lv.can_accept ? [" ", h("button", { class: "small ghost", onclick: () => send("restore", "/api/layout/restore", { digest: Lv.previous, note: "restore previous" }, "the previous layout is back") }, "Restore the previous layout"), " ", msg("restore")] : null),
      Lv.issues.length ? h("details", { class: "small" }, h("summary", null, `${Lv.issues.length} symbol(s) not matching the equipment`), h("ul", null, Lv.issues.map(x => h("li", null, x)))) : null,
      props.length ? [h("h3", null, "Layouts sent from the twin"), props] : null);
  }
  const TANK_OF = { A: "tank A", B: "tank B", ACID: "the acid tank" };
  /** F-08: preparing a fertiliser or acid tank is a person's confirmation; the simulated fertiliser is added on the
   * Digital Twin page instead. */
  function stockRows(P, PR) {
    return ["A", "B", "ACID"].map(s => {
      const r = PR ? PR.stocks[s] : null, t = TANK_OF[s], T = W.TANK[s];
      let status = null;
      if (r) {
        if (r.status === "PREPARED") status = [pill("Prepared", "ok"), h("div", { class: "muted" }, `confirmed by ${r.actor}, ${fmtTime(r.utc)}` +
          (r.level_at_ack_L != null ? `, for up to ${W.n0(num(r.level_at_ack_L, 0))} L` : "") + (r.note ? `: ${r.note}` : ""))];
        else if (r.status === "INVALIDATED" && /^twin:/.test(r.reason || "")) status = [pill("Waiting for confirmation", "warn"),
          h("div", null, `Simulated ${s === "ACID" ? "acid" : "fertiliser"} was added in the Digital Twin. Confirm ${t} is prepared before dosing.`)];
        else if (r.status === "INVALIDATED" && /fill|rose|rise|level/i.test(r.reason || "")) status = [pill("Needs preparing again", "warn"),
          h("div", null, `${W.cap(t)} needs preparing again before dosing.`), h("div", { class: "muted" }, W.plain(r.reason))];
        else if (r.status === "INVALIDATED") status = [pill("Marked not ready", "warn"), h("div", { class: "muted" }, W.plain(r.reason || r.note || ""))];
        else status = [pill("Not prepared", "warn"), h("div", { class: "muted" }, "Dosing from this tank waits until someone confirms it is prepared.")];
      }
      const confirmForm = () => {
        const note = h("input", { type: "text", maxlength: "200", placeholder: "e.g. 25 kg of A mixed in 4,000 L", "aria-label": `What was prepared in ${T.toLowerCase()}` });
        return h("details", { class: "inline" }, h("summary", null, `Confirm ${t} is prepared`),
          h("p", { class: "muted" }, "Confirm only after the fertiliser has been mixed into the tank by hand. Your name, the time and the tank's level are recorded."),
          h("label", null, "What was prepared (recorded)", note),
          pbtn("Confirm prepared", () => act("POST", `/api/stocks/${s}/acknowledge`, { note: note.value }, `${T}: prepared`)));
      };
      const notReady = () => {
        const why = h("input", { type: "text", maxlength: "200", placeholder: "why it is not ready", "aria-label": `Why ${T.toLowerCase()} is not ready` });
        return h("details", { class: "inline" }, h("summary", null, `Mark ${t} not ready`),
          h("p", { class: "muted" }, "Dosing from this tank stops until it is confirmed prepared again."),
          h("label", null, "Why (recorded)", why),
          pbtn("Mark not ready", () => { if (!why.value.trim()) { toast("Write why the tank is not ready.", true); return; }
            act("POST", `/api/stocks/${s}/invalidate`, { reason: why.value.trim() }, `${T}: marked not ready`); }, "ghost"));
      };
      return h("tr", null, h("td", null, h("strong", null, T)), h("td", null, level(P, s)),
        h("td", null, !r ? h("span", { class: "muted" }, "not tracked on this profile") : status),
        h("td", null, reqObs(P, `pump.TP_${s}.cmd`, `pump.TP_${s}.running`, ["running", "stopped"]), h("div", null, "flow ", obs(P, `flow.TP_${s}`, 1))),
        h("td", null, !r ? null : r.status === "PREPARED" ? notReady() : confirmForm()));
    });
  }
  /** The active recipe's settings for one tank tab: pH for acid, EC and A:B (or set amounts) for A and B. */
  function chemSettings(rec, tab) {
    if (!rec) return h("p", { class: "muted" }, "No recipe in use.");
    const sf = rec.stock_fill && rec.stock_fill[tab];
    const fill = sf ? [["Filled with water up to", fmtv(sf.target_L, "L")], ["Filled automatically when it falls to", fmtv(sf.auto_trigger_L, "L")], ["Never filled above", fmtv(sf.high_inhibit_L, "L")]] : [];
    if (tab === "ACID") {
      const a = rec.acid;
      return kvRows([["Acidity (pH) target", `${fmtv(a.ph_target)} (acid is added above ${a.ph_target == null || a.ph_tolerance == null ? "?" : num(a.ph_target + a.ph_tolerance, 2)})`],
        ["Stops with a problem below", a.ph_target == null || a.undershoot_margin == null ? "not set" : num(a.ph_target - a.undershoot_margin, 2) + " pH"],
        ["Acid in each dose", fmtv(a.dose_step_L, "L")], ["Wait after a dose", fmtv(a.settle_s, "s")], ["Most acid in one batch", fmtv(a.max_batch_L, "L")], ["Most acid in 24 hours", fmtv(a.max_day_L, "L")],
        ["Stops after", `${fmtv(a.max_no_response)} doses that lower pH by less than ${fmtv(a.min_response_ph)}`], ...fill]);
    }
    const r = rec.refill, ec = r.ec || {}, fx = r.fixed || {};
    const mode = { EC_TARGET: "Add nutrients until the EC target is reached", FIXED_VOLUMES: "Add set amounts of nutrients" }[r.nutrient_mode] || fmtv(r.nutrient_mode);
    const rows = [["How nutrients are added", mode], ["How much water", r.endpoint_mode === "FINAL_LEVEL" ? `Fill to a water level: ${fmtv(r.final_level_L, "L")}` : r.endpoint_mode === "FIXED_ADDITIONS" ? "Add a set amount of water" : fmtv(r.endpoint_mode)]];
    if (r.nutrient_mode === "EC_TARGET") rows.push(["Nutrient strength (EC) target", `${fmtv(ec.ec_target_mS_cm)} ± ${fmtv(ec.ec_tolerance_mS_cm, "mS/cm")}`], ["Fertiliser A for each litre of B", fmtv(ec.ratio_a_to_b)],
      ["A + B in each step", fmtv(ec.step_total_L, "L")], [`Most fertiliser ${tab} in one refill`, fmtv(tab === "A" ? ec.max_a_L : ec.max_b_L, "L")]);
    else rows.push([`Fertiliser ${tab} in each refill`, fmtv(tab === "A" ? fx.a_L : fx.b_L, "L")], ["EC afterwards must be between", `${fmtv(fx.ec_min_mS_cm)} and ${fmtv(fx.ec_max_mS_cm, "mS/cm")}`]);
    rows.push(["Always kept in the tank", fmtv(r.stock_reserve_L, "L")], ...fill);
    return kvRows(rows);
  }
  async function nutrients() {
    const [P, PR] = await load();
    const tab = S.stockTab || "A";
    const rec = PR ? PR.active_recipe_body : null;
    const tabs = h("div", { class: "tabs", role: "tablist" }, [["A", "Fertiliser tank A"], ["B", "Fertiliser tank B"], ["ACID", "Acid tank"]].map(([k, t]) =>
      h("button", { class: "tab" + (k === tab ? " active" : ""), role: "tab", "aria-selected": k === tab ? "true" : "false",
        onclick: () => { S.stockTab = k; U.refresh(); } }, t)));
    const name = W.TANK[tab];
    const top = PR ? [tab === "ACID" ? procCard("ACID", PR, P) : procCard("REFILL", PR, P), procCard("STOCK_FILL_" + tab, PR, P)] : null;
    const cards = [
      h("div", { class: "card" }, h("h2", null, "Fertiliser and acid tanks"),
        table(["Tank", "Water level", "Prepared?", "Transfer pump", ""], stockRows(P, PR)),
        h("p", { class: "muted" }, "Tanks are prepared by hand. Adding water never marks a tank prepared; adding water, a level rise above the confirmed level, or marking it not ready means it must be prepared and confirmed again. ",
          "In the Digital Twin, simulated fertiliser is added on the ", h("a", { href: "#twin" }, "Digital Twin"), " page.")),
      h("div", { class: "card" }, tabs,
        h("div", { class: "grid" },
          h("div", null, h("h3", null, `${name}: ${W.n0(P.layout.tanks_L[tab])} L`), kvRows([["Water level", level(P, tab)],
            ["Transfer pump", reqObs(P, `pump.TP_${tab}.cmd`, `pump.TP_${tab}.running`, ["running", "stopped"])], ["Transfer flow", obs(P, `flow.TP_${tab}`, 1)],
            ["Outlet valve", obs(P, `valve.OUT_${tab}.cmd`, 0, ["asked open", "asked closed"])], ["Water inlet valve", obs(P, `valve.FILL_${tab}.cmd`, 0, ["asked open", "asked closed"])]])),
          h("div", null, h("h3", null, `Settings in use (${PR ? PR.recipe.active || "no recipe in use" : "no recipes on this profile"})`), PR ? chemSettings(rec, tab) : h("p", { class: "muted" }, "Recipes exist only in simulation in this release."),
            PR ? h("p", null, h("a", { href: "#recipes" }, "Change these settings (Recipes)")) : null))),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "Main tank water quality"), kvRows([["Acidity (pH)", obs(P, "main.ph", 2)], ["Nutrient strength (EC)", obs(P, "main.ec", 2)],
          ["Temperature", obs(P, "main.temperature", 1)]]),
          h("p", { class: "muted" }, W.HELP.ph), h("p", { class: "muted" }, W.HELP.ec),
          h("p", { class: "muted" }, "The probe position is proposed, not installed or verified. In the Digital Twin the values come from a synthetic fixture (synthetic-carbonate-ec-v1), not a chemical model. No temperature compensation.")),
        PR ? refillBatchCard(PR) : null)];
    const ac = PR && PR.processes.ACID;
    const tech = PR && tab === "ACID" ? [kvRows([["Doses without a pH response", String(ac.no_response_count)], ["Acid in the last 24 h", num(ac.day_total_L, 2) + " L"],
      ac.dose ? ["Dose", `${ac.dose.state}: ${num(ac.dose.delivered_L, 2)} of ${num(ac.dose.target_L, 2)} L`] : null]),
      (ac.batches || []).length ? [h("h3", null, "Acid batches"), table(["Batch"], ac.batches.slice().reverse().map(b => h("tr", null, h("td", { class: "mono" }, summary(b)))))] : null,
      h("p", { class: "muted" }, "Acid lowers pH only; no base dosing exists. Falling below the stop margin stops acid dosing with a problem.")] : null;
    // the pinned Stop follows the chosen tank: refill or acid dosing, and that tank's own water fill
    const stops = [tab === "ACID" ? "ACID" : "REFILL", "STOCK_FILL_" + tab];
    await page(P, PR, { ...PAGE_DEF.nutrients, stops }, top, cards, tech);
  }
  function refillBatchCard(PR) {
    const rf = PR.processes.REFILL, batch = rf.batch, paths = batch ? Object.values(rf.paths || {}) : [];
    return h("div", { class: "card" }, h("h2", null, "Refill now"), batch ? [kvRows([["Refill", batch.batch_id], ["How", `${batch.endpoint_mode} / ${batch.nutrient_mode}`],
      ["Nutrients", batch.nutrients], ["EC steps", String((batch.ec_steps || []).length)], ["Started at", W.n0(num(batch.start_level_L, 0)) + " L"]]),
      table(["Step", "State", "Added", "Planned", "Measured by"], paths.map(p => h("tr", null, h("td", null, p.name), h("td", null, p.state + (p.result ? " " + p.result : "")),
        h("td", null, num(p.delivered_L, 1) + " L"), h("td", null, p.target_L == null ? "—" : num(p.target_L, 1) + " L"), h("td", { class: "mono" }, p.measured_by))))]
      : h("p", { class: "muted" }, rf.armed ? "No refill running; one can start when the level falls to the start level." : "No refill running; another refill becomes possible once the level has risen enough."));
  }
  async function mainTank() {
    const [P, PR] = await load();
    const rec = PR ? PR.active_recipe_body : null, m = rec ? rec.main : null;
    const cards = h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, `Main tank (${W.n0(P.layout.tanks_L.MAIN)} L)`), kvRows([
        ["Water level", level(P, "MAIN")], ["Very high level switch", obs(P, "tank.MAIN.high_high", 0, ["ACTIVE", "clear"])],
        ["Very low level switch", obs(P, "tank.MAIN.low_low", 0, ["ACTIVE", "clear"])], ["Acidity (pH)", obs(P, "main.ph", 2)], ["Nutrient strength (EC)", obs(P, "main.ec", 2)],
        ["Water inlet valve", obs(P, "valve.FILL_MAIN.cmd", 0, ["asked open", "asked closed"])], ["Mains water flow in", obs(P, "flow.FILL_MAIN", 1)],
        ["Drainage return flow in", obs(P, "flow.RETURN", 1)]])),
      PR ? h("div", { class: "card" }, h("h2", null, `Levels in use (${PR.recipe.active || "no recipe in use"})`), m ? [kvRows([
        ["Lowest level for watering", fmtv(m.low_low_L, "L")], ["Refill starts below", fmtv(m.refill_trigger_L, "L")],
        ["Another refill possible above", fmtv(m.refill_trigger_L + m.rearm_hysteresis_L, "L")],
        ["Refill never fills above", fmtv(m.fill_stop_L, "L")], ["No water is let in above", fmtv(m.high_inhibit_L, "L")], ["Least water before adding nutrients or acid", fmtv(m.min_dosing_L, "L")]]),
        h("p", { class: "muted" }, `If refill starts below ${W.n0(m.refill_trigger_L)} litres and the required rise is ${W.n0(m.rearm_hysteresis_L)} litres, the tank must reach at least ${W.n0(m.refill_trigger_L + m.rearm_hysteresis_L)} litres before a later drop can trigger another refill. Reaching ${W.n0(m.refill_trigger_L + m.rearm_hysteresis_L)} litres makes another refill possible; it does not start one.`)]
        : h("p", { class: "muted" }, "No recipe in use."),
        h("p", null, h("a", { href: "#recipes" }, "Change these settings (Recipes)"))) : null,
      PR ? refillBatchCard(PR) : null);
    const tech = PR ? [h("h3", null, "Mixing"), kvRows(Object.entries(PR.mixing || {}).map(([k, x]) => [k, `${x.method}; last addition ${x.last_addition_sim_s == null ? "none" : num(x.last_addition_sim_s, 0) + " s"}`])),
      h("p", { class: "muted" }, "There is no stirrer or air pump. Settling is a simulated wait; on a physical profile mixing readiness is NOT_VERIFIED and dosing cannot run.")] : null;
    await page(P, PR, PAGE_DEF["main-tank"], PR ? procCard("REFILL", PR, P) : null, cards, tech);
  }
  /** The drive's Modbus interface as the plant view reports it (simulated drive, DEMO map, in the twin). */
  function driveIfaceRows(P, drv) {
    const s = P.signals[`drive.${drv}.run`], a = s && s.adapter;
    if (!a) return [["Drive interface", h("span", { class: "muted" }, P.read_only ? "unbound on a real profile (the drive's status word is on the Overview page)" : "NOT_CONFIGURED")]];
    const mb = a.modbus, st = mb && mb.status;
    return [["Drive interface", h("span", null, a.kind, " ", a.provenance ? pill(a.provenance, a.provenance === "VERIFIED" ? "ok" : "warn") : null)],
      mb ? ["Status word", mb.ok === false ? pill("UNREADABLE: " + (mb.error || ""), "bad")
        : st ? h("span", { class: "mono" }, `${st.status_word} · running ${st.running ? "yes" : "no"} · fault ${st.fault ? "YES" : "no"}${st.frequency != null ? " · " + num(st.frequency, 1) + " Hz" : ""}`) : "not read"] : null,
      mb && mb.interface ? ["Control register", h("span", { class: "mono" }, `${mb.interface.control_value} (comm-loss stop after ${mb.interface.comm_loss_timeout_s} s)`)] : null].filter(Boolean);
  }
  async function mainPumps() {
    const [P, PR] = await load();
    const ir = PR ? PR.processes.IRRIGATION : null, drv = P.layout.shared_drive;
    const now = ir ? h("div", { class: "card" }, h("h2", null, "Watering now"), ir.sector ? kvRows([["Sector", W.sectorName(ir.sector)], ["Main pump", W.cap(W.equip(ir.pump || "—"))],
      ["Watered", ir.duration_s ? `${num(ir.irrigated_s, 0)} of ${num(ir.duration_s, 0)} s with water flow confirmed` : "—"],
      ["Water measured", num(ir.delivered_L_measured, 1) + " L"], ["Round", String(ir.cycle)], ir.stop_stage ? ["Stopping", STOP_STAGE[ir.stop_stage] || ir.stop_stage] : null])
      : h("p", { class: "muted" }, ir.stop_stage ? "Stopping: " + (STOP_STAGE[ir.stop_stage] || ir.stop_stage) : "Nothing is being watered."),
      h("p", { class: "muted" }, "Changing from one sector to the next: the pump stops and is confirmed stopped, the old valve closes, the next pump is selected, its valve opens, at least 1 s passes, then the pump starts and the water flow is checked. If the stop is not confirmed, the valve and the pump selection stay as they are.")) : null;
    const sectors = h("div", { class: "card" }, h("h2", null, "Sectors"), table(["Sector", "Valve", "Water flow", "Pressure", ""],
      P.layout.sectors.map(s => h("tr", null, h("td", null, h("strong", null, W.sectorName(s.sector_id))),
        h("td", null, reqObs(P, `valve.${s.sector_id}.cmd`, `valve.${s.sector_id}.open_limit`, ["open", "not open"], ["open", "closed"])),
        h("td", null, obs(P, `flow.${s.sector_id}`, 1)), h("td", null, obs(P, `pressure.${s.sector_id}`, 2)), h("td", null, h("a", { href: "#sector-" + s.sector_id }, "Open"))))));
    const tech = [h("h3", null, "Pump drive and pump selection"), kvRows([
      ["Drive run", obs(P, `drive.${drv}.run`, 0)], ["Drive running", obs(P, `drive.${drv}.running`, 0, ["running", "stopped"])],
      ["Drive fault", obs(P, `drive.${drv}.fault`, 0, ["FAULT", "none"])], ["Safety circuit", obs(P, "safety.chain_ok", 0, ["healthy", "OPEN"])],
      ...P.layout.main_pumps.map((mp, i) => [`Selector K${i + 1} (${W.equip(mp)})`, reqObs(P, `contactor.${mp}.cmd`, `contactor.${mp}.closed`, ["closed", "open"])]),
      ...driveIfaceRows(P, drv)]),
      h("p", { class: "muted" }, "One drive feeds one motor at a time; K1/K2 select it, never under load. The run command goes over the drive's Modbus interface (ADR-0015); the hardwired running and fault inputs stay the proof. The real drive's interface stays NOT_CONFIGURED until its register map passes CP-06 on the bench."),
      ir && ir.cycles && ir.cycles.length ? [h("h3", null, "Completed steps"),
        table(["Round", "Sector", "Pump", "Watered", "Measured", "Mode", "Recipe"], ir.cycles.slice().reverse().map(c => h("tr", null, h("td", null, String(c.cycle)), h("td", null, W.sectorName(c.sector)), h("td", null, c.pump),
          h("td", null, num(c.irrigated_s, 0) + " s"), h("td", null, num(c.delivered_L_measured, 1) + " L"), h("td", null, c.mode), h("td", { class: "mono" }, c.recipe || ""))))] : null];
    await page(P, PR, PAGE_DEF["main-pumps"], ir ? procCard("IRRIGATION", PR, P) : null, h("div", { class: "grid" }, now, sectors), tech);
  }
  async function sector(id) {
    const [P, PR] = await load();
    const s = P.layout.sectors.find(x => x.sector_id === id);
    if (!s) return mount("v-plant", h("div", { class: "card" }, "Unknown sector " + id));
    const ir = PR ? PR.processes.IRRIGATION : null, ch = P.layout.channel;
    const step = (PR && PR.active_recipe_body ? PR.active_recipe_body.irrigation.sequence : []).filter(x => x.sector === id);
    const cycles = ir ? (ir.cycles || []).filter(c => c.sector === id).slice(-10).reverse() : [];
    // watering runs both sectors from the Watering page; here its state and Stop are at the top, its start there
    const top = ir ? h("div", { class: "card proc", "data-process": "IRRIGATION" }, h("div", { class: "row" }, h("h2", null, `${W.sectorName(id)}: watering`), statePill(ir.state)),
      h("p", { class: "lead" }, ir.sector === id ? sentence("IRRIGATION", ir, P) : `${sentence("IRRIGATION", ir, P)} ${W.sectorName(id)} is not being watered now.`),
      h("div", { class: "row actions" }, h("button", { class: "cmd danger stop", disabled: !can("process_control"), "aria-describedby": "stop-help-sector",
        onclick: () => setState("IRRIGATION", "STOPPED", "Watering: stop requested") }, "Stop watering"),
        h("a", { href: "#main-pumps" }, "Start watering or water this sector once on the Watering page")),
      h("p", { class: "muted small", id: "stop-help-sector" }, W.HELP.stop)) : null;
    const cards = [h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, W.sectorName(id)), kvRows([
        ["Valve", reqObs(P, `valve.${id}.cmd`, `valve.${id}.open_limit`, ["open", "not open"], ["open", "closed"])],
        ["Closed signal", obs(P, `valve.${id}.closed_limit`, 0, ["closed", "not closed"])],
        ["Water flow", obs(P, `flow.${id}`, 1)], ["Pressure", obs(P, `pressure.${id}`, 2)], ["Automatic watering uses", W.cap(W.equip(s.auto_pump))],
        PR ? ["Watering time in the recipe", step.length ? step.map(x => x.duration_s + " s").join(", ") : "not in the sequence"] : null]),
        h("p", { class: "muted" }, "There is no closed-signal channel in the point allocation (the 32 added IO 8CH channels stay unassigned): closing is judged from the open signal released and no measured flow (weaker evidence).")),
      h("div", { class: "card" }, h("h2", null, "Channels"),
        h("p", null, `Channels ${s.channels.join(", ")}: about ${ch.length_m} × ${ch.width_m} × ${ch.height_m} m, ${ch.retained_depth_m} m of water retained (${W.n0(num(ch.retained_L, 0))} L each, ${W.n0(num(ch.retained_L * s.channels.length, 0))} L in this sector).`),
        h("p", null, `${P.layout.outlets_per_channel} outlets per channel (${P.layout.outlets_per_channel * s.channels.length} here), adjusted by hand. They are not electric valves and are not observed.`),
        h("p", { class: "muted" }, "Owner-stated approximate geometry; unverified."))),
      ir ? h("div", { class: "card" }, h("h2", null, `Recent watering of ${W.sectorName(id)}`), cycles.length ? table(["Round", "Pump", "Watered", "Measured", "Mode"], cycles.map(c => h("tr", null,
        h("td", null, String(c.cycle)), h("td", null, W.cap(W.equip(c.pump))), h("td", null, num(c.irrigated_s, 0) + " s"), h("td", null, num(c.delivered_L_measured, 1) + " L"), h("td", null, c.mode)))) : h("p", { class: "muted" }, "None yet.")) : null];
    await page(P, PR, sectorDef(id), top, cards, null);
  }
  async function drainage() {
    const [P, PR] = await load();
    const rt = PR ? PR.processes.RETURN : null, b = PR && PR.active_recipe_body ? PR.active_recipe_body.return : null;
    const duty = { FIXED_RP1: "Always return pump 1", FIXED_RP2: "Always return pump 2", ALTERNATE_EACH_START: "Take turns at each start" };
    const cards = h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, `Drainage tank (about ${W.n0(P.layout.tanks_L.SUMP)} L)`), kvRows([
        ["Water level", level(P, "SUMP")], ["High level switch", obs(P, "tank.SUMP.high", 0, ["ACTIVE", "clear"])], ["Low level switch", obs(P, "tank.SUMP.low", 0, ["ACTIVE", "clear"])],
        ...P.layout.return_pumps.map(rp => [W.cap(W.equip(rp)), reqObs(P, `pump.${rp}.cmd`, `pump.${rp}.running`, ["running", "stopped"])]),
        ["Return flow", obs(P, "flow.RETURN", 1)], ["Main tank", obs(P, "tank.MAIN.level", 0)]])),
      PR ? h("div", { class: "card" }, h("h2", null, "Return settings in use"), b ? kvRows([["The return pump starts at", fmtv(b.sump_start_L, "L")], ["It stops at", fmtv(b.sump_stop_L, "L")],
        ["Held when the main tank reaches", fmtv(b.main_inhibit_L, "L")], ["Resumes when the main tank is below", fmtv(b.main_resume_L, "L")], ["Which return pump runs", duty[b.duty_policy] || b.duty_policy], ["Longest run", fmtv(b.max_runtime_s, "s")]]) : h("p", { class: "muted" }, "No recipe in use."),
        h("p", { class: "muted" }, "Which pump runs is set in the recipe; there is no hidden switching to the other pump. A high drainage tank with a full main tank raises an alarm, and the main tank is never filled above the hold level.")) : null);
    const tech = rt && rt.runs && rt.runs.length ? [h("h3", null, "Recent runs"), table(["Run"], rt.runs.slice().reverse().map(r => h("tr", null, h("td", { class: "mono" }, summary(r)))))] : null;
    await page(P, PR, PAGE_DEF.drainage, rt ? procCard("RETURN", PR, P) : null, cards, tech);
  }

  // ---------------------------------------------------------------- recipes (F-04)
  function getp(o, path) { return path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o); }
  function setp(o, path, val) {
    const ks = path.split("."); let cur = o;
    ks.slice(0, -1).forEach(k => { if (cur[k] == null || typeof cur[k] !== "object") cur[k] = {}; cur = cur[k]; });
    cur[ks[ks.length - 1]] = val;
  }
  const SECTIONS = [["main", "Main tank levels"], ["refill", "Refill"], ["acid", "Acid dosing"], ["irrigation", "Watering"], ["return", "Drainage return"], ["stock_fill", "Water fill of the fertiliser and acid tanks"]];
  const STATUS = { DRAFT: ["Draft, not checked", "warn"], VALIDATED: ["Checked, not in use", ""], ACTIVE: ["In use", "ok"], SUPERSEDED: ["Replaced", ""] };
  const MODES = ["refill.endpoint_mode", "refill.nutrient_mode"];
  let recipesKey = null;
  /** Whether a field applies to the modes chosen in the draft (the server's own `required` condition). */
  function applies(f, body) {
    if (!f.required || !f.required.includes("=")) return true;
    const [k, want] = f.required.split("=");
    return getp(body, k) === want;
  }
  async function recipes() {
    if (U.isReal()) return mount("v-plant", h("div", { class: "card" }, h("h2", null, "Recipes"),
      h("p", null, pill("NOT AVAILABLE", "warn"), " Recipes drive the simulated process controllers only; a hardware profile has none in this release (ADR-0009).")));
    const R = await api("GET", "/api/recipes");
    const E = S.recipeEdit;
    // nothing live on this page: rebuild it only when the recipes or the shape of the draft changed, so a field or
    // button is never replaced under the pointer (the fields write into the draft themselves)
    const key = JSON.stringify([R, E && [E.recipe_id, E.version, E.from, E.checked, E.issues, MODES.map(m => getp(E.body, m)),
      ((E.body.irrigation || {}).sequence || []).length], S.perms]);
    if (key === recipesKey && document.getElementById("v-plant").dataset.view === "recipes") return;
    recipesKey = key;
    const edit = (rec, asNew) => {
      S.recipeEdit = { recipe_id: asNew ? "" : rec.recipe_id, name: asNew ? "" : rec.name, version: !asNew && rec.status === "DRAFT" ? rec.version : null,
        from: rec.ref, base: JSON.parse(JSON.stringify(rec.body)), body: JSON.parse(JSON.stringify(rec.body)), checked: false, issues: null, kept: {} };
      U.refresh();
    };
    const reasonBox = (id, ph) => h("input", { type: "text", id, maxlength: "300", placeholder: ph, "aria-label": "Reason (recorded)" });
    const list = table(["Recipe", "Status", "Made", "In use since", ""], R.recipes.map(r => {
      const st = STATUS[r.status] || [r.status, ""];
      return h("tr", null,
        h("td", null, h("strong", null, r.name || r.ref), h("div", { class: "muted mono" }, `${r.ref} · ${r.provenance}`)), h("td", null, pill(st[0], st[1]),
          r.issues && r.issues.length && r.status === "DRAFT" ? h("div", { class: "error" }, `${r.issues.length} problem(s) found by the check`) : null),
        h("td", null, `${fmtTime(r.created_utc)} by ${r.created_by}`), h("td", null, r.activated_utc ? `${fmtTime(r.activated_utc)} by ${r.activated_by}` : "—"),
        h("td", null,
          pbtn(r.status === "DRAFT" ? "Continue changing" : "Change settings", () => edit(r, false), "ghost", "recipe_edit"), " ",
          pbtn("Copy to a new recipe", () => edit(r, true), "ghost", "recipe_edit"), " ",
          r.status === "DRAFT" ? pbtn("Check settings", () => act("POST", `/api/recipes/${r.recipe_id}/${r.version}/validate`, undefined, "checked"), "", "recipe_edit") : null, " ",
          r.status === "VALIDATED" ? useBox(r) : null));
    }));
    function useBox(r) {
      if (!can("recipe_activate")) return h("span", { class: "muted" }, "An administrator can use these settings.");
      const why = reasonBox("use-" + r.recipe_id + "-" + r.version, "why (recorded)");
      return h("details", { class: "inline" }, h("summary", null, "Use these settings"),
        h("p", { class: "muted" }, effect()), h("label", null, "Why are you changing the settings? (recorded)", why),
        pbtn("Use these settings", () => { if (!why.value.trim()) { toast("Write why first; it is recorded.", true); return; }
          act("POST", `/api/recipes/${r.recipe_id}/${r.version}/activate`, { reason: why.value.trim() }, `${r.ref} in use`); }, "", "recipe_activate"));
    }
    const rb = reasonBox("rollback-reason", "why (recorded)");
    const kids = [
      h("div", { class: "card" }, h("h2", null, "Recipes"),
        h("p", null, "In use: ", h("strong", null, R.active || "none"), ". ", effect()),
        list,
        can("recipe_activate") ? h("details", { class: "inline" }, h("summary", null, "Restore previous settings"),
          h("p", { class: "muted" }, "Goes back to the recipe that was in use before this one."), h("label", null, "Why (recorded)", rb),
          pbtn("Restore previous settings", () => { if (!rb.value.trim()) { toast("Write why first; it is recorded.", true); return; }
            act("POST", "/api/recipes/rollback", { reason: rb.value.trim() }, "previous settings restored"); }, "ghost", "recipe_activate"))
          : h("p", { class: "muted" }, "Operators change and check settings; administrators put them in use or restore the previous ones."))];
    if (E) kids.push(editor(R, E));
    kids.push(h("details", { class: "card" }, h("summary", null, h("strong", null, "History of settings in use")),
      table(["Time", "What", "Recipe", "By", "Why"], R.activations.slice().reverse().map(a => h("tr", null,
        h("td", null, fmtTime(a.utc)), h("td", null, a.action), h("td", { class: "mono" }, a.recipe + (a.previous ? " (was " + a.previous + ")" : "")), h("td", null, a.actor || ""), h("td", null, a.reason || ""))))));
    const el = document.getElementById("v-plant"), drawn = el.dataset.drawn;
    mount("v-plant", kids);
    if (el.dataset.drawn === drawn) recipesKey = null;     // not drawn now (a button was held): draw at the next refresh
  }
  function effect() {
    return "New settings apply from each process's next step: watering from its next sector, refill from its next refill, acid from its next dose. Anything running now finishes with the settings it started with.";
  }
  // the sections the user opened stay open when the page is redrawn
  function keepOpen(g, d) {
    d.addEventListener("toggle", () => {
      const o = new Set(S.recipeOpen || ["main", "refill"]);
      d.open ? o.add(g) : o.delete(g);
      S.recipeOpen = [...o];
    });
    return d;
  }
  /** The editor: 1 choose how to refill, 2 set amounts or targets (only what the chosen ways use; timing and
   * tolerances under Advanced settings), 3 check the changes, 4 use them (administrators). Every field has a
   * label, unit, allowed range and example; a problem shows beside its field and the typed value stays. */
  function editor(R, E) {
    const rules = {};
    R.form.forEach(f => { rules[f.path] = f; });
    const byGroup = {};
    R.form.forEach(f => { const g = f.path.split(".")[0]; (byGroup[g] = byGroup[g] || []).push(f); });
    const issueAt = {};
    (E.issues || []).forEach(i => { (issueAt[i.path] = issueAt[i.path] || []).push(i.message); });
    const shown = new Set();
    const touched = () => { E.checked = false; };
    const fid = p => "rf-" + p.replace(/\./g, "-");
    const field = f => {
      shown.add(f.path);
      const cur = getp(E.body, f.path), id = fid(f.path), err = issueAt[f.path], help = id + "-help";
      const range = f.options && f.options.length ? "" : `${f.integer ? "a whole number, " : ""}allowed ${f.min_exclusive ? "more than" : "from"} ${f.min ?? "—"}${f.max != null ? " to " + W.n0(f.max) : ""}${f.unit ? " " + f.unit : ""}`;
      const input = h("input", { type: "number", step: f.integer ? "1" : "any", inputmode: f.integer ? "numeric" : "decimal", id, value: cur == null ? "" : String(cur), min: f.min, max: f.max, class: "w-num",
        "aria-describedby": help + (err ? " " + id + "-err" : ""), "aria-invalid": err ? "true" : null, placeholder: f.example || null });
      input.addEventListener("input", () => { setp(E.body, f.path, input.value === "" ? null : Number(input.value)); touched(); });
      return h("div", { class: "field" + (err ? " invalid" : ""), "data-path": f.path },
        h("label", { for: id }, f.label), h("div", { class: "row" }, input, h("span", null, f.unit || "")),
        h("div", { class: "muted small", id: help }, [range, f.example ? `example ${f.example}` : "", f.help].filter(Boolean).join(" · ")),
        err ? h("div", { class: "error", role: "alert", id: id + "-err" }, err.join("; ")) : null);
    };
    const choice = f => {
      shown.add(f.path);
      const cur = getp(E.body, f.path);
      const err = issueAt[f.path];
      return h("fieldset", { class: "field" + (err ? " invalid" : ""), "data-path": f.path }, h("legend", null, f.label),
        f.options.map((o, i) => {
          const id = fid(f.path) + "-" + o;
          const r = h("input", { type: "radio", name: fid(f.path), id, value: o, checked: cur === o });
          // redrawn at once (a refresh waits while focus is in a field), then focus goes back to the same choice
          r.addEventListener("change", async () => { if (r.checked) { switchMode(f.path, o); touched(); await recipes(); const x = document.getElementById(id); if (x) x.focus(); } });
          return h("div", { class: "radio" }, r, h("label", { for: id }, f.option_labels[i] || o));
        }), err ? h("div", { class: "error", role: "alert" }, err.join("; ")) : null);
    };
    // fields a mode does not use are emptied when the mode changes, and come back if the mode is chosen again
    function switchMode(path, value) {
      setp(E.body, path, value);
      R.form.filter(f => f.required && f.required.startsWith(path + "=")).forEach(f => {
        const keep = E.kept || (E.kept = {});
        if (applies(f, E.body)) { if (getp(E.body, f.path) == null && keep[f.path] != null) setp(E.body, f.path, keep[f.path]); }
        else if (getp(E.body, f.path) != null) { keep[f.path] = getp(E.body, f.path); setp(E.body, f.path, null); }
      });
    }
    const seq = (E.body.irrigation && E.body.irrigation.sequence) || [];
    const seqRows = seq.map((st, i) => {
      const sid = `seq-${i}-sector`, did = `seq-${i}-time`;
      const s = h("select", { id: sid }, R.sectors.map(x => h("option", { value: x, selected: st.sector === x }, W.sectorName(x))));
      const d = h("input", { type: "number", min: 1, max: 86400, step: 1, value: String(st.duration_s), class: "w-num", id: did });
      s.addEventListener("change", () => { st.sector = s.value; touched(); });
      d.addEventListener("input", () => { st.duration_s = Number(d.value); touched(); });
      const err = (issueAt[`irrigation.sequence.${i}.duration_s`] || []).concat(issueAt[`irrigation.sequence.${i}.sector`] || []);
      return h("tr", null, h("td", null, String(i + 1)), h("td", null, h("label", { for: sid, class: "sr-only" }, `Step ${i + 1}: sector`), s),
        h("td", null, h("label", { for: did, class: "sr-only" }, `Step ${i + 1}: watering time in seconds`), d, " s", err.length ? h("div", { class: "error", role: "alert" }, err.join("; ")) : null),
        h("td", null, h("button", { class: "small ghost", onclick: () => { seq.splice(i, 1); touched(); U.refresh(); } }, `Remove step ${i + 1}`)));
    });
    const sectionOf = g => {
      const fs = (byGroup[g] || []).filter(f => !MODES.includes(f.path) && applies(f, E.body));
      const basic = fs.filter(f => !f.advanced), adv = fs.filter(f => f.advanced);
      const choices = (byGroup[g] || []).filter(f => f.options && f.options.length && !MODES.includes(f.path));
      const groupErr = Object.keys(issueAt).filter(p => p === g || (p.startsWith(g + ".") && !rules[p] && !p.startsWith("irrigation.sequence."))).flatMap(p => issueAt[p]);
      const hyst = g === "main" ? hysteresisExample(E.body) : null;
      return keepOpen(g, h("details", { class: "section", open: (S.recipeOpen || ["main", "refill"]).includes(g) || Object.keys(issueAt).some(p => p.startsWith(g)) },
        h("summary", null, SECTIONS.find(x => x[0] === g)[1]),
        groupErr.length ? h("div", { class: "error", role: "alert" }, groupErr.join("; ")) : null,
        choices.map(choice), basic.filter(f => !(f.options && f.options.length)).map(field), hyst,
        g === "irrigation" ? [h("h3", null, "Watering sequence (one automatic round)"), table(["#", "Sector", "Watering time", ""], seqRows),
          h("button", { class: "small ghost", onclick: () => { seq.push({ sector: R.sectors[0], duration_s: 300 }); touched(); U.refresh(); } }, "Add a sector step")] : null,
        adv.length ? h("details", { class: "advanced" }, h("summary", null, `Advanced settings: timing and tolerances (${adv.length})`), adv.filter(f => !(f.options && f.options.length)).map(field)) : null));
    };
    const head = p => issueAt[p] ? h("div", { class: "error", role: "alert", id: "rf-" + p + "-err" }, issueAt[p].join("; ")) : null;
    const rid = h("input", { id: "rf-recipe-id", value: E.recipe_id, placeholder: "e.g. summer-2027", disabled: E.version != null || !!(E.recipe_id && E.from && E.from.startsWith(E.recipe_id + "@")),
      "aria-invalid": issueAt.recipe_id ? "true" : null, "aria-describedby": issueAt.recipe_id ? "rf-recipe_id-err" : null });
    const nm = h("input", { id: "rf-recipe-name", value: E.name, placeholder: "e.g. Summer tomatoes",
      "aria-invalid": issueAt.name ? "true" : null, "aria-describedby": issueAt.name ? "rf-name-err" : null });
    rid.addEventListener("input", () => { E.recipe_id = rid.value; touched(); });
    nm.addEventListener("input", () => { E.name = nm.value; touched(); });
    /** Check settings = save the draft (types) and run the server's full check; the typed values stay either way. */
    const check = async () => {
      if (S.stale) { toast("Connection stale: nothing was sent", true); return; }
      let r;
      try { r = await api("POST", "/api/recipes/draft", { recipe_id: E.recipe_id, name: E.name || E.recipe_id, body: E.body, version: E.version }); }
      catch (e) {
        // R33-02: a value of the wrong kind (1.5 doses, a bad recipe id) is refused before the draft is saved; its
        // problems go beside their fields like the check's own, and the typed values stay
        if (e.status === 422 && e.detail && Array.isArray(e.detail.fields)) {
          E.issues = e.detail.fields.map(x => ({ path: x.path, message: x.message })); E.checked = false; recipesKey = null;
          toast(`${E.issues.length} problem(s) to fix; they are shown beside the fields`, true);
          U.refresh(); return;
        }
        const raw = String(e.message || e); toast(W.refusal(raw), true, raw); return;
      }
      E.version = r.version; E.recipe_id = r.recipe_id;
      const v = await act("POST", `/api/recipes/${r.recipe_id}/${r.version}/validate`, undefined, null);
      if (!v) return;
      E.issues = v.issues || []; E.checked = v.status === "VALIDATED"; E.ref = v.ref;
      toast(E.checked ? `${v.ref}: the settings passed the check` : `${v.ref}: ${E.issues.length} problem(s) to fix; they are shown beside the fields`, !E.checked);
      U.refresh();
    };
    const changes = diffs(E.base, E.body, rules);
    // every problem is shown once: beside its field, in its section, in its watering step, or (a field that is not
    // shown for the chosen ways) in the list under the editor
    const inGroup = p => SECTIONS.some(([g]) => byGroup[g] && (p === g || (p.startsWith(g + ".") && !rules[p] && !p.startsWith("irrigation.sequence."))));
    const hidden = Object.keys(issueAt).filter(p => !shown.has(p) && !MODES.includes(p) && p !== "recipe_id" && p !== "name" && !inGroup(p) && !/^irrigation\.sequence\.\d+\.(duration_s|sector)$/.test(p));
    const modeFields = MODES.map(p => rules[p]).filter(Boolean);
    const body = [
      h("h2", null, E.version != null ? `Changing the draft ${E.recipe_id} (version ${E.version})` : `New settings, starting from ${E.from}`),
      h("div", { class: "row" }, h("div", { class: "field" + (issueAt.recipe_id ? " invalid" : ""), "data-path": "recipe_id" }, h("label", { for: "rf-recipe-id" }, "Recipe id (a-z, 0-9, - or _)", rid), head("recipe_id")),
        h("div", { class: "field" + (issueAt.name ? " invalid" : ""), "data-path": "name" }, h("label", { for: "rf-recipe-name" }, "Name", nm), head("name"))),
      h("h3", null, "1. Choose how to refill"), h("div", { class: "grid" }, modeFields.map(choice)),
      h("h3", null, "2. Set amounts and targets"),
      h("p", { class: "muted" }, "Only the settings the chosen ways use are shown. Every field gives its unit and allowed range. Nothing here is a production value until you set it."),
      SECTIONS.filter(([g]) => byGroup[g]).map(([g]) => sectionOf(g))];
    // the field rendering above registered what is shown; problems on fields not shown are listed here
    return h("div", { class: "card editor" }, body,
      hidden.length ? h("div", { class: "error", role: "alert" }, "Also to fix: ", hidden.map(p => `${rules[p] ? rules[p].label : p}: ${issueAt[p].join("; ")}`).join(" · ")) : null,
      h("h3", null, "3. Check the changes"),
      changes.length ? h("div", null, h("p", null, `Changes compared with ${E.from}:`), h("ul", null, changes.map(c => h("li", null, c)))) : h("p", { class: "muted" }, `No changes compared with ${E.from} yet.`),
      h("p", { class: "muted" }, effect()),
      h("div", { class: "row" }, pbtn("Check settings", check, "", "recipe_edit"), h("button", { class: "small ghost", onclick: () => { S.recipeEdit = null; U.refresh(); } }, "Discard changes"),
        E.checked ? h("span", { class: "done" }, `✓ ${E.ref} passed the check`) : E.issues && E.issues.length ? h("span", { class: "error" }, `${E.issues.length} problem(s): see the fields marked in red`) : null),
      h("h3", null, "4. Use these settings"),
      E.checked ? (can("recipe_activate") ? h("p", null, `${E.ref} is checked. Use it from the list above: "Use these settings".`)
        : h("p", { class: "muted" }, `${E.ref} is checked. An administrator puts it in use from the list above.`))
        : h("p", { class: "muted" }, "Available once the settings have passed the check."));
  }
  function hysteresisExample(b) {
    const m = b.main || {}, t = m.refill_trigger_L, r = m.rearm_hysteresis_L;
    if (t == null || r == null) return null;
    return h("p", { class: "example muted" }, `Example with these values: if refill starts below ${W.n0(t)} litres and the required rise is ${W.n0(r)} litres, ` +
      `the tank must reach at least ${W.n0(t + r)} litres before a later drop can trigger another refill. Reaching ${W.n0(t + r)} litres makes another refill possible; it does not start one.`);
  }
  function diffs(a, b, rules) {
    const out = [];
    Object.values(rules).forEach(f => {
      const x = getp(a, f.path), y = getp(b, f.path);
      if (x === y || (x == null && y == null)) return;
      const show = v => v == null ? "not set" : f.options && f.options.length ? (f.option_labels[f.options.indexOf(v)] || v) : `${W.n0(v)}${f.unit ? " " + f.unit : ""}`;
      out.push(`${f.label}: ${show(x)} → ${show(y)}`);
    });
    const sa = JSON.stringify(((a.irrigation || {}).sequence || [])), sb = JSON.stringify(((b.irrigation || {}).sequence || []));
    if (sa !== sb) out.push(`Watering sequence: ${((b.irrigation || {}).sequence || []).map(s => `${W.sectorName(s.sector)} ${s.duration_s} s`).join(", ") || "empty"}`);
    return out;
  }

  // ---------------------------------------------------------------- navigation and registration
  let navBuilt = false;
  async function buildNav() {
    if (navBuilt) return;
    navBuilt = true;
    let P;
    try { P = await api("GET", "/api/plant"); } catch (e) { navBuilt = false; return; }
    const box = document.getElementById("nav-inst");
    const links = PAGES.slice(0, 1).concat(P.layout.sectors.map(s => ["sector-" + s.sector_id, W.sectorName(s.sector_id)]), PAGES.slice(1), [["drainage", "Drainage return"]],
      P.read_only ? [] : [["recipes", "Recipes"]]);
    box.replaceChildren(...links.map(([k, t]) => h("a", { href: "#" + k }, t)));
    box.classList.remove("hidden");
    document.querySelectorAll('#nav a[href="#control"]').forEach(a => a.classList.add("hidden"));
    U.highlightNav();
  }
  // The pages exist for the installation topology and, read-only, for hardware profiles; not for the demo twin.
  const only = fn => (...a) => (U.isInst() || U.isReal()) ? fn(...a)
    : mount("v-plant", h("div", { class: "card" }, "This page does not exist in this profile."));
  VIEWS.nutrients = only(nutrients); VIEWS["main-tank"] = only(mainTank); VIEWS["main-pumps"] = only(mainPumps);
  VIEWS.drainage = only(drainage); VIEWS.recipes = only(recipes); VIEWS.diagram = only(fullDiagram);
  function resolve(view) { return view.startsWith("sector-") ? only(() => sector(view.slice(7))) : null; }
  window.AqPlant = { overview, hwDiagram, buildNav, resolve, problemCard, sentence, statePill, signalName: W.signal };
})();
