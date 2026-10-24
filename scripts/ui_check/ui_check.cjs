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
const SHARED_PAGES = ["twin", "io", "history", "alarms", "settings"];
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

// Tab through the page until the focused element is a button or link named `name`; true when found.
async function tabTo(p, name, max = 300) {
  for (let i = 0; i < max; i++) {
    await p.keyboard.press("Tab");
    const t = await p.evaluate(() => { const e = document.activeElement; return e ? (e.getAttribute("aria-label") || e.textContent || "").trim() : ""; });
    if (t === name) return true;
  }
  return false;
}
const RAW_LABELS = /^(AUTO|MANUAL|STOP|Ack|Reset|Reset lockout|Acknowledge prepared|Validate|Activate|Roll back.*|Export CSV|Twin: prepare.*)$/;

async function main() {
  if (process.env.AQ_ONLY === "0.3.4") { const b0 = await launch(); return o34(b0); }   // only the 0.3.4 checks (for working on them)
  if (process.env.AQ_ONLY === "0.4.0") { const b0 = await launch(); return board040(b0); }
  const b = await launch();
  const ctx = await b.newContext({ viewport: { width: 1366, height: 1000 }, acceptDownloads: true });
  const p = await ctx.newPage();
  const errs = watchErrors(p);
  await login(p, USER, PASS);

  // UI-01: every installation page and the shared pages render with content and no page error
  // the menu is completed once the installation's pages are known
  const nav = await until(p, async () => { const t = (await p.locator("#nav").innerText()).replace(/\s+/g, " "); return /Watering/.test(t) && !/\bControl\b/.test(t) && t; }, 5000)
    || (await p.locator("#nav").innerText()).replace(/\s+/g, " ");
  check("UI-01 nav", ["Getting started", "Watering", "Sector 1", "Sector 2", "Main tank", "Nutrients & acid", "Drainage return", "Recipes",
    "Digital Twin", "History", "Alarms & events"].every(t => nav.includes(t)) && !/\bControl\b|Trends/.test(nav), nav);
  for (const v of PLANT_PAGES.concat(SHARED_PAGES)) {
    await show(p, v);
    const txt = await visibleText(p);
    const stale = await p.locator("#stale").evaluate(e => !e.classList.contains("hidden"));
    await shot(p, "page-" + v);
    check("UI-01 page " + v, txt.length > 80 && !txt.includes("This page does not exist in this profile") && !txt.includes("could not be shown") && !stale,
      `${txt.length} chars, stale banner ${stale}`);
    if (v === "io") {
      const heads = await p.$$eval("#v-io .card h3", hs => hs.map(x => x.parentElement.textContent));
      const sink = heads.filter(t => /TRANSISTOR SINK — not relay contacts, max 500 mA/.test(t)).map(t => t.split(" ")[0]);
      const relay = heads.filter(t => /relay contacts/.test(t) && !/TRANSISTOR/.test(t)).map(t => t.split(" ")[0]);
      const pts = (await get(p, "/api/points")).devices;
      check("UI-01 six modules, 80 points", pts.length === 6 && pts.reduce((n, g) => n + g.points.length, 0) === 80 &&
        sink.join() === "digital_A,digital_B" && relay.join() === "relay_A,relay_B",
        `transistor sinks ${sink.join(", ")}; relay contacts ${relay.join(", ")}`);
    }
  }
  // F-03: no raw engineering label on a button anywhere; the status line in plain words, details on request
  const raw = [];
  for (const v of PLANT_PAGES.concat(SHARED_PAGES, ["start"])) {
    await show(p, v);
    raw.push(...(await p.$$eval("section.view:not(.hidden) button", (bs, re) => bs.map(x => x.textContent.trim()).filter(t => new RegExp(re).test(t)), RAW_LABELS.source)).map(t => `${v}: ${t}`));
  }
  check("F-03 every action named in plain words", raw.length === 0, raw.join(", ") || "no AUTO, MANUAL, STOP, Ack, Reset lockout, Validate, Activate or Export CSV button");
  const plain = await p.locator("#status-plain").innerText();
  const techHidden = await p.locator("#status-tech").evaluate(e => e.classList.contains("hidden"));
  await p.locator("#status-more").click();
  const tech = await p.locator("#status-tech").innerText();
  await p.locator("#status-more").click();
  check("F-03 plain status line", /^Digital Twin · Watering stopped · Main tank \d+% · Last reading \d+ seconds? ago/.test(plain) && techHidden &&
    /computer model of the greenhouse/.test(tech) && /Profile SIMULATED · config installation-80@/.test(tech),
    `"${plain}"; Details shows the Digital Twin explanation and "${tech.split("\n").pop().slice(0, 90)}…"`);
  await show(p, "start");
  const startTxt = await visibleText(p);
  const imgs = await p.$$eval("#v-start img", xs => xs.map(x => [x.naturalWidth, x.alt]));
  check("F-03 Getting started", ["1. Read the tank level", "2. Water one sector", "3. Stop watering", "4. Recover from a simulated problem"].every(t => startTxt.includes(t)) &&
    imgs.length === 4 && imgs.every(([w, alt]) => w > 200 && alt.length > 20) && /owner-tour/.test(startTxt),
    `four tasks with ${imgs.length} pictures (${imgs.map(x => x[0] + " px").join(", ")}) and alt text; the owner tour is named as separate`);

  // UI-01 / F-02: each process page: state, next step and its Stop first; diagram collapsed; trend; technical detail hidden
  for (const v of PLANT_PAGES.filter(x => !["overview", "recipes"].includes(x))) {
    await show(p, v);
    const parts = await p.evaluate(() => {
      const sec = document.querySelector("section.view:not(.hidden)");
      const card = sec.querySelector(".card.proc"), stop = card && card.querySelector("button.stop");
      const diag = [...sec.querySelectorAll("details")].find(d => /Greenhouse diagram/.test(d.querySelector("summary").textContent));
      return { focus: sec.querySelectorAll("svg.diagram g.hot.focus").length, dim: sec.querySelectorAll("svg.diagram g.hot.dim").length,
        chart: !!sec.querySelector("svg.chart-small") && sec.querySelector("svg.chart-small").childNodes.length > 0,
        first: card === sec.querySelector(".card:not(.stopbar)"), stop: stop ? stop.textContent : null, stopTop: stop ? Math.round(stop.getBoundingClientRect().top) : null,
        lead: card ? card.querySelector(".lead").textContent : "", diagClosed: diag ? !diag.open : null };
    });
    check("F-02 process page " + v, parts.first && parts.stop && /^Stop /.test(parts.stop) && parts.stopTop < 520 && parts.lead.length > 10 &&
      parts.diagClosed === true && parts.focus > 0 && parts.dim > 0 && parts.chart,
      `first card: "${parts.lead}" with "${parts.stop}" ${parts.stopTop} px from the top; diagram collapsed (${parts.focus} parts of this page highlighted); last hour drawn ${parts.chart}`);
  }
  await show(p, "main-pumps");
  const mpVisible = await visibleText(p);
  await p.locator("section.view:not(.hidden) details.tech > summary", { hasText: "Technical details" }).click();
  const mpTxt = await until(p, async () => { const t = await visibleText(p); return /Status word\s+0x[0-9A-F]{4}/.test(t) && t; });
  check("F-02 motor registers and selectors under Technical details", !/Status word|Selector K1/.test(mpVisible) && !!mpTxt &&
    /Drive interface\s+MODBUS_RTU\s+DEMO/.test(mpTxt) && /Control register/.test(mpTxt) && /Selector K1/.test(mpTxt),
    "hidden until Technical details is opened; then the simulated drive's Modbus interface (DEMO map, status word, control register) and the K1/K2 selectors");
  await show(p, "nutrients");
  await p.locator(".tabs button", { hasText: "Acid tank" }).click();
  await until(p, async () => /Acidity \(pH\) target/.test(await visibleText(p)));
  const acidTab = await visibleText(p);
  await p.locator(".tabs button", { hasText: "Fertiliser tank B" }).click();
  await until(p, async () => /Nutrient strength \(EC\) target/.test(await visibleText(p)));
  const bTab = await visibleText(p);
  await shot(p, "page-nutrients-tab-B");
  check("UI-01 nutrients tabs", acidTab.includes("Acid dosing") && /Acidity \(pH\) target\s*6/.test(acidTab) && bTab.includes("Fertiliser A for each litre of B") &&
    bTab.includes("Water fill of fertiliser tank B"), "Acid tank tab: pH target and acid dosing; Fertiliser tank B tab: EC target, A:B ratio and the tank B water fill");
  await p.locator(".tabs button", { hasText: "Fertiliser tank A" }).click();

  // UI-02: the diagram is clickable (mouse and keyboard), labelled, with units and age
  await show(p, "overview");
  const hot = await p.$$eval("#v-overview svg.diagram g.hot", gs => gs.map(g => [g.getAttribute("role"), g.getAttribute("aria-label"), g.getAttribute("tabindex")]));
  check("UI-02 diagram parts", hot.length >= 40 && hot.every(([r, l, t]) => r === "button" && l && t === "0"), `${hot.length} parts (equipment, tanks, sensors, sectors) that can be selected, all buttons with labels`);
  const svgText = await p.$eval("#v-overview svg.diagram", s => s.textContent);
  check("UI-02 units", ["L", "pH", "mS/cm", "L/min", "bar"].every(u => svgText.includes(u)) && /\d+ seconds? ago/.test(svgText),
    "litres, pH, mS/cm, L/min, bar and the age of the readings on the diagram");
  await p.locator('#v-overview svg.diagram g.hot[data-key="pump.TP_A"]').click();
  const pumpPanel = await until(p, async () => { const t = await p.locator("#v-overview aside.side-panel").innerText(); return /Transfer pump/.test(t) && t; }, 5000);
  await p.locator('#v-overview svg.diagram g.hot[data-key="valve.OUT_A"]').click();
  const valvePanel = await until(p, async () => { const t = await p.locator("#v-overview aside.side-panel").innerText(); return /Outlet valve/.test(t) && t; }, 5000);
  check("UI-02 requested vs observed", /Asked for\s+stopped/.test(pumpPanel || "") && /Reported\s+stopped, from its running signal/.test(pumpPanel || "") &&
    /no position signal/.test(valvePanel || ""), "a pump's panel gives what was asked and what it reports; a valve without a position signal says so");
  check("UI-02 no installed-probe or mixer claim", svgText.includes("probe position proposed, not installed") &&
    svgText.includes("No stirrer or air pump") && !/(agitator|mixer|stirrer)\s+(on|running|installed)/i.test(svgText), "probe and mixing notes present");
  await p.locator('#v-overview svg.diagram g.hot[data-key="sector.S2"]').click();
  await p.locator("#v-overview aside.side-panel a", { hasText: "Open the Sector 2 page" }).click();
  check("UI-02 click-through", await until(p, () => p.evaluate(() => location.hash === "#sector-S2")), "the Sector 2 box shows Sector 2 beside the diagram; its link opens #sector-S2");
  await show(p, "overview");
  await p.locator('#v-overview svg.diagram g.hot[data-key="tank.MAIN"]').focus();
  await p.keyboard.press("Enter");
  const kb = await until(p, async () => { const t = await p.locator("#v-overview aside.side-panel").innerText(); return /Main tank/.test(t) && /Measured at/.test(t) && t; }, 8000);
  await p.locator("#v-overview aside.side-panel a", { hasText: "Open Main tank" }).focus();
  await p.keyboard.press("Enter");
  check("UI-02 keyboard", !!kb && await until(p, () => p.evaluate(() => location.hash === "#main-tank")), "Enter on the main tank shows its level reading beside the diagram; Enter on its link opens #main-tank");

  // F-04: the recipe editor names every field, shows only the chosen ways' fields, and keeps typed input on errors
  await show(p, "recipes");
  await p.locator("section.view:not(.hidden) tr", { hasText: "demo-sim@v1" }).getByRole("button", { name: "Change settings" }).click();
  await p.waitForSelector(".editor");
  const named = await p.$$eval(".editor input, .editor select", xs => xs.map(x => {
    const l = x.id && document.querySelector(`label[for="${x.id}"]`);
    return l ? l.textContent.trim() : x.getAttribute("aria-label") || ("UNNAMED " + (x.id || x.name));
  }));
  check("F-04 every recipe field has a name", named.length > 20 && named.every(n => !n.startsWith("UNNAMED")) &&
    named.includes("Refill starts below") && named.includes("Add nutrients until the EC target is reached") && named.includes("Fill to a water level"),
    `${named.length} fields, all named, for example "${named.slice(0, 3).join('", "')}"`);
  const present = async path => p.locator(`.editor [data-path="${path}"]`).count();
  const ecBefore = await present("refill.ec.ec_target_mS_cm"), fxBefore = await present("refill.fixed.a_L");
  await p.getByLabel("Add set amounts of nutrients").check();
  await until(p, async () => (await present("refill.fixed.a_L")) > 0, 5000);
  const ecFixed = await present("refill.ec.ec_target_mS_cm"), fxFixed = await present("refill.fixed.a_L");
  await p.getByLabel("Add nutrients until the EC target is reached").check();
  await until(p, async () => (await present("refill.ec.ec_target_mS_cm")) > 0, 5000);
  const ecBack = await p.inputValue("#rf-refill-ec-ec_target_mS_cm");
  check("F-04 only the chosen way's fields are shown", ecBefore === 1 && fxBefore === 0 && ecFixed === 0 && fxFixed === 1 && ecBack === "1.8",
    `EC target mode: EC target shown, fixed amounts hidden; set amounts: the reverse; back to EC target: the EC target ${ecBack} is back`);
  const advClosed = await p.$$eval(".editor details.advanced", ds => ds.length > 0 && ds.every(d => !d.open));
  await p.fill("#rf-main-refill_trigger_L", "99999");
  await p.locator(".editor").getByRole("button", { name: "Check settings" }).click();
  const err = await until(p, async () => (await p.locator('.editor [data-path="main.refill_trigger_L"] .error').count()) && p.locator('.editor [data-path="main.refill_trigger_L"] .error').textContent(), 10000);
  const kept = await p.inputValue("#rf-main-refill_trigger_L");
  await shot(p, "recipe-error-beside-field");
  check("F-04 a problem shows beside its field and the typed value stays", /Refill starts below: at most 20000 L/.test(err || "") && kept === "99999" && advClosed,
    `"${err}" beside the field, the field still reads ${kept}; timing and tolerances under Advanced settings (closed)`);
  await p.fill("#rf-main-refill_trigger_L", "8000");

  // UI-03 step 1: edit a recipe (sector 1 duration 300 -> 240 s), check it, use it
  const irr = p.locator(".editor details.section", { has: p.locator("summary", { hasText: /^Watering$/ }) });
  if (!(await irr.evaluate(d => d.open))) await irr.locator("summary").first().click();
  await p.fill("#seq-0-time", "240");
  await p.locator(".editor").getByRole("button", { name: "Check settings" }).click();
  await until(p, async () => (await get(p, "/api/recipes")).recipes.some(r => r.ref.startsWith("demo-sim@") && r.ref !== "demo-sim@v1" && r.status === "VALIDATED"), 15000);
  const v2 = (await get(p, "/api/recipes")).recipes.find(r => r.ref.startsWith("demo-sim@") && r.status === "VALIDATED").ref;
  const row2 = p.locator("section.view:not(.hidden) tr", { hasText: v2 });
  await row2.locator("summary", { hasText: "Use these settings" }).click();
  await row2.locator("input[type=text]").fill("walkthrough: shorter sector 1");
  await row2.getByRole("button", { name: "Use these settings" }).click();
  const R = await until(p, async () => { const r = await get(p, "/api/recipes"); return r.active === v2 && r; });
  await shot(p, "walk-1-recipe");
  const act = R && R.recipes.find(r => r.ref === R.active);
  check("UI-03 recipe edited and activated", act && act.body.irrigation.sequence[0].duration_s === 240,
    `in use ${R && R.active}, sector 1 ${act && act.body.irrigation.sequence[0].duration_s} s`);

  // F-08 / step 2: tanks confirmed prepared by a person; the simulated fertiliser is a Digital Twin action
  await show(p, "twin");
  await p.getByRole("button", { name: "Add simulated fertiliser to tank A" }).click();
  await p.waitForTimeout(800);
  await show(p, "nutrients");
  const waiting = await visibleText(p);
  for (const t of ["tank A", "tank B", "the acid tank"]) {
    const row = p.locator("section.view:not(.hidden) tr", { has: p.locator("summary", { hasText: `Confirm ${t} is prepared` }) });
    await row.locator("summary", { hasText: `Confirm ${t} is prepared` }).click();
    await row.locator("input[type=text]").fill("ui check: prepared by hand (simulation)");
    await row.getByRole("button", { name: "Confirm prepared" }).click();
    await p.waitForTimeout(700);
  }
  const prepared = await until(p, async () => Object.values((await get(p, "/api/processes")).stocks).every(s => s.status === "PREPARED") && visibleText(p), 8000);
  check("F-08 tank preparation in plain words", /Waiting for confirmation/.test(waiting) && /Simulated fertiliser was added in the Digital Twin\. Confirm tank A is prepared/.test(waiting) &&
    !!prepared && /Mark tank A not ready/.test(prepared),
    "the twin's simulated fertiliser leaves tank A waiting for a person's confirmation; Confirm tank A is prepared, then Mark tank A not ready is offered");

  // step 3: four processes in automatic, each from its own page
  for (const [page, name] of [["main-pumps", "Start automatic watering"], ["drainage", "Switch on automatic return"], ["main-tank", "Switch on automatic refill"]]) {
    await show(p, page);
    await p.locator("section.view:not(.hidden) .card.proc").first().getByRole("button", { name }).click();
    await p.waitForTimeout(500);
  }
  await show(p, "nutrients");
  await p.locator(".tabs button", { hasText: "Acid tank" }).click();
  await until(p, async () => /Switch on automatic acid dosing/.test(await visibleText(p)));
  await p.getByRole("button", { name: "Switch on automatic acid dosing" }).click();
  await api(p, "/api/twin/control", "POST", { action: "speed", speed: 10 });
  const P2 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.phase === "IRRIGATE" && x; });
  await show(p, "overview");
  await shot(p, "walk-2-concurrent");
  const st = await get(p, "/api/processes");
  const auto = ["IRRIGATION", "RETURN", "REFILL", "ACID"].filter(k => st.processes[k].state === "AUTO");
  check("UI-03 concurrent processes", P2 && auto.length === 4 && Object.values(st.stocks).every(s => s.status === "PREPARED") &&
    st.processes.IRRIGATION.recipe === v2,
    `automatic: ${auto.join(", ")}; watering ${st.processes.IRRIGATION.phase} on ${st.processes.IRRIGATION.recipe}; tanks prepared`);

  // step 4: a simulated drive trip while watering, from the Digital Twin page
  await show(p, "twin");
  const drow = p.locator(".row", { has: p.locator("#fault-drive-what") });
  await p.locator("#fault-drive-fault").focus();
  await p.selectOption("#fault-drive-fault", "TRIP");
  await drow.getByRole("button", { name: "Apply" }).click();
  const applied = await until(p, async () => { const d = (await get(p, "/api/twin")).snapshot.plant.drive; return d.fault === "TRIP" || d.tripped; }, 5000);
  if (!applied) check("UI-03 drive trip applied from the twin page", false, `drive ${JSON.stringify((await get(p, "/api/twin")).snapshot.plant.drive)}`);
  const P3 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.state === "FAULT_LOCKOUT" && x; });
  await show(p, "main-pumps");
  await shot(p, "walk-3-fault");
  const pageTxt = await visibleText(p);
  check("F-05 the problem in plain words with its next steps", /Watering stopped because of a problem: the pump drive reports a fault/.test(pageTxt) &&
    /What to do/.test(pageTxt) && /Mark as seen/.test(pageTxt) && /Clear stop after fixing the problem/.test(pageTxt) && /Not available yet: (the problem is still there|the pump drive still reports a fault)/.test(pageTxt) && /The program still sees the problem/.test(pageTxt) && !/The program no longer sees the problem/.test(pageTxt),
    "Watering: \"Watering stopped because of a problem: the pump drive reports a fault\", then Mark as seen, Fix the problem (still seen by the program), Clear stop (not available yet: the problem is still there)");
  check("UI-03 fault injected", P3 && P3.IRRIGATION.fault_reason && ["RETURN", "REFILL", "ACID"].every(k => P3[k].state === "AUTO") && pageTxt.includes("Stopped because of a problem"),
    `watering ${P3 && P3.IRRIGATION.state} (${P3 && P3.IRRIGATION.fault_reason}); return, refill and acid stay automatic`);

  // step 5 / F-05: recover from the Alarms page: mark as seen, fix, go to the process, clear the stop, start again
  await show(p, "twin");
  await p.selectOption("#fault-drive-fault", "");
  await drow.getByRole("button", { name: "Apply" }).click();
  await p.waitForTimeout(1500);
  const early = await api(p, "/api/processes/IRRIGATION/reset", "POST");
  check("UI-03 reset needs acknowledgement", early.status === 409, `clearing the stop before marking the problem as seen: HTTP ${early.status}`);
  await show(p, "alarms");
  const card = p.locator("#v-alarms .problem", { hasText: "Watering stopped" }).first();
  const head = await card.locator("h3").textContent();
  await card.getByRole("button", { name: "Mark as seen" }).click();
  await p.waitForTimeout(1200);
  await shot(p, "alarms-problem-card");
  await p.locator("#v-alarms .problem", { hasText: "Watering stopped" }).first().getByRole("link", { name: "Go to Watering" }).first().click();
  const onPage = await until(p, () => p.evaluate(() => location.hash === "#main-pumps"));
  await show(p, "main-pumps");
  const P4w = await until(p, async () => !(await p.getByRole("button", { name: "Clear stop after fixing the problem" }).first().isDisabled()), 15000);
  await p.getByRole("button", { name: "Clear stop after fixing the problem" }).first().click();
  const P4 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.state === "STOPPED" && x; });
  await p.waitForTimeout(1500);
  const stillStopped = (await procs(p)).IRRIGATION.state === "STOPPED";
  await p.locator("section.view:not(.hidden) .card.proc").first().getByRole("button", { name: "Start automatic watering" }).click();
  const P5 = await until(p, async () => { const x = await procs(p); return x.IRRIGATION.state === "AUTO" && x; });
  await shot(p, "walk-4-recovered");
  check("F-05 from the problem to its process and back to work", head === "Watering stopped: the pump drive reports a fault" && onPage && P4w && P4 && stillStopped && P5,
    `"${head}"; Mark as seen on the Alarms page, Go to Watering, Clear stop after fixing the problem (stays stopped), Start automatic watering`);

  // F-07 / step 6: History: named, grouped readings; Today/Yesterday/Last 7 days; Download readings
  await show(p, "history");
  const groups = await p.$$eval("#v-history .picker fieldset legend", ls => ls.map(l => l.textContent));
  const mainBox = await p.locator('#v-history .picker label', { hasText: "Main tank water level" }).first().textContent();
  const rangeNames = await p.$$eval("#v-history [aria-label='Time range'] button", bs => bs.map(x => x.textContent));
  const unusedClosed = await p.$$eval("#v-history .picker details", ds => ds.length === 1 && !ds[0].open && /Unused channels \(\d+\)/.test(ds[0].querySelector("summary").textContent));
  check("F-07 History: grouped readings with plain names", groups[0] === "Tank levels" && groups.includes("Water flow and pressure") && /Main tank water level analog_A\.AI1/.test(mainBox) &&
    ["Today", "Yesterday", "Last 7 days"].every(x => rangeNames.includes(x)) && unusedClosed,
    `groups ${groups.join(", ")}; "${mainBox}" (the channel as a secondary label); ranges ${rangeNames.join(", ")}; unused channels folded away`);
  const href = await p.locator("#v-history a", { hasText: "Download readings" }).getAttribute("href");
  const rows = t => t.split("\n").filter(l => l && !l.startsWith("#")).length - 1;
  // the historian writes a snapshot on every 60 s wall-clock boundary; wait for the first one
  await until(p, async () => rows((await api(p, href)).body) > 0, 75000);
  const [dl] = await Promise.all([p.waitForEvent("download"), p.locator("#v-history a", { hasText: "Download readings" }).click()]);
  const csv = fs.readFileSync(await dl.path(), "utf8").split("\n");
  fs.copyFileSync(await dl.path(), path.join(OUT, "walk-5-export.csv"));
  const n = rows(csv.join("\n"));
  check("UI-03 export", n > 0 && /# (COMPLETE|TRUNCATED)/.test(csv.slice(-3).join(" ")), `${n} data rows downloaded with Download readings, completeness footer present`);
  await p.getByRole("button", { name: "Today" }).click();
  const today = await until(p, async () => /From \d{4}-\d{2}-\d{2} 00:00:00 to/.test(await visibleText(p)), 8000);
  await p.getByRole("button", { name: "Last 365 days" }).click();
  const dated = await until(p, async () => (await p.locator("#v-history svg.chart text").allTextContents()).some(t => /\d{4}-\d{2}-\d{2}/.test(t)), 10000);
  await shot(p, "history-365-days");
  check("OP-03 Today and a year range on the History page", !!today && !!dated, "Today starts at the site's midnight; 365 days draws with dated axis labels");
  await p.getByRole("button", { name: "Last hour" }).click();

  // UI-05: no colour-only semantics: every pill/badge carries text
  await show(p, "alarms");
  const pills = await p.$$eval(".pill, .badge, .q", xs => xs.map(x => x.textContent.trim()));
  check("UI-05 text on every status mark", pills.length > 0 && pills.every(t => t.length > 0), `${pills.length} marks, all with text`);

  // keyboard only: water one sector once, then stop it, without the mouse
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "STOPPED" });
  await until(p, async () => (await procs(p)).IRRIGATION.state === "STOPPED" && !(await procs(p)).IRRIGATION.stopping, 30000);
  await show(p, "main-pumps");
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  const k1 = await tabTo(p, "Manual mode") && (await p.keyboard.press("Enter"), true);
  await until(p, async () => (await procs(p)).IRRIGATION.state === "MANUAL", 8000);
  await show(p, "main-pumps");
  let k2 = false;
  for (let i = 0; i < 300 && !k2; i++) {           // to the watering time field of Run once, then type
    await p.keyboard.press("Tab");
    k2 = await p.evaluate(() => { const e = document.activeElement; const l = e && e.closest("label"); return !!(l && /Watering time/.test(l.textContent)); });
  }
  if (k2) await p.keyboard.type("30");
  const k3 = k2 && await tabTo(p, "Run once", 5) && (await p.keyboard.press("Enter"), true);
  const ran = await until(p, async () => (await procs(p)).IRRIGATION.manual_op, 10000);
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  const k4 = await tabTo(p, "Stop watering", 60) && (await p.keyboard.press("Enter"), true);
  const stopped = await until(p, async () => { const x = (await procs(p)).IRRIGATION; return x.state === "STOPPED" && !x.stopping; }, 30000);
  check("F-09 keyboard only: water one sector once and stop it", k1 && k2 && k3 && !!ran && k4 && !!stopped,
    `Tab and Enter: Manual mode ${k1}, watering time typed ${k2}, Run once ${k3} (ran ${!!ran}), Stop watering ${k4}, stopped ${!!stopped}`);

  // UI-05: a broken network shows stale data and a command clicked while offline is never replayed
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "AUTO" });
  await until(p, async () => (await procs(p)).IRRIGATION.state === "AUTO", 8000);
  await show(p, "main-pumps");
  await ctx.setOffline(true);
  const staleShown = await until(p, () => p.locator("#stale").evaluate(e => !e.classList.contains("hidden")), 10000);
  await p.locator("section.view:not(.hidden) .card.proc").first().getByRole("button", { name: "Stop watering" }).click({ force: true }).catch(() => {});
  await p.waitForTimeout(1000);
  await shot(p, "net-1-offline", false);
  await ctx.setOffline(false);
  const fresh = await until(p, () => p.locator("#stale").evaluate(e => e.classList.contains("hidden")), 10000);
  await p.waitForTimeout(3000);
  const after = await procs(p);
  check("UI-05 offline", staleShown && fresh && after.IRRIGATION.state === "AUTO",
    `stale banner while offline ${!!staleShown}, cleared on reconnect ${!!fresh}; watering still ${after.IRRIGATION.state} (the offline Stop was not replayed)`);
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
  const why = await vp.locator("section.view:not(.hidden)").innerText();
  const forged = await api(vp, "/api/processes/IRRIGATION/state", "POST", { target: "STOPPED" });
  const noCsrf = await vp.evaluate(async () => (await fetch("/api/processes/stop-all", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: "{}" })).status);
  await shot(vp, "role-viewer-main-pumps");
  check("UI-04 viewer", mk.status === 200 && enabled.length === 0 && /needs an operator or administrator account/.test(why) && forged.status === 403 && noCsrf === 403 &&
    (await procs(p)).IRRIGATION.state === "AUTO",
    `viewer created ${mk.status}, changed its password and signed in again; enabled command buttons ${JSON.stringify(enabled)} (the page says the account needed); forged state change ${forged.status}; no CSRF token ${noCsrf}`);
  await vctx.close();

  // UI-05 / F-09: phone, tablet and PC: no page-wide sideways scroll; on a phone a menu button, and each process page's Stop in the first screen
  for (const [name, w, hh] of [["phone", 390, 844], ["tablet", 820, 1180], ["pc", 1366, 900]]) {
    const vc = await b.newContext({ viewport: { width: w, height: hh }, isMobile: name === "phone", hasTouch: name !== "pc" });
    const q = await vc.newPage();
    const qerrs = watchErrors(q);
    await login(q, USER, PASS);
    const res = [], stops = [];
    for (const v of PLANT_PAGES.concat(SHARED_PAGES, ["start"])) {
      await show(q, v);
      const [sw, iw] = await q.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      res.push(sw <= iw ? v : `${v} overflows ${sw}px`);
      if (PLANT_PAGES.includes(v) && !["overview", "recipes"].includes(v)) {
        const top = await q.evaluate(() => { const b = [...document.querySelectorAll("section.view:not(.hidden) button.stop")].find(x => x.offsetParent); return b ? Math.round(b.getBoundingClientRect().top) : 9999; });
        stops.push(`${v} ${top}px`);
        if (top > hh * 0.6) res.push(`${v}: Stop ${top}px from the top`);
      }
      if (["overview", "main-pumps", "history", "alarms"].includes(v)) await shot(q, `viewport-${name}-${v}`, false);
    }
    let menuOk = true;
    if (name === "phone") {
      const before = await q.locator("#nav").isVisible();
      await q.getByRole("button", { name: "Menu" }).click();
      const open = await q.locator("#nav").isVisible();
      await q.locator("#nav a", { hasText: "Main tank" }).click();
      menuOk = !before && open && await until(q, () => q.evaluate(() => location.hash === "#main-tank")) && !(await q.locator("#nav").isVisible());
      await shot(q, "viewport-phone-menu-closed", false);
      res.push(menuOk ? "menu: one button, closes after choosing a page" : "MENU NOT AS EXPECTED");
    }
    check(`UI-05 viewport ${name} ${w}px`, res.every(r => !/overflows|Stop \d+px|NOT AS EXPECTED/.test(r)) && menuOk && qerrs.length === 0,
      res.filter(r => /overflows|Stop|menu|MENU/.test(r)).join(", ") + ` · no sideways page scroll on ${PLANT_PAGES.length + SHARED_PAGES.length + 1} pages · Stop at ${stops.join(", ")}`);
    await vc.close();
  }
  check("UI page errors", errs.length === 0 && verrs.length === 0, errs.concat(verrs).join("; ") || "none");
  await o34(b);
  await board040(b);
  await b.close();
}

