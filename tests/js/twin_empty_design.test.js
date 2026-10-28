// C077-06 (Codex 0.7.7, owner 27 Oct 2026): New design clears the twin's drawing, pipes and connections only. No
// symbol, route, sketch, placeholder, box or label of the old design survives; the workspace settings stay; the
// equipment is still offered beside the drawing; and a saved empty design is not covered by the Overview reference.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const src = fs.readFileSync(path.join(__dirname, "../../src/aquacrop/web/twin-graph.js"), "utf8");
const part = (from, to) => src.slice(src.indexOf(from), src.indexOf(to));

function newDesignWith(G) {
  const code = part("function emptyDiagram(old)", "async function exportOverview");
  let built = null;
  const fn = new Function("G", "build", "draw", code + "; return newDesign;")(G, (v, graph, diagram) => { built = { graph, diagram }; }, () => {});
  return { run: () => fn({}), built: () => built };
}

const OLD = { kind: "AQUACROP_DIAGRAM", format: 1, view: "installation", title: "Installation", width: 1400, height: 900, grid: 20,
  symbols: [{ node: "old" }], routes: [{ edge: "old" }], sketches: [{ id: "old-pipe" }], placeholders: [{ id: "old-tank" }],
  shapes: [{ id: "old-box" }], texts: [{ id: "old-label" }] };

test("New design (pressed twice over a saved design) starts from a blank drawing that keeps only the workspace settings", () => {
  const G = { loaded: { topo: { rev: 3, graph: { nodes: [{ id: "old" }], edges: [{ id: "x" }], measures: [{ sensor: "s" }] } }, dia: { rev: 4, diagram: OLD } }, msg: {} };
  const nd = newDesignWith(G);
  nd.run();
  assert.equal(nd.built(), null, "the first press only asks to confirm");
  nd.run();
  const { graph, diagram } = nd.built();
  assert.deepEqual(graph, { nodes: [], edges: [], measures: [] });
  for (const k of ["symbols", "routes", "sketches", "placeholders", "shapes", "texts"]) assert.deepEqual(diagram[k], [], k);
  assert.deepEqual([diagram.width, diagram.height, diagram.grid, diagram.view, diagram.title], [1400, 900, 20, "installation", "Installation"]);
  assert.equal(G.editing, true);
  assert.equal(OLD.sketches.length, 1, "the saved design itself is untouched until the new one is saved");
});

test("a drawing with only earlier-drawing content (no graph nodes) also asks before it is cleared", () => {
  const G = { loaded: { topo: { rev: 1, graph: { nodes: [], edges: [], measures: [] } }, dia: { rev: 1, diagram: Object.assign({}, OLD, { symbols: [] }) } }, msg: {} };
  const nd = newDesignWith(G);
  nd.run();
  assert.equal(nd.built(), null);
  assert.equal(G.arm, "new");
});

test("a saved empty design is not 'no design yet': the Overview reference is not drawn over it", () => {
  const body = part("function build(v, graph, diagram)", "async function save(v)");
  const none = body.match(/const none = ([^;]+);/)[1];
  const isNone = new Function("graph", "topo", "dia", `return ${none};`);
  const empty = { nodes: [], edges: [], measures: [] };
  assert.equal(isNone(undefined, { rev: 0, graph: empty }, { rev: 0, diagram: {} }), true, "never saved: the reference is shown");
  assert.equal(isNone(undefined, { rev: 5, graph: empty }, { rev: 6, diagram: { placeholders: [] } }), false, "saved empty: stays empty");
});
