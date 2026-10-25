/* AquaCrop program editor on the real dashboard (0.5.0). Loaded after app.js, built on window.AqUI.
 * A program is a trigger and an action over equipment items, saved as validated data; saving or validating runs
 * nothing. Physical runs need the bench-test profile; every version is tested in the digital twin first. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, act, pill, can, mount, toast, S, VIEWS } = U;
  const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  const ST = { VALIDATED: "ok", DRAFT: "warn" };

  function describe(b) {
    const t = b.trigger, a = b.action;
    const when = t.type === "MANUAL" ? "when started by hand" : t.type === "WEEKLY" ? t.entries.map(e => `${e.days.join(",")} ${e.start}-${e.end}`).join("; ")
      : t.type === "CONDITION" ? `when ${t.when.item}.${t.when.role} ${t.when.op.toLowerCase().replace("_", " ")}${t.when.value != null ? " " + t.when.value : ""} for ${t.when.debounce_s} s`
      : "continuously" + (t.entries.length ? " in windows" : "");
    const what = a.type === "RUN_FOR" ? `run ${a.item} ${a.duration_s ? "for " + a.duration_s + " s" : "for the window"}`
      : a.type === "DELIVER_VOLUME" ? `deliver ${a.target_L} L with ${a.item}, measured by ${a.meter} (max ${a.max_runtime_s} s)`
      : a.type === "KEEP_BETWEEN" ? `keep ${a.sensor} between ${a.low} and ${a.high} with ${a.item} (${a.direction.toLowerCase()})`
      : `${a.steps.length} steps (max ${a.max_runtime_s} s)`;
    return `${when}: ${what}`;
  }

  // ---------------------------------------------------------------- the builder
  function builder(P, existing) {
    const items = P.items;
    const opt = (list, sel) => list.map(([v, l]) => h("option", { value: v, selected: v === sel }, l));
    const itemOpts = (filter, sel) => opt(items.filter(filter).map(i => [i.item_id, i.name]), sel);
    const actuator = i => i.roles.some(r => r.purpose === "COMMAND");
    const meter = i => i.roles.some(r => r.purpose === "MEASUREMENT");
    const b = existing ? existing.body : { trigger: { type: "MANUAL" }, action: { type: "RUN_FOR", item: (items.find(actuator) || {}).item_id, duration_s: 60 } };
    const id = h("input", { maxlength: 48, value: existing ? existing.program_id : "", disabled: !!existing, placeholder: "water-am", "aria-label": "Program id" });
    const name = h("input", { maxlength: 80, value: existing ? existing.name : "", placeholder: "Morning watering", "aria-label": "Program name" });
    const ta = h("textarea", { rows: 14, spellcheck: "false", "aria-label": "Program definition (JSON)" });
    ta.value = JSON.stringify(b, null, 2);
    const ttype = h("select", { "aria-label": "Trigger" }, opt([["MANUAL", "Run now (by hand)"], ["WEEKLY", "Weekly schedule"], ["CONDITION", "When a sensor condition holds"], ["CONTINUOUS", "Keep a value between limits"]], b.trigger.type));
    const atype = h("select", { "aria-label": "Action" }, opt([["RUN_FOR", "Run for a time"], ["DELIVER_VOLUME", "Deliver a measured volume"], ["KEEP_BETWEEN", "Keep between limits"], ["SEQUENCE", "Ordered sequence"]], b.action.type));
    const fill = () => {          // a starting body for the chosen kinds; the JSON below is what is saved
      const a0 = items.find(actuator), m0 = items.find(meter);
      const t = { MANUAL: { type: "MANUAL" }, WEEKLY: { type: "WEEKLY", entries: [{ days: DAYS.slice(), start: "06:00", end: "06:10" }] },
        CONDITION: { type: "CONDITION", when: { item: (items[0] || {}).item_id, role: ((items[0] || {}).roles || [{}])[0].role, op: "IS_ON", debounce_s: 2 }, rearm_after_s: 60 },
        CONTINUOUS: { type: "CONTINUOUS", entries: [] } }[ttype.value];
      const a = { RUN_FOR: { type: "RUN_FOR", item: a0 && a0.item_id, duration_s: ttype.value === "WEEKLY" ? null : 60 },
        DELIVER_VOLUME: { type: "DELIVER_VOLUME", item: a0 && a0.item_id, meter: m0 && m0.item_id, meter_role: "flow", target_L: 10, max_runtime_s: 600, no_flow_timeout_s: 20 },
        KEEP_BETWEEN: { type: "KEEP_BETWEEN", item: a0 && a0.item_id, sensor: m0 && m0.item_id, sensor_role: m0 ? m0.roles.find(r => r.purpose === "MEASUREMENT").role : "level", direction: "RAISE", low: 10, high: 20, debounce_s: 5, max_run_s: 600, min_off_s: 60 },
        SEQUENCE: { type: "SEQUENCE", max_runtime_s: 900, steps: [{ do: "ON", item: a0 && a0.item_id, timeout_s: 10 }, { do: "RUN", seconds: 60 }, { do: "OFF", item: a0 && a0.item_id, timeout_s: 10 }] } }[atype.value];
      ta.value = JSON.stringify({ trigger: t, action: a }, null, 2); ta.dataset.dirty = "1";
    };
    ttype.addEventListener("change", fill); atype.addEventListener("change", fill);
    const save = async () => {
      let body; try { body = JSON.parse(ta.value); } catch (e) { toast("not valid JSON: " + e.message, true); return; }
      const r = await act("POST", "/api/programs/draft", { program_id: id.value, name: name.value, body, version: existing && existing.status === "DRAFT" ? existing.version : null }, "draft saved; nothing runs");
      if (r) { const v = await act("POST", "/api/programs/validate", { program_id: r.program_id, version: r.version }); if (v) toast(v.issues.length ? "problems: " + v.issues.join("; ") : "validated: test it in the digital twin", !!v.issues.length); }
    };
    return h("div", { class: "row col" },
      h("div", { class: "row" }, h("label", null, "Id ", id), h("label", null, "Name ", name), h("label", null, "Trigger ", ttype), h("label", null, "Action ", atype)),
      h("p", { class: "muted small" }, `Items: ${items.map(i => `${i.item_id} (${i.roles.map(r => r.role).join(", ")})`).join(" · ") || "none yet"}. Times are ${P.timezone}. Sequence steps: ON/OFF (item, verify_role, timeout_s), WAIT/RUN (seconds), DELIVER (meter, target_L).`),
      ta, h("div", { class: "row" }, h("button", { disabled: !can("equipment_edit"), onclick: save }, existing && existing.status !== "DRAFT" ? "Save as next version and validate" : "Save and validate")));
  }

  function card(P, v) {
    const val = v.validation || {}, ctx = val.context || {}, sim = v.simulation;
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("h3", null, v.name), pill(`${v.status} v${v.version}`, ST[v.status]),
        sim && sim.passed ? pill("TWIN PASSED", "ok") : pill("NOT TESTED IN TWIN", "warn"), h("span", { class: "muted small" }, v.program_id)),
      h("p", null, describe(v.body)),
      v.next && v.next.length ? h("p", { class: "small" }, "Next windows: ", v.next.join(" · ")) : null,
      val.issues && val.issues.length ? h("ul", { class: "bad small" }, val.issues.map(x => h("li", null, x))) : null,
      ctx.physical_needs && ctx.physical_needs.length ? h("details", null, h("summary", { class: "small" }, `Before a physical test (${ctx.physical_needs.length})`), h("ul", { class: "small" }, ctx.physical_needs.map(x => h("li", null, x)))) : null,
      ctx.overlaps && ctx.overlaps.length ? h("p", { class: "warn small" }, "Overlaps with other programs: ", ctx.overlaps.slice(0, 4).join("; ")) : null,
      sim && sim.passed ? h("p", { class: "muted small" }, `Twin result accepted by ${sim.accepted_by} (${new Date(sim.accepted_utc).toLocaleString()}): ${sim.meaning}.`) : null,
      h("details", null, h("summary", null, v.status === "DRAFT" ? "Edit draft" : "Edit (makes the next version)"), builder(P, v)));
  }

  VIEWS.programs = async function () {
    const P = await api("GET", "/api/programs");
    const latest = {};
    for (const v of P.programs) if (!latest[v.program_id] || latest[v.program_id].version < v.version) latest[v.program_id] = v;
    mount("v-programs",
      h("div", { class: "card" }, h("h2", null, "Programs"),
        h("p", { class: "muted" }, "Schedules, measured volumes, sensor rules and sequences. Saving runs nothing. Test each version in the digital twin, then accept its result there."),
        h("p", { class: "warn small" }, P.live_activation)),
      Object.values(latest).sort((a, b) => a.name.localeCompare(b.name)).map(v => card(P, v)),
      h("details", { class: "card" }, h("summary", null, "New program"), builder(P, null)));
  };
})();
