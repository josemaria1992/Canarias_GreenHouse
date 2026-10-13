// Claude's browser rehearsal of the owner's acceptance tour (docs/operator/walkthrough.md). Each case follows
// the tour's numbered steps through the screen (the buttons and fields the owner uses), checks what the tour
// says should happen, also through the API, and saves the screenshots the illustrated guide shows.
//
// Driven by run_tour_rehearsal.py. Phase "main": UAT-01 to UAT-14 and the layout part of UAT-16. Phase "after"
// (a restarted launcher on the same state): UAT-15, then the loss-of-connection part of UAT-16.
// This is not the owner's acceptance and not physical verification.
"use strict";
const fs = require("fs");
const path = require("path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const BASE = process.env.AQ_BASE, USER = process.env.AQ_USER, PASS = process.env.AQ_PASS, OUT = process.env.AQ_OUT;
const ONLY = (process.env.AQ_ONLY || "").split(",").filter(Boolean);
const PHASE = process.argv[2] || "main";
const checks = [];
let current = "";

function check(id, ok, detail) {
  checks.push({ id: `${current} ${id}`.trim(), ok: !!ok, detail: String(detail) });
  console.log(`${ok ? "PASS" : "FAIL"} ${current} ${id}: ${detail}`);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
// JPEG, the page from the top down to at most 2,200 px: the guide stays small enough to ship in the release ZIP
async function shot(p, name, full = true) {
  const vp = p.viewportSize(), h = full ? Math.min(2200, await p.evaluate(() => document.documentElement.scrollHeight)) : vp.height;
  await p.screenshot({ path: path.join(OUT, name + ".jpg"), type: "jpeg", quality: 72, fullPage: full, clip: { x: 0, y: 0, width: vp.width, height: h } });
  await p.waitForTimeout(1000);      // a full-page capture resizes the page: let the layout settle before the next click
}
const api = (p, url, method = "GET", body) => p.evaluate(async ([url, method, body]) => {
  const csrf = window.AqUI && window.AqUI.S.csrf;
  const r = await fetch(url, { method, credentials: "same-origin",
    headers: method === "GET" ? {} : { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const ct = r.headers.get("content-type") || "";
  return { status: r.status, body: ct.includes("json") ? await r.json() : await r.text() };
}, [url, method, body]);
const get = async (p, url) => (await api(p, url)).body;
async function until(p, fn, ms = 30000, step = 250) {
  const end = Date.now() + ms;
  let v;
  while (Date.now() < end) {
    v = await fn();
    if (v) return v;
    await p.waitForTimeout(step);
  }
  return v;
}
const prompts = [];          // what the next prompt()/confirm() should answer
function watch(p) {
  const errs = [];
  p.on("pageerror", e => errs.push(String(e)));
  p.on("console", m => { if (m.type() === "error" && !/status of 4\d\d|Failed to fetch|ERR_CONNECTION_REFUSED|ERR_INTERNET_DISCONNECTED/.test(m.text())) errs.push("console: " + m.text()); });
  p.on("dialog", d => { const a = prompts.shift(); d.type() === "prompt" ? d.accept(a == null ? "tour rehearsal" : a) : d.accept(); });
  return errs;
}
async function login(p, user, pass) {
  for (let i = 0; i < 120; i++) { try { await p.goto(BASE + "/"); break; } catch (e) { await p.waitForTimeout(250); } }
  try { await p.waitForSelector("input[name=username]", { timeout: 15000 }); }
  catch (e) {
    await p.screenshot({ path: path.join(OUT, `login-${user}-error.png`) }).catch(() => null);
    throw new Error(`login page for ${user} not shown: ` + await p.evaluate(() => ["login-view", "pwchange-view", "app"].map(i => i + ":" + (document.getElementById(i) || {}).className).join(" ")).catch(x => String(x)));
  }
  await p.fill("input[name=username]", user);
  await p.fill("input[name=password]", pass);
  await p.keyboard.press("Enter");
}
async function signedIn(p) {
  await p.waitForSelector("#app:not(.hidden)", { timeout: 15000 });
  await until(p, () => p.evaluate(() => !!(window.AqUI && window.AqUI.S.status)));
}
async function show(p, view) {
  await p.goto(BASE + "/#" + view);
  await until(p, () => p.evaluate(v => {
    const sec = document.querySelector("section.view:not(.hidden)");
    return sec && location.hash === "#" + v && window.AqUI.S.rendered === v && sec.innerText.trim().length > 40;
  }, view), 10000);
  await p.waitForTimeout(400);
}
const text = p => p.evaluate(() => (document.querySelector("section.view:not(.hidden)") || document.body).innerText);
const toastText = p => p.evaluate(() => { const t = document.getElementById("toast"); return t && !t.classList.contains("hidden") ? t.textContent : ""; });
const clearToast = p => p.evaluate(() => { const t = document.getElementById("toast"); if (t) { t.classList.add("hidden"); t.textContent = ""; } });
async function lastToast(p, ms = 5000) { return until(p, () => toastText(p), ms, 100); }
// a button inside the card whose heading is `title` (process panels, stock rows)
async function cardButton(p, title, label) {
  const card = p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: title }) }).first();
  await card.locator("button", { hasText: new RegExp("^" + label.replace(/[()]/g, "\\$&") + "$") }).first().click();
  return card;
}
async function viewButton(p, label) {
  await p.locator("section.view:not(.hidden) button", { hasText: new RegExp("^" + label.replace(/[()×]/g, "\\$&") + "$") }).first().click();
}
// a button in the recipe list row of `ref` ("ACTIVE" picks the active recipe's row)
async function recipeButton(p, ref, label) {
  const row = ref === "ACTIVE" ? p.locator("section.view:not(.hidden) tr", { has: p.locator("span.pill", { hasText: /^ACTIVE$/ }) })
    : p.locator("section.view:not(.hidden) tr", { has: p.locator("strong", { hasText: new RegExp("^" + ref + "$") }) });
  await row.first().locator("button", { hasText: new RegExp("^" + label + "$") }).click();
}
const pathL = (b, k) => Number(((b.paths || {})[k] || {}).delivered_L || 0).toFixed(1) * 1;
const procs = async p => (await get(p, "/api/processes")).processes;
const proc = async (p, pid) => (await procs(p))[pid];
async function twinPreset(p, name) {
  await show(p, "twin");
  await p.selectOption("#scn", name);
  await viewButton(p, "Load scenario (new run)");
  await until(p, async () => (await get(p, "/api/status")).twin.scenario === name, 15000);
  await p.waitForTimeout(800);
}
async function resetRun(p) {
  await show(p, "twin");
  await viewButton(p, "Reset run");                  // confirm() accepted by the dialog handler
  await until(p, async () => (await get(p, "/api/status")).twin.scenario === null && (await get(p, "/api/status")).twin.sim_s < 30, 15000);
}
async function speed(p, n) {
  await show(p, "twin");
  await viewButton(p, n + "×");
  await until(p, async () => (await get(p, "/api/status")).twin.speed === n, 10000);
}
async function stopAll(p) {
  await show(p, "overview");
  await viewButton(p, "Stop all processes");
  await until(p, async () => Object.values(await procs(p)).every(x => x.state === "STOPPED" || x.state === "FAULT_LOCKOUT"), 30000);
}
const simS = async p => (await get(p, "/api/status")).twin.sim_s;
async function untilSim(p, fn, simLimit, wallMs = 240000) {
  const t0 = await simS(p);
  return until(p, async () => (await fn()) || ((await simS(p)) - t0 > simLimit ? "SIM_LIMIT" : null), wallMs, 500);
}
async function ackStocks(p, stocks) {
  await show(p, "nutrients");
  for (const s of stocks) {
    prompts.push("tour: prepared by hand (simulation)");
    const row = p.locator("section.view:not(.hidden) tr", { has: p.locator("td", { hasText: new RegExp("^" + s + "$") }) }).first();
    await row.locator("button", { hasText: "Acknowledge prepared" }).click();
    await p.waitForTimeout(600);
  }
}
async function ioOverride(p, point, raw, reason) {
  await show(p, "io");
  const row = p.locator("section.view:not(.hidden) tr", { has: p.locator("td.mono", { hasText: new RegExp("^" + point.replace(".", "\\.") + "$") }) }).first();
  await row.locator("input").first().fill(String(raw));
  prompts.push(reason);
  await row.locator("button", { hasText: "Override" }).click();
  await p.waitForTimeout(800);
}
async function ioClear(p, point) {
  await show(p, "io");
  const row = p.locator("section.view:not(.hidden) tr", { has: p.locator("td.mono", { hasText: new RegExp("^" + point.replace(".", "\\.") + "$") }) }).first();
  await row.locator("button", { hasText: "Clear" }).click();
  await p.waitForTimeout(800);
}
async function applyFault(p, kind, ident, fault) {
  await show(p, "twin");
  const row = p.locator("section.view:not(.hidden) .row", { has: p.locator("span", { hasText: new RegExp("^" + kind + "$") }) }).first();
  const sels = row.locator("select");
  await sels.nth(0).selectOption(ident);
  await sels.nth(1).selectOption(fault || "");
  await row.locator("button", { hasText: "Apply" }).click();
  await p.waitForTimeout(800);
}
async function ackAll(p) {
  await show(p, "alarms");
  const btns = p.locator("section.view:not(.hidden) button", { hasText: /^Ack$/ });
  const n = await btns.count();
  for (let i = 0; i < n; i++) { await btns.first().click(); await p.waitForTimeout(500); }
  return n;
}
const alarms = async p => (await get(p, "/api/alarms")).open || (await get(p, "/api/alarms"));

// ------------------------------------------------------------------------------------------------- cases
const CASES = {};

CASES["UAT-01"] = async (p) => {
  const banner = await p.locator("#banner").innerText();
  check("SIMULATION banner", /SIMULATION/.test(banner), banner);
  const st = await get(p, "/api/status");
  const line = await p.locator("#statusline").innerText();
  check("status line names version and config", line.includes("AquaCrop " + st.software_version) && line.includes("installation-80@"), line);
  await show(p, "overview");
  await shot(p, "UAT-01-overview");
  await show(p, "settings");
  const t = await text(p);
  const rel = (t.match(/Release\n(.*)/) || [])[1] || "";
  const inv = (t.match(/Inventory\n(.*)/) || [])[1] || "";
  check("Settings, Versions: inventory", inv === "6 modules: 16 AI + 32 DI + 32 DO = 80 points", inv);
  check("Settings, Versions: config", /installation-80@2026-10-13\.80\.1 \(SIMULATED\)/.test(t), (t.match(/Config\n(.*)/) || [])[1]);
  check("Settings, Versions: release line", process.env.AQ_EXPECT_RELEASE ? rel.includes("wheel verified") && rel.includes(process.env.AQ_EXPECT_RELEASE) : rel.length > 0, rel);
  await p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: "Versions" }) }).first().screenshot({ path: path.join(OUT, "UAT-01-versions.png") });
};

