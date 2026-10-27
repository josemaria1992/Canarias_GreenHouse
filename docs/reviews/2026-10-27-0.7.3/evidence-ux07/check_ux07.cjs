// UX-07 browser demo on the throwaway bench copy (localhost:8060). Headless Chromium, real clicks and typing.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const path = require("path"), fs = require("fs");
const OUT = path.join(__dirname, "shots-ux07"); fs.mkdirSync(OUT, { recursive: true });
const PW = fs.readFileSync(path.join(__dirname, "admin.txt"), "utf8").trim().replace(/^admin: /, "");
const BASE = "http://localhost:8060";
const checks = [];
const check = (id, ok, detail) => { checks.push({ id, ok: !!ok, detail }); console.log(ok ? "PASS" : "FAIL", id, detail || ""); };
const shot = (p, name, loc) => (loc || p).screenshot({ path: path.join(OUT, name), ...(loc ? {} : { fullPage: true }) }).catch(e => console.log("shot", name, e.message));

async function login(p, user, pw) {
  await p.goto(BASE + "/");
  await p.fill("input:not([type=password])", user); await p.fill("input[type=password]", pw);
  await p.click("button[type=submit]"); await settle(p);
  await p.waitForFunction(() => window.AqUI && window.AqUI.S && document.querySelector("nav"), null, { timeout: 20000 });
}
async function types(p) {
  await p.goto(BASE + "/#equipment-types");
  await p.waitForSelector('details[data-state-key="type:new:edit"]', { timeout: 20000 });
}
const settle = async p => { const n = await p.evaluate(() => (document.querySelector('[data-view]') || {}).dataset ? document.querySelector('[data-view]').dataset.drawn : 0); await p.waitForTimeout(150); await p.waitForFunction(n0 => !document.querySelector('[data-view]') || document.querySelector('[data-view]').dataset.drawn !== n0, n, { timeout: 1500 }).catch(() => null); await p.waitForTimeout(100); const n2 = await p.evaluate(() => { const f = (window.AqUI.S.typeForms || {})['type:new:edit']; return f && f.props ? f.props.length : -1; }).catch(() => -2); if (n2 !== settle.last) { console.log('  props', settle.last, '->', n2, new Error().stack.split(String.fromCharCode(10))[2].trim()); settle.last = n2; } };
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => window.AqUI.api(m, u, b), [m, u, b]);
const newForm = p => p.locator('details[data-state-key="type:new:edit"]');
async function openNew(p) {
  const d = newForm(p);
  if (!(await d.evaluate(e => e.open))) await d.locator(":scope > summary").click(); await settle(p);
  await d.locator("[data-kind-picker], .tf").first().waitFor();
  return d;
}
async function save(p, d) {
  await d.getByRole("button", { name: /^Save/ }).click(); await settle(p);
  await p.waitForTimeout(1200);
}

