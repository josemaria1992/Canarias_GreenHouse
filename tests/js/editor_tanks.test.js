// 0.7.3 owner corrections 3-4: capacity-sized tanks, placing an item onto an earlier drawing's placeholder, and a
// level sensor dropped on a tank (an association, never a pipe), carried with the tank, undoable.
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
const SYM = { tank: { w: 100, h: 120, ports: [{ id: "in", flow: "in", side: "N", offset: 0.3, label: "Inlet", many: true }, { id: "out", flow: "out", side: "E", offset: 0.85, label: "Outlet", many: true }] },
  sensor: { w: 30, h: 30, ports: [] }, pump: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Suction" }, { id: "out", flow: "out", side: "E", offset: 0.5, label: "Discharge" }] } };
const ITEMS = { small: { kind: "tank", name: "Small", capacity_L: 5000 }, big: { kind: "tank", name: "Main tank", capacity_L: 20000 },
  lvl: { kind: "sensor", name: "Main tank level", level: true }, temp: { kind: "sensor", name: "Temperature", level: false } };
function editor() {
  const graph = { nodes: [{ id: "lvl", kind: "sensor", item: "lvl" }, { id: "temp", kind: "sensor", item: "temp" }], edges: [], measures: [] };
  const diagram = { width: 900, height: 600, grid: 10, symbols: [{ node: "lvl", x: 600, y: 400, rot: 0, ports: {} }, { node: "temp", x: 700, y: 400, rot: 0, ports: {} }], routes: [],
    placeholders: [{ id: "tank-main", kind: "tank", x: 300, y: 40, w: 160, h: 200, label: "Main tank", item: "lvl", why: "there is no tank item called Main tank" }] };
  return E.create(fakeEl("div"), { symbols: SYM, items: ITEMS, graph, diagram, readOnly: false });
}

test("a 20 000 L tank is drawn bigger than a 5 000 L one; same rule as the server", () => {
  const a = E.tankSize(5000), b = E.tankSize(20000);
  assert.deepEqual(a, { w: 100, h: 120 }); assert.deepEqual(b, { w: 200, h: 240 });
  const ed = editor(); ed.placeItem("small");
  const s = ed.get().diagram.symbols.find(x => x.node === "small");
  assert.equal(s.w, 100); assert.equal(s.h, 120);
});

test("placing the tank on its placeholder keeps the earlier place and size and its level sensor measures it", () => {
  const ed = editor(); ed.placeItem("big", "tank-main");
  const { graph, diagram } = ed.get();
  const s = diagram.symbols.find(x => x.node === "big");
  assert.deepEqual([s.x, s.y, s.w, s.h], [300, 40, 160, 200]);
  assert.equal(diagram.placeholders.length, 0);
  assert.deepEqual(graph.measures, [{ sensor: "lvl", node: "big", mode: "TAP" }]);
  assert.equal(graph.edges.length, 0);                                     // never a pipe
  ed.undo(); assert.equal(ed.get().diagram.placeholders.length, 1);         // undo puts the placeholder back
});

test("a level sensor moved onto a tank is associated; a temperature sensor is not; the tank carries it", () => {
  const ed = editor(); ed.placeItem("big"); const st = ed.state;
  const t = st.diagram.symbols.find(x => x.node === "big");
  ed.moveSymbol("lvl", t.x + 20, t.y + 20, true);
  ed.drop("lvl");                                                            // what the pointer-up handler calls
  assert.deepEqual(st.graph.measures, [{ sensor: "lvl", node: "big", mode: "TAP" }]);
  ed.moveSymbol("temp", t.x + 60, t.y + 20, true); ed.drop("temp");
  assert.equal(st.graph.measures.length, 1);                               // only a level sensor is associated
  const before = st.diagram.symbols.find(x => x.node === "lvl").x;
  ed.moveSymbol("big", t.x + 100, t.y, true);
  assert.equal(st.diagram.symbols.find(x => x.node === "lvl").x, before + 100);   // carried with its tank
  assert.equal(st.graph.edges.length, 0);
});

test("C075-06: a refused tank move leaves the tank, its sensor and the pipe exactly as they were", () => {
  const symbols = { tank: { w: 100, h: 120, ports: [{ id: "out", flow: "out", side: "E", offset: 0.85, label: "Outlet", many: true }] },
    sensor: { w: 30, h: 30, ports: [] }, pump: { w: 40, h: 40, ports: [{ id: "in", flow: "in", side: "W", offset: 0.5, label: "Inlet" }] } };
  const items = { tank: { kind: "tank", name: "Tank", capacity_L: 5000 }, level: { kind: "sensor", name: "Level", level: true }, pump: { kind: "pump", name: "Pump" } };
  const graph = { nodes: Object.keys(items).map(id => ({ id, item: id, kind: items[id].kind })),
    edges: [{ id: "pipe", src: { node: "tank", port: "out" }, dst: { node: "pump", port: "in" } }], measures: [{ sensor: "level", node: "tank", mode: "TAP" }] };
  const diagram = { width: 900, height: 600, grid: 10, symbols: [{ node: "tank", x: 100, y: 100 }, { node: "level", x: 120, y: 120 }, { node: "pump", x: 600, y: 182 }], routes: [{ edge: "pipe", points: [] }] };
  const ed = E.create(fakeEl("div"), { symbols: symbols, items, graph, diagram, readOnly: false });
  const before = JSON.stringify(ed.get().diagram);
  assert.equal(ed.moveSymbol("tank", 1200, 100, true), false);
  assert.equal(JSON.stringify(ed.get().diagram), before);
  assert.equal(ed.moveSymbol("tank", 160, 100, true), true);                 // an accepted move carries the sensor
  assert.equal(ed.get().diagram.symbols.find(s => s.node === "level").x, 180);
});
