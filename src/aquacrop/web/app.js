/* AquaCrop browser UI. Vanilla JS, no external assets. All user/config text is inserted with
 * textContent (never parsed as markup) so labels and event text cannot become executable HTML. */
"use strict";
(function () {
  const S = { csrf: null, user: null, perms: [], status: null, lastOk: 0, stale: false, view: "overview",
              cfgVersion: null, tz: "Europe/Stockholm", trendSel: ["analog_A.AI1", "analog_A.AI2", "relay_A.DI1"],
              trendRange: "hour", settingsLoaded: false, plantAt: 0 };
  const W = window.AqWords;
  const STALE_AFTER_MS = 4000;
  const OK_OUTCOMES = new Set(["READBACK_CONFIRMED", "ECHO_CONFIRMED"]);   // an echo is not actuator proof either

  // ---------------------------------------------------------------- DOM helpers
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "style") e.style.cssText = v;  // CSSOM: allowed under the strict CSP
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      e.appendChild(typeof k === "string" || typeof k === "number" ? document.createTextNode(String(k)) : k);
    }
    return e;
  }
  const $ = id => document.getElementById(id);
  // A refresh that was already fetching when the user changed a field or a choice on this page does not
  // rebuild it: that would put back the old value just before the user presses the button next to it.
  const edits = { seq: 0, id: null };
  document.addEventListener("change", e => {
    const v = e.target && e.target.closest ? e.target.closest("section") : null;
    if (v) { edits.seq += 1; edits.id = v.id; }
  }, true);
  let rendering = null;          // the edit count when the current refresh started
  let renderingView = null;      // the page the current refresh is drawing (the user may already have moved on)
  // Fields and choices the user has changed survive the once-a-second rebuild of the page, so a pump picked
  // in a form is still the pump that the button next to it sends, even after the user clicked elsewhere.
  document.addEventListener("input", e => { if (e.target && e.target.dataset) e.target.dataset.dirty = "1"; }, true);
  document.addEventListener("change", e => { if (e.target && e.target.dataset) e.target.dataset.dirty = "1"; }, true);
  function fieldKeys(el) {
    const seen = {};
    return [...el.querySelectorAll("input,select,textarea")].map(f => {
      const card = f.closest(".card"), row = f.closest("tr"), lab = f.closest("label");
      const sig = [f.tagName, f.type, f.placeholder, f.getAttribute("aria-label"), f.name,
        card && card.querySelector("h2") ? card.querySelector("h2").textContent : "",
        row && row.querySelector("td") ? row.querySelector("td").textContent : "",
        lab ? lab.firstChild && lab.firstChild.textContent : ""].join("|");
      seen[sig] = (seen[sig] || 0) + 1;
      return [sig + "#" + seen[sig], f];
    });
  }
  let drawn = 0;
  function mount(id, ...kids) {
    const el = $(id);
    if (rendering !== null && edits.seq !== rendering && edits.id === id) return el;
    const view = renderingView || S.view;
    if (view !== S.view) return el;      // the user moved to another page meanwhile: the next refresh draws that one
    // a refresh must not replace a button held down since it started its requests: the click would be lost
    if (renderingView !== null && el.dataset.view === view && pressed && Date.now() - pressed < 2000) return el;
    const keep = el.dataset.view === view ? new Map(fieldKeys(el).filter(([, f]) => f.dataset.dirty && f.type !== "file")
      .map(([k, f]) => [k, f.type === "checkbox" || f.type === "radio" ? f.checked : f.value])) : new Map();
    const same = el.dataset.view === view;
    const opened = new Map(same ? [...el.querySelectorAll("details > summary")].map(x => [x.textContent, x.parentElement.open]) : []);
    // a part scrolled sideways (the diagram on a phone, a wide table) stays where the user scrolled it
    const scrolled = same ? [...el.querySelectorAll(".diagram-wrap, table")].map(x => x.scrollLeft) : [];
    el.replaceChildren(...kids.flat(2).filter(k => k != null && k !== false));
    if (scrolled.some(Boolean)) [...el.querySelectorAll(".diagram-wrap, table")].forEach((x, i) => { if (scrolled[i]) x.scrollLeft = scrolled[i]; });
    el.dataset.view = view;
    el.dataset.drawn = String(++drawn);    // lets a page that redraws only on change tell a skipped draw
    for (const x of el.querySelectorAll("details > summary")) if (opened.has(x.textContent)) x.parentElement.open = opened.get(x.textContent);
    if (keep.size) for (const [k, f] of fieldKeys(el)) {
      if (!keep.has(k)) continue;
      const v = keep.get(k);
      if (f.type === "checkbox" || f.type === "radio") f.checked = v;
      else if (f.tagName !== "SELECT" || [...f.options].some(o => o.value === v)) f.value = v;
      f.dataset.dirty = "1";
    }
    return el;
  }
  function toast(msg, bad, raw) {
    const t = $("toast"); t.textContent = msg; t.title = raw && raw !== msg ? raw : ""; t.classList.remove("hidden");
    t.style.background = bad ? "var(--bad)" : "";
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 5000);
  }
  function can(p) { return S.perms.includes(p); }
  function simClock(sec) {          // simulated seconds since the run started, as h:mm:ss
    const t = Math.max(0, Math.floor(sec)), hh = Math.floor(t / 3600), mm = Math.floor(t / 60) % 60, ss = t % 60;
    return `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  }
  function fmtTime(iso) {
    if (!iso) return "—";
    return new Intl.DateTimeFormat("sv-SE", { timeZone: S.tz, dateStyle: "short", timeStyle: "medium" }).format(new Date(iso));
  }
  function age(iso) {
    if (!iso) return "—";
    const ref = S.status ? Date.parse(S.status.freshness.server_utc) + (Date.now() - S.lastOk) : Date.now();
    return ((ref - Date.parse(iso)) / 1000).toFixed(1) + " s";
  }
  function num(v, d = 2) { return v == null ? "—" : (typeof v === "boolean" ? (v ? "ON" : "OFF") : Number(v).toFixed(d)); }
  /** "1 second ago", "12 seconds ago", "3 min ago": how old a reading is, in words. */
  function ago(ageS) {
    if (ageS == null || !isFinite(ageS)) return "";
    const s = Math.max(1, Math.round(ageS));
    if (s < 60) return s === 1 ? "1 second ago" : `${s} seconds ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 172800) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }
  /** The site clock time (HH:MM:SS) a reading of this age was taken. */
  function agoClock(ageS) {
    const ref = S.status ? Date.parse(S.status.freshness.server_utc) + (Date.now() - S.lastOk) : Date.now();
    return new Intl.DateTimeFormat("sv-SE", { timeZone: S.tz, timeStyle: "medium" }).format(new Date(ref - 1000 * (ageS || 0)));
  }
  /** A reading's quality in plain words (F-03); the code stays in data-q and the help text explains it. */
  function qBadge(q, ageS) {
    const text = q === "STALE" && ageS != null ? `${W.quality(q)} (last update ${agoClock(ageS)})` : W.quality(q);
    return h("span", { class: "q q-" + q, "data-q": q, title: W.QUALITY_HELP[q] || q }, text);
  }
  function oBadge(o) { return h("span", { class: "origin origin-" + o }, o === "SIMULATED" ? "SIM" : o); }
  function pill(text, cls) { return h("span", { class: "pill " + (cls || "") }, text); }

  // ---------------------------------------------------------------- API
  async function api(method, path, body) {
    const opts = { method, headers: {}, credentials: "same-origin" };
    if (method !== "GET") { opts.headers["X-CSRF-Token"] = S.csrf || ""; }
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetch(path, opts);
    if (r.status === 401) { showLogin(); throw new Error("not authenticated"); }
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      // a structured answer (the recipe draft's field problems) keeps its parts for the page that asked
      const d = data && data.detail, e = new Error(d ? (typeof d === "string" ? d : d.message || JSON.stringify(d)) : r.status);
      e.status = r.status; e.detail = d; throw e;
    }
    return data;
  }
  async function act(method, path, body, okMsg) {
    if (S.stale) { toast("Connection stale: command not sent", true); return null; }
    try {
      const r = await api(method, path, body); if (okMsg) toast(okMsg);
      document.querySelectorAll("section [data-dirty]").forEach(f => { delete f.dataset.dirty; });   // done: the form starts afresh
      refresh(); return r;
    }
    catch (e) { const raw = String(e.message || e); toast(W.refusal(raw), true, raw); return null; }
  }
  function rid() { return "ui_" + crypto.getRandomValues(new Uint32Array(4)).join("").slice(0, 24); }
  function cmd(target, desired) {
    return act("POST", "/api/commands", { backend: "SIMULATED", config_version: S.cfgVersion, target, desired,
      request_id: rid(), reason: "operator via I/O bench" }).then(r => r && toast(`${target} → ${desired ? "ON" : "OFF"}: ${r.outcome}${r.detail ? " (" + r.detail + ")" : ""}`, !OK_OUTCOMES.has(r.outcome)));
  }
  function cbtn(label, onclick, cls) { return h("button", { class: "cmd small " + (cls || ""), onclick, disabled: !can("sim_command") }, label); }

  // ---------------------------------------------------------------- auth
  function showLogin() {
    $("app").classList.add("hidden"); $("pwchange-view").classList.add("hidden"); $("login-view").classList.remove("hidden");
    const b = $("banner"); b.textContent = "Operating mode is shown after sign-in"; b.className = "banner neutral";
    S.armPanel = null;
  }
  async function afterLogin(sess) {
    S.csrf = sess.csrf_token; S.user = sess.username; S.perms = sess.permissions || [];
    if (sess.must_change_password) {
      $("login-view").classList.add("hidden"); $("pwchange-view").classList.remove("hidden"); return;
    }
    $("login-view").classList.add("hidden"); $("pwchange-view").classList.add("hidden"); $("app").classList.remove("hidden");
    $("who").textContent = `${sess.username} (${sess.role}${sess.commissioning ? ", commissioning" : ""})`;
    route(); refresh();
  }
  $("login-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: f.get("username"), password: f.get("password") }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "login failed");
      ev.target.reset(); $("login-error").textContent = "";
      afterLogin(await api("GET", "/api/auth/session"));
    } catch (e) { $("login-error").textContent = e.message; }
  });
  $("pw-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      await api("POST", "/api/auth/password", { old_password: f.get("old"), new_password: f.get("new") });
      ev.target.reset(); toast("Password changed. Log in again."); showLogin();
    } catch (e) { $("pw-error").textContent = e.message; }
  });
  $("logout").addEventListener("click", async () => { try { await api("POST", "/api/auth/logout"); } catch (e) {} showLogin(); });

  // ---------------------------------------------------------------- routing / polling
  function section(view) { return document.getElementById("v-" + view) ? "v-" + view : "v-plant"; }
  function highlightNav() {
    let cur = null;
    document.querySelectorAll("#nav a").forEach(a => {
      const on = a.getAttribute("href") === "#" + S.view;
      a.classList.toggle("active", on);
      if (on) { a.setAttribute("aria-current", "page"); cur = a.textContent; } else a.removeAttribute("aria-current");
    });
    $("nav-current").textContent = cur || "";
  }
  // On a narrow screen the menu is one button (F-09); choosing a page closes it again.
  function menu(open) {
    $("nav").classList.toggle("open", open);
    $("menu-btn").setAttribute("aria-expanded", open ? "true" : "false");
  }
  $("menu-btn").addEventListener("click", () => menu(!$("nav").classList.contains("open")));
  $("nav").addEventListener("click", e => { if (e.target.closest("a")) menu(false); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && $("nav").classList.contains("open")) { menu(false); $("menu-btn").focus(); } });
  $("status-more").addEventListener("click", () => {
    const open = $("status-tech").classList.toggle("hidden") === false;
    $("status-more").setAttribute("aria-expanded", open ? "true" : "false");
    $("status-more").textContent = open ? "Hide details" : "Details";
  });
  function route() {
    S.view = (location.hash || "#overview").slice(1);
    if (S.view === "trends") { history.replaceState(null, "", "#history"); S.view = "history"; }   // the page's earlier name
    S.rendered = null;
    highlightNav();
    const sec = section(S.view);
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("hidden", v.id !== sec));
    refresh();
  }
  window.addEventListener("hashchange", route);

  let pressed = 0;                   // time a pointer button went down; 0 once it is released
  window.addEventListener("pointerdown", () => { pressed = Date.now(); }, true);
  ["pointerup", "pointercancel", "blur"].forEach(ev => window.addEventListener(ev, () => { pressed = 0; }, true));
  let busy = false, again = false;
  async function refresh(queue = true) {
    if ($("app").classList.contains("hidden")) return;
    if (busy) { again = again || queue; return; }   // a page change or an action runs right after the one in flight
    busy = true; again = false;
    rendering = edits.seq;
    const view = S.view;
    renderingView = view;
    try {
      S.status = await api("GET", "/api/status");
      S.lastOk = Date.now(); S.cfgVersion = S.status.config_version; S.tz = S.status.timezone;
      setStale(false);
      await plantForStatus();
      renderBanner();
      const ae = document.activeElement;   // don't rebuild a view while the user is typing in it
      const typing = ae && ae.closest && ae.closest("#" + section(S.view)) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)
        || Date.now() - pressed < 2000;     // nor between pressing and releasing a button: the click would be lost
      const fn = VIEWS[S.view] || (window.AqPlant && (isInst() || isReal()) ? window.AqPlant.resolve(S.view) : null);
      try {
        if (fn && !typing) await fn();
        else if (!fn && !typing) mount("v-plant", h("div", { class: "card" }, "This page does not exist in this profile."));
      } catch (e) {
        // a page that cannot be drawn says so; it is not a lost connection unless a request failed
        if (e instanceof TypeError && !/fetch/i.test(e.message)) {
          console.error(e);
          mount(section(S.view), h("div", { class: "card" }, h("p", { class: "error" }, "This page could not be shown: " + e.message), h("p", { class: "muted" }, "Reload the page. If it happens again, note the time and what you pressed.")));
        } else throw e;
      }
      if (S.view === view) S.rendered = view;
    } catch (e) {
      if (String(e.message) !== "not authenticated") setStale(true);
    } finally {
      busy = false; rendering = null; renderingView = null;
      if (again) refresh();
    }
  }
  function setStale(stale) {
    S.stale = stale;
    $("stale").classList.toggle("hidden", !stale);
    document.body.classList.toggle("disabled-by-stale", stale);
    if (stale) {
      $("stale-age").textContent = S.lastOk ? ((Date.now() - S.lastOk) / 1000).toFixed(0) : "?";
      document.querySelectorAll(".pill.ok").forEach(p => { p.className = "pill warn"; p.textContent = "STALE"; });
    }
  }
  setInterval(() => { if (S.lastOk && Date.now() - S.lastOk > STALE_AFTER_MS) setStale(true); }, 1000);
  setInterval(() => refresh(false), 1000);

  function renderBanner() {
    const st = S.status;
    const b = $("banner"); b.textContent = st.banner;
    b.className = "banner " + (st.banner.startsWith("SIMULATION") ? "sim" : st.banner.includes("ARMED") ? "armed" : "real");
    const real = st.profile !== "SIMULATED";
    document.querySelectorAll('#nav a[href="#twin"]').forEach(a => a.classList.toggle("hidden", real));
    document.querySelectorAll('#nav a[href="#control"]').forEach(a => a.classList.toggle("hidden", real || isInst()));
    // the developer I/O board is for the people who commission the modules: not for a viewer account
    $("nav-board").classList.toggle("hidden", !(can("sim_command") || can("process_control") || can("io_assign")));
    if ((real || isInst()) && window.AqPlant) window.AqPlant.buildNav();   // process pages (read-only on hardware, ADR-0009 addendum)
    plainStatus(st);
    const lines = [];
    if (real) {
      lines.push(`AquaCrop ${st.software_version} · Profile ${st.profile} · config ${st.config_id}@${st.config_version} · ${st.physical_writes}` +
        ` · newest data ${num(st.freshness.newest_sample_age_s, 1)} s old · ${S.tz}`);
    } else {
      const t = st.twin, c = st.controller;
      const ctl = isInst() ? `processes ${c.state} · recipe ${c.recipe || "none active"}` : `controller ${c.state}/${c.phase}`;
      lines.push(W.HELP.twin,
        `AquaCrop ${st.software_version} · Profile ${st.profile} · config ${st.config_id}@${st.config_version} · ${ctl}` +
        ` · twin ${t.activity} ${t.speed}× · simulated time ${simClock(t.sim_s)} since the run started · source ${t.source}${t.scenario ? " · scenario " + t.scenario : ""}` +
        ` · newest data ${num(st.freshness.newest_sample_age_s, 1)} s old · site clock ${fmtTime(new Date().toISOString())} ${S.tz}`);
    }
    $("status-tech").replaceChildren(...lines.map(x => h("div", null, x)));
  }
  /** The first line an operator reads (F-03): where this runs, watering, the main tank, how fresh the readings are. */
  function plainStatus(st) {
    const parts = [];
    parts.push(st.profile === "SIMULATED" ? h("strong", { title: W.HELP.twin }, "Digital Twin") : h("strong", null, "Real equipment, read only"));
    const ir = st.controller && st.controller.processes ? st.controller.processes.IRRIGATION : null;
    if (ir) parts.push({ STOPPED: "Watering stopped", AUTO: "Watering automatically", MANUAL: "Watering in manual mode",
      FAULT_LOCKOUT: "Watering stopped because of a problem" }[ir.state] || "Watering: " + W.state(ir.state));
    else if (st.profile === "SIMULATED" && st.controller && st.controller.state) parts.push(`Controller ${W.state(st.controller.state).toLowerCase()}`);
    const lvl = S.plant && S.plant.signals && S.plant.signals["tank.MAIN.level"], m = lvl && lvl.sample;
    if (m && m.value != null && ["GOOD", "OVERRIDDEN"].includes(m.quality)) parts.push(`Main tank ${Math.round(100 * m.value / S.plant.layout.tanks_L.MAIN)}%`);
    else if (lvl) parts.push(`Main tank: ${W.quality(m ? m.quality : "UNAVAILABLE").toLowerCase()}`);
    const age = st.freshness.newest_sample_age_s;
    parts.push(age == null ? "No reading yet" : `Last reading ${ago(age)}`);
    const n = st.alarms ? st.alarms.active + 0 : 0;
    if (n) parts.push(h("a", { href: "#alarms" }, n === 1 ? "1 problem" : `${n} problems`));
    $("status-plain").replaceChildren(...parts.flatMap((x, i) => i ? [" · ", x] : [x]));
  }
  // the plant readings behind the status line, at most every 5 s (the process pages fetch them anyway)
  async function plantForStatus() {
    if (!(isInst() || isReal()) || Date.now() - S.plantAt < 5000) return;
    try { S.plant = await api("GET", "/api/plant"); S.plantAt = Date.now(); } catch (e) { /* the line shows what it has */ }
  }

  // ---------------------------------------------------------------- views
  const VIEWS = {};

  function isReal() { return S.status && S.status.profile !== "SIMULATED"; }
  function isInst() { return S.status && S.status.topology === "installation"; }
  function devTable(st) {
    return h("table", null, h("thead", null, h("tr", null, ["Device", "Bus/address", "Health", "Failures", "Last error", "Last OK age"].map(x => h("th", null, x)))),
      h("tbody", null, Object.entries(st.devices).map(([k, d]) => h("tr", null, h("td", null, k), h("td", null, d.bus_id + " @" + d.address),
        h("td", null, d.online ? pill("online", "ok") : pill("FAILED", "bad")), h("td", null, String(d.consecutive_failures)),
        h("td", { class: "mono" }, d.last_error || ""), h("td", null, d.last_ok_utc ? age(d.last_ok_utc) : "never")))));
  }

  function gib(b) { return b == null ? "—" : (b / 1073741824).toFixed(2) + " GiB"; }
  function mib(b) { return b == null ? "—" : (b / 1048576).toFixed(0) + " MiB"; }
  function storageText(x) {
    const pause = x.snapshot_pause && x.snapshot_pause.count ? ` · ${x.snapshot_pause.count} snapshots not written (low disk)` : "";
    return `DB ${gib(x.db_bytes)} of ${gib(x.budget_bytes)} budget · ${gib(x.free_bytes)} free` +
      (x.projected_window_bytes != null ? ` · ${x.retention_days}-day projection ${gib(x.projected_window_bytes)}` : "") +
      ` · snapshot every ${x.recording_interval_s} s` + (x.snapshots_paused ? " · SNAPSHOTS PAUSED (low disk)" : "") + pause +
      (x.wal_over_warning ? ` · WAL ${mib(x.wal_bytes)} (a long reader is holding it)` : "") +
      (x.retention_provenance === "DEMO" ? " · DEMO retention: nothing is deleted" : "");
  }
  function retentionCard(r) {
    const v = r || {}, held = v.held, prop = v.proposal, last = v.last_result;
    const rows = [h("dt", null, "Policy"), h("dd", null, v.policy === "DEMO" ? pill("DEMO: nothing is deleted; the database grows without bound", "warn") : (v.policy || "—")),
      h("dt", null, "State"), h("dd", null, held ? pill("HELD: " + held.code, "bad") : (v.running ? pill("running", "ok") : pill(v.policy === "APPROVED" ? "active" : "off", "ok")))];
    if (held) rows.push(h("dt", null, "Why"), h("dd", null, held.reason));
    if (last && last.deleted) rows.push(h("dt", null, "Last run"), h("dd", null, `${Object.values(last.deleted).reduce((a, b) => a + b, 0)} rows older than ${last.cutoff_utc}` + (last.incomplete ? ` (stopped early: ${last.incomplete})` : "")));
    const kids = [h("h2", null, "Retention"), h("dl", { class: "kv" }, ...rows)];
    if (held && prop) {
      kids.push(h("p", null, `Proposed cutoff ${prop.cutoff_utc} (clock reads ${prop.wall_utc}). Newest record of an earlier process: ${prop.newest_earlier_process_record_utc || "none"}.`),
        h("p", null, "Rows a release would delete: " + Object.entries(prop.would_delete || {}).map(([k, n]) => `${k} ${n}`).join(", ")),
        h("p", { class: "muted" }, prop.note + " Acknowledging the RETENTION_HELD alarm does not release it."));
      if (can("config_admin")) {
        const ok = h("input", { type: "checkbox" });
        const btn = h("button", { class: "cmd small", disabled: true, onclick: async () => {
          const res = await act("POST", "/api/retention/confirm-clock", { cutoff_utc: prop.cutoff_utc }, "retention released; the cutoff and your name are recorded");
          if (res) { S.settingsLoaded = false; VIEWS.settings(); }
        } }, "Confirm date and release");
        ok.onchange = () => { btn.disabled = !ok.checked; };
        kids.push(h("div", { class: "row" }, h("label", null, ok, h("span", null, " I checked the date and time against an independent source")), btn));
      } else {
        kids.push(h("p", { class: "muted" }, "Releasing a hold requires an administrator."));
      }
    }
    return h("div", { class: "card" }, ...kids);
  }
  // Records whose UTC stamps an administrator quarantined (ADR-0011): the stored time is shown unchanged, flagged.
  function susPill(r) {
    return r && r.timestamp_suspect ? [" ", h("span", { class: "pill warn", title: `stored time unchanged; quarantined by ${r.quarantine_id}` }, "SUSPECT TIME")] : null;
  }
  // One quarantine and its disposition review (brief §9): quarantined records are never pruned by age alone.
  // KEEP keeps them; RELEASE lets retention remove them once the quarantine itself is older than the window.
  function quarantineRow(x, due, days) {
    const disp = x.disposition || "UNREVIEWED";
    const state = disp === "KEEP" ? pill("KEPT", "ok") : disp === "RELEASE" ? pill("RELEASED TO RETENTION", "warn")
      : due ? pill("REVIEW DUE", "bad") : pill("NOT REVIEWED", "warn");
    const kids = [h("p", null, h("strong", null, x.quarantine_id), " ", state, " ", h("span", { class: "muted" },
      `epoch ${x.clock_epoch}, confirmed by ${x.confirmed_by} at ${fmtTime(x.confirmed_utc)}; ids ` +
      Object.entries(x.ranges).map(([t, r]) => `${t} ${r[0]}–${r[1]}`).join(", ") + ". Records kept with their original stamps."))];
    if (x.reviewed_by) kids.push(h("p", { class: "muted" }, `Reviewed ${disp} by ${x.reviewed_by} at ${fmtTime(x.reviewed_utc)}: ${x.review_reason || ""}`));
    else kids.push(h("p", { class: "muted" }, due ? `The quarantine is older than the ${days}-day window; its records stay until an administrator decides.`
      : `Its records are not removed by age until an administrator reviews them.`));
    if (can("config_admin")) {
      const reason = h("input", { type: "text", maxlength: "500", placeholder: "what was checked, and why", "aria-label": `Review reason for ${x.quarantine_id}` });
      const send = (d, ok) => async () => {
        if (!reason.value.trim()) { toast("Write the reason for this review first.", "bad"); return; }
        const res = await act("POST", "/api/history/quarantine/review", { quarantine_id: x.quarantine_id, disposition: d, reason: reason.value.trim() }, ok);
        if (res) { S.settingsLoaded = false; VIEWS.settings(); }
      };
      kids.push(h("div", { class: "row" }, reason,
        h("button", { class: "cmd small", onclick: send("KEEP", "records kept until a later review") }, "Keep records"),
        h("button", { class: "cmd small", onclick: send("RELEASE", `records leave with the rolling ${days}-day window, counted from the quarantine time`) }, "Release to retention")),
        h("p", { class: "muted" }, "Neither choice deletes anything now or releases a retention hold."));
    }
    return h("div", { class: "subcard" }, ...kids);
  }
  function quarantineCard(q) {
    if (!q) return h("div", { class: "card" }, h("h2", null, "Clock and timestamps"), h("p", { class: "muted" }, "Timestamp state unavailable."));
    const prop = q.proposal || {}, rec = q.recorder || {};
    const kids = [h("h2", null, "Clock and timestamps"), h("dl", { class: "kv" },
      h("dt", null, "Clock epoch"), h("dd", null, String(q.clock_epoch), q.clock_epoch ? h("span", { class: "muted" }, " (snapshot ids carry /e" + q.clock_epoch + "/)") : null),
      h("dt", null, "Recorder"), h("dd", null, rec.clock_behind ? [pill("WAITING: stored snapshots are later than the clock", "bad"), h("div", { class: "muted" }, `since ${fmtTime(rec.clock_behind.since_utc)}; newest stored ${fmtTime(rec.clock_behind.newest_label_utc)}`)] : pill(rec.state === "RUNNING" ? "recording" : (rec.state || "unknown"), rec.state === "RUNNING" ? "ok" : "warn")),
      h("dt", null, "Quarantines"), h("dd", null, q.quarantines.length ? `${q.quarantines.length} in force` : "none"))];
    q.quarantines.slice().reverse().forEach(x => kids.push(quarantineRow(x, (q.review_due || []).includes(x.quarantine_id), q.retention_days)));
    if (prop.needed) {
      kids.push(h("h3", null, "Records stamped later than the clock"),
        h("p", null, `The clock reads ${fmtTime(prop.wall_utc)} (${S.tz}).`),
        h("table", null, h("thead", null, h("tr", null, ["Records", "Count", "Stamped from", "to"].map(x => h("th", null, x)))),
          h("tbody", null, Object.entries(prop.counts || {}).filter(([, n]) => n).map(([t, n]) => {
            const sp = (prop.stamped_utc || {})[t] || []; return h("tr", null, h("td", null, t), h("td", null, String(n)), h("td", null, fmtTime(sp[0])), h("td", null, fmtTime(sp[1]))); }))),
        h("p", null, `From ${prop.start.kind}${prop.start.utc ? " at " + fmtTime(prop.start.utc) : ""}: ${prop.start.why}. To ${prop.end.kind}${prop.end.utc ? " at " + fmtTime(prop.end.utc) : ""}: ${prop.end.why}.`),
        h("p", { class: "muted" }, prop.note));
      if (can("config_admin")) {
        const ok = h("input", { type: "checkbox" });
        const btn = h("button", { class: "cmd small", disabled: true, onclick: async () => {
          const res = await act("POST", "/api/history/quarantine", { proposal_id: prop.proposal_id, clock_checked: true },
            "records flagged as suspect; recording resumes and retention stays held until you confirm a cutoff");
          if (res) { S.settingsLoaded = false; VIEWS.settings(); }
        } }, "Quarantine suspect records");
        ok.onchange = () => { btn.disabled = !ok.checked; };
        kids.push(h("div", { class: "row" }, h("label", null, ok, h("span", null, " I checked the current date and time against an independent source")), btn),
          h("p", { class: "muted" }, "This keeps every record and its stored time, flags them, and holds retention. It deletes nothing and authorizes no deletion."));
      } else {
        kids.push(h("p", { class: "muted" }, "Quarantining requires an administrator."));
      }
    } else {
      kids.push(h("p", { class: "muted" }, prop.note || ""));
    }
    return h("div", { class: "card wide" }, ...kids);
  }
  // SD-card backups and encrypted cloud copies (ADR-0012). Everything shown comes from the controller's
  // state files; the page never contacts the cloud.
  function offsiteCard(o) {
    if (!o) return h("div", { class: "card" }, h("h2", null, "Off-device backups"), h("p", { class: "muted" }, "Off-device backup state unavailable."));
    const res = (r, okText) => !r ? "none yet" : r.ok ? [pill(okText || "ok", "ok"), " ", fmtTime(r.utc)] : [pill("FAILED" + (r.code ? ": " + r.code : ""), "bad"), " ", fmtTime(r.utc), h("div", { class: "muted" }, r.error || r.integrity || "")];
    const card = o.sd_card.last_check, q = o.queue || [], pol = o.policy, plan = pol.last_plan;
    const rows = [
      h("dt", null, "State"), h("dd", null, o.enabled ? pill("ACTIVATED", "ok") : pill("NOT ACTIVATED", "warn"), o.activation.note ? h("div", { class: "muted" }, o.activation.note) : null),
      h("dt", null, "SD card"), h("dd", null, o.sd_card.mount_path ? `${o.sd_card.mount_path} (UUID ${o.sd_card.filesystem_uuid})` : "TBD",
        card ? h("div", null, card.ok ? [pill("identity verified", "ok"), ` ${gib(card.free_bytes)} free · checked ${fmtTime(card.utc)}`] : [pill(card.code, "bad"), h("div", { class: "muted" }, card.error)]) : null),
      h("dt", null, "Last SD backup"), h("dd", null, res(o.last_backup, o.last_backup && o.last_backup.file ? `${o.last_backup.file} · ${gib(o.last_backup.bytes)}` : "ok")),
      h("dt", null, "On the card"), h("dd", null, o.local ? `${o.local.count} backups, ${gib(o.local.bytes)}; newest ${o.local.newest || "—"}` : "—"),
      h("dt", null, "Upload queue"), h("dd", null, q.length ? [pill(`${q.length} waiting`, "warn"), h("div", { class: "muted" }, `oldest ${q[0].name}; ${q[0].attempts} attempt(s); next try ${fmtTime(q[0].next_attempt_utc)}${q[0].last_error ? "; last error: " + q[0].last_error : ""}`)] : "empty"),
      h("dt", null, "Cloud copies"), h("dd", null, o.cloud.remote ? `${o.cloud.copies || 0} verified (cryptcheck) in ${o.cloud.remote}${o.cloud.newest ? "; newest " + o.cloud.newest : ""}` : "TBD (account and folder not confirmed)",
        o.cloud.account_note ? h("div", { class: "muted" }, o.cloud.account_note) : null),
      h("dt", null, "Last upload"), h("dd", null, res(o.last_upload)),
      h("dt", null, "Restore drill"), h("dd", null, res(o.drill.last, o.drill.last && o.drill.last.backup ? "restored " + o.drill.last.backup : "ok"),
        o.drill.due ? [" ", pill(`due (every ${o.drill.interval_days} days)`, "warn")] : null,
        h("div", { class: "muted" }, `from the newest cloud copy into SD-card scratch space; needs ${gib(o.drill.min_scratch_bytes)} free plus ${gib(o.drill.headroom_bytes)} headroom`))];
    const probs = Object.values(o.problems || {}).filter(Boolean);
    const kids = [h("h2", null, "Off-device backups"), h("dl", { class: "kv" }, ...rows)];
    if (probs.length) kids.push(h("div", null, probs.map(m => h("p", { class: "error" }, m))));
    kids.push(h("h3", null, "Copy policy"),
      h("p", null, `${pol.local_daily} daily copies on the SD card; ${pol.cloud_daily} daily plus ${pol.cloud_monthly} monthly copies in the cloud.`),
      h("p", null, pol.active ? [pill("pruning enabled", "ok"), ` confirmed by ${pol.confirmed.confirmed_by} at ${fmtTime(pol.confirmed.confirmed_utc)}`]
        : [pill("pruning off until confirmed", "warn"), pol.confirmed ? " (the policy changed since the last confirmation)" : " Nothing is deleted from the card or the cloud until an administrator confirms these counts."]));
    if (plan) kids.push(h("p", { class: "muted" }, `Last plan (${fmtTime(plan.utc)}): would delete ${plan.local.delete.length} from the card and ${plan.cloud.delete.length} from the cloud` +
      (plan.local.kept_for_upload && plan.local.kept_for_upload.length ? `; ${plan.local.kept_for_upload.length} kept until uploaded` : "") + (plan.refused ? `; refused: ${plan.refused}` : "")));
    if (!pol.active && can("config_admin")) {
      const ok = h("input", { type: "checkbox" });
      const btn = h("button", { class: "cmd small", disabled: true, onclick: async () => {
        const r = await act("POST", "/api/offsite/policy", { policy_hash: pol.policy_hash, confirm: true }, "copy policy confirmed; pruning follows it from the next daily run");
        if (r) { S.settingsLoaded = false; VIEWS.settings(); }
      } }, "Confirm policy and enable pruning");
      ok.onchange = () => { btn.disabled = !ok.checked; };
      kids.push(h("div", { class: "row" }, h("label", null, ok, h("span", null, ` I reviewed these copy counts (policy ${pol.policy_hash})`)), btn));
    }
    return h("div", { class: "card wide" }, ...kids);
  }
  function backupCard(b) {
    const d = b.restore_drill;
    return h("div", { class: "card" }, h("h2", null, "Backups"), h("dl", { class: "kv" },
      h("dt", null, "Local copies"), h("dd", null, `${b.local_count} of ${b.keep} kept, ${gib(b.local_bytes)}` + (b.newest ? `, newest ${b.newest}` : "")),
      h("dt", null, "Last run"), h("dd", null, b.running ? pill("running", "ok") : (b.last ? (b.last.ok ? pill("published " + b.last.file, "ok") : pill("FAILED: " + b.last.error, "bad")) : "none in this process")),
      h("dt", null, "Restore drill"), h("dd", null, d ? [d.ok ? pill("ok", "ok") : pill("FAILED", "bad"), ` ${d.utc}`] : "never run", b.restore_drill_due ? [" ", pill(`due (every ${b.restore_drill_interval_days} days)`, "warn")] : null),
      h("dt", null, "Off-device"), h("dd", null, b.offsite_enabled ? pill("SD card + cloud", "ok") : pill("NOT ACTIVATED", "warn"), " ", b.off_device)),
      h("button", { class: "cmd small", disabled: !can("backup") || b.running, onclick: () => act("POST", "/api/backup", undefined, "backup started; it ends with a BACKUP_CREATED or BACKUP_FAILED event") }, "Create backup now"));
  }

  async function hwOverview() {
    const st = S.status, al = await api("GET", "/api/alarms"), lease = st.bench_lease;
    const diag = window.AqPlant ? await window.AqPlant.hwDiagram().catch(() => null) : null;
    mount("v-overview", diag,
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "System"), h("dl", { class: "kv" },
          h("dt", null, "Profile"), h("dd", null, st.profile),
          h("dt", null, "Physical writes"), h("dd", null, lease ? pill(st.physical_writes, "bad") : pill(st.physical_writes, "ok")),
          h("dt", null, "Bus workers"), h("dd", null, st.acquisition_task.ok ? pill("polling", "ok") : pill(st.acquisition_task.state, "bad")),
          h("dt", null, "Storage"), h("dd", null, st.storage.ok ? pill("ok", "ok") : pill("DEGRADED: " + st.storage.reason, "bad"),
            " ", storageText(st.storage)),
          h("dt", null, "Live control"), h("dd", null, st.live_control))),
        h("div", { class: "card" }, h("h2", null, "Digital output modes (relay and transistor modules)"),
          h("table", null, h("tbody", null, Object.entries(st.relay_modes).map(([k, m]) => h("tr", null, h("td", null, k),
            h("td", { class: "mono" }, m ? m.join(" ") : "not read"),
            h("td", null, m && m.every(x => x === "NORMAL") ? pill("all NORMAL", "ok") : pill("bench writes blocked", "warn"))))))),
        h("div", { class: "card" }, h("h2", null, `Active alarms (${al.open.length})`),
          al.open.length ? h("table", null, h("tbody", null, al.open.map(o => h("tr", null, h("td", null, pill(o.severity, o.severity === "CRITICAL" ? "bad" : "warn")),
            h("td", null, o.rule_id), h("td", null, o.target), h("td", null, o.active ? "active" : "cleared"))))) : h("p", { class: "muted" }, "None"))),
      h("div", { class: "card" }, h("h2", null, "Devices (each polled and reported independently)"), devTable(st)),
      driveCard(st.drives));
  }

  // The shared drive's Modbus interface (ADR-0015): read every poll; written only under a bench lease.
  function driveCard(drives) {
    const rows = Object.entries(drives || {});
    if (!rows.length) return null;
    return h("div", { class: "card" }, h("h2", null, "Drive interface"),
      h("table", null, h("thead", null, h("tr", null, ["Drive", "Interface", "Register map", "Status word", "Running", "Fault", "Ready", "Frequency", "Link"].map(x => h("th", null, x)))),
        h("tbody", null, rows.map(([t, d]) => {
          const s = d.status || {};
          return h("tr", null, h("td", { class: "mono" }, t),
            h("td", null, d.kind === "MODBUS_RTU" ? `Modbus RTU ${d.bus_id} @ ${d.address}` : pill(d.kind, "warn")),
            h("td", null, d.provenance ? pill(d.provenance, d.provenance === "VERIFIED" ? "ok" : "warn") : "none"),
            h("td", { class: "mono" }, s.status_word || "—"),
            h("td", null, s.running == null ? "—" : s.running ? "yes" : "no"),
            h("td", null, s.fault == null ? "—" : s.fault ? pill("FAULT", "bad") : "no"),
            h("td", null, s.ready == null ? "—" : s.ready ? "yes" : "no"),
            h("td", { class: "mono" }, s.frequency == null ? "—" : num(s.frequency) + " Hz"),
            h("td", null, d.kind !== "MODBUS_RTU" ? "not configured" : d.online ? pill("online", "ok") : pill("FAILED: " + (d.last_error || "not read"), "bad")));
        }))),
      h("p", { class: "muted" }, "The status word is read on every poll and never written. A PROPOSED map is checked on the bench (CP-06) before it is VERIFIED; the hardwired running and fault inputs stay the proof the processes use."));
  }

  // Relay (D) outputs are relay contacts; IO 8CH outputs are open-drain transistor sinks (DC, current-limited)
  function outputPill(g) {
    if (g.output_type === "OPEN_DRAIN_SINK")
      return pill(`TRANSISTOR SINK — not relay contacts${g.output_current_limit_ma ? `, max ${g.output_current_limit_ma} mA` : ""}`, "warn");
    return g.output_type === "RELAY_CONTACT" ? pill("relay contacts") : null;
  }

  async function hwIO() {
    const d = await api("GET", "/api/points"), b = await api("GET", "/api/bench"), lease = b.lease;
    const allowed = new Set(lease ? lease.allowlist : []);
    const bench = (target, desired) => act("POST", "/api/bench/command", { backend: "REAL_BENCH", config_version: S.cfgVersion,
      lease_id: lease.lease_id, target, desired, request_id: rid(), reason: "bench test" })
      .then(r => r && toast(`REAL ${target} → ${desired ? "ON" : "OFF"}: ${r.outcome}${r.detail ? " (" + r.detail + ")" : ""}`, !OK_OUTCOMES.has(r.outcome)));
    const groups = d.devices.map(g => h("div", { class: "card" },
      h("div", { class: "row" }, h("h3", null, `${g.device_uid} — ${g.model} (SKU ${g.sku}) · ${g.bus_id} @ ${g.address}`),
        g.online ? pill("online", "ok") : pill("FAILED: " + (g.last_error || ""), "bad"), outputPill(g)),
      h("table", null, h("thead", null, h("tr", null, ["Point", "Label", "Zero-based addr", "Mode", "Raw", "Scaled", "Quality/origin", "Detail", "Age", "Bench"].map(x => h("th", null, x)))),
        h("tbody", null, g.points.map(p => {
          const s = p.sample || {};
          const ctl = p.kind === "DO" && allowed.has(p.id) && b.can_arm ? h("span", null,
            h("button", { class: "cmd small danger", onclick: () => bench(p.id, true) }, "ON"), " ",
            h("button", { class: "cmd small ghost", onclick: () => bench(p.id, false) }, "OFF")) : null;
          return h("tr", null, h("td", { class: "mono" }, p.id), h("td", null, p.label || ""),
            h("td", { class: "mono" }, "0x" + p.register_address.toString(16).padStart(4, "0")), h("td", null, p.mode || ""),
            h("td", { class: "mono" }, s.raw == null ? "—" : String(s.raw) + (s.raw_unit && s.raw_unit !== "bool" ? " " + s.raw_unit : "")),
            h("td", { class: "mono" }, s.value == null ? "—" : num(s.value) + (s.unit ? " " + s.unit : "")),
            h("td", null, qBadge(s.quality || "UNAVAILABLE"), " ", s.origin ? oBadge(s.origin) : null),
            h("td", { class: "muted" }, s.reason || ""), h("td", null, age(s.acquired_utc)), h("td", null, ctl));
        })))));
    const drives = (b.drive_targets || []).map(t => {
      const d = (b.drives || {})[t] || {}, s = d.status || {};
      const ctl = allowed.has(t) && b.can_arm ? h("span", null,
        h("button", { class: "cmd small danger", onclick: () => bench(t, true) }, "Run"), " ",
        h("button", { class: "cmd small ghost", onclick: () => bench(t, false) }, "Stop")) : null;
      return h("tr", null, h("td", { class: "mono" }, t), h("td", null, `${d.bus_id} @ ${d.address}`),
        h("td", null, pill(d.provenance || "?", d.provenance === "VERIFIED" ? "ok" : "warn")),
        h("td", { class: "mono" }, s.status_word || "—"),
        h("td", null, s.running == null ? "—" : s.running ? "running" : "stopped", s.fault ? [" ", pill("FAULT", "bad")] : null),
        h("td", null, d.online ? pill("online", "ok") : pill("FAILED: " + (d.last_error || "not read"), "bad")), h("td", null, ctl));
    });
    if (drives.length) groups.push(h("div", { class: "card" }, h("h3", null, "Drives (Modbus control register; status word read every poll)"),
      h("table", null, h("thead", null, h("tr", null, ["Target", "Bus", "Register map", "Status word", "State", "Link", "Bench"].map(x => h("th", null, x)))), h("tbody", null, drives)),
      h("p", { class: "muted" }, "Run writes the configured run sequence, Stop the stop value, each frame once. The echo proves the drive received it, not that the motor turns. Test with the motor disconnected.")));
    let panel;
    if (lease) {
      panel = h("div", { class: "card armed" }, h("h2", null, "BENCH WRITES ARMED"),
        h("p", null, `Lease for ${lease.username}, ${Math.round(lease.remaining_s)} s left. Allowlist: ${lease.allowlist.join(", ")}.`),
        h("p", null, `Setup: ${lease.setup_description}`),
        h("p", { class: "muted" }, "Coil readback and drive echo are not actuator proof. Ending the lease sends nothing; outputs and drives stay as they are."),
        h("button", { class: "cmd danger", onclick: () => act("POST", "/api/bench/lease/revoke", {}, "lease ended") }, "End lease now"));
    } else if (b.can_arm && S.armPanel) {
      panel = S.armPanel;                  // keep typed values and ticked boxes across polls
    } else if (b.can_arm) {
      const conf = h("input", { placeholder: b.confirmation_phrase, autocomplete: "off", style: "width:320px" });
      const setup = h("input", { placeholder: "describe the isolated bench setup", style: "width:420px" });
      const dur = h("input", { type: "number", min: b.duration_range_s[0], max: b.duration_range_s[1], value: 600, style: "width:90px" });
      const boxes = b.relay_outputs.concat(b.drive_targets || []).map(id => ({ id, el: h("input", { type: "checkbox" }) }));
      panel = h("div", { class: "card" }, h("h2", null, "Arm a bench lease (commissioning)"),
        h("p", null, "Only on an isolated bench with every production load disconnected. Writes go to real relays and drives."),
        h("div", { class: "row" }, ...boxes.map(x => h("label", { class: "mono" }, x.el, " ", x.id))),
        h("div", { class: "row" }, h("label", null, "Type: ", h("strong", null, b.confirmation_phrase), " ", conf)),
        h("div", { class: "row" }, setup, h("label", null, "Duration (s) ", dur)),
        h("button", { class: "cmd danger", onclick: () => act("POST", "/api/bench/lease", { confirmation: conf.value, setup_description: setup.value,
          allowlist: boxes.filter(x => x.el.checked).map(x => x.id), duration_s: Number(dur.value) }, "lease armed") }, "Arm lease"));
      S.armPanel = panel;
    } else {
      panel = h("div", { class: "card" }, h("strong", null, "REAL — READ ONLY."), " ",
        h("span", { class: "muted" }, "Bench writes need a user with the commissioning permission to arm a lease."));
    }
    if (lease || !b.can_arm) S.armPanel = null;
    mount("v-io", panel, groups);
  }

  // ------------------------------------------------ installation topology (simulation; interim pages)
  function procPill(s) { return pill(s, s === "FAULT_LOCKOUT" ? "bad" : s === "AUTO" ? "ok" : s === "MANUAL" ? "warn" : ""); }
  function instWaterCard(pl) {
    const T = { MAIN: "Main tank", A: "Fertiliser tank A", B: "Fertiliser tank B", ACID: "Acid tank", SUMP: "Drainage tank" };
    return h("div", { class: "card" }, h("h2", null, "Water in the model"), h("dl", { class: "kv" },
      ...Object.entries(pl.tanks).flatMap(([k, t]) => [h("dt", null, W.TANK[k] || T[k] || k), h("dd", null, `${W.n0(t.volume_L.toFixed(0))} of ${W.n0(t.capacity_L)} L`)]),
      ...Object.entries(pl.flows_L_min).filter(([, q]) => q > 0.05).flatMap(([k, q]) => [h("dt", null, "Flow: " + W.signal("flow." + k).replace(/ water flow$| flow$/, "")), h("dd", null, q.toFixed(1) + " L/min")]),
      h("dt", null, "Acidity (pH) / nutrient strength (EC)"), h("dd", null, `${num(pl.chemistry.probe_ph)} / ${num(pl.chemistry.probe_ec_mS_cm)} mS/cm (synthetic)`)),
      h("p", { class: "muted" }, "These are the model's own values. The process pages show what the program reads from the simulated sensors instead. Tank sizes and equipment are the owner's; the behaviour of the water, levels and chemistry is a demonstration model."));
  }
  // simulated faults, in plain words; the codes stay the values sent
  const FAULT_KIND = { drive: "Pump drive", drive_comm: "Pump drive data link", contactor: "Pump selector", pump: "Pump", valve: "Valve",
    mains: "Mains water", safety: "Safety circuit" };
  const FAULT_NAME = { TRIP: "trips (drive fault)", NO_RUN: "does not start", NO_FLOW: "runs but no water flows", STUCK_ON: "does not switch off",
    WELDED: "stays closed (welded)", NO_CLOSE: "does not close", STUCK_CLOSED: "stays closed", STUCK_OPEN: "stays open", NO_SUPPLY: "no mains water",
    CHAIN_OPEN: "safety circuit opens", NO_RESPONSE: "does not answer", EXCEPTION: "answers with an error", IGNORES_WRITES: "ignores commands" };
  async function instTwin() {
    const tw = await api("GET", "/api/twin"), sn = tw.snapshot, pl = sn.plant;
    const pr = await api("GET", "/api/processes").catch(() => null);
    if (!S.scenarios) S.scenarios = await api("GET", "/api/scenarios");
    const tc = (action, extra, msg) => act("POST", "/api/twin/control", Object.assign({ action }, extra || {}), msg || action);
    // O-03: examples by plain name; the ones that only set up a situation first, then those that run by themselves
    const ex = x => W.example(x.name, x.description);
    const opt = x => h("option", { value: x.name }, ex(x).name);
    const setup = S.scenarios.filter(x => !x.steps), scripted = S.scenarios.filter(x => x.steps);
    const sel = h("select", { id: "scn" },
      h("optgroup", { label: "Set up a situation (nothing starts)" }, setup.map(opt)),
      h("optgroup", { label: "Run a situation (it starts by itself)" }, scripted.map(opt)));
    if (S.scnSel && S.scenarios.some(x => x.name === S.scnSel)) sel.value = S.scnSel;
    const EFFECT = { starts: "starts processes or operations by itself while it runs", confirms_tanks: "confirms the fertiliser and acid tanks as prepared",
      recipe: "puts its own synthetic recipe in use", faults: "applies simulated faults or hand-set readings at set times", recovery: "marks problems as seen and clears stops by itself" };
    const desc = h("div", { id: "scn-desc" });
    const describe = () => {
      const x = S.scenarios.find(y => y.name === sel.value);
      if (!x) return desc.replaceChildren();
      const e = ex(x), uat = (x.description.match(/UAT-\d+/g) || []).join(", ");
      desc.replaceChildren(h("p", { class: "lead", "data-preview": x.name }, e.preview),
        h("p", null, h("strong", null, "Loading it "), x.steps ? "starts the model again, sets up this situation and then " + (x.effects || []).map(k => EFFECT[k]).filter(Boolean).join("; ") + "."
          : "starts the model again and only sets up this situation: nothing starts until you start it."),
        h("details", { class: "inline" }, h("summary", null, "Details"), h("p", { class: "muted small" }, `Example id ${x.name}${uat ? " · owner tour case " + uat : ""}${x.steps ? ` · runs for ${x.duration_s} s of simulated time with ${x.steps} scripted steps` : ""}. ${x.description}`)));
    };
    describe();
    sel.addEventListener("change", () => { S.scnSel = sel.value; describe(); });
    const equipName = (kind, id) => kind === "valve" && /^S\d+$/.test(id) ? W.sectorName(id) + " valve" : kind === "mains" || kind === "safety" ? FAULT_KIND[kind]
      : W.cap(W.equip(id)) + (W.equip(id) === id ? "" : ` (${id})`);
    const fsel = (kind, ids, faults) => {
      const lid = "fault-" + kind;
      const id = h("select", { id: lid + "-what", "aria-label": `${FAULT_KIND[kind]}: which one` }, ids.map(x => h("option", { value: x }, equipName(kind, x))));
      const f = h("select", { id: lid + "-fault", "aria-label": `${FAULT_KIND[kind]}: simulated fault` }, h("option", { value: "" }, "no fault (working)"),
        faults.map(x => h("option", { value: x }, FAULT_NAME[x] || x)));
      return h("div", { class: "row" }, h("strong", { class: "w-kind" }, FAULT_KIND[kind]), id, f,
        cbtn("Apply", () => act("POST", "/api/twin/actuator-fault", { kind, ident: id.value, fault: f.value || null }, f.value ? "simulated fault applied" : "simulated fault removed")));
    };
    const faults = [];
    for (const [k, c] of Object.entries(pl.contactors)) if (c.fault) faults.push(`${equipName("contactor", k)}: ${FAULT_NAME[c.fault] || c.fault}`);
    for (const [k, x] of Object.entries(pl.pumps)) if (x.fault) faults.push(`${equipName("pump", k)}: ${FAULT_NAME[x.fault] || x.fault}`);
    for (const [k, x] of Object.entries(pl.valves)) if (x.fault) faults.push(`${equipName("valve", k)}: ${FAULT_NAME[x.fault] || x.fault}`);
    if (pl.drive.tripped) faults.push(`Pump drive: ${FAULT_NAME[pl.drive.tripped] || pl.drive.tripped}`);
    // F-08: the simulated stand-in for mixing fertiliser into a tank by hand; it never confirms the tank
    const stockPrep = ["A", "B", "ACID"].map(s => {
      const r = pr && pr.stocks ? pr.stocks[s] : null, t = s === "ACID" ? "the acid tank" : "tank " + s;
      return h("div", { class: "row" }, cbtn(s === "ACID" ? "Add simulated acid to the acid tank" : `Add simulated fertiliser to tank ${s}`,
        () => act("POST", `/api/twin/stock/${s}/prepare`, undefined, `${W.TANK[s]}: simulated ${s === "ACID" ? "acid" : "fertiliser"} added; confirm ${t} is prepared on Nutrients & acid`)),
        r ? h("span", { class: "muted" }, r.status === "PREPARED" ? "confirmed prepared" : "not confirmed") : null);
    });
    const lg = pl.ledger;
    mount("v-twin",
      h("div", { class: "card" }, h("h2", null, "Digital Twin"), h("p", null, W.HELP.twin),
        h("div", { class: "row" }, h("span", null, "The model is ", h("strong", null, tw.activity === "RUN" ? "running" : "paused"), ` at ${tw.speed === 1 ? "normal speed" : tw.speed + "× speed"}.`),
          cbtn("Run", () => tc("run", null, "model running")), cbtn("Pause", () => tc("pause", null, "model paused")), cbtn("Step", () => tc("step", null, "one step")),
          ...[1, 10, 60].map(sp => cbtn(sp + "×", () => tc("speed", { speed: sp }, sp === 1 ? "normal speed" : sp + "× speed"), tw.speed === sp ? "" : "ghost")),
          cbtn("Reset run", () => confirm("Start the model again from the beginning? History is kept.") && tc("reset", null, "the model starts again"), "ghost")),
        h("h3", null, "Examples"),
        h("p", null, "These examples set up the simulated greenhouse so you can try different situations."),
        h("p", { class: "muted" }, "Before anything else, loading an example starts the model again: levels go back to the example's start, every process is stopped, simulated faults and hand-set readings are cleared. History, accounts, recipes and tank confirmations are kept, unless the example itself changes them (said below); the speed stays as set."),
        h("div", { class: "row" }, h("label", null, "Choose an example", sel), cbtn("Load this example", () => act("POST", "/api/twin/scenario", { name: sel.value },
          `${W.example(sel.value).name}: loaded`)),
          h("span", { class: "muted" }, tw.scenario ? "Loaded now: " + W.example(tw.scenario).name : "")), desc),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "Simulated fertiliser and acid"),
          h("p", { class: "muted" }, "In the real greenhouse a person mixes fertiliser into the tank by hand. Here these buttons add the simulated fertiliser to the model. Afterwards the tank still has to be confirmed prepared on the ",
            h("a", { href: "#nutrients" }, "Nutrients & acid"), " page; adding it never confirms the tank."), stockPrep),
        h("div", { class: "card" }, h("h2", null, "Simulated faults"),
          h("p", null, faults.length ? ["Active now: ", h("strong", null, faults.join("; "))] : "No simulated fault is active."),
          fsel("valve", Object.keys(pl.valves), ["STUCK_CLOSED", "STUCK_OPEN"]),
          fsel("pump", Object.keys(pl.main_pumps).concat(Object.keys(pl.pumps)), ["NO_RUN", "NO_FLOW", "STUCK_ON"]),
          fsel("contactor", Object.keys(pl.contactors), ["WELDED", "NO_CLOSE"]),
          fsel("drive", ["MAIN"], ["TRIP", "NO_RUN", "STUCK_ON"]),
          (sn.drive_adapters || []).some(a => a.kind === "MODBUS_RTU") ? fsel("drive_comm", sn.drive_adapters.filter(a => a.kind === "MODBUS_RTU").map(a => a.adapter_id), ["NO_RESPONSE", "EXCEPTION", "IGNORES_WRITES"]) : null,
          fsel("mains", ["MAINS"], ["NO_SUPPLY"]), fsel("safety", ["CHAIN"], ["CHAIN_OPEN"]),
          h("p", { class: "muted" }, "A simulated fault changes how the model behaves, so the program sees a problem the way it would on real equipment. Choose \"no fault (working)\" and Apply to remove it. Readings can be set by hand on the I/O test bench."),
          h("div", { class: "row" }, cbtn("Stall acquisition", () => tc("acq_stall", null, "readings stopped"), "ghost"), cbtn("Resume acquisition", () => tc("acq_resume", null, "readings resumed"), "ghost"),
            h("span", { class: "muted" }, "(stops or resumes new readings, to see how the pages show old readings)"))),
        instWaterCard(pl)),
      h("details", { class: "card tech" }, h("summary", null, h("strong", null, "Technical details of the model")),
        h("p", { class: "muted" }, `${tw.activity} · ${tw.speed}× · t = ${sn.sim_s.toFixed(1)} s · tick ${sn.tick} · run ${sn.run_id}`),
        h("div", { class: "grid" },
          h("div", null, h("h3", null, "Equipment (requested / actual)"), h("dl", { class: "kv" },
            h("dt", null, "Drive"), h("dd", null, `run ${pl.drive.run_requested ? "ON" : "OFF"}, speed ${(pl.drive.speed * 100).toFixed(0)} %${pl.drive.tripped ? " TRIPPED: " + pl.drive.tripped : ""}`),
            ...Object.entries(pl.contactors).flatMap(([k, c]) => [h("dt", null, "Contactor " + k), h("dd", null, `${c.requested ? "ON" : "OFF"} / ${c.closed ? "closed" : "open"}${c.fault ? " FAULT " + c.fault : ""}`)]),
            ...Object.entries(pl.pumps).flatMap(([k, p]) => [h("dt", null, "Pump " + k), h("dd", null, `${p.requested ? "ON" : "OFF"} / ${(p.speed * 100).toFixed(0)} %${p.fault ? " FAULT " + p.fault : ""}`)]),
            ...Object.entries(pl.valves).flatMap(([k, v]) => [h("dt", null, "Valve " + k), h("dd", null, `${v.requested ? "OPEN" : "CLOSED"} / ${(v.position * 100).toFixed(0)} %${v.fault ? " FAULT " + v.fault : ""}`)]))),
          h("div", null, h("h3", null, "Volume ledger (L)"), h("dl", { class: "kv" },
            ...[["initial stored", lg.initial_stored_L], ["stored", lg.stored_L], ["consumption", lg.consumption_L], ["spill", lg.spill_total_L],
               ["drained to sump", lg.drained_to_sump_L], ["returned to main", lg.returned_to_main_L], ["residual", lg.residual_L]]
              .flatMap(([k, v]) => [h("dt", null, k), h("dd", { class: "mono" }, k === "residual" ? v.toExponential(3) : v.toFixed(1))]))))));
  }
  function releaseLine(r) {
    if (!r) return "unknown";
    if (r.status === "NOT_A_RELEASE") return `not started from a release folder (${r.detail})`;
    const commit = (r.commit || "unknown").slice(0, 12);
    return [`${r.release_version} · source commit ${commit} · built ${r.built_utc || "?"} `,
      pill(r.status === "VERIFIED" ? "wheel verified" : r.status, r.status === "VERIFIED" ? "ok" : "bad"),
      r.status === "VERIFIED" ? null : ` ${r.detail}`];
  }

  // The installation's processes are run from their own pages; this page only points there.
  async function instControl() {
    const pr = await api("GET", "/api/processes");
    mount("v-control", h("div", { class: "card" }, h("h2", null, "Processes"),
      h("p", null, "Each process is started and stopped on its own page:"),
      h("ul", null, pr.order.map(pid => { const w = W.proc(pid); return h("li", null, h("a", { href: w.page }, w.name), " · ", W.state(pr.processes[pid].state)); })),
      h("p", null, "Fertiliser tank confirmations are on ", h("a", { href: "#nutrients" }, "Nutrients & acid"), "; all processes can be stopped at once from the ", h("a", { href: "#overview" }, "Overview"), ".")));
  }

  // ------------------------------------------------ getting started (F-03)
  // Four tasks, one action per step, with the exact button names and a picture of the screen.
  const START = [
    ["Read the tank level", "start-1.png", "The Main tank page: the water level in litres, “Reading valid” and how old the reading is.", [
      ["Open the menu and choose ", ["Main tank"], "."],
      ["Find ", ["Water level"], ": the litres, then “Reading valid” and how long ago the reading arrived."],
      ["The line at the top of every page also shows the main tank, for example “Main tank 60%”."]],
      "“Reading valid” means the reading arrived correctly and in time. It does not say whether the level is right for the plants."],
    ["Water one sector", "start-2.png", "The Watering page in manual mode, with the Run once form: sector, main pump and watering time.", [
      ["Open the menu and choose ", ["Watering"], "."],
      ["Press ", ["Manual mode"], "."],
      ["Under Run once, choose the sector in ", ["Sector"], ", for example Sector 1."],
      ["Type the watering time in seconds in ", ["Watering time (s)"], ", for example 60."],
      ["Press ", ["Run once"], ". The card says “Running once: Sector 1 …”, and “Manual mode: nothing is running” when it has finished."]],
      "Run once uses the same safety checks as automatic watering."],
    ["Stop watering", "start-3.png", "The top of the Watering page: what it is doing now and the Stop watering button.", [
      ["On the Watering page, press ", ["Stop watering"], ". It is at the top of the page; on a narrow screen it stays at the top of the screen."],
      ["The card says “stopping safely”, then “Watering is stopped.”"]],
      "Stop watering stops the process in the program and switches its equipment off in a safe order. It is not an emergency stop: use the emergency stop on the equipment for that."],
    ["Recover from a simulated problem", "start-4.png", "Watering stopped because of a problem, with the steps under What to do: Mark as seen, Fix the problem, Clear stop after fixing the problem.", [
      ["Open the menu and choose ", ["Digital Twin"], "."],
      ["Under Simulated faults, in the Valve row, choose ", ["Sector 1 valve"], " and ", ["stays closed"], ", then press ", ["Apply"], "."],
      ["Open ", ["Watering"], " and press ", ["Start automatic watering"], "."],
      ["After a few seconds the card says “Watering stopped because of a problem: the valve for Sector 1 did not open”. Press ", ["Mark as seen"], "."],
      ["Fix the problem: on ", ["Digital Twin"], ", in the Valve row, choose ", ["no fault (working)"], " and press ", ["Apply"], "."],
      ["Back on Watering, press ", ["Clear stop after fixing the problem"], ". Nothing starts."],
      ["To water again, press ", ["Start automatic watering"], "."]],
      "If a step is not available yet, the page says why next to its button."],
  ];
  VIEWS.start = async function () {
    const inst = isInst() && !isReal();
    const task = ([title, img, alt, steps, note], i) => h("div", { class: "card start-task", id: "start-" + (i + 1) },
      h("h2", null, `${i + 1}. ${title}`),
      h("div", { class: "start-body" }, h("img", { src: "/static/" + img, alt, loading: "lazy", width: 640 }),
        h("div", null, h("ol", null, steps.map(st => h("li", null, st.map(x => Array.isArray(x) ? h("strong", null, x[0]) : x)))),
          h("p", { class: "muted" }, note))));
    mount("v-start",
      h("div", { class: "card" }, h("h2", null, "Getting started"),
        h("p", null, "AquaCrop runs the greenhouse's watering, main tank refill, nutrients and acid, and drainage return. ", W.HELP.twin),
        h("p", null, "These four short tasks show the main screens. Each step is one action, with the name of the button to press in bold."),
        inst ? null : h("p", { class: "muted" }, "The tasks use the greenhouse pages of the installation profile; this profile shows them read-only or not at all."),
        h("p", { class: "muted" }, "The owner's acceptance tour is a separate document, ", h("strong", null, "owner-tour\\index.html"), " in the release folder, with its own result form.")),
      START.map(task));
  };

  VIEWS.overview = async function () {
    if (isReal()) return hwOverview();
    if (isInst()) return window.AqPlant.overview();
    const st = S.status, tw = await api("GET", "/api/twin"), pl = tw.snapshot.plant;
    const devRows = Object.entries(st.devices).map(([k, d]) => h("tr", null, h("td", null, k), h("td", null, d.bus_id + " @" + d.address),
      h("td", null, d.online ? pill("online", "ok") : pill("FAILED", "bad")), h("td", { class: "mono" }, d.last_error || ""),
      h("td", null, d.last_ok_utc ? age(d.last_ok_utc) : "never")));
    const al = await api("GET", "/api/alarms");
    mount("v-overview",
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "System"), h("dl", { class: "kv" },
          h("dt", null, "Backend"), h("dd", null, st.profile, " ", pill("physical writes impossible", "ok")),
          h("dt", null, "Controller"), h("dd", null, st.controller.state, " / ", st.controller.phase, st.controller.fault_reason ? " — " + st.controller.fault_reason : ""),
          h("dt", null, "Twin"), h("dd", null, `${st.twin.activity} at ${st.twin.speed}×, run ${st.twin.run_id.slice(0, 12)}…, seed ${st.twin.seed}`),
          h("dt", null, "Signal source"), h("dd", null, st.twin.source),
          h("dt", null, "Acquisition task"), h("dd", null, st.acquisition_task.ok ? pill("running", "ok") : pill(st.acquisition_task.state, "bad")),
          h("dt", null, "Storage"), h("dd", null, st.storage.ok ? pill("ok", "ok") : pill("DEGRADED: " + st.storage.reason, "bad"),
            " ", storageText(st.storage)),
          h("dt", null, "Live control"), h("dd", null, st.live_control))),
        h("div", { class: "card" }, h("h2", null, "Water (simulated)"), h("dl", { class: "kv" },
          ...Object.entries(pl.tanks).flatMap(([k, t]) => [h("dt", null, k), h("dd", null, `${t.volume_L.toFixed(1)} / ${t.capacity_L} L`)]),
          ...Object.entries(pl.sector_flow_L_min).flatMap(([k, q]) => [h("dt", null, "Sector " + k), h("dd", null, q.toFixed(2) + " L/min")]),
          ...Object.entries(pl.pumps).flatMap(([k, p]) => [h("dt", null, "Pump " + k), h("dd", null, `requested ${p.requested ? "ON" : "OFF"}, speed ${(p.speed * 100).toFixed(0)} %${p.fault ? " FAULT " + p.fault : ""}`)]),
          h("dt", null, "Ledger residual"), h("dd", null, pl.ledger.residual_L.toExponential(2) + " L"))),
        h("div", { class: "card" }, h("h2", null, `Active alarms (${al.open.length})`),
          al.open.length ? h("table", null, h("tbody", null, al.open.map(o => h("tr", null, h("td", null, pill(o.severity, o.severity === "CRITICAL" ? "bad" : "warn")),
            h("td", null, o.rule_id), h("td", null, o.target), h("td", null, o.active ? "active" : "cleared"), h("td", null, o.acknowledged ? "ack" : "unack"))))) : h("p", { class: "muted" }, "None"))),
      h("div", { class: "card" }, h("h2", null, "Devices (each polled and reported independently)"),
        h("table", null, h("thead", null, h("tr", null, ["Device", "Bus/address", "Health", "Last error", "Last OK age"].map(x => h("th", null, x)))), h("tbody", null, devRows))));
  };

  // ------------------------------------------------ digital twin
  function schematic(pl, ctrl) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 1000 520"); svg.setAttribute("class", "schematic");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Simulated hydraulic schematic (DEMO topology)");
    function e(tag, a, txt) { const x = document.createElementNS(NS, tag); for (const k in a) x.setAttribute(k, a[k]); if (txt != null) x.textContent = txt; svg.appendChild(x); return x; }
    function tank(x, y, w, hgt, name, t) {
      const frac = Math.max(0, Math.min(1, t.volume_L / t.capacity_L));
      e("rect", { x, y: y + hgt * (1 - frac), width: w, height: hgt * frac, class: "water" });
      e("rect", { x, y, width: w, height: hgt, class: "tank-wall" });
      e("text", { x: x + w / 2, y: y - 8, "text-anchor": "middle" }, name);
      e("text", { x: x + w / 2, y: y + hgt + 16, "text-anchor": "middle" }, `${t.volume_L.toFixed(1)} L (${(frac * 100).toFixed(0)} %)`);
    }
    const flowing = q => q > 0.05;
    const anyPump = Object.values(pl.pumps).some(p => p.speed > 0.05);
    // pipes
    e("path", { d: "M170 330 H260", class: "pipe" + (anyPump ? " flow" : "") });
    e("path", { d: "M90 60 V120", class: "pipe" + (pl.fill_flow_L_min > 0.05 ? " flow" : "") });
    const pumps = Object.entries(pl.pumps);
    pumps.forEach(([k, p], i) => {
      const y = 250 + i * 80;
      e("path", { d: `M260 330 V${y} H300`, class: "pipe" + (p.speed > 0.05 ? " flow" : "") });
      e("path", { d: `M360 ${y} H420 V330`, class: "pipe" + (p.speed > 0.05 ? " flow" : "") });
      e("circle", { cx: 330, cy: y, r: 22, class: "equip-act", "fill-opacity": 0.2 + 0.8 * p.speed });
      e("circle", { cx: 330, cy: y, r: 26, class: "equip-req " + (p.requested ? "req-on" : "req-off") });
      e("text", { x: 330, y: y + 4, "text-anchor": "middle" }, k);
      if (p.fault) e("text", { x: 330, y: y + 42, "text-anchor": "middle", class: "fault-mark" }, "⚠ " + p.fault);
    });
    const secs = Object.keys(pl.sector_flow_L_min);
    secs.forEach((s, i) => {
      const y = 120 + i * 260, q = pl.sector_flow_L_min[s], v = pl.valves[s];
      e("path", { d: `M420 330 V${y} H500`, class: "pipe" + (flowing(q) ? " flow" : "") });
      e("polygon", { points: `500,${y - 14} 500,${y + 14} 540,${y} `, class: "equip-act", "fill-opacity": 0.2 + 0.8 * v.position });
      e("polygon", { points: `580,${y - 14} 580,${y + 14} 540,${y}`, class: "equip-act", "fill-opacity": 0.2 + 0.8 * v.position });
      e("rect", { x: 496, y: y - 18, width: 88, height: 36, class: "equip-req " + (v.requested ? "req-on" : "req-off"), fill: "none" });
      e("text", { x: 540, y: y - 24, "text-anchor": "middle" }, `Valve ${s} ${(v.position * 100).toFixed(0)} %`);
      if (v.fault) e("text", { x: 540, y: y + 34, "text-anchor": "middle", class: "fault-mark" }, "⚠ " + v.fault);
      e("text", { x: 430, y: y + 34 }, `${q.toFixed(2)} L/min (demand ${pl.sector_demand_L_min[s].toFixed(2)})`);
    });
    // channels
    const chans = Object.entries(pl.channel_flow_L_min);
    chans.forEach(([c, q], i) => {
      const sIdx = Number(c) <= 6 ? 0 : 1, base = 120 + sIdx * 260;
      const off = (sIdx === 0 ? i : i - 6) * 16 - 48;
      e("path", { d: `M584 ${base} H620 V${base + off} H900`, class: "pipe" + (flowing(q) ? " flow" : ""), "stroke-width": 4 });
      e("text", { x: 905, y: base + off + 4 }, `ch${c}`);
    });
    tank(30, 120, 140, 300, "Reservoir", pl.tanks.reservoir);
    e("text", { x: 100, y: 52, "text-anchor": "middle" }, `Fill valve ${(pl.valves.FILL.position * 100).toFixed(0)} %${pl.valves.FILL.fault ? " ⚠ " + pl.valves.FILL.fault : ""}`);
    const rt = pl.tanks.return_tank;
    e("text", { x: 760, y: 470 }, `Return tank ${rt.volume_L.toFixed(1)} / ${rt.capacity_L} L · in transit ${pl.transit_L.toFixed(2)} L · transfer ${pl.return_transfer_L_min.toFixed(1)} L/min`);
    e("text", { x: 760, y: 492 }, `Spill ${(pl.ledger.spill_L.reservoir + pl.ledger.spill_L.return_tank).toFixed(2)} L · consumed ${pl.ledger.consumption_L.toFixed(2)} L`);
    e("text", { x: 300, y: 500 }, `Controller ${ctrl.state} / ${ctrl.phase}${ctrl.fault_reason ? " — " + ctrl.fault_reason : ""}`);
    e("text", { x: 300, y: 24 }, "DEMO topology (synthetic). Ring/box = requested state (solid on, dashed off); fill = actual.");
    return svg;
  }

  VIEWS.twin = async function () {
    if (isReal()) {                // 0.5.0: the equipment twin is its own service beside this one
      const L = await api("GET", "/api/twin-link");
      return mount("v-twin", h("div", { class: "card" }, h("h2", null, "Digital twin"), h("p", null, L.note),
        L.url ? h("p", null, h("a", { class: "button", href: L.url, target: "_blank", rel: "noopener" }, "Open the digital twin"))
          : h("p", { class: "warn" }, "The twin service is not configured on this controller yet."),
        L.feed ? h("button", { class: "ghost", disabled: !can("equipment_edit"), onclick: () => act("POST", "/api/twin-link/publish", undefined, "equipment and programs sent to the twin") }, "Send equipment and programs to the twin now") : null,
        h("p", { class: "muted small" }, "The twin receives a copy every 30 seconds. Use the same login there.")));
    }
    if (isInst()) return instTwin();
    const tw = await api("GET", "/api/twin"), sn = tw.snapshot, pl = sn.plant;
    if (!S.scenarios) S.scenarios = await api("GET", "/api/scenarios");
    const tc = (action, extra) => act("POST", "/api/twin/control", Object.assign({ action }, extra || {}), action);
    const sel = h("select", { id: "scn" }, S.scenarios.map(s => h("option", { value: s.name }, s.name)));
    const fsel = (kind, ids, faults) => {
      const id = h("select", null, ids.map(x => h("option", { value: x }, x)));
      const f = h("select", null, h("option", { value: "" }, "no fault"), faults.map(x => h("option", { value: x }, x)));
      return h("div", { class: "row" }, h("span", null, kind), id, f, cbtn("Apply", () => act("POST", "/api/twin/actuator-fault", { kind, ident: id.value, fault: f.value || null }, "fault applied")));
    };
    const lg = pl.ledger;
    mount("v-twin",
      h("div", { class: "card" }, h("div", { class: "row" },
        cbtn("Run", () => tc("run")), cbtn("Pause", () => tc("pause")), cbtn("Step", () => tc("step")),
        ...[1, 10, 60].map(sp => cbtn(sp + "×", () => tc("speed", { speed: sp }), tw.speed === sp ? "" : "ghost")),
        cbtn("Reset run", () => confirm("Start a new simulation run? History remains.") && tc("reset"), "ghost"),
        h("span", { class: "muted" }, `${tw.activity} · ${tw.speed}× · t=${sn.sim_s.toFixed(1)} s · tick ${sn.tick} · run ${sn.run_id.slice(0, 14)}…`)),
        h("div", { class: "row" }, h("label", null, "Scenario preset", sel), cbtn("Load scenario (new run)", () => act("POST", "/api/twin/scenario", { name: sel.value }, "scenario loaded")),
          h("span", { class: "muted" }, tw.scenario ? "Active: " + tw.scenario : ""))),
      h("div", { class: "card" }, schematic(pl, sn.controller)),
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h3", null, "Mass ledger (L)"), h("dl", { class: "kv" },
          ...[["initial accounted", lg.initial_accounted_L], ["external supply", lg.external_supply_L], ["consumption", lg.consumption_L],
             ["spill reservoir", lg.spill_L.reservoir], ["spill return", lg.spill_L.return_tank], ["returned to tank", lg.returned_to_tank_L],
             ["transferred", lg.transferred_to_reservoir_L], ["unmet demand", lg.unmet_demand_L], ["residual", lg.residual_L]]
            .flatMap(([k, v]) => [h("dt", null, k), h("dd", { class: "mono" }, k === "residual" ? v.toExponential(3) : v.toFixed(3))]),
          h("dt", null, "delivered by sector"), h("dd", { class: "mono" }, Object.entries(lg.delivered_by_sector_L).map(([k, v]) => `${k}: ${v.toFixed(3)}`).join("  ")))),
        h("div", { class: "card" }, h("h3", null, "Actuator faults (change plant dynamics)"),
          fsel("pump", Object.keys(pl.pumps), ["NO_RUN", "NO_FLOW", "STUCK_ON"]),
          fsel("valve", Object.keys(pl.valves), ["STUCK_CLOSED", "STUCK_OPEN"]),
          h("div", { class: "row" }, cbtn("Stall acquisition", () => tc("acq_stall"), "ghost"), cbtn("Resume acquisition", () => tc("acq_resume"), "ghost")),
          h("p", { class: "muted" }, "Sensor overrides are on the I/O test bench; they change observations only, never tank volumes.")),
        h("div", { class: "card" }, h("h3", null, "Simulation parameters (DEMO, validated)"),
          h("details", null, h("summary", null, "Show parameters and virtual tag map"),
            h("pre", { class: "mono" }, JSON.stringify(tw.params, null, 1)),
            h("table", null, h("tbody", null, tw.mapping.map(m => h("tr", null, h("td", null, m.point), h("td", null, m.signal), h("td", null, m.provenance)))))))));
  };

  // ------------------------------------------------ I/O bench
  VIEWS.io = async function () {
    if (isReal()) return hwIO();
    const d = await api("GET", "/api/points");
    const ioTest = d.source === "IO_TEST";
    const groups = d.devices.map(g => {
      const rows = g.points.map(p => {
        const s = p.sample || {};
        // an input with no plant signal reads "no value" in TWIN mode: show it as unassigned, not as a fault
        const unassigned = !ioTest && !s.override && s.reason === "UNMAPPED";
        let ctl = null;
        if (p.kind === "DO") ctl = h("span", null, cbtn("ON", () => cmd(p.id, true)), " ", cbtn("OFF", () => cmd(p.id, false), "ghost"));
        else if (ioTest && p.kind === "DI") ctl = h("span", null, cbtn("Inject 1", () => act("POST", "/api/io/inject", { point: p.id, raw: true })), " ", cbtn("Inject 0", () => act("POST", "/api/io/inject", { point: p.id, raw: false }), "ghost"));
        else if (ioTest && p.kind === "AI") { const inp = h("input", { type: "number", min: 0, max: 65535, step: 1, style: "width:90px", placeholder: "raw" }); ctl = h("span", null, inp, " ", cbtn("Inject", () => act("POST", "/api/io/inject", { point: p.id, raw: Number(inp.value) }))); }
        else if (!ioTest) {
          const inp = h("input", { style: "width:80px", placeholder: p.kind === "DI" ? "0/1" : "raw" });
          ctl = h("span", null, inp, " ", cbtn("Override", () => {
            const reason = prompt("Reason for override (recorded):"); if (!reason) return;
            const raw = p.kind === "DI" ? inp.value === "1" || inp.value === "true" : Number(inp.value);
            act("POST", "/api/io/override", { point: p.id, raw, reason }, "override set");
          }), s.override ? cbtn("Clear", () => act("POST", "/api/io/override/clear", { point: p.id }, "override cleared"), "ghost") : null);
        }
        return h("tr", null, h("td", { class: "mono" }, p.id), h("td", null, p.label || h("span", { class: "muted" }, p.mapping ? "" : "UNMAPPED")),
          h("td", { class: "mono" }, "0x" + p.register_address.toString(16).padStart(4, "0")), h("td", null, p.mode || ""),
          h("td", { class: "mono" }, s.raw == null ? "—" : String(s.raw) + (s.raw_unit && s.raw_unit !== "bool" ? " " + s.raw_unit : "")),
          h("td", { class: "mono" }, s.value == null ? "—" : num(s.value) + (s.unit ? " " + s.unit : "")),
          h("td", null, unassigned ? h("span", { class: "q q-UNASSIGNED", "data-q": "UNASSIGNED", title: W.QUALITY_HELP.UNASSIGNED }, W.quality("UNASSIGNED")) : qBadge(s.quality || "UNAVAILABLE"), " ", s.origin ? oBadge(s.origin) : null),
          h("td", { class: "muted" }, s.override ? `override by ${s.override.owner}: ${s.override.reason} (base ${s.override.base_raw})` :
            unassigned ? "nothing is connected to this channel (not a fault)" : (s.reason || "")),
          h("td", null, age(s.acquired_utc)), h("td", null, ctl));
      });
      return h("div", { class: "card" },
        h("div", { class: "row" }, h("h3", null, `${g.device_uid} — ${g.model} (SKU ${g.sku}) · ${g.bus_id} @ ${g.address}`),
          g.online ? pill("online", "ok") : pill("FAILED: " + (g.last_error || ""), "bad"),
          outputPill(g),
          cbtn(g.online ? "Simulate timeout" : "Restore", () => act("POST", "/api/io/device-fault", { device_uid: g.device_uid, fault: g.online ? "TIMEOUT" : null }), "ghost")),
        h("table", null, h("thead", null, h("tr", null, ["Point", "Label", "Zero-based addr", "Mode", "Raw", "Scaled", "Quality/origin", "Detail", "Age", "VIRTUAL control"].map(x => h("th", null, x)))),
          h("tbody", null, rows)));
    });
    mount("v-io",
      h("div", { class: "card" }, h("div", { class: "row" },
        h("strong", null, "VIRTUAL I/O — these controls can only reach simulated modules."),
        h("span", null, "Signal source: ", pill(d.source)),
        cbtn("Use IO_TEST (raw injection)", () => act("POST", "/api/io/source", { source: "IO_TEST" }, "source IO_TEST"), "ghost"),
        cbtn("Use TWIN (process model)", () => act("POST", "/api/io/source", { source: "TWIN" }, "source TWIN"), "ghost")),
        h("p", { class: "muted" }, ioTest ? "IO_TEST: process control is inactive; injected raw values and DO commands do not drive the plant." :
          "TWIN: inputs derive from the model; overrides take precedence until cleared and are shown with owner and base value. DO ON requires MANUAL.")),
      groups);
  };

  // ------------------------------------------------ I/O board (ADR-0017): a developer page for the bench bring-up
  // One card per module: outputs as switches with two lights (asked = the last command, reported = the coil
  // readback), inputs as lights, analog inputs in mA or V with a bar. On the simulation the modules are
  // virtual; on the bench a switch works only inside a test session (bench lease). Assignments are records
  // of which channel carries which installation signal; they never change what the program reads or commands.
  const MODEL_SHORT = { WAVESHARE_MODBUS_RTU_RELAY_D: "Relay (D)", WAVESHARE_MODBUS_RTU_IO_8CH: "IO 8CH",
    WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH: "Analog input 8CH" };
  const SAFE_WHEN = { START: "the service starts", STOP: "the service stops", LEASE_END: "the test session ends",
    MODULE_RETURN: "a module answers again after dropping out" };
  function led(on, cls, title) { return h("span", { class: "led " + (on == null ? "led-unk" : on ? "led-on " + (cls || "") : "led-off"), title, "aria-hidden": "true" }); }
  function mmss(s) { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
  function boardRange(p) {          // the bar's full scale, from the channel's mode (not a scaling)
    const m = p.mode || "";
    if (/4_20|0_20/.test(m)) return [0, 20, "mA"];
    if (/10/.test(m)) return [0, 10, "V"];
    if (/5/.test(m)) return [0, 5, "V"];
    return p.electrical ? [0, p.electrical.unit === "mA" ? 20 : 10, p.electrical.unit] : null;
  }
  async function boardSend(b, target, desired) {
    if (b.mode === "SIMULATION") {
      return act("POST", "/api/commands", { backend: "SIMULATED", config_version: S.cfgVersion, target, desired, request_id: rid(), reason: "I/O board" })
        .then(r => r && toast(`${target} ${desired ? "ON" : "OFF"}: ${W.outcome ? W.outcome(r.outcome) : r.outcome}${r.detail ? " (" + r.detail + ")" : ""}`, !OK_OUTCOMES.has(r.outcome)));
    }
    return act("POST", "/api/bench/command", { backend: "REAL_BENCH", config_version: S.cfgVersion, lease_id: b.lease.lease_id,
      target, desired, request_id: rid(), reason: "I/O board" })
      .then(r => r && toast(`REAL ${target} ${desired ? "ON" : "OFF"}: ${r.outcome}${r.detail ? " (" + r.detail + ")" : ""}`, !OK_OUTCOMES.has(r.outcome)));
  }
  async function boardAllOff(b) {
    if (b.mode === "BENCH") return act("POST", "/api/bench/all-off", undefined).then(r => r && toast("All outputs OFF: " +
      r.results.map(x => `${x.device_uid} ${x.result}`).join(", "), r.results.some(x => x.result !== "OFF_CONFIRMED")));
    const now = await api("GET", "/api/board");     // the outputs as they read now, not as last drawn
    const on = now.modules.flatMap(m => m.points).filter(p => p.kind === "DO" && p.sample && p.sample.value);
    if (!on.length) return toast("No output reads ON");
    let bad = 0;
    for (const p of on) {
      try { const r = await api("POST", "/api/commands", { backend: "SIMULATED", config_version: S.cfgVersion, target: p.id, desired: false, request_id: rid(), reason: "I/O board: all outputs OFF" });
        if (!OK_OUTCOMES.has(r.outcome)) bad++; } catch (e) { bad++; }
    }
    toast(bad ? `${on.length - bad} of ${on.length} outputs switched OFF; ${bad} refused` : `${on.length} outputs switched OFF`, bad > 0);
    refresh();
  }
  function boardSession(b) {
    if (b.mode === "SIMULATION") {
      const raw = b.source === "IO_TEST";
      return h("div", { class: "card" }, h("h2", null, "Virtual modules"),
        h("p", null, raw ? "I/O test mode: switches and injected inputs go to the virtual modules only, and no process runs."
          : "The model drives the inputs. Switch to I/O test mode to toggle every output and inject inputs by hand."),
        h("div", { class: "row" }, raw ? cbtn("Use the model again", () => act("POST", "/api/io/source", { source: "TWIN" }, "the model drives the inputs"), "ghost")
          : cbtn("Use I/O test mode", () => act("POST", "/api/io/source", { source: "IO_TEST" }, "I/O test mode"))));
    }
    const ss = b.safe_state || {}, when = (ss.outputs_off_on || []).map(t => SAFE_WHEN[t] || t);
    const safe = h("p", { class: "muted" }, when.length ? "Every output that reads ON is switched OFF when " + when.join(", ") + "." : "No automatic OFF is configured (safe_state is empty): outputs stay as they are when a session ends.",
      ss.last ? ` Last: ${ss.last.trigger} ${fmtTime(ss.last.utc)}, ${(ss.last.results || []).map(r => `${r.device_uid} ${r.result}`).join(", ")}.` : "");
    const L = b.lease;
    if (L) {
      return h("div", { class: "card armed" }, h("h2", null, "TEST SESSION ON: real outputs can switch"),
        h("p", null, h("strong", null, mmss(L.remaining_s)), ` left · ${L.username} · ${L.allowlist.length} outputs · ${L.setup_description}`),
        h("div", { class: "row" },
          b.can.command ? h("button", { class: "cmd", onclick: () => act("POST", "/api/bench/lease/renew", { lease_id: L.lease_id, duration_s: b.duration_range_s[1] }, "session renewed") }, `Renew (${b.duration_range_s[1] / 60} min)`) : null,
          h("button", { class: "cmd danger", onclick: () => act("POST", "/api/bench/lease/revoke", {}, "session ended") }, "End session")), safe);
    }
    if (!b.can.command) return h("div", { class: "card" }, h("h2", null, "Real modules, read only"),
      h("p", null, "Switching outputs needs a user with the commissioning permission (an admin sets it under Settings, Users)."), safe);
    if (S.boardArm) return S.boardArm;               // typed values and ticks survive the refresh
    const outs = b.modules.flatMap(m => m.points.filter(p => p.kind === "DO").map(p => p.id));
    const boxes = outs.map(id => ({ id, el: h("input", { type: "checkbox", checked: true, "aria-label": id }) }));
    const conf = h("input", { placeholder: b.confirmation_phrase, autocomplete: "off", "aria-label": "Confirmation" });
    const setup = h("input", { placeholder: "e.g. bench, lamps on all outputs, no pumps or valves", "aria-label": "Bench setup", style: "min-width:280px" });
    const dur = h("input", { type: "number", min: b.duration_range_s[0], max: b.duration_range_s[1], value: b.duration_range_s[1], style: "width:90px", "aria-label": "Duration in seconds" });
    const panel = h("div", { class: "card" }, h("h2", null, "Start a test session"),
      h("p", null, "Only on the bench, with nothing connected that can move water or hurt anyone. The switches below then reach the real modules."),
      h("div", { class: "row" }, h("label", null, "Bench setup ", setup), h("label", null, "Seconds ", dur)),
      h("div", { class: "row" }, h("label", null, "Type ", h("strong", null, b.confirmation_phrase), " ", conf)),
      h("details", { class: "inline" }, h("summary", null, `Outputs in the session (${outs.length})`),
        h("div", { class: "row" }, cbtn("All", () => boxes.forEach(x => { x.el.checked = true; }), "ghost"), cbtn("None", () => boxes.forEach(x => { x.el.checked = false; }), "ghost")),
        h("div", { class: "row" }, ...boxes.map(x => h("label", { class: "mono small" }, x.el, " ", x.id)))),
      h("div", { class: "row" }, h("button", { class: "cmd danger", onclick: () => act("POST", "/api/bench/lease", { confirmation: conf.value, setup_description: setup.value,
        allowlist: boxes.filter(x => x.el.checked).map(x => x.id), duration_s: Number(dur.value) }, "test session started").then(r => { if (r) S.boardArm = null; }) }, "Start test session")), safe);
    S.boardArm = panel;
    return panel;
  }
  function boardModule(b, m) {
    const sim = b.mode === "SIMULATION", raw = sim && b.source === "IO_TEST";
    const inLease = new Set(b.lease ? b.lease.allowlist : []);
    const pick = p => h("button", { class: "linkbtn chan", onclick: () => { S.boardSel = S.boardSel === p.id ? null : p.id; refresh(); },
      "aria-expanded": S.boardSel === p.id ? "true" : "false", title: "Assign or verify this channel" }, p.kind + p.channel);
    const cur = S.guide && !S.guide.done ? S.guide.order[S.guide.i] : null;
    const chk = p => p.check ? h("span", { class: "chk chk-" + p.check.result, title: `guided check ${p.check.result} · ${p.check.actor} · ${fmtTime(p.check.utc)}${p.check.note ? " · " + p.check.note : ""}` },
      { PASS: "✓ works", FAIL: "✗ doesn't work", SKIPPED: "skipped" }[p.check.result]) : null;
    const asg = p => p.assignment ? h("span", { class: "asg asg-" + p.assignment.status, title: p.assignment.status }, p.assignment.signal, " ", p.assignment.status === "VERIFIED" ? "✓" : "?")
      : sim && p.model_signal ? h("span", { class: "muted small", title: "what the simulation wires to this channel" }, "model: " + p.model_signal) : h("span", { class: "muted small" }, "free");
    const dos = m.points.filter(p => p.kind === "DO"), dis = m.points.filter(p => p.kind === "DI"), ais = m.points.filter(p => p.kind === "AI");
    const doTile = p => {
      const rep = p.sample && ["GOOD", "OVERRIDDEN"].includes(p.sample.quality) ? !!p.sample.value : null;
      const ask = p.asked ? p.asked.desired : null;
      const differs = ask != null && rep != null && ask !== rep;
      const ok = sim ? b.can.command : b.can.command && b.lease && inLease.has(p.id);
      const why = sim ? (b.can.command ? "" : "needs the operator role") : !b.lease ? "start a test session first" : !inLease.has(p.id) ? "not in this test session" : "";
      return h("div", { class: "tile" + (differs ? " differs" : "") + (cur === p.id ? " current" : ""), "data-point": p.id },
        h("div", { class: "tile-head" }, pick(p), h("span", { class: "lights" }, led(ask, "asked", "asked: " + (ask == null ? "no command yet" : ask ? "ON" : "OFF")), led(rep, "", "reported: " + (rep == null ? "not read" : rep ? "ON" : "OFF")))),
        h("button", { class: "switch" + (rep ? " on" : ""), role: "switch", "aria-checked": rep ? "true" : "false", "aria-label": `${p.id} ${p.label || ""}`.trim(),
          disabled: !ok || rep == null, title: why || (rep ? "switch OFF" : "switch ON"), onclick: () => boardSend(b, p.id, !rep) }, rep == null ? "?" : rep ? "ON" : "OFF"),
        h("div", { class: "small" }, differs ? h("strong", null, "asked " + (ask ? "ON" : "OFF") + ", reads " + (rep ? "ON" : "OFF")) : p.asked ? `asked ${ask ? "ON" : "OFF"} ${ago((serverNow() - Date.parse(p.asked.utc)) / 1000)}` : "no command yet"),
        chk(p), asg(p));
    };
    const diTile = p => {
      const v = p.sample && p.sample.quality === "GOOD" ? !!p.sample.value : null;
      return h("div", { class: "tile" + (cur === p.id ? " current" : ""), "data-point": p.id }, h("div", { class: "tile-head" }, pick(p), led(v, "input", v == null ? "not read" : v ? "1" : "0")),
        h("div", { class: "big mono" }, v == null ? "—" : v ? "1" : "0"),
        raw && b.can.command ? h("div", { class: "row" }, cbtn("1", () => act("POST", "/api/io/inject", { point: p.id, raw: true })), cbtn("0", () => act("POST", "/api/io/inject", { point: p.id, raw: false }), "ghost")) : null,
        h("div", { class: "small muted" }, (p.changes || []).length ? `changed ${(p.changes || []).length}× in 15 min` : "no change seen"), chk(p), asg(p));
    };
    const aiRow = p => {
      const s = p.sample || {}, e = p.electrical, r = boardRange(p);
      const pct = e && r ? Math.max(0, Math.min(100, 100 * (e.value - r[0]) / (r[1] - r[0]))) : 0;
      const inj = raw && b.can.command ? h("input", { type: "number", min: 0, max: 65535, step: 1, style: "width:80px", "aria-label": `${p.id} raw value to inject`, placeholder: (s.raw_unit || "raw") }) : null;
      const mode = (p.mode || "").replace(/^CURRENT_|^VOLTAGE_/, "").replace(/_/g, "-") + ({ CURRENT: " mA", VOLTAGE: " V" }[(p.mode || "").split("_")[0]] || "");
      return h("tr", { "data-point": p.id, class: cur === p.id ? "current" : null }, h("td", null, pick(p), h("div", { class: "small muted" }, mode), h("div", null, chk(p)), h("div", null, asg(p))),
        h("td", null, h("div", { class: "mono big" }, e ? `${e.value.toFixed(e.unit === "mA" ? 2 : 3)} ${e.unit}` : "—"),
          h("div", { class: "mono small muted" }, s.raw == null ? "no raw value" : `raw ${s.raw} ${s.raw_unit || ""}`)),
        h("td", { class: "barcell" }, h("div", { class: "bar", title: r ? `${r[0]}–${r[1]} ${r[2]}` : "" }, h("div", { class: "fill", style: `width:${pct.toFixed(1)}%` })),
          h("div", { class: "small muted" }, s.age_s == null ? "" : ago(s.age_s))),
        h("td", null, qBadge(s.quality || "UNAVAILABLE"), s.value != null && s.unit ? h("div", { class: "small muted" }, `scaled ${num(s.value)} ${s.unit}`) : null,
          inj ? h("div", { class: "row" }, inj, cbtn("Inject", () => act("POST", "/api/io/inject", { point: p.id, raw: Number(inj.value) }))) : null));
    };
    const poll = !sim && b.poll_s ? b.poll_s[m.bus_id] : null;
    return h("div", { class: "card module", "data-module": m.device_uid },
      h("div", { class: "row" }, led(m.online, "online", m.online ? "answering" : "not answering"),
        h("h2", null, `${m.device_uid} · ${MODEL_SHORT[m.model_id] || m.model}`),
        h("span", { class: "muted" }, `${m.bus_id} · address ${m.address}${poll ? ` · round ${(poll * 1000).toFixed(0)} ms` : ""}`),
        m.online ? pill("answering", "ok") : pill("NOT ANSWERING" + (m.last_error ? ": " + m.last_error : ""), "bad"), outputPill(m),
        m.hardware_variant ? pill("version " + ({ STANDARD: "A", B: "B" }[m.hardware_variant] || "unknown"), m.hardware_variant === "UNKNOWN" ? "warn" : "") : null,
        sim && b.can.command ? cbtn(m.online ? "Simulate no answer" : "Answer again", () => act("POST", "/api/io/device-fault", { device_uid: m.device_uid, fault: m.online ? "TIMEOUT" : null }), "ghost") : null),
      dos.length ? [h("h3", null, "Outputs"), h("div", { class: "tiles" }, dos.map(doTile))] : null,
      dis.length ? [h("h3", null, "Inputs"), h("div", { class: "tiles" }, dis.map(diTile))] : null,
      ais.length ? h("table", { class: "ai-table" }, h("thead", null, h("tr", null, ["Channel", "Reading", "Scale", "Quality"].map(x => h("th", null, x)))), h("tbody", null, ais.map(aiRow))) : null,
      S.boardSel && m.points.some(p => p.id === S.boardSel) ? boardAssign(b, m.points.find(p => p.id === S.boardSel)) : null);
  }
  function boardAssign(b, p) {
    const sig = S.boardSignals || { signals: {}, assignments: {} };
    const used = new Map(b.modules.flatMap(m => m.points).filter(x => x.assignment).map(x => [x.assignment.signal, x.id]));
    const sel = h("select", { "aria-label": `Signal for ${p.id}` }, h("option", { value: "" }, "— choose a signal —"),
      (sig.signals[p.kind] || []).map(n => h("option", { value: n, disabled: used.has(n) && used.get(n) !== p.id }, W.signal ? `${W.signal(n)} (${n})${used.has(n) && used.get(n) !== p.id ? " · on " + used.get(n) : ""}` : n)));
    if (p.assignment) sel.value = p.assignment.signal;
    const note = h("input", { placeholder: "note, e.g. wire 12, lamp L3", maxlength: 300, style: "min-width:220px", "aria-label": "Note" });
    const a = p.assignment, can = b.can.assign;
    const obs = h("input", { placeholder: p.kind === "DO" ? "e.g. lamp L3 lit, then went off" : p.kind === "DI" ? "e.g. pressed button B2, then released" : "e.g. calibrator 12.00 mA on AI3",
      maxlength: 300, style: "min-width:280px", "aria-label": "What you saw" });
    const ref = p.kind === "AI" ? h("input", { type: "number", step: "any", style: "width:90px", "aria-label": "Reference applied" }) : null;
    const tol = p.kind === "AI" ? h("input", { type: "number", step: "any", min: 0, value: (p.electrical && p.electrical.unit === "V") ? 0.05 : 0.1, style: "width:80px", "aria-label": "Tolerance" }) : null;
    const how = { DO: "Switch this output ON and then OFF above, watch the device, then describe what it did.",
      DI: "Change the input by hand (press, close, lift) and back again, holding each state for 2 seconds, then describe what you did.",
      AI: "Apply a known signal (a calibrator or a known resistor and supply), enter it below, then describe the setup." }[p.kind];
    return h("div", { class: "assign", id: "assign-" + p.id.replace(".", "-") },
      h("h3", null, `Assign ${p.id}`, p.label ? h("span", { class: "muted small" }, " · " + p.label) : null),
      a ? h("p", null, pill(a.status, a.status === "VERIFIED" ? "ok" : "warn"), ` ${a.signal} · ${a.actor} · ${fmtTime(a.utc)}${a.note ? " · " + a.note : ""}`,
        a.status === "VERIFIED" ? h("span", { class: "muted small" }, ` · checked: ${a.evidence.observation}`) : null) : h("p", { class: "muted" }, "Not assigned."),
      h("div", { class: "row" }, sel, note,
        h("button", { class: "cmd small", disabled: !can, onclick: () => sel.value && act("POST", "/api/assignments", { point: p.id, signal: sel.value, note: note.value || null }, `${p.id}: ${sel.value} proposed`).then(() => { S.boardSignals = null; }) }, "Save assignment"),
        a ? h("button", { class: "cmd small ghost", disabled: !can, onclick: () => confirm(`Free ${p.id}? The record stays in the history.`) && act("POST", "/api/assignments", { point: p.id, signal: null }, `${p.id} freed`) }, "Free this channel") : null),
      a && a.status !== "VERIFIED" ? h("div", null, h("p", null, h("strong", null, "Verify: "), how),
        h("div", { class: "row" }, obs, ref ? h("label", null, `Reference (${p.electrical ? p.electrical.unit : "mA or V"}) `, ref) : null, tol ? h("label", null, "± ", tol) : null,
          h("button", { class: "cmd small", disabled: !can, onclick: () => act("POST", "/api/assignments/verify", { point: p.id, observation: obs.value,
            reference: ref ? (ref.value === "" ? null : Number(ref.value)) : null, tolerance: tol ? Number(tol.value) : null }, `${p.id} verified`) }, "Mark verified"))) : null,
      can ? null : h("p", { class: "muted small" }, "Assigning needs the commissioning permission."),
      h("p", { class: "muted small" }, "An assignment is a record for the later wiring approval. It does not connect the channel to the irrigation program."));
  }
  // Guided check (owner request 2026-10-20): every channel in turn, moved on with a manual Next. An output is
  // switched ON when its step starts (the one before is switched OFF first), an input waits for its change,
  // an analog input shows its reading. Works / Doesn't work is the user's judgement, recorded per channel.
  function serverNow() { return S.status ? Date.parse(S.status.freshness.server_utc) + (Date.now() - S.lastOk) : Date.now(); }
  function guideOrder(b, only) {
    return b.modules.filter(m => !only || m.device_uid === only).flatMap(m => ["DO", "DI", "AI"].flatMap(k => m.points.filter(p => p.kind === k).map(p => p.id)));
  }
  async function guideSwitch(b, target, desired) {
    const body = b.mode === "SIMULATION"
      ? ["/api/commands", { backend: "SIMULATED", config_version: S.cfgVersion, target, desired, request_id: rid(), reason: "I/O board guided check" }]
      : ["/api/bench/command", { backend: "REAL_BENCH", config_version: S.cfgVersion, lease_id: b.lease && b.lease.lease_id, target, desired, request_id: rid(), reason: "I/O board guided check" }];
    try { const r = await api("POST", ...body); return r.outcome; }
    catch (e) { toast(W.refusal(String(e.message || e)), true, String(e.message || e)); return "REFUSED"; }
  }
  async function guideEnter(b) {
    const g = S.guide, id = g.order[g.i];
    g.start = new Date(serverNow() - 1000).toISOString();   // the server's clock, so its records compare
    g.result = null; g.note = ""; g.sent = null; g.ref = ""; delete g.done;
    if (id && id.includes(".DO")) g.sent = await guideSwitch(b, id, true);
    refresh();
    setTimeout(() => { const t = document.querySelector(`#v-board [data-point="${id}"]`); if (t) t.scrollIntoView({ block: "center", behavior: "smooth" }); }, 300);
  }
  async function guideLeave(b) {        // the current output goes OFF before anything else happens
    const g = S.guide, id = g.order[g.i];
    if (id && id.includes(".DO") && g.sent && g.sent !== "REFUSED") await guideSwitch(b, id, false);
  }
  async function guideStart(b, only, skipPassed) {
    if (b.mode === "SIMULATION" && b.source !== "IO_TEST") await api("POST", "/api/io/source", { source: "IO_TEST" }).catch(() => {});
    let order = guideOrder(b, only);
    if (skipPassed) {
      const passed = new Set(b.modules.flatMap(m => m.points).filter(p => p.check && p.check.result === "PASS").map(p => p.id));
      order = order.filter(id => !passed.has(id));
    }
    if (!order.length) return toast("Every channel in this choice has already passed");
    S.guide = { run: "chk_" + rid().slice(3, 19), order, i: 0, counts: { PASS: 0, FAIL: 0, SKIPPED: 0 } };
    await guideEnter(b);
  }
  async function guideNext(b, dir) {
    const g = S.guide;
    if (!g || g.busy) return;
    g.busy = true;
    try {
      const id = g.order[g.i];
      if (dir > 0) {
        const result = g.result || "SKIPPED";
        const ref = id.includes(".AI") && g.ref !== "" && g.ref != null ? Number(g.ref) : null;
        try {
          await api("POST", "/api/iochecks", { run_id: g.run, point: id, result, started_utc: g.start, note: g.note || null,
            reference: ref, tolerance: ref == null ? null : Number(g.tol || 0.1) });
          g.counts[result] += 1;
        } catch (e) { toast(W.refusal(String(e.message || e)), true, String(e.message || e)); return; }   // stay on this step
      }
      await guideLeave(b);
      g.i += dir;
      if (g.i < 0) g.i = 0;
      if (g.i >= g.order.length) { g.done = true; refresh(); return; }
      await guideEnter(b);
    } finally { g.busy = false; }
  }
  async function guideStop(b) { if (S.guide && !S.guide.done) await guideLeave(b); S.guide = null; refresh(); }
  function guidePanel(b) {
    const g = S.guide, all = b.modules.flatMap(m => m.points);
    const can = b.can.assign && b.can.command;
    if (!g) {
      const only = h("select", { "aria-label": "Which modules" }, h("option", { value: "" }, "every module"), b.modules.map(m => h("option", { value: m.device_uid }, m.device_uid)));
      const skip = h("input", { type: "checkbox", checked: true, "aria-label": "Skip channels that already passed" });
      const c = b.checked || {};
      return h("div", { class: "card guide" }, h("h2", null, "Guided check"),
        h("p", null, "Checks one channel at a time; you move on with Next. An output is switched ON for you to see it (and OFF again at Next), an input waits for you to change it, an analog input shows its reading."),
        h("p", { class: "muted small" }, `So far: ${c.PASS || 0} work, ${c.FAIL || 0} don't, ${c.SKIPPED || 0} skipped, of ${all.length} channels.`),
        b.mode === "BENCH" && !b.lease ? h("p", { class: "warn-text" }, "Start a test session first, so the outputs can be switched.") : null,
        h("div", { class: "row" }, h("label", null, "Check ", only), h("label", null, skip, " skip channels that already work"),
          h("button", { class: "cmd", disabled: !can || (b.mode === "BENCH" && !b.lease), onclick: () => guideStart(b, only.value, skip.checked) }, "Start guided check")),
        can ? null : h("p", { class: "muted small" }, "The guided check needs the commissioning permission and the right to switch outputs."));
    }
    if (g.done) {
      return h("div", { class: "card guide" }, h("h2", null, "Guided check finished"),
        h("p", null, `${g.order.length} channels: ${g.counts.PASS} work, ${g.counts.FAIL} don't, ${g.counts.SKIPPED} skipped. Every output it switched is OFF again.`),
        h("div", { class: "row" }, h("a", { href: "/api/assignments/export?fmt=yaml", download: "" }, "Export results and assignments (YAML)"),
          h("button", { class: "cmd ghost", onclick: () => { S.guide = null; refresh(); } }, "Close")));
    }
    const id = g.order[g.i], p = all.find(x => x.id === id) || { id, kind: "?" };
    const choose = r => { g.result = g.result === r ? null : r; refresh(); };
    const choice = h("div", { class: "row" },
      h("button", { class: "cmd" + (g.result === "PASS" ? "" : " ghost"), "aria-pressed": g.result === "PASS" ? "true" : "false", onclick: () => choose("PASS") }, "Works"),
      h("button", { class: "cmd" + (g.result === "FAIL" ? " danger" : " ghost"), "aria-pressed": g.result === "FAIL" ? "true" : "false", onclick: () => choose("FAIL") }, "Doesn't work"));
    const note = h("input", { placeholder: "note (optional)", maxlength: 300, value: g.note || "", style: "min-width:220px", "aria-label": "Note for this channel",
      oninput: e => { g.note = e.target.value; } });
    let body;
    if (p.kind === "DO") {
      const rep = p.sample ? !!p.sample.value : null;
      body = [h("p", { class: "lead" }, `Output ${p.id} is switched ON. Is the LED, lamp or relay on this output on?`),
        h("p", null, led(rep, "", ""), " ", g.sent == null ? "switching ON…" : g.sent === "REFUSED" ? "the ON command was refused (see the message)" :
          `asked ON: ${g.sent}; the module reads ${rep == null ? "nothing" : rep ? "ON" : "OFF"}`),
        h("p", { class: "muted small" }, "Next records your answer and switches this output OFF before the next channel.")];
    } else if (p.kind === "DI") {
      const seen = (p.changes || []).filter(c => Date.parse(c[0]) >= Date.parse(g.start));
      if (seen.length && !g.result) g.result = "PASS";
      const v = p.sample && p.sample.quality === "GOOD" ? !!p.sample.value : null;
      body = [h("p", { class: "lead" }, `Change input ${p.id} now: press the button or close the switch, then release it.`),
        h("p", null, led(v, "input", ""), " ", h("strong", { class: "mono" }, v == null ? "—" : v ? "1" : "0"), "  ",
          seen.length ? `seen: ${seen.map(c => `${c[1] ? "0→1" : "1→0"} at ${fmtTime(c[0]).slice(-8)}`).join(", ")}` : "waiting for the input to change…"),
        b.mode === "SIMULATION" ? h("div", { class: "row" }, h("span", { class: "muted small" }, "Virtual input:"), cbtn("1", () => act("POST", "/api/io/inject", { point: p.id, raw: true })), cbtn("0", () => act("POST", "/api/io/inject", { point: p.id, raw: false }), "ghost")) : null];
    } else {
      const e = p.electrical, s = p.sample || {};
      const ref = h("input", { type: "number", step: "any", value: g.ref || "", style: "width:90px", "aria-label": "Expected value", oninput: ev => { g.ref = ev.target.value; } });
      const tol = h("input", { type: "number", step: "any", min: 0, value: g.tol || 0.1, style: "width:70px", "aria-label": "Tolerance", oninput: ev => { g.tol = ev.target.value; } });
      body = [h("p", { class: "lead" }, `Apply your signal to ${p.id} and read the value.`),
        h("p", null, h("strong", { class: "mono big" }, e ? `${e.value.toFixed(e.unit === "mA" ? 2 : 3)} ${e.unit}` : "no reading"), " ",
          h("span", { class: "muted small" }, s.raw == null ? "" : `raw ${s.raw} ${s.raw_unit || ""} · ${ago(s.age_s)}`), " ", qBadge(s.quality || "UNAVAILABLE")),
        h("div", { class: "row" }, h("label", null, `Expected (${e ? e.unit : "mA"}, optional) `, ref), h("label", null, "± ", tol)),
        b.mode === "SIMULATION" ? h("p", { class: "muted small" }, "Virtual input: type a raw value in the module's Inject field below.") : null];
    }
    return h("div", { class: "card guide armed-soft" },
      h("div", { class: "row" }, h("h2", null, `Guided check · ${g.i + 1} of ${g.order.length}`), h("span", { class: "mono" }, p.id), p.label ? h("span", { class: "muted small" }, p.label) : null),
      ...body, choice, h("div", { class: "row" }, note),
      h("div", { class: "row" },
        h("button", { class: "cmd ghost", disabled: g.i === 0, onclick: () => guideNext(b, -1) }, "Back"),
        h("button", { class: "cmd", onclick: () => guideNext(b, +1) }, g.result ? "Next" : "Next (skip this one)"),
        h("button", { class: "cmd ghost", onclick: () => guideStop(b) }, "Stop guided check")));
  }
  VIEWS.board = async function () {
    const b = await api("GET", "/api/board");
    if (!S.boardSignals) S.boardSignals = await api("GET", "/api/assignments");
    const outs = b.modules.flatMap(m => m.points).filter(p => p.kind === "DO");
    const onCount = outs.filter(p => p.sample && p.sample.value).length;
    const head = h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", null, "I/O board"), b.mode === "SIMULATION" ? pill("SIMULATION · virtual modules", "") : pill("REAL MODULES", "bad"),
        h("span", { class: "muted" }, `${b.modules.length} modules · ${b.modules.reduce((n, m) => n + m.points.length, 0)} channels · ${b.assigned.VERIFIED} verified, ${b.assigned.PROPOSED} proposed`),
        h("a", { href: "/api/assignments/export?fmt=yaml", download: "" }, "Export assignments (YAML)"), h("a", { href: "/api/assignments/export?fmt=json", download: "" }, "JSON")),
      h("p", { class: "muted" }, "For commissioning: check every module and channel, then assign each channel to the installation signal it carries. Click a channel name to assign or verify it. Lights: left = asked (last command), right = reported (read back from the module)."),
      h("div", { class: "row" }, h("button", { class: "cmd danger", disabled: !b.can.all_off || (b.mode === "SIMULATION" && !onCount), onclick: () => boardAllOff(b) }, "All outputs OFF"),
        h("span", { class: "muted small" }, `${onCount} of ${outs.length} outputs read ON`)));
    mount("v-board", head, boardSession(b), guidePanel(b), h("div", { class: "modules" }, b.modules.map(m => boardModule(b, m))));
  };

  // ------------------------------------------------ control
  VIEWS.control = async function () {
    if (isReal()) return mount("v-control", h("div", { class: "card" }, "The controller exists only in simulation in this release."));
    if (isInst()) return instControl();
    const c = await api("GET", "/api/controller"), d = await api("GET", "/api/points");
    const st = s => act("POST", "/api/controller/state", { target: s }, s);
    const mapped = d.devices.flatMap(g => g.points).filter(p => p.kind === "DO" && p.mapping);
    mount("v-control",
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "Controller (simulation only)"), h("dl", { class: "kv" },
          h("dt", null, "State"), h("dd", null, pill(c.state, c.state === "FAULT_LOCKOUT" ? "bad" : c.state === "AUTO" ? "ok" : "")),
          h("dt", null, "Phase"), h("dd", null, c.phase, ` since ${num(c.phase_since_s, 1)} s`),
          h("dt", null, "Sector / pump"), h("dd", null, `${c.sector} / ${c.selected_pump}`),
          h("dt", null, "Fault"), h("dd", null, c.fault_reason || "none"),
          h("dt", null, "This sector"), h("dd", null, `irrigated ${num(c.irrigated_s, 1)} s, ~${num(c.delivered_L_observed, 2)} L observed`)),
          h("div", { class: "row" }, cbtn("MANUAL", () => st("MANUAL")), cbtn("AUTO (simulation)", () => st("AUTO")), cbtn("STOP", () => st("STOPPED"), "danger"),
            h("button", { class: "cmd small ghost", disabled: !can("lockout_reset"), onclick: () => act("POST", "/api/controller/reset", undefined, "reset") }, "Reset lockout")),
          h("p", { class: "muted" }, c.policy_note + ". Physical AUTO is unavailable in this release.")),
        h("div", { class: "card" }, h("h2", null, "Permissives (last precheck)"),
          h("table", null, h("tbody", null, Object.entries(c.permissives || {}).map(([k, v]) => h("tr", null, h("td", null, k), h("td", null, v ? pill("OK", "ok") : pill("FAIL", "bad"))))))),
        h("div", { class: "card" }, h("h2", null, "Virtual equipment (MANUAL)"),
          h("table", null, h("tbody", null, mapped.map(p => h("tr", null, h("td", null, p.mapping.signal), h("td", { class: "mono" }, p.id),
            h("td", null, p.sample && p.sample.value ? pill("requested ON", "ok") : pill("requested OFF")),
            h("td", null, cbtn("ON", () => cmd(p.id, true)), " ", cbtn("OFF", () => cmd(p.id, false), "ghost")))))))),
      h("div", { class: "card" }, h("h2", null, "Phase history"),
        h("table", null, h("thead", null, h("tr", null, ["sim s", "state", "phase", "reason", "actor"].map(x => h("th", null, x)))),
          h("tbody", null, c.history.slice().reverse().map(t => h("tr", null, h("td", null, num(t.sim_s, 1)), h("td", null, t.to_state), h("td", null, t.to_phase), h("td", null, t.reason), h("td", null, t.actor)))))),
      h("div", { class: "card" }, h("h2", null, "Completed sector cycles"),
        h("table", null, h("tbody", null, c.cycles.slice().reverse().map(x => h("tr", null, h("td", null, x.sector), h("td", null, `end ${num(x.end_sim_s, 1)} s`), h("td", null, `${num(x.irrigated_s, 1)} s`), h("td", null, `~${num(x.delivered_L_observed, 2)} L observed`)))))));
  };

  // ------------------------------------------------ history (F-07)
  // Times of day are the site's (Europe/Stockholm on the bench, Atlantic/Canary on site).
  function tzOffset(t) {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: S.tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(new Date(t)).map(x => [x.type, x.value]));
    return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) - Math.floor(t / 1000) * 1000;
  }
  /** Midnight at the site, `daysBack` days before today, as a time in ms. */
  function siteMidnight(daysBack) {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: S.tz, year: "numeric", month: "2-digit", day: "2-digit" })
      .formatToParts(new Date()).map(x => [x.type, x.value]));
    const wall = Date.UTC(+p.year, +p.month - 1, +p.day - daysBack);
    let t = wall - tzOffset(wall);
    t = wall - tzOffset(t);            // again, for a day that changes summer time
    return t;
  }
  const RANGES = [["hour", "Last hour"], ["today", "Today"], ["yesterday", "Yesterday"], ["7d", "Last 7 days"],
    ["30d", "Last 30 days"], ["90d", "Last 90 days"], ["365d", "Last 365 days"]];
  function rangeOf(key, now) {
    const day = 86400000;
    if (key === "today") return [siteMidnight(0), now];
    if (key === "yesterday") return [siteMidnight(1), siteMidnight(0)];
    const n = { hour: 3600000, "7d": 7 * day, "30d": 30 * day, "90d": 90 * day, "365d": 365 * day }[key] || 3600000;
    return [now - n, now];
  }
  /** Every channel with the name an operator uses, grouped; channels that carry no plant signal last, as unused. */
  async function historyChoices() {
    if (!S.allPoints) { const d = await api("GET", "/api/points"); S.allPoints = d.devices.flatMap(g => g.points); }
    if (!S.histNames) {
      S.histNames = {};
      const P = (isInst() || isReal()) ? await api("GET", "/api/plant").catch(() => null) : null;
      if (P) for (const [name, x] of Object.entries(P.signals)) if (x.point && !S.histNames[x.point]) S.histNames[x.point] = name;
    }
    const groups = new Map(W.GROUP_ORDER.map(g => [g, []]));
    const unused = [];
    for (const p of S.allPoints) {
      const name = S.histNames[p.id];
      if (name) groups.get(W.signalGroup(name)).push({ id: p.id, name: W.signal(name), kind: p.kind });
      else if (p.mapping && p.label) { if (!groups.has("Other channels")) groups.set("Other channels", []); groups.get("Other channels").push({ id: p.id, name: p.label, kind: p.kind }); }
      else unused.push({ id: p.id, name: p.label || "Unused channel", kind: p.kind });
    }
    return { groups: [...groups].filter(([, xs]) => xs.length), unused };
  }
  VIEWS.history = async function () {
    const C = await historyChoices();
    const known = new Set(S.allPoints.map(p => p.id));
    S.trendSel = S.trendSel.filter(x => known.has(x));
    const nameOf = id => { for (const [, xs] of C.groups) { const x = xs.find(y => y.id === id); if (x) return x.name; } return id; };
    // ranges over a day are drawn from buckets of an hour or more: fetched again once a minute, not every refresh
    const now = Date.now(), [t0, t1] = rangeOf(S.trendRange, now), span = t1 - t0;
    const key = S.trendSel.join(",") + "|" + S.trendRange, f = S.trendFetch;
    let start = new Date(t0), end = new Date(t1), tr;
    if (span > 86400000 && f && f.key === key && now - f.at < 60000) ({ start, end, tr } = f);
    const q = `points=${encodeURIComponent(S.trendSel.join(","))}&start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;
    if (!tr && S.trendSel.length) { tr = await api("GET", "/api/trends?" + q); S.trendFetch = { key, start, end, tr, at: now }; }
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg"); svg.setAttribute("class", "chart");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Chart of the chosen readings: " + (S.trendSel.map(nameOf).join(", ") || "none chosen"));
    const full = S.trendSel.length >= 8;
    const box = x => {
      const id = "h-" + x.id.replace(/[^A-Za-z0-9]/g, "-"), on = S.trendSel.includes(x.id);
      const c = h("input", { type: "checkbox", id, checked: on, disabled: !on && full });
      c.addEventListener("change", () => {
        S.trendSel = c.checked ? S.trendSel.concat([x.id]).slice(0, 8) : S.trendSel.filter(y => y !== x.id);
        refresh();
      });
      return h("div", { class: "check" }, c, h("label", { for: id }, x.name, " ", h("span", { class: "muted small" }, x.id)));
    };
    const picker = h("div", { class: "picker" },
      C.groups.map(([g, xs]) => h("fieldset", null, h("legend", null, g), xs.map(box))),
      C.unused.length ? h("details", null, h("summary", null, `Unused channels (${C.unused.length})`),
        h("p", { class: "muted small" }, "Nothing is connected to these channels. This is not a fault."), C.unused.map(box)) : null);
    const ranges = h("div", { class: "row", role: "group", "aria-label": "Time range" }, RANGES.map(([k, l]) =>
      h("button", { class: "small" + (k === S.trendRange ? "" : " ghost"), "aria-pressed": k === S.trendRange ? "true" : "false",
        onclick: () => { S.trendRange = k; refresh(); } }, l)));
    const legend = h("div", { class: "legend" });
    const agg = tr ? Object.entries(tr.aggregated || {}) : [];
    const aggNote = agg.length ? `one point per ${agg[0][1].bucket}: solid = average, dashed = lowest and highest` : "";
    const dl = S.trendSel.length ? h("a", { class: "button", href: "/api/export.csv?" + q, download: "aquacrop-readings.csv" }, "Download readings")
      : h("span", { class: "muted" }, "Choose a reading to download it.");
    // the runtime and volume estimate reads every sample: only for ranges up to a day
    const an = span <= 86400000 ? await api("GET", "/api/analytics?start=" + encodeURIComponent(start.toISOString()) + "&end=" + encodeURIComponent(end.toISOString()))
      : { estimates: {}, note: "Running time and volume estimates are shown for ranges up to one day." };
    mount("v-history",
      h("div", { class: "card" }, h("h2", null, "History"),
        h("p", { class: "muted" }, `Choose up to 8 readings (${S.trendSel.length} chosen) and a time range. Times are site time (${S.tz}). A reading is recorded every minute.`),
        ranges,
        h("p", null, `From ${fmtTime(start.toISOString())} to ${fmtTime(end.toISOString())}`),
        S.trendSel.length ? svg : h("p", { class: "muted" }, "No reading chosen."), legend,
        tr && tr.truncated ? h("p", { class: "error" }, `Only the first ${tr.limit} readings of each are drawn; choose a shorter range. The download has all of them.`) : null,
        h("p", { class: "muted small" }, "Shaded bands are times with no valid reading. Dotted grey lines are readings whose time stamps an administrator marked as suspect."),
        h("div", { class: "row" }, dl, h("span", { class: "muted small" }, "A CSV file (opens in Excel) with every recorded reading of the chosen range: time in UTC and site time, value, unit and whether the reading was valid. Its last line says COMPLETE; if it says TRUNCATED (more than 100,000 readings), choose a shorter range or fewer readings."))),
      h("div", { class: "card" }, h("h2", null, "Readings to show"), picker),
      h("details", { class: "card tech" }, h("summary", null, h("strong", null, "Running time and volume estimates")),
        agg.length ? h("p", { class: "muted", id: "trend-aggregation" }, `Aggregated: ${agg.map(([pid, a]) => `${pid} ${a.samples} samples in ${a.buckets} buckets of ${a.bucket}${a.samples_in_range > a.samples ? ` (${a.samples_in_range - a.samples} simulation-time recorder samples not drawn)` : ""}`).join("; ")}. `,
          "Each point is the mean of the valid samples in its bucket; the dashed lines are their minimum and maximum. The download has every sample.") : null,
        h("table", null, h("thead", null, h("tr", null, ["Signal", "Kind", "Origin", "Estimate", "Coverage"].map(x => h("th", null, x)))),
          h("tbody", null, Object.entries(an.estimates).flatMap(([sig, e]) => Object.entries(e.by_origin).map(([o, v]) =>
            h("tr", null, h("td", null, sig), h("td", null, e.kind), h("td", null, oBadge(o)), h("td", null, num(v.estimate, 2)), h("td", null, (v.coverage * 100).toFixed(0) + " %")))))),
        h("p", { class: "muted" }, an.note)));
    if (!tr) return;
    const series = trendSeries(tr, S.trendSel, pid => ({ kind: (S.allPoints.find(p => p.id === pid) || {}).kind, label: nameOf(pid) }));
    const leg = AqChart.render(svg, { series, events: tr.events.map(e => [Date.parse(e[0]), e[1] + (e[2] ? " by " + e[2] : "")]), tStart: start.getTime(), tEnd: end.getTime(), timezone: S.tz, note: aggNote });
    legend.replaceChildren(...leg.map(l => h("span", null, h("span", { style: "color:" + l.color, "aria-hidden": "true" }, l.suspect ? "◌ " : "■ "), l.name)));   // O-01: the name in the text colour, the line colour on the mark
  };
  VIEWS.trends = VIEWS.history;

  /** Chart series from an /api/trends answer. Records whose stamps an administrator quarantined are split into
   * their own dashed grey series; neither half draws a line across the other, so a suspect stamp never joins a
   * trusted trace. An aggregated point (`tr.aggregated[pid]`) is a bucket: its mean is drawn solid and its min and
   * max dashed in the same colour; a digital point shows ON when it was ON at any time in the bucket.
   * `metaOf(pid)` gives { kind: "AI" | other, label }. */
  function trendSeries(tr, pids, metaOf) {
    const col = n => tr.columns.indexOf(n), iSus = col("timestamp_suspect"), iMin = col("min"), iMax = col("max");
    const tag = isReal() ? "REAL" : "SIM";
    return pids.flatMap((pid, k) => {
      const meta = metaOf(pid), rows = tr.series[pid] || [], agg = (tr.aggregated || {})[pid];
      const digital = meta.kind !== "AI", color = AqChart.COLORS[k % AqChart.COLORS.length];
      const split = vi => {
        const trusted = [], suspect = [];
        rows.forEach((r, i) => {
          const sus = iSus >= 0 && !!r[iSus], t = Date.parse(r[0]), own = sus ? suspect : trusted;
          // The server flags a gap between neighbouring rows of the merged list; where the other kind sits
          // in between (same stamps can interleave), each half breaks only on a missed snapshot (or bucket) of its own.
          const switched = i > 0 && (iSus >= 0 && !!rows[i - 1][iSus]) !== sus;
          const step = agg ? 1.5 * agg.bucket_s * 1000 : 150000;
          const gap = switched ? (own.length === 0 || t - own[own.length - 1][0] > step) : !!r[8];
          const v = r[vi];
          const ok = agg ? r[3] === "GOOD" || r[3] === "PARTIAL" : r[3] === "GOOD" || r[3] === "OVERRIDDEN";
          own.push([t, v == null ? null : (digital ? v > 0.5 : v), ok, gap]);
        });
        return [trusted, suspect];
      };
      const unit = rows.map(r => r[5]).find(u => u) || "", name = `${meta.label || pid}${unit ? " [" + unit + "]" : ""} (${tag})`;
      if (!agg) {
        const [trusted, suspect] = split(1);
        return [{ name, digital, points: trusted, dashed: false, color },
          { name: name + " SUSPECT timestamps (quarantined)", digital, points: suspect, suspect: true }];
      }
      if (digital) {
        const [trusted, suspect] = split(iMax);
        return [{ name: `${name}, ON at any time per ${agg.bucket}`, digital, points: trusted, color },
          { name: name + " SUSPECT timestamps (quarantined)", digital, points: suspect, suspect: true }];
      }
      const [mean, sMean] = split(1), [lo] = split(iMin), [hi] = split(iMax);
      return [{ name: `${name}, mean per ${agg.bucket}`, digital, points: mean, color },
        { name: `${name}, min`, digital, points: lo, dashed: true, color },
        { name: `${name}, max`, digital, points: hi, dashed: true, color },
        { name: name + " SUSPECT timestamps (quarantined)", digital, points: sMean, suspect: true }];
    });
  }

  // ------------------------------------------------ alarms/events
  VIEWS.alarms = async function () {
    const al = await api("GET", "/api/alarms"), ev = await api("GET", "/api/events?limit=200"), ch = await api("GET", "/api/commands/history?limit=100");
    // problems first (F-05), each with its equipment, next step and a link to its page; the tables follow under Details
    const plant = window.AqPlant && (isInst() || isReal());
    const P = plant ? await api("GET", "/api/plant").catch(() => null) : null;
    const PR = plant && !isReal() ? await api("GET", "/api/processes").catch(() => null) : null;
    if (P) P.alarms = al.open;
    const cards = window.AqPlant ? al.open.map(o => window.AqPlant.problemCard(o, PR, P)) : [];
    const occRow = o => h("tr", null, h("td", null, pill(W.SEVERITY[o.severity] || o.severity, o.severity === "CRITICAL" ? "bad" : "warn")), h("td", { class: "mono" }, o.rule_id), h("td", { class: "mono" }, o.target),
      h("td", null, o.message), h("td", null, o.active ? pill("still present", "bad") : pill("gone")), h("td", null, o.acknowledged ? `seen by ${o.ack_user}` : pill("not yet seen", "warn")),
      h("td", null, o.latched ? (o.reset_required ? pill("needs clearing", "warn") : "cleared " + (o.reset_user || "")) : "—"),
      h("td", null, fmtTime(o.first_seen_utc), susPill(o)), h("td", null, fmtTime(o.last_seen_utc)), h("td", { class: "mono" }, o.value == null ? "" : String(o.value)),
      h("td", null, !o.closed && !o.acknowledged ? h("button", { class: "cmd small", disabled: !can("alarm_ack"), onclick: () => act("POST", "/api/alarms/ack", { rule_id: o.rule_id, target: o.target }, "marked as seen") }, "Mark as seen") : null,
        !o.closed && o.reset_required && !o.active && o.acknowledged && o.target !== "controller" ? h("button", { class: "cmd small ghost", disabled: !can("lockout_reset"), onclick: () => act("POST", "/api/alarms/reset", { rule_id: o.rule_id, target: o.target }, "cleared") }, "Clear") : null));
    const hdr = ["Severity", "Code", "Where", "Message", "Condition", "Seen", "Latch", "First", "Last", "Value", ""];
    mount("v-alarms",
      h("div", { class: "card" }, h("h2", null, al.open.length ? `Problems now (${al.open.length})` : "Problems now"),
        al.open.length ? (cards.length ? cards : h("p", null, al.open.map(o => o.message).join("; "))) : h("p", null, "There are no problems now."),
        h("p", { class: "muted" }, "Marking a problem as seen only records that you have seen it. Clearing a stop does not start anything: each process is started again on its own page.")),
      h("details", { class: "card tech" }, h("summary", null, h("strong", null, `Alarm table (${al.open.length} open)`)),
        h("table", null, h("thead", null, h("tr", null, hdr.map(x => h("th", null, x)))), h("tbody", null, al.open.map(occRow)))),
      h("details", { class: "card tech" }, h("summary", null, h("strong", null, "Commands sent to the equipment (including refused ones)")),
        h("table", null, h("thead", null, h("tr", null, ["Time", "By", "Target", "Asked", "Origin", "Outcome", "Detail"].map(x => h("th", null, x)))),
          h("tbody", null, ch.map(c => h("tr", null, h("td", null, fmtTime(c.intent_utc), susPill(c)), h("td", null, c.actor), h("td", { class: "mono" }, c.target), h("td", null, c.desired ? "ON" : "OFF"),
            h("td", null, c.origin), h("td", null, pill(c.outcome, OK_OUTCOMES.has(c.outcome) ? "ok" : c.outcome === "REJECTED" ? "warn" : "bad")), h("td", { class: "muted" }, c.detail || "")))))),
      h("details", { class: "card tech" }, h("summary", null, h("strong", null, "Event log (newest first)")),
        h("table", null, h("tbody", null, ev.events.map(e => h("tr", null, h("td", null, fmtTime(e.utc), susPill(e)), h("td", null, e.kind), h("td", null, e.actor || ""),
          h("td", { class: "mono" }, JSON.stringify(e.payload).slice(0, 200))))))),
      h("details", { class: "card tech" }, h("summary", null, h("strong", null, `Past alarms (${al.history.length})`)),
        h("table", null, h("thead", null, h("tr", null, hdr.map(x => h("th", null, x)))), h("tbody", null, al.history.map(occRow)))));
  };

  // ------------------------------------------------ settings
  VIEWS.settings = async function () {
    if (S.settingsLoaded && S.view === "settings" && document.activeElement && document.activeElement.closest && document.activeElement.closest("#v-settings")) return;
    const [s, ret, bk, qu, off] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/retention").catch(() => null),
      api("GET", "/api/backup").catch(() => null), api("GET", "/api/history/quarantine").catch(() => null),
      api("GET", "/api/offsite").catch(() => null)]), c = s.config;
    S.settingsLoaded = true;
    const tbl = (hdr, rows) => h("table", null, h("thead", null, h("tr", null, hdr.map(x => h("th", null, x)))), h("tbody", null, rows.map(r => h("tr", null, r.map(x => h("td", null, x == null ? "" : String(x)))))));
    const userForm = can("user_admin") ? (() => {
      const u = h("input", { placeholder: "username" }), p = h("input", { type: "password", placeholder: "initial password (12+)" });
      const r = h("select", null, ["viewer", "operator", "admin"].map(x => h("option", { value: x }, x)));
      const cm = h("input", { type: "checkbox" });
      return h("div", null, tbl(["User", "Role", "Commissioning", "Disabled", "Must change pw"], (s.users || []).map(x => [x.username, x.role, x.commissioning ? "yes" : "no", x.disabled ? "yes" : "no", x.must_change_password ? "yes" : "no"])),
        h("div", { class: "row" }, u, p, r, h("label", null, h("span", null, "commissioning"), cm),
          h("button", { class: "small", onclick: () => act("POST", "/api/users", { username: u.value, password: p.value, role: r.value, commissioning: cm.checked }, "user created") }, "Create user")));
    })() : h("p", { class: "muted" }, "User administration requires the admin role.");
    mount("v-settings",
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "Versions"), h("dl", { class: "kv" },
          h("dt", null, "Software"), h("dd", null, s.software_version),
          h("dt", null, "Release"), h("dd", null, releaseLine(s.release)),
          h("dt", null, "Config"), h("dd", null, `${c.config_id}@${c.config_version} (${c.profile})`),
          h("dt", null, "Inventory"), h("dd", null, s.inventory ? `${s.inventory.modules} modules: ${s.inventory.AI} AI + ${s.inventory.DI} DI + ${s.inventory.DO} DO = ${s.inventory.points} points` : "—"),
          s.state_dir ? [h("dt", null, "Data folder"), h("dd", null, s.state_dir)] : null,
          h("dt", null, "Python"), h("dd", null, s.python), h("dt", null, "Platform"), h("dd", null, s.platform))),
        h("div", { class: "card" }, h("h2", null, "Storage"), h("dl", { class: "kv" },
          h("dt", null, "Recording"), h("dd", null, `complete snapshot every ${s.storage.recording_interval_s} s of wall time` +
            (s.storage.sim_time_recording_s ? `; plus a separate simulation-time series every ${s.storage.sim_time_recording_s} simulated s` : ""),
            s.storage.snapshots_paused ? [" ", pill("PAUSED: low disk", "bad")] : null),
          h("dt", null, "Not written"), h("dd", null, s.storage.snapshot_pause && s.storage.snapshot_pause.count ?
            `${s.storage.snapshot_pause.count} snapshots from ${s.storage.snapshot_pause.first} to ${s.storage.snapshot_pause.last} (shown as a gap; never backfilled)` : "none"),
          h("dt", null, "Retention"), h("dd", null, `rolling ${s.storage.retention_days} days (${s.storage.retention_provenance}). ${s.storage.retention_note || ""}`),
          h("dt", null, "History held"), h("dd", null, s.storage.oldest_sample_utc ? `${(s.storage.history_span_days || 0).toFixed(1)} days, since ${s.storage.oldest_sample_utc}` : "none yet"),
          h("dt", null, "DB size"), h("dd", null, `${gib(s.storage.db_bytes)} of ${gib(s.storage.budget_bytes)} budget (file ${gib(s.storage.db_file_bytes)}, WAL ${mib(s.storage.wal_bytes)})`),
          h("dt", null, "WAL"), h("dd", null, s.storage.wal_over_warning ? pill(`${mib(s.storage.wal_bytes)}: above ${mib(s.storage.wal_warning_bytes)}`, "warn") : `${mib(s.storage.wal_bytes)} (warning above ${mib(s.storage.wal_warning_bytes)})`,
            s.storage.wal_checkpoint && s.storage.wal_checkpoint.behind_since_utc ? ` · checkpoints held back since ${s.storage.wal_checkpoint.behind_since_utc}` : ""),
          h("dt", null, "Projection"), h("dd", null, s.storage.projected_window_bytes != null ? `${gib(s.storage.projected_window_bytes)} at ${s.storage.retention_days} days` + (s.storage.projected_over_budget ? " (over budget)" : "") : "after one day of history"),
          h("dt", null, "Free disk"), h("dd", null, `${gib(s.storage.free_bytes)} (warning below ${gib(s.storage.warning_free_bytes)}, starts refused below ${gib(s.storage.reserve_bytes)}, snapshots pause below ${gib(s.storage.snapshot_floor_bytes)})`))),
        retentionCard(ret),
        quarantineCard(qu),
        bk ? backupCard(bk) : h("div", { class: "card" }, h("h2", null, "Backups"), h("p", { class: "muted" }, "Backup state unavailable.")),
        offsiteCard(off),
        h("div", { class: "card" }, h("h2", null, "Production status"), h("dl", { class: "kv" }, ...Object.entries(s.production_fields).flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
          h("p", { class: "muted" }, s.commissioning_evidence))),
      h("div", { class: "card" }, h("h2", null, "Buses"), tbl(["Bus", "Kind", "Port", "Serial", "Poll s"], c.buses.map(b => [b.bus_id, b.kind, b.port, `${b.serial.baudrate} ${b.serial.bytesize}${b.serial.parity}${b.serial.stopbits}`, b.poll_interval_s]))),
      h("div", { class: "card" }, h("h2", null, "Inventory"), tbl(["Device", "Bus", "Addr", "Model", "SKU", "Variant", "Revision evidence", "Firmware evidence"], c.devices.map(d => [d.device_uid, d.bus_id, d.address, d.model, d.sku, d.hardware_variant, d.revision_evidence || "none", d.firmware_evidence || "none"]))),
      h("div", { class: "card" }, h("h2", null, "Scaling profiles"), tbl(["Profile", "Ver", "Status", "Raw range", "Eng range", "Valid raw"], c.scaling_profiles.map(p => [p.profile_id, p.version, p.status, `${p.raw_low}..${p.raw_high} ${p.input_unit}`, `${p.eng_low}..${p.eng_high} ${p.unit}`, `${p.valid_raw_min}..${p.valid_raw_max}`]))),
      h("div", { class: "card" }, h("h2", null, "Alarm thresholds"), tbl(["Rule", "Kind", "Point", "Threshold", "Hysteresis", "Debounce s", "Severity", "Latch", "Provenance"], c.alarm_rules.map(r => [r.rule_id, r.kind, r.point, r.threshold, r.hysteresis, r.debounce_s, r.severity, r.latch, r.provenance]))),
      h("div", { class: "card" }, h("h2", null, "Virtual mapping (SIMULATED) / physical mapping"), tbl(["Point", "Signal", "Note"], c.virtual_mapping.map(m => [m.point, m.signal, m.note])),
        h("p", null, "Physical mapping: ", c.physical_mapping.length ? "" : "empty — no approved wiring or topology.")),
      h("div", { class: "card" }, h("h2", null, "Users and roles"), userForm,
        h("p", { class: "muted" }, "Proposed role rights: viewer reads; operator commands simulation and acknowledges; admin manages accounts/config. Commissioning is a separate flag that additionally needs an isolated setup and an expiring server-side lease. Production assignments need owner approval.")),
      h("div", { class: "card" }, h("h2", null, "Change my password"), (() => {
        const o = h("input", { type: "password", placeholder: "current" }), n = h("input", { type: "password", placeholder: "new (12+)" });
        return h("div", { class: "row" }, o, n, h("button", { class: "small", onclick: async () => { const r = await act("POST", "/api/auth/password", { old_password: o.value, new_password: n.value }); if (r) showLogin(); } }, "Change"));
      })()));
  };

  // ---------------------------------------------------------------- shared with plant.js
  window.AqUI = { h, $, mount, toast, can, fmtTime, age, ago, agoClock, num, qBadge, oBadge, pill, api, act, cbtn, S, VIEWS, trendSeries,
                  refresh: () => refresh(), highlightNav, isReal, isInst };

  // ---------------------------------------------------------------- boot
  api("GET", "/api/auth/session").then(afterLogin).catch(() => showLogin());
})();
