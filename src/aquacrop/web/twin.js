/* AquaCrop digital twin page (0.5.0). Its own service, database and login session; every change it sends carries
 * this twin run's id, so a page left open across a reset or opened against another runtime is refused.
 * All text is inserted with textContent. */
"use strict";
(function () {
  const S = { csrf: null, rid: null, perms: [], v: null, busy: false, open: {} };
  const $ = id => document.getElementById(id);
  const icon = window.AqIcons.icon;
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat(Infinity)) if (k != null && k !== false)
      e.appendChild(typeof k === "string" || typeof k === "number" ? document.createTextNode(String(k)) : k);
    return e;
  }
  const can = p => S.perms.includes(p);
  const pill = (t, c) => h("span", { class: "pill " + (c || "") }, t);
  function toast(msg, bad) {
    const t = $("toast"); t.textContent = msg; t.classList.remove("hidden"); t.style.background = bad ? "var(--bad)" : "";
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 5000);
  }
  async function api(method, path, body) {
    const o = { method, headers: {}, credentials: "same-origin" };
    if (method !== "GET") { o.headers["X-CSRF-Token"] = S.csrf || ""; o.headers["X-Runtime-Id"] = S.rid || ""; }
    if (body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
    const r = await fetch(path, o);
    if (r.status === 401) { showLogin(); throw new Error("not signed in"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.status);
    return d;
  }
  async function act(method, path, body, ok) {
    try { const r = await api(method, path, body); if (ok) toast(ok); await refresh(); return r; }
    catch (e) { toast(String(e.message || e), true); return null; }
  }
  function showLogin() { $("app").classList.add("hidden"); $("login-view").classList.remove("hidden"); }
  async function signedIn(s) {
    S.csrf = s.csrf_token; S.rid = s.runtime_id; S.perms = s.permissions || [];
    $("who").textContent = `${s.username} (${s.role})`;
    $("login-view").classList.add("hidden"); $("app").classList.remove("hidden");
    await refresh();
  }
  $("login-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: f.get("username"), password: f.get("password") }) });
    if (!r.ok) { $("login-error").textContent = "login failed"; return; }
    signedIn(await api("GET", "/api/auth/session"));
  });
  $("logout").addEventListener("click", async () => { await api("POST", "/api/auth/logout").catch(() => null); showLogin(); });

  // ---------------------------------------------------------------- pieces
  const SIM_TZ = () => (S.v && S.v.timezone) || "Europe/Stockholm";
  function simTime(iso) {
    return new Date(iso).toLocaleString("sv-SE", { timeZone: SIM_TZ(), weekday: "short", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function led(state, label) {
    const cls = state === "ON" ? "led led-on" : state === "OFF" ? "led led-off" : "led led-unk";
    return h("span", { class: "led-wrap", title: `${label}: ${state}` }, h("span", { class: cls }), h("span", { class: "small" }, `${label} ${state}`));
  }
  function controls(v) {
    const run = can("sim_command");
    const speed = h("select", { "aria-label": "Speed" }, v.speeds.map(s => h("option", { value: s, selected: s === v.speed }, `${s}×`)));
    const start = h("input", { type: "datetime-local", "aria-label": "Scenario start (site time)" });
    return h("div", { class: "card" },
      h("div", { class: "row" },
        h("strong", { class: "clock" }, simTime(v.sim_utc)), h("span", { class: "muted small" }, SIM_TZ()),
        v.running ? pill(`RUNNING ${v.effective_speed}× (asked ${v.speed}×)`, "ok") : pill("PAUSED"),
        h("label", null, "Speed ", speed),
        h("button", { disabled: !run, onclick: () => act("POST", "/api/twin/control", { action: "run", speed: Number(speed.value) }) }, "Run"),
        h("button", { disabled: !run, onclick: () => act("POST", "/api/twin/control", { action: "pause" }) }, "Pause"),
        h("button", { disabled: !run, onclick: () => act("POST", "/api/twin/control", { action: "step", steps: 2 }) }, "Step 1 s"),
        h("label", { class: "small" }, "Start at ", start),
        h("button", { class: "ghost", disabled: !run, onclick: () => { if (confirm("Reset the twin? Its simulated state and clock start again; its history is kept.")) act("POST", "/api/twin/control", { action: "reset", start_local: start.value || null }, "twin reset"); } }, "Reset"),
        h("button", { class: "ghost", disabled: !run, onclick: () => act("POST", "/api/twin/control", { action: "import" }, "copied from the real system") }, "Copy from the real system")),
      h("p", { class: "muted small" }, `${v.label} Last copy from the real system: ${v.snapshot_utc ? new Date(v.snapshot_utc).toLocaleString() : "none"}. Twin run ${v.run_id}.`));
  }
  function modeCard(v) {
    const e = v.engine, ok = can("process_control");
    const kids = [h("div", { class: "row" }, h("h2", null, "Installation mode (twin)"),
      pill(e.mode, e.mode === "AUTO" ? "ok" : e.mode === "MANUAL" ? "warn" : "bad"),
      e.mode !== "RECOVERY" ? [
        h("button", { class: e.mode === "AUTO" ? "cmd" : "", disabled: !ok || e.mode === "AUTO" || e.mode === "STOPPING", onclick: () => act("POST", "/api/twin/mode", { target: "AUTO" }, "AUTO") }, "AUTO"),
        h("button", { class: e.mode !== "AUTO" ? "cmd" : "", disabled: !ok || e.mode !== "AUTO", onclick: () => act("POST", "/api/twin/mode", { target: "MANUAL" }, "stopping automatic operations") }, "MANUAL")] : null),
      h("p", { class: "muted small" }, "This is the twin's own selector. It never changes the real system's mode.")];
    if (e.mode === "STOPPING") kids.push(h("p", { class: "warn" }, "Stopping every automatic operation; MANUAL is reached only when each stop is verified."));
    if (e.mode === "RECOVERY" && v.checklist) {
      kids.push(h("div", { class: "card" }, h("strong", null, "After a restart: check, then approve"),
        h("ul", null, v.checklist.map(c => h("li", { class: c.ok ? "" : "bad" }, `${c.ok ? "✓" : "✗"} ${c.check} (${c.detail})`))),
        ["AUTO", "MANUAL"].map(t => h("div", null, h("strong", null, `Approve into ${t}:`),
          h("ul", { class: "small" }, v.consequences[t].map(x => h("li", null, x))),
          h("button", { disabled: !ok, onclick: () => { if (confirm(`Approve the restart into ${t}?\n\n` + v.consequences[t].join("\n"))) act("POST", "/api/twin/recovery/approve", { target: t }, `approved into ${t}`); } }, `Approve into ${t}`))),
        h("p", { class: "muted small" }, "Approving is your decision that the installation may operate again. It does not clear a fault: every check above must pass first.")));
    }
    return h("div", { class: "card" }, kids);
  }
  function equipmentGrid(v) {
    const run = can("sim_command");
    return h("div", { class: "card" }, h("h2", null, "Equipment"),
      v.items.length ? h("div", { class: "eq-grid" }, v.items.map(it => {
        const faultSel = h("select", { "aria-label": `Fault for ${it.name}` }, ["", "NO_FEEDBACK", "STUCK_ON", "NO_READBACK", "SENSOR_LOST", "NO_FLOW"].map(f => h("option", { value: f, selected: (it.fault || "") === f }, f || "no fault")));
        return h("div", { class: "eq-card" + (it.owner ? " eq-busy" : "") },
          h("div", { class: "row" }, icon(it.icon, 32), h("strong", null, it.name), pill(it.behaviour === "NOT_MODELLED" ? "NOT MODELLED" : it.behaviour.toLowerCase(), it.behaviour === "NOT_MODELLED" ? "bad" : "")),
          it.coil !== "NONE" ? h("div", { class: "row" }, led(it.commanded ? "ON" : "OFF", "asked"), led(it.coil, "output"), it.feedback !== "NONE" ? led(it.feedback, "feedback") : null) : h("p", { class: "muted small" }, "input only"),
          it.owner ? h("p", { class: "small" }, `used by ${it.owner}`) : null,
          h("table", { class: "compact small" }, h("tbody", null, it.roles.filter(r => r.direction === "INPUT").map(r => {
            const val = r.value == null ? (r.quality === "NO CHANNEL" ? "no channel" : "NOT MODELLED") : (typeof r.value === "boolean" ? (r.value ? "ON" : "OFF") : `${r.value}${r.unit ? " " + r.unit : ""}`);
            const inp = r.point && (it.behaviour.startsWith("MANUAL") || r.label === "Pressed") ? (typeof r.value === "boolean" || it.behaviour === "MANUAL_INPUT"
              ? h("button", { class: "small", disabled: !run, onclick: () => act("POST", "/api/twin/input", { point: r.point, value: !(r.value === true) }) }, "toggle")
              : (() => { const x = h("input", { type: "number", step: "any", class: "narrow-num", "aria-label": `Set ${r.label}` }); return [x, h("button", { class: "small", disabled: !run, onclick: () => act("POST", "/api/twin/input", { point: r.point, value: Number(x.value) }) }, "set")]; })()) : null;
            return h("tr", null, h("td", null, r.label), h("td", null, val), h("td", { class: "muted" }, r.point || ""), h("td", null, inp));
          }))),
          h("div", { class: "row small" }, faultSel, h("button", { class: "small ghost", disabled: !run, onclick: () => act("POST", "/api/twin/fault", { target: it.item_id, fault: faultSel.value || null }, "fault set") }, "Apply fault")));
      })) : h("p", { class: "muted" }, "No equipment in the copy yet. Create equipment on the real dashboard, then press Copy from the real system."),
      h("p", { class: "muted small" }, "Lights: asked = the program's command; output = the virtual coil read back; feedback = the item's own input. UNKNOWN is never shown as OFF."));
  }
  function programsCard(v) {
    const run = can("sim_command"), edit = can("equipment_edit"), pc = can("process_control");
    const latest = {};
    for (const p of v.programs) if (!latest[p.program_id] || latest[p.program_id].version < p.version) latest[p.program_id] = p;
    const rows = v.programs.filter(p => p.active || latest[p.program_id] === p);
    return h("div", { class: "card" }, h("h2", null, "Programs"),
      rows.length ? h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Program", "Version", "Trigger → action", "In the twin", "Next", ""].map(x => h("th", null, x)))),
        h("tbody", null, rows.map(p => {
          const t = p.body.trigger, a = p.body.action;
          const runs = v.results.filter(r => r.program_id === p.program_id && r.version === p.version);
          return h("tr", null, h("td", null, p.name), h("td", null, `v${p.version} ${p.status.toLowerCase()}`),
            h("td", null, `${t.type.toLowerCase()} → ${a.type.toLowerCase().replace("_", " ")}`),
            h("td", null, p.active ? pill("ACTIVE", "ok") : pill("inactive"), runs.length ? ` ${runs.filter(r => r.result === "DONE").length}/${runs.length} done` : ""),
            h("td", { class: "small" }, (p.next || []).join(", ")),
            h("td", null,
              !p.active ? h("button", { class: "small", disabled: !edit, onclick: () => act("POST", "/api/twin/programs/activate", { program_id: p.program_id, version: p.version }, "active in the twin") }, "Activate in twin")
                : h("button", { class: "small ghost", disabled: !edit, onclick: () => act("POST", "/api/twin/programs/deactivate", { program_id: p.program_id, version: p.version }) }, "Deactivate"),
              p.active && t.type !== "CONTINUOUS" ? h("button", { class: "small", disabled: !run || v.engine.mode !== "MANUAL", title: "MANUAL mode only", onclick: () => act("POST", "/api/twin/run-now", { program_id: p.program_id }, "started") }, "Run now") : null,
              runs.some(r => r.result === "DONE") ? h("button", { class: "small", disabled: !pc, title: "Record this twin result for the same version on the real system", onclick: () => {
                const failed = runs.some(r => r.result !== "DONE");
                const note = failed ? prompt("Some runs did not end DONE. Why do you accept this version anyway?") : "";
                if (failed && !note) return;
                act("POST", "/api/twin/accept", { program_id: p.program_id, version: p.version, note: note || "" }, "accepted: the real system records this twin result");
              } }, "Accept twin result") : null));
        }))) : h("p", { class: "muted" }, "No programs in the copy yet."),
      h("p", { class: "muted small" }, "A program edited on the real system arrives as a new version. Accepting a result records a logic test for exactly that version; it is not physical verification."));
  }
  function opsCard(v) {
    const e = v.engine, run = can("sim_command"), pc = can("process_control");
    return h("div", { class: "card" }, h("h2", null, "Running now"),
      e.conflicts.map(c => h("div", { class: "card bad" }, h("strong", null, `Conflict on ${c.item}`),
        h("p", null, `Program 1: ${c.running.program_id} v${c.running.version}: ${c.running.consequence}.`),
        h("p", null, `Program 2: ${c.waiting.program_id} v${c.waiting.version}: ${c.waiting.consequence}${c.waiting.window_end_utc ? " (" + simTime(c.waiting.window_end_utc) + ")" : ""}.`),
        h("button", { disabled: !pc, onclick: () => act("POST", "/api/twin/conflict", { conflict_id: c.conflict_id, choice: "KEEP_FIRST" }) }, `Keep ${c.running.program_id}`),
        h("button", { disabled: !pc, onclick: () => act("POST", "/api/twin/conflict", { conflict_id: c.conflict_id, choice: "SWITCH" }) }, `Stop it and run ${c.waiting.program_id}`))),
      e.ops.length ? h("table", { class: "compact" }, h("thead", null, h("tr", null, ["Program", "Origin", "State", "Step", "Runtime", "Measured", ""].map(x => h("th", null, x)))),
        h("tbody", null, e.ops.map(o => h("tr", null, h("td", null, `${o.program_id} v${o.version}`), h("td", null, o.origin), h("td", null, pill(o.state, o.state === "STOP_UNVERIFIED" ? "bad" : "")),
          h("td", null, o.step), h("td", null, `${o.runtime_s} s (${o.remaining_s} s left)`),
          h("td", null, o.requested_L ? `${o.measured_L} of ${o.requested_L} L${o.uncertain_L ? ", " + o.uncertain_L + " L uncertain" : ""}` : (o.measured_L ? o.measured_L + " L" : "")),
          h("td", null, o.state === "RUNNING" ? h("button", { class: "small danger", disabled: !run, onclick: () => act("POST", "/api/twin/stop", { op_id: o.op_id }) }, "Stop") : null)))))
        : h("p", { class: "muted" }, "Nothing is running."));
  }
  function resultsCard(v) {
    return h("details", { class: "card", open: S.open.results || null, ontoggle: e => { S.open.results = e.target.open; } }, h("summary", null, `Results (${v.engine.finished.length})`),
      h("table", { class: "compact small" }, h("thead", null, h("tr", null, ["Ended (twin time)", "Program", "Result", "Why", "Measured"].map(x => h("th", null, x)))),
        h("tbody", null, v.engine.finished.slice().reverse().map(r => h("tr", null, h("td", null, simTime(r.ended_utc)), h("td", null, `${r.program_id} v${r.version}`),
          h("td", null, pill(r.result, r.result === "DONE" ? "ok" : "bad")), h("td", null, r.reason || ""), h("td", null, r.requested_L ? `${r.measured_L} of ${r.requested_L} L${r.uncertain_L ? " (" + r.uncertain_L + " L uncertain)" : ""}` : ""))))));
  }

  async function refresh() {
    if (S.busy) return;
    S.busy = true;
    try {
      const v = await api("GET", "/api/twin");
      S.v = v;
      if (v.runtime_id !== S.rid) S.rid = v.runtime_id;
      const keep = document.activeElement && document.activeElement.tagName === "INPUT" && document.activeElement.closest("#main");
      if (keep) return;               // don't redraw under a field being typed in
      $("main").replaceChildren(controls(v), modeCard(v), opsCard(v), equipmentGrid(v), programsCard(v), resultsCard(v));
    } catch (e) { /* shown by the next action */ }
    finally { S.busy = false; }
  }
  setInterval(() => { if (!$("app").classList.contains("hidden")) refresh(); }, 1000);
  api("GET", "/api/auth/session").then(signedIn).catch(showLogin);
})();
