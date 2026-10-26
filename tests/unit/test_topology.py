"""0.7.0 explicit hydraulic graph and its drawing (TOPO-01..05, RTE-04/05/07 backend part)."""
import math

import pytest
from pydantic import ValidationError

from aquacrop import diagram as dg
from aquacrop.topology.definitions import Topology, digest, parse
from aquacrop.topology.validation import issues, paths_from

ITEMS = {"src": {"kind": "source", "name": "Mains"}, "tank": {"kind": "tank", "name": "Main tank"},
         "pump": {"kind": "pump", "name": "Main pump"}, "meter": {"kind": "meter", "name": "Flow meter"},
         "valve": {"kind": "valve", "name": "Sector valve"}, "sector": {"kind": "sector", "name": "Sector 1"},
         "lvl": {"kind": "sensor", "name": "Tank level"}, "drain": {"kind": "tank", "name": "Drain tank"},
         "rpump": {"kind": "pump", "name": "Return pump"}, "suction": {"kind": "valve", "name": "Suction valve"},
         "p2": {"kind": "pump", "name": "Pump 2"}, "pres": {"kind": "sensor", "name": "Line pressure"}}


def node(i, kind=None):
    return {"id": i, "kind": kind or ITEMS[i]["kind"], "item": i}


def edge(i, a, b):
    an, ap = a.split(".")
    bn, bp = b.split(".")
    return {"id": i, "src": {"node": an, "port": ap}, "dst": {"node": bn, "port": bp}}


def line(extra_nodes=(), extra_edges=(), measures=()):
    """source -> tank -> pump -> meter -> valve -> sector -> drain tank -> return pump -> tank.return"""
    nodes = [node(i) for i in ("src", "tank", "pump", "meter", "valve", "sector", "lvl", "drain", "rpump")]
    edges = [edge("e1", "src.out", "tank.in"), edge("e2", "tank.out", "pump.in"), edge("e3", "pump.out", "meter.in"),
             edge("e4", "meter.out", "valve.in"), edge("e5", "valve.out", "sector.in"),
             edge("e6", "sector.drain", "drain.in"), edge("e7", "drain.out", "rpump.in"),
             edge("e8", "rpump.out", "tank.return")]
    return parse({"nodes": nodes + list(extra_nodes), "edges": edges + list(extra_edges),
                  "measures": [{"sensor": "lvl", "node": "tank"}] + list(measures)})


def errors(t, items=ITEMS):
    return [i for i in issues(t, items) if i["severity"] == "ERROR"]


def test_topo01_a_valid_line_with_return_validates_and_paths_follow_the_edges():
    t = line()
    assert errors(t) == []
    p = paths_from(t, "tank", "out")
    assert [(x["to"], x["nodes"]) for x in p] == [("sector", ["pump", "meter", "valve"])]
    assert paths_from(t, "drain", "out")[0]["to"] == "tank" and paths_from(t, "drain", "out")[0]["port"] == "return"


def test_topo02_precise_errors_for_wrong_ports_directions_and_missing_objects():
    t = parse({"nodes": [node("pump"), node("tank"), node("meter")],
               "edges": [edge("x1", "pump.in", "tank.in"), edge("x2", "tank.out", "pump.nope"),
                         edge("x3", "meter.out", "ghost.in")]})
    rules = {(i["rule"], i["object"]) for i in errors(t)}
    assert ("EDGE_DIRECTION", "x1") in rules and ("EDGE_PORT", "x2") in rules and ("EDGE_NODE", "x3") in rules
    t = parse({"nodes": [node("pump", "valve")], "edges": []})
    assert {i["rule"] for i in errors(t)} == {"ITEM_KIND"}
    for i in issues(line(), ITEMS):
        assert {"id", "severity", "object", "rule", "message", "fix"} <= set(i)


def test_topo03_a_suction_valve_before_the_pump_and_a_recirculation_are_valid():
    t = parse({"nodes": [node("tank"), node("suction"), node("pump")],
               "edges": [edge("a", "tank.out", "suction.in"), edge("b", "suction.out", "pump.in"),
                         edge("c", "pump.out", "tank.return")]})
    assert errors(t) == []
    assert paths_from(t, "tank", "out")[0] == {"to": "tank", "port": "return", "nodes": ["suction", "pump"],
                                               "edges": ["a", "b", "c"]}


def test_topo04_parallel_pumps_need_a_junction_and_series_pumps_are_a_chain():
    shared = parse({"nodes": [node("tank"), node("pump"), node("p2"), node("sector")],
                    "edges": [edge("a", "tank.out", "pump.in"), edge("b", "tank.out", "p2.in"),
                              edge("c", "pump.out", "sector.in"), edge("d", "p2.out", "sector.in")]})
    assert errors(shared) == []                   # tank outlet and sector inlet take several pipes
    par = {tuple(x["nodes"]) for x in paths_from(shared, "tank", "out")}
    assert par == {("pump",), ("p2",)}             # two alternatives
    series = parse({"nodes": [node("tank"), node("pump"), node("p2"), node("sector")],
                    "edges": [edge("a", "tank.out", "pump.in"), edge("b", "pump.out", "p2.in"),
                              edge("c", "p2.out", "sector.in")]})
    assert [x["nodes"] for x in paths_from(series, "tank", "out")] == [["pump", "p2"]]
    two_on_one = parse({"nodes": [node("pump"), node("p2"), node("meter")],
                        "edges": [edge("a", "pump.out", "meter.in"), edge("b", "p2.out", "meter.in")]})
    assert {i["rule"] for i in errors(two_on_one)} == {"PORT_SHARED"}   # an inline meter inlet: join first


