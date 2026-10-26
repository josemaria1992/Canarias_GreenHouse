// 0.7.1 ID-01: the browser's Name -> ID rule is the server's (aquacrop/ids.py), on the owner's examples and edge cases.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const I = require("../../src/aquacrop/web/ids.js");

const CASES = [["Valve 1", "Valve_1"], ["Irrigation Pump 2", "IrrigationPump_2"], ["Main tank", "Maintank"],
  ["pump-3", "pump_3"], ["Válvula 12", "Valvula_12"], ["  Sector  A  ", "SectorA"], ["7", "7"], ["Flow meter 1b", "Flowmeter1b"],
  ["Tank_2", "Tank_2"], ["", ""]];

test("ID-01 the owner's examples and edge cases", () => {
  for (const [name, id] of CASES) assert.equal(I.fromName(name), id, name);
});

test("ID-01 the generated ID is a valid ID whenever the name has a letter or digit", () => {
  for (const [name, id] of CASES) if (id) assert.equal(I.problem(id), null, name);
  assert.match(I.problem("bad id"), /letters/);
});

test("ID-01 parity file written by the Python test matches", () => {
  const fs = require("node:fs"), path = require("node:path");
  const f = path.join(__dirname, "ids-cases.json");
  const cases = JSON.parse(fs.readFileSync(f, "utf-8"));
  for (const [name, id] of cases) assert.equal(I.fromName(name), id, name);
});
