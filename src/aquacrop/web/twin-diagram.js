/* The digital twin's installation diagram (0.5.6): the simulation drawn live, a timeline to go back and replay
 * it, and the layout editor. The layout is edited here only; "Send to the real system" writes a proposal that an
 * administrator, or an operator with commissioning, accepts on the real dashboard. Results and refusals are shown
 * as text beside the buttons (owner preference), never as pop-ups. Loaded after twin.js. */
"use strict";
(function () {
  const T = window.AqTwin, L = window.AqLayout, { h, api, can } = T;
  const D = { card: null, body: null, data: null, frames: [], runId: null, at: null, playing: null, edit: null,
    sel: null, pipeDraft: null, msg: {}, dragging: false, lastFetch: 0, diagEl: null };
  const KINDS = [["tank", "Tank"], ["pump", "Pump"], ["valve", "Valve"], ["sensor", "Sensor"], ["sector", "Sector"], ["box", "Box"], ["label", "Text"]];
  const clone = o => JSON.parse(JSON.stringify(o));
  const simTime = iso => new Date(iso).toLocaleString("sv-SE", { timeZone: T.tz(), hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" });

  function say(key, text, bad) { D.msg[key] = { text, bad }; draw(); }
  const msgEl = key => D.msg[key] ? h("span", { class: D.msg[key].bad ? "error small" : "muted small", role: "status" }, D.msg[key].text) : null;
  async function call(key, method, path, body, ok) {
    try { const r = await api(method, path, body); say(key, ok || "done"); return r; }
    catch (e) { say(key, String(e.message || e), true); return null; }
  }

  // ---------------------------------------------------------------- data
  async function load() { D.data = await api("GET", "api/twin/diagram"); }
  async function pullFrames(v) {
    if (v.run_id !== D.runId) { D.frames = []; D.runId = v.run_id; D.at = null; }
    const after = D.frames.length ? D.frames[D.frames.length - 1].sim_s : -1;
    const fr = await api("GET", `api/twin/frames?after=${after}`);
    D.frames.push(...fr);
    if (D.frames.length > 2400) D.frames.splice(0, D.frames.length - 2400);
  }
  /** The readings to draw: a replayed frame, or now. */
  function shown() {
    if (D.at != null && D.frames[D.at]) return { R: D.frames[D.at].items, frame: D.frames[D.at] };
    return { R: D.data.readings, frame: D.frames[D.frames.length - 1] || null };
  }

  // ---------------------------------------------------------------- the card
  function card() {
    if (!D.card) {
      D.body = h("div");
      D.card = h("div", { class: "card", id: "twin-diagram" }, D.body);
      D.card.addEventListener("pointerdown", () => { D.dragging = true; });
      window.addEventListener("pointerup", () => { if (D.dragging) { D.dragging = false; } });
    }
    return D.card;
  }
  async function update(v) {
    if (D.dragging || D.edit) return;                // the editor redraws on its own changes only
    const now = Date.now();
    if (now - D.lastFetch < 900) return;
    D.lastFetch = now;
    try { await Promise.all([load(), pullFrames(v)]); } catch (e) { return; }
    draw();
  }

  function draw() {
    if (!D.data || !D.body) return;
    const editing = !!D.edit, lay = editing ? D.edit : D.data.layout;
    const { R, frame } = editing ? { R: D.data.readings, frame: null } : shown();
    const svg = L.render(lay, R, {
      edit: editing, selected: D.sel && D.sel.id,
      onSelect: (kind, id) => { D.sel = { kind, id }; draw(); },
      onMove: (id, x, y) => { const n = D.edit.nodes.find(q => q.id === id); n.x = x; n.y = y; D.sel = { kind: "node", id }; D.dirty = true; draw(); },
      onPoint: (pid, i, x, y) => { D.edit.pipes.find(q => q.id === pid).points[i] = [x, y]; D.dirty = true; draw(); },
      onCanvas: (x, y) => {
        if (D.pipeDraft) { D.pipeDraft.points.push([x, y]); if (D.pipeDraft.points.length === 2) D.edit.pipes.push(D.pipeDraft); D.dirty = true; draw(); }
        else if (D.sel) { D.sel = null; draw(); }
      } });
    const wrap = h("div", { class: "diagram-layout" }, h("div", { class: "diagram-wrap" }, svg), side(R, lay));
    D.body.replaceChildren(...[
      h("div", { class: "row" }, h("h2", null, editing ? "Installation diagram — editing" : "Installation diagram"),
        editing ? h("span", { class: "pill warn" }, D.dirty ? "unsaved changes" : "editing") : (D.at != null ? h("span", { class: "pill warn" }, "REPLAY") : h("span", { class: "pill ok" }, "LIVE")),
        h("span", { class: "muted small" }, `Layout: ${D.data.source}.`)),
      editing ? editBar() : timeline(frame),
      wrap,
      editing ? null : L.legend(h),
      D.data.issues.length ? h("details", { class: "small" }, h("summary", null, `${D.data.issues.length} symbol(s) not matching the equipment`), h("ul", null, D.data.issues.map(x => h("li", null, x)))) : null,
      editing ? null : layoutBar()].filter(Boolean));   // replaceChildren would print a null
  }

  // ---------------------------------------------------------------- timeline
  function timeline(frame) {
    const n = D.frames.length;
    const slider = h("input", { type: "range", min: 0, max: Math.max(0, n - 1), value: D.at == null ? n - 1 : D.at, "aria-label": "Twin timeline", class: "timeline",
      oninput: ev => { stop(); const i = Number(ev.target.value); D.at = i >= n - 1 ? null : i; draw(); } });
    const marks = [];
    let prev = "";
    D.frames.forEach((f, i) => { const k = f.ops.map(o => o.program_id).join(","); if (k !== prev) { marks.push([i, k]); prev = k; } });
    const go = d => { stop(); const i = (D.at == null ? n - 1 : D.at) + d; D.at = i >= n - 1 ? null : Math.max(0, i); draw(); };
    return h("div", { class: "timeline-box" },
      h("div", { class: "row" },
        h("button", { class: "small ghost", disabled: !n, onclick: () => go(-1), "aria-label": "One frame back" }, "◀"),
        D.playing ? h("button", { class: "small", onclick: stop }, "Stop replay")
          : h("button", { class: "small", disabled: n < 2, onclick: play }, D.at == null ? "Replay from start" : "Play"),
        h("button", { class: "small ghost", disabled: D.at == null, onclick: () => go(1), "aria-label": "One frame forward" }, "▶"),
        h("button", { class: "small ghost", disabled: D.at == null, onclick: () => { stop(); D.at = null; draw(); } }, "Back to live"),
        h("strong", null, frame ? simTime(frame.sim_utc) : "—"),
        h("span", { class: "muted small" }, frame && frame.ops.length ? "running: " + frame.ops.map(o => `${o.program_id} v${o.version} (${o.state.toLowerCase()})`).join(", ") : "nothing running")),
      slider,
      h("div", { class: "timeline-marks small muted" }, marks.slice(-8).map(([i, k]) =>
        h("button", { class: "link small", onclick: () => { stop(); D.at = i; draw(); } }, `${simTime(D.frames[i].sim_utc)} ${k ? "▶ " + k : "■ idle"}`))),
      h("p", { class: "muted small" }, `The timeline keeps the last ${n} pictures of this twin run (four a second while it runs, so at 60× about a simulated minute per four pictures). Replaying never changes the simulation.`));
  }
  function play() {
    if (D.at == null) D.at = 0;
    D.playing = setInterval(() => { if (D.at == null || D.at >= D.frames.length - 1) { stop(); D.at = null; } else D.at += 1; draw(); }, 250);
    draw();
  }
  function stop() { if (D.playing) { clearInterval(D.playing); D.playing = null; } }

  // ---------------------------------------------------------------- side panel
  function side(R, lay) {
    if (!D.sel) return h("div", { class: "card side-panel" }, h("h3", null, "Nothing selected"),
      h("p", { class: "muted" }, D.edit ? "Drag a symbol to move it. Select a symbol or a pipe to change it, or add one above." : "Select a pump, valve, tank, sensor or pipe to see its state here."));
    if (D.sel.kind === "pipe") {
      const p = lay.pipes.find(q => q.id === D.sel.id);
      if (!p) return h("div");
      return D.edit ? pipeForm(p) : h("div", { class: "card side-panel" }, h("h3", null, `Pipe ${p.id}`),
        h("p", null, p.item ? `Water moves while ${itemName(p.item)} runs or is open.` : "Not linked to a pump or valve."),
        p.flow_item ? h("p", null, `Flow meter: ${itemName(p.flow_item)} — ${L.reading(R, { item: p.flow_item, role: p.flow_role }).txt}`) : null);
    }
    const n = lay.nodes.find(q => q.id === D.sel.id);
    if (!n) return h("div");
    if (D.edit) return nodeForm(n);
    const r = n.item && R[n.item];
    const s = ["pump", "valve", "box", "sector"].includes(n.kind) ? L.eqState(R, n) : null;
    return h("div", { class: "card side-panel" }, h("h3", null, n.label || n.id),
      h("dl", { class: "kv" },
        h("dt", null, "Equipment"), h("dd", null, n.item ? itemName(n.item) : n.planned ? "planned, not installed yet" : "not linked"),
        s ? [h("dt", null, "State"), h("dd", null, h("span", { class: "pill " + s.cls }, s.word), " ", h("span", { class: "muted small" }, s.why))] : null,
        r ? [h("dt", null, "Asked by a program"), h("dd", null, r.commanded ? "yes" : "no"),
          h("dt", null, "Output read back"), h("dd", null, r.coil), h("dt", null, "Feedback"), h("dd", null, r.feedback)] : null,
        r ? Object.entries(r.values).flatMap(([k, x]) => [h("dt", null, x.label || k),
          h("dd", null, x.value == null ? (x.quality === "NO CHANNEL" ? "no channel" : "no reading") : `${typeof x.value === "boolean" ? (x.value ? "on" : "off") : x.value}${x.unit ? " " + x.unit : ""} (${String(x.quality).toLowerCase()})`)]) : null));
  }
  const itemName = id => { const it = D.data.items.find(i => i.item_id === id); return it ? `${it.name} (${id})` : `${id} (not in the equipment)`; };

  // ---------------------------------------------------------------- the editor
  function startEdit() { D.edit = clone(D.data.layout); D.dirty = false; D.sel = null; D.pipeDraft = null; stop(); D.at = null; draw(); }
  function leaveEdit() { D.edit = null; D.dirty = false; D.sel = null; D.pipeDraft = null; D.msg = {}; D.lastFetch = 0; draw(); }
  function newId(prefix, list) { let i = 1; while (list.some(q => q.id === `${prefix}-${i}`)) i++; return `${prefix}-${i}`; }
  function add(kind) {
    const n = { id: newId(kind, D.edit.nodes), kind, x: 60, y: 60, label: kind === "label" ? "Text" : KINDS.find(k => k[0] === kind)[1], sub: "",
      item: null, role: null, tag: kind === "sensor" ? "F" : "", dir: "right", vertical: false, capacity_L: kind === "tank" ? 100 : null, planned: false,
      w: ["tank", "sector", "box"].includes(kind) ? (kind === "tank" ? 100 : 160) : null, h: ["tank", "sector", "box"].includes(kind) ? (kind === "sector" ? 104 : kind === "box" ? 50 : 100) : null };
    D.edit.nodes.push(n); D.sel = { kind: "node", id: n.id }; D.dirty = true; draw();
  }
  function editBar() {
    const ed = can("equipment_edit");
    return h("div", { class: "edit-bar" },
      h("div", { class: "row" }, h("span", { class: "small" }, "Add:"), KINDS.map(([k, w]) => h("button", { class: "small ghost", onclick: () => add(k) }, w)),
        D.pipeDraft ? h("button", { class: "small", onclick: () => { if (D.pipeDraft.points.length < 2) D.edit.pipes = D.edit.pipes.filter(p => p !== D.pipeDraft); D.sel = { kind: "pipe", id: D.pipeDraft.id }; D.pipeDraft = null; draw(); } }, "Finish pipe")
          : h("button", { class: "small ghost", onclick: () => { D.pipeDraft = { id: newId("pipe", D.edit.pipes), points: [], item: null, flow_item: null, flow_role: null }; D.sel = null; draw(); } }, "Pipe"),
        D.pipeDraft ? h("span", { class: "small warn" }, `Click on the grid for each corner of the pipe (${D.pipeDraft.points.length} so far), then Finish pipe.`) : null),
      h("div", { class: "row" },
        h("button", { disabled: !ed || !D.dirty, onclick: save }, "Save in the twin"),
        h("button", { class: "ghost", onclick: () => { if (D.dirty && D.msg.leave == null) { say("leave", "Unsaved changes: press Close again to drop them.", true); return; } leaveEdit(); } }, "Close editor"),
        msgEl("save"), msgEl("leave")));
  }
  async function save() {
    const r = await call("save", "PUT", "api/twin/layout", { layout: D.edit }, "saved in the twin");
    if (r) { D.dirty = false; await load(); say("save", r.issues.length ? `saved; ${r.issues.length} symbol(s) linked to equipment that does not exist` : "saved in the twin"); }
  }
  function field(label, input) { return h("label", { class: "small field" }, label, " ", input); }
  function txt(obj, key, max, after) {
    return h("input", { value: obj[key] == null ? "" : obj[key], maxlength: max || 80, onchange: ev => { obj[key] = ev.target.value; if (after) after(ev.target.value); D.dirty = true; draw(); } });
  }
  function numIn(obj, key) {
    return h("input", { type: "number", step: "5", class: "narrow-num", value: obj[key] == null ? "" : obj[key], onchange: ev => { obj[key] = ev.target.value === "" ? null : Number(ev.target.value); D.dirty = true; draw(); } });
  }
  function itemSelect(obj, key, after) {
    return h("select", { onchange: ev => { obj[key] = ev.target.value || null; if (after) after(); D.dirty = true; draw(); } },
      h("option", { value: "" }, "— not linked —"),
      D.data.items.map(i => h("option", { value: i.item_id, selected: obj[key] === i.item_id }, `${i.name} (${i.item_id})`)),
      obj[key] && !D.data.items.some(i => i.item_id === obj[key]) ? h("option", { value: obj[key], selected: true }, `${obj[key]} (not in the equipment)`) : null);
  }
  function roleSelect(obj, itemKey, key) {
    const it = D.data.items.find(i => i.item_id === obj[itemKey]);
    return h("select", { disabled: !it, onchange: ev => { obj[key] = ev.target.value || null; D.dirty = true; draw(); } },
      h("option", { value: "" }, "first measured input"),
      (it ? it.inputs : []).map(r => h("option", { value: r.role, selected: obj[key] === r.role }, `${r.label}${r.unit ? " (" + r.unit + ")" : ""}`)));
  }
  function nodeForm(n) {
    const sized = ["tank", "sector", "box"].includes(n.kind), linked = n.kind !== "label";
    return h("div", { class: "card side-panel" }, h("h3", null, `${KINDS.find(k => k[0] === n.kind)[1]}: ${n.label || n.id}`),
      field("Text", txt(n, "label")),
      ["sector", "box"].includes(n.kind) ? field("Second line", txt(n, "sub", 120)) : null,
      n.kind === "sensor" ? field("Letter", h("select", { onchange: ev => { n.tag = ev.target.value; D.dirty = true; draw(); } }, ["L", "F", "P", "pH", "EC", "T"].map(t => h("option", { selected: n.tag === t }, t)))) : null,
      linked ? field("Equipment", itemSelect(n, "item", () => { n.role = null; if (n.item) n.planned = false; })) : null,
      ["sensor", "tank"].includes(n.kind) ? field("Shows", roleSelect(n, "item", "role")) : null,
      linked ? h("label", { class: "small field" }, h("input", { type: "checkbox", checked: n.planned, disabled: !!n.item, onchange: ev => { n.planned = ev.target.checked; D.dirty = true; draw(); } }), " Planned (not installed yet)") : null,
      n.kind === "pump" ? field("Direction", h("select", { onchange: ev => { n.dir = ev.target.value; D.dirty = true; draw(); } }, ["right", "left", "down", "up"].map(d => h("option", { selected: n.dir === d }, d)))) : null,
      n.kind === "valve" ? h("label", { class: "small field" }, h("input", { type: "checkbox", checked: n.vertical, onchange: ev => { n.vertical = ev.target.checked; D.dirty = true; draw(); } }), " Vertical") : null,
      n.kind === "tank" ? field("Capacity (L)", numIn(n, "capacity_L")) : null,
      h("div", { class: "row" }, field("x", numIn(n, "x")), field("y", numIn(n, "y")), sized ? [field("width", numIn(n, "w")), field("height", numIn(n, "h"))] : null),
      h("div", { class: "row" },
        h("button", { class: "small ghost", onclick: () => { const c = clone(n); c.id = newId(n.kind, D.edit.nodes); c.x += 20; c.y += 20; D.edit.nodes.push(c); D.sel = { kind: "node", id: c.id }; D.dirty = true; draw(); } }, "Duplicate"),
        h("button", { class: "small danger", onclick: () => { D.edit.nodes = D.edit.nodes.filter(q => q !== n); D.sel = null; D.dirty = true; draw(); } }, "Remove from the diagram")),
      h("p", { class: "muted small" }, "Removing a symbol changes the drawing only. Equipment itself is created and retired on the Equipment page."));
  }
  function pipeForm(p) {
    return h("div", { class: "card side-panel" }, h("h3", null, `Pipe ${p.id}`),
      field("Water moves while this runs or is open", itemSelect(p, "item")),
      field("Flow meter on this pipe", itemSelect(p, "flow_item", () => { p.flow_role = null; })),
      field("Its flow input", roleSelect(p, "flow_item", "flow_role")),
      h("p", { class: "muted small" }, `${p.points.length} corners. Drag a round handle to move a corner.`),
      h("div", { class: "row" },
        h("button", { class: "small ghost", onclick: () => { const a = p.points[p.points.length - 1]; p.points.push([a[0] + 40, a[1]]); D.dirty = true; draw(); } }, "Add a corner"),
        h("button", { class: "small ghost", disabled: p.points.length <= 2, onclick: () => { p.points.pop(); D.dirty = true; draw(); } }, "Remove the last corner"),
        h("button", { class: "small danger", onclick: () => { D.edit.pipes = D.edit.pipes.filter(q => q !== p); D.sel = null; D.dirty = true; draw(); } }, "Remove pipe")));
  }

  // ---------------------------------------------------------------- the layout and the real system
  function layoutBar() {
    const ed = can("equipment_edit"), c = D.data.changes, same = D.data.digest === D.data.live_digest;
    const count = c.added.length + c.removed.length + c.moved.length + c.changed.length + c.pipes_added.length + c.pipes_removed.length + c.pipes_changed.length;
    const note = h("input", { maxlength: 500, placeholder: "what changed and why (optional)", "aria-label": "Note for the approver" });
    const list = (w, xs) => xs.length ? h("li", null, `${w}: ${xs.slice(0, 8).join(", ")}${xs.length > 8 ? ` and ${xs.length - 8} more` : ""}`) : null;
    return h("div", { class: "card" }, h("h3", null, "Layout"),
      h("div", { class: "row" },
        h("button", { disabled: !ed, onclick: startEdit }, "Edit layout"),
        h("button", { class: "ghost small", disabled: !ed, onclick: async () => { if (await call("start", "POST", "api/twin/layout/start-from", { source: "live" }, "the twin now uses the real system's layout")) { await load(); draw(); } } }, "Start again from the real system's layout"),
        h("button", { class: "ghost small", disabled: !ed, onclick: async () => { if (await call("start", "POST", "api/twin/layout/start-from", { source: "default" }, "the twin now uses the standard drawing")) { await load(); draw(); } } }, "Start from the standard drawing"),
        msgEl("start")),
      same ? h("p", { class: "muted small" }, "The twin's layout is the same as the real system's.")
        : h("div", null, h("p", { class: "small" }, `Different from the real system's layout (${count} change${count === 1 ? "" : "s"}):`),
          h("ul", { class: "small" }, list("added", c.added), list("removed", c.removed), list("moved", c.moved), list("changed", c.changed),
            list("pipes added", c.pipes_added), list("pipes removed", c.pipes_removed), list("pipes changed", c.pipes_changed)),
          h("div", { class: "row" }, note, h("button", { disabled: !ed, onclick: () => call("send", "POST", "api/twin/layout/send", { note: note.value }, "sent: an administrator or an operator with commissioning accepts it on the real dashboard (Overview)") }, "Send to the real system"), msgEl("send"))),
      h("p", { class: "muted small" }, "The layout is edited in the twin only. The real system draws it after it is accepted there; it changes the drawing, never equipment, channels or programs."));
  }

  window.AqTwinDiagram = { card, update };
})();
