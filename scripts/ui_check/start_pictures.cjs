// Pictures for the in-app Getting started page, taken by following its four tasks on a fresh simulated
// installation (driven by make_start_pictures.py). Each picture is the part of the page the task points at.
"use strict";
const path = require("path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const BASE = process.env.AQ_BASE, OUT = process.env.AQ_OUT;

async function picture(p, locator, name, extraBelow = 0) {
  const box = await locator.boundingBox();
  const vp = p.viewportSize();
  const clip = { x: Math.max(0, box.x - 8), y: Math.max(0, box.y - 8), width: Math.min(vp.width, box.width + 16), height: Math.min(900, box.height + 16 + extraBelow) };
  await p.screenshot({ path: path.join(OUT, name), clip, fullPage: true });
  console.log("saved", name, Math.round(clip.width), "x", Math.round(clip.height));
}
(async () => {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const p = await b.newPage({ viewport: { width: 900, height: 1000 }, colorScheme: "light" });
  const errs = [];
  p.on("pageerror", e => errs.push(String(e)));
  p.on("dialog", d => d.accept());
  await p.goto(BASE + "/");
  await p.waitForSelector("input[name=username]");
  await p.fill("input[name=username]", process.env.AQ_USER);
  await p.fill("input[name=password]", process.env.AQ_PASS);
  await p.keyboard.press("Enter");
  await p.waitForSelector("#app:not(.hidden)");
  const go = async v => { await p.goto(BASE + "/#" + v); await p.waitForFunction(v => window.AqUI.S.rendered === v, v); await p.waitForTimeout(1500); };
  const card = (title) => p.locator("section.view:not(.hidden) .card", { has: p.locator("h2", { hasText: title }) }).first();
  const lead = () => p.locator("section.view:not(.hidden) .card.proc .lead").first();
  // 1. the main tank level
  await go("main-tank");
  await picture(p, card(/^Main tank \(/), "start-1.png");
  // 2. one sector, once: manual mode and the Run once form
  await go("main-pumps");
  await p.getByRole("button", { name: "Manual mode" }).first().click();
  await p.waitForSelector("text=Run once");
  await p.waitForTimeout(1200);
  const form = p.locator("section.view:not(.hidden) .card.proc .subcard");
  await form.locator("label", { hasText: "Sector" }).locator("select").selectOption("S1");
  await form.locator("label", { hasText: "Watering time (s)" }).locator("input").fill("60");
  await p.waitForTimeout(400);
  await picture(p, p.locator("section.view:not(.hidden) .card.proc").first(), "start-2.png");
  // 3. running once, with Stop watering at the top
  await form.getByRole("button", { name: "Run once" }).click();
  await p.waitForFunction(() => /Running once/.test(document.querySelector("section.view:not(.hidden) .card.proc .lead").textContent), null, { timeout: 30000 });
  await p.waitForTimeout(1500);
  const proc = p.locator("section.view:not(.hidden) .card.proc").first();
  const top = await proc.boundingBox(), stop = await proc.getByRole("button", { name: "Stop watering" }).boundingBox();
  await p.screenshot({ path: path.join(OUT, "start-3.png"), clip: { x: top.x - 8, y: top.y - 8, width: top.width + 16, height: stop.y + stop.height + 60 - top.y }, fullPage: true });
  console.log("saved start-3.png");
  await proc.getByRole("button", { name: "Stop watering" }).click();
  await p.waitForFunction(() => /is stopped/.test(document.querySelector("section.view:not(.hidden) .card.proc .lead").textContent), null, { timeout: 60000 });
  // 4. a simulated problem and its recovery steps
  await go("twin");
  await p.selectOption("#fault-valve-what", "S1");
  await p.selectOption("#fault-valve-fault", "STUCK_CLOSED");
  await p.locator(".row", { has: p.locator("#fault-valve-what") }).getByRole("button", { name: "Apply" }).click();
  await p.waitForTimeout(800);
  await go("main-pumps");
  await p.getByRole("button", { name: "Start automatic watering" }).click();
  await p.waitForFunction(() => /because of a problem/.test(document.querySelector("section.view:not(.hidden) .card.proc .lead").textContent), null, { timeout: 90000 });
  await p.waitForTimeout(3000);
  const steps = await lead().textContent();
  await picture(p, p.locator("section.view:not(.hidden) .card.proc").first(), "start-4.png");
  await go("twin");
  await p.selectOption("#fault-valve-what", "S1");
  await p.selectOption("#fault-valve-fault", "");
  await p.locator(".row", { has: p.locator("#fault-valve-what") }).getByRole("button", { name: "Apply" }).click();
  console.log("problem shown:", steps);
  if (errs.length) { console.log("page errors:", errs); process.exitCode = 1; }
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
