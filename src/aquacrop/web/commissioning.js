/* AquaCrop Hardware → Add module and Discovery pages (0.5.0). Loaded after app.js, built on window.AqUI.
 * The wizard prepares one new module on the controller's USB-to-RS485 adapter; every step is checked again by
 * the server. All text is inserted with textContent. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, act, pill, fmtTime, can, mount, toast, S, VIEWS } = U;
  const STATE_CLS = { PREPARED: "ok", DETECTED: "ok", UNCERTAIN: "bad", FAILED: "bad", WRITING: "warn", RESERVED: "warn",
    CANCELLED: "" };
  const STEPS = ["OPEN", "READ", "IDENTIFIED", "RESERVED", "PREPARED", "DETECTED"];

  function diagram(bus) {
    return h("pre", { class: "small diagram-text", "aria-label": "Wiring for preparing a module" }, [
      "  CM4 controller",
      `   ├─ ${bus ? bus.bus_id + " (" + bus.port + ")" : "installation bus"} ── existing modules stay connected and polled`,
      "   └─ USB port ── USB-to-RS485 adapter ── A, B, GND ── NEW MODULE ONLY",
      "                                                       └─ its own 7-36 V supply (the adapter does not power it)",
    ].join("\n"));
  }

  function progress(t) {
    const i = STEPS.indexOf(t.state);
    return h("ol", { class: "steps small" }, STEPS.map((s, k) => h("li", { class: k < i ? "done" : k === i ? "now" : "" }, s.toLowerCase())));
  }

  function txCard(C, t) {
    const busy = !can("module_setup");
    const kids = [h("div", { class: "row" }, h("h3", null, t.identity ? t.identity.name : "New module"), pill(t.state, STATE_CLS[t.state]),
      h("span", { class: "muted small" }, `${t.adapter ? t.adapter.by_id || t.adapter.path : ""} → ${t.bus_id} · started ${fmtTime(t.created_utc)} by ${t.actor}`)),
      progress(t)];
    const last = t.log[t.log.length - 1];
    if (last && last.reason) kids.push(h("p", { class: "warn" }, last.reason));
    if (t.recovery) kids.push(h("div", { class: "card warn" }, h("strong", null, "Recovery"), h("ol", null, t.recovery.map(x => h("li", null, x))),
      h("button", { disabled: busy, onclick: () => act("POST", `/api/commissioning/${t.tx_id}/recheck`, undefined, "re-checked") }, "Re-check")));
    if (t.state === "OPEN") {
      const conf = h("input", { maxlength: 80, placeholder: C.confirmation_phrase, "aria-label": "Confirmation" });
      kids.push(h("p", null, "Wire only the new module to the adapter and power it. Two modules at the same address can look like one, so AquaCrop cannot check this for you."),
        h("div", { class: "row" }, h("label", null, `Type “${C.confirmation_phrase}” `, conf),
          h("button", { disabled: busy, onclick: () => act("POST", `/api/commissioning/${t.tx_id}/read`, { confirmation: conf.value }) }, "Read the module")));
    }
    if (t.state === "READ" || t.state === "IDENTIFIED") {
      const f = t.found && t.found[0];
      const name = h("input", { maxlength: 48, placeholder: "digital_C", "aria-label": "Name marked on the module" });
      const label = h("input", { maxlength: 48, placeholder: "same as the name", "aria-label": "Label" });
      const sku = h("select", { "aria-label": "SKU" }, (f && f.kind === "analog" ? [["25821", "25821 Analog Input 8CH"]] :
        [["26244", "26244 IO 8CH (transistor outputs)"], ["26517", "26517 Relay (D) (relay contacts)"]]).map(([v, l]) => h("option", { value: v }, l)));
      const variant = h("select", { "aria-label": "Analog version" }, h("option", { value: "" }, "—"), h("option", { value: "A" }, "A (0-5 V)"), h("option", { value: "B" }, "B (0-10 V)"));
      const range = h("select", { "aria-label": "Analog range" }, ["", "4-20mA", "0-20mA", "0-5V", "1-5V", "0-10V", "2-10V"].map(x => h("option", { value: x }, x || "—")));
      kids.push(h("p", null, f ? `One module answers at address ${f.address}; it reads as ${f.kind}. The bus cannot tell a Relay (D) from an IO 8CH: read the label.` : ""),
        h("div", { class: "row" }, h("label", null, "Name ", name), h("label", null, "Label confirmation ", label), h("label", null, "SKU ", sku),
          f && f.kind === "analog" ? [h("label", null, "Version ", variant), h("label", null, "Range, all channels ", range)] : null,
          h("button", { disabled: busy, onclick: () => act("POST", `/api/commissioning/${t.tx_id}/identity`, { name: name.value, sku: sku.value,
            label: label.value, ai_variant: variant.value || null, ai_range: range.value || null }, "identity recorded") }, "Save identity")));
    }
    if (t.state === "IDENTIFIED" || t.state === "RESERVED") {
      const addr = h("input", { type: "number", min: 1, max: 247, value: t.address || "", "aria-label": "Address" });
      kids.push(h("div", { class: "row" }, h("label", null, "Address on " + t.bus_id + " ", addr),
        h("button", { disabled: busy, onclick: async () => {
          try { const s = await api("GET", `/api/commissioning/${t.tx_id}/suggest`); addr.value = s.address; addr.dataset.dirty = "1"; toast(`Suggested ${s.address}: ${s.note}`); }
          catch (e) { toast(String(e.message), true); }
        } }, "Suggest"),
        h("button", { disabled: busy, onclick: () => act("POST", `/api/commissioning/${t.tx_id}/reserve`, { address: Number(addr.value) }, "address reserved") }, "Reserve")));
    }
    if (t.state === "RESERVED" && t.preview) {
      kids.push(h("div", { class: "card" }, h("strong", null, "Change preview"),
        h("p", null, `On ${t.preview.port}, to unit ${t.preview.unit} only:`), h("ul", null, t.preview.writes.map(w => h("li", null, w))),
        h("p", { class: "muted small" }, `Destination ${t.preview.destination}: ${t.preview.probe_at_destination}. Never sent: ${t.preview.never}.`),
        h("button", { class: "cmd", disabled: busy, onclick: () => { if (confirm("Write these registers to the module on the USB adapter?")) act("POST", `/api/commissioning/${t.tx_id}/commit`, { confirm: true }, "written and verified"); } }, "Write and verify")));
    }
    if (t.state === "PREPARED") {
      kids.push(h("div", { class: "card" }, h("strong", null, `Prepared at address ${t.address}. Move it to ${t.bus_id}:`),
        h("ol", null, (t.move_steps || C.move_steps).map(x => h("li", null, x))),
        h("button", { disabled: busy, onclick: () => act("POST", `/api/commissioning/${t.tx_id}/detect`, undefined) }, "Detect on " + t.bus_id)));
    }
    if (t.state === "DETECTED") {
      kids.push(h("p", null, `Found at ${t.address} on ${t.bus_id}. A configuration proposal with this module was written on the controller; its channels start unassigned and unverified. Install it as the upgrade guide describes. Nothing was switched on.`));
    }
    if (["OPEN", "READ", "IDENTIFIED", "RESERVED", "PREPARED"].includes(t.state))
      kids.push(h("button", { class: "ghost small", disabled: busy, onclick: () => act("POST", `/api/commissioning/${t.tx_id}/cancel`, { reason: "cancelled in the dashboard" }, "cancelled") },
        t.state === "PREPARED" ? "Release the adapter" : "Cancel"));
    return h("div", { class: "card" }, kids);
  }

  VIEWS["hw-add"] = async function () {
    const C = await api("GET", "/api/commissioning");
    if (!C.available) return mount("v-hw-add", h("div", { class: "card" }, h("h2", null, "Add module"), h("p", null, C.reason)));
    const bus = h("select", { "aria-label": "Destination bus" }, C.buses.map(b => h("option", { value: b.bus_id }, `${b.bus_id} (${b.port}): ${b.modules.map(m => m.device_uid + "@" + m.address).join(", ")}`)));
    const ad = h("select", { "aria-label": "Adapter" }, C.adapters.map(a => h("option", { value: a.stable_id, disabled: !a.selectable },
      `${a.by_id || a.path} · ${[a.manufacturer, a.product].filter(Boolean).join(" ")}${a.selectable ? "" : " — " + a.why_not}`)));
    const open = C.transactions.filter(t => !["CANCELLED", "FAILED", "DETECTED"].includes(t.state));
    const done = C.transactions.filter(t => ["CANCELLED", "FAILED", "DETECTED"].includes(t.state));
    mount("v-hw-add",
      h("div", { class: "card" }, h("h2", null, "Hardware → Add module"), h("p", null, C.where),
        diagram(C.buses[0]),
        C.adapters.length ? h("div", { class: "row" }, h("label", null, "Adapter ", ad), h("label", null, "Destination ", bus),
          h("button", { disabled: !can("module_setup"), onclick: () => act("POST", "/api/commissioning/open", { adapter: ad.value, bus_id: bus.value }, "adapter opened") }, "Start"),
          h("button", { class: "ghost", onclick: () => U.refresh() }, "Refresh list"))
          : h("p", { class: "warn" }, "No USB serial adapter is plugged into the controller."),
        h("p", { class: "muted small" }, "USB details identify the adapter, not the module wired to it. Preparing a module never switches an output and never writes to the installation buses.")),
      open.map(t => txCard(C, t)),
      done.length ? h("details", { class: "card" }, h("summary", null, `Finished (${done.length})`), done.map(t => txCard(C, t))) : null);
  };

  VIEWS.discovery = async function () {
    const D = await api("GET", "/api/discovery");
    if (!D.available) return mount("v-discovery", h("div", { class: "card" }, h("h2", null, "Discovery"), h("p", null, D.reason)));
    const CLS = { KNOWN: "ok", NEW: "warn", MISSING: "bad", CHANGED: "bad" };
    mount("v-discovery", h("div", { class: "card" }, h("h2", null, "Discovery (read only)"),
      h("p", { class: "muted" }, "Reads one address per poll on each bus, only while the regular poll has time to spare. A new device gets no channels and no output permission.")),
      Object.entries(D.buses).map(([bid, b]) => h("div", { class: "card" },
        h("div", { class: "row" }, h("h3", null, bid), b.full_sweep ? pill(`sweeping, at ${b.cursor}`, "warn") : pill(b.last_full_utc ? "last sweep " + fmtTime(b.last_full_utc) : "background"),
          h("button", { disabled: !can("module_setup") || b.full_sweep, onclick: () => act("POST", "/api/discovery/sweep", { bus_id: bid }, "sweep requested") }, `Scan ${b.range[0]}-${b.range[1]}`)),
        b.held ? h("p", { class: "warn small" }, b.held) : null,
        h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Address", "State", "Module", "Expected", "Seen"].map(x => h("th", null, x)))),
          h("tbody", null, b.rows.map(r => h("tr", null, h("td", null, r.address), h("td", null, pill(r.status, CLS[r.status])),
            h("td", null, r.device_uid || r.note || ""), h("td", null, r.expected_kind || "—"), h("td", null, r.seen_kind || "—"))))),
        h("p", { class: "muted small" }, b.limits))));
  };
})();
