/* Minimal, dependency-free SVG time-series chart (locally packaged; no CDN).
 * X axis is real timestamps. Lines break at invalid samples and where the server flags a gap
 * (missing cycles relative to the recorded cadence, run change, origin change); gaps are shaded. Digital series are steps in a lower band.
 * A series with `suspect: true` (records whose UTC stamps an administrator quarantined) is drawn dashed grey with hollow ring
 * markers on every point, so it can never be read as a trusted trace. */
"use strict";
window.AqChart = (function () {
  const NS = "http://www.w3.org/2000/svg";
  const COLORS = ["#2d6fb5", "#c4532b", "#2f8f5b", "#8a4fb3", "#b38a1e", "#2a9aa0", "#b33b74", "#5c6b2f"];
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function fmt(tz) {
    return new Intl.DateTimeFormat("sv-SE", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function render(svg, opts) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const W = svg.clientWidth || 800, H = svg.clientHeight || 280;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const L = 56, R = 12, T = 10, B = 34;
    const analog = opts.series.filter(s => !s.digital), digital = opts.series.filter(s => s.digital);
    const bandH = digital.length ? Math.min(18 * digital.length + 8, 0.4 * (H - T - B)) : 0;
    const aTop = T, aBot = H - B - bandH - (bandH ? 6 : 0);
    const t0 = opts.tStart, t1 = opts.tEnd;
    const x = t => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0);
    let vmin = Infinity, vmax = -Infinity;
    analog.forEach(s => s.points.forEach(p => { if (p[1] != null && p[2]) { vmin = Math.min(vmin, p[1]); vmax = Math.max(vmax, p[1]); } }));
    if (!isFinite(vmin)) { vmin = 0; vmax = 1; }
    if (vmax - vmin < 1e-9) { vmax += 1; vmin -= 1; }
    const pad = (vmax - vmin) * 0.05; vmin -= pad; vmax += pad;
    const y = v => aBot - (aBot - aTop) * (v - vmin) / (vmax - vmin);
    // grid + axes
    for (let i = 0; i <= 4; i++) {
      const v = vmin + (vmax - vmin) * i / 4, yy = y(v);
      el("line", { x1: L, x2: W - R, y1: yy, y2: yy, class: "grid" }, svg);
      const tx = el("text", { x: L - 4, y: yy + 4, "text-anchor": "end" }, svg); tx.textContent = v.toFixed(Math.abs(vmax - vmin) < 10 ? 2 : 0);
    }
    const f = fmt(opts.timezone || "Europe/Stockholm");
    for (let i = 0; i <= 5; i++) {
      const t = t0 + (t1 - t0) * i / 5, xx = x(t);
      el("line", { x1: xx, x2: xx, y1: T, y2: H - B, class: "grid" }, svg);
      const tx = el("text", { x: xx, y: H - B + 14, "text-anchor": "middle" }, svg); tx.textContent = f.format(new Date(t));
    }
    const tzn = new Intl.DateTimeFormat("en-GB", { timeZone: opts.timezone, timeZoneName: "short" }).formatToParts(new Date(t1)).find(p => p.type === "timeZoneName");
    const cap = el("text", { x: W - R, y: H - 4, "text-anchor": "end", class: "muted" }, svg);
    const anySuspect = opts.series.some(s => s.suspect && s.points.length);
    cap.textContent = `${opts.timezone} (${tzn ? tzn.value : ""}) — gaps shaded; lines never bridge missing/invalid data` +
      (anySuspect ? "; dashed grey with rings = SUSPECT timestamps (quarantined)" : "");
    el("line", { x1: L, x2: L, y1: T, y2: H - B, class: "axis" }, svg);
    el("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }, svg);
    // series
    const all = analog.concat(digital);
    all.forEach((s, idx) => {
      const color = s.suspect ? "#7a7a7a" : (s.color || COLORS[idx % COLORS.length]);
      s.color = color;
      const pts = s.points;
      let d = "", open = false, gapStart = null;
      const di = digital.indexOf(s);
      const yd = v => { const laneH = bandH / Math.max(1, digital.length); const base = H - B - di * laneH - 4; return base - (v ? laneH - 8 : 0); };
      for (let i = 0; i < pts.length; i++) {
        const [t, v, ok] = pts[i];
        const brokenByTime = i > 0 && !!pts[i][3];
        if (!ok || v == null || brokenByTime) {
          if (gapStart == null) gapStart = i > 0 ? pts[i - 1][0] : t;
          open = false;
          if (!ok || v == null) continue;
        }
        if (gapStart != null) {
          el("rect", { x: x(gapStart), y: T, width: Math.max(1, x(t) - x(gapStart)), height: H - B - T, class: "gap" }, svg);
          gapStart = null;
        }
        const yy = s.digital ? yd(v) : y(v);
        if (s.suspect) el("circle", { cx: x(t).toFixed(1), cy: yy.toFixed(1), r: 2.6, class: "suspect-pt", stroke: color }, svg);
        if (!open) { d += `M${x(t).toFixed(1)},${yy.toFixed(1)}`; open = true; }
        else if (s.digital) { d += `H${x(t).toFixed(1)}V${yy.toFixed(1)}`; }
        else { d += `L${x(t).toFixed(1)},${yy.toFixed(1)}`; }
      }
      // A suspect series is the other half of a split trace: its shading is drawn by the trusted half.
      if (gapStart != null && !s.suspect) el("rect", { x: x(gapStart), y: T, width: Math.max(1, x(t1) - x(gapStart)), height: H - B - T, class: "gap" }, svg);
      const path = el("path", { d, class: "series" + (s.suspect ? " suspect" : ""), stroke: color }, svg);
      if (s.dashed || s.suspect) path.setAttribute("stroke-dasharray", "5 3");
    });
    (opts.events || []).forEach(ev => {
      if (ev[0] < t0 || ev[0] > t1) return;
      const l = el("line", { x1: x(ev[0]), x2: x(ev[0]), y1: T, y2: H - B, class: "ev" }, svg);
      const ti = el("title", {}, l); ti.textContent = ev[1];
    });
    return all.filter(s => !s.suspect || s.points.length).map(s => ({ name: s.name, color: s.color, suspect: !!s.suspect }));
  }
  return { render };
})();