// ------------------------------------------------------------------------------------------------ 0.3.4
// Owner feedback O-01 to O-05 and the reviewer's corrections R33-01 to R33-03 (simulation only).
const CONTRAST_ON_WHITE = c => {      // WCAG contrast of white text on colour c
  const m = /rgba?\((\d+), (\d+), (\d+)/.exec(c); if (!m) return 0;
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = m.slice(1).map(Number).map(lin);
  return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05);
};
const BLUE = c => { const m = /rgba?\((\d+), (\d+), (\d+)/.exec(c); if (!m) return false; const [r, g, bl] = m.slice(1).map(Number); return bl > r + 40 && bl > g + 20; };
async function textColours(q) {
  return q.evaluate(() => {
    const out = { blue: [], links: [], n: 0 };
    const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden"; };
    for (const e of document.querySelectorAll("body *")) {
      if (!vis(e) || e.closest("svg") || e.closest(".banner")) continue;
      const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      if (!own) continue;
      out.n++;
      const c = getComputedStyle(e).color, m = /rgba?\((\d+), (\d+), (\d+)/.exec(c);
      if (m) { const [r, g, b] = m.slice(1).map(Number); if (b > r + 40 && b > g + 20 && !e.closest("[aria-hidden=true]")) out.blue.push(e.tagName + ":" + e.textContent.trim().slice(0, 30) + " " + c); }
      if (e.matches("a:not(.button):not(nav a)")) out.links.push(c);
    }
    return out;
  });
}
const sentPosts = q => { const xs = []; q.on("request", r => { if (r.method() === "POST" && /\/api\/(processes|twin\/scenario)/.test(r.url())) xs.push(r.url().replace(/^https?:\/\/[^/]+/, "")); }); return xs; };
const panel = q => q.locator("section.view:not(.hidden) aside.side-panel");
async function pick(q, key) {
  await q.locator(`section.view:not(.hidden) svg.diagram g.hot[data-key="${key}"]`).first().click();
  await until(q, () => q.evaluate(k => { const a = document.querySelector("section.view:not(.hidden) aside.side-panel"); return a && a.dataset.selected === k; }, key), 5000);
}
const panelText = q => panel(q).innerText();
// a phone shows the diagram at a readable size, scrolled sideways inside its card: bring the part to show into view
async function phoneDiagram(q, across) {
  await q.evaluate(f => { const w = document.querySelector("#overview-diagram .diagram-wrap"); w.scrollIntoView({ block: "start" }); window.scrollBy(0, -40);
    w.scrollLeft = (w.scrollWidth - w.clientWidth) * f; }, across);
  await q.waitForTimeout(1200);
}
async function stopAll(q) {
  await api(q, "/api/processes/stop-all", "POST", { reason: "ui check 0.3.4" });
  await until(q, async () => Object.values(await procs(q)).every(x => x.state === "STOPPED" && !x.stopping), 60000);
}
async function o34(b) {
  const ctx0 = { p: null };
  try { await o34run(b, ctx0); } catch (e) { if (ctx0.p) { await shot(ctx0.p, "o34-error").catch(() => {}); console.log("PANEL", await ctx0.p.locator("aside.side-panel").first().innerText().catch(() => "")); } throw e; }
}
async function o34run(b, ctx0) {
  const ctx = await b.newContext({ viewport: { width: 1366, height: 1000 }, colorScheme: "dark" });
  const p = ctx0.p = await ctx.newPage();
  const errs = watchErrors(p), posts = sentPosts(p);
  await login(p, USER, PASS);
  await stopAll(p);
  await api(p, "/api/twin/control", "POST", { action: "reset" });
  await api(p, "/api/twin/control", "POST", { action: "speed", speed: 1 });
  await p.waitForTimeout(1500);

  // O-01: no blue text; every link white on dark green, also hovered and focused, in both colour schemes
  const col = [];
  for (const scheme of ["dark", "light"]) {
    await p.emulateMedia({ colorScheme: scheme });
    for (const v of ["overview", "nutrients", "twin", "history", "alarms", "main-pumps"]) {
      await show(p, v);
      const t = await textColours(p);
      col.push(`${scheme} ${v}: ${t.n} texts, ${t.blue.length} blue, links ${[...new Set(t.links)].join("/") || "none"}`);
      if (t.blue.length || t.links.some(c => c !== "rgb(255, 255, 255)")) col.push("BAD " + t.blue.slice(0, 3).join("; "));
    }
  }
  await p.emulateMedia({ colorScheme: "dark" });
  await show(p, "overview");
  const link = p.locator("section.view:not(.hidden) a", { hasText: "All alarms and events" }).first();
  await link.hover();
  const hov = await link.evaluate(a => [getComputedStyle(a).color, getComputedStyle(a).backgroundColor]);
  await link.focus(); await p.keyboard.press("Shift+Tab"); await p.keyboard.press("Tab");
  const foc = await link.evaluate(a => [getComputedStyle(a).color, getComputedStyle(a).outlineColor, getComputedStyle(a).outlineStyle]);
  // the Carbon look (owner, 2026-10-24) puts white link text on IBM Blue; O-01 asks for no blue *text* and a
  // readable link in every state: white on a ground dark enough for 4.5:1
  check("O-01 white text instead of blue", !col.some(x => x.startsWith("BAD")) && hov[0] === "rgb(255, 255, 255)" && CONTRAST_ON_WHITE(hov[1]) >= 4.5 && foc[0] === "rgb(255, 255, 255)" && foc[2] !== "none",
    col.join(" · ") + ` · hover ${hov.join(" on ")} · focus ${foc.join(", ")}`);

  // O-02 / O-04 in Manual mode: the Sector 1 valve from the diagram, through the controller
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "MANUAL" });
  await until(p, async () => (await procs(p)).IRRIGATION.state === "MANUAL", 8000);
  await show(p, "overview");
  await pick(p, "valve.S1");
  const t0 = await panelText(p);
  posts.length = 0;
  await panel(p).locator('button[data-act="equip"]').click();
  const rep = await until(p, async () => (await panel(p).locator('[data-pending="reported"]').count()) > 0, 40000);
  const pm = await procs(p), m1 = [...posts];
  await until(p, async () => (await p.locator('section.view:not(.hidden) svg.diagram path.pipe.flow').count()) >= 3, 15000);
  await p.waitForTimeout(6000);                           // the level's change needs a few readings
  const route = await p.evaluate(() => [...document.querySelectorAll("section.view:not(.hidden) svg.diagram path.pipe.flow")].map(x => x.getAttribute("d")));
  const trend = await p.evaluate(() => (document.querySelector('section.view:not(.hidden) svg.diagram [data-trend]') || {}).textContent || "");
  const pt1 = await panelText(p);
  await shot(p, "o02-watering-route-desktop-dark");
  const pc = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: "dark" });
  const q = await pc.newPage();
  await login(q, USER, PASS);
  await show(q, "overview");
  await phoneDiagram(q, 1);
  await shot(q, "o02-watering-route-phone-dark", false);
  check("O-04 Manual mode: open a valve from the diagram", /Opening this|In Manual mode/.test(t0) && rep && m1.length === 1 && m1[0] === "/api/processes/IRRIGATION/manual" &&
    pm.IRRIGATION.manual_op && pm.IRRIGATION.manual_op.op.sector === "S1" && /Reported: open \(its open signal\)/.test(pt1),
    `no confirmation in Manual mode; sent ${m1.join(", ")}; the controller runs ${JSON.stringify(pm.IRRIGATION.manual_op && pm.IRRIGATION.manual_op.op)}; panel: "${(pt1.match(/Reported:[^\n]*/) || [""])[0]}"`);
  check("O-02 the active watering route and a falling main tank", route.some(d => d.startsWith("M660 410 H700 V290")) && route.some(d => d.startsWith("M776 290 H830 V200")) &&
    route.some(d => d.startsWith("M893 200")) && /falling/.test(trend),
    `${route.length} pipes with measured flow: main tank to main pump 1 to the Sector 1 valve to Sector 1; main tank "${trend}"`);
  posts.length = 0;
  await panel(p).locator('button[data-act="equip"]').click();
  const closed = await until(p, async () => { const x = (await procs(p)).IRRIGATION; return x.state === "STOPPED" && !x.stopping && (await panel(p).locator('[data-pending="reported"]').count()) > 0; }, 40000);
  const m2 = [...posts], pt2 = await panelText(p);
  check("O-04 Manual mode: close it again", closed && m2.length === 1 && m2[0] === "/api/processes/IRRIGATION/state" && /Reported: closed/.test(pt2),
    `sent ${m2.join(", ")} (stop); watering now STOPPED; panel: "${(pt2.match(/Reported:[^\n]*/) || [""])[0]}"`);

  // O-04 in automatic mode: a confirmation that names the equipment and the effect; Cancel sends nothing
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "AUTO" });
  await until(p, async () => { const x = (await procs(p)).IRRIGATION; return x.phase === "IRRIGATE" && x.pump === "MP1"; }, 40000);
  await show(p, "overview");
  await pick(p, "pump.MP1");
  posts.length = 0;
  await panel(p).locator('button[data-act="equip"]').click();
  const cq = await until(p, async () => (await panel(p).locator('[role="alertdialog"]').count()) > 0 && panel(p).locator('[role="alertdialog"]').innerText(), 5000);
  await shot(p, "o04-confirm-auto-stop");
  await panel(p).locator('button[data-act="cancel"]').click();
  await p.waitForTimeout(2500);
  const afterCancel = (await procs(p)).IRRIGATION, cancelPosts = [...posts];
  check("O-04 Automatic mode: confirmation, and Cancel changes nothing", /Stop Main pump 1\?/.test(cq || "") && /Watering is in automatic mode/.test(cq || "") && /stays stopped until you start it again/.test(cq || "") &&
    cancelPosts.length === 0 && afterCancel.state === "AUTO" && afterCancel.phase === "IRRIGATE",
    `"${(cq || "").replace(/\s+/g, " ").slice(0, 200)}"; after Cancel nothing was sent and watering is still ${afterCancel.state} / ${afterCancel.phase}`);
  await panel(p).locator('button[data-act="equip"]').click();
  await panel(p).locator('button[data-act="confirm"]').click();
  const stoppedA = await until(p, async () => { const x = (await procs(p)).IRRIGATION; return x.state === "STOPPED" && !x.stopping && (await panel(p).locator('[data-pending="reported"]').count()) > 0; }, 40000);
  const m3 = [...posts], pt3 = await panelText(p);
  check("O-04 Automatic mode: confirmed stop of a pump", stoppedA && m3.length === 1 && m3[0] === "/api/processes/IRRIGATION/state" && /Reported: stopped/.test(pt3),
    `sent ${m3.join(", ")}; watering STOPPED; panel "${(pt3.match(/Reported:[^\n]*/) || [""])[0]}"`);
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "AUTO" });
  await until(p, async () => (await procs(p)).IRRIGATION.phase === "IRRIGATE", 40000);
  await show(p, "overview");
  await pick(p, "valve.S2");
  posts.length = 0;
  await panel(p).locator('button[data-act="equip"]').click();
  const cq2 = await until(p, async () => (await panel(p).locator('[role="alertdialog"]').count()) > 0 && panel(p).locator('[role="alertdialog"]').innerText(), 5000);
  await panel(p).locator('button[data-act="confirm"]').click();
  const opened2 = await until(p, async () => (await panel(p).locator('[data-pending="reported"]').count()) > 0, 90000);
  const pm4 = await procs(p), m4 = [...posts];
  check("O-04 Automatic mode: the switch to Manual mode is stated and done by the controller", /switches watering to Manual mode/.test(cq2 || "") && /Automatic operation of watering stays off/.test(cq2 || "") &&
    opened2 && JSON.stringify(m4) === JSON.stringify(["/api/processes/IRRIGATION/state", "/api/processes/IRRIGATION/state", "/api/processes/IRRIGATION/manual"]) &&
    pm4.IRRIGATION.state === "MANUAL" && pm4.IRRIGATION.manual_op && pm4.IRRIGATION.manual_op.op.sector === "S2",
    `confirmation "${(cq2 || "").replace(/\s+/g, " ").slice(0, 160)}…"; sent ${m4.join(", ")} (stop, Manual mode, run once); controller: ${pm4.IRRIGATION.state} running ${JSON.stringify(pm4.IRRIGATION.manual_op && pm4.IRRIGATION.manual_op.op)}`);
  await pick(p, "pump.MP1");
  const refuse = await panel(p).locator("[data-refusal]").innerText().catch(() => "");
  check("O-04 a request that cannot be done is explained", /already running once/.test(refuse), `main pump 1 while Sector 2 is watered once: "${refuse}"`);
  await stopAll(p);
  // a refusal by the controller itself, in plain words
  await show(p, "overview");
  await pick(p, "pump.RP1");
  await panel(p).locator('button[data-act="equip"]').click();
  await panel(p).locator('[role="alertdialog"]').waitFor({ timeout: 8000 });
  await panel(p).locator('input[aria-label="Run time (s)"]').fill("99999");
  await panel(p).locator('input[aria-label="Run time (s)"]').dispatchEvent("change");
  await panel(p).locator('button[data-act="confirm"]').click();
  const refused = await until(p, async () => (await panel(p).locator('[data-pending="refused"]').count()) > 0 && panel(p).locator('[data-pending="refused"]').innerText(), 20000);
  check("O-04 a refusal by the controller is shown in plain words", !!refused && !/[a-z_]+\.[a-z_]+|MANUAL|duration_s/.test(refused.split("\n")[0]),
    `return pump 1 for 99,999 s: "${(refused || "").split("\n")[0]}"`);
  await stopAll(p);

  // O-04 sensors: the reading shown is the one acquired after the click; without a new one, its real age
  await show(p, "overview");
  const rd = p.waitForResponse(r => r.url().includes("/api/plant/reading?signal=main.ph"));
  await pick(p, "main.ph");
  const body = await (await rd).json();
  await until(p, async () => (await panel(p).locator('[data-reading="fresh"]').count()) > 0, 8000);
  const shown = await panel(p).locator("[data-acquired]").getAttribute("data-acquired");
  const sTxt = await panelText(p);
  check("O-04 a sensor click asks for a new reading and shows its own time", body.fresh && shown === body.sample.acquired_utc && Date.parse(shown) > Date.parse(body.requested_utc) &&
    /New reading, taken after you asked/.test(sTxt) && /Measured at/.test(sTxt) && /Validity\s+Reading valid/.test(sTxt) && /pH/.test(sTxt),
    `asked ${body.requested_utc}, acquired ${body.sample.acquired_utc} (shown ${shown}): ${body.sample.value} ${body.sample.unit}, ${body.sample.quality}`);
  await api(p, "/api/twin/control", "POST", { action: "acq_stall" });
  await p.waitForTimeout(1500);
  const rd2 = p.waitForResponse(r => r.url().includes("/api/plant/reading?signal=main.ph"));
  await panel(p).getByRole("button", { name: "Ask for a new reading" }).click();
  const body2 = await (await rd2).json();
  await until(p, async () => (await panel(p).locator('[data-reading="old"]').count()) > 0, 8000);
  const oTxt = await panelText(p);
  await shot(p, "o04-sensor-no-fresh-reading");
  await api(p, "/api/twin/control", "POST", { action: "acq_resume" });
  check("O-04 without a new reading, the newest is shown as old", body2.fresh === false && /No new reading arrived within 3 seconds\. This is the newest reading, not a new one/.test(oTxt) &&
    Date.parse(body2.sample.acquired_utc) < Date.parse(body2.requested_utc) && /Age\s+\d+ seconds ago/.test(oTxt),
    `acquisition stalled: "${(oTxt.match(/No new reading[^\n]*/) || [""])[0]}", measured ${body2.sample.acquired_utc}, ${(oTxt.match(/Age\s+[^\n]*/) || [""])[0]}`);
  await p.waitForTimeout(2500);

  // O-05: sector names, readings and their pages and history stay on the Overview
  await show(p, "overview");
  const sec = await p.locator("#overview-sectors").innerText();
  await p.locator("#overview-sectors a", { hasText: "Open Sector 1" }).click();
  const toS1 = await until(p, () => p.evaluate(() => location.hash === "#sector-S1"));
  await show(p, "overview");
  await p.locator('#overview-sectors tr[data-sector="S2"] a', { hasText: "History of these readings" }).click();
  await show(p, "history");
  const histSel = await p.evaluate(() => window.AqUI.S.trendSel.slice());
  const pv = await get(p, "/api/plant");
  check("O-05 sector data on the Overview", /Sector 1/.test(sec) && /Sector 2/.test(sec) && /L\/min/.test(sec) && /bar/.test(sec) && toS1 &&
    histSel.includes(pv.signals["flow.S2"].point) && histSel.includes(pv.signals["pressure.S2"].point),
    `Sectors card with valve, flow and pressure; Open Sector 1 opens #sector-S1; History of Sector 2 chooses ${histSel.join(", ")}`);

  // O-02: a refill shows water entering the main tank and its level rising (opened from the diagram)
  await show(p, "overview");
  await pick(p, "valve.FILL_MAIN");
  posts.length = 0;
  await panel(p).locator('button[data-act="equip"]').click();
  await panel(p).locator('[role="alertdialog"]').waitFor({ timeout: 8000 });
  await panel(p).locator('input[aria-label="Amount (L)"]').fill("2000");
  await panel(p).locator('input[aria-label="Amount (L)"]').dispatchEvent("change");
  await panel(p).locator('button[data-act="confirm"]').click();
  await until(p, async () => (await p.locator('section.view:not(.hidden) svg.diagram path.pipe.flow[d^="M620 40"]').count()) > 0, 30000);
  await p.waitForTimeout(7000);
  const rise = await p.evaluate(() => (document.querySelector('section.view:not(.hidden) svg.diagram [data-trend="rising"]') || {}).textContent || "");
  const rf = await procs(p);
  await shot(p, "o02-refill-rising-desktop-dark");
  await show(q, "overview");
  await q.waitForTimeout(6000);
  await phoneDiagram(q, 0.45);
  await shot(q, "o02-refill-rising-phone-dark", false);
  check("O-02 a refill: water in and the main tank rising", /rising/.test(rise) && JSON.stringify(posts) === JSON.stringify(["/api/processes/REFILL/state", "/api/processes/REFILL/manual"]) &&
    rf.REFILL.manual_op && rf.REFILL.manual_op.op.path === "WATER",
    `water inlet valve of the main tank opened from the diagram (sent ${posts.join(", ")}); mains pipe with measured flow; main tank "${rise}"`);
  await stopAll(p);

  // O-03: the examples by name, with what loading does
  await show(p, "twin");
  const opts = await p.$$eval("#scn option", os => os.map(o => [o.value, o.textContent]));
  await p.selectOption("#scn", "tour_low_main");
  await p.waitForTimeout(300);
  const ex1 = await p.locator("#v-twin").innerText();
  await p.selectOption("#scn", "irrigation_alternation");
  await p.waitForTimeout(300);
  const ex2 = await p.locator("#scn-desc").innerText();
  await p.selectOption("#scn", "tour_low_main");
  await shot(p, "o03-examples");
  posts.length = 0;
  await p.getByRole("button", { name: "Load this example" }).click();
  const loaded = await until(p, async () => (await get(p, "/api/status")).twin.scenario === "tour_low_main", 15000);
  await p.waitForTimeout(1500);
  const lv = (await get(p, "/api/plant")).signals["tank.MAIN.level"].sample.value, st5 = await procs(p);
  check("O-03 Digital Twin examples", /Choose an example/.test(ex1) && /These examples set up the simulated greenhouse so you can try different situations\./.test(ex1) &&
    opts.find(o => o[0] === "tour_low_main")[1] === "Try refilling the main tank" && /Sets the main tank level low \(7,600 L\) so you can try a refill/.test(ex1) &&
    /only sets up this situation: nothing starts/.test(ex1) && /starts the model again/.test(ex1) && /starts processes or operations by itself/.test(ex2) &&
    !opts.some(o => /tour_|UAT/.test(o[1])) && loaded && Math.abs(lv - 7600) < 200 && Object.values(st5).every(x => x.state === "STOPPED"),
    `${opts.length} examples named in words (for example "${opts[0][1]}"); preview and what loading does shown; ids and UAT under Details; loaded: main tank ${lv} L, everything stopped`);

  // R33-01: on a phone, the pinned Stop is the chosen tank's processes and stops exactly those
  const r1 = [];
  const qp = sentPosts(q);
  for (const [tab, label, procsOf] of [["A", "Fertiliser tank A", ["REFILL", "STOCK_FILL_A"]], ["B", "Fertiliser tank B", ["REFILL", "STOCK_FILL_B"]], ["ACID", "Acid tank", ["ACID", "STOCK_FILL_ACID"]]]) {
    for (const pid of procsOf) await api(q, `/api/processes/${pid}/state`, "POST", { target: "MANUAL" });
    await show(q, "nutrients");
    await q.locator(".tabs button", { hasText: label }).click();
    await until(q, () => q.evaluate(pids => { const r = [...document.querySelectorAll(".stopbar .stoprow")].map(x => x.dataset.proc); return JSON.stringify(r) === JSON.stringify(pids); }, procsOf), 8000);
    const rows = await q.$$eval(".stopbar .stoprow", rs => rs.map(r => [r.dataset.proc, r.innerText.replace(/\s+/g, " ")]));
    if (tab === "ACID") await shot(q, "r33-01-phone-acid-tab-stopbar", false);
    for (const pid of procsOf) {
      qp.length = 0;
      await q.locator(`.stopbar button.stop[data-proc="${pid}"]`).click();
      const ok = await until(q, async () => (await procs(q))[pid].state === "STOPPED", 10000);
      r1.push([tab, pid, rows.map(x => x[1]).join(" | "), qp.join(","), ok]);
    }
  }
  check("R33-01 the pinned Stop follows the chosen tank", r1.length === 6 && r1.every(([, pid, , sent, ok]) => sent === `/api/processes/${pid}/state` && ok),
    r1.map(([tab, pid, rows, sent, ok]) => `${tab}: [${rows}] → ${sent} → ${ok ? "STOPPED" : "NOT STOPPED"}`).join(" · "));

  // R33-02: every recipe problem beside its field, with the typed value kept
  await show(p, "recipes");
  await p.locator("section.view:not(.hidden) tr", { hasText: /demo-sim@v\d+/ }).filter({ hasText: "In use" }).getByRole("button", { name: "Change settings" }).click();
  await p.waitForSelector(".editor");
  const acid = p.locator(".editor details.section", { has: p.locator("summary", { hasText: /^Acid dosing$/ }) });
  if (!(await acid.evaluate(d => d.open))) await acid.locator("summary").first().click();
  const adv = acid.locator("details.advanced");
  if (!(await adv.evaluate(d => d.open))) await adv.locator("summary").first().click();
  const help = await p.locator("#rf-acid-max_no_response-help").innerText();
  await p.fill("#rf-acid-max_no_response", "1.5");
  const toasts = [];
  p.on("console", () => {});
  await p.locator(".editor").getByRole("button", { name: "Check settings" }).click();
  const e1 = await until(p, async () => (await p.locator('.editor [data-path="acid.max_no_response"] .error').count()) && p.locator('.editor [data-path="acid.max_no_response"] .error').innerText(), 10000);
  const k1 = await p.inputValue("#rf-acid-max_no_response"), inv = await p.getAttribute("#rf-acid-max_no_response", "aria-invalid"), desc = await p.getAttribute("#rf-acid-max_no_response", "aria-describedby");
  const toastTxt = await p.locator("#toast").innerText().catch(() => "");
  await shot(p, "r33-02-whole-number-beside-field");
  check("R33-02 a fractional count is marked at its field", /Enter a whole number, without decimals/.test(e1 || "") && k1 === "1.5" && inv === "true" && /-err/.test(desc || "") && /a whole number/.test(help) &&
    !/Input should be|pydantic|int_from_float/.test(toastTxt),
    `"${e1}" beside "Doses without a pH drop…", typed 1.5 kept, aria-invalid and described by the message; hint "${help.slice(0, 40)}…"; toast "${toastTxt}"`);
  await p.fill("#rf-acid-max_no_response", "11");
  await p.locator(".editor").getByRole("button", { name: "Check settings" }).click();
  const e2 = await until(p, async () => { const t = await p.locator('.editor [data-path="acid.max_no_response"] .error').innerText().catch(() => ""); return /at most|10/.test(t) && t; }, 10000);
  await p.fill("#rf-acid-max_no_response", "2");
  await p.locator(".editor").getByRole("button", { name: "Discard changes" }).click();
  await p.locator("section.view:not(.hidden) tr", { hasText: /demo-sim@v\d+/ }).filter({ hasText: "In use" }).getByRole("button", { name: "Copy to a new recipe" }).click();
  await p.waitForSelector(".editor");
  await p.fill("#rf-recipe-id", "Summer 2027");
  await p.fill("#rf-recipe-name", "ui check (not saved)");
  await p.locator(".editor").getByRole("button", { name: "Check settings" }).click();
  const e3 = await until(p, async () => (await p.locator('.editor [data-path="recipe_id"] .error').count()) && p.locator('.editor [data-path="recipe_id"] .error').innerText(), 10000);
  const k3 = await p.inputValue("#rf-recipe-id");
  await p.locator(".editor").getByRole("button", { name: "Discard changes" }).click();
  check("R33-02 out-of-range values and a bad recipe id", !!e2 && /Use 1 to 48 characters/.test(e3 || "") && k3 === "Summer 2027",
    `11 doses: "${e2}"; recipe id "Summer 2027": "${e3}" beside the field, typed id kept`);

  // R33-03: a valve that did not open is not reported as repaired; a drive fault that is gone is
  await api(p, "/api/twin/actuator-fault", "POST", { kind: "valve", ident: "S1", fault: "STUCK_CLOSED" });
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "AUTO" });
  await until(p, async () => (await procs(p)).IRRIGATION.state === "FAULT_LOCKOUT" && !(await procs(p)).IRRIGATION.stopping, 60000);
  await show(p, "main-pumps");
  const card = p.locator("section.view:not(.hidden) .card.proc").first();
  await card.getByRole("button", { name: "Mark as seen" }).click();
  await until(p, async () => !(await card.getByRole("button", { name: "Clear stop after fixing the problem" }).isDisabled()), 10000);
  const v1 = await card.innerText();
  await shot(p, "r33-03-valve-cannot-be-checked");
  await card.getByRole("button", { name: "Clear stop after fixing the problem" }).click();
  const cleared = await until(p, async () => (await procs(p)).IRRIGATION.state === "STOPPED", 10000);
  await p.waitForTimeout(2000);
  const still = (await procs(p)).IRRIGATION.state;
  check("R33-03 a retained valve fault is not reported as fixed", /The program cannot check this while watering is stopped/.test(v1) && /Check the valve of Sector 1 yourself/.test(v1) &&
    !/no longer sees the problem/.test(v1) && cleared && still === "STOPPED",
    `"${(v1.match(/The program cannot check[^\n]*/) || [""])[0]}"; Mark as seen, then Clear stop (separate); stays ${still}`);
  await api(p, "/api/twin/actuator-fault", "POST", { kind: "valve", ident: "S1", fault: null });
  await api(p, "/api/processes/IRRIGATION/state", "POST", { target: "AUTO" });
  await until(p, async () => (await procs(p)).IRRIGATION.phase === "IRRIGATE", 40000);
  await api(p, "/api/twin/actuator-fault", "POST", { kind: "drive", ident: "MAIN", fault: "TRIP" });
  await until(p, async () => (await procs(p)).IRRIGATION.state === "FAULT_LOCKOUT", 20000);
  await show(p, "main-pumps");
  const d1 = await until(p, async () => { const t = await p.locator("section.view:not(.hidden) .card.proc").first().innerText(); return /The program still sees the problem/.test(t) && t; }, 15000);
  await api(p, "/api/twin/actuator-fault", "POST", { kind: "drive", ident: "MAIN", fault: null });
  const d2 = await until(p, async () => { const t = await p.locator("section.view:not(.hidden) .card.proc").first().innerText(); return /no longer sees the problem/.test(t) && t; }, 20000);
  check("R33-03 the drive fault case", !!d1 && !!d2 && !/cannot check this/.test(d2),
    "drive tripped: \"The program still sees the problem\"; fault input released: \"The program no longer sees the problem; check the equipment yourself before starting again\"");
  const occ = (await get(p, "/api/alarms")).open || [];
  for (const o of (Array.isArray(occ) ? occ : [])) if (!o.acknowledged) await api(p, "/api/alarms/ack", "POST", { rule_id: o.rule_id, target: o.target });
  await api(p, "/api/processes/IRRIGATION/reset", "POST");
  await stopAll(p).catch(() => {});
  await api(p, "/api/twin/control", "POST", { action: "reset" });
  check("0.3.4 page errors", errs.length === 0, errs.join("; ") || "none");
  await pc.close();
  await ctx.close();
}

