"""AT-OPS-02 / AT-MIG-01: backup-first v0.1 import with LEGACY_UNKNOWN provenance; compat launcher."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from aquacrop.cli import main as cli
from tests.conftest import ROOT
from tests.legacy.test_v0_1_findings import run, sim_controller, v01  # noqa: F401  (fixture)


@pytest.fixture
def legacy_db(v01, tmp_path):  # noqa: F811
    async def go():
        c = sim_controller(v01, tmp_path, "greenhouse.sqlite3")
        await c.start()
        await c.set_analog(2, 15000, "op")
        await c.set_relay(7, True, "op")
        await c.set_threshold(0, 4000, 20000, "op")
        c.quality = "ERROR"
        c.driver.read = lambda: (_ for _ in ()).throw(IOError("bus down"))
        await c.poll_once()
        await c.stop()
    run(go())
    return tmp_path / "greenhouse.sqlite3"


def test_migration_imports_with_legacy_provenance(legacy_db, tmp_path, capsys):
    before = legacy_db.read_bytes()
    sd = tmp_path / "state"
    assert cli(["migrate-v01", "--legacy-db", str(legacy_db), "--config", "demo-24", "--state-dir", str(sd)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert legacy_db.read_bytes() == before                      # original untouched
    assert (sd / "backups").exists() and res["samples_imported"] >= 4
    con = sqlite3.connect(sd / "demo-24.sqlite3")
    origins = {r[0] for r in con.execute("SELECT DISTINCT origin FROM samples")}
    assert origins == {"LEGACY_UNKNOWN"}
    row = con.execute("SELECT raw_value, raw_unit, value, quality FROM samples WHERE point_id='legacy_v01.AI3' "
                      "AND quality='GOOD' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == (15000.0, "uA", None, "GOOD")                  # raw kept, no invented engineering value
    assert con.execute("SELECT count(*) FROM samples WHERE quality='UNAVAILABLE' AND reason LIKE '%ERROR%'").fetchone()[0] == 24
    assert con.execute("SELECT count(*) FROM events WHERE kind='LEGACY_RELAY_WRITE'").fetchone()[0] == 1
    s = json.loads(con.execute("SELECT payload_json FROM events WHERE kind='LEGACY_SETTINGS_NOT_APPLIED'").fetchone()[0])
    assert "thresholds" in s["settings"]
    con.close()
    # second import of the same file refused
    assert cli(["migrate-v01", "--legacy-db", str(legacy_db), "--config", "demo-24", "--state-dir", str(sd)]) == 2


def test_migration_rejects_non_legacy(tmp_path):
    p = tmp_path / "x.sqlite3"
    sqlite3.connect(p).execute("CREATE TABLE t(x)")
    assert cli(["migrate-v01", "--legacy-db", str(p), "--config", "demo-24", "--state-dir", str(tmp_path / "s")]) == 2


@pytest.mark.parametrize("env,needle", [({"HYDRO_BACKEND": "modbus"}, "REFUSED"),
                                        ({"HYDRO_BENCH_WRITES": "1"}, "REFUSED")])
def test_compat_launcher_refuses_hardware_env(env, needle, tmp_path):
    r = subprocess.run([sys.executable, "-m", "greenhouse"], cwd=tmp_path, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **env})
    assert r.returncode == 2 and needle in r.stderr
