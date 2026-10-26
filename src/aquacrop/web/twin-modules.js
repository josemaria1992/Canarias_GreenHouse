/* The digital twin's simulated modules (0.7.0, MOD-01). Loaded after twin.js. A simulated module exists only in the
   twin: its channels appear in the twin's equipment pages for testing, the real system never sees it, and equipment
   wired to it cannot be sent to the real system. Adding or removing one starts a new twin run. */
"use strict";
(function () {
  const T = window.AqTwin, { h, api, can } = T;
  const M = { card: null, body: null, msg: null, last: 0, data: null };
  const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));
  function card() {
    if (!M.card) { M.body = h("div"); M.card = h("details", { class: "card", id: "twin-modules" }, h("summary", null, "Modules of this twin"), M.body); }
    return M.card;
  }
  async function send(path, body, ok) {
    try { await api("POST", path, body); M.msg = { text: ok }; }
    catch (e) { M.msg = { text: String(e.message || e), bad: true }; }
    M.last = 0;
    await update(true);
  }
  function draw() {
    const d = M.data;
    const name = h("input", { maxlength: 32, placeholder: "sim_relay_1", "aria-label": "Simulated module name" });
    const model = h("select", { "aria-label": "Module model" }, Object.entries(d.models).map(([k, t]) => h("option", { value: k }, t)));
    const label = h("input", { maxlength: 80, placeholder: "what it stands for (optional)", "aria-label": "Simulated module label" });
    const edit = can("equipment_edit");
    put(M.body,
      h("p", { class: "muted small" }, d.meaning),
      h("h3", null, "From the real system"),
      h("ul", { class: "small" }, d.real.map(m => h("li", null, `${m.device_uid}: ${m.label || m.model}`))),
      h("h3", null, "Simulated here only"),
      d.simulated.length ? h("table", { class: "compact small" }, h("tbody", null, d.simulated.map(m => h("tr", null,
        h("td", null, m.device_uid), h("td", null, d.models[m.model] || m.model), h("td", null, m.label),
        h("td", null, edit ? h("button", { class: "small ghost", onclick: () => send("api/twin/modules/remove", { device_uid: m.device_uid }, `${m.device_uid} removed; a new twin run started`) }, "Remove") : null)))))
        : h("p", { class: "muted small" }, "None yet."),
      edit ? h("div", { class: "row wrap" }, h("label", null, "Name", name), h("label", null, "Model", model), h("label", null, "Label", label),
        h("button", { onclick: () => send("api/twin/modules", { device_uid: name.value, model: model.value, label: label.value }, "Added; a new twin run started with its channels") }, "Create simulated module")) : null,
      M.msg ? h("p", { class: "inline-msg " + (M.msg.bad ? "bad" : "ok"), role: "status" }, M.msg.text) : null);
  }
  async function update(force) {
    if (!M.body || (!force && M.card && !M.card.open && M.data)) return;
    const ae = document.activeElement;
    if (!force && ae && M.card.contains(ae) && /^(INPUT|SELECT)$/.test(ae.tagName)) return;   // never under the user's typing
    const now = Date.now();
    if (!force && now - M.last < 5000) return;
    M.last = now;
    try { M.data = await api("GET", "api/twin/modules"); } catch (e) { return; }
    draw();
  }
  window.AqTwinModules = { card, update };
})();
