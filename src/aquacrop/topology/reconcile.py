"""The earlier drawing's pipes as connections, with one reconciliation for what only its geometry suggests (Codex 0.7.7
C077-08, owner request of 27 October 2026: import the Overview's complete diagram, pipes and connections so it is
ready for simulation).

Explicit in the earlier drawing, and kept without asking: a pipe's named ends (`from_node`, `to_node`), the item that
gates it (`item`) and its flow meter (`flow_item`), in series in the order they are drawn along it; pipes the drawing
attaches to one single port get the junction that port needs (which ends are linked does not change).

Suggested only by geometry, and asked once as a factual question with the drawing's suggestion: equipment drawn on a
pipe without being linked to it, a pipe end that only touches a symbol or another pipe, and a sensor drawn by a pipe or
a tank. An answer is applied only when it is yes; an open question or a dangling end keeps that pipe a dotted sketch
and is a named blocker. Nothing else connects: crossings never join, a meter is never bypassed by a guess, and the
result is never called ready while a blocker is left. The answers are kept by the twin per earlier drawing, so a later
Import from Overview applies them again without asking.
"""
from __future__ import annotations

from .. import diagram as dg
from .. import layout as legacy
from .definitions import parse, ports_of

TAP_NEAR = 30.0              # a sensor drawn this close to a pipe is asked whether it measures that pipe
TANK_NEAR = 80.0             # ... or this close to a tank, whether it measures that tank
FAN_OUT = 20.0               # a junction for pipes sharing a single port, when their drawn routes part at once
JOIN_END = 6.0               # a loose pipe end where another pipe starts on it continues into that junction


def _point_at(pts, t):
    from ..twin.water import _seglen
    along = 0.0
    for a, b in zip(pts, pts[1:]):
        L = _seglen(a, b)
        if along + L >= t - 1e-9:
            f = 0.0 if L == 0 else max(0.0, min(1.0, (t - along) / L))
            return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
        along += L
    return tuple(pts[-1])


def _inside(pts, t0, t1):
    """The drawn corners strictly between two distances along a pipe."""
    from ..twin.water import _seglen
    out, along = [], 0.0
    for i, q in enumerate(pts):
        if i:
            along += _seglen(pts[i - 1], q)
        if t0 + 0.5 < along < t1 - 0.5:
            out.append(tuple(q))
    return out


def _dir_at(pts, t) -> str:
    """The way a pipe runs at a distance along it (the side its flow heads to)."""
    from ..twin.water import _seglen
    along = 0.0
    for a, b in zip(pts, pts[1:]):
        L = _seglen(a, b)
        if L and along + L >= t - 1e-9:
            return _side(a, b)
        along += L
    return _side(pts[-2], pts[-1]) if len(pts) > 1 else "E"


def _side(frm, to) -> str:
    dx, dy = to[0] - frm[0], to[1] - frm[1]
    return ("E" if dx > 0 else "W") if abs(dx) >= abs(dy) else ("S" if dy > 0 else "N")


