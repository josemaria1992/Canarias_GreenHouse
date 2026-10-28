/* Orthogonal pipe router and route editing (0.7.0, owner request 2026-10-25; PIPE_ROUTING_AND_DIAGRAM_LAYOUT.md).

   Pure functions, no DOM: the diagram editor calls them, and the tests run them under Node. Every route they
   return is a list of points in which consecutive points differ in exactly one coordinate (horizontal and
   vertical segments, 90-degree bends). There is no diagonal fallback: when no route fits, the result is
   { ok: false, reason } and the caller keeps the last committed drawing.

   Strategy: leave each port by a short stub in the direction it faces; try the direct, L and Z shapes (and the
   shape through the user's pinned segments) between the stub ends; otherwise search a sparse rectilinear grid
   built from the obstacle edges (A* whose state includes the incoming direction, so bends cost). Among valid
   candidates the cost is length + bends + crossings + closeness to the previous route; ties break by a stable
   order. Obstacles are the symbols' boxes inflated by the clearance; a route may enter only its own two
   symbols, and only along their port's stub. The search is bounded (expanded states and time). */
(function (root) {
  "use strict";
  const EPS = 0.5;
  const DIR = { N: [0, -1], E: [1, 0], S: [0, 1], W: [-1, 0] };
  const COST = { bend: 40, cross: 30, overlap: 1e6, deviation: 0.25 };
  const LIMIT = { states: 20000, ms: 40 };

  const snap = v => Math.round(v / EPS) * EPS;
  const same = (a, b) => a[0] === b[0] && a[1] === b[1];

  /** Snap, drop repeats, merge collinear runs. A diagonal stays diagonal (and fails `orthogonal`). */
  function canonical(points) {
    const out = [];
    for (const p of points) {
      const q = [snap(p[0]), snap(p[1])];
      if (!out.length || !same(out[out.length - 1], q)) out.push(q);
    }
    for (let i = 1; i < out.length - 1;) {
      const a = out[i - 1], b = out[i], c = out[i + 1];
      // merge only a straight run (b between a and c); a run that doubles back stays visible, and is refused
      const between = (u, v, w) => (u <= v && v <= w) || (u >= v && v >= w);
      if ((a[0] === b[0] && b[0] === c[0] && between(a[1], b[1], c[1])) || (a[1] === b[1] && b[1] === c[1] && between(a[0], b[0], c[0]))) out.splice(i, 1); else i++;
    }
    return out;
  }
  function orthogonal(points) {
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i];
      if ((a[0] === b[0]) === (a[1] === b[1])) return false;    // both equal: zero length; neither: diagonal
    }
    return points.length >= 2;
  }
  /** A pipe leaves its first port and enters its last one straight, along the side the port faces, for at least
      MIN_STUB: never along a symbol's edge or into a port from the side. */
  const MIN_STUB = 5;
  function portsStraight(points, startSide, endSide) {
    if (points.length < 2) return false;
    const seg = (a, b) => [Math.sign(b[0] - a[0]), Math.sign(b[1] - a[1]), Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1])];
    if (startSide) {
      const [dx, dy, len] = seg(points[0], points[1]), v = DIR[startSide];
      if (dx !== v[0] || dy !== v[1] || len < MIN_STUB) return false;
    }
    if (endSide) {
      const n = points.length, [dx, dy, len] = seg(points[n - 2], points[n - 1]), v = DIR[endSide];
      if (dx !== -v[0] || dy !== -v[1] || len < MIN_STUB) return false;
    }
    return true;
  }
  /** Two consecutive segments on one line: the pipe doubles back over itself. */
  function doublesBack(points) {
    for (let i = 2; i < points.length; i++) {
      const a = points[i - 2], b = points[i - 1], c = points[i];
      if ((a[0] === b[0] && b[0] === c[0]) || (a[1] === b[1] && b[1] === c[1])) return true;
    }
    return false;
  }
  function bends(points) { return Math.max(0, points.length - 2); }
  function length(points) {
    let n = 0;
    for (let i = 1; i < points.length; i++) n += Math.abs(points[i][0] - points[i - 1][0]) + Math.abs(points[i][1] - points[i - 1][1]);
    return n;
  }
  function segs(points) { const out = []; for (let i = 1; i < points.length; i++) out.push([points[i - 1], points[i]]); return out; }

  /** A segment passes through the inside of a box (touching its border is allowed). */
  function hitsBox(a, b, r) {
    const x1 = Math.min(a[0], b[0]), x2 = Math.max(a[0], b[0]), y1 = Math.min(a[1], b[1]), y2 = Math.max(a[1], b[1]);
    if (a[1] === b[1]) return a[1] > r.y && a[1] < r.y + r.h && x2 > r.x && x1 < r.x + r.w;
    return a[0] > r.x && a[0] < r.x + r.w && y2 > r.y && y1 < r.y + r.h;
  }
  /** Horizontal-vertical crossings strictly inside both segments. */
  function crossings(pa, pb) {
    const out = [];
    for (const s of segs(pa)) for (const t of segs(pb)) {
      for (const [h, v] of [[s, t], [t, s]]) {
        if (h[0][1] !== h[1][1] || v[0][0] !== v[1][0]) continue;
        const x = v[0][0], y = h[0][1];
        if (Math.min(h[0][0], h[1][0]) < x && x < Math.max(h[0][0], h[1][0]) && Math.min(v[0][1], v[1][1]) < y && y < Math.max(v[0][1], v[1][1])) out.push([x, y]);
      }
    }
    return out;
  }
  function overlaps(s, t) {
    const [a, b] = s, [c, d] = t;
    if (a[1] === b[1] && c[1] === d[1] && a[1] === c[1]) return Math.min(Math.max(a[0], b[0]), Math.max(c[0], d[0])) - Math.max(Math.min(a[0], b[0]), Math.min(c[0], d[0])) > EPS;
    if (a[0] === b[0] && c[0] === d[0] && a[0] === c[0]) return Math.min(Math.max(a[1], b[1]), Math.max(c[1], d[1])) - Math.max(Math.min(a[1], b[1]), Math.min(c[1], d[1])) > EPS;
    return false;
  }

  /** Validity and cost of a whole route (points from the start anchor to the end anchor). */
  function evaluate(points, ctx) {
    if (!orthogonal(points) || doublesBack(points)) return null;
    if (!portsStraight(points, ctx.startSide, ctx.endSide)) return null;
    const b = ctx.bounds;
    for (const p of points) if (b && (p[0] < b.x || p[0] > b.x + b.w || p[1] < b.y || p[1] > b.y + b.h)) return null;
    const ss = segs(points);
    for (let i = 0; i < ss.length; i++) {
      for (const r of ctx.obstacles) {
        // the first and last segments are the port stubs: they may start inside their own symbol's clearance
        if ((i === 0 && r.id === ctx.startOwner) || (i === ss.length - 1 && r.id === ctx.endOwner)) continue;
        if (hitsBox(ss[i][0], ss[i][1], r)) return null;
      }
    }
    // self-intersection or doubling back over itself
    for (let i = 0; i < ss.length; i++) for (let j = i + 2; j < ss.length; j++) {
      if (overlaps(ss[i], ss[j]) || crossings([ss[i][0], ss[i][1]], [ss[j][0], ss[j][1]]).length) return null;
    }
    let cost = length(points) + COST.bend * bends(points);
    const A = points[0], B = points[points.length - 1];
    for (const other of ctx.others) {
      cost += COST.cross * crossings(points, other).length;
      const oa = other[0], ob = other[other.length - 1];
      for (let i = 0; i < ss.length; i++) for (const t of segs(other)) {
        if (!overlaps(ss[i], t)) continue;
        // pipes on one port leave by the same stub; anywhere else, never share a run with another pipe
        const shared = (i === 0 && (same(oa, A) || same(ob, A))) || (i === ss.length - 1 && (same(oa, B) || same(ob, B)));
        if (!shared) return null;
      }
    }
    if (ctx.previous && ctx.previous.length >= 2) cost += COST.deviation * deviation(points, ctx.previous);
    return cost;
  }
  function deviation(a, b) {
    // area-free proxy: summed distance from each bend of `a` to the nearest point of `b`'s segments
    let d = 0;
    for (const p of a) {
      let best = Infinity;
      for (const [s, t] of segs(b)) {
        const x = Math.max(Math.min(s[0], t[0]), Math.min(Math.max(s[0], t[0]), p[0]));
        const y = Math.max(Math.min(s[1], t[1]), Math.min(Math.max(s[1], t[1]), p[1]));
        best = Math.min(best, Math.abs(x - p[0]) + Math.abs(y - p[1]));
      }
      d += best;
    }
    return d;
  }

  function stubPoint(port, stub) { const v = DIR[port.side]; return [snap(port.x + v[0] * stub), snap(port.y + v[1] * stub)]; }

  /** Shapes between the two stub ends. */
  function candidates(s, e, pins) {
    const out = [];
    if (s[0] === e[0] || s[1] === e[1]) out.push([s, e]);
    out.push([s, [e[0], s[1]], e], [s, [s[0], e[1]], e]);
    const mx = snap((s[0] + e[0]) / 2), my = snap((s[1] + e[1]) / 2);
    out.push([s, [mx, s[1]], [mx, e[1]], e], [s, [s[0], my], [e[0], my], e]);
    if (pins && pins.length) {
      let pts = [s], cur = s;
      for (const p of pins) {
        if (p.axis === "x") { pts.push([p.value, cur[1]]); cur = [p.value, cur[1]]; }
        else { pts.push([cur[0], p.value]); cur = [cur[0], p.value]; }
      }
      const last = pins[pins.length - 1];
      pts.push(last.axis === "x" ? [cur[0], e[1]] : [e[0], cur[1]], e);
      out.unshift(pts);                                     // the user's own shape first
    }
    return out;
  }

  /** Binary min-heap on (f, tie): deterministic order for equal costs. */
  function Heap() { this.a = []; }
  Heap.prototype.less = function (x, y) { return x.f < y.f || (x.f === y.f && x.tie < y.tie); };
  Heap.prototype.push = function (v) {
    const a = this.a; a.push(v);
    for (let i = a.length - 1; i > 0;) { const p = (i - 1) >> 1; if (!this.less(a[i], a[p])) break; const t = a[i]; a[i] = a[p]; a[p] = t; i = p; }
  };
  Heap.prototype.pop = function () {
    const a = this.a, top = a[0], last = a.pop();
    if (a.length) {
      a[0] = last;
      for (let i = 0; ;) {
        const l = 2 * i + 1, r = l + 1; let m = i;
        if (l < a.length && this.less(a[l], a[m])) m = l;
        if (r < a.length && this.less(a[r], a[m])) m = r;
        if (m === i) break;
        const t = a[i]; a[i] = a[m]; a[m] = t; i = m;
      }
    }
    return top;
  };

  /** Merged open intervals, and whether (a, b) meets one of them by more than a point. */
  function Intervals() { this.v = []; }
  Intervals.prototype.add = function (a, b) { this.v.push([Math.min(a, b), Math.max(a, b)]); };
  Intervals.prototype.done = function () {
    this.v.sort((p, q) => p[0] - q[0]);
    const out = [];
    for (const p of this.v) { if (out.length && p[0] < out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], p[1]); else out.push(p.slice()); }
    this.v = out;
    return this;
  };
  Intervals.prototype.hits = function (a, b) {        // a < b
    const v = this.v;
    let lo = 0, hi = v.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (v[m][1] <= a + EPS / 2) lo = m + 1; else hi = m; }
    return lo < v.length && v[lo][0] < b - EPS / 2;
  };
  function sortedCount(arr, a, b) {                   // how many values lie strictly between a and b
    const lower = x => { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] <= x) lo = m + 1; else hi = m; } return lo; };
    const upper = x => { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return lo; };
    return Math.max(0, upper(b) - lower(a));
  }

  /** A* over a sparse rectilinear grid, state = (point, incoming direction). Grid lines come from the obstacle
      edges, the two stub ends, tracks beside the pipes already drawn and the middle of every corridor. Each row
      and column knows its blocked intervals (obstacles, and runs of other pipes on that very line) and where
      other pipes cross it, so a step is checked with a binary search. */
  function search(s, sdir, e, ctx) {
    const xs = new Set([s[0], e[0]]), ys = new Set([s[1], e[1]]);
    const b0 = ctx.bounds;
    for (const r of ctx.obstacles) { xs.add(snap(r.x)); xs.add(snap(r.x + r.w)); ys.add(snap(r.y)); ys.add(snap(r.y + r.h)); }
    if (b0) { xs.add(b0.x); xs.add(b0.x + b0.w); ys.add(b0.y); ys.add(b0.y + b0.h); }
    const g = ctx.track;
    for (const o of ctx.others) for (const p of o) { xs.add(snap(p[0] - g)); xs.add(snap(p[0] + g)); ys.add(snap(p[1] - g)); ys.add(snap(p[1] + g)); }
    const thicken = set => {
      const v = [...set].filter(x => !b0 || true).sort((a, b) => a - b);
      for (let i = 1; i < v.length; i++) if (v[i] - v[i - 1] > 2 * g) set.add(snap((v[i] + v[i - 1]) / 2));
      return [...set].filter(x => !b0 || (x >= Math.min(b0.x, b0.y) - 1e9)).sort((a, b) => a - b);
    };
    const X = thicken(xs).filter(x => !b0 || (x >= b0.x && x <= b0.x + b0.w));
    const Y = thicken(ys).filter(y => !b0 || (y >= b0.y && y <= b0.y + b0.h));
    const ix = new Map(X.map((v, i) => [v, i])), iy = new Map(Y.map((v, i) => [v, i]));
    if (!ix.has(s[0]) || !iy.has(s[1]) || !ix.has(e[0]) || !iy.has(e[1])) return { ok: false, reason: "a pipe end is outside the drawing" };
    const rowBlock = Y.map(() => new Intervals()), colBlock = X.map(() => new Intervals());
    const rowCross = Y.map(() => []), colCross = X.map(() => []);
    const rowsIn = (a, b) => { const out = []; for (let j = 0; j < Y.length; j++) if (Y[j] > a && Y[j] < b) out.push(j); return out; };
    const colsIn = (a, b) => { const out = []; for (let i = 0; i < X.length; i++) if (X[i] > a && X[i] < b) out.push(i); return out; };
    for (const r of ctx.obstacles) {
      for (const j of rowsIn(r.y, r.y + r.h)) rowBlock[j].add(r.x, r.x + r.w);
      for (const i of colsIn(r.x, r.x + r.w)) colBlock[i].add(r.y, r.y + r.h);
    }
    for (const o of ctx.others) for (const [p, q] of segs(o)) {
      if (p[1] === q[1]) {
        if (iy.has(p[1])) rowBlock[iy.get(p[1])].add(p[0], q[0]);        // a run on this very line
        for (const i of colsIn(Math.min(p[0], q[0]), Math.max(p[0], q[0]))) colCross[i].push(p[1]);
      } else {
        if (ix.has(p[0])) colBlock[ix.get(p[0])].add(p[1], q[1]);
        for (const j of rowsIn(Math.min(p[1], q[1]), Math.max(p[1], q[1]))) rowCross[j].push(p[0]);
      }
    }
    rowBlock.forEach(x => x.done()); colBlock.forEach(x => x.done());
    rowCross.forEach(x => x.sort((a, b) => a - b)); colCross.forEach(x => x.sort((a, b) => a - b));
    const key = (i, j, d) => (i * 65536 + j) * 4 + "NESW".indexOf(d);
    const h = (i, j) => Math.abs(X[i] - e[0]) + Math.abs(Y[j] - e[1]);
    const start = [ix.get(s[0]), iy.get(s[1])], goal = [ix.get(e[0]), iy.get(e[1])];
    const open = new Heap();
    open.push({ i: start[0], j: start[1], d: sdir, g: 0, f: h(start[0], start[1]), prev: null, tie: 0 });
    const best = new Map([[key(start[0], start[1], sdir), 0]]);
    const t0 = Date.now();
    let n = 0, tie = 0;
    while (open.a.length) {
      if (++n > LIMIT.states || ((n & 255) === 0 && Date.now() - t0 > LIMIT.ms)) return { ok: false, reason: "routing took too long; move the equipment apart or route it by hand" };
      const cur = open.pop();
      if (cur.g > best.get(key(cur.i, cur.j, cur.d))) continue;      // a stale entry
      if (cur.i === goal[0] && cur.j === goal[1]) {
        const pts = [];
        for (let x = cur; x; x = x.prev) pts.unshift([X[x.i], Y[x.j]]);
        return { ok: true, points: pts };
      }
      for (const d of "NESW") {
        if (DIR[d][0] === -DIR[cur.d][0] && DIR[d][1] === -DIR[cur.d][1]) continue;   // no U-turn on the spot
        const ni = cur.i + DIR[d][0], nj = cur.j + DIR[d][1];
        if (ni < 0 || nj < 0 || ni >= X.length || nj >= Y.length) continue;
        let blocked, cross;
        if (nj === cur.j) {
          const a = Math.min(X[cur.i], X[ni]), b = Math.max(X[cur.i], X[ni]);
          blocked = rowBlock[cur.j].hits(a, b); cross = blocked ? 0 : sortedCount(rowCross[cur.j], a, b);
        } else {
          const a = Math.min(Y[cur.j], Y[nj]), b = Math.max(Y[cur.j], Y[nj]);
          blocked = colBlock[cur.i].hits(a, b); cross = blocked ? 0 : sortedCount(colCross[cur.i], a, b);
        }
        if (blocked) continue;
        const g2 = cur.g + Math.abs(X[ni] - X[cur.i]) + Math.abs(Y[nj] - Y[cur.j]) + (d === cur.d ? 0 : COST.bend) + COST.cross * cross;
        const kk = key(ni, nj, d);
        if (best.has(kk) && best.get(kk) <= g2) continue;
        best.set(kk, g2);
        open.push({ i: ni, j: nj, d, g: g2, f: g2 + h(ni, nj), prev: cur, tie: ++tie });
      }
    }
    return { ok: false, reason: "no free path between these ports; move the equipment or remove what is in the way" };
  }
  /**
   * route({ start: {x, y, side, owner}, end: {x, y, side, owner}, obstacles: [{id, x, y, w, h}],
   *         others: [[points]...], pins: [{axis, value}], previous: [points], bounds: {x,y,w,h},
   *         clearance, stub }) -> { ok: true, points, cost } | { ok: false, reason }
   */
  function route(q) {
    const clearance = q.clearance == null ? 10 : q.clearance, stub = q.stub == null ? 20 : q.stub;
    let r = route1(q, clearance, stub);
    if (!r.ok && clearance > 2) {                      // symbols close together: a tighter clearance may fit
      const r2 = route1(q, clearance / 2, Math.max(clearance / 2 + 2, stub / 2));
      if (r2.ok) r = Object.assign(r2, { tight: true });
    }
    return r;
  }
  function route1(q, clearance, stub) {
    const ctx = { obstacles: (q.obstacles || []).map(r => ({ id: r.id, x: r.x - clearance, y: r.y - clearance, w: r.w + 2 * clearance, h: r.h + 2 * clearance })),
      others: (q.others || []).filter(o => o && o.length >= 2), previous: q.previous || null, bounds: q.bounds || null,
      startOwner: q.start.owner, endOwner: q.end.owner, clearance, track: q.track == null ? 10 : q.track,
      startSide: q.start.side, endSide: q.end.side };
    const A = [snap(q.start.x), snap(q.start.y)], B = [snap(q.end.x), snap(q.end.y)];
    if (same(A, B)) return { ok: false, reason: "both ends are at the same point" };
    const s = stubPoint(q.start, stub), e = stubPoint(q.end, stub);
    let best = null;
    const consider = mid => {
      const pts = canonical([A].concat(mid, [B]));
      const c = evaluate(pts, ctx);
      if (c != null && (!best || c < best.cost - 1e-9)) best = { ok: true, points: pts, cost: c };
    };
    for (const cand of candidates(s, e, q.pins)) consider(cand);
    const pinned = q.pins && q.pins.length;
    if (pinned && best && pinsKept(best.points, q.pins)) return best;
    if (!best || bends(best.points) > 4) {
      // the stubs leave the symbols; the search runs outside both owners' boxes, among the obstacles near them
      const m = 4 * stub + 200;
      const x1 = Math.min(s[0], e[0]) - m, x2 = Math.max(s[0], e[0]) + m, y1 = Math.min(s[1], e[1]) - m, y2 = Math.max(s[1], e[1]) + m;
      const near = ctx.obstacles.filter(o => o.x < x2 && o.x + o.w > x1 && o.y < y2 && o.y + o.h > y1);
      const r = search(s, q.start.side, e, Object.assign({}, ctx, { obstacles: near }));
      if (r.ok) consider(r.points);
      else if (!best) return r;
    }
    if (!best) return { ok: false, reason: "no free path between these ports" };
    if (pinned && !pinsKept(best.points, q.pins)) best.pinsDropped = true;   // the caller says so beside the pipe
    return best;
  }
  /** Is a route (for instance a drag preview) acceptable as it stands? The router's own rules. */
  function valid(points, q) {
    const clearance = q.clearance == null ? 10 : q.clearance;
    const ctx = { obstacles: (q.obstacles || []).map(r => ({ id: r.id, x: r.x - clearance, y: r.y - clearance, w: r.w + 2 * clearance, h: r.h + 2 * clearance })),
      others: (q.others || []).filter(o => o && o.length >= 2), previous: null, bounds: q.bounds || null,
      startOwner: q.start && q.start.owner, endOwner: q.end && q.end.owner, clearance,
      startSide: q.start && q.start.side, endSide: q.end && q.end.side };
    return evaluate(canonical(points), ctx) != null;
  }
  function pinsKept(points, pins) {
    return pins.every(p => segs(points).some(([a, b]) => p.axis === "x" ? (a[0] === b[0] && a[0] === p.value) : (a[1] === b[1] && a[1] === p.value)));
  }

  /**
   * Drag one segment of a route perpendicular to itself by `delta` (document units). Its neighbours stretch;
   * both ends stay on their ports. The end segments (port stubs) are split first so the anchor never moves.
   * Returns { points, pin } (a pin keeps the moved segment where the user put it).
   */
  function dragSegment(points, index, delta, minStub) {
    let pts = points.map(p => [p[0], p[1]]);
    const ms = minStub == null ? 10 : minStub;
    if (index < 0 || index >= pts.length - 1) return { points: pts, pin: null };
    const split = (i, atStart) => {                 // insert a short piece at the anchor end of segment i
      const a = pts[i], b = pts[i + 1];
      const horiz = a[1] === b[1];
      const len = horiz ? b[0] - a[0] : b[1] - a[1];
      const step = Math.sign(len) * Math.min(ms, Math.abs(len) / 3);
      if (atStart) {
        const q = horiz ? [a[0] + step, a[1]] : [a[0], a[1] + step];
        pts.splice(i + 1, 0, q, [q[0], q[1]]);
        return i + 2;
      }
      const q = horiz ? [b[0] - step, b[1]] : [b[0], b[1] - step];
      pts.splice(i + 1, 0, [q[0], q[1]], q);
      return i;
    };
    if (index === 0) index = split(0, true);
    if (index === pts.length - 2) index = split(index, false);
    const a = pts[index], b = pts[index + 1];
    const horiz = a[1] === b[1];
    if (horiz) { a[1] = snap(a[1] + delta); b[1] = a[1]; } else { a[0] = snap(a[0] + delta); b[0] = a[0]; }
    const out = canonical(pts);
    return { points: out, pin: horiz ? { axis: "y", value: a[1] } : { axis: "x", value: a[0] } };
  }

  /** Crossing marks of a set of routes: the route whose id sorts later carries the gap (stable order). */
  function gaps(routes) {
    const ids = Object.keys(routes).sort();
    const out = {};
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      for (const p of crossings(routes[ids[i]], routes[ids[j]])) (out[ids[j]] = out[ids[j]] || []).push(p);
    }
    return out;
  }

  const api = { route, valid, portsStraight, dragSegment, canonical, orthogonal, doublesBack, crossings, gaps, length, bends, evaluate, snap, EPS, COST, LIMIT };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AqRouter = api;
})(typeof window !== "undefined" ? window : this);