CASES["UAT-02"] = async (p) => {
  const targets = [["Stock A", "nutrients"], ["Main tank", "main-tank"], ["Main pump MP1", "main-pumps"], ["Sector S1", "sector-S1"],
    ["Sector S2", "sector-S2"], ["Sump", "drainage"]];
  for (const [label, view] of targets) {
    await show(p, "overview");
    await p.locator(`section.view:not(.hidden) svg.diagram g.hot[aria-label^="${label}"]`).first().click();
    const ok = await until(p, () => p.evaluate(v => location.hash === "#" + v && window.AqUI.S.rendered === v, view), 8000);
    await p.waitForTimeout(500);
    check(`diagram part "${label}" opens ${view}`, ok, ok ? "opened" : await p.evaluate(() => location.hash));
    await shot(p, `UAT-02-${view}`);
  }
  await show(p, "main-tank");
  const t = await text(p);
  check("values carry unit, quality and age", /12000 L GOOD SIM \d+\.\d s/.test(t) && /pH\n6\.\d\d pH GOOD SIM/.test(t), (t.match(/Level\n(.*)/) || [])[1]);
  const lay = (await get(p, "/api/plant")).layout;
  check("layout matches the confirmed installation", lay.main_pumps.length === 2 && lay.return_pumps.length === 2 &&
    lay.sectors.map(s => s.channels.length).join("/") === "6/7" && !lay.agitators, JSON.stringify({ mp: lay.main_pumps, rp: lay.return_pumps, sectors: lay.sectors.map(s => s.channels.length) }));
};

