/* AquaCrop equipment pages (0.5.0): equipment types, individual equipment, and the channel of each role.
 * 0.7.1 (owner decisions): Equipment is the one page where equipment is created and configured, for operators and
 * administrators; Digital Twin only places it in the drawing. Name then ID: the ID follows the name (`Valve 1` ->
 * `Valve_1`) until the user edits it, and stays editable after creation (a rename keeps every reference).
 * Loaded after app.js and built on window.AqUI. All text is inserted with textContent. Icons are drawn from the
 * built-in set (icons.js); an icon never decides what a channel does. A chosen channel is PROPOSED until the same
 * server-side check as on the I/O board plus your observation make it VERIFIED. Nothing on these pages sends
 * anything to a module. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, act, pill, fmtTime, can, mount, toast, S, VIEWS } = U;

  const icon = window.AqIcons.icon;
  const cap = s => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
  const MSG = {};
  const say = (k, text, bad) => { MSG[k] = { text, bad: !!bad }; };
  const note = k => MSG[k] ? h("span", { class: "inline-msg " + (MSG[k].bad ? "bad" : "ok"), role: "status" }, MSG[k].text) : null;
  async function send(k, method, path, body, ok) {
    try { const r = await api(method, path, body); say(k, ok); U.refresh(); return r; }
    catch (e) { say(k, String(e.message || e), true); U.refresh(); return null; }
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
    const lockedVerified = b && b.status === "VERIFIED" && !can("io_assign");
    const sel = h("select", { "aria-label": `Channel for ${it.name} ${role.label}`, disabled: !can("equipment_edit") || lockedVerified },
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
      h("td", null, sel, " ", h("button", { class: "small", disabled: !can("equipment_edit") || lockedVerified, onclick: () =>
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
      h("div", { class: "row" }, icon(it.icon), h("h3", null, it.name), h("span", { class: "mono small", "data-public-id": it.item_id }, it.public_id || it.item_id),
        pill(it.status, STATUS_CLS[it.status]), h("span", { class: "muted" }, `${t ? t.name : it.type_id} v${it.type_version} · equipment v${it.version}`),
        r ? (r.ready ? pill("READY FOR PROGRAMS", "ok") : roles.some(x => E.bindings.find(b => b.item_id === it.item_id && b.role === x.role))
          ? pill("NOT READY", "warn") : pill("PLANNED · UNASSIGNED", "warn")) : null),
      r && !r.ready ? h("p", { class: "muted small" }, [
        r.missing_required.length ? `No channel: ${r.missing_required.join(", ")}. ` : "",
        r.unverified.length ? `Not verified: ${r.unverified.join(", ")}. ` : "",
        r.unscaled.length ? `No scaling: ${r.unscaled.join(", ")}.` : ""].join("")) : null,
      latest && latest.version !== it.type_version ? h("p", { class: "warn small" }, `Type v${latest.version} is active; this item stays on v${it.type_version} until you upgrade it.`,
        " ", h("button", { class: "small", disabled: !can("equipment_edit"), onclick: () => upgrade(it, latest.version) }, "Preview upgrade")) : null,
      upgradeBox(it),
      props.length ? h("p", { class: "small" }, props.map(p => `${p.label}: ${it.body.properties[p.key] ?? p.default ?? "—"}${p.unit ? " " + p.unit : ""}`).join(" · ")) : null,
      tankPreview(it),
      h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Role", "Signal", "Channel", "State", "Check"].map(x => h("th", null, x)))),
        h("tbody", null, roles.map(role => roleRow(E, it, role, board)))),
      h("p", { class: "muted small" }, "Commanded state, coil readback and independent feedback are shown separately on the twin and bench pages; a missing feedback is UNKNOWN, never OFF."),
      it.status === "ACTIVE" ? itemDetails(it, t) : null,
      it.status === "ACTIVE" && can("equipment_activate") ? retireRow(it) : null);
  }
  function retireRow(it) {
    const why = h("input", { maxlength: 200, placeholder: "why it is retired", "aria-label": `Reason to retire ${it.name}` });
    return h("details", null, h("summary", { class: "small" }, `Retire ${it.name}`),
      h("div", { class: "row" }, why, h("button", { class: "small ghost", onclick: () => {
        if (why.value.trim().length < 3) { why.focus(); return; }
        act("POST", `/api/equipment/items/${it.item_id}/retire`, { reason: why.value }, "retired; its history and records are kept");
      } }, `Retire ${it.name}`)), h("p", { class: "muted small" }, "Retiring keeps the item's history and records; programs that use it must be changed first."));
  }

  // 0.7.3: a tank with a capacity shows the size it gets in the drawing (the editor's rule, schematic only)
  function tankPreview(it) {
    const cap = Number(((it.body || {}).properties || {}).capacity_l);
    if (!(cap > 0) || !window.AqHydraulicEditor || !window.AqHydraulicEditor.tankSize) return null;
    const z = window.AqHydraulicEditor.tankSize(cap), k = 0.3, NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg"), r = document.createElementNS(NS, "rect");
    svg.setAttribute("width", z.w * k + 2); svg.setAttribute("height", z.h * k + 2); svg.setAttribute("aria-hidden", "true");
    Object.entries({ x: 1, y: 1, width: z.w * k, height: z.h * k, rx: 3, fill: "none", stroke: "currentColor" }).forEach(([a, b]) => r.setAttribute(a, b));
    svg.appendChild(r);
    return h("p", { class: "small muted", "data-tank-size": `${z.w}x${z.h}` }, svg, ` Drawn ${z.w} × ${z.h} in the installation drawing for ${cap} L (schematic, not the tank's real dimensions).`);
  }
  // 0.7.0: the item's details as typed fields (no JSON): properties of its type, the scaling and alarm limits of
  // each measurement. Values the user typed survive the page's refresh; problems come back beside the button.
  const ED = {};
  function itemDetails(it, t) {
    const d = ED[it.item_id] = ED[it.item_id] || { name: it.name, body: JSON.parse(JSON.stringify(it.body)), msg: null, version: it.version };
    if (d.version !== it.version && !d.dirty) Object.assign(d, { name: it.name, body: JSON.parse(JSON.stringify(it.body)), version: it.version });
    const b = d.body;
    b.properties = b.properties || {}; b.scaling = b.scaling || {}; b.alarms = b.alarms || [];
    const mark = () => { d.dirty = true; d.msg = null; };          // an edit retires the last save's message
    const props = (t ? t.body.properties || [] : []).map(p => {
      const cur = b.properties[p.key];
      let inp;
      if (p.kind === "CHOICE") inp = h("select", { "aria-label": p.label, onchange: ev => { b.properties[p.key] = ev.target.value; mark(); } },
        h("option", { value: "" }, "— not set —"), p.choices.map(c => h("option", { value: c, selected: cur === c ? true : null }, c)));
      else if (p.kind === "BOOLEAN") inp = h("input", { type: "checkbox", checked: cur ? true : null, "aria-label": p.label, onchange: ev => { b.properties[p.key] = ev.target.checked; mark(); } });
      else if (p.kind === "TEXT") inp = h("input", { maxlength: 200, value: cur == null ? "" : cur, "aria-label": p.label, oninput: ev => { b.properties[p.key] = ev.target.value; mark(); } });
      else inp = h("input", { type: "number", step: p.kind === "INTEGER" ? 1 : "any", min: p.min == null ? null : p.min, max: p.max == null ? null : p.max, class: "w-num",
        value: cur == null ? "" : cur, "aria-label": p.label,
        oninput: ev => { if (ev.target.value === "") delete b.properties[p.key]; else b.properties[p.key] = Number(ev.target.value); mark(); } });
      return h("label", null, `${p.label}${p.unit ? " (" + p.unit + ")" : ""}${p.required ? " *" : ""}`, inp);
    });
    const meas = t ? t.body.roles.filter(r => r.purpose === "MEASUREMENT") : [];
    const scaleRows = meas.map(r => {
      const sc = b.scaling[r.role];
      const set = (k, v) => { b.scaling[r.role] = Object.assign(b.scaling[r.role] || { raw_min: 4, raw_max: 20, raw_unit: "mA", eng_min: 0, eng_max: 100, unit: r.unit || "", provenance: "PROPOSED" }, { [k]: v }); mark(); };
      const num = (k, label) => h("input", { type: "number", step: "any", class: "w-num", value: sc && sc[k] != null ? sc[k] : "", "aria-label": `${r.label} ${label}`, oninput: ev => set(k, Number(ev.target.value)) });
      return h("tr", null, h("td", null, r.label),
        h("td", null, sc ? null : h("span", { class: "muted small" }, "not scaled "), num("raw_min", "signal at minimum"), " – ", num("raw_max", "signal at maximum"), " ",
          h("select", { "aria-label": `${r.label} signal unit`, onchange: ev => set("raw_unit", ev.target.value) }, ["mA", "V"].map(u => h("option", { value: u, selected: sc && sc.raw_unit === u ? true : null }, u)))),
        h("td", null, num("eng_min", "value at minimum"), " – ", num("eng_max", "value at maximum"), " ",
          h("input", { maxlength: 16, class: "w-num", value: sc ? sc.unit : (r.unit || ""), "aria-label": `${r.label} unit`, oninput: ev => set("unit", ev.target.value) })),
        h("td", null, h("select", { "aria-label": `${r.label} provenance`, onchange: ev => set("provenance", ev.target.value) },
          ["PROPOSED", "DATASHEET", "CALIBRATED", "DEMO"].map(v => h("option", { value: v, selected: sc && sc.provenance === v ? true : null }, v.toLowerCase()))),
          sc && sc.provenance === "CALIBRATED" ? h("input", { maxlength: 120, placeholder: "calibration record", value: sc.calibration_ref || "", "aria-label": `${r.label} calibration record`, oninput: ev => set("calibration_ref", ev.target.value) }) : null),
        h("td", null, sc ? h("button", { class: "small ghost", onclick: () => { delete b.scaling[r.role]; mark(); U.refresh(); } }, "Remove") : null));
    });
    const alarmRows = meas.map(r => {
      let a = b.alarms.find(x => x.role === r.role);
      const ensure = () => { if (!a) { a = { role: r.role, low: null, high: null, deadband: 0, delay_s: 0 }; b.alarms.push(a); } return a; };
      const num = (k, label) => h("input", { type: "number", step: "any", min: k === "low" || k === "high" ? null : 0, class: "w-num", value: a && a[k] != null ? a[k] : "", "aria-label": `${r.label} ${label}`,
        oninput: ev => { ensure()[k] = ev.target.value === "" ? (k === "low" || k === "high" ? null : 0) : Number(ev.target.value); mark(); } });
      return h("tr", null, h("td", null, r.label), h("td", null, num("low", "low alarm")), h("td", null, num("high", "high alarm")),
        h("td", null, num("deadband", "deadband")), h("td", null, num("delay_s", "delay seconds")),
        h("td", null, a ? h("button", { class: "small ghost", onclick: () => { b.alarms = b.alarms.filter(x => x !== a); mark(); U.refresh(); } }, "Remove") : null));
    });
    const save = async () => {
      b.alarms = b.alarms.filter(x => x.low != null || x.high != null);
      try {
        await api("POST", "/api/equipment/items", { item_id: it.item_id, name: d.name, type_id: it.type_id, type_version: it.type_version,
          icon: it.icon, body: b, expected_version: it.version });
        d.msg = { text: "Saved as a new version of this equipment.", bad: false }; d.dirty = false;
      } catch (e) { d.msg = { text: String(e.message || e), bad: true }; }
      U.refresh();
    };
    const idIn = h("input", { maxlength: 48, value: d.pid == null ? (it.public_id || it.item_id) : d.pid, "aria-label": `ID of ${it.name}`, class: "mono",
      oninput: ev => { d.pid = ev.target.value; } });
    const rename = async () => {
      const want = idIn.value.trim(), bad = window.AqIds.problem(want);
      if (bad) { say("id:" + it.item_id, bad, true); U.refresh(); return; }
      const r = await send("id:" + it.item_id, "POST", `/api/equipment/items/${encodeURIComponent(it.item_id)}/id`, { public_id: want, expected_rev: it.id_rev || 0 },
        `ID is now ${want}. Blocks, programs, channels and history keep pointing at this equipment.`);
      if (r) d.pid = null;
    };
    return h("details", { "data-item-details": it.item_id }, h("summary", { class: "small" }, `Details of ${it.name}: name, ID, properties, scaling, alarm limits`),
      h("div", { class: "row wrap" }, h("label", null, "Name", h("input", { maxlength: 80, value: d.name, "aria-label": `Name of ${it.item_id}`, oninput: ev => { d.name = ev.target.value; mark(); } })),
        h("label", null, "ID", idIn), h("button", { class: "small", disabled: !can("equipment_edit"), onclick: rename }, "Change ID"), note("id:" + it.item_id)),
      h("div", { class: "row wrap" }, props),
      meas.length ? [h("h4", null, "Scaling (the module's signal to the engineering value)"),
        h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Reading", "Signal range", "Value range and unit", "Where it comes from", ""].map(x => h("th", null, x)))), h("tbody", null, scaleRows)),
        h("h4", null, "Alarm limits"),
        h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Reading", "Low", "High", "Deadband", "Delay (s)", ""].map(x => h("th", null, x)))), h("tbody", null, alarmRows)),
        h("p", { class: "muted small" }, "A limit raises an alarm after its delay and clears once the value is back inside by the deadband. A reading that is missing or stale neither raises nor clears it.")] : null,
      h("div", { class: "row" }, h("button", { class: "small", disabled: !can("equipment_edit"), onclick: save }, "Save details"),
        d.msg ? h("span", { class: "inline-msg " + (d.msg.bad ? "bad" : "ok"), role: "status" }, d.msg.text) : null));
  }

  const UP = {};
  async function upgrade(it, to) {
    const p = await api("GET", `/api/equipment/items/${it.item_id}/upgrade?to_version=${to}`).catch(e => ({ error: String(e.message || e) }));
    UP[it.item_id] = { p, to };
    U.refresh();
  }
  function upgradeBox(it) {
    const u = UP[it.item_id]; if (!u) return null;
    const p = u.p;
    if (p.error) return h("p", { class: "bad small" }, p.error);
    const lines = [p.roles_added.length ? `Roles added: ${p.roles_added.join(", ")}` : null,
      p.roles_removed.length ? `Roles removed: ${p.roles_removed.join(", ")}` : null,
      p.bindings_cleared.length ? `Channels cleared: ${p.bindings_cleared.join(", ")}` : null,
      p.bindings_back_to_proposed.length ? `Verify again: ${p.bindings_back_to_proposed.join(", ")}` : null,
      p.programs.length ? `Programs to revalidate: ${p.programs.join(", ")}` : null].filter(Boolean);
    return h("div", { class: "confirm", role: "group", "aria-label": `Upgrade ${it.name}` },
      h("p", null, h("strong", null, `Upgrade ${it.name} from type v${p.from} to v${p.to}?`)), h("ul", { class: "small" }, lines.map(x => h("li", null, x))),
      p.item_issues.length ? h("p", { class: "bad small" }, `Not possible: ${p.item_issues.join("; ")}`) : null,
      h("div", { class: "row" }, h("button", { class: "ghost small", onclick: () => { delete UP[it.item_id]; U.refresh(); } }, "Cancel"),
        p.item_issues.length ? null : h("button", { class: "small", onclick: () => { delete UP[it.item_id]; act("POST", `/api/equipment/items/${it.item_id}/upgrade`, { to_version: p.to, expected_version: it.version }, "upgraded"); } }, "Upgrade")));
  }

  function newItemCard(E) {
    const types = activeTypes(E);
    const tsel = h("select", { "aria-label": "Type" }, types.map(t => h("option", { value: t.type_id }, `${t.name} (v${t.version})`)));
    const name = h("input", { maxlength: 80, placeholder: "e.g. Valve 1", "aria-label": "Name" });
    const id = h("input", { maxlength: 48, placeholder: "Valve_1", class: "mono", "aria-label": "ID" });
    window.AqIds.follow(name, id);
    const isel = h("select", { "aria-label": "Icon" }, E.icons.map(i => h("option", { value: i }, cap(i))));
    const preview = h("span", null);
    const showIcon = () => preview.replaceChildren(icon(isel.value));
    isel.addEventListener("change", showIcon);
    tsel.addEventListener("change", () => { const t = types.find(x => x.type_id === tsel.value); if (t && !isel.dataset.dirty) { isel.value = t.body.default_icon; showIcon(); } });
    if (types[0]) isel.value = types[0].body.default_icon;
    showIcon();
    const create = async () => {
      const bad = !name.value.trim() ? "Give it a name." : window.AqIds.problem(id.value.trim());
      if (bad) { say("new", bad, true); U.refresh(); return; }
      const r = await send("new", "POST", "/api/equipment/items", { item_id: id.value.trim(), name: name.value, type_id: tsel.value, icon: isel.value, body: {} },
        `${name.value} created as ${id.value.trim()}. It is planned and unassigned until a channel is chosen; place it in the drawing from Digital Twin.`);
      if (r) [name, id].forEach(f => { f.value = ""; delete f.dataset.dirty; delete f.dataset.owned; });
    };
    return h("div", { class: "card" }, h("h3", null, "New equipment"),
      types.length ? h("div", { class: "row wrap" },
        h("label", null, "Name ", name), h("label", null, "ID ", id), h("label", null, "Type ", tsel), h("label", null, "Icon ", isel), preview,
        h("button", { disabled: !can("equipment_edit"), onclick: create }, "Create"), note("new"))
        : h("p", { class: "muted" }, can("equipment_activate") ? "No active equipment type yet. Activate one on Equipment types first." : "No active equipment type yet: an administrator activates the types on Equipment types."),
      h("p", { class: "muted small" }, "The ID fills in from the name (Valve 1 → Valve_1) until you change it, and can be changed later. Properties, scaling and alarm limits are edited after creation. The icon is only a picture."));
  }

  VIEWS.equipment = async function () {
    const [E, board] = await Promise.all([api("GET", "/api/equipment"), api("GET", "/api/board").catch(() => null)]);
    const pts = board ? board.modules.flatMap(m => m.points.map(p => ({ point: p.id, kind: p.kind }))) : [];
    const live = E.items.filter(i => i.status === "ACTIVE"), retired = E.items.filter(i => i.status === "RETIRED");
    mount("v-equipment",
      h("div", { class: "card" }, h("div", { class: "row" }, h("h2", null, "Equipment"),
        h("span", { class: "muted" }, `${live.length} items · ${E.bindings.filter(b => b.status === "VERIFIED").length} verified channels`),
        h("a", { href: "/api/equipment/export", download: "aquacrop-equipment.json" }, "Export"),
        can("equipment_activate") ? h("a", { href: "#equipment-types" }, "Equipment types") : null),
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
