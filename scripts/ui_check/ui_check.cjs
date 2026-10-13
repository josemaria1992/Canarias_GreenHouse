// Browser evidence for the installation pages (UI-01 to UI-05) in headless Chromium.
//
// Driven by run_ui_check.py, which starts `python -m aquacrop serve --config installation-48` (simulation)
// with a throwaway state directory, runs the "main" phase, restarts the server and runs the "after"
// phase. Every check reads the controller through the API as well as the screen. None of this is
// physical verification.
//
// Environment: AQ_BASE, AQ_USER, AQ_PASS (throwaway administrator), AQ_OUT (screenshots and results),
// PLAYWRIGHT_MODULE (defaults to "playwright"), CHROMIUM_PATH (optional browser executable).
"use strict";
const fs = require("fs");
const path = require("path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const BASE = process.env.AQ_BASE, USER = process.env.AQ_USER, PASS = process.env.AQ_PASS, OUT = process.env.AQ_OUT;
const PHASE = process.argv[2] || "main";   // main, after (restart) or hardware (read-only profile, fake bus)
const PLANT_PAGES = ["overview", "nutrients", "main-tank", "main-pumps", "sector-S1", "sector-S2", "drainage", "recipes"];
const SHARED_PAGES = ["twin", "io", "trends", "alarms", "settings"];
const checks = [];

function check(id, ok, detail) {
  checks.push({ id, ok: !!ok, detail: String(detail) });
  console.log(`${ok ? "PASS" : "FAIL"} ${id}: ${detail}`);
}
const shot = (p, name, full = true) => p.screenshot({ path: path.join(OUT, name + ".png"), fullPage: full });
const api = (p, url, method = "GET", body) => p.evaluate(async ([url, method, body]) => {
  const csrf = window.AqUI && window.AqUI.S.csrf;
  const r = await fetch(url, { method, credentials: "same-origin",
    headers: method === "GET" ? {} : { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const ct = r.headers.get("content-type") || "";
  return { status: r.status, body: ct.includes("json") ? await r.json() : await r.text() };
}, [url, method, body]);
const get = async (p, url) => (await api(p, url)).body;

async function until(p, fn, ms = 20000) {
  const end = Date.now() + ms;
  let v;
  while (Date.now() < end) {
    v = await fn();
    if (v) return v;
    await p.waitForTimeout(250);
  }
  return v;
}
const browsers = [];
async function launch() {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  browsers.push(b);
  return b;
}
function watchErrors(p) {
  const errs = [];
  p.on("pageerror", e => errs.push(String(e)));
  // deliberate refusals (4xx) and the offline step log network errors; script errors and 5xx still count
  p.on("console", m => { if (m.type() === "error" && !/status of 4\d\d|Failed to fetch|ERR_INTERNET_DISCONNECTED/.test(m.text())) errs.push("console: " + m.text()); });
  p.on("dialog", d => d.accept(d.message().startsWith("Activate") ? "walkthrough: shorter sector 1" : "walkthrough"));
  return errs;
}
async function login(p, user, pass) {
  for (let i = 0; i < 120; i++) { try { await p.goto(BASE + "/"); break; } catch (e) { await p.waitForTimeout(250); } }
  await p.waitForSelector("input[name=username]");
  await p.fill("input[name=username]", user);
  await p.fill("input[name=password]", pass);
  await p.keyboard.press("Enter");
  await p.waitForSelector("#app:not(.hidden)", { timeout: 15000 });
  await until(p, () => p.evaluate(() => !!(window.AqUI && window.AqUI.S.status)));
}
async function show(p, view) {
  await p.goto(BASE + "/#" + view);
  await until(p, () => p.evaluate(v => {
    const sec = document.querySelector("section.view:not(.hidden)");   // rendered for this page, not the previous one
    return sec && location.hash === "#" + v && window.AqUI.S.rendered === v && sec.innerText.trim().length > 40;
  }, view), 10000);
  await p.waitForTimeout(400);
}
const visibleText = p => p.evaluate(() => (document.querySelector("section.view:not(.hidden)") || document.body).innerText);
const procs = async p => (await get(p, "/api/processes")).processes;

async function main() {
  const b = await launch();
  const ctx = await b.newContext({ viewport: { width: 1366, height: 1000 }, acceptDownloads: true });
  const p = await ctx.newPage();
  const errs = watchErrors(p);
  await login(p, USER, PASS);

  // UI-01: every installation page and the shared pages render with content and no page error
  const nav = (await p.locator("#nav").innerText()).replace(/\s+/g, " ");
  check("UI-01 nav", ["Nutrients & acid", "Main tank", "Main pumps", "Sector 1", "Sector 2", "Drainage", "Recipes"]
    .every(t => nav.includes(t)) && !/\bControl\b/.test(nav), nav);
  for (const v of PLANT_PAGES.concat(SHARED_PAGES)) {
    await show(p, v);
    const txt = await visibleText(p);
    const stale = await p.locator("#stale").evaluate(e => !e.classList.contains("hidden"));
    await shot(p, "page-" + v);
    check("UI-01 page " + v, txt.length > 80 && !txt.includes("This page does not exist in this profile") && !stale, `${txt.length} chars, stale banner ${stale}`);
  }

  // UI-01: each process page carries its part of the diagram, its controls, its alarms and its trend
  for (const v of PLANT_PAGES.filter(x => !["overview", "recipes"].includes(x))) {
    await show(p, v);
    const parts = await p.evaluate(() => {
      const sec = document.querySelector("section.view:not(.hidden)");
      return { focus: sec.querySelectorAll("svg.diagram g.hot.focus").length, dim: sec.querySelectorAll("svg.diagram g.hot.dim").length,
        chart: !!sec.querySelector("svg.chart-small") && sec.querySelector("svg.chart-small").childNodes.length > 0,
        alarms: /Alarms for this page/.test(sec.innerText), auto: [...sec.querySelectorAll("button")].some(b => b.textContent === "AUTO"),
        link: !!sec.querySelector('a[href="#main-pumps"]') };
    });
    check("UI-01 process page " + v, parts.focus > 0 && parts.dim > 0 && parts.chart && parts.alarms && (parts.auto || (v.startsWith("sector") && parts.link)),
      `${parts.focus} highlighted parts, trend drawn ${parts.chart}, page alarms ${parts.alarms}, ${parts.auto ? "AUTO/MANUAL/STOP" : "link to the irrigation controls"}`);
  }
  await show(p, "main-pumps");
  const mpTxt = await until(p, async () => { const t = await visibleText(p); return /Status word\s+0x[0-9A-F]{4}/.test(t) && t; });
  check("UI-01 drive interface (simulated)", !!mpTxt && /Drive interface\s+MODBUS_RTU\s+DEMO/.test(mpTxt) && /Control register/.test(mpTxt),
    "Main pumps shows the simulated drive's Modbus interface: MODBUS_RTU with the DEMO map, its status word and control register");
  await show(p, "nutrients");
  await p.locator(".tabs button", { hasText: "Acid" }).click();
  await until(p, async () => /pH target \(adjustable\)/.test(await visibleText(p)));
  const acidTab = await visibleText(p);
  await p.locator(".tabs button", { hasText: "Stock B" }).click();
  await until(p, async () => /EC target/.test(await visibleText(p)));
  const bTab = await visibleText(p);
  await shot(p, "page-nutrients-tab-B");
  check("UI-01 nutrients tabs", acidTab.includes("Acid pH dosing") && /pH target \(adjustable\)\s*6/.test(acidTab) && bTab.includes("A:B ratio") && bTab.includes("Stock B water fill"),
    "Acid tab: pH target and the acid process; Stock B tab: EC target, A:B ratio and the stock B fill");
  await p.locator(".tabs button", { hasText: "Stock A" }).click();

  // UI-02: the diagram is clickable (mouse and keyboard), labelled, with units, quality and age
  await show(p, "overview");
  const hot = await p.$$eval("#v-overview svg.diagram g.hot", gs => gs.map(g => [g.getAttribute("role"), g.getAttribute("aria-label"), g.getAttribute("tabindex")]));
  check("UI-02 diagram parts", hot.length >= 25 && hot.every(([r, l, t]) => r === "link" && l && t === "0"), `${hot.length} clickable parts, all role=link with labels`);
  const svgText = await p.$eval("#v-overview svg.diagram", s => s.textContent);
  check("UI-02 units", ["L", "pH", "mS/cm", "L/min", "bar", "GOOD"].every(u => svgText.includes(u)) && / \d+\.\d s/.test(svgText),
    "litres, pH, mS/cm, L/min, bar, quality and age on the diagram");
  const labels = hot.map(x => x[1]).join(" | ");
  check("UI-02 requested vs observed", /requested .*observed/.test(labels) && /no position feedback/.test(labels),
    "pump labels give requested and observed; valves without a limit switch say so");
  check("UI-02 no installed-probe or mixer claim", svgText.includes("probe point proposed; not installed or verified") &&
    svgText.includes("No agitator or air pump") && !/(agitator|mixer)\s+(on|running|installed)/i.test(svgText), "probe and mixing notes present");
  await p.locator('#v-overview svg.diagram g.hot[aria-label^="Sector S2"]').click();
  check("UI-02 click-through", await until(p, () => p.evaluate(() => location.hash === "#sector-S2")), "sector 2 box opens #sector-S2");
  await show(p, "overview");
  await p.locator('#v-overview svg.diagram g.hot[aria-label^="Main tank"]').focus();
  await p.keyboard.press("Enter");
  check("UI-02 keyboard", await until(p, () => p.evaluate(() => location.hash === "#main-tank")), "Enter on the main tank opens #main-tank");

  // UI-03 step 1: edit a recipe (sector 1 duration 300 -> 240 s), save, validate, activate
  await show(p, "recipes");
  await p.locator("button", { hasText: "Edit as next version" }).first().click();
  const irr = p.locator("details", { hasText: "Irrigation" }).first();
  await irr.waitFor();
  await irr.locator("summary").click();
  const dur = irr.locator("table").nth(1).locator("input[type=number]").first();
  await dur.fill("240");
  await dur.blur();
  await p.locator("button", { hasText: "Save draft" }).click();
  await until(p, async () => (await p.locator("tr", { hasText: "demo-sim@v2" }).count()) > 0);
  await p.locator("tr", { hasText: "demo-sim@v2" }).locator("button", { hasText: "Validate" }).click();
  await until(p, async () => (await get(p, "/api/recipes")).recipes.some(r => r.ref === "demo-sim@v2" && r.status === "VALIDATED"));
  await p.locator("tr", { hasText: "demo-sim@v2" }).locator("button", { hasText: "Activate" }).click();
  const R = await until(p, async () => { const r = await get(p, "/api/recipes"); return r.active === "demo-sim@v2" && r; });
  await shot(p, "walk-1-recipe");
  const act = R && R.recipes.find(r => r.ref === R.active);
  check("UI-03 recipe edited and activated", act && act.body.irrigation.sequence[0].duration_s === 240,
    `active ${R && R.active}, sector 1 ${act && act.body.irrigation.sequence[0].duration_s} s`);

  // step 2: concurrent processes (stock acknowledgements, then four processes in AUTO at 10x)
  await show(p, "nutrients");
  for (const t of ["Stock A", "Stock B", "Acid"]) {
    await p.locator("tr", { hasText: t }).locator("button", { hasText: "Acknowledge prepared" }).click();
    await p.waitForTimeout(500);
  }
  await show(p, "overview");
  for (const t of ["Irrigation", "Drainage return", "Main-tank refill", "Acid pH dosing"]) {
    await p.locator("#v-overview tr", { hasText: t }).locator("button", { hasText: "AUTO" }).click();
    await p.waitForTimeout(500);
  }
  await api(p, "/api/twin/control", "POST", { action: "speed", speed: 10 });
  const P2 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.phase === "IRRIGATE" && x; });
  await show(p, "overview");
  await shot(p, "walk-2-concurrent");
  const st = await get(p, "/api/processes");
  const auto = ["IRRIGATION", "RETURN", "REFILL", "ACID"].filter(k => st.processes[k].state === "AUTO");
  check("UI-03 concurrent processes", P2 && auto.length === 4 && Object.values(st.stocks).every(s => s.status === "PREPARED") &&
    st.processes.IRRIGATION.recipe === "demo-sim@v2",
    `AUTO: ${auto.join(", ")}; irrigation ${st.processes.IRRIGATION.phase} on ${st.processes.IRRIGATION.recipe}; stocks prepared`);

  // step 3: inject a fault (drive trip while irrigating) from the twin page
  await show(p, "twin");
  const row = p.locator(".row").filter({ has: p.locator("span", { hasText: /^drive$/ }) }).first();
  const fault = row.locator("select").nth(1);
  await fault.focus();                       // as a user's click would; the page is not rebuilt under a focused field
  await fault.selectOption("TRIP");
  await row.locator("button", { hasText: "Apply" }).click();
  const applied = await until(p, async () => { const d = (await get(p, "/api/twin")).snapshot.plant.drive; return d.fault === "TRIP" || d.tripped; }, 5000);
  if (!applied) check("UI-03 drive trip applied from the twin page", false, `drive ${JSON.stringify((await get(p, "/api/twin")).snapshot.plant.drive)}`);
  const P3 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.state === "FAULT_LOCKOUT" && x; });
  await show(p, "main-pumps");
  await shot(p, "walk-3-fault");
  const pageTxt = await visibleText(p);
  const pageAck = await p.locator("section.view:not(.hidden) .card", { hasText: "Alarms for this page" }).locator("tr", { hasText: "process.IRRIGATION" })
    .locator("button", { hasText: "Ack" }).count();
  check("UI-01 actionable alarm on the process page", pageAck > 0 && /Reset lockout on the process/.test(pageTxt),
    `the Main pumps page lists the irrigation lockout with Ack and what to do next (${pageAck} Ack button)`);
  check("UI-03 fault injected", P3 && P3.IRRIGATION.fault_reason && ["RETURN", "REFILL", "ACID"].every(k => P3[k].state === "AUTO") &&
    pageTxt.includes("FAULT_LOCKOUT"),
    `irrigation ${P3 && P3.IRRIGATION.state} (${P3 && P3.IRRIGATION.fault_reason}); return, refill and acid stay AUTO`);

  // step 4: recover deliberately: clear the fault, reset refused before acknowledgement, ack, reset, AUTO
  await show(p, "twin");
  await row.locator("select").nth(1).selectOption("");
  await row.locator("button", { hasText: "Apply" }).click();
  await p.waitForTimeout(1500);
  const early = await api(p, "/api/processes/IRRIGATION/reset", "POST");
  check("UI-03 reset needs acknowledgement", early.status === 409, `reset before acknowledgement: HTTP ${early.status}`);
  await show(p, "alarms");
  const ackRows = p.locator("#v-alarms tr", { hasText: "process.IRRIGATION" }).locator("button", { hasText: "Ack" });
  for (let i = await ackRows.count(); i > 0; i--) { await ackRows.first().click(); await p.waitForTimeout(600); }
  await show(p, "main-pumps");
  await p.locator("#v-plant button", { hasText: "Reset lockout" }).click();
  const P4 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.state === "STOPPED" && x; });
  await p.waitForTimeout(1500);
  const stillStopped = (await procs(p)).IRRIGATION.state === "STOPPED";
  await p.locator("#v-plant .card", { hasText: "Irrigation" }).locator("button", { hasText: "AUTO" }).last().click();
  const P5 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.state === "AUTO" && x; });
  await shot(p, "walk-4-recovered");
  check("UI-03 deliberate recovery", P4 && stillStopped && P5, "acknowledge, reset (stays STOPPED), then AUTO on request");

  // step 5: export data from the trends page
  await show(p, "trends");
  const href = await p.locator("a", { hasText: "Export CSV" }).getAttribute("href");
  const rows = t => t.split("\n").filter(l => l && !l.startsWith("#")).length - 1;
  // the historian writes a snapshot on every 60 s wall-clock boundary; wait for the first one
  await until(p, async () => rows((await api(p, href)).body) > 0, 75000);
  const [dl] = await Promise.all([p.waitForEvent("download"), p.locator("a", { hasText: "Export CSV" }).click()]);
  const csv = fs.readFileSync(await dl.path(), "utf8").split("\n");
  fs.copyFileSync(await dl.path(), path.join(OUT, "walk-5-export.csv"));
  const n = rows(csv.join("\n"));
  check("UI-03 export", n > 0 && /# (COMPLETE|TRUNCATED)/.test(csv.slice(-3).join(" ")), `${n} data rows downloaded, completeness footer present`);

  // OP-03: ranges up to a year; a year range renders with dated axis labels (aggregation itself, which needs a
  // long history, is covered by tests/integration/test_api.py::test_op03_year_range_chart_is_aggregated_and_export_pages_are_complete)
  const range = p.locator("#v-trends label", { hasText: "Range" }).locator("select");
  await range.selectOption("525600");
  const dated = await until(p, async () => (await p.locator("#v-trends svg.chart text").allTextContents()).some(t => /\d{4}-\d{2}-\d{2}/.test(t)), 10000);
  await shot(p, "trends-365-days");
  check("OP-03 year range on the trends page", !!dated, "365 days selectable; the time axis shows dates");
  await p.locator("#v-trends label", { hasText: "Range" }).locator("select").selectOption("30");

  // UI-05: no colour-only semantics: every pill/badge carries text
  await show(p, "alarms");
  const pills = await p.$$eval(".pill, .badge, .q", xs => xs.map(x => x.textContent.trim()));
  check("UI-05 text on every status mark", pills.length > 0 && pills.every(t => t.length > 0), `${pills.length} marks, all with text`);

  // UI-05: a broken network shows stale data and a command clicked while offline is never replayed
  await show(p, "main-pumps");
  await ctx.setOffline(true);
  const staleShown = await until(p, () => p.locator("#stale").evaluate(e => !e.classList.contains("hidden")), 10000);
  await p.locator("#v-plant .card", { hasText: "Irrigation" }).locator("button", { hasText: "STOP" }).last().click({ force: true }).catch(() => {});
  await p.waitForTimeout(1000);
  await shot(p, "net-1-offline", false);
  await ctx.setOffline(false);
  const fresh = await until(p, () => p.locator("#stale").evaluate(e => e.classList.contains("hidden")), 10000);
  await p.waitForTimeout(3000);
  const after = await procs(p);
  check("UI-05 offline", staleShown && fresh && after.IRRIGATION.state === "AUTO",
    `stale banner while offline ${!!staleShown}, cleared on reconnect ${!!fresh}; irrigation still ${after.IRRIGATION.state} (the offline STOP was not replayed)`);
  await shot(p, "net-2-reconnected", false);

  // UI-04 in the browser: a viewer sees the pages, the controls are disabled, and a forged request is refused
  const vpw = "viewer-" + Math.random().toString(36).slice(2) + "-pass";
  const mk = await api(p, "/api/users", "POST", { username: "viewer1", password: vpw, role: "viewer" });
  const vctx = await b.newContext({ viewport: { width: 1366, height: 1000 } });
  const vp = await vctx.newPage();
  const verrs = watchErrors(vp);
  // a new account must change its password before anything else; the old session is revoked
  for (let i = 0; i < 40; i++) { try { await vp.goto(BASE + "/"); break; } catch (e) { await vp.waitForTimeout(250); } }
  await vp.fill("input[name=username]", "viewer1");
  await vp.fill("input[name=password]", vpw);
  await vp.keyboard.press("Enter");
  await vp.waitForSelector("#pwchange-view:not(.hidden)");
  const vpw2 = vpw + "-changed";
  await vp.fill("input[name=old]", vpw);
  await vp.fill("input[name=new]", vpw2);
  await vp.locator("#pw-form button[type=submit]").click();
  await vp.waitForSelector("#login-view:not(.hidden)");
  await login(vp, "viewer1", vpw2);
  await show(vp, "main-pumps");
  const enabled = await vp.$$eval("section.view:not(.hidden) button.cmd", bs => bs.filter(b => !b.disabled).map(b => b.textContent));
  const forged = await api(vp, "/api/processes/IRRIGATION/state", "POST", { target: "STOPPED" });
  const noCsrf = await vp.evaluate(async () => (await fetch("/api/processes/stop-all", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: "{}" })).status);
  await shot(vp, "role-viewer-main-pumps");
  check("UI-04 viewer", mk.status === 200 && enabled.length === 0 && forged.status === 403 && noCsrf === 403 && (await procs(p)).IRRIGATION.state === "AUTO",
    `viewer created ${mk.status}, changed its password and signed in again; enabled command buttons ${JSON.stringify(enabled)}; forged state change ${forged.status}; no CSRF token ${noCsrf}`);
  await vctx.close();

  // UI-05: phone, tablet and PC viewports without horizontal overflow
  for (const [name, w, hh] of [["phone", 390, 844], ["tablet", 820, 1180], ["pc", 1366, 900]]) {
    const vc = await b.newContext({ viewport: { width: w, height: hh }, isMobile: name === "phone", hasTouch: name !== "pc" });
    const q = await vc.newPage();
    const qerrs = watchErrors(q);
    await login(q, USER, PASS);
    const res = [];
    for (const v of PLANT_PAGES) {
      await show(q, v);
      const [sw, iw] = await q.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      res.push(sw <= iw ? v : `${v} overflows ${sw}px`);
      if (v === "overview" || v === "nutrients") await shot(q, `viewport-${name}-${v}`, false);
    }
    check(`UI-05 viewport ${name} ${w}px`, res.every(r => !r.includes("overflows")) && qerrs.length === 0, res.join(", "));
    await vc.close();
  }
  check("UI page errors", errs.length === 0 && verrs.length === 0, errs.concat(verrs).join("; ") || "none");
  await b.close();
}

