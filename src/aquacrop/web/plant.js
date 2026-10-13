/* AquaCrop installation pages: clickable installation diagram, process pages and the recipe editor.
 * Loaded after app.js and built on its helpers (window.AqUI). Everything shown here is what the
 * controllers observe (value, unit, quality, age, origin), never the twin's internal truth. All text is
 * inserted with textContent. Process control exists only in simulation; on a hardware profile the same
 * pages are read-only observation, every control is replaced by the reason it is unavailable, and every
 * signal stays UNBOUND (not measured) until a physical mapping is approved (ADR-0009 addendum). */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, act, pill, qBadge, oBadge, num, fmtTime, can, mount, toast, S, VIEWS } = U;
  const NS = "http://www.w3.org/2000/svg";
  const USABLE = ["GOOD", "OVERRIDDEN"];
  const PAGES = [["nutrients", "Nutrients & acid"], ["main-tank", "Main tank"], ["main-pumps", "Main pumps"]];

  // ---------------------------------------------------------------- signal reading
  function sig(P, name) {
    const s = P.signals[name];
    if (!s) return { bound: false, txt: "not in layout", ok: false };
    if (s.binding === "UNBOUND") return { bound: false, txt: "not measured", ok: false, why: "no point carries this signal" };
    if (s.binding === "DRIVE_ADAPTER") {
      const a = s.adapter;
      return { bound: true, adapter: true, ok: a.requested != null, v: a.requested, txt: a.requested == null ? a.kind : (a.requested ? "ON" : "OFF"), kind: a.kind };
    }
    const m = s.sample;
    if (!m) return { bound: true, ok: false, q: "UNAVAILABLE", txt: "no sample", point: s.point, label: s.label };
    const ok = USABLE.includes(m.quality) && m.value != null;
    return { bound: true, ok, v: m.value, unit: m.unit, q: m.quality, age: m.age_s, origin: m.origin, point: s.point, label: s.label,
      reason: m.reason, override: m.override, txt: m.value == null ? "—" : (typeof m.value === "boolean" ? (m.value ? "1" : "0") : "") };
  }
  function fmtVal(r, d, words) {
    if (!r.bound) return r.txt;
    if (r.adapter) return `${r.kind} adapter: ${r.txt}`;
    if (r.v == null) return r.txt;
    if (typeof r.v === "boolean") return words ? (r.v ? words[0] : words[1]) : (r.v ? "ON" : "OFF");
    return num(r.v, d == null ? 1 : d) + (r.unit ? " " + r.unit : "");
  }
  /** One observation for tables: value with unit, quality, age, and the point it comes from. */
  function obs(P, name, d, words) {
    const r = sig(P, name);
    if (!r.bound) return h("span", { class: "muted", title: r.why || "" }, r.txt);
    if (r.adapter) return h("span", null, pill(fmtVal(r, d, words)), " ", h("span", { class: "muted" }, "(drive adapter, not a relay point)"));
    return h("span", { title: `${r.point}${r.label ? " — " + r.label : ""}` }, h("strong", null, fmtVal(r, d, words)), " ",
      qBadge(r.q || "UNAVAILABLE"), r.origin ? [" ", oBadge(r.origin)] : null, r.age != null ? h("span", { class: "muted" }, ` ${r.age.toFixed(1)} s`) : null,
      r.override ? h("span", { class: "muted" }, " override") : null, r.reason && !r.ok ? h("span", { class: "muted" }, " " + r.reason) : null);
  }
  function kvRows(rows) {
    return h("dl", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]));
  }
  /** Requested (command readback or adapter) versus observed (feedback). */
  function reqObs(P, cmd, fb, words) {
    return h("span", null, "requested ", obs(P, cmd, 0, ["ON", "OFF"]), fb ? [" · observed ", obs(P, fb, 0, words)] : null);
  }

  // ---------------------------------------------------------------- the installation diagram
  /** opts.readOnly: a hardware profile (navigation only, no controls anywhere); opts.focus: the page whose parts
   * are highlighted (the others are dimmed). Clicking a part only navigates. */
  function diagram(P, opts) {
    const readOnly = !!(opts && opts.readOnly), focus = opts && opts.focus;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 1100 690"); svg.setAttribute("class", "schematic diagram");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Installation diagram: observed values as the controllers see them" + (readOnly ? " (read-only)" : ""));
    if (focus) svg.classList.add("compact");
    let parent = svg;
    function e(tag, a, txt) {
      const x = document.createElementNS(NS, tag);
      for (const k in a) if (a[k] != null) x.setAttribute(k, a[k]);
      if (txt != null) x.textContent = txt;
      parent.appendChild(x); return x;
    }
    function hot(page, label, draw) {
      const g = e("g", { class: "hot" + (focus ? (page === focus ? " focus" : " dim") : ""), tabindex: "0", role: "link", "aria-label": label });
      const prev = parent; parent = g; draw(); parent = prev;
      if (page) {
        g.addEventListener("click", () => { location.hash = page; });
        g.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); location.hash = page; } });
      }
      return g;
    }
    const v = n => sig(P, n);
    const flowing = n => { const r = v(n); return r.ok && typeof r.v === "number" && r.v > 0.5; };
    const pipe = (d, flowSig) => e("path", { d, class: "pipe" + (flowSig && flowing(flowSig) ? " flow" : "") });
    function short(r, d, words) { return !r.bound ? "not measured" : fmtVal(r, d, words) + (r.adapter || r.ok ? "" : " " + (r.q || "")); }
    function tank(x, y, w, hh, name, t, page) {
      const cap = P.layout.tanks_L[t], r = v(`tank.${t}.level`);
      hot(page, `${name}: ${short(r, 0)} of ${cap} L`, () => {
        if (r.ok) { const f = Math.max(0, Math.min(1, r.v / cap)); e("rect", { x, y: y + hh * (1 - f), width: w, height: hh * f, class: "water" }); }
        else e("rect", { x: x + 2, y: y + 2, width: w - 4, height: hh - 4, class: "unknown" });
        e("rect", { x, y, width: w, height: hh, class: "tank-wall hl" });
        e("text", { x: x + w / 2, y: y - 6, "text-anchor": "middle", class: "th" }, name);
        e("text", { x: x + w / 2, y: y + hh + 14, "text-anchor": "middle", class: "val" }, r.ok ? `${num(r.v, 0)} / ${cap} L` : `level ${short(r, 0)}`);
        if (r.ok) e("text", { x: x + w / 2, y: y + hh + 27, "text-anchor": "middle", class: "val muted-t" }, `${(100 * r.v / cap).toFixed(0)} % · ${r.q} ${r.age.toFixed(1)} s`);
      });
    }
    function reqClass(cmd) { const r = v(cmd); return !r.bound ? "equip-req unbound" : "equip-req " + (r.v ? "req-on" : "req-off"); }
    function valve(x, y, id, label, page, fb) {
      const cmd = `valve.${id}.cmd`, rc = v(cmd), rf = fb ? v(fb) : null;
      hot(page, `${label}: requested ${short(rc, 0, ["OPEN", "CLOSED"])}${rf ? ", open limit " + short(rf, 0, ["yes", "no"]) : ", no position feedback"}`, () => {
        const open = rf && rf.ok ? (rf.v ? 1 : 0.15) : null;
        e("polygon", { points: `${x - 14},${y - 9} ${x - 14},${y + 9} ${x},${y}`, class: open == null ? "equip-unk" : "equip-act", "fill-opacity": open == null ? null : open });
        e("polygon", { points: `${x + 14},${y - 9} ${x + 14},${y + 9} ${x},${y}`, class: open == null ? "equip-unk" : "equip-act", "fill-opacity": open == null ? null : open });
        e("rect", { x: x - 17, y: y - 12, width: 34, height: 24, class: reqClass(cmd) + " hl" });
        e("text", { x, y: y - 16, "text-anchor": "middle", class: "small" }, label);
        e("text", { x, y: y + 24, "text-anchor": "middle", class: "small muted-t" }, `req ${short(rc, 0, ["open", "closed"])}`);
        if (rf) e("text", { x, y: y + 36, "text-anchor": "middle", class: "small muted-t" }, short(rf, 0, ["open", "not open"]));
      });
    }
    function pump(x, y, id, label, page, cmd, fb, extra, words) {
      // words: what the feedback observes ([on, off]); main pumps observe their contactor, not the motor
      const rc = v(cmd), rf = fb ? v(fb) : null, w = words || ["run", "stop"];
      hot(page, `${label}: requested ${short(rc, 0)}${rf ? ", observed " + short(rf, 0, words || ["running", "stopped"]) : ""}`, () => {
        e("circle", { cx: x, cy: y, r: 16, class: rf && rf.ok ? "equip-act" : "equip-unk", "fill-opacity": rf && rf.ok ? (rf.v ? 1 : 0.15) : null });
        e("circle", { cx: x, cy: y, r: 20, class: reqClass(cmd) + " hl" });
        e("text", { x, y: y + 4, "text-anchor": "middle", class: "small" }, id);
        e("text", { x, y: y + 34, "text-anchor": "middle", class: "small muted-t" },
          `req ${short(rc, 0)}${rf ? " · " + short(rf, 0, w) : ""}${extra || ""}`);
      });
    }
    function flowText(x, y, n, anchor) {
      const r = v(n);
      e("text", { x, y, "text-anchor": anchor || "start", class: "val" }, r.bound ? `${fmtVal(r, 1)}${r.ok ? "" : " " + (r.q || "")}` : "flow not measured");
    }

    // mains header and stock fills
    e("text", { x: 20, y: 24, class: "th" }, "Mains water (external supply)");
    pipe("M20 40 H620", null);
    [["A", "Stock A", 60], ["B", "Stock B", 200], ["ACID", "Acid", 340]].forEach(([s, name, x]) => {
      const c = x + 45;
      pipe(`M${c} 40 V125`, null);
      valve(c, 72, `FILL_${s}`, `fill ${s}`, "#nutrients", null);
      tank(x, 125, 90, 100, name, s, "#nutrients");
      pipe(`M${c} 258 V420 H500`, `flow.TP_${s}`);
      valve(c, 285, `OUT_${s}`, `outlet ${s}`, "#nutrients", null);
      pump(c, 345, `TP_${s}`, `Transfer pump ${s}`, "#nutrients", `pump.TP_${s}.cmd`, `pump.TP_${s}.running`);
      flowText(c, 396, `flow.TP_${s}`, "middle");
    });
    e("text", { x: 20, y: 470, class: "small muted-t" }, "No agitator or air pump: mixing is unverified.");
    // main tank
    pipe("M620 40 V210", "flow.FILL_MAIN");
    valve(620, 120, "FILL_MAIN", "fill main", "#main-tank", null);
    flowText(632, 180, "flow.FILL_MAIN");
    tank(500, 210, 160, 240, "Main tank", "MAIN", "#main-tank");
    const ph = v("main.ph"), ec = v("main.ec");
    hot("#main-tank", `pH ${short(ph, 2)}, EC ${short(ec, 2)}`, () => {
      e("rect", { x: 505, y: 495, width: 150, height: 40, class: "box hl" });
      e("text", { x: 580, y: 511, "text-anchor": "middle", class: "small" }, `pH ${short(ph, 2)}`);
      e("text", { x: 580, y: 527, "text-anchor": "middle", class: "small" }, `EC ${short(ec, 2)}`);
    });
    e("text", { x: 505, y: 550, class: "small muted-t" }, "probe point proposed; not installed or verified");
    // main pumps on the shared drive
    const drv = P.layout.shared_drive, mps = P.layout.main_pumps;
    pipe("M660 410 H700", null);
    mps.forEach((mp, i) => {
      const y = 290 + i * 120, k = "K" + (i + 1);
      pipe(`M700 410 V${y} H740`, null);
      pump(760, y, mp, `Main pump ${mp} (contactor ${k})`, "#main-pumps", `contactor.${mp}.cmd`, `contactor.${mp}.closed`, "", [`${k} closed`, `${k} open`]);
      pipe(`M780 ${y} H830`, null);
    });
    pipe("M830 200 V440", null);
    const dr = v(`drive.${drv}.run`), drr = v(`drive.${drv}.running`), drf = v(`drive.${drv}.fault`);
    hot("#main-pumps", `Shared drive: run ${short(dr, 0)}, running ${short(drr, 0, ["yes", "no"])}, fault ${short(drf, 0, ["yes", "no"])}`, () => {
      e("rect", { x: 690, y: 472, width: 140, height: 46, class: "box hl" });
      e("text", { x: 760, y: 489, "text-anchor": "middle", class: "small" }, "Shared drive (VFD)");
      e("text", { x: 760, y: 505, "text-anchor": "middle", class: "small muted-t" }, `run ${dr.adapter ? dr.txt : short(dr, 0)} · ${short(drr, 0, ["running", "stopped"])}`);
      e("text", { x: 760, y: 534, "text-anchor": "middle", class: drf.ok && drf.v ? "small fault-mark" : "small muted-t" },
        drf.ok && drf.v ? "⚠ DRIVE FAULT" : `fault ${short(drf, 0, ["yes", "no"])}`);
    });
    // sectors
    P.layout.sectors.forEach((s, i) => {
      const y = 200 + i * 240, id = s.sector_id, page = "#sector-" + id;
      pipe(`M830 ${y} H866`, `flow.${id}`);
      valve(880, y, id, `valve ${id}`, page, `valve.${id}.open_limit`);
      pipe(`M894 ${y} H930`, `flow.${id}`);
      hot(page, `Sector ${id}: channels ${s.channels[0]} to ${s.channels[s.channels.length - 1]}`, () => {
        e("rect", { x: 930, y: y - 50, width: 160, height: 100, class: "box hl" });
        e("text", { x: 1010, y: y - 32, "text-anchor": "middle", class: "th" }, `Sector ${id.slice(1)}`);
        e("text", { x: 1010, y: y - 16, "text-anchor": "middle", class: "small" }, `channels ${s.channels[0]}–${s.channels[s.channels.length - 1]} · ${s.channels.length * P.layout.outlets_per_channel} outlets`);
        e("text", { x: 1010, y: y + 2, "text-anchor": "middle", class: "small" }, `flow ${short(v("flow." + id), 1)}`);
        e("text", { x: 1010, y: y + 18, "text-anchor": "middle", class: "small" }, `pressure ${short(v("pressure." + id), 2)}`);
        e("text", { x: 1010, y: y + 36, "text-anchor": "middle", class: "small muted-t" }, `AUTO pump ${s.auto_pump}`);
      });
      pipe(`M1090 ${y + 30} H1097`, null);
    });
    // drainage
    pipe("M1097 230 V600 H960", null);
    e("text", { x: 1000, y: 590, "text-anchor": "middle", class: "small muted-t" }, "gravity drain");
    tank(860, 560, 100, 80, "Sump", "SUMP", "#drainage");
    pipe("M860 620 H478 V440 H500", "flow.RETURN");
    P.layout.return_pumps.forEach((rp, i) => pump(770 - i * 100, 620, rp, `Return pump ${rp}`, "#drainage", `pump.${rp}.cmd`, `pump.${rp}.running`));
    flowText(490, 612, "flow.RETURN");
    const sc = v("safety.chain_ok");
    e("text", { x: 20, y: 680, class: sc.ok && !sc.v ? "fault-mark" : "small" }, `Safety chain: ${short(sc, 0, ["healthy", "OPEN"])}`);
    return svg;
  }
  function legend(P) {
    return h("p", { class: "muted" }, "Outline = requested state (solid ON or open, dashed OFF or closed, dotted = no point). Fill = observed feedback ",
      "(hatched = no feedback point). Values are what the controllers read: value, quality, age. Click a part to open its page. ",
      provenance(P) + " Layout: " + P.layout.provenance + ".");
  }

  // ---------------------------------------------------------------- processes
  function statePill(s) { return pill(s, s === "FAULT_LOCKOUT" ? "bad" : s === "AUTO" ? "ok" : s === "MANUAL" ? "warn" : ""); }
  function pbtn(label, onclick, cls, perm, title) {
    return h("button", { class: "cmd small " + (cls || ""), onclick, disabled: !can(perm || "process_control"), title: can(perm || "process_control") ? title || null : "needs the " + (perm || "process_control").replace("_", " ") + " permission" }, label);
  }
  const setState = (pid, target) => act("POST", `/api/processes/${pid}/state`, { target }, `${pid}: ${target} requested`);
  function manualForm(pid, pv, layout) {
    if (pv.state !== "MANUAL") return h("p", { class: "muted" }, "Request MANUAL to run one bounded operation. MANUAL uses the same interlocks as AUTO.");
    if (pv.manual_op) return h("p", null, "Manual operation running: ", h("span", { class: "mono" }, JSON.stringify(pv.manual_op.op || pv.manual_op)));
    const go = op => act("POST", `/api/processes/${pid}/manual`, { op }, "manual operation started");
    const inp = (ph, step) => h("input", { type: "number", min: 0, step: step || "any", placeholder: ph, class: "w-num" });
    const sel = opts => h("select", null, opts.map(o => h("option", { value: o }, o)));
    let kids;
    if (pid === "IRRIGATION") {
      const p = sel(layout.main_pumps), s = sel(layout.sectors.map(x => x.sector_id)), d = inp("seconds");
      kids = [h("label", null, "Pump", p), h("label", null, "Sector", s), h("label", null, "Duration (s)", d),
        pbtn("Run one step", () => go({ pump: p.value, sector: s.value, duration_s: Number(d.value) }))];
    } else if (pid === "REFILL") {
      const p = sel(["WATER", "A", "B"]), vol = inp("litres");
      kids = [h("label", null, "Path", p), h("label", null, "Volume (L)", vol), pbtn("Transfer", () => go({ path: p.value, volume_L: Number(vol.value) }))];
    } else if (pid === "ACID") {
      const vol = inp("litres");
      kids = [h("label", null, "Acid dose (L, at most the recipe step)", vol), pbtn("Dose once", () => go({ volume_L: Number(vol.value) }))];
    } else if (pid === "RETURN") {
      const p = sel(layout.return_pumps), d = inp("seconds");
      kids = [h("label", null, "Pump", p), h("label", null, "Duration (s)", d), pbtn("Run", () => go({ pump: p.value, duration_s: Number(d.value) }))];
    } else {
      const t = inp("litres");
      kids = [h("label", null, "Fill to level (L)", t), pbtn("Fill", () => go({ target_L: Number(t.value) }))];
    }
    return h("div", { class: "row" }, kids);
  }
  function procPanel(pid, PR, layout, extra) {
    if (!PR) return null;
    const pv = PR.processes[pid];
    if (!pv) return h("div", { class: "card" }, "Unknown process " + pid);
    const perms = Object.entries(pv.permissives || {});
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", null, pv.title), statePill(pv.state), h("span", null, pv.phase),
        h("span", { class: "muted" }, `phase entered at t = ${num(pv.phase_since_s, 1)} s · recipe ${pv.recipe || "none (snapshot taken at the next start)"}`)),
      pv.fault_reason ? h("p", { class: "error" }, "⚠ Lockout: ", h("strong", null, pv.fault_reason), pv.fault_detail ? " — " + pv.fault_detail : "",
        ". Acknowledge the alarm, clear the cause, then Reset; the process stays STOPPED until you request a mode.") : null,
      kvRows([pv.inhibit ? ["Waiting", pv.inhibit] : null, pv.next ? ["Next", pv.next] : null,
        ["Owns", pv.resources && pv.resources.length ? pv.resources.join(", ") : "nothing"],
        ["Outputs ON", pv.outputs_on.length ? pv.outputs_on.join(", ") : "none"],
        pv.last_result ? ["Last result", h("span", { class: "mono" }, summary(pv.last_result))] : null]),
      h("div", { class: "row" },
        pv.auto_capable === false ? null : pbtn("AUTO", () => setState(pid, "AUTO"), "", null, "start automatic operation (simulation)"),
        pbtn("MANUAL", () => setState(pid, "MANUAL"), "ghost"), pbtn("STOP", () => setState(pid, "STOPPED"), "danger"),
        pv.state === "FAULT_LOCKOUT" ? pbtn("Reset lockout", () => act("POST", `/api/processes/${pid}/reset`, undefined, "reset"), "ghost", "lockout_reset") : null),
      manualForm(pid, pv, layout),
      extra || null,
      perms.length ? h("details", null, h("summary", null, `Permissives (${perms.filter(([, x]) => !x.ok).length} failing)`),
        h("table", null, h("tbody", null, perms.map(([k, x]) => h("tr", null, h("td", null, k), h("td", null, x.ok ? pill("OK", "ok") : pill("FAIL", "bad")), h("td", { class: "muted" }, x.detail || "")))))) : null,
      h("details", null, h("summary", null, "Transition history"),
        h("table", null, h("thead", null, h("tr", null, ["sim s", "state", "phase", "reason", "actor", "recipe"].map(x => h("th", null, x)))),
          h("tbody", null, pv.history.slice().reverse().map(t => h("tr", null, h("td", null, num(t.sim_s, 1)), h("td", null, t.to_state), h("td", null, t.to_phase),
            h("td", null, t.reason), h("td", null, t.actor), h("td", { class: "mono" }, t.recipe || "")))))));
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
  function procTable(PR) {
    return table(["Process", "State", "Phase", "Fault / waiting", ""], PR.order.map(pid => {
      const p = PR.processes[pid];
      return h("tr", null, h("td", null, h("strong", null, p.title)), h("td", null, statePill(p.state)), h("td", null, p.phase),
        h("td", null, p.fault_reason ? h("span", { class: "error" }, "⚠ " + p.fault_reason) : h("span", { class: "muted" }, p.inhibit || p.next || "")),
        h("td", null, p.auto_capable === false ? null : pbtn("AUTO", () => setState(pid, "AUTO")), " ", pbtn("STOP", () => setState(pid, "STOPPED"), "danger")));
    }));
  }
  // ---------------------------------------------------------------- what each process page covers
  // procs: the processes it runs; trend: the signals charted; sigs: signals whose point alarms belong here;
  // targets: other alarm targets (flow plausibility keys).
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
  /** Open alarms with what to do next; acknowledging never resets, and a process lockout resets with its process. */
  function alarmsCard(P, def) {
    const list = def ? relevantAlarms(P, def) : P.alarms;
    const rows = list.map(o => {
      const proc = o.target.startsWith("process.");
      const todo = o.reset_required ? (proc ? "Acknowledge, clear the cause, then Reset lockout on the process; it stays STOPPED until a mode is requested."
        : "Acknowledge, clear the cause, then reset the alarm.") : (o.active ? "Acknowledge; it clears when the condition ends." : "Cleared; acknowledge to close it.");
      return h("tr", null, h("td", null, pill(o.severity, o.severity === "CRITICAL" ? "bad" : "warn")),
        h("td", null, h("strong", null, o.rule_id), h("div", { class: "mono" }, o.target)), h("td", null, fmtTime(o.first_seen_utc)),
        h("td", null, (o.active ? "ACTIVE" : "cleared") + " · " + (o.acknowledged ? "acknowledged" : "UNACK") + (o.reset_required ? " · RESET REQUIRED" : "")),
        h("td", null, o.message || "", h("div", { class: "muted" }, todo)),
        h("td", null, o.acknowledged ? null : pbtn("Ack", () => act("POST", "/api/alarms/ack", { rule_id: o.rule_id, target: o.target }, "acknowledged"), "", "alarm_ack"),
          !proc && o.reset_required && !o.active && o.acknowledged ? pbtn("Reset", () => act("POST", "/api/alarms/reset", { rule_id: o.rule_id, target: o.target }, "reset"), "ghost", "lockout_reset") : null));
    });
    return h("div", { class: "card" }, h("h2", null, `${def ? "Alarms for this page" : "Active alarms"} (${list.length})`),
      list.length ? table(["Severity", "Alarm", "Since", "State", "Message and next step", ""], rows) : h("p", { class: "muted" }, "None"),
      h("p", null, h("a", { href: "#alarms" }, "All alarms and events")));
  }

  // Last hour of this page's recorded signals (60 s snapshots); refetched at most every 15 s.
  const trendCache = {};
  function trendCard(P, names) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "chart chart-small");
    const leg = h("div", { class: "legend" });
    const card = h("div", { class: "card" }, h("h2", null, "Last hour"), svg, leg,
      h("p", { class: "muted" }, "Recorded snapshots of this page's signals (one per minute). ", h("a", { href: "#trends" }, "Trends and export")));
    const draw = async () => {
      const pts = names.map(n => [n, P.signals[n]]).filter(([, x]) => x && x.point);
      if (!pts.length) { leg.replaceChildren(h("span", { class: "muted" }, "Nothing measured for this page on this profile.")); return; }
      const key = pts.map(([, x]) => x.point).join(","), now = Date.now();
      let c = trendCache[key];
      if (!c || now - c.at > 15000) {
        const start = new Date(now - 3600000), end = new Date(now);
        const tr = await api("GET", `/api/trends?points=${encodeURIComponent(key)}&start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}&limit=500`);
        c = trendCache[key] = { at: now, tr, start: start.getTime(), end: end.getTime() };
      }
      const meta = pid => { const e = pts.find(([, x]) => x.point === pid); return { kind: e[1].kind === "analog" ? "AI" : "DI", label: e[0] }; };
      const series = U.trendSeries(c.tr, pts.map(([, x]) => x.point), meta);
      const out = window.AqChart.render(svg, { series, events: c.tr.events.map(e => [Date.parse(e[0]), e[1] + (e[2] ? " by " + e[2] : "")]), tStart: c.start, tEnd: c.end, timezone: S.tz });
      leg.replaceChildren(...out.map(l => h("span", { style: "color:" + l.color }, (l.suspect ? "◌ " : "■ ") + l.name)));
    };
    return [card, () => draw().catch(e => leg.replaceChildren(h("span", { class: "error" }, "Trend unavailable: " + (e.message || e))))];
  }

  function provenance(P) {
    return P.read_only ? `Observations: REAL where a signal is mapped; ${P.mapping}.`
      : `Observations: SIMULATED (twin, source ${S.status.twin ? S.status.twin.source : "?"}); ${P.mapping}.`;
  }
  /** The installation diagram with this page's parts highlighted; collapsible, and stays as the user left it. */
  function pageDiagram(P, def) {
    const d = h("details", { class: "card", open: S.pageDiagram !== false },
      h("summary", null, h("strong", null, "Installation"), h("span", { class: "muted" }, " — the parts of this page are highlighted")),
      h("div", { class: "diagram-wrap" }, diagram(P, { readOnly: P.read_only, focus: def.focus })),
      h("p", { class: "muted" }, provenance(P)));
    d.addEventListener("toggle", () => { S.pageDiagram = d.open; });
    return d;
  }
  /** On a hardware profile the controls are replaced by the reason they are not offered. */
  function noControl(P, what) {
    return h("div", { class: "card" }, h("h2", null, what || "Process control"),
      h("p", null, pill("NOT AVAILABLE", "warn"), ` This is a ${S.status.profile} profile: this release has no process controller on real equipment (ADR-0009), `,
        "so AUTO, MANUAL, STOP, manual operations, stock acknowledgements and recipes are not offered here."),
      h("p", { class: "muted" }, "Physical control needs an approved physical mapping, verified calibration, a commissioned profile and the owner's authorization. " + P.mapping + "."));
  }
  async function load() {
    const P = await api("GET", "/api/plant");
    const PR = P.read_only ? null : await api("GET", "/api/processes");
    return [P, PR];
  }
  /** Lay out a process page: its diagram, the page's own cards, its processes (or why there are none), its alarms
   * and its trend. The trend is drawn once the page is in the document. */
  async function page(P, PR, def, cards, panels) {
    const [tc, draw] = trendCard(P, def.trend);
    mount("v-plant", pageDiagram(P, def), cards, PR ? panels : noControl(P), h("div", { class: "grid" }, alarmsCard(P, def), tc));
    await draw();
  }
  const fmtv = (x, unit) => x == null ? "not set" : `${x}${unit ? " " + unit : ""}`;

  // ---------------------------------------------------------------- pages
  async function overview() {
    const [P, PR] = await load();
    mount("v-overview",
      h("div", { class: "card" }, h("h2", null, "Installation"), h("div", { class: "diagram-wrap" }, diagram(P, {})), legend(P)),
      h("div", { class: "grid" },
        h("div", { class: "card wide" }, h("h2", null, "Processes (simulation only)"),
          h("p", null, "Active recipe: ", h("strong", null, PR.recipe.active || "none"), PR.recipe.provenance ? " · " + PR.recipe.provenance : ""),
          procTable(PR),
          h("div", { class: "row" }, pbtn("Stop all processes", () => confirm("Stop every process?") && act("POST", "/api/processes/stop-all", { reason: "operator stop all" }, "stop all"), "danger"),
            h("span", { class: "muted" }, "Physical AUTO does not exist in this release."))),
        alarmsCard(P)));
  }
  async function hwDiagram() {
    const P = await api("GET", "/api/plant");
    return h("div", { class: "card" }, h("h2", null, "Installation (read-only)"), h("div", { class: "diagram-wrap" }, diagram(P, { readOnly: true })), legend(P),
      h("p", { class: "muted" }, "No process control exists on a hardware profile in this release. Each part opens its read-only page."));
  }
  function stockRows(P, PR) {
    return ["A", "B", "ACID"].map(s => {
      const r = PR ? PR.stocks[s] : null;
      return h("tr", null, h("td", null, h("strong", null, s === "ACID" ? "Acid" : "Stock " + s)), h("td", null, obs(P, `tank.${s}.level`, 0)),
        h("td", null, !r ? h("span", { class: "muted" }, "not tracked on this profile") : [pill(r.status, r.status === "PREPARED" ? "ok" : "warn"),
          h("div", { class: "muted" }, r.actor ? `${r.actor}, ${fmtTime(r.utc)}${r.level_at_ack_L != null ? ", covers up to " + num(r.level_at_ack_L, 0) + " L" : ""}${r.note ? ": " + r.note : ""}` : (r.reason || ""))]),
        h("td", null, reqObs(P, `pump.TP_${s}.cmd`, `pump.TP_${s}.running`, ["running", "stopped"]), h("div", null, "flow ", obs(P, `flow.TP_${s}`, 1))),
        h("td", null, "outlet ", obs(P, `valve.OUT_${s}.cmd`, 0, ["OPEN", "CLOSED"]), h("div", null, "fill ", obs(P, `valve.FILL_${s}.cmd`, 0, ["OPEN", "CLOSED"]))),
        h("td", null, !r ? null : [pbtn("Acknowledge prepared", () => { const note = prompt(`${s}: what was prepared? (recorded with your name and the current level)`); if (note != null) act("POST", `/api/stocks/${s}/acknowledge`, { note }, "acknowledged"); }),
          " ", pbtn("Invalidate", () => { const reason = prompt(`${s}: why is the preparation no longer valid?`); if (reason) act("POST", `/api/stocks/${s}/invalidate`, { reason }, "invalidated"); }, "ghost"),
          " ", h("button", { class: "cmd small ghost", disabled: !can("sim_command"), title: "simulation only: adds synthetic solute to the twin tank; it does not acknowledge anything",
            onclick: () => act("POST", `/api/twin/stock/${s}/prepare`, undefined, "twin stock prepared (synthetic)") }, "Twin: prepare")]));
    });
  }
  /** The active recipe's settings for one stock tab: pH for acid, EC and A:B (or fixed volumes) for A and B. */
  function chemSettings(rec, tab) {
    if (!rec) return h("p", { class: "muted" }, "No active recipe.");
    const sf = rec.stock_fill && rec.stock_fill[tab];
    const fill = sf ? [["Stock fill target", fmtv(sf.target_L, "L")], ["Automatic fill below", fmtv(sf.auto_trigger_L, "L")], ["Fill stops above", fmtv(sf.high_inhibit_L, "L")]] : [];
    if (tab === "ACID") {
      const a = rec.acid;
      return kvRows([["pH target (adjustable)", `${fmtv(a.ph_target)} ± ${fmtv(a.ph_tolerance)}`], ["Undershoot lockout below", a.ph_target == null || a.undershoot_margin == null ? "not set" : num(a.ph_target - a.undershoot_margin, 2) + " pH"],
        ["Dose step", fmtv(a.dose_step_L, "L")], ["Settling after a dose", fmtv(a.settle_s, "s")], ["Batch maximum", fmtv(a.max_batch_L, "L")], ["Day maximum", fmtv(a.max_day_L, "L")],
        ["Stop after", `${fmtv(a.max_no_response)} doses moving pH less than ${fmtv(a.min_response_ph)}`], ...fill]);
    }
    const r = rec.refill, ec = r.ec || {}, fx = r.fixed || {};
    const rows = [["Nutrient mode", fmtv(r.nutrient_mode)], ["Refill ends at", r.endpoint_mode === "FINAL_LEVEL" ? fmtv(r.final_level_L, "L") + " (final level)" : fmtv(r.endpoint_mode)]];
    if (r.nutrient_mode === "EC_TARGET") rows.push(["EC target (separate setting)", `${fmtv(ec.ec_target_mS_cm)} ± ${fmtv(ec.ec_tolerance_mS_cm, "mS/cm")}`], ["A:B ratio", fmtv(ec.ratio_a_to_b)],
      ["Step (A + B together)", fmtv(ec.step_total_L, "L")], [`Maximum ${tab} per batch`, fmtv(tab === "A" ? ec.max_a_L : ec.max_b_L, "L")]);
    else rows.push([`Fixed ${tab} per batch`, fmtv(tab === "A" ? fx.a_L : fx.b_L, "L")], ["EC must end between", `${fmtv(fx.ec_min_mS_cm)} and ${fmtv(fx.ec_max_mS_cm, "mS/cm")}`]);
    rows.push(["Kept in reserve", fmtv(r.stock_reserve_L, "L")], ...fill);
    return kvRows(rows);
  }
  async function nutrients() {
    const [P, PR] = await load();
    const tab = S.stockTab || "A";
    const rec = PR ? PR.active_recipe_body : null;
    const tabs = h("div", { class: "tabs", role: "tablist" }, [["A", "Stock A"], ["B", "Stock B"], ["ACID", "Acid"]].map(([k, t]) =>
      h("button", { class: "tab" + (k === tab ? " active" : ""), role: "tab", "aria-selected": k === tab ? "true" : "false",
        onclick: () => { S.stockTab = k; U.refresh(); } }, t)));
    const name = tab === "ACID" ? "Acid" : "Stock " + tab;
    const cards = [
      h("div", { class: "card" }, h("h2", null, "Nutrient stocks and acid"),
        table(["Tank", "Level", "Preparation", "Transfer pump", "Valves", ""], stockRows(P, PR)),
        h("p", { class: "muted" }, "Stocks are prepared by hand. Water filling never marks a stock prepared; a fill, a level rise above the acknowledged level or an invalidation revokes it. There are no stock low switches in the point allocation (the 32 added IO 8CH channels stay unassigned): the reserve comes from the level transmitter only.")),
      h("div", { class: "card" }, tabs,
        h("div", { class: "grid" },
          h("div", null, h("h3", null, `${name}: ${P.layout.tanks_L[tab]} L`), kvRows([["Level", obs(P, `tank.${tab}.level`, 0)],
            ["Transfer pump TP_" + tab, reqObs(P, `pump.TP_${tab}.cmd`, `pump.TP_${tab}.running`, ["running", "stopped"])], ["Transfer flow", obs(P, `flow.TP_${tab}`, 1)],
            ["Outlet valve", obs(P, `valve.OUT_${tab}.cmd`, 0, ["OPEN", "CLOSED"])], ["Water fill valve", obs(P, `valve.FILL_${tab}.cmd`, 0, ["OPEN", "CLOSED"])]])),
          h("div", null, h("h3", null, `Recipe settings (${PR ? PR.recipe.active || "no active recipe" : "no recipes on this profile"})`), PR ? chemSettings(rec, tab) : h("p", { class: "muted" }, "Recipes exist only in simulation in this release."),
            PR ? h("p", null, h("a", { href: "#recipes" }, "Edit recipe")) : null))),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "Main tank chemistry"), kvRows([["pH", obs(P, "main.ph", 2)], ["EC", obs(P, "main.ec", 2)],
          ["Temperature", obs(P, "main.temperature", 1)]]),
          h("p", { class: "muted" }, "Probe point proposed, not installed or verified. In simulation the values come from a synthetic fixture (synthetic-carbonate-ec-v1), not a chemical model. No temperature compensation.")),
        PR ? refillBatchCard(PR) : null)];
    const ac = PR && PR.processes.ACID;
    const panels = PR ? [tab === "ACID" ? procPanel("ACID", PR, P.layout, h("div", null, kvRows([["Doses without response", String(ac.no_response_count)], ["Acid in the last 24 h", num(ac.day_total_L, 2) + " L"],
      ac.dose ? ["Dose", `${ac.dose.state}: ${num(ac.dose.delivered_L, 2)} of ${num(ac.dose.target_L, 2)} L`] : null]),
      (ac.batches || []).length ? h("details", null, h("summary", null, "Acid batches"), table(["Batch"], ac.batches.slice().reverse().map(b => h("tr", null, h("td", { class: "mono" }, summary(b)))))) : null,
      h("p", { class: "muted" }, "Acid lowers pH only; no base dosing exists. Undershoot locks the process out."))) : procPanel("REFILL", PR, P.layout),
      procPanel("STOCK_FILL_" + tab, PR, P.layout)] : null;
    await page(P, PR, PAGE_DEF.nutrients, cards, panels);
  }
  function refillBatchCard(PR) {
    const rf = PR.processes.REFILL, batch = rf.batch, paths = batch ? Object.values(rf.paths || {}) : [];
    return h("div", { class: "card" }, h("h2", null, "Refill batch"), batch ? [kvRows([["Batch", batch.batch_id], ["Modes", `${batch.endpoint_mode} / ${batch.nutrient_mode}`],
      ["Nutrients", batch.nutrients], ["EC steps", String((batch.ec_steps || []).length)], ["Started at", num(batch.start_level_L, 0) + " L"]]),
      table(["Path", "State", "Delivered", "Target", "Measured by"], paths.map(p => h("tr", null, h("td", null, p.name), h("td", null, p.state + (p.result ? " " + p.result : "")),
        h("td", null, num(p.delivered_L, 1) + " L"), h("td", null, p.target_L == null ? "—" : num(p.target_L, 1) + " L"), h("td", { class: "mono" }, p.measured_by))))]
      : h("p", { class: "muted" }, rf.armed ? "No batch running; armed." : "No batch running; waiting to re-arm."));
  }
  async function mainTank() {
    const [P, PR] = await load();
    const rec = PR ? PR.active_recipe_body : null, m = rec ? rec.main : null;
    const cards = h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, `Main tank (${P.layout.tanks_L.MAIN} L)`), kvRows([
        ["Level", obs(P, "tank.MAIN.level", 0)], ["High-high switch", obs(P, "tank.MAIN.high_high", 0, ["ACTIVE", "clear"])],
        ["Low-low switch", obs(P, "tank.MAIN.low_low", 0, ["ACTIVE", "clear"])], ["pH", obs(P, "main.ph", 2)], ["EC", obs(P, "main.ec", 2)],
        ["Mains fill valve", obs(P, "valve.FILL_MAIN.cmd", 0, ["OPEN", "CLOSED"])], ["Mains flow", obs(P, "flow.FILL_MAIN", 1)],
        ["Return flow in", obs(P, "flow.RETURN", 1)]])),
      PR ? h("div", { class: "card" }, h("h2", null, `Recipe levels (${PR.recipe.active || "no active recipe"})`), m ? kvRows([
        ["Dry-run guard", m.low_low_L + " L"], ["Refill trigger", m.refill_trigger_L + " L"], ["Re-arm above", (m.refill_trigger_L + m.rearm_hysteresis_L) + " L"],
        ["Refill stop", m.fill_stop_L + " L"], ["High inflow inhibit", m.high_inhibit_L + " L"], ["Minimum for dosing", m.min_dosing_L + " L"]]) : h("p", { class: "muted" }, "No active recipe."),
        h("p", null, h("a", { href: "#recipes" }, "Edit recipe"))) : null,
      h("div", { class: "card" }, h("h2", null, "Mixing"), PR ? kvRows(Object.entries(PR.mixing || {}).map(([k, x]) => [k, `${x.method}; last addition ${x.last_addition_sim_s == null ? "none" : num(x.last_addition_sim_s, 0) + " s"}`])) : null,
        h("p", { class: "muted" }, "There is no agitator or air pump. Settling is a simulated window; on a physical profile mixing readiness is NOT_VERIFIED and dosing cannot run.")),
      PR ? refillBatchCard(PR) : null);
    await page(P, PR, PAGE_DEF["main-tank"], cards, PR ? procPanel("REFILL", PR, P.layout) : null);
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
    const cards = h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, "Shared drive and pump selection"), kvRows([
        ["Drive run", obs(P, `drive.${drv}.run`, 0)], ["Drive running", obs(P, `drive.${drv}.running`, 0, ["running", "stopped"])],
        ["Drive fault", obs(P, `drive.${drv}.fault`, 0, ["FAULT", "none"])], ["Safety chain", obs(P, "safety.chain_ok", 0, ["healthy", "OPEN"])],
        ...P.layout.main_pumps.map((mp, i) => [`K${i + 1} (${mp})`, reqObs(P, `contactor.${mp}.cmd`, `contactor.${mp}.closed`, ["closed", "open"])]),
        ...driveIfaceRows(P, drv)]),
        h("p", { class: "muted" }, "One drive feeds one motor at a time; K1/K2 select it, never under load. The run command goes over the drive's Modbus interface (ADR-0015); the hardwired running and fault inputs stay the proof. The real drive's interface stays NOT_CONFIGURED until its register map passes CP-06 on the bench.")),
      ir ? h("div", { class: "card" }, h("h2", null, "Current step"), ir.sector ? kvRows([["Sector / pump", `${ir.sector} / ${ir.pump || "—"}`],
        ["Step", ir.duration_s ? `${num(ir.irrigated_s, 0)} of ${num(ir.duration_s, 0)} s with verified flow` : "—"],
        ["Delivered (measured)", num(ir.delivered_L_measured, 1) + " L"], ["Cycle", String(ir.cycle)], ir.stop_stage ? ["Stopping", ir.stop_stage] : null])
        : h("p", { class: "muted" }, ir.stop_stage ? "Stopping: " + ir.stop_stage : "No step running."),
        h("p", { class: "muted" }, "Changeover: stop the drive, verify stopped, close the old valve, select the next pump, open its valve, wait at least 1 s, start, verify running and flow. If the stop is not confirmed, the valve and the selection are held.")) : null);
    const panel = ir ? procPanel("IRRIGATION", PR, P.layout, ir.cycles && ir.cycles.length ? h("details", null, h("summary", null, "Completed steps"),
      table(["Cycle", "Sector", "Pump", "Irrigated", "Measured", "Mode", "Recipe"], ir.cycles.slice().reverse().map(c => h("tr", null, h("td", null, String(c.cycle)), h("td", null, c.sector), h("td", null, c.pump),
        h("td", null, num(c.irrigated_s, 0) + " s"), h("td", null, num(c.delivered_L_measured, 1) + " L"), h("td", null, c.mode), h("td", { class: "mono" }, c.recipe || ""))))) : null) : null;
    await page(P, PR, PAGE_DEF["main-pumps"], cards, panel);
  }
  async function sector(id) {
    const [P, PR] = await load();
    const s = P.layout.sectors.find(x => x.sector_id === id);
    if (!s) return mount("v-plant", h("div", { class: "card" }, "Unknown sector " + id));
    const ir = PR ? PR.processes.IRRIGATION : null, ch = P.layout.channel;
    const step = (PR && PR.active_recipe_body ? PR.active_recipe_body.irrigation.sequence : []).filter(x => x.sector === id);
    const cycles = ir ? (ir.cycles || []).filter(c => c.sector === id).slice(-10).reverse() : [];
    const cards = [h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, `Sector ${id.slice(1)}`), kvRows([
        ["Valve", reqObs(P, `valve.${id}.cmd`, `valve.${id}.open_limit`, ["open", "not open"])],
        ["Closed limit", obs(P, `valve.${id}.closed_limit`, 0, ["closed", "not closed"])],
        ["Flow", obs(P, `flow.${id}`, 1)], ["Pressure", obs(P, `pressure.${id}`, 2)], ["AUTO pump", s.auto_pump],
        PR ? ["Recipe duration", step.length ? step.map(x => x.duration_s + " s").join(", ") : "not in the sequence"] : null,
        ir ? ["Now", ir.sector === id ? `${ir.phase}, ${num(ir.irrigated_s, 0)} s irrigated` : "not irrigating"] : null]),
        h("p", { class: "muted" }, "No closed-limit point in the point allocation (the 32 added IO 8CH channels stay unassigned): closure is the open limit released and no measured flow (weaker evidence).")),
      h("div", { class: "card" }, h("h2", null, "Channels"),
        h("p", null, `Channels ${s.channels.join(", ")}: about ${ch.length_m} × ${ch.width_m} × ${ch.height_m} m, ${ch.retained_depth_m} m of water retained (${num(ch.retained_L, 0)} L each, ${num(ch.retained_L * s.channels.length, 0)} L in this sector).`),
        h("p", null, `${P.layout.outlets_per_channel} outlets per channel (${P.layout.outlets_per_channel * s.channels.length} here), adjusted by hand. They are not electric valves and are not observed.`),
        h("p", { class: "muted" }, "Owner-stated approximate geometry; unverified."))),
      ir ? h("div", { class: "card" }, h("h2", null, "Recent steps in this sector"), cycles.length ? table(["Cycle", "Pump", "Irrigated", "Measured", "Mode"], cycles.map(c => h("tr", null,
        h("td", null, String(c.cycle)), h("td", null, c.pump), h("td", null, num(c.irrigated_s, 0) + " s"), h("td", null, num(c.delivered_L_measured, 1) + " L"), h("td", null, c.mode)))) : h("p", { class: "muted" }, "None yet."),
        h("p", null, h("a", { href: "#main-pumps" }, "Irrigation process and manual step"))) : null];
    // The irrigation process is run from Main pumps; this page links there instead of repeating its controls.
    await page(P, PR, sectorDef(id), cards, ir ? h("div", { class: "card" }, h("h2", null, "Irrigation process"), h("p", null, statePill(ir.state), " ", ir.phase, " ",
      h("span", { class: "muted" }, ir.inhibit || ir.next || "")), h("p", null, h("a", { href: "#main-pumps" }, "AUTO, MANUAL and STOP are on the Main pumps page"))) : null);
  }
  async function drainage() {
    const [P, PR] = await load();
    const rt = PR ? PR.processes.RETURN : null, b = PR && PR.active_recipe_body ? PR.active_recipe_body.return : null;
    const cards = h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", null, `Sump (about ${P.layout.tanks_L.SUMP} L)`), kvRows([
        ["Level", obs(P, "tank.SUMP.level", 0)], ["High switch", obs(P, "tank.SUMP.high", 0, ["ACTIVE", "clear"])], ["Low switch", obs(P, "tank.SUMP.low", 0, ["ACTIVE", "clear"])],
        ...P.layout.return_pumps.map(rp => [rp, reqObs(P, `pump.${rp}.cmd`, `pump.${rp}.running`, ["running", "stopped"])]),
        ["Return flow", obs(P, "flow.RETURN", 1)], ["Main tank", obs(P, "tank.MAIN.level", 0)]])),
      PR ? h("div", { class: "card" }, h("h2", null, "Return settings"), b ? kvRows([["Start at (sump)", b.sump_start_L + " L"], ["Stop at (sump)", b.sump_stop_L + " L"],
        ["Hold when main reaches", b.main_inhibit_L + " L"], ["Resume below", b.main_resume_L + " L"], ["Duty policy", b.duty_policy], ["Maximum run", b.max_runtime_s + " s"]]) : h("p", { class: "muted" }, "No active recipe."),
        h("p", { class: "muted" }, "The duty policy is explicit: no implicit rotation or failover. Sump high with the main tank full raises an alarm and the main tank is never filled above the hold level.")) : null);
    await page(P, PR, PAGE_DEF.drainage, cards, rt ? procPanel("RETURN", PR, P.layout, rt.runs && rt.runs.length ? h("details", null, h("summary", null, "Recent runs"),
      table(["Run"], rt.runs.slice().reverse().map(r => h("tr", null, h("td", { class: "mono" }, summary(r)))))) : null) : null);
  }

  // ---------------------------------------------------------------- recipes
  function getp(o, path) { return path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o); }
  function setp(o, path, val) {
    const ks = path.split("."); let cur = o;
    ks.slice(0, -1).forEach(k => { if (cur[k] == null || typeof cur[k] !== "object") cur[k] = {}; cur = cur[k]; });
    cur[ks[ks.length - 1]] = val;
  }
  const GROUPS = [["main", "Main tank levels"], ["refill", "Refill"], ["acid", "Acid dosing"], ["irrigation", "Irrigation"], ["return", "Drainage return"], ["stock_fill", "Stock fills"]];
  let recipesKey = null;
  async function recipes() {
    if (U.isReal()) return mount("v-plant", h("div", { class: "card" }, h("h2", null, "Recipes"),
      h("p", null, pill("NOT AVAILABLE", "warn"), " Recipes drive the simulated process controllers only; a hardware profile has none in this release (ADR-0009).")));
    const R = await api("GET", "/api/recipes");
    const E = S.recipeEdit;
    // nothing live on this page: rebuild it only when the recipes or the draft being edited changed, so a
    // button is never replaced under the pointer while the owner clicks it
    // (the fields themselves write into the draft: only a new, discarded or re-shaped draft needs a rebuild)
    const key = JSON.stringify([R, E && [E.recipe_id, E.version, E.from, ((E.body.irrigation || {}).sequence || []).length], S.perms]);
    if (key === recipesKey && document.getElementById("v-plant").dataset.view === "recipes") return;
    recipesKey = key;
    const statusPill = st => pill(st, st === "ACTIVE" ? "ok" : st === "VALIDATED" ? "" : st === "DRAFT" ? "warn" : "");
    const edit = (rec, asNew) => {
      S.recipeEdit = { recipe_id: asNew ? "" : rec.recipe_id, name: asNew ? "" : rec.name, version: !asNew && rec.status === "DRAFT" ? rec.version : null,
        from: rec.ref, body: JSON.parse(JSON.stringify(rec.body)) };
      U.refresh();
    };
    const list = table(["Recipe", "Status", "Created", "Activated", "Issues", ""], R.recipes.map(r => h("tr", null,
      h("td", null, h("strong", null, r.ref), h("div", { class: "muted" }, `${r.name} · ${r.provenance}`)), h("td", null, statusPill(r.status)),
      h("td", null, `${fmtTime(r.created_utc)} by ${r.created_by}`), h("td", null, r.activated_utc ? `${fmtTime(r.activated_utc)} by ${r.activated_by}` : "—"),
      h("td", null, r.issues && r.issues.length ? h("span", { class: "error" }, `${r.issues.length} issue(s)`) : ""),
      h("td", null,
        pbtn(r.status === "DRAFT" ? "Edit draft" : "Edit as next version", () => edit(r, false), "ghost", "recipe_edit"), " ",
        pbtn("Copy to new recipe", () => edit(r, true), "ghost", "recipe_edit"), " ",
        r.status === "DRAFT" ? pbtn("Validate", () => act("POST", `/api/recipes/${r.recipe_id}/${r.version}/validate`, undefined, "validated"), "", "recipe_edit") : null, " ",
        r.status === "VALIDATED" ? pbtn("Activate", () => { const reason = prompt(`Activate ${r.ref}? It applies from each process's next cycle, batch or dose. Reason (recorded):`); if (reason) act("POST", `/api/recipes/${r.recipe_id}/${r.version}/activate`, { reason }, "activated"); }, "", "recipe_activate") : null))));
    const issues = R.recipes.filter(r => r.issues && r.issues.length && r.status === "DRAFT");
    const kids = [
      h("div", { class: "card" }, h("h2", null, "Recipes"),
        h("p", null, "Active: ", h("strong", null, R.active || "none"), ". A change applies from each process's next cycle, batch or dose; running operations keep their version."),
        list,
        h("div", { class: "row" }, pbtn("Roll back to the previous version", () => { const reason = prompt("Roll back the active recipe. Reason (recorded):"); if (reason) act("POST", "/api/recipes/rollback", { reason }, "rolled back"); }, "ghost", "recipe_activate"),
          h("span", { class: "muted" }, "Operators edit and validate; administrators activate and roll back."))),
      issues.length ? h("div", { class: "card" }, h("h2", null, "Validation issues"), issues.map(r => h("div", null, h("strong", null, r.ref),
        h("ul", null, r.issues.map(i => h("li", null, h("span", { class: "mono" }, i.path), ": ", i.message)))))) : null];
    if (E) kids.push(editor(R, E));
    kids.push(h("div", { class: "card" }, h("h2", null, "Activation log"), table(["Time", "Action", "Recipe", "By", "Reason"], R.activations.slice().reverse().map(a => h("tr", null,
      h("td", null, fmtTime(a.utc)), h("td", null, a.action), h("td", { class: "mono" }, a.recipe + (a.previous ? " (was " + a.previous + ")" : "")), h("td", null, a.actor || ""), h("td", null, a.reason || ""))))));
    mount("v-plant", kids);
  }
  // the sections the user opened stay open when the page refreshes
  function keepOpen(g, d) {
    d.addEventListener("toggle", () => {
      const o = new Set(S.recipeOpen || ["main", "refill"]);
      d.open ? o.add(g) : o.delete(g);
      S.recipeOpen = [...o];
    });
    return d;
  }
  function editor(R, E) {
    const groups = {};
    R.form.forEach(f => { const g = f.path.split(".")[0]; (groups[g] = groups[g] || []).push(f); });
    const field = f => {
      const cur = getp(E.body, f.path);
      let input;
      if (f.options && f.options.length) {
        input = h("select", null, h("option", { value: "" }, "— not set —"), f.options.map(o => h("option", { value: o, selected: cur === o }, o)));
        input.addEventListener("change", () => { setp(E.body, f.path, input.value || null); });
      } else {
        input = h("input", { type: "number", step: "any", value: cur == null ? "" : String(cur), min: f.min, max: f.max, class: "w-num" });
        input.addEventListener("input", () => { setp(E.body, f.path, input.value === "" ? null : Number(input.value)); });
      }
      const range = f.options && f.options.length ? "" : `${f.min_exclusive ? ">" : "≥"} ${f.min ?? "—"}${f.max != null ? ", ≤ " + f.max : ""}`;
      return h("tr", null, h("td", null, f.label, f.help ? h("div", { class: "muted" }, f.help) : null), h("td", null, input),
        h("td", null, f.unit || ""), h("td", { class: "muted" }, range + (f.required !== "always" ? ` · required when ${f.required}` : "")));
    };
    const seq = (E.body.irrigation && E.body.irrigation.sequence) || [];
    const seqRows = seq.map((st, i) => {
      const s = h("select", null, R.sectors.map(x => h("option", { value: x, selected: st.sector === x }, x)));
      const d = h("input", { type: "number", min: 1, max: 86400, step: 1, value: String(st.duration_s), class: "w-num" });
      s.addEventListener("change", () => { st.sector = s.value; });
      d.addEventListener("input", () => { st.duration_s = Number(d.value); });
      return h("tr", null, h("td", null, String(i + 1)), h("td", null, s), h("td", null, d, " s"),
        h("td", null, h("button", { class: "small ghost", onclick: () => { seq.splice(i, 1); U.refresh(); } }, "Remove")));
    });
    const rid = h("input", { value: E.recipe_id, placeholder: "recipe id (a-z, 0-9, - or _)", disabled: E.version != null || !!(E.recipe_id && E.from && E.from.startsWith(E.recipe_id + "@")) });
    const nm = h("input", { value: E.name, placeholder: "name" });
    rid.addEventListener("input", () => { E.recipe_id = rid.value; });
    nm.addEventListener("input", () => { E.name = nm.value; });
    const save = () => act("POST", "/api/recipes/draft", { recipe_id: E.recipe_id, name: E.name || E.recipe_id, body: E.body, version: E.version },
      "draft saved").then(r => { if (r) { S.recipeEdit = null; toast(`${r.ref} saved as DRAFT; validate it next`); U.refresh(); } });
    return h("div", { class: "card" },
      h("h2", null, E.version != null ? `Editing draft ${E.recipe_id}@v${E.version}` : `New draft from ${E.from}`),
      h("div", { class: "row" }, h("label", null, "Recipe id", rid), h("label", null, "Name", nm)),
      h("p", { class: "muted" }, "Units are part of every field. Saving checks types; Validate checks ranges, required fields for the chosen modes and ordered thresholds. Nothing here is a production value until you set it."),
      GROUPS.filter(([g]) => groups[g]).map(([g, title]) => keepOpen(g, h("details", { open: (S.recipeOpen || ["main", "refill"]).includes(g) }, h("summary", null, title),
        table(["Field", "Value", "Unit", "Allowed"], groups[g].map(field)),
        g === "irrigation" ? [h("h3", null, "Sequence (one AUTO cycle)"), table(["#", "Sector", "Duration", ""], seqRows),
          h("button", { class: "small ghost", onclick: () => { seq.push({ sector: R.sectors[0], duration_s: 300 }); U.refresh(); } }, "Add step")] : null))),
      h("div", { class: "row" }, pbtn("Save draft", save, "", "recipe_edit"), h("button", { class: "small ghost", onclick: () => { S.recipeEdit = null; U.refresh(); } }, "Discard changes")));
  }

  // ---------------------------------------------------------------- navigation and registration
  let navBuilt = false;
  async function buildNav() {
    if (navBuilt) return;
    navBuilt = true;
    let P;
    try { P = await api("GET", "/api/plant"); } catch (e) { navBuilt = false; return; }
    const box = document.getElementById("nav-inst");
    const links = PAGES.concat(P.layout.sectors.map(s => ["sector-" + s.sector_id, "Sector " + s.sector_id.slice(1)]), [["drainage", "Drainage"]],
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
  VIEWS.drainage = only(drainage); VIEWS.recipes = only(recipes);
  function resolve(view) { return view.startsWith("sector-") ? only(() => sector(view.slice(7))) : null; }
  window.AqPlant = { overview, hwDiagram, buildNav, resolve };
})();