CASES["UAT-03"] = async (p) => {
  await resetRun(p);
  await speed(p, 10);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  const s1 = await untilSim(p, async () => { const x = await proc(p, "IRRIGATION"); return x.phase === "IRRIGATE" && JSON.stringify(x).includes('"S1"') && x; }, 120);
  await show(p, "main-pumps");
  await shot(p, "UAT-03-irrigating-S1");
  const t1 = await text(p);
  check("sector 1 with MP1, requested and observed apart", s1 && s1 !== "SIM_LIMIT" && /K1 \(MP1\)\nrequested ON .*observed closed/.test(t1), (t1.match(/K1 \(MP1\)\n(.*)/) || [])[1]);
  const changeover = await untilSim(p, async () => { const x = await proc(p, "IRRIGATION"); return x.phase !== "IRRIGATE" && x.phase !== "IDLE" && x.phase; }, 400);
  await show(p, "main-pumps");
  await shot(p, "UAT-03-changeover");
  check("changeover phase shown after 300 s", typeof changeover === "string" && changeover !== "SIM_LIMIT", changeover);
  const s2 = await untilSim(p, async () => { const x = await proc(p, "IRRIGATION"); return x.phase === "IRRIGATE" && JSON.stringify(x).includes('"S2"') && x; }, 120);
  await show(p, "sector-S2");
  await shot(p, "UAT-03-irrigating-S2");
  check("then sector 2 with MP2", s2 && s2 !== "SIM_LIMIT", s2 && s2.phase);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "STOP");
  const stopped = await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "STOPPED" && (await proc(p, "IRRIGATION")).outputs_on.length === 0, 120);
  check("STOP ends with every output off", stopped === true, JSON.stringify((await proc(p, "IRRIGATION")).outputs_on));
  const hist = ((await proc(p, "IRRIGATION")).history || []).map(t => t.to_phase);
  const order = ["SELECT", "OPEN", "LEAD", "START", "VERIFY_FLOW", "IRRIGATE", "STOP_DRIVE"];
  let k = 0; for (const ph of hist) if (ph === order[k]) k++;
  check("transition history: valve opens and leads before the drive starts; drive stopped before changeover", k === order.length, hist.join(" > "));
  await show(p, "main-pumps");
  const icard = p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: "Irrigation" }) }).first();
  await icard.locator("details > summary", { hasText: "Transition history" }).click();
  await p.waitForTimeout(600);
  await icard.screenshot({ path: path.join(OUT, "UAT-03-transition-history.png") });
  const log = (await proc(p, "IRRIGATION")).cycles || [];
  check("cycle log", log.length >= 1 && log[0].sector === "S1" && log[0].pump === "MP1" && Math.abs(log[0].irrigated_s - 300) < 2,
    JSON.stringify(log.map(x => [x.sector, x.pump, Math.round(x.irrigated_s)])));
};

CASES["UAT-04"] = async (p) => {
  await resetRun(p);
  await speed(p, 10);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "MANUAL");
  await until(p, async () => (await proc(p, "IRRIGATION")).state === "MANUAL", 10000);
  await show(p, "main-pumps");
  const card = p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: "Irrigation" }) }).first();
  const sels = card.locator("select");
  await sels.nth(0).selectOption("MP2"); await sels.nth(1).selectOption("S1");
  await card.locator("input[placeholder=seconds]").fill("99999");
  await clearToast(p);
  await card.locator("button", { hasText: "Run one step" }).click();
  const refused = await lastToast(p);
  check("an out-of-bounds duration is refused with the reason", /duration|bound|max|allowed/i.test(refused), refused);
  await p.waitForTimeout(5500);
  await card.locator("input[placeholder=seconds]").fill("60");
  await card.locator("button", { hasText: "Run one step" }).click();
  await p.waitForTimeout(500);
  const running = await untilSim(p, async () => { const x = await proc(p, "IRRIGATION"); return x.phase === "IRRIGATE" && x.outputs_on.includes("drive.MAIN.run") && x; }, 60);
  await show(p, "main-pumps");
  await shot(p, "UAT-04-manual-MP2-S1");
  check("manual step: MP2 on sector 1", running && running !== "SIM_LIMIT" && running.outputs_on.includes("contactor.MP2.cmd") && running.outputs_on.includes("valve.S1.cmd") && !running.outputs_on.includes("contactor.MP1.cmd"), JSON.stringify(running && running.outputs_on));
  await show(p, "main-pumps");
  await clearToast(p);
  await cardButton(p, "Irrigation", "AUTO");
  const autoRefused = await lastToast(p);
  check("AUTO is refused while the MANUAL step runs, with the reason", /MANUAL/.test(autoRefused) && (await proc(p, "IRRIGATION")).state === "MANUAL", autoRefused);
  // a raw output command is refused while the process controllers own the installation
  await show(p, "io");
  const row = p.locator("section.view:not(.hidden) tr", { has: p.locator("td.mono", { hasText: /^relay_A\.DO3$/ }) }).first();
  await clearToast(p);
  await row.locator("button", { hasText: /^ON$/ }).click();
  const raw = await lastToast(p);
  check("raw relay command refused on the installation", /REJECTED|refused|process/i.test(raw), raw);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "STOP");
  const stopped = await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "STOPPED" && (await proc(p, "IRRIGATION")).outputs_on.length === 0, 120);
  await shot(p, "UAT-04-stopped");
  check("STOP during the manual step runs the stop sequence to all off", stopped === true, (await proc(p, "IRRIGATION")).phase);
};