(async () => {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = []; p.on("pageerror", e => errors.push(String(e)));
  p.on("dialog", d => d.accept());
  await login(p, "admin", PW);
  await types(p);

  // 1. the kind picker: visual choices, a copy option, no JSON
  let d = await openNew(p);
  const tiles = await d.locator("[data-kind]").allInnerTexts();
  const copyOpts = await d.getByLabel("Copy an existing type").locator("option").count();
  check("UX07-1 New type opens on visual kind choices with a copy option, no JSON", tiles.length === 13 && copyOpts > 1 && (await p.locator("textarea").count()) === 0,
    `${tiles.length} kinds (${tiles.map(t => t.split("\n")[0]).join(", ")}); ${copyOpts - 1} types to copy; textareas on the page: ${await p.locator("textarea").count()}`);
  await shot(p, "01-kind-picker.png", d);

  // 2. Pump: the relevant questions, polarity never assumed
  await d.locator('[data-kind="pump"]').click(); await settle(p);
  await d.locator(".tf").waitFor();
  const asks = await d.locator("[data-ask]").evaluateAll(es => es.map(e => e.dataset.ask));
  check("UX07-2 Pump asks: control output, running? fault?", asks.join() === "RUNNING,FAULT" && (await d.getByLabel("Name of the control output").count()) === 1, asks.join());
  await d.getByLabel("Type name").fill("Irrigation pump");
  await d.getByLabel("Does it report that it is running?").check(); await settle(p);
  const warn = await d.locator('[data-role="running"]').innerText();
  const polChecked = await d.locator('[data-role="running"] input[type=radio]:checked').count();
  check("UX07-2 a new running signal has no polarity until chosen", polChecked === 0 && /never assumed/.test(warn), warn.split("\n").find(l => /never assumed/.test(l)));
  await save(p, d);
  const missing = await d.locator("[data-missing]").innerText().catch(() => "");
  check("UX07-2 saving names what is missing and keeps the entries", /contact close or open/.test(missing) && (await d.getByLabel("Type name").inputValue()) === "Irrigation pump",
    missing.replace(/\n/g, " | "));
  await d.getByLabel("Running: contact closes when active").check(); await settle(p);
  await d.getByLabel("Does it report a fault?").check(); await settle(p);
  await d.getByLabel("Fault: contact opens when active").check(); await settle(p);
  const tplMax = await d.getByLabel("Maximum running time in minutes").inputValue();
  await d.getByLabel("Maximum running time in minutes").fill("30");
  console.log("template max running time (min):", tplMax);

  // 3. every icon previewed, with the actual drawing symbol and its connection points
  const icons = await d.locator("[data-icon]").evaluateAll(es => es.map(e => e.dataset.icon));
  let seen = 0;
  for (const n of icons) {
    await d.locator(`[data-icon="${n}"]`).click(); await settle(p);
    if ((await d.locator(`.tf-preview[data-preview="${n}"]`).count()) === 1 && (await d.locator(`[data-icon="${n}"][aria-checked=true]`).count()) === 1) seen++;
  }
  await d.locator('[data-icon="pump"]').click(); await settle(p);
  const sym = d.locator('.tf-preview svg[data-symbol="pump"]');
  const ports = await sym.locator("[data-port]").evaluateAll(es => es.map(e => e.dataset.port));
  check("UX07-3 every icon can be chosen and previewed before saving", seen === icons.length && icons.length >= 10, `${seen}/${icons.length} icons previewed: ${icons.join(", ")}`);
  check("UX07-3 the preview shows the pump's drawing symbol with its connection points", ports.length >= 2, `ports: ${ports.join(", ")}; ${await sym.getAttribute("aria-label")}`);
  await shot(p, "02-pump-icon-and-symbol.png", d.locator(".tf-preview"));

  // 4. a refresh keeps the open form, its entries and the selection
  await p.evaluate(() => window.AqUI.refresh()); await p.waitForTimeout(1500);
  d = newForm(p);
  check("UX07-4 a refresh keeps the open form, entries and icon", (await d.evaluate(e => e.open)) && (await d.getByLabel("Type name").inputValue()) === "Irrigation pump"
    && (await d.locator('[data-icon="pump"][aria-checked=true]').count()) === 1 && (await d.getByLabel("Maximum running time in minutes").inputValue()) === "30", "name, icon and limit kept");

  // 5. Add a feature: a detail; a second control output is unsupported; a need is drafted, never sent
  await d.getByRole("button", { name: "+ Add a feature" }).click(); await settle(p);
  await d.getByText("A detail (manufacturer, rated flow…)").click(); await settle(p);
  await d.getByRole("button", { name: "Add this detail" }).click(); await settle(p);
  const msg = await d.locator(".tf-add [role=alert]").innerText().catch(() => "");
  await d.getByLabel("New feature: Name").fill("Rated flow");
  await d.getByRole("button", { name: "Add this detail" }).click(); await settle(p);
  const dup = await d.locator(".tf-add [role=alert]").innerText().catch(() => "");
  await d.getByLabel("New feature: Name").fill("Motor power");
  await d.getByLabel("New feature: Unit").fill("kW");
  await d.getByRole("button", { name: "Add this detail" }).click(); await settle(p);
  check("UX07-5 Add a feature adds a detail (and says what is missing or duplicated first)", /name of the detail/.test(msg) && /already has a detail called “Rated flow”/.test(dup) && (await d.locator('[data-prop="motor_power"]').count()) === 1, `${msg} / ${dup}`);
  await d.getByRole("button", { name: "+ Add a feature" }).click(); await settle(p);
  await d.getByText("A control output", { exact: true }).click(); await settle(p);
  const unsup = await d.locator(".tf-add").innerText();
  await d.getByLabel("Describe the missing need").fill("A second output to reverse the pump for back-flushing the filter.");
  await d.getByRole("button", { name: "Keep this request" }).click(); await settle(p);
  const req = await d.locator("[data-requests]").innerText();
  check("UX07-5 a second control output is explained as unsupported and the need is kept, not sent", /not supported/.test(unsup) && /not sent, and nobody has been notified/.test(req)
    && (await d.locator('[data-role]').count()) === 3, req.split("\n").slice(1, 3).join(" | "));
  const summary = await d.locator("[data-summary]").innerText();
  check("UX07-5 the summary is in ordinary words", /^Irrigation pump — .*control output.*Running signal.*Fault signal.*maximum running time 30 min.*Rated flow \(L\/min\), Motor power \(kW\)/.test(summary), summary);
  await shot(p, "03-pump-form.png", d);
  await save(p, d);
  const pumpCard = p.locator(".card", { has: p.locator("h3", { hasText: /^Irrigation pump$/ }) });
  check("UX07-5 the draft is saved and its card reads in words", (await pumpCard.count()) === 1 && /DRAFT v1/.test(await pumpCard.innerText()) && /Running signal/.test(await pumpCard.innerText()),
    (await pumpCard.locator("[data-type-summary]").innerText().catch(() => "")));
  const v1 = (await api(p, "GET", "/api/equipment")).types.find(t => t.type_id === "irrigation_pump");

  // 6. reopen: meaning kept; a second edit keeps the hidden fields
  const ed = pumpCard.locator('details[data-state-key="type:irrigation_pump:draft:edit"]');
  await ed.locator(":scope > summary").click(); await settle(p);
  await ed.locator(".tf").waitFor();
  const reopened = await ed.locator("[data-summary]").innerText();
  const nc = await ed.getByLabel("Fault: contact opens when active").isChecked();
  check("UX07-6 a reopened draft means the same", reopened === summary && nc, `fault contact opens: ${nc}; ${reopened}`);
  await ed.getByLabel("Description").fill("Main irrigation pump, 2.2 kW");
  await save(p, ed);
  const v2 = (await api(p, "GET", "/api/equipment")).types.find(t => t.type_id === "irrigation_pump" && t.status === "DRAFT");
  const strip = x => JSON.stringify(Object.assign({}, x, { description: undefined }));
  check("UX07-6 a second edit changes only what was edited (hidden fields kept)", strip(v1.body) === strip(v2.body) && v2.body.description === "Main irrigation pump, 2.2 kW",
    `simulation ${JSON.stringify(v2.body.simulation)}; operations ${v2.body.operations.join(",")}; roles ${v2.body.roles.map(r => r.role + (r.inverted ? "(NC)" : "")).join(",")}`);
  // a no-change edit of an active built-in keeps every field
  const valve = (await api(p, "GET", "/api/equipment")).types.find(t => t.type_id === "valve" && t.status === "ACTIVE");
  const vc = p.locator(".card", { has: p.locator("h3", { hasText: new RegExp("^" + valve.name + "$") }) }).first();
  const ve = vc.locator('details[data-state-key="type:valve:next:edit"]');
  await ve.locator(":scope > summary").click(); await settle(p); await ve.locator(".tf").waitFor();
  await save(p, ve);
  const vd = (await api(p, "GET", "/api/equipment")).types.find(t => t.type_id === "valve" && t.status === "DRAFT");
  const norm = x => JSON.stringify(Object.assign({ description: "" }, x));
  check("UX07-6 a no-change edit of the built-in Valve saves the same definition", vd && norm(vd.body) === norm(valve.body), vd ? `v${valve.version} -> draft v${vd.version}` : "no draft");

  // 7. Tank: passive, capacity per item, representative preview
  await types(p); d = await openNew(p);
  await d.locator('[data-kind="tank"]').click(); await settle(p); await d.locator(".tf").waitFor();
  await d.getByLabel("Type name").fill("Buffer tank");
  const tankText = await d.innerText();
  check("UX07-7 Tank: no channel, capacity entered on each tank, preview labelled representative",
    /None: this is a passive water part/.test(tankText) && /capacity: it is entered on the tank/.test(tankText) && /representative/.test(tankText) && (await d.locator('svg[data-symbol="tank"]').count()) === 1, "");
  await shot(p, "04-tank-form.png", d);
  await save(p, d);
  const tank = (await api(p, "GET", "/api/equipment")).types.find(t => t.type_id === "buffer_tank");
  check("UX07-7 Tank draft saved with no roles", tank && tank.body.roles.length === 0 && tank.body.hydraulic === "tank", tank && JSON.stringify(tank.body.limits));

  // 8. Flow meter and Level sensor: the right measurement choices
  for (const [kind, name, unit, sym] of [["flow", "Line flow meter", "L/min", "meter"], ["level", "Tank level probe", "cm", "sensor"]]) {
    await types(p); d = await openNew(p);
    await d.locator(`[data-kind="${kind}"]`).click(); await settle(p); await d.locator(".tf").waitFor();
    await d.getByLabel("Type name").fill(name);
    const row = d.locator("[data-role]").first();
    const units = await row.locator('select[aria-label$=" unit"] option').allInnerTexts();
    const sigs = await row.locator('select[aria-label$=" signal"] option').allInnerTexts();
    await row.locator('select[aria-label$=" signal"]').selectOption("CURRENT_4_20"); await settle(p);
    const help = await d.locator("[data-role]").first().innerText();
    check(`UX07-8 ${name}: measurement, units and signal choices`, units[0] === unit && sigs.includes("4–20 mA") && /broken wire/.test(help) && (await d.locator(`svg[data-symbol="${sym}"]`).count()) === 1,
      `units ${units.join("/")}; signals ${sigs.join("/")}`);
    await shot(p, `05-${kind}-form.png`, d);
    await save(p, d);
    const t = (await api(p, "GET", "/api/equipment")).types.find(x => x.name === name);
    check(`UX07-8 ${name} saved`, t && t.body.roles[0].unit === unit && t.body.roles[0].electrical[0] === "CURRENT_4_20", t && JSON.stringify(t.body.roles[0]));
  }

  // 9. custom type with an extra reading, status signal and detail
  await types(p); d = await openNew(p);
  await d.locator('[data-kind="custom"]').click(); await settle(p); await d.locator(".tf").waitFor();
  await d.getByLabel("Type name").fill("Cabinet monitor");
  await d.getByRole("button", { name: "+ Add a feature" }).click(); await settle(p);
  await d.getByText("A reading (temperature, pressure, flow…)").click(); await settle(p);
  await d.getByLabel("New reading: what it measures").selectOption("temperature"); await settle(p);
  await d.getByRole("button", { name: "Add this reading" }).click(); await settle(p);
  await d.getByRole("button", { name: "+ Add a feature" }).click(); await settle(p);
  await d.getByText("A status signal (running, fault, position, on/off)").click(); await settle(p);
  await d.getByLabel("New feature: Name").fill("Door open");
  await d.getByRole("button", { name: "Add this signal" }).click(); await settle(p);
  await d.getByLabel("Door open: contact closes when active").check(); await settle(p);
  await d.getByRole("button", { name: "+ Add a feature" }).click(); await settle(p);
  await d.getByText("A detail (manufacturer, rated flow…)").click(); await settle(p);
  await d.getByLabel("New feature: Name").fill("Manufacturer");
  await d.getByLabel("New detail: value type").selectOption("TEXT"); await settle(p);
  await d.getByRole("button", { name: "Add this detail" }).click(); await settle(p);
  const cs = await d.locator("[data-summary]").innerText();
  await shot(p, "06-custom-form.png", d);
  await save(p, d);
  const cm = (await api(p, "GET", "/api/equipment")).types.find(t => t.name === "Cabinet monitor");
  check("UX07-9 custom type with a reading, a status signal and a detail", cm && cm.body.roles.map(r => r.purpose).join() === "MEASUREMENT,STATE" && cm.body.properties[0].kind === "TEXT"
    && cm.body.operations.includes("MEASURE") && cm.body.simulation.behavior === "NOT_MODELLED", cs);

  // 10. the preview matches what the Overview draws
  await types(p);
  const over = await p.evaluate(async () => {
    const E = await window.AqUI.api("GET", "/api/equipment");
    const svg = window.AqHydraulicEditor.symbolPreview(E.symbols, "pump"); const r = svg.querySelector("rect");
    return { preview: [r.getAttribute("width"), r.getAttribute("height")], ports: [...svg.querySelectorAll("[data-port]")].map(x => x.dataset.port) };
  });
  // the renderer the Overview and the Twin use, given a graph with one pump
  const drawn = await p.evaluate(async () => {
    const E = await window.AqUI.api("GET", "/api/equipment");
    const host = document.createElement("div"); document.body.appendChild(host);
    window.AqHydraulicEditor.create(host, { symbols: E.symbols, items: { p1: { kind: "pump", name: "P" } }, readOnly: true, viewOnly: true,
      graph: { nodes: [{ id: "p1", kind: "pump", item: "p1" }], edges: [], measures: [] },
      diagram: { view: "installation", width: 400, height: 300, symbols: [{ node: "p1", x: 100, y: 100 }], routes: [], texts: [] } });
    const r = host.querySelector('[data-node="p1"] rect.hed-box'); const out = r ? [r.getAttribute("width"), r.getAttribute("height")] : null; host.remove(); return out;
  });
  check("UX07-10 the pump preview has the size the diagram renderer (Overview and Twin) draws a pump at", drawn && drawn.join() === over.preview.join(), `preview ${over.preview.join("x")}, diagram ${drawn && drawn.join("x")}; ports ${over.ports.join(",")}`);

  // 11. activation follows permissions: an operator drafts but cannot activate
  const opw = "op-" + Math.random().toString(36).slice(2, 12) + "A9!";
  await api(p, "POST", "/api/users", { username: "ux07op", password: opw, role: "operator" });
  const p2 = await b.newPage({ viewport: { width: 1400, height: 1000 } }); p2.on("dialog", x => x.accept());
  await login(p2, "ux07op", opw);
  const opw2 = opw + "-changed";                     // a new account changes its password first
  await p2.evaluate(([a, b]) => window.AqUI.api("POST", "/api/auth/password", { old_password: a, new_password: b }), [opw, opw2]).catch(e => console.log("pw", e.message));
  await login(p2, "ux07op", opw2);
  await p2.goto(BASE + "/#equipment-types"); await p2.waitForTimeout(2500);
  const opText = await p2.locator("main").innerText();
  const opAct = await p2.evaluate(() => window.AqUI.api("POST", "/api/equipment/types/activate", { type_id: "irrigation_pump", version: 1 }).then(() => "accepted", e => String(e.message || e)));
  check("UX07-11 an operator cannot activate: the types page needs an administrator and the server refuses", /administrator account/.test(opText) && opAct !== "accepted",
    `page: ${(opText.match(/.*administrator account.*/) || [""])[0]}; activate: ${opAct}`);
  const adCard = p.locator(".card", { has: p.locator("h3", { hasText: /^Irrigation pump$/ }) });
  await types(p);
  await adCard.getByRole("button", { name: "Activate" }).click(); await settle(p); await p.waitForTimeout(1200);
  const act = (await api(p, "GET", "/api/equipment")).types.find(t => t.type_id === "irrigation_pump" && t.status === "ACTIVE");
  check("UX07-11 the administrator activates it", !!act, act && `v${act.version} ACTIVE`);
  check("UX07 no page errors", errors.length === 0, errors.join(" | "));
  fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify(checks, null, 1));
  await b.close();
  console.log(`${checks.filter(c => c.ok).length}/${checks.length} PASS`);
})().catch(e => { console.error(e); process.exit(1); });
