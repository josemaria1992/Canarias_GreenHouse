"""Codex 0.7.7 C077-08 (owner, 27 Oct 2026): the bench's earlier drawing imported into the twin with its pipes and
connections, ready to simulate once its factual questions are answered. Explicit links are kept without asking; what
only geometry suggests becomes a connection only on a yes; open questions and missing equipment are named blockers;
the result is never "ready" while one is left. Uses the bench drawing fixture (geometry, labels, item names/kinds)."""
import json

from aquacrop import diagram as dg
from aquacrop import layout as L
from aquacrop.topology.definitions import parse
from aquacrop.topology.migrate import propose
from aquacrop.topology.validation import issues
from tests.conftest import ROOT

FX = json.loads((ROOT / "tests/fixtures/bench-legacy-drawing.json").read_text(encoding="utf-8"))
CREATED = [("tank-a", "tank", "Fertiliser A"), ("tank-b", "tank", "Fertiliser B"), ("tank-acid", "tank", "Acid"),
           ("tank-main", "tank", "Main tank"), ("tank-sump", "tank", "Drainage tank"), ("sector-1", "sector", "Sector 1"),
           ("sector-2", "sector", "Sector 2"), ("mains", "source", "Mains")]


def _bench(created=True, answers=None):
    kinds = {k: v["kind"] for k, v in FX["items"].items()}
    names = {k: v["name"] for k, v in FX["items"].items()}
    if created:                                         # the owner made the tanks, sectors and mains on Equipment
        for iid, k, n in CREATED:
            kinds[iid], names[iid] = k, n
    lay = L.parse(FX["layout"])
    return lay, propose(lay, kinds, names, answers), kinds, names


def _all_yes():
    lay, p, *_ = _bench()
    return {q["id"]: "yes" for q in p["questions"]}


def test_explicit_links_are_kept_without_asking_and_shared_ports_get_their_junction():
    _, p, kinds, names = _bench(created=False)
    e = {(x["src"]["node"], x["dst"]["node"]) for x in p["topology"]["edges"]}
    # each main pump feeds both sector valves (named in the drawing): one junction per shared single port
    assert {("mp1", "j-mp1-out"), ("mp2", "j-mp2-out"), ("j-v-s1-in", "v-s1"), ("j-v-s2-in", "v-s2")} <= e
    assert not [i for i in issues(parse(p["topology"]), {k: {"kind": v, "name": names[k]} for k, v in kinds.items()})
                if i["rule"] == "PORT_SHARED"]
    assert p["ready"] is False                           # tanks, sectors and mains are missing: never "ready"
    texts = [b["text"] for b in p["blockers"]]
    assert any("Create the tank Main tank" in t for t in texts) and any("Create the source Mains" in t for t in texts)


def test_only_geometry_is_asked_and_every_question_carries_the_drawings_suggestion():
    _, p, *_ = _bench()
    kinds = {q["kind"] for q in p["questions"]}
    assert kinds <= {"ATTACH", "JOIN", "INLINE", "TAP", "MEASURES"} and all(q["suggest"] == "yes" for q in p["questions"])
    ids = {q["id"] for q in p["questions"]}
    assert "inline:dose-a:v-out-a" in ids and "start:fill-a" in ids and "tap:pres-s1" in ids
    assert not any(q.startswith("inline:dose-a:tp-a") or q.startswith("inline:dose-a:flow-tp-a") for q in ids)  # explicit
    assert p["ready"] is False and {b["id"] for b in p["blockers"]} >= {f"answer:{i}" for i in ids}


