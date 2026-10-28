"""Checks of a hydraulic graph, and the paths water can take along it.

Each issue is a dict: a stable `id` (rule and object, so the same problem is one issue across checks), `severity`
ERROR (prevents validation of a block or a runnable process) or WARNING (shown, saved), the affected `object`,
the `rule`, a `message` in plain words and the `fix` to try. Drafts save with errors; they do not validate.

What these checks can prove is structure: ports fit, every connection is explicit, a meter is on the path it is
said to measure. They cannot prove that the drawing matches the real pipes, that a pump is sized correctly or that
a material suits a chemical; the API and the page say so beside the result.
"""
from __future__ import annotations

from collections import defaultdict

from .definitions import SYMBOLS, Topology, ports_of

PASS_THROUGH = {"pump", "valve", "meter", "junction"}     # water passes through them along explicit ports


def _issue(out: list, rule: str, obj: str, message: str, fix: str, severity: str = "ERROR", field: str | None = None):
    out.append({"id": f"{rule}:{obj}", "severity": severity, "object": obj, "field": field, "rule": rule,
                "message": message, "fix": fix})


def issues(t: Topology, items: dict[str, dict] | None = None) -> list[dict]:
    """`items`: equipment item id -> {"kind": its type's hydraulic kind, "name": ...}. None skips item checks."""
    out: list[dict] = []
    nodes = {n.id: n for n in t.nodes}
    name = lambda nid: (items or {}).get(nodes[nid].item or "", {}).get("name") or nid       # noqa: E731
    if items is not None:
        for n in t.nodes:
            if n.item is None:
                continue
            it = items.get(n.item)
            if it is None:
                _issue(out, "ITEM_MISSING", n.id, f"{n.id}: equipment item {n.item} does not exist (or is retired)",
                       "choose an existing item or remove the symbol")
            elif it.get("kind") != n.kind:
                _issue(out, "ITEM_KIND", n.id, f"{name(n.id)} is a {it.get('kind') or 'non-water'} item, drawn as a {n.kind}",
                       "use a symbol of the item's own kind")
    use: dict[str, list[str]] = defaultdict(list)
    pairs = set()
    for e in t.edges:
        bad = False
        for end, ref, allowed in (("from", e.src, ("out", "both")), ("to", e.dst, ("in", "both"))):
            n = nodes.get(ref.node)
            if n is None:
                _issue(out, "EDGE_NODE", e.id, f"pipe {e.id}: its {end} end names {ref.node}, which is not in the graph",
                       "reconnect the pipe or remove it", field=end)
                bad = True
                continue
            p = ports_of(n.kind).get(ref.port)
            if p is None:
                _issue(out, "EDGE_PORT", e.id, f"pipe {e.id}: {name(n.id)} has no port {ref.port}",
                       f"connect it to one of {', '.join(ports_of(n.kind)) or 'no port (a sensor is associated, not piped)'}",
                       field=end)
                bad = True
                continue
            if p.flow not in allowed:
                _issue(out, "EDGE_DIRECTION", e.id,
                       f"pipe {e.id}: water cannot {'leave' if end == 'from' else 'enter'} {name(n.id)} by its "
                       f"{p.label.lower()}", "reverse the pipe or use the other port", field=end)
                bad = True
            use[ref.key()].append(e.id)
        if bad:
            continue
        if e.src.node == e.dst.node:
            _issue(out, "EDGE_SELF", e.id, f"pipe {e.id} joins {name(e.src.node)} to itself",
                   "remove it, or route a recirculation through other equipment")
        key = (e.src.key(), e.dst.key())
        if key in pairs:
            _issue(out, "EDGE_DUPLICATE", e.id, f"pipe {e.id} repeats a connection that already exists", "remove it")
        pairs.add(key)
    for key, eids in use.items():
        nid, port = key.split(".", 1)
        p = ports_of(nodes[nid].kind)[port]
        if len(eids) > 1 and not p.many:
            _issue(out, "PORT_SHARED", key, f"{name(nid)} {p.label.lower()} has {len(eids)} pipes ({', '.join(sorted(eids))})",
                   "join them with an explicit junction first")
    for n in t.nodes:
        connected = [p for p in ports_of(n.kind) if f"{n.id}.{p}" in use]
        if n.kind == "junction" and len(connected) < 2:
            _issue(out, "JUNCTION_OPEN", n.id, f"junction {n.id} joins {len(connected)} pipe(s)",
                   "a junction joins at least two pipes; connect it or remove it")
        elif n.kind in ("pump", "valve", "meter") and len(connected) == 1:
            _issue(out, "PORT_OPEN", n.id, f"{name(n.id)} is connected on one side only",
                   "connect its other port; water cannot pass a dead end", severity="WARNING")
        elif n.kind in ("pump", "valve", "meter", "tank", "source", "sector") and not connected:
            _issue(out, "NODE_UNCONNECTED", n.id, f"{name(n.id)} has no pipe", "connect it or remove it from this graph",
                   severity="WARNING")
    measured = set()
    edges = {e.id for e in t.edges}
    for m in t.measures:
        s = nodes.get(m.sensor)
        if s is None or s.kind != "sensor":
            _issue(out, "MEASURE_SENSOR", m.sensor, f"{m.sensor} is not a sensor of this graph", "choose a sensor")
            continue
        if m.node is not None:
            tgt = nodes.get(m.node)
            if tgt is None or tgt.kind not in ("tank", "sector"):
                _issue(out, "MEASURE_TARGET", m.sensor, f"{name(m.sensor)} measures {m.node}, which is not a tank or a sector",
                       "associate it with the tank whose level it reads")
        elif m.edge not in edges:
            _issue(out, "MEASURE_TARGET", m.sensor, f"{name(m.sensor)} measures pipe {m.edge}, which does not exist",
                   "associate it with the pipe it taps")
        measured.add(m.sensor)
    for n in t.nodes:
        if n.kind == "sensor" and n.id not in measured:
            _issue(out, "MEASURE_NONE", n.id, f"{name(n.id)} is drawn but measures nothing",
                   "associate it with the tank or pipe it measures; until then no rule can use it as process feedback",
                   severity="WARNING")
    return out


