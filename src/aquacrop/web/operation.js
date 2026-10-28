/* AquaCrop Operation page on the real dashboard (0.5.0): the installation's AUTO/MANUAL selector, the restart
 * checklist and approval, running operations, conflicts and program activation for physical bench tests.
 * Every change carries this server's runtime id. Loaded after app.js, built on window.AqUI. */
"use strict";
(function () {
  const U = window.AqUI;
  const { h, api, pill, can, mount, toast, S, VIEWS } = U;
  let RID = null;

  async function send(path, body, ok) {
    if (S.stale) { toast("Connection stale: command not sent", true); return null; }
    try {
      const r = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json",
        "X-CSRF-Token": S.csrf || "", "X-Runtime-Id": RID || "" }, body: JSON.stringify(body || {}) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || r.status);
      if (ok) toast(ok); U.refresh(); return d;
    } catch (e) { toast(String(e.message || e), true); return null; }
  }

  VIEWS.operation = async function () {
    const v = await api("GET", "/api/operation");
    if (!v.available) return mount("v-operation", h("div", { class: "card" }, h("h2", null, "Operation"), h("p", null, v.reason),
      h("p", { class: "muted small" }, "Programs can be built and tested in the digital twin meanwhile.")));
    RID = v.runtime_id;
    const pc = can("process_control"), adm = can("equipment_activate");
    const P = await api("GET", "/api/programs");
    const latest = {};
    for (const p of P.programs) if (p.status === "VALIDATED" && (!latest[p.program_id] || latest[p.program_id].version < p.version)) latest[p.program_id] = p;
    const head = h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", null, "Installation mode"), pill(v.mode, v.mode === "AUTO" ? "ok" : v.mode === "MANUAL" ? "warn" : "bad"),
        v.mode !== "RECOVERY" ? [
          h("button", { class: "cmd", disabled: !pc || v.mode === "AUTO" || v.mode === "STOPPING", onclick: () => send("/api/operation/mode", { target: "AUTO" }, "AUTO") }, "AUTO"),
          h("button", { class: "cmd danger", disabled: !pc || v.mode !== "AUTO", onclick: () => send("/api/operation/mode", { target: "MANUAL" }, "stopping automatic operations") }, "MANUAL")] : null),
      h("p", { class: "muted small" }, v.meaning),
      h("p", { class: "small" }, `Approved test outputs: ${v.allowlist.join(", ")} (${v.loads}; approved by ${v.approved_by}). `,
        v.session ? pill(`TEST SESSION ARMED by ${v.session.username} until ${new Date(v.session.expires_utc).toLocaleTimeString()}`, "ok")
          : pill("NO TEST SESSION: programs cannot switch outputs (arm one on the I/O board)", "warn")),
      v.last_error ? h("p", { class: "bad small" }, "Engine error: " + v.last_error) : null);
    const rec = v.mode === "RECOVERY" ? h("div", { class: "card bad" }, h("h3", null, "Restart: check, then approve"),
      h("ul", null, v.checklist.map(c => h("li", { class: c.ok ? "" : "bad" }, `${c.ok ? "✓" : "✗"} ${c.check} (${c.detail})`,
        !c.ok && c.fix ? h("div", { class: "small" }, "What to do: " + c.fix) : null))),
      ["MANUAL", "AUTO"].map(t => h("div", null, h("strong", null, `Approve into ${t}`), h("ul", { class: "small" }, v.consequences[t].map(x => h("li", null, x))),
        h("button", { disabled: !pc || v.checklist.some(c => !c.ok), onclick: () => { if (confirm(`Approve the restart into ${t}?\n\n` + v.consequences[t].join("\n"))) send("/api/operation/recovery/approve", { target: t }, `approved into ${t}`); } }, `Approve into ${t}`))),
      h("p", { class: "muted small" }, "Approving does not clear a fault; every check must pass first. Interrupted operations never resume.")) : null;
    const conflicts = v.conflicts.map(c => h("div", { class: "card bad" }, h("strong", null, `Conflict on ${c.item}`),
      h("p", null, `Program 1: ${c.running.program_id}: ${c.running.consequence}.`), h("p", null, `Program 2: ${c.waiting.program_id}: ${c.waiting.consequence}.`),
      h("button", { disabled: !pc, onclick: () => send("/api/operation/conflict", { conflict_id: c.conflict_id, choice: "KEEP_FIRST" }) }, `Keep ${c.running.program_id}`),
      h("button", { disabled: !pc, onclick: () => send("/api/operation/conflict", { conflict_id: c.conflict_id, choice: "SWITCH" }) }, `Stop it and run ${c.waiting.program_id}`)));
    const items = h("div", { class: "card" }, h("h3", null, "Equipment state"),
      h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Item", "Asked", "Output (readback)", "Feedback", "Used by"].map(x => h("th", null, x)))),
        h("tbody", null, Object.values(v.items).map(i => h("tr", null, h("td", null, i.name), h("td", null, i.commanded ? "ON" : "OFF"),
          h("td", null, i.coil), h("td", null, i.feedback), h("td", null, i.owner || ""))))),
      h("p", { class: "muted small" }, "Output is the module's coil readback, not proof of the device. UNKNOWN is never shown as OFF."));
    const ops = h("div", { class: "card" }, h("h3", null, "Running"),
      v.ops.length ? h("table", { class: "compact" }, h("tbody", null, v.ops.map(o => h("tr", null, h("td", null, `${o.program_id} v${o.version}`), h("td", null, o.origin),
        h("td", null, pill(o.state, o.state === "STOP_UNVERIFIED" ? "bad" : "")), h("td", null, `${o.runtime_s} s, ${o.remaining_s} s left`),
        h("td", null, o.state === "RUNNING" ? h("button", { class: "small danger", disabled: !pc, onclick: () => send("/api/operation/stop", { op_id: o.op_id }) }, "Stop") : null)))))
        : h("p", { class: "muted" }, "Nothing is running."));
    const progs = h("div", { class: "card" }, h("h3", null, "Programs for physical tests"),
      Object.values(latest).length ? h("table", { class: "compact" }, h("tbody", null, Object.values(latest).map(p => {
        const active = v.active[p.program_id] === p.version, tested = p.simulation && p.simulation.passed;
        const passed = tested && p.simulation.current !== false;   // a result from another context is history
        return h("tr", null, h("td", null, `${p.name} v${p.version}`), h("td", null, passed ? pill("TWIN PASSED", "ok")
          : tested ? h("span", { title: p.simulation.why_not_current || "" }, pill("twin test outdated", "warn")) : pill("not tested in twin", "warn")),
          h("td", null, active ? pill("ACTIVE", "ok") : ""),
          h("td", null, !active ? h("button", { class: "small", disabled: !adm || !passed, onclick: () => send("/api/operation/activate", { program_id: p.program_id, version: p.version }, "active on the real system") }, "Activate")
            : h("button", { class: "small ghost", disabled: !adm, onclick: () => send("/api/operation/deactivate", { program_id: p.program_id, version: p.version }) }, "Deactivate"),
            active && p.body.trigger.type !== "CONTINUOUS" ? h("button", { class: "small", disabled: !pc || v.mode !== "MANUAL", onclick: () => send("/api/operation/run-now", { program_id: p.program_id }, "started") }, "Run now") : null));
      }))) : h("p", { class: "muted" }, "No validated program yet."));
    const fin = h("details", { class: "card" }, h("summary", null, `Results (${v.finished.length})`),
      h("table", { class: "compact small" }, h("tbody", null, v.finished.slice().reverse().map(r => h("tr", null, h("td", null, new Date(r.ended_utc).toLocaleString()),
        h("td", null, `${r.program_id} v${r.version}`), h("td", null, pill(r.result, r.result === "DONE" ? "ok" : "bad")), h("td", null, r.reason || ""))))));
    mount("v-operation", head, rec, conflicts, ops, items, progs, fin);
  };
})();
