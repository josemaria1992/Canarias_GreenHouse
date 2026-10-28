"""Review R07-03 (0.7.0): every pump and source is one capacity shared by all active paths through it.

A main pump feeding a direct branch and a branch through a booster never moves more than its rated flow in all; a
balanced ledger is not enough on its own. DEMO numbers, simulation only."""
import pytest

from aquacrop.topology.definitions import parse
from aquacrop.topology.validation import issues as topology_issues
from aquacrop.twin.graph_water import GraphWater, fill, params_from
from tests.unit.test_graph_water import demo, item, run


def branched(main=20.0, booster=10.0):
    """tank -> main pump -> junction: east -> meter -> valve -> sector 1; south -> booster -> sector 2."""
    t, views = demo(rate=main)
    g = t.model_dump(mode="json")
    g["nodes"] += [{"id": "j", "kind": "junction"}, {"id": "booster", "kind": "pump", "item": "booster"},
                   {"id": "s2", "kind": "sector", "item": "s2"}]
    g["edges"] = [e for e in g["edges"] if e["id"] != "b"] + [
        {"id": "b1", "src": {"node": "pump", "port": "out"}, "dst": {"node": "j", "port": "w"}},
        {"id": "b2", "src": {"node": "j", "port": "e"}, "dst": {"node": "meter", "port": "in"}},
        {"id": "b3", "src": {"node": "j", "port": "s"}, "dst": {"node": "booster", "port": "in"}},
        {"id": "b4", "src": {"node": "booster", "port": "out"}, "dst": {"node": "s2", "port": "in"}}]
    t = parse(g)
    assert topology_issues(t) == []
    views |= {"booster": item("booster", "pump", rated_flow_l_min=booster), "s2": item("s2", "sector")}
    return t, views


def test_r07_03_a_shared_main_pump_is_never_counted_twice():
    t, views = branched()
    p, w = params_from(t, views), GraphWater()
    w.step(0.5, t, p, lambda n: n in {"pump", "valve", "booster"})
    assert w.node_flow["pump"] == pytest.approx(20) and p.rate["pump"] == 20
    assert w.sector_rate["sector"] == pytest.approx(10) and w.sector_rate["s2"] == pytest.approx(10)
    for n, (q, rate) in w.pump_loads(t, p).items():
        assert q <= rate + 1e-9, n
    assert abs(w.ledger()["residual_L"]) < 1e-9


def test_r07_03_what_a_booster_cannot_take_goes_to_the_other_branch():
    t, views = branched(main=20, booster=5)
    p, w = params_from(t, views), GraphWater()
    w.step(0.5, t, p, lambda n: n in {"pump", "valve", "booster"})
    assert w.sector_rate["s2"] == pytest.approx(5) and w.sector_rate["sector"] == pytest.approx(15)
    assert w.node_flow["pump"] == pytest.approx(20) and w.node_flow["booster"] == pytest.approx(5)


def test_r07_03_one_branch_closed_gives_the_other_the_main_pump_only():
    t, views = branched(main=20, booster=30)
    p, w = params_from(t, views), GraphWater()
    w.step(0.5, t, p, lambda n: n in {"pump", "booster"})             # sector 1's valve closed
    assert w.sector_rate["s2"] == pytest.approx(20)                    # series: the smallest rate
    assert "sector" not in w.sector_rate


def test_r07_03_pumps_in_series_give_their_smallest_rate():
    t, views = branched(main=20, booster=8)
    p, w = params_from(t, views), GraphWater()
    w.step(0.5, t, p, lambda n: n in {"pump", "booster"})
    assert w.sector_rate["s2"] == pytest.approx(8) and w.node_flow["pump"] == pytest.approx(8)


def test_r07_03_a_source_supply_is_one_capacity_for_pumped_and_unpumped_paths():
    caps = {("source", "s"): (12.0, [0, 1]), ("pump", "p"): (20.0, [1])}
    assert fill([0, 1], caps) == {0: pytest.approx(6), 1: pytest.approx(6)}
    caps = {("source", "s"): (12.0, [0, 1]), ("pump", "p"): (4.0, [1])}
    assert fill([0, 1], caps) == {0: pytest.approx(8), 1: pytest.approx(4)}


def test_r07_03_two_sources_and_overlapping_pumps_respect_every_capacity():
    caps = {("source", "a"): (10.0, [0, 1]), ("source", "b"): (30.0, [2]),
            ("pump", "p1"): (12.0, [1, 2]), ("pump", "p2"): (25.0, [2])}
    f = fill([0, 1, 2], caps)
    for _, (cap, members) in caps.items():
        assert sum(f[i] for i in members) <= cap + 1e-9
    assert f == {0: pytest.approx(5), 1: pytest.approx(5), 2: pytest.approx(7)}   # max-min fair


def test_r07_03_an_emptying_tank_scales_every_shared_path_and_keeps_the_ledger():
    t, views = branched()
    views["tank"] = item("tank", "tank", capacity_l=200, sim_initial_l=0.1, level_full=200, provenance="DEMO")
    p, w = params_from(t, views), GraphWater()
    w.step(0.5, t, p, lambda n: n in {"pump", "valve", "booster"})   # wants 0.1667 L, has 0.1 L
    assert w.vol["tank"] == pytest.approx(0)
    assert sum(w.delivered.values()) == pytest.approx(0.1)
    assert w.node_flow["pump"] <= 20 and abs(w.ledger()["residual_L"]) < 1e-9


def test_r07_03_the_same_steps_give_the_same_water_in_any_batch():
    t, views = branched(main=20, booster=5)
    p = params_from(t, views)
    on = {"pump", "valve", "booster"}
    one, two = GraphWater(), GraphWater()
    run(one, t, p, on, 60)
    run(two, t, p, on, 25)
    run(two, t, p, on, 35)
    assert one.vol == two.vol and one.delivered == two.delivered
    assert one.delivered["s2"] == pytest.approx(5) and one.delivered["sector"] == pytest.approx(15)
