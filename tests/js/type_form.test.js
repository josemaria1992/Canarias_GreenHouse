// UX-07: the equipment type form's model. It must write the same definition the server validates, keep every field it
// does not show, never assume a contact's polarity, and say what is missing in plain words.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../../src/aquacrop/web/type-form.js");

const PUMP = { category: "Pumps", default_icon: "pump", description: "A pump.", properties: [{ key: "rated_flow_l_min", label: "Rated flow", kind: "NUMBER", unit: "L/min", min: 0, max: 10000 }],
  roles: [{ role: "start", label: "Start", direction: "OUTPUT", signal: "DIGITAL", purpose: "COMMAND" },
    { role: "running", label: "Running", direction: "INPUT", signal: "DIGITAL", purpose: "RUNNING", required: false, feedback_timeout_s: 5 },
    { role: "fault", label: "Fault", direction: "INPUT", signal: "DIGITAL", purpose: "FAULT", required: false }],
  operations: ["RUN_FOR", "SCHEDULE", "DELIVER_VOLUME", "KEEP_BETWEEN", "TRIGGERED", "SEQUENCE_STEP"],
  limits: { max_runtime_s: 3600, min_off_s: 10 }, simulation: { behavior: "PUMP_FLOW", delay_s: 1.0, demo_flow_L_min: 20 }, hydraulic: "pump" };
const TEMPLATES = { pump: { name: "Pump", body: PUMP },
  tank: { name: "Tank", body: { category: "Tanks", default_icon: "tank", hydraulic: "tank", description: "A tank.", properties: [{ key: "capacity_l", label: "Capacity", kind: "NUMBER", unit: "L", min: 0.1 }] } },
  level: { name: "Level", body: { category: "Level sensors", default_icon: "level", roles: [{ role: "level", label: "Level", direction: "INPUT", signal: "ANALOG", purpose: "MEASUREMENT", electrical: ["CURRENT_4_20"], unit: "cm" }],
    operations: ["MEASURE"], simulation: { behavior: "MANUAL_ANALOG" }, hydraulic: "sensor" } } };

test("a no-change edit writes back exactly the stored definition (nothing hidden is lost)", () => {
  const f = M.fromBody(PUMP, { name: "Pump", type_id: "pump", version: 1 });
  const out = M.toBody(f);
  const norm = b => JSON.parse(JSON.stringify(b, Object.keys(b).sort()));
  assert.deepEqual(out.roles.map(r => r.role), ["start", "running", "fault"]);
  assert.deepEqual(out.roles[2], Object.assign({}, PUMP.roles[2], { inverted: false }));
  for (const k of ["category", "default_icon", "description", "properties", "operations", "limits", "simulation", "hydraulic"]) assert.deepEqual(out[k], PUMP[k], k);
  assert.ok(norm(out));
});

test("a new Pump asks about running and fault feedback instead of assuming it, and never assumes polarity", () => {
  const f = M.startFrom("pump", TEMPLATES);
  assert.deepEqual(f.roles.map(r => r.purpose), ["COMMAND"]);          // optional feedback is asked, not copied
  f.name = "Irrigation pump";
  M.addStatus(f, "RUNNING");
  M.addStatus(f, "FAULT");
  const p = M.problems(f);
  assert.equal(p.filter(x => /contact close or open/.test(x.text)).length, 2);
  f.roles[1].pol = "closed"; f.roles[2].pol = "open";
  assert.deepEqual(M.problems(f), []);
  const b = M.toBody(f);
  assert.deepEqual(b.roles.map(r => [r.role, r.purpose, r.inverted]), [["start", "COMMAND", undefined], ["running", "RUNNING", false], ["fault", "FAULT", true]]);
  assert.match(M.summary(f), /^Irrigation pump — Start \(control output\); Running signal .*; Fault signal \(it has a fault; contact opens when active, optional\); maximum running time 1 h; at least 10 s off between starts/);
});

test("a passive Tank has no channel and its capacity stays a detail of each tank", () => {
  const f = M.startFrom("tank", TEMPLATES);
  f.name = "Water tank";
  assert.deepEqual(M.problems(f), []);
  const b = M.toBody(f);
  assert.deepEqual(b.roles, []);
  assert.equal(b.hydraulic, "tank");
  assert.equal(b.properties[0].key, "capacity_l");
  assert.ok(!("default" in b.properties[0]));                          // no shared capacity is forced on every tank
});

test("a sensor's reading needs a unit; its signal and unit are written as the server expects", () => {
  const f = M.startFrom("level", TEMPLATES);
  f.name = "Tank level";
  f.roles[0].unit = "";
  assert.ok(M.problems(f).some(x => /unit/.test(x.text)));
  f.roles[0].unit = "cm";
  assert.deepEqual(M.problems(f), []);
  assert.deepEqual(M.toBody(f).roles[0], TEMPLATES.level.body.roles[0]);
});

test("a custom type with an added reading, status signal and detail: stable keys, duplicates refused", () => {
  const f = M.startFrom("custom", TEMPLATES);
  f.name = "Mixer";
  assert.ok(M.problems(f).some(x => x.field === "roles"));            // needs a connection unless passive
  M.addOutput(f, "Motor on");
  f.limits.max_runtime_s = 600;
  M.addReading(f, "Motor temperature", "temperature", "°C", "CURRENT_4_20");
  M.addStatus(f, "STATE", "Lid closed");
  f.roles[2].pol = "closed";
  M.addProperty(f, { label: "Manufacturer", kind: "TEXT" });
  M.addProperty(f, { label: "Rated power", kind: "NUMBER", unit: "kW", min: 0, max: 100, required: true });
  assert.deepEqual(M.problems(f), []);
  const b = M.toBody(f);
  assert.deepEqual(b.roles.map(r => r.role), ["motor_on", "temperature", "lid_closed"]);
  assert.ok(b.operations.includes("MEASURE") && b.operations.includes("RUN_FOR"));
  assert.deepEqual(b.properties.map(p => p.key), ["manufacturer", "rated_power"]);
  // a second edit keeps the keys, even after renaming a label
  const g = M.fromBody(b, { name: "Mixer" });
  g.roles[1].label = "Winding temperature";
  assert.deepEqual(M.toBody(g).roles.map(r => r.role), ["motor_on", "temperature", "lid_closed"]);
  M.addStatus(g, "STATE", "Winding temperature");
  g.roles[3].pol = "closed";
  assert.ok(M.problems(g).some(x => /same name/.test(x.text)));
  M.addOutput(g, "Reverse");
  assert.ok(M.problems(g).some(x => /Only one control output/.test(x.text)));
});

test("a switchable type without a maximum running time is refused in words", () => {
  const f = M.startFrom("pump", TEMPLATES);
  f.name = "P";
  f.limits.max_runtime_s = null;
  assert.ok(M.problems(f).some(x => /maximum running time/.test(x.text)));
});
