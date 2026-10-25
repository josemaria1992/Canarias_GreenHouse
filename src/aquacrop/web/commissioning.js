/* AquaCrop Hardware → Add module and Discovery pages (0.5.0). Loaded after app.js, built on window.AqUI.
 * The wizard prepares one new module on the controller's USB-to-RS485 adapter; every step is checked again by
 * the server. All text is inserted with textContent. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, pill, fmtTime, can, mount, S, VIEWS } = U;
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

  // Messages are shown on the page, next to the buttons that caused them (owner, 2026-10-24), not as pop-ups.
  const MSG = {};                       // key ("start", a transaction id, "scan:<bus>") -> {text, bad}
  function say(key, text, bad) { MSG[key] = { text, bad: !!bad }; }
  function note(key) {
    const m = MSG[key];
    return m ? h("span", { class: "inline-msg " + (m.bad ? "bad" : "ok"), role: "status" }, m.text) : null;
  }
  async function step(key, method, path, body, okText) {
    if (S.stale) { say(key, "Connection lost: nothing was sent.", true); U.refresh(); return null; }
    try {
      const r = await api(method, path, body);
      if (okText) say(key, okText);
      document.querySelectorAll("#v-hw-add [data-dirty]").forEach(f => { delete f.dataset.dirty; });
      U.refresh();
      return r;
    } catch (e) { say(key, String(e.message || e), true); U.refresh(); return null; }
  }

  function txCard(C, t) {
    const busy = !can("module_setup"), id = t.tx_id;
    const kids = [h("div", { class: "row" }, h("h3", null, t.identity ? t.identity.name : "New module"), pill(t.state, STATE_CLS[t.state]),
      h("span", { class: "muted small" }, `${t.adapter ? t.adapter.by_id || t.adapter.path : ""} → ${t.bus_id} · started ${fmtTime(t.created_utc)} by ${t.actor}`)),
      progress(t)];
    const last = t.log[t.log.length - 1];
    if (last && last.reason) kids.push(h("p", { class: "warn" }, last.reason));
    if (t.recovery) kids.push(h("div", { class: "card warn" }, h("strong", null, "Recovery"), h("ol", null, t.recovery.map(x => h("li", null, x))),
      h("div", { class: "row" }, h("button", { disabled: busy, onclick: () => step(id, "POST", `/api/commissioning/${id}/recheck`, undefined, "Re-checked.") }, "Re-check"), note(id))));
    if (t.state === "OPEN") {
      kids.push(h("div", { class: "row" }, h("span", null, "No single module was read. Check the wiring and the supply, then read it again."),
        h("button", { disabled: busy, onclick: () => step(id, "POST", `/api/commissioning/${id}/read`, { confirmation: C.confirmation_phrase }, "Read again.") }, "Read the module"), note(id)));
    }
    if (t.state === "READ" || t.state === "IDENTIFIED" || t.state === "RESERVED") {
      const f = t.found && t.found[0], ident = t.identity || {};
      const analog = !!(f && f.kind === "analog");
      const name = h("input", { maxlength: 48, value: ident.name || "", placeholder: analog ? "analog_C" : "digital_C", "aria-label": "Name marked on the module" });
      const sku = h("select", { "aria-label": "SKU" }, (analog ? [["25821", "25821 Analog Input 8CH"]] :
        [["26244", "26244 IO 8CH (transistor outputs)"], ["26517", "26517 Relay (D) (relay contacts)"]]).map(([v, l]) => h("option", { value: v, selected: ident.sku === v }, l)));
      const variant = h("select", { "aria-label": "Analog version" }, h("option", { value: "A", selected: ident.ai_variant !== "B" }, "A (0-5 V)"), h("option", { value: "B", selected: ident.ai_variant === "B" }, "B (0-10 V)"));
      const range = h("select", { "aria-label": "Analog range" }, ["4-20mA", "0-20mA", "0-5V", "1-5V", "0-10V", "2-10V"].map(x => h("option", { value: x }, x)));
      const addr = h("input", { type: "number", min: 1, max: 247, value: t.address || MSG[id + ":suggested"] || "", "aria-label": "Address", class: "narrow-num" });
      const identity = () => step(id, "POST", `/api/commissioning/${id}/identity`, { name: name.value, sku: sku.value, label: name.value,
        ai_variant: analog ? variant.value : null, ai_range: analog ? range.value : null });
      const suggest = async () => {
        try {
          const sg = await api("GET", `/api/commissioning/${id}/suggest`);
          MSG[id + ":suggested"] = sg.address; addr.value = sg.address; addr.dataset.dirty = "1";
          const taken = Object.entries(sg.taken).map(([a, w]) => `${a} ${w.replace("configured: ", "")}`).join(", ") || "none";
          say(id, `Suggested address ${sg.address}: nothing answered there on ${t.bus_id} just now (a switched-off module could still use it). In use: ${taken}.`);
        } catch (e) { say(id, String(e.message || e), true); }
        U.refresh();
      };
      kids.push(h("div", { class: "card" }, h("h4", null, "Configuration"),
        h("p", { class: "small" }, f ? `One module answers on the adapter at address ${f.address} and reads as ${f.kind}. The bus cannot tell a Relay (D) from an IO 8CH: choose the SKU printed on its label.` : ""),
        h("div", { class: "row" }, h("label", null, "Name ", name), h("label", null, "SKU ", sku),
          analog ? [h("label", null, "Version ", variant), h("label", null, "Range, all channels ", range)] : null),
        h("div", { class: "row" }, h("label", null, `Address on ${t.bus_id} `, addr),
          h("button", { disabled: busy, onclick: suggest }, "Suggest"),
          h("button", { class: "cmd", disabled: busy, onclick: async () => {
            if (!(await identity())) return;
            await step(id, "POST", `/api/commissioning/${id}/reserve`, { address: Number(addr.value) }, `Address ${addr.value} reserved. Check the change below, then write it.`);
          } }, t.state === "RESERVED" ? "Save again" : "Reserve address"),
          note(id))));
    }
    if (t.state === "RESERVED" && t.preview) {
      kids.push(h("div", { class: "card" }, h("h4", null, "Change to write"),
        h("p", null, `On ${t.preview.port}, to the module at address ${t.preview.unit} only:`), h("ul", null, t.preview.writes.map(w => h("li", null, w))),
        h("p", { class: "muted small" }, `Never sent: ${t.preview.never}.`),
        h("div", { class: "row" }, h("button", { class: "cmd", disabled: busy, onclick: () => step(id, "POST", `/api/commissioning/${id}/commit`, { confirm: true }, "Written and verified: the module now answers at its new address only.") }, "Write and verify"), note(id))));
    }
    if (t.state === "PREPARED") {
      kids.push(h("div", { class: "card" }, h("strong", null, `Prepared at address ${t.address}. Move it to ${t.bus_id}:`),
        h("ol", null, (t.move_steps || C.move_steps).map(x => h("li", null, x))),
        h("div", { class: "row" }, h("button", { class: "cmd", disabled: busy, onclick: () => step(id, "POST", `/api/commissioning/${id}/detect`, undefined, "Detected on the bus.") }, "Detect on " + t.bus_id), note(id))));
    }
    if (t.state === "DETECTED") {
      kids.push(h("p", null, `Found at ${t.address} on ${t.bus_id}. A configuration proposal with this module was written on the controller; its channels start unassigned and unverified. Install it as the upgrade guide describes. Nothing was switched on.`));
    }
    if (["OPEN", "READ", "IDENTIFIED", "RESERVED", "PREPARED"].includes(t.state))
      kids.push(h("button", { class: "ghost small", disabled: busy, onclick: () => step(id, "POST", `/api/commissioning/${id}/cancel`, { reason: "cancelled in the dashboard" }, "Cancelled.") },
        t.state === "PREPARED" ? "Release the adapter" : "Cancel"));
    return h("div", { class: "card" }, kids);
  }

  VIEWS["hw-add"] = async function () {
    const C = await api("GET", "/api/commissioning");
    if (!C.available) return mount("v-hw-add", h("div", { class: "card" }, h("h2", null, "Add module"), h("p", null, C.reason)));
    const bus = h("select", { "aria-label": "Destination port" }, C.buses.map(b => h("option", { value: b.bus_id }, `${b.bus_id} (${b.port}): ${b.modules.map(m => m.device_uid + "@" + m.address).join(", ") || "no modules yet"}`)));
    const ad = h("select", { "aria-label": "Adapter" }, C.adapters.map(a => h("option", { value: a.stable_id, disabled: !a.selectable },
      `${a.by_id || a.path} · ${[a.manufacturer, a.product].filter(Boolean).join(" ")}${a.selectable ? "" : " — " + a.why_not}`)));
    const open = C.transactions.filter(t => !["CANCELLED", "FAILED", "DETECTED"].includes(t.state));
    const done = C.transactions.filter(t => ["CANCELLED", "FAILED", "DETECTED"].includes(t.state));
    mount("v-hw-add",
      h("div", { class: "card" }, h("h2", null, "How to add a module"),
        h("ol", null,
          h("li", null, "Plug the USB-to-RS485 adapter into a USB port of the controller (the Raspberry CM4), NOT into this PC."),
          h("li", null, "Wire the adapter to the new module: A to A, B to B, and GND to GND."),
          h("li", null, "Check what supply the new module needs and power it up (our modules use 7-36 V). The adapter does not power it."),
          h("li", null, "Select the adapter and the destination port below."),
          h("li", null, "Press Start."),
          h("li", null, "Follow the instructions in the panel that opens.")),
        diagram(C.buses[0]),
        C.adapters.length ? h("div", { class: "row" }, h("label", null, "Adapter ", ad), h("label", null, "Destination port ", bus),
          h("button", { class: "cmd", disabled: !can("module_setup"), onclick: () => step("start", "POST", "/api/commissioning/open", { adapter: ad.value, bus_id: bus.value, read: true }) }, "Start"),
          h("button", { class: "ghost", onclick: () => { say("start", "Adapter list refreshed."); U.refresh(); } }, "Refresh list"), note("start"))
          : h("p", { class: "warn" }, "No USB-to-RS485 adapter is plugged into the controller. Plug it in, then refresh this page."),
        h("p", { class: "muted small" }, "Only the new module may be wired to the adapter: two modules at the same address can look like one. Preparing a module never switches an output and never writes to the installation buses.")),
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
          h("button", { disabled: !can("module_setup") || b.full_sweep, onclick: () => step("scan:" + bid, "POST", "/api/discovery/sweep", { bus_id: bid }, "Scan started: one address per poll.") }, `Scan ${b.range[0]}-${b.range[1]}`), note("scan:" + bid)),
        b.held ? h("p", { class: "warn small" }, b.held) : null,
        h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Address", "State", "Module", "Expected", "Seen"].map(x => h("th", null, x)))),
          h("tbody", null, b.rows.map(r => h("tr", null, h("td", null, r.address), h("td", null, pill(r.status, CLS[r.status])),
            h("td", null, r.device_uid || r.note || ""), h("td", null, r.expected_kind || "—"), h("td", null, r.seen_kind || "—"))))),
        h("p", { class: "muted small" }, b.limits))));
  };
})();
