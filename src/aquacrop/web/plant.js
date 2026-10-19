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
    return h("span", null, "asked: ", asked, fb ? [" · reports: ", obs(P, fb, 0, words)] : null);
  }
  // a selector (contactor) only selects its pump; the drive runs it
  const names = ids => ids && ids.length ? ids.map(x => { const id = x.replace(/^(valve|pump|contactor|drive)\./, "").replace(/\.(cmd|run)$/, "");
    return x.startsWith("contactor.") ? `${W.cap(W.equip(id))} selected` : x.startsWith("drive.") ? "Pump drive running" : W.cap(W.equip(id)); }).join(", ") : "none";
  const STOP_STAGE = { DRIVE: "stopping the pump", SETTLE: "waiting for the pump to stop", VALVES: "closing the valve", DESELECT: "releasing the pump selection" };

  // ---------------------------------------------------------------- the installation diagram
  /** opts.readOnly: a hardware profile (navigation only, no controls anywhere); opts.focus: the page whose parts
   * are highlighted (the others are dimmed). Clicking a part only navigates. */
  function diagram(P, opts) {
    const readOnly = !!(opts && opts.readOnly), focus = opts && opts.focus;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 1100 690"); svg.setAttribute("class", "schematic diagram");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Greenhouse diagram: the readings the program uses" + (readOnly ? " (read-only)" : ""));
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
    function short(r, d, words) { return !r.bound ? "not measured" : fmtVal(r, d, words) + (r.adapter || r.ok ? "" : " (" + W.quality(r.q || "UNAVAILABLE").toLowerCase() + ")"); }
    function tank(x, y, w, hh, name, t, page) {
      const cap = P.layout.tanks_L[t], r = v(`tank.${t}.level`);
      hot(page, `${name}: ${short(r, 0)} of ${cap} L`, () => {
        if (r.ok) { const f = Math.max(0, Math.min(1, r.v / cap)); e("rect", { x, y: y + hh * (1 - f), width: w, height: hh * f, class: "water" }); }
        else e("rect", { x: x + 2, y: y + 2, width: w - 4, height: hh - 4, class: "unknown" });
        e("rect", { x, y, width: w, height: hh, class: "tank-wall hl" });
        e("text", { x: x + w / 2, y: y - 6, "text-anchor": "middle", class: "th" }, name);
        e("text", { x: x + w / 2, y: y + hh + 14, "text-anchor": "middle", class: "val" }, r.ok ? `${W.n0(num(r.v, 0))} / ${W.n0(cap)} L` : `level ${short(r, 0)}`);
        if (r.ok) e("text", { x: x + w / 2, y: y + hh + 27, "text-anchor": "middle", class: "val muted-t" }, `${(100 * r.v / cap).toFixed(0)} % · ${U.ago(r.age)}`);
      });
    }
    function reqClass(cmd) { const r = v(cmd); return !r.bound ? "equip-req unbound" : "equip-req " + (r.v ? "req-on" : "req-off"); }
    function valve(x, y, id, label, page, fb) {
      const cmd = `valve.${id}.cmd`, rc = v(cmd), rf = fb ? v(fb) : null;
      hot(page, `${label}: asked ${short(rc, 0, ["open", "closed"])}${rf ? ", open signal " + short(rf, 0, ["yes", "no"]) : ", no position signal"}`, () => {
        const open = rf && rf.ok ? (rf.v ? 1 : 0.15) : null;
        e("polygon", { points: `${x - 14},${y - 9} ${x - 14},${y + 9} ${x},${y}`, class: open == null ? "equip-unk" : "equip-act", "fill-opacity": open == null ? null : open });
        e("polygon", { points: `${x + 14},${y - 9} ${x + 14},${y + 9} ${x},${y}`, class: open == null ? "equip-unk" : "equip-act", "fill-opacity": open == null ? null : open });
        e("rect", { x: x - 17, y: y - 12, width: 34, height: 24, class: reqClass(cmd) + " hl" });
        e("text", { x, y: y - 16, "text-anchor": "middle", class: "small" }, label);
        e("text", { x, y: y + 24, "text-anchor": "middle", class: "small muted-t" }, `asked ${short(rc, 0, ["open", "closed"])}`);
        if (rf) e("text", { x, y: y + 36, "text-anchor": "middle", class: "small muted-t" }, short(rf, 0, ["open", "not open"]));
      });
    }
    function pump(x, y, id, label, page, cmd, fb, extra, words) {
      // words: what the feedback reports ([on, off]); main pumps report their selector, not the motor
      const rc = v(cmd), rf = fb ? v(fb) : null, w = words || ["running", "stopped"];
      hot(page, `${label}: asked ${short(rc, 0)}${rf ? ", reports " + short(rf, 0, w) : ""}`, () => {
        e("circle", { cx: x, cy: y, r: 16, class: rf && rf.ok ? "equip-act" : "equip-unk", "fill-opacity": rf && rf.ok ? (rf.v ? 1 : 0.15) : null });
        e("circle", { cx: x, cy: y, r: 20, class: reqClass(cmd) + " hl" });
        e("text", { x, y: y + 4, "text-anchor": "middle", class: "small" }, id);
        e("text", { x, y: y + 34, "text-anchor": "middle", class: "small muted-t" },
          `asked ${short(rc, 0)}${rf ? " · " + short(rf, 0, w) : ""}${extra || ""}`);
      });
    }
    function flowText(x, y, n, anchor) {
      const r = v(n);
      e("text", { x, y, "text-anchor": anchor || "start", class: "val" }, r.bound ? `${fmtVal(r, 1)}${r.ok ? "" : " (" + W.quality(r.q || "UNAVAILABLE").toLowerCase() + ")"}` : "flow not measured");
    }

    // mains header and stock fills
    e("text", { x: 20, y: 24, class: "th" }, "Mains water");
    pipe("M20 40 H620", null);
    [["A", "Fertiliser A", 60], ["B", "Fertiliser B", 200], ["ACID", "Acid", 340]].forEach(([s, name, x]) => {
      const c = x + 45;
      pipe(`M${c} 40 V125`, null);
      valve(c, 72, `FILL_${s}`, `water in`, "#nutrients", null);
      tank(x, 125, 90, 100, name, s, "#nutrients");
      pipe(`M${c} 258 V420 H500`, `flow.TP_${s}`);
      valve(c, 285, `OUT_${s}`, `outlet`, "#nutrients", null);
      pump(c, 345, `TP_${s}`, `Transfer pump, ${W.TANK[s].toLowerCase()}`, "#nutrients", `pump.TP_${s}.cmd`, `pump.TP_${s}.running`);
      flowText(c, 396, `flow.TP_${s}`, "middle");
    });
    e("text", { x: 20, y: 470, class: "small muted-t" }, "No stirrer or air pump: mixing is not checked.");
    // main tank
    pipe("M620 40 V210", "flow.FILL_MAIN");
    valve(620, 120, "FILL_MAIN", "water in", "#main-tank", null);
    flowText(632, 180, "flow.FILL_MAIN");
    tank(500, 210, 160, 240, "Main tank", "MAIN", "#main-tank");
    const ph = v("main.ph"), ec = v("main.ec");
    hot("#main-tank", `Acidity (pH) ${short(ph, 2)}, nutrient strength (EC) ${short(ec, 2)}`, () => {
      e("rect", { x: 505, y: 495, width: 150, height: 40, class: "box hl" });
      e("text", { x: 580, y: 511, "text-anchor": "middle", class: "small" }, `pH ${short(ph, 2)}`);
      e("text", { x: 580, y: 527, "text-anchor": "middle", class: "small" }, `EC ${short(ec, 2)}`);
    });
    e("text", { x: 505, y: 550, class: "small muted-t" }, "probe position proposed, not installed");
    // main pumps on the shared drive
    const drv = P.layout.shared_drive, mps = P.layout.main_pumps;
    pipe("M660 410 H700", null);
    mps.forEach((mp, i) => {
      const y = 290 + i * 120, k = "K" + (i + 1);
      pipe(`M700 410 V${y} H740`, null);
      pump(760, y, mp, `Main pump ${i + 1} (selector ${k})`, "#main-pumps", `contactor.${mp}.cmd`, `contactor.${mp}.closed`, "", [`${k} closed`, `${k} open`]);
      pipe(`M780 ${y} H830`, null);
    });
    pipe("M830 200 V440", null);
    const dr = v(`drive.${drv}.run`), drr = v(`drive.${drv}.running`), drf = v(`drive.${drv}.fault`);
    hot("#main-pumps", `Pump drive: asked ${short(dr, 0)}, running ${short(drr, 0, ["yes", "no"])}, fault ${short(drf, 0, ["yes", "no"])}`, () => {
      e("rect", { x: 690, y: 472, width: 140, height: 46, class: "box hl" });
      e("text", { x: 760, y: 489, "text-anchor": "middle", class: "small" }, "Pump drive (shared)");
      e("text", { x: 760, y: 505, "text-anchor": "middle", class: "small muted-t" }, `asked ${dr.adapter ? dr.txt : short(dr, 0)} · ${short(drr, 0, ["running", "stopped"])}`);
      e("text", { x: 760, y: 534, "text-anchor": "middle", class: drf.ok && drf.v ? "small fault-mark" : "small muted-t" },
        drf.ok && drf.v ? "⚠ DRIVE FAULT" : `fault ${short(drf, 0, ["yes", "no"])}`);
    });
    // sectors
    P.layout.sectors.forEach((s, i) => {
      const y = 200 + i * 240, id = s.sector_id, page = "#sector-" + id;
      pipe(`M830 ${y} H866`, `flow.${id}`);
      valve(880, y, id, `valve ${id}`, page, `valve.${id}.open_limit`);
      pipe(`M894 ${y} H930`, `flow.${id}`);
      hot(page, `${W.sectorName(id)}: channels ${s.channels[0]} to ${s.channels[s.channels.length - 1]}`, () => {
        e("rect", { x: 930, y: y - 50, width: 160, height: 100, class: "box hl" });
        e("text", { x: 1010, y: y - 32, "text-anchor": "middle", class: "th" }, W.sectorName(id));
        e("text", { x: 1010, y: y - 16, "text-anchor": "middle", class: "small" }, `channels ${s.channels[0]}–${s.channels[s.channels.length - 1]} · ${s.channels.length * P.layout.outlets_per_channel} outlets`);
        e("text", { x: 1010, y: y + 2, "text-anchor": "middle", class: "small" }, `flow ${short(v("flow." + id), 1)}`);
        e("text", { x: 1010, y: y + 18, "text-anchor": "middle", class: "small" }, `pressure ${short(v("pressure." + id), 2)}`);
        e("text", { x: 1010, y: y + 36, "text-anchor": "middle", class: "small muted-t" }, `automatic: main pump ${s.auto_pump.slice(2)}`);
      });
      pipe(`M1090 ${y + 30} H1097`, null);
    });
    // drainage
    pipe("M1097 230 V600 H960", null);
    e("text", { x: 1000, y: 590, "text-anchor": "middle", class: "small muted-t" }, "drains by gravity");
    tank(860, 560, 100, 80, "Drainage tank", "SUMP", "#drainage");
    pipe("M860 620 H478 V440 H500", "flow.RETURN");
    P.layout.return_pumps.forEach((rp, i) => pump(770 - i * 100, 620, rp, `Return pump ${rp.slice(2)}`, "#drainage", `pump.${rp}.cmd`, `pump.${rp}.running`));
    flowText(490, 612, "flow.RETURN");
    const sc = v("safety.chain_ok");
    e("text", { x: 20, y: 680, class: sc.ok && !sc.v ? "fault-mark" : "small" }, `Safety circuit: ${short(sc, 0, ["healthy", "OPEN"])}`);
    return svg;
  }
  function legend(P) {
    return h("p", { class: "muted" }, "Outline: what the program asked for (solid = on or open, dashed = off or closed, dotted = no channel). ",
      "Fill: what the equipment reports (hatched = nothing reports it). The values are the readings the program uses, with their age. ",
      "Click a part to open its page. ", provenance(P) + " Layout: " + P.layout.provenance + ".");
  }
  function scrollHint() { return h("p", { class: "scroll-hint muted" }, "Scroll sideways to see the whole greenhouse."); }

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
    const gone = pv ? !pv.fault_present : !o.active;
    steps.push(h("li", null, h("strong", null, "Fix the problem: "), pb.fix,
      sim && pb.simFault ? h("span", { class: "muted" }, " In the Digital Twin, a simulated fault is removed on the ", h("a", { href: "#twin" }, "Digital Twin"), " page.") : null,
      gone ? h("span", { class: "muted" }, " The program no longer sees the problem; check the equipment yourself before starting again.")
        : h("span", { class: "warn-t" }, " The program still sees the problem.")));
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
  function stopbar(PR, pid) {
    if (!PR || !PR.processes[pid] || !can("process_control")) return null;
    const pv = PR.processes[pid], w = W.proc(pid);
    return h("div", { class: "stopbar", role: "region", "aria-label": `${w.name}: state and stop` },
      h("span", null, h("strong", null, w.name), " ", statePill(pv.state)),
      h("button", { class: "cmd danger stop", onclick: () => setState(pid, "STOPPED", `${w.name}: stop requested`) }, w.stop));
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
      leg.replaceChildren(...out.map(l => h("span", { style: "color:" + l.color }, (l.suspect ? "◌ " : "■ ") + l.name)));
    };
    return [card, () => draw().catch(e => leg.replaceChildren(h("span", { class: "error" }, "Chart unavailable: " + (e.message || e))))];
  }

  function provenance(P) {
    return P.read_only ? `Readings: REAL where a signal is mapped; ${P.mapping}.`
      : `Readings: from the Digital Twin (simulated, source ${S.status.twin ? S.status.twin.source : "?"}); ${P.mapping}.`;
  }
  /** The installation diagram with this page's parts highlighted: a secondary view, closed until opened (F-02). */
  function pageDiagram(P, def) {
    const d = h("details", { class: "card", open: S.pageDiagram === true },
      h("summary", null, h("strong", null, "Greenhouse diagram"), h("span", { class: "muted" }, " (the parts of this page are highlighted)")),
      scrollHint(), h("div", { class: "diagram-wrap" }, diagram(P, { readOnly: P.read_only, focus: def.focus })),
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
    return [P, PR];
  }
  /** A process page: the main process's stop bar (narrow screens), the process cards with their state, Stop and
   * next step, the page's other problems, its own cards, the collapsed diagram and the last hour. */
  async function page(P, PR, def, top, cards, tech) {
    const [tc, draw] = trendCard(P, def.trend);
    mount("v-plant", stopbar(PR, def.procs[0]), PR ? top : noControl(P), problemsCard(P, PR, def), cards,
      tech ? h("details", { class: "card tech" }, h("summary", null, h("strong", null, "Technical details")), tech) : null,
      pageDiagram(P, def), tc);
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
    mount("v-overview",
      h("div", { class: "grid" },
        h("div", { class: "card wide" }, h("h2", null, "What is happening now"),
          table(["Process", "State", "Now", ""], procs),
          h("div", { class: "row" }, h("button", { class: "cmd danger", disabled: !can("process_control"),
            onclick: () => confirm("Stop every process?") && act("POST", "/api/processes/stop-all", { reason: "operator stop all" }, "every process stopping") }, "Stop all processes"),
            h("span", { class: "muted" }, "Recipe in use: ", h("strong", null, PR.recipe.active || "none"), ". Automatic operation exists only in the Digital Twin in this release."))),
        h("div", { class: "card" }, h("h2", null, "Tanks"), table(["Tank", "Water level", ""], tanks)),
        problemsCard(P, PR, null)),
      h("div", { class: "card" }, h("h2", null, "Greenhouse diagram"), scrollHint(), h("div", { class: "diagram-wrap" }, diagram(P, {})), legend(P),
        h("p", null, h("a", { href: "#diagram" }, "Open the full diagram"))));
  }
  async function fullDiagram() {
    const P = await api("GET", "/api/plant");
    mount("v-plant", h("div", { class: "card" }, h("h2", null, "Greenhouse diagram"), scrollHint(),
      h("div", { class: "diagram-wrap full" }, diagram(P, { readOnly: P.read_only })), legend(P)));
  }
  async function hwDiagram() {
    const P = await api("GET", "/api/plant");
    return h("div", { class: "card" }, h("h2", null, "Installation (read-only)"), scrollHint(), h("div", { class: "diagram-wrap" }, diagram(P, { readOnly: true })), legend(P),
      h("p", { class: "muted" }, "No process control exists on a hardware profile in this release. Each part opens its read-only page."));
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
    await page(P, PR, PAGE_DEF.nutrients, top, cards, tech);
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
      const range = f.options && f.options.length ? "" : `allowed ${f.min_exclusive ? "more than" : "from"} ${f.min ?? "—"}${f.max != null ? " to " + W.n0(f.max) : ""}${f.unit ? " " + f.unit : ""}`;
      const input = h("input", { type: "number", step: "any", id, value: cur == null ? "" : String(cur), min: f.min, max: f.max, class: "w-num",
        "aria-describedby": help, "aria-invalid": err ? "true" : null, placeholder: f.example || null });
      input.addEventListener("input", () => { setp(E.body, f.path, input.value === "" ? null : Number(input.value)); touched(); });
      return h("div", { class: "field" + (err ? " invalid" : ""), "data-path": f.path },
        h("label", { for: id }, f.label), h("div", { class: "row" }, input, h("span", null, f.unit || "")),
        h("div", { class: "muted small", id: help }, [range, f.example ? `example ${f.example}` : "", f.help].filter(Boolean).join(" · ")),
        err ? h("div", { class: "error", role: "alert" }, err.join("; ")) : null);
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
    const rid = h("input", { id: "rf-recipe-id", value: E.recipe_id, placeholder: "e.g. summer-2027", disabled: E.version != null || !!(E.recipe_id && E.from && E.from.startsWith(E.recipe_id + "@")) });
    const nm = h("input", { id: "rf-recipe-name", value: E.name, placeholder: "e.g. Summer tomatoes" });
    rid.addEventListener("input", () => { E.recipe_id = rid.value; touched(); });
    nm.addEventListener("input", () => { E.name = nm.value; touched(); });
    /** Check settings = save the draft (types) and run the server's full check; the typed values stay either way. */
    const check = async () => {
      const r = await act("POST", "/api/recipes/draft", { recipe_id: E.recipe_id, name: E.name || E.recipe_id, body: E.body, version: E.version }, null);
      if (!r) return;
      E.version = r.version; E.recipe_id = r.recipe_id;
      const v = await act("POST", `/api/recipes/${r.recipe_id}/${r.version}/validate`, undefined, null);
      if (!v) return;
      E.issues = v.issues || []; E.checked = v.status === "VALIDATED"; E.ref = v.ref;
      toast(E.checked ? `${v.ref}: the settings passed the check` : `${v.ref}: ${E.issues.length} problem(s) to fix; they are shown beside the fields`, !E.checked);
      U.refresh();
    };
    const changes = diffs(E.base, E.body, rules);
    const hidden = Object.keys(issueAt).filter(p => rules[p] && !shown.has(p) && !MODES.includes(p));
    const modeFields = MODES.map(p => rules[p]).filter(Boolean);
    const body = [
      h("h2", null, E.version != null ? `Changing the draft ${E.recipe_id} (version ${E.version})` : `New settings, starting from ${E.from}`),
      h("div", { class: "row" }, h("label", { for: "rf-recipe-id" }, "Recipe id (a-z, 0-9, - or _)", rid), h("label", { for: "rf-recipe-name" }, "Name", nm)),
      h("h3", null, "1. Choose how to refill"), h("div", { class: "grid" }, modeFields.map(choice)),
      h("h3", null, "2. Set amounts and targets"),
      h("p", { class: "muted" }, "Only the settings the chosen ways use are shown. Every field gives its unit and allowed range. Nothing here is a production value until you set it."),
      SECTIONS.filter(([g]) => byGroup[g]).map(([g]) => sectionOf(g))];
    // the field rendering above registered what is shown; problems on fields not shown are listed here
    return h("div", { class: "card editor" }, body,
      hidden.length ? h("div", { class: "error", role: "alert" }, "Also to fix: ", hidden.map(p => `${rules[p].label}: ${issueAt[p].join("; ")}`).join(" · ")) : null,
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