// 0.4.0 (ADR-0017): the developer I/O board on the simulation's six virtual modules
const boardPoint = async (q, id) => (await get(q, "/api/board")).modules.flatMap(m => m.points).find(x => x.id === id);
async function board040(b) {
  const ctx = await b.newContext({ viewport: { width: 1366, height: 1000 }, acceptDownloads: true });
  const p = await ctx.newPage();
  const errs = watchErrors(p);
  await login(p, USER, PASS);
  await api(p, "/api/users/" + USER, "POST", { commissioning: true });     // the throwaway admin commissions
  await login(p, USER, PASS);
  const nav = await p.locator("#nav").innerText();
  await show(p, "board");
  const n = await p.evaluate(() => ({ mods: document.querySelectorAll("#v-board .card.module").length, ch: document.querySelectorAll("#v-board [data-point]").length }));
  await shot(p, "board-1-simulation");
  check("BOARD-01 one card per module, every channel", /I\/O board/.test(nav) && n.mods === 6 && n.ch === 80, `menu has I/O board; ${n.mods} module cards, ${n.ch} channels`);
  await p.locator("#v-board").getByRole("button", { name: "Use I/O test mode" }).click();
  await until(p, async () => (await get(p, "/api/board")).source === "IO_TEST", 10000);
  const sw = p.locator('#v-board [data-point="relay_A.DO3"] .switch');
  await until(p, async () => !(await sw.isDisabled()), 5000);
  await sw.click();
  const on = await until(p, async () => { const x = await boardPoint(p, "relay_A.DO3"); return x.sample.value === true && x.asked && x.asked.desired === true && x; }, 10000);
  const dom = await until(p, () => p.evaluate(() => { const t = document.querySelector('#v-board [data-point="relay_A.DO3"]');
    return t.querySelector(".switch").getAttribute("aria-checked") === "true" && t.querySelectorAll(".led.led-on").length === 2 && t.innerText; }), 5000);
  check("BOARD-02 a switch sends the command; asked and reported lights", !!on && !!dom, `relay_A.DO3 asked ON (${on && on.asked.outcome}), reads ON; both lights on`);
  await p.locator('#v-board [data-point="relay_B.DO5"] .switch').click();
  await until(p, async () => (await boardPoint(p, "relay_B.DO5")).sample.value === true, 10000);
  await p.locator("#v-board").getByRole("button", { name: "All outputs OFF" }).click();
  const off = await until(p, async () => (await get(p, "/api/board")).modules.flatMap(m => m.points).filter(x => x.kind === "DO" && x.sample.value).length === 0, 10000);
  check("BOARD-03 All outputs OFF", off, "relay_A.DO3 and relay_B.DO5 read OFF after one press");
  const di = p.locator('#v-board [data-point="digital_A.DI2"]');
  await di.getByRole("button", { name: "1", exact: true }).click();
  await until(p, async () => (await boardPoint(p, "digital_A.DI2")).sample.value === true, 10000);
  await p.waitForTimeout(600);
  await p.locator('#v-board [data-point="digital_A.DI2"]').getByRole("button", { name: "0", exact: true }).click();
  const diTxt = await until(p, async () => { const t = await p.locator('#v-board [data-point="digital_A.DI2"]').innerText(); return /changed 2×/.test(t) && t; }, 10000);
  check("BOARD-04 an input light follows the input", !!diTxt, "digital_A.DI2 injected 1 then 0: light on then off, \"changed 2× in 15 min\"");
  const ai = p.locator('#v-board tr[data-point="analog_A.AI1"]');
  await ai.locator("input").fill("12000");
  await ai.getByRole("button", { name: "Inject" }).click();
  const aiTxt = await until(p, async () => { const t = await p.locator('#v-board tr[data-point="analog_A.AI1"]').innerText(); return /12\.00 mA/.test(t) && t; }, 10000);
  check("BOARD-05 an analog reading in mA with the raw value", !!aiTxt && /raw 12000 uA/.test(aiTxt), (aiTxt || "").replace(/\s+/g, " ").slice(0, 120));
  // assign relay_A.DO3, exercise it, verify it
  await p.locator('#v-board [data-point="relay_A.DO3"] .chan').click();
  const form = p.locator("#v-board .assign");
  await form.locator("select").selectOption("valve.S1.cmd");
  await form.locator('input[aria-label="Note"]').fill("ui check: lamp L3");
  await form.getByRole("button", { name: "Save assignment" }).click();
  const prop = await until(p, async () => { const x = await boardPoint(p, "relay_A.DO3"); return x.assignment && x.assignment.status === "PROPOSED"; }, 10000);
  await p.locator("#v-board").getByRole("button", { name: "Mark verified" }).waitFor();
  await p.locator('#v-board input[aria-label="What you saw"]').fill("virtual lamp on, then off");
  await p.locator("#v-board").getByRole("button", { name: "Mark verified" }).click();
  await until(p, async () => /switch relay_A.DO3 ON and then OFF/.test(await p.locator("#toast").innerText()), 5000);
  const refused = await p.locator("#toast").innerText();
  await sw.click();
  // the switch sends the opposite of what it shows, so wait until it shows ON
  await until(p, async () => (await p.locator('#v-board [data-point="relay_A.DO3"] .switch').getAttribute("aria-checked")) === "true", 10000);
  await p.locator('#v-board [data-point="relay_A.DO3"] .switch').click();
  await until(p, async () => (await boardPoint(p, "relay_A.DO3")).sample.value === false, 10000);
  await p.locator('#v-board input[aria-label="What you saw"]').fill("virtual lamp on, then off");
  await p.locator("#v-board").getByRole("button", { name: "Mark verified" }).click();
  const ver = await until(p, async () => { const x = await boardPoint(p, "relay_A.DO3"); return x.assignment && x.assignment.status === "VERIFIED" && x.assignment; }, 10000);
  await shot(p, "board-2-assigned-verified");
  check("BOARD-06 assign, then verify after ON and OFF", prop && !!ver && ver.evidence.check === "DO_ON_OFF" && /ON and then OFF/i.test(refused),
    `PROPOSED valve.S1.cmd; verify refused before the ON/OFF ("${refused}"); VERIFIED after it, observation recorded`);
  const [dl] = await Promise.all([p.waitForEvent("download"), p.locator("#v-board a", { hasText: "Export assignments (YAML)" }).click()]);
  const yml = fs.readFileSync(await dl.path(), "utf8");
  check("BOARD-07 export", /AQUACROP_IO_ASSIGNMENT_PROPOSAL/.test(yml) && /VERIFIED: 1/.test(yml) && /signal: valve.S1.cmd/.test(yml), `${dl.suggestedFilename()}, ${yml.length} bytes`);
  await p.setViewportSize({ width: 390, height: 900 });
  await show(p, "board");
  const wide = await p.evaluate(() => document.scrollingElement.scrollWidth);
  await shot(p, "board-3-phone");
  check("BOARD-08 phone width", wide <= 391, `page ${wide} px wide at 390 px`);
  // the guided check: Next moves through every channel; each output is ON during its own step only
  await p.setViewportSize({ width: 1366, height: 1000 });
  await show(p, "board");
  const gp = p.locator("#v-board .card.guide");
  await gp.locator('select[aria-label="Which modules"]').selectOption("relay_B");
  await gp.getByRole("button", { name: "Start guided check" }).click();
  const s1 = await until(p, async () => { const x = await boardPoint(p, "relay_B.DO1"); return x.sample.value === true && x.asked.desired === true; }, 10000);
  const t1 = await gp.innerText();
  await shot(p, "board-4-guided-output");
  await gp.getByRole("button", { name: "Works" }).click();
  await gp.getByRole("button", { name: "Next", exact: true }).click();
  const s2 = await until(p, async () => { const a = await boardPoint(p, "relay_B.DO1"), b2 = await boardPoint(p, "relay_B.DO2");
    return a.sample.value === false && b2.sample.value === true; }, 10000);
  await until(p, async () => /2 of 16/.test(await gp.innerText()), 5000);
  await gp.getByRole("button", { name: "Doesn't work" }).click();
  await gp.getByRole("button", { name: "Next", exact: true }).click();
  await until(p, async () => /3 of 16/.test(await gp.innerText()), 10000);
  for (let k = 3; k <= 8; k++) {                                       // skip the other outputs
    await gp.getByRole("button", { name: "Next (skip this one)" }).click();
    await until(p, async () => new RegExp(`${k + 1} of 16`).test(await gp.innerText()), 10000);
  }
  const t9 = await gp.innerText();
  await gp.getByRole("button", { name: "1", exact: true }).click();     // the virtual input changes
  const auto = await until(p, async () => (await gp.getByRole("button", { name: "Works" }).getAttribute("aria-pressed")) === "true", 10000);
  await shot(p, "board-5-guided-input");
  await gp.getByRole("button", { name: "Next", exact: true }).click();
  await until(p, async () => /10 of 16/.test(await gp.innerText()), 10000);
  await gp.getByRole("button", { name: "Stop guided check" }).click();
  const res = await get(p, "/api/iochecks");
  const anyOn = (await get(p, "/api/board")).modules.flatMap(m => m.points).filter(x => x.kind === "DO" && x.sample.value).map(x => x.id);
  check("BOARD-09 guided check with Next", !!s1 && !!s2 && /Output relay_B.DO1 is switched ON/.test(t1) && /Change input relay_B.DI1 now/.test(t9) && auto &&
    res["relay_B.DO1"].result === "PASS" && res["relay_B.DO2"].result === "FAIL" && res["relay_B.DO5"].result === "SKIPPED" &&
    res["relay_B.DI1"].result === "PASS" && anyOn.length === 0,
    `DO1 ON during its step then OFF at Next (works), DO2 (doesn't work), DO3-8 skipped, DI1 change seen and marked works; after Stop no output reads ON (${anyOn.join(",") || "none"})`);
  await api(p, "/api/io/source", "POST", { source: "TWIN" });
  await api(p, "/api/users/" + USER, "POST", { commissioning: false });
  check("BOARD page errors", errs.length === 0, errs.join("; ") || "none");
  await ctx.close();
}

