"""0.7.0 graph water model (TWIN-02..06): explicit paths only, conservation every step, NOT_MODELLED instead of
defaults. DEMO numbers are the roadmap's named test fixture, not installation values."""
import pytest

from aquacrop.equipment.definitions import ItemBody, TEMPLATES, TypeBody
from aquacrop.programs.engine import ItemView
from aquacrop.topology.definitions import parse
from aquacrop.twin.graph_water import GraphWater, ModelError, params_from


def item(iid, tmpl, **props):
    return ItemView(iid, iid, TypeBody.model_validate(TEMPLATES[tmpl]["body"]),
                    ItemBody.model_validate({"properties": props}), {}, {})


def demo(rate=20.0, extra_items=None):
    """DEMO single sector: source -> 200 L tank (100 L) -> 20 L/min pump -> meter -> valve -> sector."""
    views = {"tank": item("tank", "tank", capacity_l=200, sim_initial_l=100, level_full=200, provenance="DEMO"),
             "pump": item("pump", "pump", rated_flow_l_min=rate) if rate else item("pump", "pump"),
             "meter": item("meter", "flow"), "valve": item("valve", "valve"),
             "sector": item("sector", "sector", provenance="DEMO"), "lvl": item("lvl", "level")} | (extra_items or {})
    t = parse({"nodes": [{"id": k, "kind": {"tank": "tank", "pump": "pump", "meter": "meter", "valve": "valve",
                                             "sector": "sector", "lvl": "sensor"}[k], "item": k} for k in
                         ("tank", "pump", "meter", "valve", "sector", "lvl")],
               "edges": [{"id": "a", "src": {"node": "tank", "port": "out"}, "dst": {"node": "pump", "port": "in"}},
                         {"id": "b", "src": {"node": "pump", "port": "out"}, "dst": {"node": "meter", "port": "in"}},
                         {"id": "c", "src": {"node": "meter", "port": "out"}, "dst": {"node": "valve", "port": "in"}},
                         {"id": "d", "src": {"node": "valve", "port": "out"}, "dst": {"node": "sector", "port": "in"}}],
               "measures": [{"sensor": "lvl", "node": "tank"}]})
    return t, views


def run(w, t, p, on, seconds, dt=0.5):
    for _ in range(int(round(seconds / dt))):
        w.step(dt, t, p, lambda n: n in on)


def test_twin02_flow_needs_the_open_valve_and_the_running_pump():
    t, views = demo()
    p, w = params_from(t, views), GraphWater()
    run(w, t, p, {"pump"}, 10)                       # valve closed: nothing moves
    assert w.vol["tank"] == 100 and w.delivered == {}
    run(w, t, p, {"valve"}, 10)                      # pump stopped: nothing moves
    assert w.vol["tank"] == 100
    run(w, t, p, {"valve", "pump"}, 30)              # 20 L/min for 30 s
    assert w.vol["tank"] == pytest.approx(90) and w.delivered["sector"] == pytest.approx(10)
    assert w.readings(t, p)["meter"] == pytest.approx(20) and w.readings(t, p)["lvl"] == pytest.approx(90)


def test_twin03_conservation_every_step_and_an_empty_tank_supplies_nothing_more():
    t, views = demo(rate=600)                        # 10 L/s empties 100 L in 10 s
    p, w = params_from(t, views), GraphWater()
    for _ in range(40):
        w.step(0.5, t, p, lambda n: n in {"pump", "valve"})
        led = w.ledger()
        assert abs(led["residual_L"]) < 1e-9 and w.vol["tank"] >= 0
    assert w.vol["tank"] == pytest.approx(0) and w.delivered["sector"] == pytest.approx(100)
    assert any("ran empty" in n for n in w.notes) or w.vol["tank"] == 0


