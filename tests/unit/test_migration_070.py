"""0.7.0 TOPO-06 / DEP-02: a 0.6 drawing becomes a reviewed proposal. Explicit links become edges; what the old
water model inferred from geometry (equipment drawn on a pipe, a loose pipe end) and unlinked symbols are listed
for a person to decide, never connected. The old drawing is referenced by its digest, not changed."""
from aquacrop import layout as legacy
from aquacrop.topology.definitions import parse
from aquacrop.topology.migrate import propose

KINDS = {"tank-1": "tank", "pump-1": "pump", "valve-1": "valve", "sector-1": "sector"}


def old_drawing():
    return legacy.Layout.model_validate({"nodes": [
        {"id": "T1", "kind": "tank", "x": 40, "y": 60, "w": 80, "h": 100, "item": "tank-1", "label": "Main tank"},
        {"id": "P1", "kind": "pump", "x": 200, "y": 100, "item": "pump-1", "label": "Main pump"},
        {"id": "V1", "kind": "valve", "x": 340, "y": 100, "item": "valve-1", "label": "Sector valve"},
        {"id": "S1", "kind": "sector", "x": 480, "y": 80, "w": 120, "h": 40, "item": "sector-1", "label": "Sector 1"},
        {"id": "P2", "kind": "pump", "x": 200, "y": 300, "label": "Spare pump (no item)"}],
        "pipes": [
            {"id": "tank-to-pump", "points": [[120, 100], [200, 100]], "from_node": "T1", "to_node": "P1"},
            # the valve is drawn on this pipe: the old model put it inline; the proposal asks instead
            {"id": "pump-to-sector", "points": [[200, 100], [480, 100]], "from_node": "P1", "to_node": "S1"},
            {"id": "loose", "points": [[200, 100], [200, 250]], "from_node": "P1"}]})


def test_topo06_explicit_links_become_edges_and_geometry_is_only_a_question():
    lay = old_drawing()
    before = legacy.digest(lay)
    pr = propose(lay, KINDS, {"valve-1": "Sector valve"})
    assert pr["source"]["digest"] == before == legacy.digest(lay)          # the old drawing is untouched
    edges = [(e["src"]["node"], e["dst"]["node"]) for e in pr["topology"]["edges"]]
    assert edges == [("tank-1", "pump-1")]                                  # the one unambiguous link
    assert all(e["provenance"] == "UNKNOWN" for e in pr["topology"]["edges"])
    amb = {a["legacy"]: a for a in pr["ambiguous"]}
    assert amb["pump-to-sector"]["kind"] == "INLINE" and "Sector valve" in amb["pump-to-sector"]["why"]
    assert amb["pump-to-sector"]["suggests"] == {"from": "pump-1", "to": "sector-1", "through": ["valve-1"]}
    assert amb["loose"]["kind"] == "PIPE_END"
    assert [u["legacy"] for u in pr["unresolved"]] == ["P2"]                # no item: not invented
    t = parse(pr["topology"])                                              # a well-formed draft
    assert {n.id for n in t.nodes} == set(KINDS)
    assert all(not r["points"] for r in pr["diagram"]["routes"])           # routed again by the orthogonal router


def test_dep02_a_pipe_without_named_ends_is_a_question_never_a_connection():
    lay = legacy.Layout.model_validate({"nodes": [{"id": "T1", "kind": "tank", "x": 40, "y": 60, "item": "tank-1"}],
                                        "pipes": [{"id": "p", "points": [[0, 0], [10, 0]]}]})
    pr = propose(lay, KINDS)
    assert pr["topology"]["edges"] == [] and pr["ambiguous"][0]["kind"] == "PIPE_END"