# ---------------------------------------------------------------------- paths
def _inner(kind: str, port_in: str) -> list[str]:
    """Ports water entering a node by `port_in` may leave by, for nodes water passes through."""
    if kind == "junction":
        return [p for p in ports_of(kind) if p != port_in]
    if kind == "valve":
        return [p for p in ("in", "out") if p != port_in]
    if kind in ("pump", "meter"):
        return ["out"] if port_in == "in" else []
    return []


def outgoing(t: Topology) -> dict[str, list]:
    """port key -> edges that start there, in the direction water may flow (valves and junctions both ways)."""
    nodes = {n.id: n for n in t.nodes}
    out: dict[str, list] = defaultdict(list)
    for e in t.edges:
        if e.src.node not in nodes or e.dst.node not in nodes:
            continue
        out[e.src.key()].append((e, e.dst))
        sp = ports_of(nodes[e.src.node].kind).get(e.src.port)
        dp = ports_of(nodes[e.dst.node].kind).get(e.dst.port)
        if sp is not None and dp is not None and sp.flow == "both" and dp.flow == "both":
            out[e.dst.key()].append((e, e.src))        # a pipe between two two-way ports carries either way
    return out


def paths_from(t: Topology, node: str, port: str | None = None, limit: int = 200) -> list[dict]:
    """Every simple path water can take from `node` (by `port`, or by any port it may leave by) to the first
    storage or terminal it reaches (a tank, a sector, or a source as a sink is not one). Each path:
    {"to": node, "port": entered port, "nodes": [...pass-through nodes in order], "edges": [...]}"""
    nodes = {n.id: n for n in t.nodes}
    if node not in nodes:
        return []
    out_by = outgoing(t)
    starts = [port] if port else [p.id for p in SYMBOLS[nodes[node].kind]["ports"] if p.flow in ("out", "both")]
    found: list[dict] = []

    def walk(key: str, via_nodes: list[str], via_edges: list[str]):
        if len(found) >= limit:
            return
        for e, dst in out_by.get(key, []):
            if e.id in via_edges:
                continue
            n = nodes[dst.node]
            if n.kind in PASS_THROUGH:
                if n.id in via_nodes or n.id == node:
                    continue
                for nxt in _inner(n.kind, dst.port):
                    walk(f"{n.id}.{nxt}", via_nodes + [n.id], via_edges + [e.id])
            elif n.kind in ("tank", "sector"):
                found.append({"to": n.id, "port": dst.port, "nodes": via_nodes, "edges": via_edges + [e.id]})
    for p in starts:
        walk(f"{node}.{p}", [], [])
    return found
