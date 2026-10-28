// Owner decisions 28 Oct 2026 in the browser (bench copy, localhost:8060): operators open Equipment types; a need the
// form cannot meet is sent to the operators, who approve or reject it; activation stays with administrators.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const path = require("path"), fs = require("fs");
const OUT = path.join(__dirname, "shots-owner28"); fs.mkdirSync(OUT, { recursive: true });
const PW = fs.readFileSync(path.join(__dirname, "admin.txt"), "utf8").trim().replace(/^admin: /, "");
const BASE = "http://localhost:8060";
const checks = [];
const check = (id, ok, detail) => { checks.push({ id, ok: !!ok, detail }); console.log(ok ? "PASS" : "FAIL", id, detail || ""); };
const settle = p => p.waitForTimeout(1200);
async function login(p, u, pw, first = false) {
  await p.goto(BASE + "/");
  await p.fill("input:not([type=password])", u); await p.fill("input[type=password]", pw);
  await p.click("button[type=submit]");
  if (first) { await p.waitForTimeout(2500); return; }        // a new account is asked to change its password first
  await p.waitForFunction(() => window.AqUI && window.AqUI.S && window.AqUI.S.role, null, { timeout: 20000 });
}
(async () => {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const a = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  const errors = []; a.on("pageerror", e => errors.push(String(e)));
  await login(a, "admin", PW);
  const opw = "op-" + Math.random().toString(36).slice(2, 12) + "A9!", opw2 = opw + "-changed";
  await a.evaluate(([pw]) => window.AqUI.api("POST", "/api/users", { username: "oper28", password: pw, role: "operator" }), [opw]);
  const o = await (await b.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
  o.on("pageerror", e => errors.push(String(e)));
  await login(o, "oper28", opw, true);
  await o.evaluate(([x, y]) => window.AqUI.api("POST", "/api/auth/password", { old_password: x, new_password: y }), [opw, opw2]);
  await login(o, "oper28", opw2);

  // the operator sees and opens Equipment types
  const nav = await o.locator('#nav [data-nav="equipment-types"]').isVisible();
  await o.goto(BASE + "/#equipment-types");
  await o.waitForSelector('details[data-state-key="type:new:edit"]', { timeout: 20000 });
  check("an operator has Equipment types in the menu and opens the page", nav && (await o.locator("[data-type-requests]").count()) === 1, `menu visible: ${nav}`);

  // the operator sends a need the form cannot meet
  const d = o.locator('details[data-state-key="type:new:edit"]');
  await d.locator(":scope > summary").click(); await settle(o);
  await d.locator('[data-kind="pump"]').click(); await settle(o);
  await d.getByLabel("Type name").fill("Irrigation pump");
  await d.getByRole("button", { name: "+ Add a feature" }).click(); await settle(o);
  await d.getByText("A control output", { exact: true }).click(); await settle(o);
  await d.getByLabel("Describe the missing need").fill("A second output to reverse the pump for back-flushing the filter.");
  await d.getByRole("button", { name: "Send to the operators for review" }).click(); await settle(o);
  const sent = await d.locator("[data-requests]").innerText().catch(() => "");
  await o.screenshot({ path: path.join(OUT, "01-request-sent.png"), fullPage: false });
  check("the request is sent to the operators (never only kept on the page)", /Sent to the operators for review/.test(sent) && /Request \d+:/.test(sent) && !/not sent/.test(sent), sent.replace(/\n/g, " | "));

  // it appears under Requests for every operator and administrator, who can approve or reject it
  await a.goto(BASE + "/#equipment-types"); await a.waitForSelector("[data-type-requests]");
  const card = a.locator("[data-type-requests]");
  const waiting = await card.getAttribute("data-type-requests");
  await card.screenshot({ path: path.join(OUT, "02-requests-card.png") });
  o.once("dialog", dl => dl.accept("needed for back-flushing"));
  await o.reload(); await o.waitForSelector("[data-type-requests]");
  await o.locator("[data-type-requests] tr").first().getByRole("button", { name: "Approve" }).click(); await settle(o);
  await o.reload(); await o.waitForSelector("[data-type-requests]");
  const row = await o.locator("[data-type-requests] tr").first().innerText();
  await o.locator("[data-type-requests]").screenshot({ path: path.join(OUT, "03-approved.png") });
  check("an operator reviews and approves it, with the note and the reviewer shown", waiting === "1" && /approved/.test(row) && /oper28: needed for back-flushing/.test(row), row.replace(/\s+/g, " "));

  // activation stays with administrators
  const act = o.locator(".card", { has: o.locator("h3", { hasText: /^Pump$/ }) }).getByRole("button", { name: "Retire" });
  const actRes = await o.evaluate(() => window.AqUI.api("POST", "/api/equipment/types/activate", { type_id: "pump", version: 1 }).then(() => "accepted", e => String(e.message || e)));
  check("activation stays with administrators", (await act.count()) === 0 && actRes !== "accepted", `operator activate: ${actRes}`);
  check("no page errors", !errors.length, errors.join(" | "));
  fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify(checks, null, 1));
  await b.close();
  console.log(`${checks.filter(c => c.ok).length}/${checks.length} PASS`);
})().catch(e => { console.log("FAIL run", e.stack); process.exit(1); });