def pipes(lay, nodes, symbols, by_legacy, measures, names, item_kinds, answers, taken, texts, shapes, placeholders,
          to_create, unresolved, ambiguous):
    """(graph, diagram, extra) for `migrate.propose`; extra carries questions, blockers, ready and the counts."""
    from ..twin.water import Network, _length, _project
    from .migrate import DEFAULT_IN, DEFAULT_OUT, _edge_id, _safe_id
    from .validation import issues as topo_issues
    try:
        net = Network(legacy.canonical(lay))
    except Exception:                               # an unreadable old drawing: everything stays for review
        net = None
    word = lambda nid: names.get(nid, nid)          # noqa: E731
    leg = {n.id: n for n in lay.nodes}
    pos_of = {by_legacy[k]: (leg[k].x, leg[k].y) for k in by_legacy}
    pts_of = {p.id: [tuple(q) for q in p.points] for p in lay.pipes}
    length = {pid: _length(pts) for pid, pts in pts_of.items()}
    questions, blockers = [], []

    def ask(qid, kind, text, value, pipe=None):
        a = answers.get(qid)
        a = a if a in ("yes", "no") else None
        questions.append({"id": qid, "kind": kind, "pipe": pipe, "text": text, "suggest": "yes", "value": value,
                          "answer": a})
        return a

    plan = {}
    for p in lay.pipes:
        info = net.pipes.get(p.id) if net else None
        pl = {"start": None, "end": None, "members": [], "why": [], "open": [], "dangles": []}
        plan[p.id] = pl
        for end, named, touch in (("start", p.from_node, info.start_node if info else None),
                                  ("end", p.to_node, info.end_node if info else None)):
            nid = by_legacy.get(named or "")
            if nid:
                pl[end] = ("node", nid)
            elif named:
                pl["why"].append(f"its {end} is drawn at {leg[named].label or named}, which is not linked to equipment yet"
                                 if named in leg else f"its {end} names {named}, which is not in the drawing")
            elif touch and by_legacy.get(touch):
                nid = by_legacy[touch]
                qid = f"{end}:{p.id}"
                a = ask(qid, "ATTACH", f"The {end} of the earlier pipe {p.id} touches {word(nid)}: does the pipe "
                        f"{'start' if end == 'start' else 'end'} there?", {"node": nid}, p.id)
                if a == "yes":
                    pl[end] = ("node", nid)
                elif a is None:
                    pl["open"].append(qid)
                else:
                    pl["why"].append(f"its {end} does not attach to {word(nid)} (answered)")
            elif touch:
                pl["why"].append(f"its {end} touches {leg[touch].label or touch}, which is not linked to equipment yet")
            else:
                host = None
                if end == "start" and net:
                    host = next(((q, tt) for q, qi in net.pipes.items() for tt, b in qi.branches if b == p.id), None)
                elif end == "end" and info and info.end_join:
                    host = info.end_join
                if host:
                    qid = f"{end}:{p.id}"
                    a = ask(qid, "JOIN", f"The earlier pipe {p.id} {'starts' if end == 'start' else 'ends'} on pipe "
                            f"{host[0]}: do they join there (a junction)?", {"pipe": host[0], "t": round(host[1], 1)}, p.id)
                    if a == "yes":
                        pl[end] = ("pipe", host[0], host[1])
                    elif a is None:
                        pl["open"].append(qid)
                    else:
                        pl["why"].append(f"its {end} does not join pipe {host[0]} (answered)")
                else:
                    pl["dangles"].append(end)
        # explicit members, then what is only drawn on it
        ends = {x[1] for x in (pl["start"], pl["end"]) if x and x[0] == "node"}
        ends |= {by_legacy.get(p.from_node or ""), by_legacy.get(p.to_node or "")}
        explicit = [x for x in dict.fromkeys((p.item, p.flow_item)) if x and x in nodes and x in pos_of
                    and nodes[x]["kind"] in ("valve", "pump", "meter") and x not in ends]
        for x in explicit:
            pl["members"].append((_project(pts_of[p.id], *pos_of[x])[0], x))
        for tt, lid in (info.inline if info else []):
            x = by_legacy.get(lid)
            if not x or x in ends or x in explicit or nodes[x]["kind"] not in ("valve", "pump", "meter"):
                continue
            qid = f"inline:{p.id}:{x}"
            a = ask(qid, "INLINE", f"{word(x)} is drawn on the earlier pipe {p.id} without being linked to it: is it in "
                    "series in that pipe?", {"node": x}, p.id)
            if a == "yes":
                pl["members"].append((tt, x))
            elif a is None:
                pl["open"].append(qid)
        pl["members"].sort()
    for pid, pl in plan.items():
        if "end" in pl["dangles"] and pl["end"] is None:
            on_me = [x["start"] for x in plan.values() if x["start"] and x["start"][0] == "pipe" and x["start"][1] == pid
                     and x["start"][2] >= length[pid] - JOIN_END]
            if on_me:                                # its loose end is where another pipe starts on it
                pl["end"] = on_me[0]
            else:
                pl["why"].append("its end is not attached to anything in the earlier drawing")
        if "start" in pl["dangles"]:
            pl["why"].append("its start is not attached to anything in the earlier drawing")
    # connected: both ends, nothing open, and every pipe it joins is connected too
    ok = {pid for pid, pl in plan.items() if pl["start"] and pl["end"] and not pl["open"]}
    changed = True
    while changed:
        changed = False
        for pid in sorted(ok):
            for st in (plan[pid]["start"], plan[pid]["end"]):
                if st[0] == "pipe" and st[1] not in ok:
                    ok.discard(pid)
                    plan[pid]["why"].append(f"it joins pipe {st[1]}, which is not connected yet")
                    changed = True
                    break
    # the junctions on host pipes
    jpos: dict[str, list] = {}
    for pid in sorted(ok):
        for st in (plan[pid]["start"], plan[pid]["end"]):
            if st[0] == "pipe":
                lst = jpos.setdefault(st[1], [])
                if not any(abs(t0 - st[2]) <= 1.0 for t0, _ in lst):
                    lst.append((st[2], None))
    jnodes = {}
    for host in sorted(jpos):
        lst = sorted(jpos[host])
        for i, (tt, _) in enumerate(lst):
            jid = _safe_id(f"j-{host}-{i + 1}", taken | set(nodes))
            pt = _point_at(pts_of[host], tt)
            lst[i] = (tt, jid)
            jnodes[jid] = (dg.snap(pt[0]), dg.snap(pt[1]))
            nodes[jid] = {"id": jid, "kind": "junction", "item": None}
            symbols.append(dg.Symbol(node=jid, x=jnodes[jid][0], y=jnodes[jid][1]))
        jpos[host] = lst

    def jat(host, tt):
        return min(jpos[host], key=lambda x: abs(x[0] - tt))[1]
    # owner, 27 Oct 2026: equipment in series sits in its pipe, aligned with it like a valve: turned with the flow and
    # centred on the drawn line, so the pipe's drawn corners stay the route
    for pid in sorted(ok):
        for tt, x in plan[pid]["members"]:
            i = next(k for k, s in enumerate(symbols) if s.node == x)
            cx, cy = _point_at(pts_of[pid], tt)
            rot = {"E": 0, "S": 90, "W": 180, "N": 270}[_dir_at(pts_of[pid], tt)]
            s = symbols[i].model_copy(update={"rot": rot})
            _, _, w, h = dg.box(nodes[x]["kind"], s)
            symbols[i] = s.model_copy(update={"x": dg.snap(cx - w / 2), "y": dg.snap(cy - h / 2)})
    syms = {s.node: s for s in symbols}
    jused: dict[str, set] = {}

    def jport(jid, toward):
        side = _side(jnodes[jid], toward)
        used = jused.setdefault(jid, set())
        for s in [side] + [x for x in "NESW" if x != side]:
            if s.lower() not in used:
                used.add(s.lower())
                return s.lower()
        return None
    edges, routes, segs = [], [], []           # segs: (edge, pipe, t0, t1), for the sensor taps
    eids: set[str] = set()
    for pid in sorted(ok):
        pl, pts = plan[pid], pts_of[pid]
        s0, s1 = pl["start"], pl["end"]
        st = [(0.0, s0[1] if s0[0] == "node" else jat(s0[1], s0[2]))]
        st += [(tt, x) for tt, x in pl["members"]]
        st += [(tt, jid) for tt, jid in jpos.get(pid, []) if 0.5 < tt < length[pid] - 0.5]
        st.append((length[pid], s1[1] if s1[0] == "node" else jat(s1[1], s1[2])))
        st.sort(key=lambda x: x[0])
        mine, bad = [], None
        for i in range(len(st) - 1):
            (ta, a), (tb, b) = st[i], st[i + 1]
            ka, kb = nodes[a]["kind"], nodes[b]["kind"]
            inner = _inside(pts, ta, tb)
            pa_t, pb_t = _point_at(pts, ta), _point_at(pts, tb)
            po_a = jport(a, inner[0] if inner else pb_t) if ka == "junction" else (DEFAULT_OUT.get(ka) if i == 0 else "out")
            pi_b = jport(b, inner[-1] if inner else pa_t) if kb == "junction" else (
                DEFAULT_IN.get(kb) if i == len(st) - 2 else "in")
            if not po_a or not pi_b:
                bad = f"no default ports from a {ka} to a {kb}"
                break
            eid = _edge_id(pid if len(st) == 2 else f"{pid}-{i + 1}", eids)
            eids.add(eid)
            (pa, _), (pb, _) = dg.anchor(ka, syms[a], po_a), dg.anchor(kb, syms[b], pi_b)
            mine.append(({"id": eid, "src": {"node": a, "port": po_a}, "dst": {"node": b, "port": pi_b},
                          "provenance": "UNKNOWN"}, {"edge": eid, "points": dg.canonical_points([pa, *inner, pb]), "legacy": pid},
                         (eid, pid, ta, tb)))
        if bad:
            ok.discard(pid)
            pl["why"].append(bad)
            continue
        for e, r, s in mine:
            edges.append(e)
            routes.append(r)
            segs.append(s)
    _fan_junctions(nodes, symbols, edges, routes, taken, _safe_id, _edge_id)
    syms = {s.node: s for s in symbols}
    # sensors: an association, never a pipe; level sensors already matched to their tank stay so
    have = {m["sensor"] for m in measures}
    for lid, nid in sorted(by_legacy.items()):
        if nodes[nid]["kind"] != "sensor" or nid in have:
            continue
        x, y = leg[lid].x, leg[lid].y
        best = None
        for e, pid, t0, t1 in segs:
            tt, d = _project(pts_of[pid], x, y)
            if t0 - 1 <= tt <= t1 + 1 and d <= TAP_NEAR and (best is None or d < best[0]):
                best = (d, "edge", e, pid)
        if best is None:
            for tid, n in nodes.items():
                if n["kind"] != "tank" or tid not in syms:
                    continue
                bx, by_, bw, bh = dg.box("tank", syms[tid])
                d = ((max(bx, min(bx + bw, x)) - x) ** 2 + (max(by_, min(by_ + bh, y)) - y) ** 2) ** 0.5
                if d <= TANK_NEAR and (best is None or d < best[0]):
                    best = (d, "node", tid, None)
        if best is None:
            continue
        _, what, ref, pid = best
        if what == "edge":
            a = ask(f"tap:{nid}", "TAP", f"{word(nid)} is drawn by the earlier pipe {pid}: does it measure that pipe?",
                    {"edge": ref, "pipe": pid}, pid)
            if a == "yes":
                measures.append({"sensor": nid, "edge": ref, "mode": "TAP"})
        else:
            a = ask(f"tap:{nid}", "MEASURES", f"{word(nid)} is drawn by {word(ref)}: does it measure that tank?", {"node": ref})
            if a == "yes":
                measures.append({"sensor": nid, "node": ref, "mode": "TAP"})
    eids = {e["id"] for e in edges}
    graph = {"nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": edges,
             "measures": [m for m in measures if m["sensor"] in nodes and (m.get("node") in nodes or m.get("edge") in eids)]}
    topo = parse(graph)                              # raises if the proposal is malformed: a bug, never silent
    sketches = []
    for p in lay.pipes:
        if p.id in ok:
            continue
        pl = plan[p.id]
        why = "; ".join(dict.fromkeys(pl["why"])) or "waits for the answers listed: " + ", ".join(pl["open"])
        sketches.append(dg.Sketch(id=_safe_id(p.id, taken), points=[tuple(pt) for pt in p.points], why=why[:240]))
        inl = [q.split(":", 2)[2] for q in pl["open"] if q.startswith("inline:")]
        entry = {"legacy": p.id, "kind": "INLINE" if inl else "JOIN" if pl["open"] else
                 "PORTS" if why.startswith("no default ports") else "PIPE_END", "why": why, "questions": list(pl["open"]),
                 "fix": "answer the questions of the reconciliation" if pl["open"] else "connect its ports explicitly in the editor"}
        if inl:
            entry |= {"why": f"the old drawing had {', '.join(word(x) for x in inl)} drawn on this pipe",
                      "suggests": {"from": by_legacy.get(p.from_node or ""), "to": by_legacy.get(p.to_node or ""), "through": inl},
                      "fix": "decide the connection: through that equipment, through a junction, or not connected"}
        ambiguous.append(entry)
    view = dg.Diagram(view="installation", title=lay.title, width=max(lay.width, 400), height=max(lay.height, 300),
                      symbols=symbols, routes=[dg.Route(edge=r["edge"], points=r["points"], mode="MANUAL") for r in routes],
                      texts=texts, shapes=shapes, placeholders=placeholders)
    # a drawn route that is not horizontal/vertical, or does not meet its ports, is rerouted; the original stays visible
    bad = {i["object"] for i in dg.route_issues(view, topo) if i["severity"] == "ERROR" and i["rule"].startswith("ROUTE_")}
    kept, shown = [], set()
    for r in routes:
        if r["edge"] in bad:
            kept.append(dg.Route(edge=r["edge"], mode="AUTO"))
            if r.get("legacy") and r["legacy"] not in shown:
                shown.add(r["legacy"])
                sketches.append(dg.Sketch(id=_safe_id(r["legacy"] + "-drawn", taken), points=pts_of[r["legacy"]], edge=r["edge"],
                                          why="the pipe as it was drawn (not horizontal/vertical or off its ports): "
                                              "shown for reference; the connection is rerouted"))
        else:
            kept.append(dg.Route(edge=r["edge"], points=r["points"], mode="MANUAL"))
    view = view.model_copy(update={"routes": kept, "sketches": sketches})
    # what stands between this import and a simulation, by name (never "ready" while one is left)
    for c in to_create:
        blockers.append({"id": f"create:{c['legacy']}", "text": f"Create the {c['kind']} {c['name']} on Equipment (main "
                         "dashboard): the earlier drawing shows it and pipes end at it", "fix": "create it, then import again"})
    for u in unresolved:
        if not any(c["legacy"] == u["legacy"] for c in to_create):
            blockers.append({"id": f"map:{u['legacy']}", "text": f"{u['label']}: {u['why']}", "fix": u["fix"]})
    for q in questions:
        if q["answer"] is None:
            blockers.append({"id": f"answer:{q['id']}", "text": q["text"], "fix": "answer it in the reconciliation"})
    for p in lay.pipes:
        if p.id not in ok and not plan[p.id]["open"]:
            blockers.append({"id": f"pipe:{p.id}", "text": f"Earlier pipe {p.id}: {'; '.join(dict.fromkeys(plan[p.id]['why']))}",
                             "fix": "connect it in the editor, or answer its question"})
    items = {iid: {"kind": k, "name": names.get(iid, iid)} for iid, k in item_kinds.items()}
    for i in topo_issues(topo, items):
        if i["severity"] == "ERROR":
            blockers.append({"id": f"graph:{i['id']}", "text": i["message"], "fix": i["fix"]})
    return topo.model_dump(mode="json"), view, {
        "questions": questions, "blockers": blockers, "ready": not blockers,
        "kept": {"symbols": sum(1 for s in symbols if nodes[s.node]["kind"] != "junction"),
                 "placeholders": len(placeholders), "texts": len(texts), "shapes": len(shapes),
                 "pipes_connected": len(ok), "edges": len(edges),
                 "junctions": sum(1 for n in nodes.values() if n["kind"] == "junction"),
                 "routes_kept": sum(1 for r in kept if r.points), "routes_corrected": len(bad), "sketches": len(sketches)}}