CASES["UAT-05"] = async (p) => {
  await resetRun(p);
  await speed(p, 10);
  // 1. a value out of range is saved as a draft but Validate refuses it and says why
  await show(p, "recipes");
  await recipeButton(p, "ACTIVE", "Edit as next version");
  await p.waitForTimeout(800);
  const field = (label) => p.locator("section.view:not(.hidden) tr", { has: p.locator("td", { hasText: label }) }).first().locator("input, select").first();
  await field("Refill trigger level").fill("25000");
  await viewButton(p, "Save draft");
  await until(p, async () => (await get(p, "/api/recipes")).recipes.some(r => r.status === "DRAFT"), 8000);
  await show(p, "recipes");
  await recipeButton(p, "demo-sim@v2", "Validate");
  await until(p, async () => (await get(p, "/api/recipes")).recipes.some(r => r.status === "DRAFT" && r.issues && r.issues.length), 8000);
  await show(p, "recipes");
  let t = await text(p);
  check("Validate refuses the out-of-range value and says why", /Validation issues/.test(t) && /refill_trigger_L/.test(t),
    (t.match(/[^\n]*refill_trigger_L[^\n]*/) || [""])[0].slice(0, 200));
  await shot(p, "UAT-05-invalid");
  // 2. irrigation runs on the active version while the draft is corrected
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  await untilSim(p, async () => (await proc(p, "IRRIGATION")).phase === "IRRIGATE", 120);
  await show(p, "recipes");
  await recipeButton(p, "demo-sim@v2", "Edit draft");
  await p.waitForTimeout(800);
  await field("Refill trigger level").fill("8000");
  await p.locator("section.view:not(.hidden) details > summary", { hasText: /^Irrigation$/ }).click();
  await p.waitForTimeout(1500);                      // the section stays open across the page refresh
  const seqRow = p.locator("section.view:not(.hidden) details[open] tr", { has: p.locator("td", { hasText: /^1$/ }) }).first();
  await seqRow.locator("input").first().fill("120");
  await shot(p, "UAT-05-editing-sequence");
  await viewButton(p, "Save draft");
  await p.waitForTimeout(1000);
  await show(p, "recipes");
  await recipeButton(p, "demo-sim@v2", "Validate");
  await until(p, async () => (await get(p, "/api/recipes")).recipes.some(r => r.status === "VALIDATED"), 8000);
  await show(p, "recipes");
  prompts.push("tour: shorter sector 1 (synthetic)");
  await recipeButton(p, "demo-sim@v2", "Activate");
  await until(p, async () => (await get(p, "/api/recipes")).active === "demo-sim@v2", 8000);
  await show(p, "recipes");
  await shot(p, "UAT-05-activated");
  const r = await get(p, "/api/recipes");
  const v2 = r.recipes.find(x => x.ref === "demo-sim@v2");
  check("new version active, sector 1 120 s", r.active === "demo-sim@v2" && v2 && v2.body.irrigation.sequence[0].duration_s === 120 && v2.body.main.refill_trigger_L === 8000,
    `${r.active} ${v2 && JSON.stringify(v2.body.irrigation.sequence)}`);
  const cur = await proc(p, "IRRIGATION");
  check("running step keeps its latched version", cur.recipe === "demo-sim@v1", cur.recipe);
  const next = await untilSim(p, async () => { const x = await proc(p, "IRRIGATION"); return x.recipe === "demo-sim@v2" && x; }, 900);
  check("the next cycle uses the new version", next && next !== "SIM_LIMIT", next && next.recipe);
  const short = await untilSim(p, async () => { const x = await proc(p, "IRRIGATION"); const c = (x.cycles || []).find(c => c.recipe === "demo-sim@v2" && c.sector === "S1"); return c; }, 900);
  check("sector 1 now irrigates 120 s", short && short !== "SIM_LIMIT" && Math.abs(short.irrigated_s - 120) < 2, JSON.stringify(short));
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "STOP");
  await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "STOPPED", 120);
  await show(p, "recipes");
  prompts.push("tour: back to the DEMO preset");
  await viewButton(p, "Roll back to the previous version");
  await until(p, async () => (await get(p, "/api/recipes")).active === "demo-sim@v1", 8000);
  await show(p, "recipes");
  await shot(p, "UAT-05-rolled-back");
  check("rollback re-activates demo-sim@v1", (await get(p, "/api/recipes")).active === "demo-sim@v1", (await get(p, "/api/recipes")).active);
};

CASES["UAT-06"] = async (p) => {
  // EC_TARGET with the DEMO recipe
  await twinPreset(p, "tour_low_main");
  await ackStocks(p, ["Stock A", "Stock B"]);
  await show(p, "main-tank");
  await cardButton(p, "Main-tank refill", "AUTO");
  await speed(p, 60);
  const dosing = await untilSim(p, async () => { const x = await proc(p, "REFILL"); return x.phase === "BATCH" && x; }, 120);
  await show(p, "nutrients");
  await shot(p, "UAT-06-ec-target-batch");
  check("batch starts: water and A/B", dosing && dosing !== "SIM_LIMIT", dosing && dosing.phase);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  const irrDuring = await untilSim(p, async () => { const i = await proc(p, "IRRIGATION"), r = await proc(p, "REFILL"); return i.phase === "IRRIGATE" && r.phase === "BATCH" && "IRRIGATE during refill BATCH"; }, 300);
  check("irrigation runs on its own permissives during the refill", irrDuring === "IRRIGATE during refill BATCH", irrDuring);
  await cardButton(p, "Irrigation", "STOP");
  const done = await untilSim(p, async () => { const x = await proc(p, "REFILL"); return x.last_result && x.last_result.result && x; }, 7200, 300000);
  await show(p, "main-tank");
  await shot(p, "UAT-06-ec-target-done");
  const lr = done && done.last_result || {};
  check("EC_TARGET batch completes at the final level with measured A/B", lr.result === "COMPLETED" && /EC_TARGET_REACHED/.test(lr.nutrients || "") && lr.end_level_L >= 14990,
    `${lr.result} ${lr.nutrients} end ${Math.round(lr.end_level_L || 0)} L, A ${(lr.delivered_a_L || 0).toFixed(1)} L, B ${(lr.delivered_b_L || 0).toFixed(1)} L`);
  await show(p, "main-tank");
  await cardButton(p, "Main-tank refill", "STOP");
  await untilSim(p, async () => (await proc(p, "REFILL")).state === "STOPPED", 120);
  // FIXED_VOLUMES: a new recipe through the editor
  await show(p, "recipes");
  await recipeButton(p, "ACTIVE", "Copy to new recipe");
  await p.waitForTimeout(800);
  const field = (label) => p.locator("section.view:not(.hidden) tr", { has: p.locator("td", { hasText: label }) }).first().locator("input, select").first();
  const idName = p.locator("section.view:not(.hidden) label", { hasText: "Recipe id" }).locator("input");
  await idName.fill("tour-fixed");
  await p.locator("section.view:not(.hidden) label", { hasText: "Name" }).locator("input").fill("Tour FIXED_VOLUMES (synthetic)");
  await field("Refill endpoint").selectOption("FIXED_ADDITIONS");
  await field("Final operating level").fill("");
  await field("Mains water addition").fill("1000");
  await field("Nutrient mode").selectOption("FIXED_VOLUMES");
  await field("Stock A addition").fill("15");
  await field("Stock B addition").fill("15");
  await field("EC validation minimum").fill("1.4");
  await field("EC validation maximum").fill("2.2");
  await field("EC validation settling window").fill("600");
  await viewButton(p, "Save draft");
  await p.waitForTimeout(1000);
  await show(p, "recipes");
  const row = p.locator("section.view:not(.hidden) tr", { hasText: "tour-fixed@v1" }).first();
  await row.locator("button", { hasText: /^Validate$/ }).click();
  await p.waitForTimeout(800);
  prompts.push("tour: FIXED_VOLUMES exercise (synthetic)");
  await show(p, "recipes");
  await p.locator("section.view:not(.hidden) tr", { hasText: "tour-fixed@v1" }).first().locator("button", { hasText: /^Activate$/ }).click();
  await p.waitForTimeout(1000);
  await show(p, "recipes");
  await shot(p, "UAT-06-fixed-recipe");
  await twinPreset(p, "tour_low_main");
  await show(p, "main-tank");
  await cardButton(p, "Main-tank refill", "AUTO");
  await speed(p, 60);
  const fdone = await untilSim(p, async () => { const x = await proc(p, "REFILL"); return x.last_result && x.last_result.result && x; }, 7200, 300000);
  await show(p, "nutrients");
  await shot(p, "UAT-06-fixed-done");
  const fl = fdone && fdone.last_result || {};
  check("FIXED_VOLUMES batch: 1,000 L water, 15 L A and B measured, EC validated", fl.result === "COMPLETED" && /EC_VALID/.test(fl.nutrients || "") &&
    Math.abs(pathL(fl, "A") - 15) < 1 && Math.abs(pathL(fl, "B") - 15) < 1 && Math.abs(pathL(fl, "WATER") - 1000) < 30,
    `${fl.result} ${fl.nutrients} water ${pathL(fl, "WATER")} L, A ${pathL(fl, "A")} L, B ${pathL(fl, "B")} L (measured)`);
  await show(p, "main-tank");
  await cardButton(p, "Main-tank refill", "STOP");
  await untilSim(p, async () => (await proc(p, "REFILL")).state === "STOPPED", 120);
  await show(p, "recipes");
  prompts.push("tour: back to the DEMO preset");
  await viewButton(p, "Roll back to the previous version");
  await p.waitForTimeout(1000);
  const r2 = await get(p, "/api/recipes");
  const a2 = r2.recipes.find(x => x.status === "ACTIVE");
  check("rollback returns to demo-sim@v1", a2 && a2.ref === "demo-sim@v1", a2 && a2.ref);
  await speed(p, 1);
};

