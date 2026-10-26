// 0.7.0 RTE-01..03, RTE-06, RTE-07 (router part), RTE-10 (timing): the orthogonal router and segment dragging.
// Run with `node --test tests/js` (tests/unit/test_router_js.py runs it under pytest).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const R = require("../../src/aquacrop/web/router.js");

function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }
const SIDES = ["N", "E", "S", "W"];
function anchorOf(box, side, off) {
  if (side === "N") return { x: box.x + off * box.w, y: box.y, side };
  if (side === "E") return { x: box.x + box.w, y: box.y + off * box.h, side };
  if (side === "S") return { x: box.x + (1 - off) * box.w, y: box.y + box.h, side };
  return { x: box.x, y: box.y + (1 - off) * box.h, side };
}
function assertRoute(r, a, b, msg) {
  assert.ok(r.ok, `${msg}: ${r.reason}`);
  const p = r.points;
  assert.ok(R.orthogonal(p), `${msg}: not orthogonal ${JSON.stringify(p)}`);
  assert.ok(!R.doublesBack(p), `${msg}: doubles back ${JSON.stringify(p)}`);
  assert.deepEqual(p[0], [R.snap(a.x), R.snap(a.y)], `${msg}: start detached`);
  assert.deepEqual(p[p.length - 1], [R.snap(b.x), R.snap(b.y)], `${msg}: end detached`);
}

test("RTE-01 every relative arrangement and every pair of port sides gives an attached H/V route", () => {
  const A = { id: "a", x: 300, y: 300, w: 40, h: 40 };
  let n = 0;
  for (const dx of [-250, -60, 0, 60, 250]) for (const dy of [-250, -60, 0, 60, 250]) {
    if (Math.abs(dx) < 60 && Math.abs(dy) < 60) continue;            // overlapping boxes are not an arrangement
    const B = { id: "b", x: 300 + dx, y: 300 + dy, w: 40, h: 40 };
    for (const sa of SIDES) for (const sb of SIDES) {
      const a = Object.assign(anchorOf(A, sa, 0.5), { owner: "a" }), b = Object.assign(anchorOf(B, sb, 0.5), { owner: "b" });
      const r = R.route({ start: a, end: b, obstacles: [A, B], bounds: { x: 0, y: 0, w: 800, h: 800 } });
      assertRoute(r, a, b, `dx=${dx} dy=${dy} ${sa}->${sb}`);
      n++;
    }
  }
  assert.ok(n > 300);
});

test("RTE-02 an obstacle in the way is avoided, with few bends, deterministically", () => {
  const A = { id: "a", x: 0, y: 200, w: 40, h: 40 }, B = { id: "b", x: 600, y: 200, w: 40, h: 40 };
  const wall = { id: "tank", x: 250, y: 100, w: 100, h: 240 };
  const q = { start: { x: 40, y: 220, side: "E", owner: "a" }, end: { x: 600, y: 220, side: "W", owner: "b" },
    obstacles: [A, B, wall], bounds: { x: -50, y: -50, w: 800, h: 600 } };
  const r1 = R.route(q), r2 = R.route(JSON.parse(JSON.stringify(q)));
  assertRoute(r1, q.start, q.end, "around the tank");
  assert.deepEqual(r1.points, r2.points);
  assert.ok(R.bends(r1.points) <= 4, JSON.stringify(r1.points));
  for (let i = 1; i < r1.points.length; i++) {
    const [p, s] = [r1.points[i - 1], r1.points[i]];
    const inside = (x, y) => x > wall.x && x < wall.x + wall.w && y > wall.y && y < wall.y + wall.h;
    assert.ok(!inside((p[0] + s[0]) / 2, (p[1] + s[1]) / 2));
  }
  const straight = R.route({ start: q.start, end: q.end, obstacles: [A, B], bounds: q.bounds });
  assert.equal(R.bends(straight.points), 0);                         // nothing in the way: a straight pipe
});

test("RTE-01 randomized: obstacles and ports, every success is H/V and attached, failures say why", () => {
  const rand = rng(20261025);
  let ok = 0, refused = 0;
  for (let k = 0; k < 300; k++) {
    const boxes = [];
    for (let i = 0; i < 6; i++) boxes.push({ id: "o" + i, x: Math.round(rand() * 700), y: Math.round(rand() * 500), w: 30 + Math.round(rand() * 80), h: 30 + Math.round(rand() * 80) });
    const A = boxes[0], B = boxes[1];
    const a = Object.assign(anchorOf(A, SIDES[k % 4], 0.5), { owner: A.id }), b = Object.assign(anchorOf(B, SIDES[(k >> 2) % 4], 0.5), { owner: B.id });
    if (Math.abs(a.x - b.x) < 1 && Math.abs(a.y - b.y) < 1) continue;
    const r = R.route({ start: a, end: b, obstacles: boxes, bounds: { x: -60, y: -60, w: 960, h: 760 } });
    if (r.ok) { assertRoute(r, a, b, `case ${k}`); ok++; } else { assert.ok(typeof r.reason === "string" && r.reason.length > 5); refused++; }
  }
  assert.ok(ok > 200, `${ok} routed, ${refused} refused`);
});

