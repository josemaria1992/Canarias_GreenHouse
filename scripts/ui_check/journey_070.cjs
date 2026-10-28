// 0.7.0 operator journey in headless Chromium (REL-02, RTE-09, RBAC-01, UX-01..04), simulation only.
//
// Driven by run_journey_070.py: a throwaway live service and twin, an administrator (type setup), an operator (the
// journey) and a viewer. Real mouse gestures (with intermediate moves), clicks and typing; every result is also
// read back through the API. Screenshots go to AQ_OUT (the walkthrough uses them). None of this is physical
// verification.
//
// Environment: AQ_BASE, AQ_TWIN, AQ_ADMIN, AQ_OPER, AQ_VIEW ("user:password"), AQ_OUT, PLAYWRIGHT_MODULE, CHROMIUM_PATH.
"use strict";
const fs = require("fs");
const path = require("path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const BASE = process.env.AQ_BASE, TWIN = process.env.AQ_TWIN, OUT = process.env.AQ_OUT;
const cred = k => { const [u, ...p] = process.env[k].split(":"); return { user: u, pass: p.join(":") }; };
const checks = [];
function check(id, ok, detail) {
  checks.push({ id, ok: !!ok, detail: String(detail) });
  console.log(`${ok ? "PASS" : "FAIL"} ${id}: ${detail}`);
}
const shot = (p, name, opts = {}) => p.screenshot(Object.assign({ path: path.join(OUT, name + ".png") }, opts));
async function shotOf(loc, name) {      // one element, cut from the whole page: the fixed banner is not stitched into it
  const pg = loc.page(), b = await loc.boundingBox(), [sx, sy] = await pg.evaluate(() => [scrollX, scrollY]);
  return pg.screenshot({ path: path.join(OUT, name + ".png"), fullPage: true, clip: { x: b.x + sx, y: b.y + sy, width: b.width, height: b.height } });
}
const api = (p, url, method = "GET", body, twin = false) => p.evaluate(async ([url, method, body, twin]) => {
  let headers = {};
  if (method !== "GET") {
    const s = await (await fetch(twin ? "api/auth/session" : "/api/auth/session")).json();
    headers = { "Content-Type": "application/json", "X-CSRF-Token": s.csrf_token, "X-Runtime-Id": s.runtime_id || "" };
  }
  const r = await fetch(url, { method, credentials: "same-origin", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const ct = r.headers.get("content-type") || "";
  return { status: r.status, body: ct.includes("json") ? await r.json() : await r.text() };
}, [url, method, body, twin]);
const pause = ms => new Promise(r => setTimeout(r, ms));

async function login(p, who) {
  await p.goto(BASE + "/");
  await p.fill('input[name="username"]', who.user);
  await p.fill('input[name="password"]', who.pass);
  await p.press('input[name="password"]', "Enter");
  await p.waitForFunction(() => !document.getElementById("app").classList.contains("hidden"), null, { timeout: 15000 });
}
async function logout(p) {
  await p.click("#logout");
  await p.waitForSelector("#login-view:not(.hidden)");
}
async function navVisible(p) {
  return p.$$eval("#nav [data-nav]", xs => xs.filter(x => !x.classList.contains("hidden")).map(x => x.dataset.nav));
}
async function drag(p, from, to, steps = 12) {
  await p.mouse.move(from.x, from.y);
  await p.mouse.down();
  await p.mouse.move(to.x, to.y, { steps });
  await p.mouse.up();
}
async function center(loc) { const b = await loc.boundingBox(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; }

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const p = await ctx.newPage();
  try {
    // ------------------------------------------------------------------ administrator: activate the type templates
    await login(p, cred("AQ_ADMIN"));
    await api(p, "/api/equipment/types/seed", "POST");
    for (const t of (await api(p, "/api/equipment")).body.types.filter(t => t.status === "DRAFT"))
      await api(p, "/api/equipment/types/activate", "POST", { type_id: t.type_id, version: t.version });
    check("RBAC-01 admin navigation", (await navVisible(p)).length === 10, (await navVisible(p)).join(","));
    await logout(p);

    // ------------------------------------------------------------------ viewer
    await login(p, cred("AQ_VIEW"));
    const vnav = await navVisible(p);
    check("RBAC-01 viewer sees Overview, Programs, Alarms only", vnav.join(",") === "overview,programs,alarms", vnav.join(","));
    await shot(p, "06-viewer-menu");
    await p.goto(BASE + "/#blocks");
    await p.waitForSelector("[data-denied]");
    check("RBAC-01 an old deep link explains the role needed", (await p.textContent("[data-denied]")).includes("operator or administrator"), "blocks link as viewer");
    check("RBAC-02 viewer API refused", (await api(p, "/api/topology")).status === 403, "GET /api/topology");
    await logout(p);

    // ------------------------------------------------------------------ operator: equipment from forms
    await login(p, cred("AQ_OPER"));
    const onav = await navVisible(p);
    check("RBAC-01 operator navigation", onav.join(",") === "overview,blocks,twin,hw-add,programs,history,alarms", onav.join(","));
    await shot(p, "01-operator-menu");
    await p.goto(BASE + "/#equipment");
    const parts = [["water_source", "Mains", "mains"], ["tank", "Main tank", "tank-1"], ["pump", "Main pump", "pump-1"],
      ["flow", "Flow meter", "meter-1"], ["valve", "Sector valve", "valve-1"], ["sector", "Sector 1", "sector-1"]];
    for (const [type, name, id] of parts) {
      await p.selectOption('select[aria-label="Type"]', type);
      await p.fill('input[placeholder="e.g. LED 1 on digital_A"]', name);
      await p.fill('input[placeholder="led-1"]', id);
      await p.click('button:text-is("Create")');
      await p.waitForSelector(`[data-item-details="${id}"]`);
    }
    async function details(id, fill) {
      const d = p.locator(`[data-item-details="${id}"]`);
      if (!(await d.evaluate(el => el.open))) await d.locator("summary").click();
      await fill(d);
      await d.evaluate(el => el.querySelectorAll(".inline-msg").forEach(m => m.dataset.old = "1"));   // the last save's
      await d.locator('button:text-is("Save details")').click();
      const m = d.locator(".inline-msg:not([data-old])");
      await m.waitFor();
      return (await m.textContent()) || "";
    }
    let msg = await details("tank-1", async d => {
      await d.locator('input[aria-label="Capacity"]').fill("200");
      await d.locator('input[aria-label="Water at the start of a simulation"]').fill("100");
      await d.locator('input[aria-label="Level reading when full"]').fill("200");
      await d.locator('select[aria-label="Where the numbers come from"]').selectOption("DEMO");
    });
    await shotOf(p.locator('[data-item-details="tank-1"]'), "02-tank-form");
    const tank = (await api(p, "/api/equipment")).body.items.filter(i => i.item_id === "tank-1").pop();
    check("EQ-03 typed tank form saved", msg.includes("Saved") && tank.body.properties.capacity_l === 200, msg);
    await details("pump-1", async d => { await d.locator('input[aria-label="Rated flow"]').fill("20"); });
    await details("mains", async d => {
      await d.locator('input[aria-label="Supply rate in a simulation"]').fill("60");
      await d.locator('select[aria-label="Where the numbers come from"]').selectOption("DEMO");
    });
    msg = await details("meter-1", async d => {
      await d.locator('input[aria-label="Flow rate value at maximum"]').fill("100");
      await d.locator('input[aria-label="Flow rate unit"]').fill("L/min");
      await d.locator('select[aria-label="Flow rate provenance"]').selectOption("DEMO");
    });
    const meter = (await api(p, "/api/equipment")).body.items.filter(i => i.item_id === "meter-1").pop();
    check("EQ-03 typed scaling saved", meter.body.scaling.flow && meter.body.scaling.flow.unit === "L/min", JSON.stringify(meter.body.scaling));
    const bad = await details("pump-1", async d => { await d.locator('input[aria-label="Rated flow"]').fill("-5"); });
    check("EQ-03 a wrong value is refused beside the button", /below the minimum|minimum/.test(bad), bad);
    await details("pump-1", async d => { await d.locator('input[aria-label="Rated flow"]').fill("20"); });
    const board = (await api(p, "/api/board")).body;
    const dos = board.modules.flatMap(m => m.points).filter(x => x.kind === "DO").map(x => x.id);
    const ais = board.modules.flatMap(m => m.points).filter(x => x.kind === "AI").map(x => x.id);
    for (const [iid, label, choices] of [["pump-1", "Main pump Start", dos.slice(-2)], ["valve-1", "Sector valve Open", dos.slice(-1)], ["meter-1", "Flow meter Flow rate", ais.slice(-1)]]) {
      const sel = p.locator(`select[aria-label="Channel for ${label}"]`);
      await sel.selectOption(choices[0]);
      await sel.locator("xpath=ancestor::tr[1]").locator('button:text-is("Set")').click();
      await pause(600);
    }
    const binds = (await api(p, "/api/equipment")).body.bindings;
    check("EQ-02 an operator proposes channels (PROPOSED, no output right)", binds.length === 3 && binds.every(b => b.status === "PROPOSED"), binds.map(b => b.item_id + ":" + b.status).join(","));

    // ------------------------------------------------------------------ the drawing, with real mouse gestures
    await p.goto(BASE + "/#blocks");
    await p.click('button.tab:text-is("Installation drawing")');
    await p.waitForSelector(".hed-canvas");
    for (const name of ["Mains", "Main tank", "Main pump", "Flow meter", "Sector valve", "Sector 1"]) {
      await p.locator(".hed-palette li", { hasText: name }).locator('button:text-is("Place")').click();
    }
    const pos = { "mains": [40, 260], "tank-1": [160, 200], "pump-1": [360, 300], "meter-1": [480, 300], "valve-1": [600, 300], "sector-1": [740, 290] };
    const canvas = p.locator(".hed-canvas");
    const toScreen = async ([x, y]) => p.evaluate(([x, y]) => {
      const svg = document.querySelector(".hed-canvas"); const pt = new DOMPoint(x, y).matrixTransform(svg.getScreenCTM());
      return { x: pt.x, y: pt.y };
    }, [x, y]);
    let moved = 0;
    for (const [id, [x, y]] of Object.entries(pos)) {
      const sym = p.locator(`[data-node="${id}"] rect`).first();
      await sym.scrollIntoViewIfNeeded();
      const from = await center(sym);
      const box = await sym.boundingBox();
      const target = await toScreen([x, y]);
      await drag(p, from, { x: target.x + box.width / 2, y: target.y + box.height / 2 });
      const s = await p.evaluate(id => window.AqUI.S.blocksUi.editor.get().diagram.symbols.find(q => q.node === id), id);
      if (Math.abs(s.x - x) <= 20 && Math.abs(s.y - y) <= 20) moved++;
    }
    check("RTE-04 symbols dragged with the mouse land where dropped (grid snapped)", moved === 6, `${moved}/6`);
    await p.click('button:text-is("Connect pipe")');
    for (const [a, b] of [["mains.out", "tank-1.in"], ["tank-1.out", "pump-1.in"], ["pump-1.out", "meter-1.in"], ["meter-1.out", "valve-1.in"], ["valve-1.out", "sector-1.in"]]) {
      await p.locator(`[data-port="${a}"]`).click();
      await p.locator(`[data-port="${b}"]`).click();
    }
    let g = await p.evaluate(() => window.AqUI.S.blocksUi.editor.get());
    const ortho = await p.evaluate(() => window.AqUI.S.blocksUi.editor.get().diagram.routes.every(r => window.AqRouter.orthogonal(r.points) && r.points.length >= 2));
    check("RTE-01 five pipes connected by clicking ports, all horizontal/vertical", g.graph.edges.length === 5 && ortho, `${g.graph.edges.length} edges`);
    await p.click('button:text-is("Select")');
    // drag the pump down with the mouse: its pipes follow, still orthogonal, every frame
    const pump = p.locator('[data-node="pump-1"] rect').first();
    const pc = await center(pump);
    await p.mouse.move(pc.x, pc.y); await p.mouse.down();
    let allFrames = true;
    for (let i = 1; i <= 10; i++) {
      await p.mouse.move(pc.x, pc.y + 8 * i);
      allFrames = allFrames && await p.evaluate(() => [...document.querySelectorAll(".hed-pipe")].every(el => !/[^MLml0-9 .\-]/.test(el.getAttribute("d"))) &&
        window.AqUI.S.blocksUi.editor.get().diagram.routes.every(r => window.AqRouter.orthogonal(r.points)));
    }
    await p.mouse.up();
    check("RTE-04 every pipe stays horizontal/vertical during the drag preview", allFrames, "10 pointer moves checked");
    // drag the middle segment of the pump->meter pipe sideways
    const mid = await p.evaluate(() => {                 // a point on the pipe's middle segment (its box centre may be off it)
      const pts = window.AqUI.S.blocksUi.editor.get().diagram.routes.find(r => r.edge === "e-3").points, i = Math.floor((pts.length - 1) / 2);
      return [(pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2];
    });
    const mp = await toScreen(mid);
    await p.mouse.click(mp.x, mp.y);
    const handles = p.locator(".hed-handle");
    const n = await handles.count();
    const before = JSON.stringify(await p.evaluate(() => window.AqUI.S.blocksUi.editor.get().diagram.routes.find(r => r.edge === "e-3").points));
    if (n) { const hc = await center(handles.nth(Math.floor(n / 2))); await drag(p, hc, { x: hc.x - 25, y: hc.y }); }
    const r3 = await p.evaluate(() => window.AqUI.S.blocksUi.editor.get().diagram.routes.find(r => r.edge === "e-3"));
    const edMsg = () => p.evaluate(() => (document.querySelector(".hed-msg") || {}).textContent || "");
    check("RTE-03 a dragged segment moves, stays orthogonal and is pinned", JSON.stringify(r3.points) !== before && r3.mode === "MANUAL" &&
      (await p.evaluate(pts => window.AqRouter.orthogonal(pts), r3.points)), `${n} handles; ${before} -> ${JSON.stringify(r3.points)}; ${await edMsg()}`);
    // keyboard: move the sector valve with the arrow keys (tablet/accessibility path)
    await p.locator('[data-node="valve-1"] rect').first().click();
    await pause(2500);                                   // the page's own refresh runs meanwhile: focus must stay
    for (let i = 0; i < 3; i++) await p.keyboard.press("ArrowUp");
    const v1 = await p.evaluate(() => window.AqUI.S.blocksUi.editor.get().diagram.symbols.find(s => s.node === "valve-1"));
    check("RTE-09 arrow keys move the selection by grid steps", v1.y < 300, `valve y ${v1.y}; focus ${await p.evaluate(() => document.activeElement && document.activeElement.className.baseVal)}; ${await edMsg()}`);
    // a pipe that crosses another without connecting: drain the sector back to the tank's return around the line
    g = await p.evaluate(() => window.AqUI.S.blocksUi.editor.get());
    await shotOf(p.locator(".hed-wrap"), "03-drawing");
    await p.click('button:text-is("Save drawing")');
    await p.waitForFunction(() => /Saved/.test(document.querySelector("#v-blocks").innerText));
    const topo = (await api(p, "/api/topology")).body;
    check("TOPO-05 graph saved with five explicit edges", topo.rev === 1 && topo.graph.edges.length === 5, `rev ${topo.rev}`);

    // ------------------------------------------------------------------ block, then program, from their forms
    await p.click('button.tab:text-is("Blocks")');
    await p.click('button:text-is("New block")');
    await p.fill('input[aria-label="Block name"]', "Sector 1 watering");
    await p.fill('input[aria-label="Block id"]', "sector-1");
    await p.selectOption('select[aria-label="Water comes from"]', "tank-1");
    await p.selectOption('select[aria-label="Water goes to"]', "sector-1");
    await p.fill('input[aria-label="Valve travel seconds"]', "3");
    await p.click('button:text-is("Find the path and an example plan")');
    await p.waitForSelector("text=Path: ");
    const verify = p.locator('label:has-text("wait for its feedback") input[type=checkbox]');
    for (let i = 0; i < await verify.count(); i++) if (await verify.nth(i).isChecked()) await verify.nth(i).uncheck();   // no feedback wired
    await p.click('[data-block-form] button:text-is("Save draft")');
    await p.waitForSelector('[data-block-form] .inline-msg:has-text("Saved")');
    await p.click('[data-block-form] button:text-is("Validate")');
    await p.waitForFunction(() => !document.querySelector("[data-block-form]"));
    const blk = (await api(p, "/api/blocks")).body.blocks[0];
    check("PROG/EQ block validated from the form", blk.status === "VALIDATED" && blk.body.meter === "meter-1", `${blk.block_id} ${blk.status}`);
    await shot(p, "07-blocks");

    await p.goto(BASE + "/#programs");
    await p.click('button:text-is("New program")');
    await p.fill('input[aria-label="Program name"]', "Sector 1 morning");
    await p.fill('input[aria-label="Program id"]', "sector-1-am");
    await p.check('input[name="prog-block"][value="sector-1"]');
    await p.check('input[name="prog-end"][value="VOLUME"]');
    await p.fill('input[aria-label="Volume to deliver"]', "10");
    await p.fill('input[aria-label="Maximum for the whole operation"]', "5");
    await p.check('input[name="prog-when"][value="WEEKLY"]');
    await p.fill('input[aria-label="Start time to add"]', "06:00");
    await p.click('button:text-is("Add start time")');
    await p.click('button:text-is("Preview")');
    await p.waitForSelector("[data-preview]");
    const pvText = await p.textContent("[data-preview]");
    check("PROG-01 preview shows the compiled sequence and next starts", /deliver 10 L/.test(pvText) && /Next starts/.test(pvText), pvText.slice(0, 160));
    await shotOf(p.locator("[data-program-form]"), "04-program-preview");
    await p.click('[data-program-form] button:text-is("Save draft")');
    await p.waitForSelector('[data-program-form] .inline-msg:has-text("Saved as a draft")');
    await p.click('[data-program-form] button:text-is("Validate")');
    await p.waitForSelector('[data-program-form] .inline-msg:has-text("Validated")');
    const progs = (await api(p, "/api/programs")).body.programs;
    check("PROG-01 program saved and validated with no JSON typed", progs[0].status === "VALIDATED" && progs[0].form.endpoint.litres === 10, progs[0].status);

    // ------------------------------------------------------------------ tablet width
    await p.setViewportSize({ width: 820, height: 1180 });
    for (const view of ["programs", "blocks", "hw-add", "history"]) {
      await p.goto(BASE + "/#" + view); await pause(1500);
      const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(`UX-04 no page-wide horizontal scroll at tablet width (${view})`, over <= 1, `overflow ${over}px`);
    }
    await shot(p, "08-tablet-history");
    await p.setViewportSize({ width: 1366, height: 900 });

    // ------------------------------------------------------------------ the twin
    check("DEP the real system publishes its snapshot for the twin", (await api(p, "/api/twin-link/publish", "POST")).status === 200, "publish");
    const t = (await api(p, "/api/twin-link/ticket", "POST")).body;
    const tp = await ctx.newPage();
    const twinErrors = [];
    tp.on("pageerror", e => twinErrors.push(e.message));
    tp.on("console", m => { if (m.type() === "error") twinErrors.push(m.text()); });
    await tp.goto(t.url + "#t=" + encodeURIComponent(t.ticket));
    await tp.click('button:text-is("Import from the real system")');
    await tp.waitForSelector("#twin-graph .hed-canvas", { timeout: 20000 });
    await tp.locator('button:text-is("Activate in twin")').first().click();
    await tp.click('button:text-is("Step 1 s")'); await pause(1500);
    const ap = tp.locator('button:has-text("Approve into MANUAL")');
    await ap.click(); await pause(300); await tp.locator('button:has-text("Press again to approve into MANUAL")').click().catch(() => ap.click());
    await tp.waitForSelector('button:text-is("Run now"):not([disabled])', { timeout: 15000 });
    await tp.click('button:text-is("Run now")');
    await tp.selectOption('select[aria-label="Speed"]', "60");
    await tp.click('button:text-is("Run")');
    const res = await (async () => { for (let i = 0; i < 60; i++) { const v = (await api(tp, "api/twin", "GET", undefined, true)).body; if (v.results.length) return v; await pause(500); } return null; })();
    await tp.click('button:text-is("Pause")');
    const last = res && res.results[res.results.length - 1];
    check("TWIN-02 the program delivers 10 L in the twin from the page", last && last.result === "DONE" && last.measured_L >= 10 && last.measured_L <= 10.2, JSON.stringify(last));
    check("TWIN-03 ledger balanced", res && Math.abs(res.graph_water.ledger.residual_L) < 1e-6, res && res.graph_water.ledger.residual_L);
    await pause(1500);
    const tnodes = await tp.locator("#twin-graph [data-node]").count();
    const ttopo = (await api(tp, "api/topology", "GET", undefined, true)).body;
    const tdia = (await api(tp, "api/diagram/installation", "GET", undefined, true)).body;
    check("TWIN-04 the twin's installation graph shows the imported drawing", tnodes >= 6,
      `${tnodes} symbols drawn; page errors: ${JSON.stringify(twinErrors.slice(0, 3))}; topology rev ${ttopo.rev} with ${ttopo.graph.nodes.length} nodes; diagram rev ${tdia.rev} with ${tdia.diagram.symbols.length} symbols`);
    await shotOf(tp.locator("#twin-graph"), "05-twin-graph");
    await tp.locator('button:text-is("Accept twin result")').first().click(); await pause(1500);
    const live = (await api(p, "/api/programs")).body.programs[0];
    check("DEP twin result recorded on the real system for this version", live.simulation && live.simulation.passed, JSON.stringify(live.simulation && live.simulation.twin_run_id));

    // ------------------------------------------------------------------ history
    await p.goto(BASE + "/#history");
    await p.waitForSelector("[data-runs]");
    const runsText = await p.textContent("[data-runs]");
    check("HIST the live system's history lists no physical run (the twin's is in the twin)", /No program has run yet|Program runs/.test(runsText), runsText.slice(0, 80));
    const truns = (await api(tp, "api/history/runs", "GET", undefined, true)).body.runs;
    check("HIST the twin run is in the twin's Operation History with its result", truns.length && truns[0].result === "DONE", truns.length);
  } catch (e) {
    check("journey", false, e.stack || e);
    for (const [i, pg] of ctx.pages().entries()) await shot(pg, "zz-failure-" + i).catch(() => {});
  } finally {
    fs.writeFileSync(path.join(OUT, "checks-journey.json"), JSON.stringify(checks, null, 1));
    await browser.close();
  }
})();