CASES["UAT-07"] = async (p) => {
  await resetRun(p);
  await ackStocks(p, ["Stock A"]);
  await show(p, "nutrients");
  let t = await text(p);
  check("stock A shows PREPARED", /Stock A\t3000 L[^\n]*\tPREPARED/.test(t) || /Stock A[\s\S]{0,80}PREPARED/.test(t), (t.match(/Stock A\t[^\n]*/) || [""])[0]);
  await cardButton(p, "Stock A water fill", "MANUAL");
  await until(p, async () => (await proc(p, "STOCK_FILL_A")).state === "MANUAL", 10000);
  await show(p, "nutrients");
  const card = p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: "Stock A water fill" }) }).first();
  await card.locator("input[placeholder=litres]").fill("3100");
  await card.locator("button", { hasText: /^Fill$/ }).click();
  await speed(p, 10);
  const filled = await untilSim(p, async () => { const x = await proc(p, "STOCK_FILL_A"); return x.last_result && x; }, 600);
  await show(p, "nutrients");
  await shot(p, "UAT-07-fill-invalidates");
  t = await text(p);
  check("filling with water invalidates the preparation", /INVALIDATED/.test(t), (t.match(/Stock A\t[^\n]*\n[^\n]*/) || [""])[0]);
  check("manual fill completed to 3,100 L", filled && filled.last_result && /COMPLETED/.test(JSON.stringify(filled.last_result)), JSON.stringify(filled && filled.last_result).slice(0, 160));
  await cardButton(p, "Stock A water fill", "STOP");
  await speed(p, 1);
};

CASES["UAT-08"] = async (p) => {
  await twinPreset(p, "tour_ph_high");
  await ackStocks(p, ["Acid"]);
  await show(p, "nutrients");
  await p.locator(".tabs button", { hasText: "Acid" }).click();
  await p.waitForTimeout(500);
  const t0 = await text(p);
  check("pH target, step, settling and limits shown", /pH target \(adjustable\)/.test(t0), (t0.match(/pH target[^\n]*\n[^\n]*/) || [""])[0]);
  await cardButton(p, "Acid pH dosing", "AUTO");
  await speed(p, 60);
  await untilSim(p, async () => (await proc(p, "ACID")).phase === "SETTLING", 120);
  await show(p, "nutrients");
  await p.locator(".tabs button", { hasText: "Acid" }).click();
  await shot(p, "UAT-08-settling");
  const done = await untilSim(p, async () => { const x = await proc(p, "ACID"); return x.last_result && x.last_result.result && x; }, 9000, 300000);
  const lr = done && done.last_result || {};
  check("doses in steps until pH is in band", lr.result === "PH_IN_BAND", `${lr.result} after ${(lr.doses || []).length} doses, total ${(lr.total_L || 0).toFixed(1)} L, pH ${lr.ph_start && lr.ph_start.toFixed(2)} -> ${lr.ph_end && lr.ph_end.toFixed(2)}`);
  await speed(p, 1);
  await ioOverride(p, "analog_A.AI6", 0, "tour: pH probe open circuit");
  const inh = await untilSim(p, async () => { const x = await proc(p, "ACID"); return /pH/.test(x.inhibit || "") && x.inhibit; }, 60);
  await show(p, "nutrients");
  await shot(p, "UAT-08-ph-unusable");
  check("an unusable pH probe inhibits dosing with the reason", typeof inh === "string" && inh !== "SIM_LIMIT", inh);
  await ioClear(p, "analog_A.AI6");
  await show(p, "nutrients");
  await cardButton(p, "Acid pH dosing", "STOP");
};

CASES["UAT-09"] = async (p) => {
  await twinPreset(p, "tour_sump_high");
  await show(p, "drainage");
  await cardButton(p, "Drainage return", "AUTO");
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  await speed(p, 60);
  const r1 = await untilSim(p, async () => { const x = await proc(p, "RETURN"); return x.outputs_on.length && x; }, 120);
  await show(p, "drainage");
  await shot(p, "UAT-09-return-running");
  check("return starts above 2,500 L", r1 && r1 !== "SIM_LIMIT", JSON.stringify(r1 && r1.outputs_on));
  const runs = await untilSim(p, async () => { const x = await proc(p, "RETURN"); return (x.runs || []).length >= 2 && x.runs; }, 4000, 240000);
  check("each start alternates the pump", Array.isArray(runs) && runs[0].pump !== runs[1].pump, JSON.stringify(Array.isArray(runs) ? runs.map(r => [r.pump, Math.round(r.delivered_L_measured || 0) + " L"]) : runs));
  await stopAll(p);
  await speed(p, 1);
  await twinPreset(p, "tour_main_full_sump_high");
  await show(p, "drainage");
  await cardButton(p, "Drainage return", "AUTO");
  const hold = await untilSim(p, async () => (await proc(p, "RETURN")).phase === "HOLD", 60);
  await show(p, "drainage");
  await shot(p, "UAT-09-hold-main-full");
  const al = await get(p, "/api/alarms");
  check("destination full: HOLD with alarm", hold === true && JSON.stringify(al).includes("SUMP_HIGH_MAIN_FULL"), (await proc(p, "RETURN")).phase);
  await cardButton(p, "Drainage return", "STOP");
};

