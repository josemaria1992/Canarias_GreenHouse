// C077-06/07/08 browser check on the throwaway bench copy (localhost:8060 live, 8061 twin). Headless Chromium.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const path = require("path"), fs = require("fs");
const OUT = path.join(__dirname, "shots"); fs.mkdirSync(OUT, { recursive: true });
const PW = fs.readFileSync(path.join(__dirname, "admin.txt"), "utf8").trim().replace(/^admin: /, "");
const checks = [];
const check = (id, ok, detail) => { checks.push({ id, ok: !!ok, detail }); console.log(ok ? "PASS" : "FAIL", id, detail || ""); };
(async () => {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = []; p.on("pageerror", e => errors.push(String(e)));
  await p.goto("http://localhost:8061/");
  await p.fill("input:not([type=password])", "admin"); await p.fill("input[type=password]", PW);
  await p.click("button[type=submit]");
  await p.waitForSelector("#twin-graph", { timeout: 20000 });
  const card = p.locator("#twin-graph");
  await card.scrollIntoViewIfNeeded();
  // C077-08: Import from Overview brings the earlier drawing with its explicit links and one reconciliation
  await card.getByRole("button", { name: "Import from Overview" }).click();
  await p.waitForSelector("[data-reconcile]", { timeout: 20000 });
  const rec = p.locator("[data-reconcile]");
  const before = await rec.innerText();
  const nq = await rec.locator("li[data-q]").count();
  await p.screenshot({ path: path.join(OUT, "08a-after-import.png"), fullPage: true });
  check("C077-08 import lists the questions and is not ready", nq > 0 && /Not ready to simulate/.test(before), `${nq} questions; ${before.split("\n")[0]}`);
  const sk0 = await p.locator("#twin-graph .hed-sketch, #twin-graph [data-sketch]").count();
  await rec.getByRole("button", { name: /Apply these answers/ }).click();
  await p.waitForTimeout(2500);
  const guard = p.locator("[data-reconcile]").getByRole("button", { name: "Replace and apply" });
  if (await guard.count()) { console.log("guard: the twin design was changed since the import; pressing again"); await guard.click(); }
  await p.waitForSelector("[data-ready=yes]", { timeout: 30000 });
  await p.waitForTimeout(1500);
  const after = await p.locator("[data-reconcile]").innerText();
  const sk1 = await p.locator("#twin-graph [data-sketch]").count();
  const nodes = await p.locator("#twin-graph [data-node]").count();
  const edges = await p.evaluate(() => new Set([...document.querySelectorAll("#twin-graph [data-edge]")].map(e => e.getAttribute("data-edge"))).size);
  const total = (await p.evaluate(() => fetch("api/topology").then(r => r.json()))).graph.edges.length;
  const undrawn = Number(await p.locator("[data-undrawn]").getAttribute("data-undrawn").catch(() => "0"));
  await p.locator("#twin-graph .hed-twin").screenshot({ path: path.join(OUT, "08b-reconciled-drawing.png") });
  await p.locator("[data-reconcile]").screenshot({ path: path.join(OUT, "08c-reconcile-card.png") });
  check("C077-08 the suggested answers applied: ready to simulate", /ready to simulate/.test(after) && !/Not ready/.test(after), after.split("\n").slice(0, 2).join(" | "));
  check("C077-08 every connection is drawn or named as not drawn yet (never hidden)", edges + undrawn === total && nodes >= 45 && sk1 < sk0,
    `${total} connections: ${edges} drawn, ${undrawn} named as not drawn yet; ${nodes} symbols; dotted sketches ${sk0} -> ${sk1} (the rest are drawn references)`);
  // C077-07: the flow meters sit in their pipes, the vertical one turned with its pipe
  const meter = await p.evaluate(() => {
    const g = document.querySelector('#twin-graph [data-node="flow-fill-main"]');
    const ic = g && g.querySelector(".hed-icon");
    return ic ? ic.getAttribute("transform") : null;
  });
  const g = p.locator('#twin-graph [data-node="flow-fill-main"]');
  if (await g.count()) await g.screenshot({ path: path.join(OUT, "07-vertical-meter.png") }).catch(() => null);
  check("C077-07 a vertical meter's glyph is turned with its pipe", /rotate\((90|270) 12 12\)/.test(meter || ""), meter);
  // repeatable: reload and import again, nothing asked again
  await p.reload(); await p.waitForSelector("#twin-graph", { timeout: 20000 });
  await p.locator("#twin-graph").getByRole("button", { name: "Import from Overview" }).click();
  await p.waitForTimeout(2500);
  const again = await p.locator("[data-reconcile]").innerText().catch(() => "");
  check("C077-08 a later import applies the kept answers (no question open)", (await p.locator("[data-ready=yes]").count()) === 1 && !/\(\d+ new\)/.test(again), again.split("\n")[0]);
  // C077-06: New design is really empty, with the equipment listed to place
  const nd = p.locator("#twin-graph").getByRole("button", { name: /New design|Start an empty design/ });
  await nd.click(); await p.waitForTimeout(300);
  await p.locator("#twin-graph").getByRole("button", { name: "Start an empty design" }).click();
  await p.waitForTimeout(1200);
  const empty = await p.evaluate(() => ({ nodes: document.querySelectorAll("#twin-graph [data-node]").length,
    sketches: document.querySelectorAll("#twin-graph [data-sketch]").length, ph: document.querySelectorAll("#twin-graph [data-placeholder]").length,
    places: [...document.querySelectorAll("#twin-graph button")].filter(x => /^Place$/.test(x.textContent)).length }));
  await p.screenshot({ path: path.join(OUT, "06-empty-design.png"), fullPage: true });
  check("C077-06 an empty design shows nothing of the old drawing; equipment offered to place", empty.nodes === 0 && empty.sketches === 0 && empty.ph === 0 && empty.places >= 40, JSON.stringify(empty));
  check("no page error", !errors.length, errors.join(" | "));
  fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify(checks, null, 1));
  await b.close();
})().catch(e => { console.log("FAIL run", e.stack); process.exit(1); });
