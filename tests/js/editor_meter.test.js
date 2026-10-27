// UX-02: a flow meter inserted into a pipe splits the hydraulic edge through its inlet and outlet, sits on the pipe
// turned with the flow, keeps 90-degree routes, is one undoable step, and a refused insertion changes nothing.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
function fakeEl(tag) {
  return { tag, children: [], attrs: {}, style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; }, appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); }, replaceChildren(...c) { this.children = c; }, addEventListener() {}, insertBefore(c) { this.children.unshift(c); },
    get childNodes() { return this.children; }, focus() {}, setPointerCapture() {}, getScreenCTM() { return null; }, set textContent(v) { this._t = v; }, set className(v) { this.attrs.class = v; } };
}
global.document = { createElementNS: (_, t) => fakeEl(t), createElement: t => fakeEl(t), createTextNode: t => ({ text: t }) };
global.window = {}; global.requestAnimationFrame = f => setTimeout(f, 0); global.cancelAnimationFrame = () => {};
window.AqRouter = require("../../src/aquacrop/web/router.js");
require("../../src/aquacrop/web/hydraulic-editor.js");
const E = window.AqHydraulicEditor;
const SYM = { pump: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Suction" }, { id: "out", flow: "out", side: "E", offset: 0.5, label: "Discharge" }] },
  valve: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Inlet" }, { id: "out", flow: "out", side: "E", offset: 0.5, label: "Outlet" }] },
  meter: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Inlet" }, { id: "out", flow: "out", side: "E", offset: 0.5, label: "Outlet" }] },
  sensor: { w: 30, h: 30, ports: [] } };
const ITEMS = { p: { kind: "pump", name: "Pump 1" }, v: { kind: "valve", name: "Valve 1" }, m: { kind: "meter", name: "Flow 1" }, s: { kind: "sensor", name: "Pressure" } };
function editor(pos, rot) {
  const graph = { nodes: [{ id: "p", kind: "pump", item: "p" }, { id: "v", kind: "valve", item: "v" }, { id: "s", kind: "sensor", item: "s" }],
    edges: [{ id: "e1", src: { node: "p", port: "out" }, dst: { node: "v", port: "in" }, medium: "WATER", provenance: "OWNER" }],
    measures: [{ sensor: "s", edge: "e1", mode: "TAP" }] };
  const diagram = { width: 1000, height: 800, grid: 10, symbols: [{ node: "p", x: pos.p[0], y: pos.p[1], rot: rot || 0, ports: {} }, { node: "v", x: pos.v[0], y: pos.v[1], rot: rot || 0, ports: {} }, { node: "s", x: 700, y: 700, rot: 0, ports: {} }],
    routes: [{ edge: "e1", points: [], mode: "AUTO", pins: [] }] };
  const ed = E.create(fakeEl("div"), { symbols: SYM, items: ITEMS, graph, diagram, readOnly: false });
  ed.rerouteAll && ed.rerouteAll();
  return ed;
}
const ortho = pts => pts.every((q, i) => i === 0 || q[0] === pts[i - 1][0] || q[1] === pts[i - 1][1]);

test("horizontal pipe: upstream → meter inlet, meter outlet → downstream, on the line, 90-degree routes", () => {
  const ed = editor({ p: [100, 200], v: [500, 200] });
  assert.equal(ed.insertMeter("e1", "m"), true);
  const { graph, diagram } = ed.get();
  const ids = graph.edges.map(e => `${e.src.node}.${e.src.port}>${e.dst.node}.${e.dst.port}`).sort();
  assert.deepEqual(ids, ["m.out>v.in", "p.out>m.in"]);
  assert.ok(graph.edges.every(e => e.provenance === "OWNER" && e.lineage === "e1"));
  assert.equal(diagram.symbols.find(s => s.node === "m").rot, 0);
  for (const r of diagram.routes) assert.ok(r.points.length >= 2 && ortho(r.points));
  assert.equal(graph.measures[0].edge, "e1-in");                      // the tap names the upstream half
  ed.undo();
  assert.deepEqual(ed.get().graph.edges.map(e => e.id), ["e1"]);
  assert.ok(!ed.get().graph.nodes.some(n => n.id === "m"));
});

