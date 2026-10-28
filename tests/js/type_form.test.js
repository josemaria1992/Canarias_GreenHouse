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

// R402-04: a new or copied type never carries a contact polarity nobody confirmed
test("a new Button or switch, and a copy of any type, ask every digital input's polarity again", () => {
  const BUTTON = { category: "Buttons", default_icon: "button", roles: [{ role: "pressed", label: "Pressed", direction: "INPUT", signal: "DIGITAL", purpose: "STATE" }],
    operations: [], simulation: { behavior: "MANUAL_INPUT" } };
  const f = M.startFrom("button", { button: { name: "Button", body: BUTTON } });
  f.name = "Float switch";
  assert.equal(f.roles[0].pol, null);
  assert.ok(M.problems(f).some(p => /contact close or open/.test(p.text)));
  f.roles[0].pol = "open";
  assert.equal(M.problems(f).length, 0);
  assert.equal(M.toBody(f).roles[0].inverted, true);
  const stored = Object.assign({}, PUMP, { roles: PUMP.roles.map(r => r.role === "fault" ? Object.assign({}, r, { inverted: true }) : r) });
  const c = M.copyOf({ name: "Pump", body: stored });
  assert.equal(c.name, "Copy of Pump");
  assert.deepEqual(c.roles.filter(r => r.direction === "INPUT").map(r => r.pol), [null, null]);
  assert.equal(M.problems(c).filter(p => /contact close or open/.test(p.text)).length, 2);
  // editing the stored type keeps what was confirmed
  const e = M.fromBody(stored, { name: "Pump", type_id: "pump", version: 2 });
  assert.deepEqual(e.roles.filter(r => r.direction === "INPUT").map(r => r.pol), ["closed", "open"]);
});

// R402-05: what a reading measures survives save and reopen
test("changing what a new reading measures persists through save and reopen; a saved reading keeps its quantity", () => {
  const f = M.startFrom("flow", { flow: { name: "Flow", body: { category: "Flow meters", default_icon: "flow", hydraulic: "meter",
    roles: [{ role: "flow", label: "Flow rate", direction: "INPUT", signal: "ANALOG", purpose: "MEASUREMENT", unit: "L/min" }],
    operations: ["MEASURE"], simulation: { behavior: "NOT_MODELLED" } } } });
  f.name = "Line sensor";
  assert.ok(M.setMeasure(f, f.roles[0], "pressure"));
  assert.equal(f.roles[0].unit, "bar");
  const saved = M.toBody(f);
  assert.deepEqual([saved.roles[0].role, saved.roles[0].label, saved.roles[0].unit], ["pressure", "Pressure", "bar"]);
  const again = M.fromBody(saved, { name: "Line sensor", type_id: "line_sensor", version: 1 });
  assert.equal(again.roles[0].measure, "pressure");
  assert.equal(M.summary(again), M.summary(Object.assign(f, { roles: f.roles })));
  // a saved reading cannot change quantity (items hold its channel and history)
  assert.equal(M.setMeasure(again, again.roles[0], "flow"), false);
  assert.equal(again.roles[0].role, "pressure");
  // an incompatible unit is named before saving
  const g = M.startFrom("custom", {});
  g.name = "Probe";
  M.addReading(g, "Temperature", "temperature", "bar");
  assert.ok(M.problems(g).some(p => /unit is one of °C, not bar/.test(p.text)));
  // a temperature reading added to a Pump stays a temperature reading
  const pump = M.startFrom("pump", TEMPLATES);
  M.addReading(pump, "Motor temperature", "temperature", "°C");
  const back = M.fromBody(M.toBody(pump), { name: "P", type_id: "p", version: 1 });
  const t = back.roles.find(r => r.purpose === "MEASUREMENT");
  assert.deepEqual([t.role, t.measure, t.unit], ["temperature", "temperature", "°C"]);
});

// R913-03: several readings of one quantity, and copied/older custom keys, keep what they measure on reopen
test("two readings of the same quantity keep their quantity through save and reopen, with keys unchanged", () => {
  const f = M.startFrom("custom", {});
  f.name = "Line monitor";
  M.addReading(f, "Pressure", "pressure", "bar");
  M.addReading(f, "Something", "other", "x");
  assert.ok(M.setMeasure(f, f.roles[1], "pressure"));
  f.roles[1].label = "Discharge pressure";
  M.addReading(f, "Temperature", "temperature", "°C");
  M.addReading(f, "Bearing temperature", "temperature", "°C");
  const saved = M.toBody(f);
  assert.deepEqual(saved.roles.map(r => r.role), ["pressure", "pressure_2", "temperature", "temperature_2"]);
  const again = M.fromBody(saved, { name: "Line monitor", type_id: "line_monitor", version: 1 });
  assert.deepEqual(again.roles.map(r => [r.role, r.measure, r.unit]),
    [["pressure", "pressure", "bar"], ["pressure_2", "pressure", "bar"], ["temperature", "temperature", "°C"], ["temperature_2", "temperature", "°C"]]);
  assert.deepEqual(M.toBody(again).roles.map(r => r.role), saved.roles.map(r => r.role));   // stable keys
  // a unit changed to one that does not fit the quantity is refused, saved reading or not
  again.roles[3].unit = "bar";
  assert.ok(M.problems(again).some(p => /measures temperature: its unit is one of °C, not bar/.test(p.text)));
});

test("an older or copied reading with a custom key is recognised by its unit", () => {
  const body = { category: "Other", default_icon: "generic", operations: ["MEASURE"], simulation: { behavior: "NOT_MODELLED" },
    roles: [{ role: "bearing_temperature", label: "Bearing temperature", direction: "INPUT", signal: "ANALOG", purpose: "MEASUREMENT", unit: "°C" },
      { role: "discharge", label: "Discharge", direction: "INPUT", signal: "ANALOG", purpose: "MEASUREMENT", unit: "bar" },
      { role: "odd", label: "Odd", direction: "INPUT", signal: "ANALOG", purpose: "MEASUREMENT", unit: "rpm" }] };
  const f = M.fromBody(body, { name: "Old", type_id: "old", version: 3 });
  assert.deepEqual(f.roles.map(r => r.measure), ["temperature", "pressure", "other"]);
  const c = M.copyOf({ name: "Old", body });
  assert.deepEqual(c.roles.map(r => [r.role, r.measure]), [["bearing_temperature", "temperature"], ["discharge", "pressure"], ["odd", "other"]]);
  assert.equal(M.problems(f).filter(p => /unit is one of/.test(p.text)).length, 0);   // unchanged saved units pass
});
