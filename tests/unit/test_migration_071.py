"""0.7.1 DT-01: converting a drawing made before tank, sector and source items existed (the bench's drawing).

A tank was drawn showing its level sensor, a sector showing its valve, the mains with no item at all; equipment types
from 0.5/0.6 have no water kind. The conversion must keep every item at its place, match those symbols to items of the
right kind *by name* (reviewable), propose the level sensor as measuring its tank, list what to create, and never
turn geometry into a connection."""
from aquacrop import layout as L
from aquacrop.equipment.definitions import TypeBody, water_kind
from aquacrop.topology.migrate import propose


def _lay(pipes=()):
    return L.parse({"kind": "AQUACROP_LAYOUT", "title": "bench", "width": 900, "height": 500,
                    "nodes": [{"id": "mains", "kind": "source", "x": 20, "y": 60, "label": "Mains"},
                              {"id": "tank-main", "kind": "tank", "x": 100, "y": 40, "w": 120, "h": 160, "label": "Main tank",
                               "item": "lvl-main", "role": "level"},
                              {"id": "lvl-main", "kind": "sensor", "x": 260, "y": 60, "label": "Main tank level", "item": "lvl-main", "tag": "L"},
                              {"id": "mp1", "kind": "pump", "x": 300, "y": 220, "label": "Main pump 1", "item": "mp1"},
                              {"id": "v-s1", "kind": "valve", "x": 420, "y": 220, "label": "Sector 1 valve", "item": "v-s1"},
                              {"id": "sector-1", "kind": "sector", "x": 520, "y": 200, "w": 120, "h": 60, "label": "Sector 1", "item": "v-s1"}],
                    "pipes": list(pipes)})


def test_types_without_a_water_kind_take_it_from_their_built_in_template():
    old_pump = TypeBody.model_validate({"category": "Pumps", "roles": [{"role": "start", "label": "Start", "direction": "OUTPUT",
                                                                          "signal": "DIGITAL", "purpose": "COMMAND"}],
                                        "operations": ["RUN_FOR"], "limits": {"max_runtime_s": 60}})
    assert old_pump.hydraulic is None
    assert water_kind(old_pump, "pump") == ("pump", "built-in template")
    assert water_kind(old_pump) == ("pump", "built-in template")                 # by its category (twin side)
    custom = old_pump.model_copy(update={"category": "My things"})
    assert water_kind(custom, "my-type") == (None, None)                         # never guessed for a custom type


def test_bench_drawing_without_tank_items_lists_what_to_create_and_keeps_the_rest():
    kinds = {"lvl-main": "sensor", "mp1": "pump", "v-s1": "valve"}
    names = {"lvl-main": "Main tank level", "mp1": "Main pump 1", "v-s1": "Sector 1 valve"}
    p = propose(_lay(), kinds, names)
    assert {n["id"] for n in p["topology"]["nodes"]} == {"lvl-main", "mp1", "v-s1"}
    assert sorted((c["kind"], c["name"]) for c in p["to_create"]) == [("sector", "Sector 1"), ("source", "Mains"), ("tank", "Main tank")]
    assert p["topology"]["edges"] == [] and p["matched"] == []
    # the sector symbol linked to the valve does not make the valve appear twice
    assert not any(a["kind"] == "SYMBOL_TWICE" for a in p["ambiguous"])


def test_once_created_by_name_the_symbols_match_and_the_level_sensor_measures_its_tank():
    kinds = {"lvl-main": "sensor", "mp1": "pump", "v-s1": "valve", "main-tank": "tank", "s1": "sector", "mains": "source"}
    names = {"lvl-main": "Main tank level", "mp1": "Main pump 1", "v-s1": "Sector 1 valve", "main-tank": "Main tank",
             "s1": "Sector 1", "mains": "Mains"}
    pipes = [{"id": "p1", "points": [[220, 180], [300, 230]], "from_node": "tank-main", "to_node": "mp1"},
             {"id": "p2", "points": [[340, 230], [420, 230]], "from_node": "mp1", "to_node": "v-s1"},
             {"id": "p3", "points": [[600, 300], [700, 300]]}]                     # ends not named: never a connection
    p = propose(_lay(pipes), kinds, names)
    ids = {n["id"]: n["kind"] for n in p["topology"]["nodes"]}
    assert ids["main-tank"] == "tank" and ids["s1"] == "sector" and ids["mains"] == "source"
    assert {m["item"] for m in p["matched"]} == {"main-tank", "s1", "mains"} and p["to_create"] == []
    assert p["topology"]["measures"] == [{"sensor": "lvl-main", "node": "main-tank", "edge": None, "mode": "TAP"}]
    edges = {(e["src"]["node"], e["dst"]["node"]) for e in p["topology"]["edges"]}
    assert ("main-tank", "mp1") in edges and ("mp1", "v-s1") in edges and len(edges) == 2
    assert any(a["legacy"] == "p3" and a["kind"] == "PIPE_END" for a in p["ambiguous"])
    syms = {s["node"]: (s["x"], s["y"]) for s in p["diagram"]["symbols"]}
    assert syms["main-tank"] == (100, 40)                                         # placed where the owner drew it