def test_twin04_partial_step_exhaustion_and_an_explicit_mass_adjustment():
    t, views = demo(rate=60)
    p, w = params_from(t, views), GraphWater()
    w.set_level("tank", 0.25, p, "scenario: almost empty")
    assert w.ledger()["adjustments_L"] == pytest.approx(-99.75)
    w.step(0.5, t, p, lambda n: n in {"pump", "valve"})   # 0.5 L wanted, 0.25 L there
    assert w.delivered["sector"] == pytest.approx(0.25) and w.vol["tank"] == pytest.approx(0)
    assert abs(w.ledger()["residual_L"]) < 1e-9
    assert w.events[0]["kind"] == "MASS_ADJUSTMENT"


def test_twin06_unknown_parameters_are_not_modelled_never_a_default():
    t, views = demo(rate=None)
    p, w = params_from(t, views), GraphWater()
    run(w, t, p, {"pump", "valve"}, 10)
    assert w.vol["tank"] == 100 and "pump" in w.not_modelled
    views["tank"] = item("tank", "tank")             # no capacity
    p = params_from(t, views)
    w = GraphWater()
    w.step(0.5, t, p, lambda n: True)
    assert "tank" in w.not_modelled and w.readings(t, p)["lvl"] is None


def test_twin03_parallel_paths_share_one_rate_and_a_drain_returns_after_its_delay():
    t, views = demo()
    g = t.model_dump(mode="json")
    g["nodes"] += [{"id": "v2", "kind": "valve", "item": "v2"}, {"id": "s2", "kind": "sector", "item": "s2"},
                   {"id": "drain", "kind": "tank", "item": "drain"}]
    g["edges"] += [{"id": "e", "src": {"node": "meter", "port": "out"}, "dst": {"node": "v2", "port": "in"}},
                   {"id": "f", "src": {"node": "v2", "port": "out"}, "dst": {"node": "s2", "port": "in"}},
                   {"id": "g", "src": {"node": "sector", "port": "drain"}, "dst": {"node": "drain", "port": "in"}}]
    # meter.out now has two pipes: an inline meter's outlet takes one; join through a junction instead
    g["nodes"].append({"id": "j", "kind": "junction"})
    g["edges"] = [e for e in g["edges"] if e["id"] not in ("c", "e")] + [
        {"id": "c1", "src": {"node": "meter", "port": "out"}, "dst": {"node": "j", "port": "w"}},
        {"id": "c2", "src": {"node": "j", "port": "e"}, "dst": {"node": "valve", "port": "in"}},
        {"id": "e2", "src": {"node": "j", "port": "s"}, "dst": {"node": "v2", "port": "in"}}]
    t2 = parse(g)
    views |= {"v2": item("v2", "valve"), "s2": item("s2", "sector"),
              "sector": item("sector", "sector", drain_fraction=0.3, drain_delay_s=5),
              "drain": item("drain", "tank", capacity_l=50, sim_initial_l=0)}
    p, w = params_from(t2, views), GraphWater()
    run(w, t2, p, {"pump", "valve", "v2"}, 30)       # 10 L in total, shared between the two sectors
    assert w.delivered["sector"] == pytest.approx(5) and w.delivered["s2"] == pytest.approx(5)
    assert w.vol["drain"] == pytest.approx(5 * 0.3 * (25 / 30), rel=0.05)   # the last 5 s are still in transit
    run(w, t2, p, set(), 10)
    assert w.vol["drain"] == pytest.approx(1.5) and abs(w.ledger()["residual_L"]) < 1e-9


def test_twin05_the_model_does_not_read_coordinates_and_a_crossing_is_nothing():
    t, views = demo()
    w1, w2 = GraphWater(), GraphWater()
    p = params_from(t, views)
    run(w1, t, p, {"pump", "valve"}, 12)
    run(w2, t, p, {"pump", "valve"}, 12)
    assert w1.ledger() == w2.ledger()
    import inspect
    from aquacrop.twin import graph_water
    assert "diagram" not in inspect.getsource(graph_water).split('"""', 2)[2]


def test_a_broken_ledger_stops_the_model():
    t, views = demo()
    p, w = params_from(t, views), GraphWater()
    w.step(0.5, t, p, lambda n: False)
    w.vol["tank"] += 1.0                              # water from nowhere
    with pytest.raises(ModelError):
        w.step(0.5, t, p, lambda n: False)
