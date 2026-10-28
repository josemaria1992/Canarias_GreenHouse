"""Independent re-review of 7bc098c (RR-02..RR-04): the reviewer's probes asserted the defects; these are the same
situations with the expected-correct outcome. Fake bus, virtual modules and temporary state only."""
import copy
import json
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from aquacrop.api import changes
from aquacrop.api.app import create_app
from tests.hardware.test_twin_service import ORIGIN, live_and_twin, login  # noqa: F401  (fixture)


def _record(*steps):
    return {"digest": "b" * 64, "state": "COMPENSATING", "applied_utc": datetime.now(timezone.utc).isoformat(),
            "summary": "re-review fixture", "steps": list(steps)}


def _restart(app, tmp_path):
    c = TestClient(create_app(app.state.cfg, state_dir=tmp_path / "live", bind_host="127.0.0.1"), base_url=ORIGIN)
    h = login(c, "bench1", ORIGIN)
    return c, h, c.get("/api/twin-changes", headers=h).json()["applied"][0]


def _journal(tmp_path):
    return changes.Journal(tmp_path / "live" / "twin-changes")


# ---------------------------------------------------------------- RR-02
@pytest.mark.parametrize("recorded", ["DONE", "INTENT"])
def test_rr02_a_block_version_dropped_by_compensation_recovers_as_undone(live_and_twin, tmp_path, recorded):
    app = live_and_twin[0]
    blocks = app.state.blocks
    b = blocks.save_block("admin1", "b1", "Review block", {}, expected_version=None)
    blocks.drop_version("admin1", "b1", b["version"], "compensation already executed")
    assert blocks.get("b1", b["version"])["status"] == "RETIRED"
    step = {"op": "block_added", "block_id": "b1", "digest": b["digest"], "version_before": None, "state": recorded}
    if recorded == "DONE":
        step["version"] = b["version"]
    _journal(tmp_path).save(_record(step))
    c, h, rec = _restart(app, tmp_path)
    assert rec["state"] == "INTERRUPTED"
    assert rec["interrupted"]["done"] == [] and rec["interrupted"]["unknown"] == []
    # recorded DONE then taken back: UNDONE; never recorded made: NOT_DONE (nothing to undo)
    assert rec["interrupted"]["undone"] == (["block_added b1"] if recorded == "DONE" else [])
    assert _journal(tmp_path).get(rec["digest"])["steps"][0]["state"] == ("UNDONE" if recorded == "DONE" else "NOT_DONE")


def test_rr02_an_update_whose_new_version_was_dropped_keeps_the_previous_version_in_effect(live_and_twin, tmp_path):
    app = live_and_twin[0]
    blocks = app.state.blocks
    v1 = blocks.save_block("admin1", "b2", "Block", {}, expected_version=None)
    blocks.h.submit(lambda c: c.execute("UPDATE blocks SET status='VALIDATED' WHERE block_id='b2'"), wait=True)
    v2 = blocks.save_block("admin1", "b2", "Block renamed", {}, expected_version=v1["version"])
    assert v2["version"] != v1["version"]
    blocks.drop_version("admin1", "b2", v2["version"], "compensation")
    _journal(tmp_path).save(_record({"op": "block_added", "block_id": "b2", "digest": v2["digest"],
                                     "version_before": v1["version"], "version": v2["version"], "state": "DONE"}))
    c, h, rec = _restart(app, tmp_path)
    assert rec["interrupted"]["undone"] == ["block_added b2"] and rec["interrupted"]["done"] == []


def test_rr02_a_block_version_still_in_effect_recovers_as_done(live_and_twin, tmp_path):
    app = live_and_twin[0]
    b = app.state.blocks.save_block("admin1", "b3", "Block", {}, expected_version=None)
    _journal(tmp_path).save(_record({"op": "block_added", "block_id": "b3", "digest": b["digest"],
                                     "version_before": None, "state": "INTENT"}))
    c, h, rec = _restart(app, tmp_path)
    assert rec["interrupted"]["done"] == ["block_added b3"]
    step = _journal(tmp_path).get(rec["digest"])["steps"][0]
    assert step["version"] == b["version"]


