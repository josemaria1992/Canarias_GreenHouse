// 0.7.1 DT-03 (owner screenshot): ports of aligned equipment get a straight pipe. The editor's geometry puts a tank's
// outlet at 85% of its height, so on a 10 px grid a 40 px pump can never be level with it by grid snapping alone;
// alignShift levels the connected ports, and a deliberate offset beyond the snap distance keeps its bend.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

// a minimal DOM for the editor (it builds SVG/HTML nodes; nothing is rendered)
function fakeEl(tag) {
  return { tag, children: [], attrs: {}, style: {}, dataset: {}, classList: { toggle() {}, add() {}, remove() {} },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; }, appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); }, replaceChildren(...c) { this.children = c; }, addEventListener() {}, insertBefore(c) { this.children.unshift(c); },
    get childNodes() { return this.children; }, focus() {}, setPointerCapture() {}, getScreenCTM() { return null; }, set textContent(v) { this._t = v; }, set className(v) { this.attrs.class = v; } };
}
global.document = { createElementNS: (_, t) => fakeEl(t), createElement: t => fakeEl(t), createTextNode: t => ({ text: t }) };
global.window = {};
global.requestAnimationFrame = f => setTimeout(f, 0);
global.cancelAnimationFrame = () => {};
window.AqRouter = require("../../src/aquacrop/web/router.js");
require("../../src/aquacrop/web/hydraulic-editor.js");
const E = window.AqHydraulicEditor;

const SYM = { tank: { w: 100, h: 120, ports: [{ id: "out", flow: "out", side: "E", offset: 0.85, label: "Outlet", many: true }] },
  pump: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Suction" }, { id: "out", flow: "out", side: "E", offset: 0.5, label: "Discharge" }] },
  valve: { w: 40, h: 40, ports: [{ id: "in", flow: "both", side: "W", offset: 0.5, label: "Port A" }, { id: "out", flow: "both", side: "E", offset: 0.5, label: "Port B" }] },
  meter: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Inlet" }, { id: "out", flow: "out", side: "E", offset: 0.5, label: "Outlet" }] } };
const ITEMS = { tank: { kind: "tank", name: "tank" }, pump1: { kind: "pump", name: "pump1" }, meter: { kind: "meter", name: "meter" }, valva: { kind: "valve", name: "valva" } };

function editor(pumpY) {
  const graph = { nodes: Object.entries(ITEMS).map(([id, it]) => ({ id, kind: it.kind, item: id })), edges: [] };
  const diagram = { width: 900, height: 400, grid: 10, symbols: [{ node: "tank", x: 100, y: 20, rot: 0, ports: {} }, { node: "pump1", x: 300, y: pumpY, rot: 0, ports: {} },
    { node: "meter", x: 420, y: 100, rot: 0, ports: {} }, { node: "valva", x: 540, y: 100, rot: 0, ports: {} }], routes: [] };
  return E.create(fakeEl("div"), { symbols: SYM, items: ITEMS, graph, diagram, readOnly: false });
}
const ys = r => r.points.map(p => p[1]);

test("DT-03 the owner's tank -> pump -> meter -> valve: connecting levels the ports and the pipes are straight", () => {
  const ed = editor(100);
  ed.connect({ node: "tank", port: "out" }, { node: "pump1", port: "in" });
  ed.connect({ node: "pump1", port: "out" }, { node: "meter", port: "in" });
  ed.connect({ node: "meter", port: "out" }, { node: "valva", port: "in" });
  const { diagram } = ed.get();
  for (const r of diagram.routes) assert.equal(new Set(ys(r)).size, 1, `${r.edge} has a step: ${JSON.stringify(r.points)}`);
  assert.equal(diagram.symbols.find(s => s.node === "pump1").y, 122 - 20);   // its suction is level with the tank outlet
});

test("DT-03 a drawing imported with the step is straightened, and undo puts it back", () => {
  const ed = editor(100);
  const st = ed.state;
  st.graph.edges.push({ id: "e1", src: { node: "tank", port: "out" }, dst: { node: "pump1", port: "in" }, medium: "WATER" });
  st.diagram.routes.push({ edge: "e1", points: [], mode: "AUTO", pins: [] });
  ed.reroute(["e1"]);
  assert.ok(new Set(ys(st.diagram.routes[0])).size > 1, "the imported pipe had the step");
  ed.straighten();
  assert.equal(new Set(ys(st.diagram.routes[0])).size, 1);
  ed.undo();
  assert.ok(new Set(ys(ed.state.diagram.routes[0])).size > 1);
});

test("DT-03 a deliberate difference beyond the snap distance keeps its bend; alignment never connects", () => {
  const ed = editor(200);
  ed.connect({ node: "tank", port: "out" }, { node: "pump1", port: "in" });
  const r = ed.get().diagram.routes[0];
  assert.ok(new Set(ys(r)).size > 1, "a pump placed far below keeps a real bend");
  assert.ok(r.points.every((p, i) => i === 0 || p[0] === r.points[i - 1][0] || p[1] === r.points[i - 1][1]), "H/V only");
  const sh = ed.alignShift("meter", 420, 100);                            // a guide only: no pipe is made
  assert.equal(ed.get().graph.edges.length, 1);
  assert.ok(Math.abs(sh.dy) <= 12);
});