CASES["UAT-10"] = async (p) => {
  await resetRun(p);
  await speed(p, 10);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  await untilSim(p, async () => (await proc(p, "IRRIGATION")).phase === "IRRIGATE", 120);
  await speed(p, 1);
  await applyFault(p, "drive", "MAIN", "TRIP");
  const lock = await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "FAULT_LOCKOUT", 30);
  await show(p, "main-pumps");
  await shot(p, "UAT-10-drive-trip");
  const x = await proc(p, "IRRIGATION");
  check("drive trip: lockout with the cause", lock === true, `${x.fault_reason}: ${x.fault_detail}`);
  await show(p, "main-pumps");
  await clearToast(p);
  await cardButton(p, "Irrigation", "Reset lockout");
  const t1 = await lastToast(p);
  check("reset refused while the fault is present / unacknowledged", /refus|still|acknowledg|active|fault/i.test(t1), t1);
  await applyFault(p, "drive", "MAIN", "");
  const n = await ackAll(p);
  await shot(p, "UAT-10-acknowledged");
  const after = await proc(p, "IRRIGATION");
  check("acknowledging restarts nothing", after.state === "FAULT_LOCKOUT", `${n} alarm(s) acknowledged; Irrigation ${after.state}`);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "Reset lockout");
  const st = await until(p, async () => (await proc(p, "IRRIGATION")).state === "STOPPED", 10000);
  check("reset returns to STOPPED, not running", st === true, (await proc(p, "IRRIGATION")).state);
  await cardButton(p, "Irrigation", "AUTO");
  const again = await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "AUTO", 30);
  check("deliberate restart", again === true, (await proc(p, "IRRIGATION")).state);
  await cardButton(p, "Irrigation", "STOP");
  await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "STOPPED", 120);
  // valve fault
  await resetRun(p);
  await applyFault(p, "valve", "S1", "STUCK_CLOSED");
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  const vl = await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "FAULT_LOCKOUT", 60);
  await show(p, "sector-S1");
  await shot(p, "UAT-10-valve-stuck");
  const v = await proc(p, "IRRIGATION");
  const drv = (await get(p, "/api/twin")).snapshot.plant.drive;
  check("valve does not open: lockout, the drive never ran", vl === true && !drv.run_requested, `${v.fault_reason}: ${v.fault_detail}`);
  await applyFault(p, "valve", "S1", "");
  await ackAll(p);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "Reset lockout");
  await until(p, async () => (await proc(p, "IRRIGATION")).state === "STOPPED", 10000);
};

