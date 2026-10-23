/* AquaCrop equipment pages (0.5.0): equipment types, individual equipment, and the channel of each role.
 * Loaded after app.js and built on window.AqUI. All text is inserted with textContent. Icons are drawn from the
 * built-in set below; an icon never decides what a channel does. A chosen channel is PROPOSED until the same
 * server-side check as on the I/O board plus your observation make it VERIFIED. Nothing on these pages sends
 * anything to a module. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, act, pill, fmtTime, can, mount, toast, S, VIEWS } = U;
  const NS = "http://www.w3.org/2000/svg";

  // 24x24 outline glyphs, one path list each (stroke only).
  const GLYPHS = {
    pump: ["M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16", "M12 4h8", "M9 9l6 3l-6 3z"],
    valve: ["M3 8v8l9-4z", "M21 8v8l-9-4z", "M12 12V5", "M8 5h8"],
    tank: ["M5 4h14v16H5z", "M5 13h14"],
    level: ["M7 3h10v18H7z", "M7 14h10", "M4 14h2", "M18 14h2"],
    flow: ["M3 12h14", "M13 8l4 4l-4 4", "M3 7h6", "M3 17h6"],
    pressure: ["M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16", "M12 12l4-4"],
    temperature: ["M10 4h4v10a4 4 0 1 1-4 0z", "M12 9v7"],
    button: ["M6 10h12v6H6z", "M9 10V7h6v3"],
    lamp: ["M9 18h6", "M10 21h4", "M12 3a6 6 0 0 0-3 11v2h6v-2a6 6 0 0 0-3-11"],
    fan: ["M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0", "M12 11c0-4 2-7 4-7s1 5-4 7", "M13 12c4 0 7 2 7 4s-5 1-7-4",
      "M12 13c0 4-2 7-4 7s-1-5 4-7", "M11 12c-4 0-7-2-7-4s5-1 7 4"],
    heater: ["M5 6h14v12H5z", "M8 9v6", "M12 9v6", "M16 9v6"],
    motor: ["M4 7h12v10H4z", "M16 12h5", "M7 7V5h6v2"],
    sensor: ["M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "M7 7a7 7 0 0 0 0 10", "M17 7a7 7 0 0 1 0 10"],
    switch: ["M4 12h5", "M15 12h5", "M9 12l6-4"],
    drop: ["M12 3c3 5 6 8 6 11a6 6 0 0 1-12 0c0-3 3-6 6-11"],
    gauge: ["M4 16a8 8 0 1 1 16 0", "M12 16l4-5"],
    plug: ["M9 3v5", "M15 3v5", "M6 8h12v4a6 6 0 0 1-12 0z", "M12 18v3"],
    generic: ["M5 5h14v14H5z"],
  };
  function icon(name, size) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", size || 28); svg.setAttribute("height", size || 28);
    svg.setAttribute("class", "eq-icon"); svg.setAttribute("aria-hidden", "true");
    for (const d of GLYPHS[name] || GLYPHS.generic) {
      const p = document.createElementNS(NS, "path"); p.setAttribute("d", d); svg.appendChild(p);
    }
    return svg;
  }
  const STATUS_CLS = { ACTIVE: "ok", DRAFT: "warn", SUPERSEDED: "", RETIRED: "", VERIFIED: "ok", PROPOSED: "warn" };

  // ---------------------------------------------------------------- equipment items
  function typeOf(E, it) { return E.types.find(t => t.type_id === it.type_id && t.version === it.type_version); }
  function activeTypes(E) { return E.types.filter(t => t.status === "ACTIVE"); }
  function channels(kind) {
    const st = S.status; if (!st || !st.devices) return [];
    const out = [];
    for (const d of st.devices) for (const p of (d.points || [])) if (!kind || p.kind === kind) out.push(p.point || p.id || p);
    return out;
  }
  const KIND = { "OUTPUT/DIGITAL": "DO", "INPUT/DIGITAL": "DI", "INPUT/ANALOG": "AI" };

  function roleRow(E, it, role, board) {
    const b = E.bindings.find(x => x.item_id === it.item_id && x.role === role.role);
    const kind = KIND[role.direction + "/" + role.signal];
    const opts = (board || []).filter(p => p.kind === kind).map(p => {
      const taken = E.bindings.find(x => x.point === p.point && !(x.item_id === it.item_id && x.role === role.role));
      return h("option", { value: p.point, disabled: !!taken }, p.point + (taken ? ` (${taken.item_id})` : ""));
    });
    const sel = h("select", { "aria-label": `Channel for ${it.name} ${role.label}`, disabled: !can("io_assign") },
      h("option", { value: "" }, "— choose a channel —"), opts);
    if (b) sel.value = b.point;
    const obs = h("input", { placeholder: "what you saw the device do", maxlength: 300, "aria-label": "Observation" });
    const ref = kind === "AI" ? h("input", { type: "number", step: "any", placeholder: "applied mA/V", "aria-label": "Reference" }) : null;
    const tol = kind === "AI" ? h("input", { type: "number", step: "any", placeholder: "tolerance", "aria-label": "Tolerance" }) : null;
    const verify = cite => act("POST", "/api/equipment/bind/verify", { item_id: it.item_id, role: role.role, observation: obs.value,
      reference: ref && ref.value !== "" ? Number(ref.value) : null, tolerance: tol && tol.value !== "" ? Number(tol.value) : null,
      cite_assignment: cite }, "channel verified");
    return h("tr", null,
      h("td", null, role.label, role.required ? "" : h("span", { class: "muted small" }, " (optional)")),
      h("td", null, `${role.direction.toLowerCase()} ${role.signal.toLowerCase()} · ${role.purpose.toLowerCase()}`),
      h("td", null, sel, " ", h("button", { class: "small", disabled: !can("io_assign"), onclick: () =>
        act("POST", "/api/equipment/bind", { item_id: it.item_id, role: role.role, point: sel.value || null }, sel.value ? "channel proposed" : "channel cleared") },
        sel.value || !b ? "Set" : "Clear")),
      h("td", null, b ? pill(b.status, STATUS_CLS[b.status]) : pill("NO CHANNEL", role.required ? "bad" : "")),
      h("td", null, b && b.status !== "VERIFIED" && can("io_assign") ? h("div", { class: "row" }, obs, ref, tol,
        h("button", { class: "small", onclick: () => verify(false) }, "Verify"),
        h("button", { class: "small ghost", title: "Use this channel's VERIFIED I/O-board assignment as the evidence", onclick: () => verify(true) }, "Cite I/O board check"))
        : b && b.status === "VERIFIED" ? h("span", { class: "muted small" }, `by ${b.actor}, ${fmtTime(b.utc)}`) : null));
  }

  function itemCard(E, it, board) {
    const t = typeOf(E, it), r = E.readiness[it.item_id];
    const latest = E.types.filter(x => x.type_id === it.type_id && x.status === "ACTIVE")[0];
    const roles = t ? t.body.roles : [];
    const props = t ? t.body.properties || [] : [];
    return h("div", { class: "card eq-item" },
      h("div", { class: "row" }, icon(it.icon), h("h3", null, it.name),
        pill(it.status, STATUS_CLS[it.status]), h("span", { class: "muted" }, `${t ? t.name : it.type_id} v${it.type_version} · equipment v${it.version}`),
        r ? (r.ready ? pill("READY FOR PROGRAMS", "ok") : pill("NOT READY", "warn")) : null),
      r && !r.ready ? h("p", { class: "muted small" }, [
        r.missing_required.length ? `No channel: ${r.missing_required.join(", ")}. ` : "",
        r.unverified.length ? `Not verified: ${r.unverified.join(", ")}. ` : "",
        r.unscaled.length ? `No scaling: ${r.unscaled.join(", ")}.` : ""].join("")) : null,
      latest && latest.version !== it.type_version ? h("p", { class: "warn small" }, `Type v${latest.version} is active; this item stays on v${it.type_version} until you upgrade it.`,
        " ", h("button", { class: "small", disabled: !can("equipment_edit"), onclick: () => upgrade(it, latest.version) }, "Preview upgrade")) : null,
      props.length ? h("p", { class: "small" }, props.map(p => `${p.label}: ${it.body.properties[p.key] ?? p.default ?? "—"}${p.unit ? " " + p.unit : ""}`).join(" · ")) : null,
      h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Role", "Signal", "Channel", "State", "Check"].map(x => h("th", null, x)))),
        h("tbody", null, roles.map(role => roleRow(E, it, role, board)))),
      h("p", { class: "muted small" }, "Commanded state, coil readback and independent feedback are shown separately on the twin and bench pages; a missing feedback is UNKNOWN, never OFF."),
      it.status === "ACTIVE" && can("equipment_activate") ? h("button", { class: "small ghost", onclick: () => {
        const why = prompt(`Retire ${it.name}? Its history and records are kept. Reason:`); if (why) act("POST", `/api/equipment/items/${it.item_id}/retire`, { reason: why }, "retired");
      } }, "Retire") : null);
  }

  async function upgrade(it, to) {
    const p = await api("GET", `/api/equipment/items/${it.item_id}/upgrade?to_version=${to}`).catch(e => { toast(String(e.message), true); return null; });
    if (!p) return;
    const lines = [`Upgrade ${it.name} from type v${p.from} to v${p.to}?`,
      p.roles_added.length ? `Roles added: ${p.roles_added.join(", ")}` : null,
      p.roles_removed.length ? `Roles removed: ${p.roles_removed.join(", ")}` : null,
      p.bindings_cleared.length ? `Channels cleared: ${p.bindings_cleared.join(", ")}` : null,
      p.bindings_back_to_proposed.length ? `Verify again: ${p.bindings_back_to_proposed.join(", ")}` : null,
      p.programs.length ? `Programs to revalidate: ${p.programs.join(", ")}` : null,
      p.item_issues.length ? `Problems: ${p.item_issues.join("; ")}` : null].filter(Boolean);
    if (p.item_issues.length) { alert(lines.join("\n")); return; }
    if (confirm(lines.join("\n"))) act("POST", `/api/equipment/items/${it.item_id}/upgrade`, { to_version: p.to, expected_version: it.version }, "upgraded");
  }

  function newItemCard(E) {
    const types = activeTypes(E);
    const tsel = h("select", { "aria-label": "Type" }, types.map(t => h("option", { value: t.type_id }, `${t.name} (v${t.version})`)));
    const name = h("input", { maxlength: 80, placeholder: "e.g. LED 1 on digital_A" });
    const id = h("input", { maxlength: 48, placeholder: "led-1", pattern: "[a-z0-9][a-z0-9_-]*" });
    const isel = h("select", { "aria-label": "Icon" }, E.icons.map(i => h("option", { value: i }, i)));
    const preview = h("span", null);
    const showIcon = () => preview.replaceChildren(icon(isel.value));
    isel.addEventListener("change", showIcon);
    tsel.addEventListener("change", () => { const t = types.find(x => x.type_id === tsel.value); if (t && !isel.dataset.dirty) { isel.value = t.body.default_icon; showIcon(); } });
    if (types[0]) isel.value = types[0].body.default_icon;
    showIcon();
    return h("div", { class: "card" }, h("h3", null, "New equipment"),
      types.length ? h("div", { class: "row" },
        h("label", null, "Type ", tsel), h("label", null, "Name ", name), h("label", null, "Id ", id), h("label", null, "Icon ", isel), preview,
        h("button", { disabled: !can("equipment_edit"), onclick: () => act("POST", "/api/equipment/items",
          { item_id: id.value, name: name.value, type_id: tsel.value, icon: isel.value, body: {} }, "equipment created") }, "Create"))
        : h("p", { class: "muted" }, "No active equipment type yet. Activate one on Equipment types first."),
      h("p", { class: "muted small" }, "Properties, scaling and alarm limits are edited after creation. The icon is only a picture."));
  }

  VIEWS.equipment = async function () {
    const [E, board] = await Promise.all([api("GET", "/api/equipment"), api("GET", "/api/board").catch(() => null)]);
    const pts = board ? board.modules.flatMap(m => m.points.map(p => ({ point: p.id, kind: p.kind }))) : [];
    const live = E.items.filter(i => i.status === "ACTIVE"), retired = E.items.filter(i => i.status === "RETIRED");
    mount("v-equipment",
      h("div", { class: "card" }, h("div", { class: "row" }, h("h2", null, "Equipment"),
        h("span", { class: "muted" }, `${live.length} items · ${E.bindings.filter(b => b.status === "VERIFIED").length} verified channels`),
        h("a", { href: "/api/equipment/export", download: "aquacrop-equipment.json" }, "Export")),
        h("p", { class: "muted" }, "Each item is made from an equipment type. Choose the channel of each role, then verify it with the device connected. A verified channel is still not an output permission: physical switching also needs the bench-test profile and an armed test session.")),
      newItemCard(E), live.map(it => itemCard(E, it, pts)),
      retired.length ? h("details", { class: "card" }, h("summary", null, `Retired (${retired.length})`), retired.map(it => h("p", null, `${it.name} · retired ${fmtTime(it.updated_utc)}`))) : null);
  };

  // ---------------------------------------------------------------- equipment types
  function typeEditor(E, t) {
    const ta = h("textarea", { rows: 16, cols: 90, spellcheck: "false", "aria-label": "Type definition (JSON)" });
    ta.value = JSON.stringify(t ? t.body : { category: "", default_icon: "generic", roles: [], operations: [], limits: {}, simulation: { behavior: "NOT_MODELLED" } }, null, 2);
    const id = h("input", { maxlength: 48, value: t ? t.type_id : "", disabled: !!t, "aria-label": "Type id" });
    const name = h("input", { maxlength: 80, value: t ? t.name : "", "aria-label": "Type name" });
    const save = () => {
      let body; try { body = JSON.parse(ta.value); } catch (e) { toast("The definition is not valid JSON: " + e.message, true); return; }
      act("POST", "/api/equipment/types/draft", { type_id: id.value, name: name.value, body, version: t && t.status === "DRAFT" ? t.version : null }, "draft saved");
    };
    return h("div", { class: "row col" }, h("div", { class: "row" }, h("label", null, "Id ", id), h("label", null, "Name ", name)), ta,
      h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit"), onclick: save }, t && t.status === "DRAFT" ? "Save draft" : "Save as new draft version"),
        h("span", { class: "muted small" }, "Roles: direction OUTPUT/INPUT, signal DIGITAL/ANALOG, purpose COMMAND/RUNNING/FAULT/OPEN/CLOSED/STATE/MEASUREMENT. Switchable types need limits.max_runtime_s.")));
  }
  function typeCard(E, t) {
    const users = E.items.filter(i => i.type_id === t.type_id && i.status === "ACTIVE");
    return h("div", { class: "card" },
      h("div", { class: "row" }, icon(t.body.default_icon), h("h3", null, t.name), pill(`${t.status} v${t.version}`, STATUS_CLS[t.status]),
        h("span", { class: "muted" }, `${t.body.category} · ${t.body.roles.length} roles · ${users.length} items`)),
      t.body.description ? h("p", { class: "small" }, t.body.description) : null,
      h("p", { class: "small" }, "Roles: ", t.body.roles.map(r => `${r.label} (${r.direction === "OUTPUT" ? "out" : "in"} ${r.signal.toLowerCase()}${r.required ? "" : ", optional"})`).join(" · ")),
      h("p", { class: "small" }, `Operations: ${(t.body.operations || []).join(", ") || "none"} · Twin behaviour: ${t.body.simulation.behavior}`),
      h("div", { class: "row" },
        t.status === "DRAFT" ? h("button", { disabled: !can("equipment_activate"), onclick: () => {
          const msg = users.length ? `Activate ${t.name} v${t.version}? ${users.length} item(s) stay on their current version until each is upgraded.` : `Activate ${t.name} v${t.version}?`;
          if (confirm(msg)) act("POST", "/api/equipment/types/activate", { type_id: t.type_id, version: t.version }, "type activated");
        } }, "Activate") : null,
        t.status === "ACTIVE" && can("equipment_activate") ? h("button", { class: "ghost small", onclick: () => {
          const why = prompt(`Retire ${t.name}? Reason:`); if (why) act("POST", `/api/equipment/types/${t.type_id}/retire`, { reason: why }, "type retired");
        } }, "Retire") : null),
      t.status === "ACTIVE" || t.status === "DRAFT" ? h("details", null, h("summary", null, t.status === "DRAFT" ? "Edit draft" : "Edit (creates the next draft version)"), typeEditor(E, t)) : null);
  }
  VIEWS["equipment-types"] = async function () {
    const E = await api("GET", "/api/equipment");
    const current = {};
    for (const t of E.types) if (t.status !== "SUPERSEDED") current[t.type_id + ":" + t.status] = t;
    const shown = Object.values(current).sort((a, b) => a.name.localeCompare(b.name));
    const imp = h("input", { type: "file", accept: "application/json", "aria-label": "Import equipment export" });
    imp.addEventListener("change", async () => {
      const f = imp.files[0]; if (!f) return;
      let doc; try { doc = JSON.parse(await f.text()); } catch (e) { toast("Not a JSON file", true); return; }
      const r = await act("POST", "/api/equipment/import", { doc }, "imported as drafts");
      if (r && r.note) toast(r.note);
    });
    mount("v-equipment-types",
      h("div", { class: "card" }, h("div", { class: "row" }, h("h2", null, "Equipment types"),
        h("button", { disabled: !can("equipment_edit"), onclick: () => act("POST", "/api/equipment/types/seed", undefined, "templates added as drafts") }, "Add built-in templates"),
        h("label", { class: "small" }, "Import ", imp)),
        h("p", { class: "muted" }, "A type defines the channels (roles) an item needs, its properties, limits, permitted operations and how the twin simulates it. Templates arrive as drafts you review and activate; activating a new version never changes existing items until each is upgraded.")),
      shown.map(t => typeCard(E, t)),
      h("details", { class: "card" }, h("summary", null, "New type"), typeEditor(E, null)));
  };
})();