test("RTE-03 dragging a segment keeps H/V, keeps both ends, and returns a pin the router keeps", () => {
  const q = { start: { x: 40, y: 100, side: "E", owner: "a" }, end: { x: 400, y: 300, side: "W", owner: "b" },
    obstacles: [{ id: "a", x: 0, y: 80, w: 40, h: 40 }, { id: "b", x: 400, y: 280, w: 40, h: 40 }], bounds: { x: 0, y: 0, w: 600, h: 500 } };
  const r = R.route(q);
  assertRoute(r, q.start, q.end, "base");
  for (let i = 0; i < r.points.length - 1; i++) {
    for (const d of [-30, 25, 60]) {
      const m = R.dragSegment(r.points, i, d);
      assert.ok(R.orthogonal(m.points), `seg ${i} by ${d}: ${JSON.stringify(m.points)}`);
      assert.deepEqual(m.points[0], r.points[0]);
      assert.deepEqual(m.points[m.points.length - 1], r.points[r.points.length - 1]);
      assert.ok(m.pin && (m.pin.axis === "x" || m.pin.axis === "y"));
    }
  }
  const mid = r.points.findIndex((p, i) => i > 0 && i < r.points.length - 2);
  const moved = R.dragSegment(r.points, Math.max(1, mid), -40);           // away from the destination symbol
  const again = R.route(Object.assign({}, q, { pins: [moved.pin], previous: moved.points }));
  assertRoute(again, q.start, q.end, "with pin");
  assert.ok(again.points.some((p, i) => i > 0 && (moved.pin.axis === "x" ? p[0] === moved.pin.value && again.points[i - 1][0] === moved.pin.value
    : p[1] === moved.pin.value && again.points[i - 1][1] === moved.pin.value)), "pinned segment kept");
});

test("RTE-05 crossings are found strictly inside both segments; the later id carries the gap", () => {
  const a = [[0, 50], [100, 50]], b = [[50, 0], [50, 100]], touching = [[100, 50], [100, 120]];
  assert.deepEqual(R.crossings(a, b), [[50, 50]]);
  assert.deepEqual(R.crossings(a, touching), []);
  assert.deepEqual(R.gaps({ "e-b": b, "e-a": a }), { "e-b": [[50, 50]] });
});

test("RTE-06 no route: a refusal with a reason, never a diagonal", () => {
  const box = { id: "cage", x: 0, y: 0, w: 1000, h: 1000 };
  const r = R.route({ start: { x: 100, y: 100, side: "E", owner: "a" }, end: { x: 900, y: 900, side: "W", owner: "b" },
    obstacles: [box, { id: "a", x: 60, y: 80, w: 40, h: 40 }, { id: "b", x: 900, y: 880, w: 40, h: 40 }],
    bounds: { x: -10, y: -10, w: 1020, h: 1020 } });
  assert.equal(r.ok, false);
  assert.match(r.reason, /no free path|too long/);
});

test("RTE-07 canonical form: repeats and straight runs merge, a diagonal stays visible, a backtrack is caught", () => {
  assert.deepEqual(R.canonical([[0, 0], [0, 0], [10, 0], [20, 0], [20, 30]]), [[0, 0], [20, 0], [20, 30]]);
  assert.equal(R.orthogonal(R.canonical([[0, 0], [10, 10]])), false);
  assert.equal(R.doublesBack(R.canonical([[0, 0], [20, 0], [10, 0]])), true);
  assert.deepEqual(R.canonical([[0.2, 0.26], [10.24, 0.26]]), [[0, 0.5], [10, 0.5]]);
});

test("RTE-10 timing: a 100-symbol, 150-pipe layout routes within the per-route budget", () => {
  const rand = rng(7);
  const boxes = [];
  for (let i = 0; i < 100; i++) boxes.push({ id: "s" + i, x: (i % 10) * 110 + 20, y: Math.floor(i / 10) * 90 + 20, w: 40, h: 40 });
  const times = [], done = [];
  for (let k = 0; k < 150; k++) {
    const A = boxes[Math.floor(rand() * 100)], B = boxes[Math.floor(rand() * 100)];
    if (A === B) continue;
    const a = Object.assign(anchorOf(A, "E", 0.5), { owner: A.id }), b = Object.assign(anchorOf(B, "W", 0.5), { owner: B.id });
    const t0 = process.hrtime.bigint();
    const r = R.route({ start: a, end: b, obstacles: boxes, others: done.slice(-40), bounds: { x: 0, y: 0, w: 1200, h: 1000 } });
    times.push(Number(process.hrtime.bigint() - t0) / 1e6);
    if (r.ok) { assertRoute(r, a, b, `pipe ${k}`); done.push(r.points); }
  }
  times.sort((x, y) => x - y);
  const p95 = times[Math.floor(times.length * 0.95)];
  console.log(`RTE-10 route p95 ${p95.toFixed(1)} ms, worst ${times[times.length - 1].toFixed(1)} ms, ${done.length}/${times.length} routed`);
  assert.ok(p95 < 100, `p95 ${p95} ms`);                               // provisional budget: visible drag response
  assert.ok(done.length >= 0.9 * times.length, `${done.length} of ${times.length} routed`);
});