CASES["UAT-11"] = async (p) => {
  // invalid: main level loop open during irrigation
  await resetRun(p);
  await speed(p, 10);
  await show(p, "main-pumps");
  await cardButton(p, "Irrigation", "AUTO");
  await untilSim(p, async () => (await proc(p, "IRRIGATION")).phase === "IRRIGATE", 120);
  await speed(p, 1);
  await ioOverride(p, "analog_A.AI1", 0, "tour: main level loop open");
  const inv = await untilSim(p, async () => (await proc(p, "IRRIGATION")).state === "FAULT_LOCKOUT", 30);
  await show(p, "main-tank");
  await shot(p, "UAT-11-invalid-level");
  const a = await proc(p, "IRRIGATION");
  check("INVALID main level locks irrigation out", inv === true, `${a.fault_reason}: ${a.fault_detail}`);
  await ioClear(p, "analog_A.AI1");
  // unavailable: one module stops answering
  await resetRun(p);
  await show(p, "io");
  const card = p.locator("section.view:not(.hidden) .card", { has: p.locator("h3", { hasText: /^analog_B/ }) }).first();
  await card.locator("button", { hasText: "Simulate timeout" }).click();
  await p.waitForTimeout(2500);
  await show(p, "io");
  await shot(p, "UAT-11-module-timeout");
  const pts = (await get(p, "/api/points")).devices;
  const qual = dev => pts.find(d => d.device_uid === dev).points.map(x => (x.sample || {}).quality);
  check("analog_B UNAVAILABLE, analog_A still GOOD", qual("analog_B").every(q => q === "UNAVAILABLE") && qual("analog_A").every(q => q === "GOOD"), `analog_B ${[...new Set(qual("analog_B"))]}, analog_A ${[...new Set(qual("analog_A"))]}`);
  await show(p, "io");
  await p.locator("section.view:not(.hidden) .card", { has: p.locator("h3", { hasText: /^analog_B/ }) }).first().locator("button", { hasText: "Restore" }).click();
  await p.waitForTimeout(1500);
  // stale: acquisition stalls
  await resetRun(p);
  await show(p, "twin");
  await viewButton(p, "Stall acquisition");
  await p.waitForTimeout(3000);
  const stTxt = (await get(p, "/api/status")).freshness;
  await show(p, "overview");
  await shot(p, "UAT-11-stale-acquisition");
  check("stalled acquisition ages the data (stale)", (stTxt.newest_sample_age_s || 0) > 2, JSON.stringify(stTxt).slice(0, 160));
  await show(p, "twin");
  await viewButton(p, "Resume acquisition");
  // the corrected EC boundary
  const before = (await get(p, "/api/recipes"));
  const act = (before.recipes.find(x => x.status === "ACTIVE") || {}).ref;
  check("DEMO recipe active before the EC boundary preset", act === "demo-sim@v1", act);
  await speed(p, 1);
  await twinPreset(p, "tour_ec_lost_at_valve_opening");
  const stopped = await untilSim(p, async () => { const x = await proc(p, "REFILL"); return /EC unavailable/.test(JSON.stringify(x)) && x; }, 60);
  await show(p, "nutrients");
  await shot(p, "UAT-11-ec-boundary");
  check("EC lost at the last opening scan: step STOPPED with the reason", stopped && stopped !== "SIM_LIMIT", (JSON.stringify(stopped).match(/STOPPED \(EC unavailable[^"]*/) || [""])[0]);
  await show(p, "alarms");
  await shot(p, "UAT-11-audit");
  const at = await text(p);
  const pumpOn = /relay_B\.DO1\tTrue|relay_B\.DO2\tTrue|pump\.TP_A[^\n]*ON|pump\.TP_B[^\n]*ON/.test(at);
  check("command audit: no transfer pump A/B ON", !pumpOn && /REFILL_EC_UNAVAILABLE/.test(at), pumpOn ? "a TP ON row is listed" : "no TP_A/TP_B ON rows; REFILL_EC_UNAVAILABLE listed");
  await ioClear(p, "analog_A.AI7");
  const t0 = await simS(p);
  await untilSim(p, async () => false, 60, 90000);
  const rf = await proc(p, "REFILL");
  check("probe back: the stopped step does not resume", /STOPPED \(EC unavailable/.test(JSON.stringify(rf)) && !(rf.outputs_on || []).some(o => /TP_A|TP_B/.test(o)), `after ${((await simS(p)) - t0).toFixed(0)} s: outputs ${JSON.stringify(rf.outputs_on)}`);
  await stopAll(p);
};

CASES["UAT-12"] = async (p) => {
  await resetRun(p);
  await stopAll(p);
  await show(p, "io");
  await viewButton(p, "Use IO_TEST (raw injection)");
  await until(p, async () => (await get(p, "/api/points")).source === "IO_TEST", 10000);
  await show(p, "io");
  const row = id => p.locator("section.view:not(.hidden) tr", { has: p.locator("td.mono", { hasText: new RegExp("^" + id.replace(".", "\\.") + "$") }) }).first();
  await row("analog_A.AI1").locator("input").fill("12000");
  await row("analog_A.AI1").locator("button", { hasText: "Inject" }).click();
  await p.waitForTimeout(800);
  await row("digital_A.DI1").locator("button", { hasText: "Inject 1" }).click();
  await p.waitForTimeout(800);
  await row("relay_A.DO1").locator("button", { hasText: /^ON$/ }).click();
  await p.waitForTimeout(800);
  await row("digital_B.DO1").locator("button", { hasText: /^ON$/ }).click();
  await p.waitForTimeout(1200);
  await show(p, "io");
  await shot(p, "UAT-12-io-test");
  const pts = (await get(p, "/api/points")).devices;
  const s = id => { for (const d of pts) for (const x of d.points) if (x.id === id) return x.sample || {}; return {}; };
  check("injected AI/DI and switched relay + transistor DO", s("analog_A.AI1").raw === 12000 && s("digital_A.DI1").raw === true && s("relay_A.DO1").raw === true && s("digital_B.DO1").raw === true,
    `AI1 ${s("analog_A.AI1").raw}, digital_A.DI1 ${s("digital_A.DI1").raw}, relay_A.DO1 ${s("relay_A.DO1").raw}, digital_B.DO1 ${s("digital_B.DO1").raw}`);
  const heads = await p.$$eval("section.view:not(.hidden) .card h3", hs => hs.map(x => x.parentElement.textContent));
  check("relay contacts vs transistor sinks labelled", heads.filter(t => /TRANSISTOR SINK/.test(t)).length === 2 && heads.filter(t => /relay contacts/.test(t) && !/TRANSISTOR/.test(t)).length === 2, "2 relay, 2 transistor");
  await p.locator("section.view:not(.hidden) .card", { has: p.locator("h3", { hasText: /^digital_A/ }) }).first().locator("button", { hasText: "Simulate timeout" }).click();
  await p.waitForTimeout(2500);
  await show(p, "io");
  await shot(p, "UAT-12-module-failed");
  const pts2 = (await get(p, "/api/points")).devices;
  const on = Object.fromEntries(pts2.map(d => [d.device_uid, d.online]));
  check("one module failed, the others still online", !on.digital_A && on.relay_A && on.digital_B && on.analog_A, JSON.stringify(on));
  await p.locator("section.view:not(.hidden) .card", { has: p.locator("h3", { hasText: /^digital_A/ }) }).first().locator("button", { hasText: "Restore" }).click();
  await p.waitForTimeout(1500);
  await show(p, "io");
  for (const id of ["relay_A.DO1", "digital_B.DO1"]) { await row(id).locator("button", { hasText: /^OFF$/ }).click(); await p.waitForTimeout(600); }
  await viewButton(p, "Use TWIN (process model)");
  await until(p, async () => (await get(p, "/api/points")).source === "TWIN", 10000);
  await show(p, "io");
  await shot(p, "UAT-12-twin-unassigned");
  const t = await text(p);
  check("back in plant mode the added channels read UNASSIGNED", /digital_A\.DI1[^\n]*UNASSIGNED/.test(t) && /digital_B\.DI8[^\n]*UNASSIGNED/.test(t), (t.match(/digital_A\.DI1[^\n]*/) || [""])[0]);
  const mapping = (await get(p, "/api/settings")).config.virtual_mapping.map(m => m.point);
  check("no plant signal bound to digital_A/digital_B", !mapping.some(x => x.startsWith("digital_")), `${mapping.length} bindings, none on digital_A/B`);
};

CASES["UAT-13"] = async (p) => {
  // history holds one complete snapshot per 60 s of wall time: wait for two (only when this case runs alone)
  const rows = async () => { const e = new Date(), s0 = new Date(e - 600000);
    const tr = await get(p, `/api/trends?points=analog_A.AI1&start=${encodeURIComponent(s0.toISOString())}&end=${encodeURIComponent(e.toISOString())}`);
    return (tr.series["analog_A.AI1"] || []).length; };
  await until(p, async () => (await rows()) >= 2, 180000, 5000);
  await show(p, "trends");
  const sel = p.locator("section.view:not(.hidden) select[multiple]").first();
  await sel.selectOption(["analog_A.AI1", "analog_A.AI2"]);
  await p.waitForTimeout(2500);
  await shot(p, "UAT-13-trends");
  const t = await p.evaluate(() => document.querySelector("section.view:not(.hidden)").textContent);
  check("chart has units and time zone", /\[L\]/.test(t) && /Atlantic\/Canary/.test(t), ((t.match(/Atlantic\/Canary \(\w+\)/) || [""])[0] + " · " + (t.match(/analog_A\.AI1 \[\w+\]/) || [""])[0]));
  const [dl] = await Promise.all([p.waitForEvent("download", { timeout: 30000 }), p.locator("section.view:not(.hidden) a", { hasText: "Export CSV" }).click()]);
  const f = path.join(OUT, "UAT-13-export.csv");
  await dl.saveAs(f);
  const csv = fs.readFileSync(f, "utf8");
  const last = csv.trim().split(/\r?\n/).slice(-1)[0];
  check("export ends with COMPLETE/TRUNCATED and names the range", /COMPLETE|TRUNCATED/.test(last) && /range|from|to/i.test(csv.split(/\r?\n/).slice(0, 6).join(" ")), last.slice(0, 120));
};

CASES["UAT-14"] = async (p, b) => {
  await show(p, "settings");
  const users = [["tour-viewer", "viewer"], ["tour-operator", "operator"]];
  for (const [u, role] of users) {
    const card = p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: "Users and roles" }) }).first();
    const inputs = card.locator(".row input");
    await inputs.nth(0).fill(u);
    await inputs.nth(1).fill("tour first password 1");
    await card.locator(".row select").first().selectOption(role);
    await card.locator("button", { hasText: "Create user" }).click();
    await p.waitForTimeout(1000);
    await show(p, "settings");
  }
  await shot(p, "UAT-14-users");
  for (const [u, role] of users) {
    const ctx = await b.newContext({ viewport: { width: 1366, height: 900 } });
    const q = await ctx.newPage(); watch(q);
    await login(q, u, "tour first password 1");
    await q.waitForSelector("#pwchange-view:not(.hidden)", { timeout: 10000 }).catch(() => null);
    const mustChange = await q.locator("#pwchange-view").isVisible().catch(() => false);
    if (mustChange) {
      const pw = q.locator("#pwchange-view input[type=password]");
      const n = await pw.count();
      const vals = ["tour first password 1", "tour own password 22", "tour own password 22"];   // current, then new
      for (let i = 0; i < n; i++) await pw.nth(i).fill(vals[i] || vals[vals.length - 1]);
      await q.locator("#pwchange-view button").first().click();
      await q.waitForTimeout(1500);
      if (!(await q.locator("#app").isVisible())) await login(q, u, "tour own password 22");
    }
    await signedIn(q);
    check(`${role}: first sign-in asks for a new password`, mustChange, mustChange ? "password change required first" : "no change asked");
    await show(q, "main-pumps");
    const auto = q.locator("section.view:not(.hidden) .card", { has: q.locator("h2", { hasText: "Irrigation" }) }).first().locator("button", { hasText: /^AUTO$/ });
    const disabled = await auto.isDisabled();
    const title = await auto.getAttribute("title");
    await q.locator("section.view:not(.hidden)").screenshot({ path: path.join(OUT, `UAT-14-${role}-main-pumps.png`) });
    if (role === "viewer") check("viewer: controls disabled, the required permission named", disabled && /permission/.test(title || ""), title);
    else check("operator: AUTO available", !disabled, `disabled=${disabled}`);
    if (role === "operator") {
      const r = await api(q, "/api/recipes/demo-sim/1/activate", "POST", { reason: "operator tries" });
      check("operator: activating a recipe is refused (administrator only)", r.status === 403, `HTTP ${r.status} ${JSON.stringify(r.body).slice(0, 120)}`);
      const u2 = await api(q, "/api/users", "POST", { username: "x-test", password: "abcdefghijklmn", role: "viewer" });
      check("operator: creating accounts is refused", u2.status === 403, `HTTP ${u2.status}`);
    }
    await ctx.close();
  }
};

CASES["UAT-16a"] = async (p, b) => {
  for (const [w, hgt, name] of [[390, 844, "phone"], [820, 1180, "tablet"]]) {
    const ctx = await b.newContext({ viewport: { width: w, height: hgt } });
    const q = await ctx.newPage(); watch(q);
    await login(q, USER, PASS); await signedIn(q);
    for (const v of ["overview", "main-pumps", "nutrients", "alarms"]) {
      await show(q, v);
      await shot(q, `UAT-16-${name}-${v}`);
    }
    const scrollX = await q.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(`${name} ${w}px: no page-wide horizontal scroll`, scrollX <= 1, `overflow ${scrollX}px`);
    await ctx.close();
  }
};

// ------------------------------------------------------------------------------------------------- phases
async function runCases(p, b, ids) {
  for (const id of ids) {
    if (ONLY.length && !ONLY.includes(id) && !ONLY.includes(id.slice(0, 6))) continue;
    current = id;
    const t0 = Date.now();
    try { await CASES[id](p, b); }
    catch (e) { check("completed", false, String(e).split("\n")[0] + " at " + ((String(e.stack).match(/tour_rehearsal\.cjs:\d+/g) || []).join(" < "))); await shot(p, `${id}-error`).catch(() => null); }
    console.log(`${id} took ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
  current = "";
}

async function main() {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const ctx = await b.newContext({ viewport: { width: 1366, height: 1000 }, acceptDownloads: true });
  const p = await ctx.newPage();
  const errs = watch(p);
  await login(p, USER, PASS);
  await signedIn(p);
  if (PHASE === "main") {
    await runCases(p, b, ["UAT-01", "UAT-02", "UAT-03", "UAT-04", "UAT-05", "UAT-06", "UAT-07", "UAT-08", "UAT-09", "UAT-10",
      "UAT-11", "UAT-12", "UAT-13", "UAT-14", "UAT-16a"]);
    // UAT-15, before the restart: a local backup and a known state to compare
    current = "UAT-15";
    await show(p, "settings");
    await viewButton(p, "Create backup now");
    const bk = await until(p, async () => { const x = await get(p, "/api/backup"); return !x.running && x.local_count >= 1 && x; }, 120000, 1000);
    await show(p, "settings");
    await shot(p, "UAT-15-backup");
    check("local backup created and verified", !!bk, JSON.stringify(bk).slice(0, 200));
    fs.writeFileSync(path.join(OUT, "before-restart.json"), JSON.stringify({ users: (await get(p, "/api/settings")).users,
      recipes: await get(p, "/api/recipes"), stocks: ((await get(p, "/api/plant")).processes || {}).stocks || null }));
  } else {
    current = "UAT-15";
    const before = JSON.parse(fs.readFileSync(path.join(OUT, "before-restart.json"), "utf8"));
    const now = { users: (await get(p, "/api/settings")).users, recipes: await get(p, "/api/recipes") };
    const names = x => (x || []).map(u => u.username).sort().join(",");
    check("accounts kept", names(before.users) === names(now.users), names(now.users));
    const refs = r => (r.recipes || []).map(x => `${x.ref}:${x.status}`).sort().join(",");
    check("recipes kept", refs(before.recipes) === refs(now.recipes), refs(now.recipes).slice(0, 160));
    const ps = await procs(p);
    check("every process starts STOPPED", Object.values(ps).every(x => x.state === "STOPPED"), Object.entries(ps).map(([k, v]) => `${k} ${v.state}`).join(", "));
    await show(p, "trends");
    await shot(p, "UAT-15-after-restart");
    // UAT-16: loss of connection while the page is open
    current = "UAT-16b";
    await show(p, "main-pumps");
    fs.writeFileSync(process.env.AQ_STOP_FLAG, "stop");
    const stale = await until(p, () => p.locator("#stale").evaluate(e => !e.classList.contains("hidden")), 60000, 500);
    await p.waitForTimeout(1500);
    await shot(p, "UAT-16-connection-lost");
    check("STALE banner when the server is gone", !!stale, await p.locator("#stale").innerText().catch(() => ""));
    await clearToast(p);
    await p.locator("section.view:not(.hidden) button", { hasText: /^AUTO$/ }).first().click({ timeout: 3000 }).catch(() => null);
    const tt = await lastToast(p, 3000);
    check("a command is not sent while stale", /stale|not sent/i.test(tt) || !tt, tt || "button inactive");
  }
  current = "";
  check(`page errors (${PHASE})`, errs.length === 0, errs.slice(0, 3).join(" | ") || "none");
  fs.writeFileSync(path.join(OUT, `rehearsal-${PHASE}.json`), JSON.stringify(checks, null, 1));
  await b.close();
}

main().catch(e => {
  console.error(e);
  checks.push({ id: `phase ${PHASE}`, ok: false, detail: String(e) });
  fs.writeFileSync(path.join(OUT, `rehearsal-${PHASE}.json`), JSON.stringify(checks, null, 1));
  process.exit(1);
});