def test_junctions_join_and_a_tap_is_not_on_the_path():
    t = parse({"nodes": [node("tank"), node("pump"), {"id": "j1", "kind": "junction"}, node("valve"), node("sector"),
                         node("pres")],
               "edges": [edge("a", "tank.out", "pump.in"), edge("b", "pump.out", "j1.w"), edge("c", "j1.e", "valve.in"),
                         edge("d", "valve.out", "sector.in"), edge("f", "j1.s", "tank.return")],
               "measures": [{"sensor": "pres", "edge": "c"}]})
    assert errors(t) == []
    assert sorted((x["to"], tuple(x["nodes"])) for x in paths_from(t, "tank", "out")) == [
        ("sector", ("pump", "j1", "valve")), ("tank", ("pump", "j1"))]
    lonely = parse({"nodes": [{"id": "j9", "kind": "junction"}], "edges": []})
    assert {i["rule"] for i in errors(lonely, {})} == {"JUNCTION_OPEN"}


def test_validation_rejects_duplicates_and_bad_values():
    with pytest.raises(ValidationError):
        parse({"nodes": [node("tank"), node("tank")]})
    with pytest.raises(ValidationError):
        parse({"nodes": [{"id": "a", "kind": "tank", "item": "tank"}, {"id": "b", "kind": "tank", "item": "tank"}]})
    with pytest.raises(ValidationError):
        parse({"nodes": [{"id": "j", "kind": "junction", "item": "x"}]})
    with pytest.raises(ValidationError):
        parse({"edges": [dict(edge("a", "x.out", "y.in"), length_m=math.nan)]})


# ---------------------------------------------------------------------- the drawing
def drawing(t, moves=None):
    pos = {"src": (20, 40), "tank": (120, 20), "pump": (300, 100), "meter": (400, 100), "valve": (500, 100),
           "sector": (620, 90), "lvl": (240, 20), "drain": (620, 260), "rpump": (500, 300)} | (moves or {})
    syms = [dg.Symbol(node=n, x=pos[n][0], y=pos[n][1]) for n in pos]
    d = dg.Diagram(symbols=syms)
    kinds = {n.id: n.kind for n in t.nodes}
    sym = {s.node: s for s in syms}
    routes = []
    for e in t.edges:
        a, _ = dg.anchor(kinds[e.src.node], sym[e.src.node], e.src.port)
        b, _ = dg.anchor(kinds[e.dst.node], sym[e.dst.node], e.dst.port)
        pts = [a, (a[0] + 10, a[1])] if a[0] != b[0] or a[1] != b[1] else [a]
        pts += [(pts[-1][0], b[1]), b]                 # a simple elbow route for the test
        routes.append(dg.Route(edge=e.id, points=dg.canonical_points(pts)))
    return d.model_copy(update={"routes": routes})


def test_rte04_moving_symbols_changes_the_drawing_digest_but_never_the_graph_digest():
    t = line()
    d1, d2 = drawing(t), drawing(t, {"pump": (300, 160), "valve": (520, 180)})
    assert dg.presentation_digest(d1) != dg.presentation_digest(d2)
    assert digest(t) == digest(parse(t.model_dump(mode="json")))
    errs = [i for i in dg.route_issues(d2, t) if i["severity"] == "ERROR"]
    assert errs == [], errs


def test_rte07_diagonal_detached_unrouted_and_overlapping_routes_are_reported():
    t = line()
    d = drawing(t)
    r = d.routes
    diag = r[0].model_copy(update={"points": [r[0].points[0], (r[0].points[0][0] + 30, r[0].points[0][1] + 30),
                                             r[0].points[-1]]})
    det = r[1].model_copy(update={"points": [(r[1].points[0][0] + 3, r[1].points[0][1])] + list(r[1].points[1:])})
    unr = r[2].model_copy(update={"points": []})
    bad = d.model_copy(update={"routes": [diag, det, unr] + r[3:]})
    got = {(i["rule"], i["object"], i["severity"]) for i in dg.route_issues(bad, t)}
    assert ("ROUTE_DIAGONAL", "e1", "ERROR") in got and ("ROUTE_UNROUTED", "e3", "WARNING") in got
    assert ("ROUTE_DETACHED", "e2", "ERROR") in got or ("ROUTE_DIAGONAL", "e2", "ERROR") in got
    with pytest.raises(ValidationError):
        dg.Diagram(routes=[{"edge": "e1", "points": [(0, 0), (math.inf, 0)]}])
    assert dg.canonical_points([(0, 0), (0, 0), (10, 0), (20, 0), (20, 5)]) == [(0, 0), (20, 0), (20, 5)]
    assert dg.doubles_back(dg.canonical_points([(0, 0), (20, 0), (10, 0)]))


def test_rte05_crossing_is_not_a_connection():
    a, b = [(0, 50), (100, 50)], [(50, 0), (50, 100)]
    assert dg.crossings(a, b) == [(50, 50)]
    t = line()
    assert all("cross" not in i["rule"].lower() for i in issues(t, ITEMS))    # the graph has no notion of it