# ---------------------------------------------------------------- RR-03
def _drawing_step(blocks, before_t, before_d, target_d, state="INTENT"):
    from aquacrop import diagram as dg

    def dd(d):
        return dg.presentation_digest(dg.parse(dict(d, view="installation")))
    return {"op": "hydraulics_saved", "before_graph": before_t["graph"], "before_diagram": before_d["diagram"],
            "target_digest": before_t["digest"], "before_digest": before_t["digest"],
            "before_diagram_digest": dd(before_d["diagram"]), "target_diagram_digest": dd(target_d),
            "before_topology_rev": before_t["rev"], "before_diagram_rev": before_d["rev"], "state": state}


def test_rr03_an_interrupted_drawing_only_save_recovers_with_its_revisions_and_can_be_undone(live_and_twin, tmp_path):
    app = live_and_twin[0]
    blocks = app.state.blocks
    before_t, before_d = blocks.topology(), blocks.diagram("installation")
    after = copy.deepcopy(before_d["diagram"])
    after["title"] = "Changed drawing"
    blocks.save_diagram("admin1", "installation", after, expected_rev=before_d["rev"])
    rec = _record(_drawing_step(blocks, before_t, before_d, after))
    _journal(tmp_path).save(rec)
    c, h, recovered = _restart(app, tmp_path)
    assert recovered["interrupted"]["done"] == ["hydraulics_saved"]
    step = _journal(tmp_path).get(rec["digest"])["steps"][0]
    assert step["topology_rev"] == blocks.topology()["rev"] == before_t["rev"]
    assert step["diagram_rev"] == blocks.diagram("installation")["rev"] == before_d["rev"] + 1
    undo = c.post("/api/twin-changes/undo", json={"digest": rec["digest"], "note": "re-review"}, headers=h)
    assert undo.status_code == 200 and undo.json()["problems"] == [], undo.text
    assert blocks.diagram("installation")["diagram"].get("title") == before_d["diagram"].get("title")


def test_rr03_a_drawing_save_that_did_not_happen_recovers_as_not_done(live_and_twin, tmp_path):
    app = live_and_twin[0]
    blocks = app.state.blocks
    before_t, before_d = blocks.topology(), blocks.diagram("installation")
    target = copy.deepcopy(before_d["diagram"])
    target["title"] = "Never saved"
    _journal(tmp_path).save(_record(_drawing_step(blocks, before_t, before_d, target)))
    c, h, rec = _restart(app, tmp_path)
    assert rec["interrupted"]["done"] == [] and rec["interrupted"]["unknown"] == []
    assert _journal(tmp_path).get(rec["digest"])["steps"][0]["state"] == "NOT_DONE"


def test_rr03_a_compensated_drawing_recorded_done_recovers_as_undone(live_and_twin, tmp_path):
    app = live_and_twin[0]
    blocks = app.state.blocks
    before_t, before_d = blocks.topology(), blocks.diagram("installation")
    target = copy.deepcopy(before_d["diagram"])
    target["title"] = "Saved then taken back"
    s1 = blocks.save_diagram("admin1", "installation", target, expected_rev=before_d["rev"])
    blocks.save_diagram("admin1", "installation", before_d["diagram"], expected_rev=s1["rev"])
    step = _drawing_step(blocks, before_t, before_d, target, state="DONE") | {"topology_rev": before_t["rev"],
                                                                               "diagram_rev": s1["rev"]}
    _journal(tmp_path).save(_record(step))
    c, h, rec = _restart(app, tmp_path)
    assert rec["interrupted"]["undone"] == ["hydraulics_saved"] and rec["interrupted"]["done"] == []


def test_rr03_a_record_without_a_drawing_identity_is_unknown_not_a_false_conflict(live_and_twin, tmp_path):
    """The reviewer's exact record (written before RR-03): the drawing changed and the record cannot say whether to
    the target. Recovery says so, instead of DONE without revisions and a later 'graph changed' at revision 0."""
    app = live_and_twin[0]
    blocks = app.state.blocks
    before = blocks.diagram()["diagram"]
    after = copy.deepcopy(before)
    after["title"] = "Changed drawing"
    blocks.save_diagram("admin1", "installation", after, expected_rev=0)
    graph = blocks.topology()
    rec = _record({"op": "hydraulics_saved", "before_graph": graph["graph"], "before_diagram": before,
                   "target_digest": graph["digest"], "state": "INTENT"})
    _journal(tmp_path).save(rec)
    c, h, recovered = _restart(app, tmp_path)
    assert recovered["interrupted"]["done"] == []
    assert recovered["interrupted"]["unknown"] and "no drawing identity" in recovered["interrupted"]["unknown"][0]
    undo = c.post("/api/twin-changes/undo", json={"digest": rec["digest"], "note": "re-review"}, headers=h)
    assert undo.status_code == 409 and "graph changed" not in undo.text and "unknown" in undo.text


