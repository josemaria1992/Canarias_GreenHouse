"""0.7.0 ALM-01/02: item alarm limits with delay and deadband; UNKNOWN neither raises nor clears; the message says
what, where, value, response and next check; acknowledging never clears the condition."""
from aquacrop.config import load_config
from aquacrop.control.alarms import AlarmEngine
from tests.conftest import ROOT


def inp(v, **kw):
    return [{"item": "lvl", "name": "Main tank level", "role": "level", "label": "Level", "unit": "cm", "value": v,
             "low": 20.0, "high": None, "deadband": 5.0, "delay_s": 10.0, "quality": "GOOD"} | kw]


def test_alm01_delay_deadband_unknown_and_ack():
    a = AlarmEngine(load_config(ROOT / "tests" / "fixtures" / "bench-6.yaml"))
    a.evaluate_item_limits(inp(15.0), 0.0, None)
    assert not a.is_active("ITEM_LOW", "lvl.level")              # below the limit, not yet for its delay
    a.evaluate_item_limits(inp(15.0), 9.0, None)
    assert not a.is_active("ITEM_LOW", "lvl.level")
    a.evaluate_item_limits(inp(15.0), 10.5, None)
    occ = a.open[("ITEM_LOW", "lvl.level")]
    assert occ.active and "Main tank level Level is 15 cm, below its low limit 20" in occ.message and "Check:" in occ.message
    a.evaluate_item_limits(inp(None), 20.0, None)               # the reading is lost: not called fixed
    assert a.is_active("ITEM_LOW", "lvl.level")
    a.acknowledge(occ.occurrence_id, "oper1")
    assert a.is_active("ITEM_LOW", "lvl.level")                 # acknowledged, still active
    a.evaluate_item_limits(inp(22.0), 30.0, None)               # above the limit but inside the deadband
    assert a.is_active("ITEM_LOW", "lvl.level")
    a.evaluate_item_limits(inp(26.0), 31.0, None)
    assert not a.is_active("ITEM_LOW", "lvl.level")
    a.evaluate_item_limits(inp(10.0), 40.0, None)
    a.evaluate_item_limits(inp(30.0), 45.0, None)               # a short dip under its delay raises nothing
    a.evaluate_item_limits(inp(10.0), 46.0, None)
    assert not a.is_active("ITEM_LOW", "lvl.level")


def test_a_normal_range_without_limits_is_no_alarm_and_a_removed_limit_clears():
    a = AlarmEngine(load_config(ROOT / "tests" / "fixtures" / "bench-6.yaml"))
    a.evaluate_item_limits(inp(5.0, delay_s=0), 0.0, None)
    assert a.is_active("ITEM_LOW", "lvl.level")
    a.evaluate_item_limits(inp(5.0, low=None), 1.0, None)
    assert not a.is_active("ITEM_LOW", "lvl.level")