// The same pages on HARDWARE_READ_ONLY (fake bus): observation only, controls replaced by the reason.
async function hardware() {
  const b = await launch();
  const p = await b.newPage({ viewport: { width: 1366, height: 1000 } });
  const errs = watchErrors(p);
  await login(p, USER, PASS);
  await until(p, () => p.evaluate(() => !document.getElementById("nav-inst").classList.contains("hidden")), 10000);
  const nav = (await p.locator("#nav").innerText()).replace(/\s+/g, " ");
  check("HW nav", ["Nutrients & acid", "Main tank", "Main pumps", "Sector 1", "Sector 2", "Drainage"].every(t => nav.includes(t)) &&
    !nav.includes("Recipes") && !nav.includes("Digital twin"), nav);
  await show(p, "overview");
  await shot(p, "hw-overview");
  const ov = await p.evaluate(() => ({ hot: document.querySelectorAll("#v-overview svg.diagram g.hot").length, label: document.querySelector("#v-overview svg.diagram").getAttribute("aria-label") }));
  check("HW overview diagram", ov.hot >= 25 && /read-only/.test(ov.label), `${ov.hot} parts that open read-only pages; ${ov.label}`);
  await p.locator('#v-overview svg.diagram g.hot[aria-label^="Main tank"]').click();
  check("HW click-through", await until(p, () => p.evaluate(() => location.hash === "#main-tank")), "main tank opens #main-tank");
  for (const v of ["nutrients", "main-tank", "main-pumps", "sector-S1", "sector-S2", "drainage"]) {
    await show(p, v);
    const t = await visibleText(p);
    const ctl = await p.$$eval("section.view:not(.hidden) button", bs => bs.map(x => x.textContent).filter(x => /^(AUTO|MANUAL|STOP|Acknowledge prepared|Invalidate|Twin: prepare|Run one step|Reset lockout)$/.test(x)));
    await shot(p, "hw-page-" + v);
    check("HW page " + v, t.includes("NOT AVAILABLE") && t.includes("not measured") && ctl.length === 0 && t.includes("REAL"),
      `read-only: reason shown, unmapped values "not measured", no control buttons ${JSON.stringify(ctl)}`);
  }
  await show(p, "overview");
  const drvTxt = await until(p, async () => { const t = await visibleText(p); return /Drive interface/.test(t) && /PROPOSED/.test(t) && /0x[0-9A-F]{4}/.test(t) && t; });
  check("HW drive interface", !!drvTxt, "the overview lists drive:MAIN over Modbus with its PROPOSED map and the status word it reads");
  await show(p, "io");
  const ioDrv = await p.locator("#v-io tr", { hasText: "drive:MAIN" }).first();
  const runBtns = await ioDrv.locator("button").count();
  check("HW drive read-only without a lease", (await ioDrv.count()) > 0 && runBtns === 0, `drive row on the I/O page, ${runBtns} Run/Stop buttons without a lease`);
  check("HW page errors", errs.length === 0, errs.join("; ") || "none");
  await b.close();
}

async function afterRestart() {
  const b = await launch();
  const p = await b.newPage({ viewport: { width: 1366, height: 1000 } });
  const errs = watchErrors(p);
  await login(p, USER, PASS);
  await show(p, "overview");
  const st = await get(p, "/api/processes");
  await shot(p, "walk-6-restarted");
  check("UI-03 restart", Object.values(st.processes).every(x => x.state === "STOPPED") && st.recipe.active === "demo-sim@v2" &&
    Object.values(st.stocks).every(s => s.status === "PREPARED"),
    `after restart: every process STOPPED, recipe ${st.recipe.active}, stocks ${Object.values(st.stocks).map(s => s.status).join("/")}`);
  check("UI page errors after restart", errs.length === 0, errs.join("; ") || "none");
  await b.close();
}

(PHASE === "after" ? afterRestart() : PHASE === "hardware" ? hardware() : main())
  .catch(e => check("UI check ran to the end", false, e.stack || e))
  .finally(async () => {
    fs.writeFileSync(path.join(OUT, `checks-${PHASE}.json`), JSON.stringify(checks, null, 1));
    for (const b of browsers) await b.close().catch(() => {});
    process.exit(checks.length && checks.every(c => c.ok) ? 0 : 1);
  });
