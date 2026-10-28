"""0.7.1 ID-01/ID-02 (owner decision 26 October 2026): Name -> ID, editable public IDs over stable keys."""
import json
import threading

import pytest

from aquacrop.ids import IdRefused, id_from_name, key_for
from tests.conftest import ROOT

CASES = [("Valve 1", "Valve_1"), ("Irrigation Pump 2", "IrrigationPump_2"), ("Main tank", "Maintank"),
         ("pump-3", "pump_3"), ("Válvula 12", "Valvula_12"), ("  Sector  A  ", "SectorA"), ("7", "7"),
         ("Flow meter 1b", "Flowmeter1b"), ("Tank_2", "Tank_2"), ("", "")]


def test_id01_owner_examples():
    assert id_from_name("Valve 1") == "Valve_1"
    assert id_from_name("Irrigation Pump 2") == "IrrigationPump_2"
    for name, want in CASES:
        assert id_from_name(name) == want, name
    # the browser runs the same cases (tests/js/ids.test.js reads this file)
    (ROOT / "tests" / "js" / "ids-cases.json").write_text(json.dumps(CASES, ensure_ascii=False), encoding="utf-8")


def test_keys_are_lower_case_and_unique():
    assert key_for("Valve_1", set()) == "valve_1"
    assert key_for("Valve_1", {"valve_1"}) == "valve_1-2"
    assert key_for("Valve_1", {"valve_1", "valve_1-2"}) == "valve_1-3"