test("a vertical pipe flowing up turns the meter with the flow", () => {
  const ed = editor({ p: [300, 600], v: [300, 100] }, 270);
  assert.equal(ed.insertMeter("e1", "m"), true);
  assert.equal(ed.get().diagram.symbols.find(s => s.node === "m").rot, 270);
});

test("refusals change nothing: too short, not a meter, already connected, a sketch", () => {
  const short = editor({ p: [100, 200], v: [180, 200] });
  const before = JSON.stringify(short.get());
  short.insertMeter("e1", "m");
  assert.equal(JSON.stringify(short.get()), before);
  const ed = editor({ p: [100, 200], v: [500, 200] });
  const b2 = JSON.stringify(ed.get());
  ed.insertMeter("e1", "s"); ed.insertMeter("nope", "m");
  assert.equal(JSON.stringify(ed.get()), b2);
  ed.insertMeter("e1", "m");
  const b3 = JSON.stringify(ed.get());
  ed.insertMeter("e1-out", "m");                                      // already connected: shown, not moved
  assert.equal(JSON.stringify(ed.get().graph), JSON.stringify(JSON.parse(b3).graph));
});

test("UX-03: an earlier pipe is confirmed as a real pipe between the equipment at its ends, undoably", () => {
  const graph = { nodes: [{ id: "p", kind: "pump", item: "p" }, { id: "v", kind: "valve", item: "v" }], edges: [], measures: [] };
  const diagram = { width: 1000, height: 800, grid: 10, routes: [],
    symbols: [{ node: "p", x: 100, y: 200, rot: 0, ports: {} }, { node: "v", x: 500, y: 200, rot: 0, ports: {} }],
    sketches: [{ id: "old-1", points: [[140, 220], [300, 260], [500, 220]], why: "a pipe end is not attached by name" }] };
  const ed = E.create(fakeEl("div"), { symbols: SYM, items: ITEMS, graph, diagram, readOnly: false });
  assert.deepEqual(ed.remaining().pipes.map(x => x.id), ["old-1"]);
  ed.confirmSketch("old-1");
  const g = ed.get();
  assert.deepEqual(g.graph.edges.map(e => `${e.src.node}.${e.src.port}>${e.dst.node}.${e.dst.port}`), ["p.out>v.in"]);
  assert.equal((g.diagram.sketches || []).length, 0);
  ed.undo();
  assert.equal(ed.get().graph.edges.length, 0);
  assert.equal(ed.get().diagram.sketches.length, 1);
});

test("UX-03: an earlier pipe with equipment drawn along it is never joined end to end (no bypass)", () => {
  const graph = { nodes: [{ id: "p", kind: "pump", item: "p" }, { id: "m", kind: "meter", item: "m" }, { id: "v", kind: "valve", item: "v" }], edges: [], measures: [] };
  const diagram = { width: 1000, height: 800, grid: 10, routes: [],
    symbols: [{ node: "p", x: 100, y: 200, rot: 0, ports: {} }, { node: "m", x: 300, y: 200, rot: 0, ports: {} }, { node: "v", x: 500, y: 200, rot: 0, ports: {} }],
    sketches: [{ id: "old-2", points: [[140, 220], [500, 220]], why: "the old drawing had a meter drawn on this pipe" }] };
  const ed = E.create(fakeEl("div"), { symbols: SYM, items: ITEMS, graph, diagram, readOnly: false });
  ed.confirmSketch("old-2");
  assert.equal(ed.get().graph.edges.length, 0);
  assert.equal(ed.get().diagram.sketches.length, 1);
});
