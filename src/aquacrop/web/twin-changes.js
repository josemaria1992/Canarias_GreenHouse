/* The twin's simulation channels (0.6.0; reduced to this by the owner's decisions of 26 October 2026, 0.7.1).
 * Equipment is created and configured on the main dashboard's Equipment page; the twin places it in the drawing
 * (Installation design) and exports with Export to Overview there. Here each item can be wired to a channel for the
 * simulation: a channel of a simulated module stays in the twin; a channel of a real module goes with an export as
 * PROPOSED, to be verified at the bench before any physical run. Results and refusals are text beside the buttons
 * (owner preference). Loaded after twin.js. */
"use strict";
(function () {
  const T = window.AqTwin, { h, api, can } = T;
  const C = { card: null, body: null, data: null, twin: null, msg: {}, open: null, last: 0, prog: null, progOpen: false,
    eqEl: null, progEl: null, expEl: null };
  const ICONS = ["pump", "valve", "tank", "level", "flow", "pressure", "temperature", "button", "lamp", "fan", "heater", "motor", "sensor", "switch", "drop", "gauge", "plug", "generic"];

  function say(k, text, bad) { C.msg[k] = { text, bad }; draw(); }
  const msg = k => C.msg[k] ? h("span", { class: C.msg[k].bad ? "error small" : "muted small", role: "status" }, C.msg[k].text) : null;
  async function call(k, path, body, ok) {
    try { const r = await api("POST", path, body); C.msg[k] = { text: ok || "done" }; await load(); draw(); return r; }
    catch (e) { say(k, String(e.message || e), true); return null; }
  }
  // the program editor is drawn apart: the equipment and export parts refresh every few seconds without touching it
  function sayProg(text, bad) { C.msg.prog = { text, bad }; drawProg(); }
  async function load() { C.data = await api("GET", "api/twin/changes"); }

  function card() {
    if (!C.card) {
      C.eqEl = h("div"); C.progEl = h("div"); C.expEl = h("div");
      C.body = h("div", null, C.eqEl, C.progEl, C.expEl);
      C.card = h("div", { class: "card", id: "twin-changes" }, C.body);
    }
    return C.card;
  }
  async function update(v) {
    C.twin = v;
    const ae = document.activeElement;
    const first = !C.data;
    if (C.open || (ae && (C.eqEl.contains(ae) || C.expEl.contains(ae)) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName))) return;
    if (Date.now() - C.last < 3000) return;
    C.last = Date.now();
    try { await load(); } catch (e) { return; }
    draw();
    if (first) drawProg();
  }

  // ---------------------------------------------------------------- equipment channels in the twin
  function typeOf(i) { return C.data.types.find(t => t.type_id === i.type_id && t.version === i.type_version); }
  function channelSelect(i, role) {
    const cur = i.bindings[role.role] || "";
    const used = new Set(C.data.items.flatMap(x => Object.entries(x.bindings).filter(([r]) => !(x.item_id === i.item_id && r === role.role)).map(([, p]) => p)));
    const fits = role.points || C.data.points;            // only the channels whose kind and interface suit the role
    return h("select", { "aria-label": `${i.name} ${role.label} channel`, disabled: !can("equipment_edit") || i.status === "RETIRED",
      onchange: ev => call("it-" + i.item_id, "api/twin/equipment/bind", { item_id: i.item_id, role: role.role, point: ev.target.value || null }, "channel changed in the twin") },
      h("option", { value: "" }, "— no channel —"),
      fits.filter(p => !used.has(p) || p === cur).map(p => h("option", { value: p, selected: p === cur }, p)),
      cur && !fits.includes(cur) ? h("option", { value: cur, selected: true }, `${cur} (does not suit)`) : null);
  }
  function equipmentTable() {
    const ed = can("equipment_edit");
    return h("div", null, h("h3", null, "Channels for the simulation"),
      h("p", { class: "muted small" }, "Equipment is created on the main dashboard's Equipment page and placed in the drawing above. Choose here which channel each item uses in the twin; a simulated module's channel stays in the twin."),
      h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Equipment", "Type", "Channels", ""].map(x => h("th", null, x)))),
        h("tbody", null, C.data.items.filter(i => i.status !== "RETIRED").map(i => {
          const t = typeOf(i);
          return h("tr", null,
            h("td", null, h("strong", null, i.name), " ", h("span", { class: "muted small" }, i.public_id || i.item_id)),
            h("td", { class: "small" }, t ? `${t.name} v${t.version}` : i.type_id),
            h("td", { class: "small" }, (t ? t.roles : []).map(r => h("div", null, `${r.label}: `, channelSelect(i, r)))),
            h("td", null, i.changed ? [h("span", { class: "pill warn" }, "CHANGED"), " ",
              h("button", { class: "small ghost", disabled: !ed, onclick: () => call("it-" + i.item_id, "api/twin/equipment/revert", { item_id: i.item_id }, "back to the real system's") }, "Undo in twin")] : null,
              msg("it-" + i.item_id)));
        }))));
  }

  function draw() {
    if (!C.body || !C.data) return;
    C.eqEl.replaceChildren(h("h2", null, "Equipment in the twin"),
      h("p", { class: "muted small" }, `Copied from the main dashboard on ${C.data.snapshot_utc ? new Date(C.data.snapshot_utc).toLocaleString() : "—"}. Import from Overview (above) brings its latest equipment.`),
      equipmentTable());
    C.expEl.replaceChildren();
  }
  function drawProg() { if (C.progEl) C.progEl.replaceChildren(); }
  window.AqTwinChanges = { card, update };
})();