def _fan_junctions(nodes, symbols, edges, routes, taken, _safe_id, _edge_id) -> None:
    """Pipes the earlier drawing attaches to one single port (two pumps on one valve inlet, one pump feeding two valves):
    that port needs a junction. It goes where their drawn routes part, else just outside the port. This is how the
    explicit links are written in the new graph, not a guess: which ends are linked does not change."""
    syms = {s.node: s for s in symbols}
    rmap = {r["edge"]: r for r in routes}
    groups: dict[tuple, list] = {}
    for e in edges:
        groups.setdefault(("src", e["src"]["node"], e["src"]["port"]), []).append(e)
        groups.setdefault(("dst", e["dst"]["node"], e["dst"]["port"]), []).append(e)
    for (end, nid, port), es in sorted(groups.items(), key=lambda kv: kv[0]):
        kind = nodes[nid]["kind"]
        if len(es) < 2 or len(es) > 3 or kind == "junction" or ports_of(kind)[port].many:
            continue
        lines = [list(rmap[e["id"]]["points"]) if end == "src" else list(reversed(rmap[e["id"]]["points"])) for e in es]
        common = 0
        while all(len(ln) > common + 1 for ln in lines) and all(ln[common] == lines[0][common] for ln in lines):
            common += 1
        (px, py), side = dg.anchor(kind, syms[nid], port)
        if common >= 2:
            jx, jy = lines[0][common - 1]
        else:
            dx, dy = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}[side]
            jx, jy = px + dx * FAN_OUT, py + dy * FAN_OUT
            common = 1
        jid = _safe_id(f"j-{nid}-{port}", taken | set(nodes))
        nodes[jid] = {"id": jid, "kind": "junction", "item": None}
        js = dg.Symbol(node=jid, x=dg.snap(jx), y=dg.snap(jy))
        symbols.append(js)
        syms[jid] = js
        back = _side((jx, jy), lines[0][common - 2] if common >= 2 else (px, py)).lower()
        stem = lines[0][:common - 1] + [(jx, jy)] if common >= 2 else [(px, py), (jx, jy)]
        eid = _edge_id(f"{nid}-{port}", {e["id"] for e in edges})
        free = [s for s in "nesw" if s != back]
        legacy_of = rmap[es[0]["id"]].get("legacy")
        if end == "src":
            edges.append({"id": eid, "src": {"node": nid, "port": port}, "dst": {"node": jid, "port": back},
                          "provenance": "UNKNOWN"})
            routes.append({"edge": eid, "points": dg.canonical_points(stem), "legacy": legacy_of})
        else:
            edges.append({"id": eid, "src": {"node": jid, "port": back}, "dst": {"node": nid, "port": port},
                          "provenance": "UNKNOWN"})
            routes.append({"edge": eid, "points": dg.canonical_points(list(reversed(stem))), "legacy": legacy_of})
        rmap[eid] = routes[-1]
        for e, ln in zip(es, lines):
            rest = [(jx, jy)] + ln[common:]
            want = _side((jx, jy), rest[1]).lower() if len(rest) > 1 else free[0]
            jp = want if want in free else free[0]
            free.remove(jp)
            if end == "src":
                e["src"] = {"node": jid, "port": jp}
                rmap[e["id"]]["points"] = dg.canonical_points(rest)
            else:
                e["dst"] = {"node": jid, "port": jp}
                rmap[e["id"]]["points"] = dg.canonical_points(list(reversed(rest)))