# ---------------------------------------------------------------- RR-04
def test_rr04_a_listing_never_writes_and_a_stale_flush_cannot_overwrite_a_newer_record(tmp_path, monkeypatch):
    journal = changes.Journal(tmp_path)
    old = _record({"op": "bound", "item_id": "p", "state": "INTENT"})
    old["state"] = "APPLYING"
    original = changes.replace

    def denied(*a, **k):
        raise PermissionError("re-review fixture")
    monkeypatch.setattr(changes, "replace", denied)
    with pytest.raises(PermissionError):
        journal.save(old)
    monkeypatch.setattr(changes, "replace", original)
    assert journal.unsaved

    writes = []
    real_replace = changes.replace
    monkeypatch.setattr(changes, "replace", lambda *a: (writes.append(threading.current_thread().name), real_replace(*a)))
    listing = journal.all()                                    # a GET
    assert writes == [], "a listing wrote the journal"
    assert listing[0]["journal_unsaved"] and listing[0]["state"] == "APPLYING"

    newer = copy.deepcopy(journal.unsaved[old["digest"]])
    newer["state"] = "APPLIED"
    newer["steps"][0]["state"] = "DONE"
    journal.save(newer)                                        # the apply path's own save wins
    stale = copy.deepcopy(old)                                 # a late copy of the older state
    with pytest.raises(changes.StaleRecord):
        journal.save(stale)
    journal.unsaved[old["digest"]] = stale                     # even if some path kept the old copy
    journal.flush_unsaved()
    disk = json.loads(next((tmp_path / "applied").glob("*.json")).read_text())
    assert disk["state"] == "APPLIED" and disk["steps"][0]["state"] == "DONE"
    assert journal.all()[0]["state"] == "APPLIED" and not journal.all()[0].get("journal_unsaved")


def test_rr04_concurrent_listings_and_saves_keep_the_newest_record(tmp_path):
    journal = changes.Journal(tmp_path)
    rec = _record({"op": "bound", "item_id": "p", "state": "INTENT"})
    rec["state"] = "APPLYING"
    journal.save(rec)
    stop = threading.Event()
    errors = []

    def lister():
        while not stop.is_set():
            try:
                for r in journal.all():
                    assert r["state"] in ("APPLYING", "APPLIED", "UNDOING", "UNDONE")
            except BaseException as e:                        # noqa: BLE001
                errors.append(repr(e))
    threads = [threading.Thread(target=lister) for _ in range(4)]
    for t in threads:
        t.start()
    for state in ("APPLIED", "UNDOING", "UNDONE"):
        rec["state"] = state
        journal.save(rec)
    stop.set()
    for t in threads:
        t.join(10)
    assert not errors
    assert journal.get(rec["digest"])["state"] == "UNDONE" and journal.get(rec["digest"])["rev"] == 4


def test_rr04_a_listing_during_an_apply_does_not_change_its_outcome(live_and_twin, tmp_path):
    """Integrated: GETs interleaved with an approval and an undo; the record ends exactly as the apply/undo left it."""
    from tests.hardware.test_changes_durable_070 import _export
    app, live, twin_app, twin, bus, hl = live_and_twin
    digest = _export(twin)
    seen = []

    def between(where, step):
        seen.append(live.get("/api/twin-changes", headers=hl).status_code)
    app.state.changes_fail_point = between
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": digest, "note": "ok"}, headers=hb)
    app.state.changes_fail_point = None
    assert r.status_code == 200, r.text
    assert seen and set(seen) == {200}
    rec = _journal(tmp_path).get(digest)
    assert rec["state"] == "APPLIED" and all(s["state"] == "DONE" for s in rec["steps"])
    u = live.post("/api/twin-changes/undo", json={"digest": digest, "note": "back"}, headers=hb)
    assert u.status_code == 200, u.text
    assert _journal(tmp_path).get(digest)["state"] == "UNDONE"
