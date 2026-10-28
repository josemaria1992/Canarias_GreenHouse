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
    with pytest.raises(ProgramRefused, match="is validated and cannot change: use Change"):
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
    assert any("reads cm, not a flow rate" in x and "L/min, L/h" in x for x in issues)


def test_a_program_is_retired_only_when_it_cannot_run_and_its_history_stays(store):
    """Owner, 28 Oct 2026: a program can be removed. Retiring keeps every version (RETIRED) and is refused while any
    version is active, with what to do."""
    store.save_draft("vicente", "fill", "Fill fertiliser", BODY)
    store.mark_validated("vicente", "fill", 1, [], {})
    store.activate("vicente", "MAIN", "fill", 1, require_simulation=False, overlaps_accepted=True, overlaps=[])
    with pytest.raises(ProgramRefused, match="is active, so it can still run: deactivate it first on Operation"):
        store.retire("vicente", "fill", "not used any more")
    store.deactivate("vicente", "MAIN", "fill")
    store.save_draft("vicente", "fill", "Fill fertiliser", BODY | {"action": BODY["action"] | {"duration_s": 9}})
    assert store.retire("vicente", "fill", "not used any more")["status"] == "RETIRED"
    assert {v["status"] for v in store.versions() if v["program_id"] == "fill"} == {"RETIRED"}
    assert [v["version"] for v in store.versions() if v["program_id"] == "fill"] == [1, 2]   # history kept
    with pytest.raises(ProgramRefused, match="already retired"):
        store.retire("vicente", "fill", "again")
    with pytest.raises(ProgramRefused, match="does not exist"):
        store.retire("vicente", "nope", "x")
