"""AT-HIS-01, AT-AUD-01, AT-OPS-01/02/03 (storage parts)."""
import sqlite3

import pytest

from aquacrop.domain.model import ControllerState, Outcome
from aquacrop.historian.backup import create_backup, restore_backup, verify_backup
from aquacrop.historian.schema import CURRENT_VERSION, connect, migrate, schema_version
from aquacrop.historian.writer import Historian
from aquacrop.twin.runtime import SimulationRuntime


@pytest.fixture
def hist(tmp_path, cfg48):
    h = Historian(tmp_path / "db.sqlite3", cfg48, "sess_test")
    yield h
    h.close()


def make_rt(cfg, h):
    rt = SimulationRuntime(cfg, audit=h, history=h, session_id=h.session_id, start_permitted=h.can_accept_starts)
    h.register_run(rt.run_id, rt.seed, rt.params.provenance, "test")
    return rt


def test_every_cycle_records_all_points_including_unchanged(hist, cfg48):
    rt = make_rt(cfg48, hist)
    rt.run_for(5)  # stride 1 s => 5 cycles
    hist.wait_idle()
    rows = hist.query("SELECT cycle_pk, count(*) n FROM samples GROUP BY cycle_pk")
    assert len(rows) == 5 and all(r["n"] == 48 for r in rows)
    di = hist.query("SELECT count(*) n FROM samples WHERE point_id='relay_A.DI1'")[0]["n"]
    assert di == 5  # unchanged digital still recorded each cycle
    r = hist.query("SELECT * FROM samples WHERE point_id='analog_A.AI1' LIMIT 1")[0]
    assert r["origin"] == "SIMULATED" and r["calibration_ref"] == "demo_level_reservoir@1" and r["sim_run_id"]
    assert r["raw_unit"] == "uA" and r["unit"] == "L" and r["config_version"] == cfg48.config_version
    un = hist.query("SELECT * FROM samples WHERE point_id='relay_B.DI1' LIMIT 1")[0]
    assert un["quality"] == "UNAVAILABLE" and un["value"] is None and un["reason"] == "UNMAPPED"


def test_commands_audited_with_outcomes(hist, cfg24):
    rt = make_rt(cfg24, hist)
    rej = rt.operator_command("alice", "relay_A.DO1", True)
    rt.request_state("alice", ControllerState.MANUAL)
    ok = rt.operator_command("alice", "relay_A.DO1", True)
    hist.wait_idle()
    rows = {r["request_id"]: r for r in hist.query("SELECT * FROM commands")}
    assert rows[rej.command.request_id]["outcome"] == "REJECTED" and rows[rej.command.request_id]["actor"] == "alice"
    assert rows[ok.command.request_id]["outcome"] == "READBACK_CONFIRMED"
    assert rows[ok.command.request_id]["intent_utc"] <= rows[ok.command.request_id]["outcome_utc"]


def test_storage_failure_inhibits_starts(hist, cfg24):
    rt = make_rt(cfg24, hist)
    rt.request_state("op", ControllerState.MANUAL)
    hist.fail_writes = True
    rec = rt.operator_command("op", "relay_A.DO1", True)
    assert rec.outcome == Outcome.REJECTED
    assert rt.backend.state.coils["relay_A.DO1"] is False
    assert not hist.health["ok"]
    hist.fail_writes = False
    hist.flush()
    assert hist.health["ok"]


def test_alarm_persist_and_restore_without_duplicates(tmp_path, cfg24):
    h = Historian(tmp_path / "a.sqlite3", cfg24, "s1")
    rt = make_rt(cfg24, h)
    rt.set_override("op", "analog_A.AI1", 19800, "t")
    rt.run_for(1)
    h.wait_idle()
    h.close()
    h2 = Historian(tmp_path / "a.sqlite3", cfg24, "s2")
    restored = h2.load_open_alarms()
    assert [(o.rule_id, o.target) for o in restored] == [("RESERVOIR_LEVEL_HIGH", "analog_A.AI1")]
    rt2 = make_rt(cfg24, h2)
    rt2.alarms.restore(restored)
    rt2.set_override("op", "analog_A.AI1", 19800, "t")
    rt2.run_for(1)
    assert rt2.alarms.open[("RESERVOIR_LEVEL_HIGH", "analog_A.AI1")].occurrence_id == restored[0].occurrence_id
    assert not [e for e in rt2.events if e["kind"] == "ALARM_RAISED"]
    h2.close()


def test_pending_intent_reconciled_as_unknown(tmp_path, cfg24):
    h = Historian(tmp_path / "r.sqlite3", cfg24, "s1")
    rt = make_rt(cfg24, h)
    rt.request_state("op", ControllerState.MANUAL)
    orig = h.command_outcome
    h.command_outcome = lambda rec: (_ for _ in ()).throw(OSError("crash before outcome"))
    rec = rt.operator_command("op", "relay_A.DO1", True)
    assert rec.outcome == Outcome.OUTCOME_UNKNOWN
    h.command_outcome = orig
    ids = h.reconcile_pending_commands()
    assert ids == [rec.command.request_id]
    assert h.query("SELECT outcome FROM commands WHERE request_id=?", (rec.command.request_id,))[0][0] == "OUTCOME_UNKNOWN"
    h.close()


def test_backup_verify_restore_to_new_instance(tmp_path, cfg24):
    h = Historian(tmp_path / "live.sqlite3", cfg24, "s1")
    rt = make_rt(cfg24, h)
    rt.run_for(3)
    h.wait_idle()
    man = create_backup(tmp_path / "live.sqlite3", tmp_path / "bk", config_id=cfg24.config_id)
    v = verify_backup(man["path"])
    assert v["ok"] and v["integrity"] == "ok" and v["counts"]["samples"] == 3 * 24
    with pytest.raises(FileExistsError):
        restore_backup(man["path"], tmp_path / "live.sqlite3")
    res = restore_backup(man["path"], tmp_path / "restored" / "new.sqlite3")
    con = sqlite3.connect(res["target"])
    assert con.execute("SELECT count(*) FROM samples").fetchone()[0] == 72
    con.close()
    # corrupt the copy: verification must fail
    data = bytearray(open(man["path"], "rb").read())
    data[5000] ^= 0xFF
    open(man["path"], "wb").write(bytes(data))
    assert not verify_backup(man["path"])["ok"]
    h.close()


def test_migration_backup_first_and_no_downgrade(tmp_path):
    p = tmp_path / "m.sqlite3"
    con = connect(p)
    con.execute("CREATE TABLE legacy(x)")  # pre-existing data => backup must be taken
    assert migrate(con, p) == list(range(1, CURRENT_VERSION + 1))
    assert schema_version(con) == CURRENT_VERSION
    bk = con.execute("SELECT backup_path FROM migrations WHERE version=1").fetchone()[0]
    assert bk and (tmp_path / "backups").exists()
    con.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(RuntimeError, match="newer"):
        migrate(con, p)
    con.close()
