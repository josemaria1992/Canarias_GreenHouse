// C075-02: choosing a duty pair from two unset selects keeps the first choice and sets the pair; a half pair is
// refused at save instead of being dropped.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
global.window = { AqUI: { h: () => ({}), api: async () => ({}), can: () => true, mount: () => {}, S: {}, VIEWS: {} } };
global.document = {};
require("../../src/aquacrop/web/blocks.js");
const { dutyChoose, dutyProblem } = window.AqBlocks;

test("two unset pumps chosen one after the other make the pair", () => {
  const F = {}, body = { duty: null };
  dutyChoose(F, body, 0, "pump-1");
  assert.equal(body.duty, null);
  assert.equal(dutyProblem(F), "Lead alternation: choose the second pump too, or clear the first.");
  dutyChoose(F, body, 1, "pump-2");
  assert.deepEqual(body.duty, { pumps: ["pump-1", "pump-2"] });
  assert.equal(dutyProblem(F), null);
});

test("a saved pair reopens, and clearing both removes it", () => {
  const F = {}, body = { duty: { pumps: ["pump-1", "pump-2"] } };
  dutyChoose(F, body, 1, "pump-3");
  assert.deepEqual(body.duty, { pumps: ["pump-1", "pump-3"] });
  dutyChoose(F, body, 0, ""); dutyChoose(F, body, 1, "");
  assert.equal(body.duty, null); assert.equal(dutyProblem(F), null);
  dutyChoose(F, body, 0, "pump-1"); dutyChoose(F, body, 1, "pump-1");
  assert.match(dutyProblem(F), /two different pumps/);
});
