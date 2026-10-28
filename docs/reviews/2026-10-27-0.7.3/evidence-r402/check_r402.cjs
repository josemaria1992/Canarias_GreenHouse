// R402-04/05/06 browser regressions on the throwaway bench copy (localhost:8060 live, 8061 twin). Headless Chromium.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const path = require("path"), fs = require("fs");
const OUT = path.join(__dirname, "shots-r402"); fs.mkdirSync(OUT, { recursive: true });
const PW = fs.readFileSync(path.join(__dirname, "admin.txt"), "utf8").trim().replace(/^admin: /, "");
const checks = [];
const check = (id, ok, detail) => { checks.push({ id, ok: !!ok, detail }); console.log(ok ? "PASS" : "FAIL", id, detail || ""); };
const settle = p => p.waitForTimeout(1200);

async function login(p, base) {
  await p.goto(base + "/");
  await p.fill("input:not([type=password])", "admin"); await p.fill("input[type=password]", PW);
  await p.click("button[type=submit]");
}
async function twinReady(p) { await p.waitForSelector("#twin-graph", { timeout: 20000 }); await p.waitForTimeout(1500); }
async function importAndApply(p) {
  const card = p.locator("#twin-graph");
  await card.getByRole("button", { name: /Import from Overview|Replace with the Overview's drawing/ }).click(); await settle(p);
  const rep = card.getByRole("button", { name: "Replace with the Overview's drawing" });
  if (await rep.count()) { await rep.click(); await settle(p); }
  await p.waitForSelector("[data-reconcile]", { timeout: 20000 });
  const apply = p.locator("[data-reconcile]").getByRole("button", { name: /Apply these answers|Replace and apply/ });
  if (await apply.count()) {
    await apply.click(); await settle(p);
    const guard = p.locator("[data-reconcile]").getByRole("button", { name: "Replace and apply" });
    if (await guard.count()) { await guard.click(); await settle(p); }
  }
  await p.waitForSelector("[data-ready=yes]", { timeout: 30000 });
}
const readyState = p => p.evaluate(() => [...document.querySelectorAll("[data-ready]")].map(e => e.dataset.ready).join(",") || "no panel");

(async () => {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = []; p.on("pageerror", e => errors.push(String(e)));
  p.on("dialog", d => d.accept());

  // R402-06 (a): resolved import -> remove a connection -> save -> reload
  await login(p, "http://localhost:8061"); await twinReady(p);
  await importAndApply(p);
  check("R402-06 the reconciled import says ready", (await readyState(p)) === "yes", await readyState(p));
  await p.locator("#twin-graph").getByRole("button", { name: "Edit the drawing" }).click(); await settle(p);
  const edge = p.locator("#twin-graph [data-edge]").first();
  const gone = await edge.getAttribute("data-edge");
  await edge.dispatchEvent("pointerdown"); await p.waitForTimeout(300);
  await p.keyboard.press("Delete"); await p.waitForTimeout(300);
  const stillThere = await p.locator(`#twin-graph [data-edge="${gone}"]`).count();
  await p.locator("#twin-graph").getByRole("button", { name: "Save in the twin" }).click(); await settle(p);
  await p.reload(); await twinReady(p);
  const after1 = await readyState(p);
  const edges = (await p.evaluate(() => fetch("api/topology").then(r => r.json()))).graph.edges.map(e => e.id);
  await p.screenshot({ path: path.join(OUT, "01-edited-after-import.png"), fullPage: true });
  check("R402-06 after removing a connection and saving, the panel no longer says ready", stillThere === 0 && !edges.includes(gone) && !/yes/.test(after1),
    `removed ${gone}; panel: ${after1}`);

  // a deliberate re-import applies the kept answers again
  await importAndApply(p);
  check("R402-06 a re-import applies the kept answers (ready again, nothing asked)", (await readyState(p)) === "yes" &&
    !/\(\d+ new\)/.test(await p.locator("[data-reconcile]").innerText()), await readyState(p));

  // R402-06 (b): resolved import -> New design -> save -> reload
  const card = p.locator("#twin-graph");
  await card.getByRole("button", { name: "New design" }).click(); await p.waitForTimeout(300);
  await card.getByRole("button", { name: "Start an empty design" }).click(); await settle(p);
  await card.getByRole("button", { name: "Save in the twin" }).click(); await settle(p);
  await p.reload(); await twinReady(p);
  const after2 = await readyState(p);
  const nodes = (await p.evaluate(() => fetch("api/topology").then(r => r.json()))).graph.nodes.length;
  await p.screenshot({ path: path.join(OUT, "02-new-design-after-import.png"), fullPage: true });
  check("R402-06 a saved New design is never described as the ready import", nodes === 0 && !/yes/.test(after2), `nodes ${nodes}; panel: ${after2}`);

  // R402-04: a new Button or switch asks the polarity
  const q = await b.newPage({ viewport: { width: 1400, height: 1000 } }); q.on("dialog", d => d.accept());
  q.on("pageerror", e => errors.push(String(e)));
  await login(q, "http://localhost:8060");
  await q.waitForFunction(() => window.AqUI && document.querySelector("nav"));
  await q.goto("http://localhost:8060/#equipment-types");
  await q.waitForSelector('details[data-state-key="type:new:edit"]');
  const d = q.locator('details[data-state-key="type:new:edit"]');
  await d.locator(":scope > summary").click(); await settle(q);
  await d.locator('[data-kind="button"]').click(); await settle(q);
  await d.getByLabel("Type name").fill("Float switch");
  const checked = await d.locator('[data-role] input[type=radio]:checked').count();
  await d.getByRole("button", { name: /^Save/ }).click(); await settle(q);
  const missing = await d.locator("[data-missing]").innerText().catch(() => "");
  await q.screenshot({ path: path.join(OUT, "03-button-polarity-asked.png"), fullPage: false });
  check("R402-04 a new Button or switch has no polarity chosen and cannot be saved until it is", checked === 0 && /contact close or open/.test(missing), missing.split("\n")[0]);
  await d.locator('[data-role] input[type=radio]').nth(1).check(); await settle(q);   // opens when active (normally closed)
  await d.getByRole("button", { name: /^Save/ }).click(); await settle(q);
  const fs_ = (await q.evaluate(() => window.AqUI.api("GET", "/api/equipment"))).types.find(t => t.name === "Float switch");
  check("R402-04 the answered polarity is saved", fs_ && fs_.body.roles[0].inverted === true, fs_ && JSON.stringify(fs_.body.roles[0]));

  // R402-05: what a reading measures persists through save and reopen
  await d.locator('[data-kind="flow"]').click(); await settle(q);
  await d.getByLabel("Type name").fill("Line pressure sensor");
  const t0 = Date.now();
  await d.locator('select[aria-label$=": what it measures"]').focus();      // as a click on the list does
  await d.locator('select[aria-label$=": what it measures"]').selectOption("pressure");
  await q.waitForFunction(() => !!document.querySelector('details[data-state-key="type:new:edit"] select[aria-label="Pressure unit"]'), null, { timeout: 5000 }).catch(() => null);
  console.log("redrawn after", Date.now() - t0, "ms");
  const unit = await d.locator('[data-role] select[aria-label$=" unit"]').first().inputValue();
  console.log('unit selects:', await d.locator('select[aria-label$=" unit"]').evaluateAll(es => es.map(e => e.getAttribute('aria-label') + '=' + e.value)));
  await d.getByRole("button", { name: /^Save/ }).click(); await settle(q);
  const lp = (await q.evaluate(() => window.AqUI.api("GET", "/api/equipment"))).types.find(t => t.name === "Line pressure sensor");
  const card2 = q.locator(".card", { has: q.locator("h3", { hasText: /^Line pressure sensor$/ }) });
  await card2.locator(`details[data-state-key="type:${lp.type_id}:draft:edit"] > summary`).click(); await settle(q);
  const ed = card2.locator(".tf");
  const sel = ed.locator('select[aria-label$=": what it measures"]');
  const reopened = { measure: await sel.inputValue(), disabled: await sel.isDisabled(), unit: await ed.locator('select[aria-label$=" unit"]').inputValue() };
  await q.screenshot({ path: path.join(OUT, "04-measure-reopened.png"), fullPage: false });
  check("R402-05 a reading changed to Pressure is saved as pressure and reopens as Pressure in bar (fixed once saved)",
    unit === "bar" && lp.body.roles[0].role === "pressure" && reopened.measure === "pressure" && reopened.unit === "bar" && reopened.disabled,
    `unit after the change: ${unit}; ${JSON.stringify(lp.body.roles[0])}; reopened ${JSON.stringify(reopened)}`);
  check("no page errors", !errors.length, errors.join(" | "));
  fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify(checks, null, 1));
  await b.close();
  console.log(`${checks.filter(c => c.ok).length}/${checks.length} PASS`);
})().catch(e => { console.log("FAIL run", e.stack); process.exit(1); });
