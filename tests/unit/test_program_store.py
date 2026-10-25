"""Program version lifecycle in the store (review finding 2026-10-24): a validated version is immutable."""
import pytest

from aquacrop.config import load_config
from aquacrop.historian.writer import Historian
from aquacrop.programs.store import ProgramRefused, ProgramStore
from tests.conftest import ROOT

BODY = {"trigger": {"type": "MANUAL"}, "action": {"type": "RUN_FOR", "item": "led1", "duration_s": 5}}


@pytest.fixture
def store(tmp_path):
    h = Historian(tmp_path / "db.sqlite3", load_config(ROOT / "tests" / "fixtures" / "bench-6.yaml"), "s1")
    yield ProgramStore(h)
    h.close()


def test_a_validated_version_that_fails_revalidation_is_invalid_not_editable_and_deactivated(store):
    store.save_draft("vicente", "p", "Lamp", BODY)
    store.mark_validated("vicente", "p", 1, [], {})
    store.activate("vicente", "TWIN", "p", 1, require_simulation=False, overlaps_accepted=True, overlaps=[])
    assert "p" in store.active("TWIN")
    v = store.mark_validated("vicente", "p", 1, ["led1 has no command channel"], {})
    assert v["status"] == "INVALID"
    assert "p" not in store.active("TWIN")
    with pytest.raises(ProgramRefused, match="not a draft"):
        store.save_draft("vicente", "p", "Lamp", BODY | {"action": BODY["action"] | {"duration_s": 50}}, version=1)
    assert store.save_draft("vicente", "p", "Lamp", BODY)["version"] == 2    # the fix is the next version
    with pytest.raises(ProgramRefused, match="not validated"):
        store.activate("vicente", "TWIN", "p", 1, require_simulation=False, overlaps_accepted=True, overlaps=[])


def test_a_volume_needs_a_flow_meter_in_litres_per_minute():
    from aquacrop.equipment.definitions import TEMPLATES, TypeBody
    from aquacrop.programs.definitions import parse, validate
    types = {(k, 1): TypeBody.model_validate(TEMPLATES[k]["body"]) for k in ("pump", "flow", "level")}
    items = {i: {"status": "ACTIVE", "type_id": t, "type_version": 1, "name": i, "body": {}}
             for i, t in (("pump", "pump"), ("fm", "flow"), ("lv", "level"))}
    body = {"trigger": {"type": "MANUAL"}, "action": {"type": "DELIVER_VOLUME", "item": "pump", "meter": "fm",
                                                      "meter_role": "flow", "target_L": 5, "max_runtime_s": 120}}
    assert not [x for x in validate(parse(body), items, types, physical=False, bindings={}, readiness={})
                if "flow-rate" in x]
    body["action"] |= {"meter": "lv", "meter_role": "level"}
    issues = validate(parse(body), items, types, physical=False, bindings={}, readiness={})
    assert any("not a flow-rate measurement in L/min (cm)" in x for x in issues)
