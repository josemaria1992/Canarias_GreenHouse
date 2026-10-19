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
  check("UI-02 diagram parts", hot.length >= 25 && hot.every(([r, l, t]) => r === "link" && l && t === "0"), `${hot.length} clickable parts, all role=link with labels`);
  const svgText = await p.$eval("#v-overview svg.diagram", s => s.textContent);
  check("UI-02 units", ["L", "pH", "mS/cm", "L/min", "bar"].every(u => svgText.includes(u)) && /\d+ seconds? ago/.test(svgText),
    "litres, pH, mS/cm, L/min, bar and the age of the readings on the diagram");
  const labels = hot.map(x => x[1]).join(" | ");
  check("UI-02 requested vs observed", /asked .*reports/.test(labels) && /no position signal/.test(labels),
    "pump labels give what was asked and what it reports; valves without a position signal say so");
  check("UI-02 no installed-probe or mixer claim", svgText.includes("probe position proposed, not installed") &&
    svgText.includes("No stirrer or air pump") && !/(agitator|mixer|stirrer)\s+(on|running|installed)/i.test(svgText), "probe and mixing notes present");
  await p.locator('#v-overview svg.diagram g.hot[aria-label^="Sector 2"]').click();
  check("UI-02 click-through", await until(p, () => p.evaluate(() => location.hash === "#sector-S2")), "the Sector 2 box opens #sector-S2");
  await show(p, "overview");
  await p.locator('#v-overview svg.diagram g.hot[aria-label^="Main tank:"]').focus();
  await p.keyboard.press("Enter");
  check("UI-02 keyboard", await until(p, () => p.evaluate(() => location.hash === "#main-tank")), "Enter on the main tank opens #main-tank");

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
  check("HW nav", ["Nutrients & acid", "Main tank", "Watering", "Sector 1", "Sector 2", "Drainage return"].every(t => nav.includes(t)) &&
    !nav.includes("Recipes") && !/Digital Twin/i.test(nav), nav);
  const hwPlain = await p.locator("#status-plain").innerText();
  check("HW status line", /^Real equipment, read only/.test(hwPlain), hwPlain);
  await show(p, "overview");
  await shot(p, "hw-overview");
  const ov = await p.evaluate(() => ({ hot: document.querySelectorAll("#v-overview svg.diagram g.hot").length, label: document.querySelector("#v-overview svg.diagram").getAttribute("aria-label") }));
  check("HW overview diagram", ov.hot >= 25 && /read-only/.test(ov.label), `${ov.hot} parts that open read-only pages; ${ov.label}`);
  await p.locator('#v-overview svg.diagram g.hot[aria-label^="Main tank"]').click();
  check("HW click-through", await until(p, () => p.evaluate(() => location.hash === "#main-tank")), "main tank opens #main-tank");
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