def test_with_every_answer_the_bench_drawing_is_a_complete_graph_ready_to_simulate():
    lay, p, kinds, names = _bench(answers=_all_yes())
    assert p["ready"] is True and p["blockers"] == []
    assert p["kept"]["pipes_connected"] == len(lay.pipes)
    t = parse(p["topology"])
    errors = [i for i in issues(t, {k: {"kind": v, "name": names[k]} for k, v in kinds.items()}) if i["severity"] == "ERROR"]
    assert errors == []
    deg = {}
    for x in t.edges:
        deg.setdefault(x.src.node, set()).add(x.src.port)
        deg.setdefault(x.dst.node, set()).add(x.dst.port)
    for m in ("flow-fill-main", "flow-return", "flow-s1", "flow-s2", "flow-tp-a", "flow-tp-acid", "flow-tp-b"):
        assert deg[m] == {"in", "out"}, m                # every flow meter in series, never bypassed
    measured = {m.sensor for m in t.measures}
    assert {"pres-s1", "pres-s2", "ph", "ec", "temp", "lvl-main", "lvl-sump"} <= measured
    dia = dg.parse(p["diagram"])
    assert not [s for s in dia.sketches if not s.id.endswith("-drawn")]       # no loose dotted pipe is left
    assert not [i for i in dg.route_issues(dia, t) if i["severity"] == "ERROR"]
    sym = {s.node: s for s in dia.symbols}
    assert sym["v-fill-a"].rot in (90, 270) and sym["flow-s1"].rot in (0, 180)  # turned with their pipe


def test_a_no_is_respected_and_an_open_question_keeps_its_pipe_a_named_blocker():
    a = _all_yes()
    a["inline:return:rp2"] = "no"                        # the owner: Return pump 2 is not in series in that line
    del a["inline:dose-a:v-out-a"]                       # not answered yet
    lay, p, *_ = _bench(answers=a)
    e = {(x["src"]["node"], x["dst"]["node"]) for x in p["topology"]["edges"]}
    assert ("rp1", "flow-return") in e and not any("rp2" in pair for pair in e)
    assert not any(x["src"]["node"] == "tank-a" for x in p["topology"]["edges"])     # dose-a waits for its answer
    assert any(s["id"] == "dose-a" for s in p["diagram"]["sketches"])
    assert p["ready"] is False and any(b["id"] == "answer:inline:dose-a:v-out-a" for b in p["blockers"])


def test_a_join_answered_no_leaves_that_pipe_unconnected_and_says_why():
    a = _all_yes()
    a["start:fill-b"] = "no"
    _, p, *_ = _bench(answers=a)
    assert not any(x["dst"]["node"] == "v-fill-b" for x in p["topology"]["edges"])
    b = next(b for b in p["blockers"] if b["id"] == "pipe:fill-b")
    assert "does not join pipe mains (answered)" in b["text"] and p["ready"] is False


def test_the_same_answers_give_the_same_design_every_time():
    a = _all_yes()
    _, p1, *_ = _bench(answers=a)
    _, p2, *_ = _bench(answers=dict(reversed(list(a.items()))))
    assert p1["topology"] == p2["topology"] and p1["diagram"] == p2["diagram"]


def test_a_second_pump_on_a_line_can_be_its_parallel_spare():
    """Owner, 27 Oct 2026: Return pumps 1 and 2 are side by side in the drainage tank, the second in case one fails."""
    q = next(q for q in _bench()[1]["questions"] if q["id"] == "inline:return:rp2")
    assert q["choices"] == ["yes", "no", "parallel"] and q["value"]["parallel_to"] == "rp1"
    assert all(x["choices"] == ["yes", "no"] for x in _bench()[1]["questions"] if x["id"] != q["id"])
    answers = _all_yes() | {"inline:return:rp2": "parallel"}
    lay, p, kinds, names = _bench(answers=answers)
    assert p["ready"] is True and p["kept"]["pipes_connected"] == len(lay.pipes)
    e = p["topology"]["edges"]
    into = {x["dst"]["node"]: x["src"]["node"] for x in e}
    out = {x["src"]["node"]: x["dst"]["node"] for x in e}
    assert into["rp1"] == into["rp2"] == "tank-sump"          # same suction
    assert out["rp1"] == out["rp2"]                            # they join before the return line continues
    assert not any(x["src"]["node"] == "rp1" and x["dst"]["node"] == "rp2" for x in e)   # never in series
    items = {k: {"kind": v, "name": names[k]} for k, v in kinds.items()}
    assert not [i for i in issues(parse(p["topology"]), items) if i["severity"] == "ERROR"]