// The same pages on HARDWARE_READ_ONLY (fake bus): observation only, controls replaced by the reason.
async function hardware() {
  const b = await launch();
  const p = await b.newPage({ viewport: { width: 1366, height: 1000 } });
  const errs = watchErrors(p);
  await login(p, USER, PASS);
  await until(p, () => p.evaluate(() => !document.getElementById("nav-inst").classList.contains("hidden")), 10000);
  const nav = (await p.locator("#nav").innerText()).replace(/\s+/g, " ");
  check("HW nav", ["Nutrients & acid", "Main tank", "Watering", "Sector 1", "Sector 2", "Drainage return"].every(t => nav.includes(t)) &&
    !nav.includes("Recipes"), nav);      // 0.5.5: Digital Twin is listed: it opens the twin service at /twin/
  const hwPlain = await p.locator("#status-plain").innerText();
  check("HW status line", /^Real equipment, read only/.test(hwPlain), hwPlain);
  await show(p, "overview");
  await shot(p, "hw-overview");
  const ov = await p.evaluate(() => ({ hot: document.querySelectorAll("#v-overview svg.diagram g.hot").length, label: document.querySelector("#v-overview svg.diagram").getAttribute("aria-label") }));
  check("HW overview diagram", ov.hot >= 40 && /read-only/.test(ov.label), `${ov.hot} parts; ${ov.label}`);
  // 0.5.6: the real Overview draws the accepted layout (symbols by layout id), read-only, with a side panel
  await p.locator('#v-overview svg.diagram g.hot[data-id="mp1"]').click();
  const hwPanel = await until(p, async () => { const t = await p.locator("#v-overview .side-panel").innerText(); return /Main pump 1/.test(t) && t; }, 5000);
  const hwBtns = await p.$$eval("#v-overview .side-panel button", bs => bs.map(x => x.textContent));
  await p.locator('#v-overview svg.diagram g.hot[data-id="tank-main"]').click();
  const hwTank = await until(p, async () => { const t = await p.locator("#v-overview .side-panel").innerText(); return /Main tank/.test(t) && t; }, 5000);
  const hwHead = await p.locator("#overview-layout h2").first().innerText();
  check("HW click-through", !!hwPanel && !!hwTank && hwBtns.length === 0 && /read-only/.test(hwHead),
    "selecting a main pump or the main tank shows its state in the side panel, with no action: the installation is read-only");
  for (const v of ["nutrients", "main-tank", "main-pumps", "sector-S1", "sector-S2", "drainage"]) {
    await show(p, v);
    const t = await visibleText(p);
    const ctl = await p.$$eval("section.view:not(.hidden) button", bs => bs.map(x => x.textContent).filter(x => /^(Start automatic watering|Switch on automatic.*|Manual mode|Stop .*|Run once|Confirm prepared|Mark not ready|Clear stop after fixing the problem|Add simulated .*)$/.test(x)));
    await shot(p, "hw-page-" + v);
    check("HW page " + v, t.includes("Real equipment, read only: this release does not control real equipment") && t.includes("not measured") && ctl.length === 0,
      `read-only: reason shown, unmapped values "not measured", no control buttons ${JSON.stringify(ctl)}`);
  }
  await show(p, "overview");
  const drvTxt = await until(p, async () => { const t = await visibleText(p); return /Drive interface/.test(t) && /PROPOSED/.test(t) && /0x[0-9A-F]{4}/.test(t) && t; });
  check("HW drive interface", !!drvTxt, "the overview lists drive:MAIN over Modbus with its PROPOSED map and the status word it reads");
  await show(p, "io");
  const ioDrv = await p.locator("#v-io tr", { hasText: "drive:MAIN" }).first();
  const runBtns = await ioDrv.locator("button").count();
  check("HW drive read-only without a lease", (await ioDrv.count()) > 0 && runBtns === 0, `drive row on the I/O page, ${runBtns} Run/Stop buttons without a lease`);
  const hwIoHead = await p.$$eval("#v-io .card h3", hs => hs.map(x => x.parentElement.textContent));
  const hwDig = hwIoHead.find(t => t.startsWith("digital_A")) || "", hwRel = hwIoHead.find(t => t.startsWith("relay_A")) || "";
  check("HW output types", /TRANSISTOR SINK — not relay contacts, max 500 mA/.test(hwDig) && /relay contacts/.test(hwRel) && !/TRANSISTOR/.test(hwRel),
    "I/O page: digital_A (SKU 26244) labelled transistor sink, relay_A labelled relay contacts");
  await show(p, "board");
  const hb = await p.evaluate(() => ({ t: document.querySelector("#v-board").innerText, sw: [...document.querySelectorAll("#v-board .switch")].map(x => x.disabled) }));
  await shot(p, "hw-board");
  // 0.5.4: an administrator has the commissioning right, so the board offers a test session; until one is armed
  // every output switch stays disabled
  check("HW I/O board read only", /REAL MODULES/.test(hb.t) && /Start a test session/.test(hb.t) && hb.sw.length === 16 && hb.sw.every(Boolean),
    `${hb.sw.length} output switches, all disabled until a test session is armed`);
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
  check("UI-03 restart", Object.values(st.processes).every(x => x.state === "STOPPED") && /^demo-sim@v[2-9]/.test(st.recipe.active || "") &&
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
