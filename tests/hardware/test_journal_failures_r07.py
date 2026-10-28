"""Review R07-04 (0.7.0): journal write failures during an apply and its compensation are reported truthfully, and
the next start checks every step recorded as done against the real system. Fake bus and virtual modules only."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aquacrop.api import changes
from aquacrop.api.app import create_app
from aquacrop.twin import feed
from tests.hardware.test_changes_durable_070 import _export, _live_state
from tests.hardware.test_twin_service import ORIGIN, live_and_twin, login  # noqa: F401  (fixture)


def _deny(monkeypatch, rule):
    """Refuse journal replaces chosen by `rule(doc, denied)`, every retry of that write included (a single refusal
    is absorbed by the retry, as a passing Windows lock should be); returns the list of refused writes."""
    original = changes.os.replace
    denied, refused = [], set()

    def replace(src, dst):
        if Path(dst).parent.name == "applied":
            text = Path(src).read_text(encoding="utf-8")
            if text in refused:                                       # a retry of a refused write
                raise PermissionError("test: journal replace denied again")
            why = rule(json.loads(text), denied)
            if why:
                denied.append(why)
                refused.add(text)
                raise PermissionError(f"test: journal replace denied ({why})")
        return original(src, dst)
    monkeypatch.setattr(changes.os, "replace", replace)
    return denied, lambda: monkeypatch.setattr(changes.os, "replace", original)


def _binding_intent(doc):
    return doc["state"] == "APPLYING" and doc["steps"] and doc["steps"][-1]["op"] == "bound"


def _restart(app, tmp_path):
    c2 = TestClient(create_app(app.state.cfg, state_dir=tmp_path / "live", bind_host="127.0.0.1"), base_url=ORIGIN)
    return c2, login(c2, "bench1", ORIGIN)


def test_r07_04_the_reviews_two_failed_writes_are_recovered_truthfully(live_and_twin, tmp_path, monkeypatch):
    app, live, twin_app, twin, bus, hl = live_and_twin
    exported = _export(twin)

    def rule(doc, denied):
        if not denied and _binding_intent(doc):
            return "binding intent"
        if len(denied) == 1 and doc["state"] == "FAILED_NOTHING_CHANGED":
            return "compensation record"
    denied, restore = _deny(monkeypatch, rule)
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": exported}, headers=hb)
    restore()
    assert r.status_code == 409 and r.json()["detail"].startswith("nothing was changed"), r.text
    assert "could not be written" in r.json()["detail"]
    assert denied == ["binding intent", "compensation record"]
    assert _live_state(app)["p2"] == "RETIRED"                        # the compensation took the item back
    c2, h2 = _restart(app, tmp_path)
    rec = c2.get("/api/twin-changes", headers=h2).json()["applied"][0]
    assert rec["state"] == "INTERRUPTED"
    assert "item_created p2" not in rec["interrupted"]["done"]        # never a stale DONE
    assert "item_created p2" in rec["interrupted"]["undone"] and rec["interrupted"]["unknown"] == []
    undo = c2.post("/api/twin-changes/undo", json={"digest": exported, "note": "after recovery"}, headers=h2)
    assert undo.status_code == 200 and undo.json()["problems"] == [], undo.text


def test_r07_04_a_journal_that_cannot_be_written_at_all_is_settled_at_the_next_start(live_and_twin, tmp_path,
                                                                                      monkeypatch):
    app, live, twin_app, twin, bus, hl = live_and_twin
    exported = _export(twin)
    broken = []

    def rule(doc, denied):
        if broken or _binding_intent(doc):
            broken.append(1)                                          # the disk stays refused from here on
            return doc["state"]
    denied, restore = _deny(monkeypatch, rule)
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": exported}, headers=hb)
    assert r.status_code == 409 and "could not be written" in r.json()["detail"], r.text
    assert "COMPENSATING" in denied and "FAILED_NOTHING_CHANGED" in denied
    v = live.get("/api/twin-changes", headers=hl).json()             # this process tells the truth meanwhile
    assert v["applied"][0]["state"] == "FAILED_NOTHING_CHANGED" and v["applied"][0]["journal_unsaved"] is True
    assert v["attention"][0]["digest"] == exported
    on_disk = json.loads(next((tmp_path / "live" / "twin-changes" / "applied").glob("*.json")).read_text("utf-8"))
    assert on_disk["state"] == "APPLYING" and on_disk["steps"][0]["state"] == "DONE"   # the stale record
    restore()
    c2, h2 = _restart(app, tmp_path)                                  # the process ended before it could write
    rec = c2.get("/api/twin-changes", headers=h2).json()["applied"][0]
    assert rec["state"] == "INTERRUPTED" and rec["interrupted"]["done"] == []
    assert rec["interrupted"]["undone"] == ["item_created p2"]


def test_r07_04_a_passing_windows_lock_is_retried(live_and_twin, monkeypatch):
    app, live, twin_app, twin, bus, hl = live_and_twin
    exported = _export(twin)
    original, n = changes.os.replace, []

    def once(src, dst):
        if Path(dst).parent.name == "applied" and not n:
            n.append(1)
            raise PermissionError("[WinError 5] Access is denied (test)")
        return original(src, dst)
    monkeypatch.setattr(changes.os, "replace", once)
    monkeypatch.setattr(feed, "WINDOWS", True)                 # not os.name: pathlib would follow it
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": exported}, headers=hb)
    assert r.status_code == 200 and "warning" not in r.json(), r.text


def test_r07_04_a_record_that_cannot_be_created_changes_nothing_and_can_be_approved_later(live_and_twin, monkeypatch):
    app, live, twin_app, twin, bus, hl = live_and_twin
    exported = _export(twin)
    before = _live_state(app)
    denied, restore = _deny(monkeypatch, lambda doc, d: "create" if doc["state"] == "APPLYING" and not doc["steps"] else None)
    hb = login(live, "bench1", ORIGIN)
    r = live.post("/api/twin-changes/approve", json={"digest": exported}, headers=hb)
    restore()
    assert r.status_code == 503 and r.json()["detail"].startswith("nothing was changed"), r.text
    assert _live_state(app) == before
    assert live.get("/api/twin-changes", headers=hl).json()["applied"] == []
    r = live.post("/api/twin-changes/approve", json={"digest": exported, "note": "disk back"}, headers=hb)
    assert r.status_code == 200, r.text
    assert _live_state(app)["p2"] == "ACTIVE"


def test_r07_04_an_unknown_step_blocks_undo_until_checked(live_and_twin, tmp_path, monkeypatch):
    app, live, twin_app, twin, bus, hl = live_and_twin
    exported = _export(twin)

    class Crash(BaseException):
        pass

    def kill(where, step):
        if where == "before_done" and step["op"] == "bound":
            raise Crash()
    app.state.changes_fail_point = kill
    doomed = TestClient(app, base_url=ORIGIN)
    hb = login(doomed, "bench1", ORIGIN)
    with pytest.raises(BaseException):
        doomed.post("/api/twin-changes/approve", json={"digest": exported}, headers=hb)
    app.state.changes_fail_point = None
    # someone else moves the channel to a third point before the restart: neither as applied nor as before
    app.state.equipment.bind("admin1", "p2", "start", "relay_A.DO3", note="by hand")
    c2, h2 = _restart(app, tmp_path)
    rec = c2.get("/api/twin-changes", headers=h2).json()["applied"][0]
    assert rec["interrupted"]["unknown"] == ["bound p2"]
    r = c2.post("/api/twin-changes/undo", json={"digest": exported}, headers=h2)
    assert r.status_code == 409 and "unknown" in r.text
