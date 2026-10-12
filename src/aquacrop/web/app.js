/* AquaCrop browser UI. Vanilla JS, no external assets. All user/config text is inserted with
 * textContent (never parsed as markup) so labels and event text cannot become executable HTML. */
"use strict";
(function () {
  const S = { csrf: null, user: null, perms: [], status: null, lastOk: 0, stale: false, view: "overview",
              cfgVersion: null, tz: "Europe/Stockholm", trendSel: ["analog_A.AI1", "analog_A.AI2", "relay_A.DI1"],
              trendMin: 10, settingsLoaded: false };
  const STALE_AFTER_MS = 4000;

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
    for (const k of kids.flat()) {
      if (k == null || k === false) continue;
      e.appendChild(typeof k === "string" || typeof k === "number" ? document.createTextNode(String(k)) : k);
    }
    return e;
  }
  const $ = id => document.getElementById(id);
  function mount(id, ...kids) { const el = $(id); el.replaceChildren(...kids.flat()); return el; }
  function toast(msg, bad) {
    const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
    t.style.background = bad ? "var(--bad)" : "";
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 5000);
  }
  function can(p) { return S.perms.includes(p); }
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
  function qBadge(q) { return h("span", { class: "q q-" + q }, q); }
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
    if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.status);
    return data;
  }
  async function act(method, path, body, okMsg) {
    if (S.stale) { toast("Connection stale: command not sent", true); return null; }
    try { const r = await api(method, path, body); if (okMsg) toast(okMsg); refresh(); return r; }
    catch (e) { toast(String(e.message || e), true); return null; }
  }
  function rid() { return "ui_" + crypto.getRandomValues(new Uint32Array(4)).join("").slice(0, 24); }
  function cmd(target, desired) {
    return act("POST", "/api/commands", { backend: "SIMULATED", config_version: S.cfgVersion, target, desired,
      request_id: rid(), reason: "operator via I/O bench" }).then(r => r && toast(`${target} → ${desired ? "ON" : "OFF"}: ${r.outcome}${r.detail ? " (" + r.detail + ")" : ""}`, r.outcome !== "READBACK_CONFIRMED"));
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
  function route() {
    S.view = (location.hash || "#overview").slice(1);
    document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + S.view));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("hidden", v.id !== "v-" + S.view));
    refresh();
  }
  window.addEventListener("hashchange", route);

  let busy = false;
  async function refresh() {
    if (busy || $("app").classList.contains("hidden")) return;
    busy = true;
    try {
      S.status = await api("GET", "/api/status");
      S.lastOk = Date.now(); S.cfgVersion = S.status.config_version; S.tz = S.status.timezone;
      setStale(false);
      renderBanner();
      const ae = document.activeElement;   // don't rebuild a view while the user is typing in it
      const typing = ae && ae.closest && ae.closest("#v-" + S.view) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName);
      const fn = VIEWS[S.view]; if (fn && !typing) await fn();
    } catch (e) {
      if (String(e.message) !== "not authenticated") setStale(true);
    } finally { busy = false; }
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
  setInterval(refresh, 1000);

  function renderBanner() {
    const st = S.status;
    const b = $("banner"); b.textContent = st.banner;
    b.className = "banner " + (st.banner.startsWith("SIMULATION") ? "sim" : st.banner.includes("ARMED") ? "armed" : "real");
    const real = st.profile !== "SIMULATED";
    document.querySelectorAll('#nav a[href="#twin"], #nav a[href="#control"]').forEach(a => a.classList.toggle("hidden", real));
    if (real) {
      $("statusline").textContent = `Profile ${st.profile} · config ${st.config_id}@${st.config_version} · ${st.physical_writes}` +
        ` · newest data ${num(st.freshness.newest_sample_age_s, 1)} s old · ${S.tz}`;
      return;
    }
    const t = st.twin, c = st.controller;
    $("statusline").textContent = `Profile ${st.profile} · config ${st.config_id}@${st.config_version} · controller ${c.state}/${c.phase}` +
      ` · twin ${t.activity} ${t.speed}× sim ${t.sim_s.toFixed(1)} s · source ${t.source}${t.scenario ? " · scenario " + t.scenario : ""}` +
      ` · newest data ${num(st.freshness.newest_sample_age_s, 1)} s old · ${S.tz}`;
  }

  // ---------------------------------------------------------------- views
  const VIEWS = {};

  function isReal() { return S.status && S.status.profile !== "SIMULATED"; }
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
  // Records whose UTC stamps an administrator quarantined (ADR-0009): the stored time is shown unchanged, flagged.
  function susPill(r) {
    return r && r.timestamp_suspect ? [" ", h("span", { class: "pill warn", title: `stored time unchanged; quarantined by ${r.quarantine_id}` }, "SUSPECT TIME")] : null;
  }
  function quarantineCard(q) {
    if (!q) return h("div", { class: "card" }, h("h2", null, "Clock and timestamps"), h("p", { class: "muted" }, "Timestamp state unavailable."));
    const prop = q.proposal || {}, rec = q.recorder || {};
    const kids = [h("h2", null, "Clock and timestamps"), h("dl", { class: "kv" },
      h("dt", null, "Clock epoch"), h("dd", null, String(q.clock_epoch), q.clock_epoch ? h("span", { class: "muted" }, " (snapshot ids carry /e" + q.clock_epoch + "/)") : null),
      h("dt", null, "Recorder"), h("dd", null, rec.clock_behind ? [pill("WAITING: stored snapshots are later than the clock", "bad"), h("div", { class: "muted" }, `since ${fmtTime(rec.clock_behind.since_utc)}; newest stored ${fmtTime(rec.clock_behind.newest_label_utc)}`)] : pill(rec.state === "RUNNING" ? "recording" : (rec.state || "unknown"), rec.state === "RUNNING" ? "ok" : "warn")),
      h("dt", null, "Quarantines"), h("dd", null, q.quarantines.length ? `${q.quarantines.length} in force` : "none"))];
    q.quarantines.slice().reverse().forEach(x => kids.push(h("p", { class: "muted" },
      `${x.quarantine_id}: epoch ${x.clock_epoch}, confirmed by ${x.confirmed_by} at ${fmtTime(x.confirmed_utc)}; ids ` +
      Object.entries(x.ranges).map(([t, r]) => `${t} ${r[0]}–${r[1]}`).join(", ") + ". Records kept with their original stamps.")));
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
  // SD-card backups and encrypted cloud copies (ADR-0010). Everything shown comes from the controller's
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
    mount("v-overview",
      h("div", { class: "grid" },
        h("div", { class: "card" }, h("h2", null, "System"), h("dl", { class: "kv" },
          h("dt", null, "Profile"), h("dd", null, st.profile),
          h("dt", null, "Physical writes"), h("dd", null, lease ? pill(st.physical_writes, "bad") : pill(st.physical_writes, "ok")),
          h("dt", null, "Bus workers"), h("dd", null, st.acquisition_task.ok ? pill("polling", "ok") : pill(st.acquisition_task.state, "bad")),
          h("dt", null, "Storage"), h("dd", null, st.storage.ok ? pill("ok", "ok") : pill("DEGRADED: " + st.storage.reason, "bad"),
            " ", storageText(st.storage)),
          h("dt", null, "Live control"), h("dd", null, st.live_control))),
        h("div", { class: "card" }, h("h2", null, "Relay channel modes"),
          h("table", null, h("tbody", null, Object.entries(st.relay_modes).map(([k, m]) => h("tr", null, h("td", null, k),
            h("td", { class: "mono" }, m ? m.join(" ") : "not read"),
            h("td", null, m && m.every(x => x === "NORMAL") ? pill("all NORMAL", "ok") : pill("bench writes blocked", "warn"))))))),
        h("div", { class: "card" }, h("h2", null, `Active alarms (${al.open.length})`),
          al.open.length ? h("table", null, h("tbody", null, al.open.map(o => h("tr", null, h("td", null, pill(o.severity, o.severity === "CRITICAL" ? "bad" : "warn")),
            h("td", null, o.rule_id), h("td", null, o.target), h("td", null, o.active ? "active" : "cleared"))))) : h("p", { class: "muted" }, "None"))),
      h("div", { class: "card" }, h("h2", null, "Devices (each polled and reported independently)"), devTable(st)));
  }

  async function hwIO() {
    const d = await api("GET", "/api/points"), b = await api("GET", "/api/bench"), lease = b.lease;
    const allowed = new Set(lease ? lease.allowlist : []);
    const bench = (target, desired) => act("POST", "/api/bench/command", { backend: "REAL_BENCH", config_version: S.cfgVersion,
      lease_id: lease.lease_id, target, desired, request_id: rid(), reason: "bench test" })
      .then(r => r && toast(`REAL ${target} → ${desired ? "ON" : "OFF"}: ${r.outcome}${r.detail ? " (" + r.detail + ")" : ""}`, r.outcome !== "READBACK_CONFIRMED"));
    const groups = d.devices.map(g => h("div", { class: "card" },
      h("div", { class: "row" }, h("h3", null, `${g.device_uid} — ${g.model} (SKU ${g.sku}) · ${g.bus_id} @ ${g.address}`),
        g.online ? pill("online", "ok") : pill("FAILED: " + (g.last_error || ""), "bad")),
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
    let panel;
    if (lease) {
      panel = h("div", { class: "card armed" }, h("h2", null, "BENCH WRITES ARMED"),
        h("p", null, `Lease for ${lease.username}, ${Math.round(lease.remaining_s)} s left. Allowlist: ${lease.allowlist.join(", ")}.`),
        h("p", null, `Setup: ${lease.setup_description}`),
        h("p", { class: "muted" }, "Coil readback is not actuator proof. Ending the lease sends nothing; outputs stay as they are."),
        h("button", { class: "cmd danger", onclick: () => act("POST", "/api/bench/lease/revoke", {}, "lease ended") }, "End lease now"));
    } else if (b.can_arm && S.armPanel) {
      panel = S.armPanel;                  // keep typed values and ticked boxes across polls
    } else if (b.can_arm) {
      const conf = h("input", { placeholder: b.confirmation_phrase, autocomplete: "off", style: "width:320px" });
      const setup = h("input", { placeholder: "describe the isolated bench setup", style: "width:420px" });
      const dur = h("input", { type: "number", min: b.duration_range_s[0], max: b.duration_range_s[1], value: 600, style: "width:90px" });
      const boxes = b.relay_outputs.map(id => ({ id, el: h("input", { type: "checkbox" }) }));
      panel = h("div", { class: "card" }, h("h2", null, "Arm a bench lease (commissioning)"),
        h("p", null, "Only on an isolated bench with every production load disconnected. Writes go to real relays."),
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

  VIEWS.overview = async function () {
    if (isReal()) return hwOverview();
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
    if (isReal()) return mount("v-twin", h("div", { class: "card" }, "The digital twin is available in the simulation profile only."));
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
          h("td", null, qBadge(s.quality || "UNAVAILABLE"), " ", s.origin ? oBadge(s.origin) : null),
          h("td", { class: "muted" }, s.override ? `override by ${s.override.owner}: ${s.override.reason} (base ${s.override.base_raw})` : (s.reason || "")),
          h("td", null, age(s.acquired_utc)), h("td", null, ctl));
      });
      return h("div", { class: "card" },
        h("div", { class: "row" }, h("h3", null, `${g.device_uid} — ${g.model} (SKU ${g.sku}) · ${g.bus_id} @ ${g.address}`),
          g.online ? pill("online", "ok") : pill("FAILED: " + (g.last_error || ""), "bad"),
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

  // ------------------------------------------------ control
  VIEWS.control = async function () {
    if (isReal()) return mount("v-control", h("div", { class: "card" }, "The controller exists only in simulation in this release."));
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

  // ------------------------------------------------ trends
  VIEWS.trends = async function () {
    if (!S.allPoints) { const d = await api("GET", "/api/points"); S.allPoints = d.devices.flatMap(g => g.points); }
    const end = new Date(Date.now()), start = new Date(end - S.trendMin * 60000);
    const q = `points=${encodeURIComponent(S.trendSel.join(","))}&start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;
    const tr = await api("GET", "/api/trends?" + q);
    // Records whose stamps an administrator quarantined are split into their own dashed grey series; neither
    // half draws a line across the other, so a suspect stamp never joins a trusted trace.
    const iSus = tr.columns.indexOf("timestamp_suspect");
    const series = S.trendSel.flatMap(pid => {
      const meta = S.allPoints.find(p => p.id === pid) || {}, rows = tr.series[pid] || [];
      const trusted = [], suspect = [];
      rows.forEach((r, i) => {
        const sus = iSus >= 0 && !!r[iSus], t = Date.parse(r[0]), own = sus ? suspect : trusted;
        // The server flags a gap between neighbouring rows of the merged list; where the other kind sits
        // in between (same stamps can interleave), each half breaks only on a missed snapshot of its own.
        const switched = i > 0 && (iSus >= 0 && !!rows[i - 1][iSus]) !== sus;
        const gap = switched ? (own.length === 0 || t - own[own.length - 1][0] > 150000) : !!r[8];
        own.push([t, r[1] == null ? null : (meta.kind === "AI" ? r[1] : r[1] > 0.5), r[3] === "GOOD" || r[3] === "OVERRIDDEN", gap]);
      });
      const unit = rows.map(r => r[5]).find(u => u) || "", name = `${pid}${unit ? " [" + unit + "]" : ""} (SIM)`;
      return [{ name, digital: meta.kind !== "AI", points: trusted, dashed: false },
        { name: name + " SUSPECT timestamps (quarantined)", digital: meta.kind !== "AI", points: suspect, suspect: true }];
    });
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg"); svg.setAttribute("class", "chart");
    const picker = h("select", { multiple: true, size: 8 }, S.allPoints.map(p => h("option", { value: p.id, selected: S.trendSel.includes(p.id) }, `${p.id}${p.label ? " — " + p.label : ""}`)));
    picker.addEventListener("change", () => { S.trendSel = Array.from(picker.selectedOptions).map(o => o.value).slice(0, 8); refresh(); });
    const rng = h("select", null, [5, 10, 30, 120, 720].map(m => h("option", { value: m, selected: m === S.trendMin }, m + " min")));
    rng.addEventListener("change", () => { S.trendMin = Number(rng.value); refresh(); });
    const an = await api("GET", "/api/analytics?start=" + encodeURIComponent(start.toISOString()) + "&end=" + encodeURIComponent(end.toISOString()));
    const legend = h("div", { class: "legend" });
    mount("v-trends",
      h("div", { class: "card" }, h("div", { class: "row" }, h("label", null, "Points (max 8)", picker), h("label", null, "Range", rng),
        h("a", { href: "/api/export.csv?" + q, download: "aquacrop-samples.csv" }, "Export CSV (complete, paginated)")),
        svg, legend, tr.truncated ? h("p", { class: "error" }, `Truncated at ${tr.limit} rows per point — narrow the range.`) : null),
      h("div", { class: "card" }, h("h3", null, "Runtime / volume estimates (from sampled history; coverage disclosed)"),
        h("table", null, h("thead", null, h("tr", null, ["Signal", "Kind", "Origin", "Estimate", "Coverage"].map(x => h("th", null, x)))),
          h("tbody", null, Object.entries(an.estimates).flatMap(([sig, e]) => Object.entries(e.by_origin).map(([o, v]) =>
            h("tr", null, h("td", null, sig), h("td", null, e.kind), h("td", null, oBadge(o)), h("td", null, num(v.estimate, 2)), h("td", null, (v.coverage * 100).toFixed(0) + " %")))))),
        h("p", { class: "muted" }, an.note)));
    const leg = AqChart.render(svg, { series, events: tr.events.map(e => [Date.parse(e[0]), e[1] + (e[2] ? " by " + e[2] : "")]), tStart: start.getTime(), tEnd: end.getTime(), timezone: S.tz });
    legend.replaceChildren(...leg.map(l => h("span", { style: "color:" + l.color }, (l.suspect ? "◌ " : "■ ") + l.name)));
  };

  // ------------------------------------------------ alarms/events
  VIEWS.alarms = async function () {
    const al = await api("GET", "/api/alarms"), ev = await api("GET", "/api/events?limit=200"), ch = await api("GET", "/api/commands/history?limit=100");
    const occRow = o => h("tr", null, h("td", null, pill(o.severity, o.severity === "CRITICAL" ? "bad" : "warn")), h("td", null, o.rule_id), h("td", { class: "mono" }, o.target),
      h("td", null, o.message), h("td", null, o.active ? pill("ACTIVE", "bad") : pill("cleared")), h("td", null, o.acknowledged ? `ack ${o.ack_user}` : pill("UNACK", "warn")),
      h("td", null, o.latched ? (o.reset_required ? pill("RESET REQUIRED", "warn") : "reset " + (o.reset_user || "")) : "—"),
      h("td", null, fmtTime(o.first_seen_utc), susPill(o)), h("td", null, fmtTime(o.last_seen_utc)), h("td", { class: "mono" }, o.value == null ? "" : String(o.value)),
      h("td", null, !o.closed && !o.acknowledged ? h("button", { class: "cmd small", disabled: !can("alarm_ack"), onclick: () => act("POST", "/api/alarms/ack", { rule_id: o.rule_id, target: o.target }, "acknowledged") }, "Ack") : null,
        !o.closed && o.reset_required && !o.active && o.acknowledged && o.target !== "controller" ? h("button", { class: "cmd small ghost", disabled: !can("lockout_reset"), onclick: () => act("POST", "/api/alarms/reset", { rule_id: o.rule_id, target: o.target }, "reset") }, "Reset") : null));
    const hdr = ["Sev", "Rule", "Target", "Message", "Condition", "Ack", "Latch/reset", "First", "Last", "Value", ""];
    mount("v-alarms",
      h("div", { class: "card" }, h("h2", null, "Open alarm occurrences"), h("p", { class: "muted" }, "Acknowledging never clears a condition or resets a lockout."),
        h("table", null, h("thead", null, h("tr", null, hdr.map(x => h("th", null, x)))), h("tbody", null, al.open.map(occRow)))),
      h("div", { class: "card" }, h("h2", null, "Commands (incl. rejected / uncertain)"),
        h("table", null, h("thead", null, h("tr", null, ["Time", "Actor", "Target", "Desired", "Origin", "Outcome", "Detail"].map(x => h("th", null, x)))),
          h("tbody", null, ch.map(c => h("tr", null, h("td", null, fmtTime(c.intent_utc), susPill(c)), h("td", null, c.actor), h("td", { class: "mono" }, c.target), h("td", null, c.desired ? "ON" : "OFF"),
            h("td", null, c.origin), h("td", null, pill(c.outcome, c.outcome === "READBACK_CONFIRMED" ? "ok" : c.outcome === "REJECTED" ? "warn" : "bad")), h("td", { class: "muted" }, c.detail || "")))))),
      h("div", { class: "card" }, h("h2", null, "Events"),
        h("table", null, h("tbody", null, ev.events.map(e => h("tr", null, h("td", null, fmtTime(e.utc), susPill(e)), h("td", null, e.kind), h("td", null, e.actor || ""),
          h("td", { class: "mono" }, JSON.stringify(e.payload).slice(0, 200))))))),
      h("div", { class: "card" }, h("h2", null, "Alarm history (closed)"),
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
          h("dt", null, "Software"), h("dd", null, s.software_version), h("dt", null, "Python"), h("dd", null, s.python), h("dt", null, "Platform"), h("dd", null, s.platform),
          h("dt", null, "Config"), h("dd", null, `${c.config_id}@${c.config_version} (${c.profile})`))),
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

  // ---------------------------------------------------------------- boot
  api("GET", "/api/auth/session").then(afterLogin).catch(() => showLogin());
})();
