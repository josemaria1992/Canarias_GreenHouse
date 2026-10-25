"""A twin edit older than the real system's item keeps its own change and takes the rest from the newer item (0.6.13)."""
from aquacrop.twin.changes import TwinChanges

SCALING = {"level": {"raw_min": 4.0, "raw_max": 20.0, "raw_unit": "mA", "eng_min": 0.0, "eng_max": 100.0, "unit": "cm"}}


def _item(version, body):
    return {"item_id": "lvl-acid", "version": version, "status": "ACTIVE", "name": "Acid level", "type_id": "level",
            "type_version": 1, "icon": "level", "body": body}


def _snap(item):
    return {"equipment": {"items": [item], "types": []}, "bindings": []}


def _change(body, base_version, base_body=None):
    c = {"name": "Acid level", "type_id": "level", "type_version": 1, "icon": "level", "body": body,
         "base_version": base_version}
    if base_body is not None:
        c["base_body"] = base_body
    return c


def test_old_edit_without_base_takes_the_newer_scaling(tmp_path):
    ch = TwinChanges(tmp_path / "changes.json")
    ch.doc["items"]["lvl-acid"] = _change({"scaling": {}, "stale_after_s": 5.0}, 2)
    ch.reconcile(_snap(_item(3, {"scaling": SCALING, "stale_after_s": 5.0})))
    assert "lvl-acid" not in ch.doc["items"]                 # nothing of its own was left: back to the real item


def test_edit_keeps_its_own_change_and_takes_the_rest(tmp_path):
    ch = TwinChanges(tmp_path / "changes.json")
    base = {"scaling": {}, "stale_after_s": 5.0}
    ch.doc["items"]["lvl-acid"] = _change({"scaling": {}, "stale_after_s": 10.0}, 2, base)
    ch.reconcile(_snap(_item(3, {"scaling": SCALING, "stale_after_s": 5.0})))
    c = ch.doc["items"]["lvl-acid"]
    assert c["body"] == {"scaling": SCALING, "stale_after_s": 10.0} and c["base_version"] == 3
    assert ch.overlay(_snap(_item(3, {"scaling": SCALING, "stale_after_s": 5.0})))["equipment"]["items"][0]["body"]["scaling"] == SCALING
