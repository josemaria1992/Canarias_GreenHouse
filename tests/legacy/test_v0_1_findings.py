"""Reproductions of v0.1 baseline findings (brief §3) against the byte-identical vendored source.

These tests PASS when the v0.1 defect is reproduced. They document the baseline; the new
platform's own tests cover the corrections. See docs/architecture/v0_1_findings.md.
"""
import asyncio
import importlib.util
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "legacy" / "greenhouse_control_v0_1"


@pytest.fixture(scope="module")
def v01():
    """Load legacy `greenhouse` as `greenhouse_v01` so it never shadows anything installed."""
    name = "greenhouse_v01"
    spec = importlib.util.spec_from_file_location(name, LEGACY / "greenhouse" / "__init__.py",
                                                  submodule_search_locations=[str(LEGACY / "greenhouse")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    for sub in ("config", "driver", "storage", "controller", "app"):
        importlib.import_module(f"{name}.{sub}")
    return SimpleNamespace(**{s: sys.modules[f"{name}.{s}"] for s in ("config", "driver", "storage", "controller", "app")})


def sim_controller(v01, tmp_path, db="t.sqlite"):
    cfg = v01.config.Config(db_path=str(tmp_path / db), password="the-test-password")
    cfg.validate()
    return v01.controller.Controller(cfg)


class Reply:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def isError(self):
        return False


class FakeClient:
    def __init__(self, modes=None, types=None, fail_readback=False, fail_unit=None):
        self.coils = [False] * 8
        self.modes = [0] * 8 if modes is None else modes
        self.types = [3] * 8 if types is None else types
        self.fail_readback, self.fail_unit, self.reads_after_write = fail_readback, fail_unit, 0
        self.written = False

    def connect(self):
        return True

    def close(self):
        pass

    def _maybe_fail(self, device_id):
        if device_id == self.fail_unit:
            raise IOError(f"timeout unit {device_id}")

    def read_input_registers(self, address, count, device_id):
        self._maybe_fail(device_id)
        return Reply(registers=[12000] * 8)

    def read_holding_registers(self, address, count, device_id):
        self._maybe_fail(device_id)
        return Reply(registers=self.types if device_id == 2 else self.modes)

    def read_coils(self, address, count, device_id):
        self._maybe_fail(device_id)
        if self.fail_readback and self.written:
            raise IOError("readback timeout")
        return Reply(bits=self.coils)

    def read_discrete_inputs(self, address, count, device_id):
        self._maybe_fail(device_id)
        return Reply(bits=[False] * 8)

    def write_coil(self, address, value, device_id):
        self.coils[address] = value
        self.written = True
        return Reply(bits=[])


def mock_driver(v01, client, bench=True):
    d = object.__new__(v01.driver.WaveshareModbusDriver)
    d.client, d.id_key, d.lock = client, "device_id", threading.RLock()
    d.cfg = SimpleNamespace(serial_port="fake", analog_id=2, relay_id=1, bench_writes=bench)
    return d


def run(coro):
    return asyncio.run(coro)


def test_F01_fixed_single_bus_24_points(v01):
    snap = v01.driver.IOSnapshot([0] * 8, [3] * 8, [False] * 8, [False] * 8)
    assert len(snap.analog) + len(snap.digital_inputs) + len(snap.relays) == 24
    fields = v01.config.Config.__dataclass_fields__
    assert {"relay_id", "analog_id", "serial_port"} <= set(fields) and "buses" not in fields


def test_F02_auto_never_commands_outputs(v01, tmp_path):
    async def go():
        c = sim_controller(v01, tmp_path)
        await c.start()
        await c.set_mode("AUTO", "t")
        c.phase_started -= 100
        await c.poll_once()
        relays = c.state()["io"]["relays"]
        await c.stop()
        return relays
    assert not any(run(go()))


def test_F03_alarm_identity_contains_value(v01, tmp_path):
    async def go():
        c = sim_controller(v01, tmp_path)
        await c.start()
        await c.set_analog(0, 3000, "t")
        await c.set_analog(0, 3100, "t")
        await c.set_analog(0, 3200, "t")
        ev = [e for e in c.storage.events(100) if e["type"].startswith("ALARM")]
        await c.stop()
        return ev
    ev = run(go())
    assert sum(e["type"] == "ALARM_RAISED" for e in ev) == 3 and sum(e["type"] == "ALARM_CLEARED" for e in ev) == 2


def test_F04_write_applied_then_readback_fails_without_command_event(v01, tmp_path):
    client = FakeClient(fail_readback=True)
    d = mock_driver(v01, client)
    with pytest.raises(IOError):
        d.write_relay(0, True)
    assert client.coils[0] is True  # side effect happened, caller only saw an exception


def test_F05_short_mode_replies_accepted(v01):
    client = FakeClient(modes=[])          # empty relay-mode reply
    d = mock_driver(v01, client)
    d.write_relay(1, True)                  # write proceeds: any([]) is False
    assert client.coils[1] is True
    client2 = FakeClient(types=[3, 3])      # 2 of 8 analog types
    snap = mock_driver(v01, client2).read()
    assert len(snap.analog_types) == 2      # short reply silently accepted


def test_F06_one_failed_module_invalidates_whole_snapshot(v01):
    d = mock_driver(v01, FakeClient(fail_unit=1))  # only the relay module fails
    with pytest.raises(IOError):
        d.read()                                    # analog data from healthy unit 2 is discarded too


def test_F07_simulated_limits_carry_into_hardware_mode(v01, tmp_path):
    async def go():
        c = sim_controller(v01, tmp_path, "shared.sqlite")
        await c.start()
        await c.set_threshold(0, 4000, 20000, "t")
        await c.stop()
    run(go())
    st = v01.storage.Storage(str(tmp_path / "shared.sqlite"))
    stored = st.settings_get("thresholds", None)
    st.close()
    # controller.py:26 loads this for ANY backend, including modbus (whose default is unspecified limits)
    assert stored[0] == {"min": 4000, "max": 20000}


def test_F08_single_shared_basic_credential(v01, tmp_path):
    src = (LEGACY / "greenhouse" / "app.py").read_text(encoding="utf-8")
    assert "HTTPBasic" in src and "csrf" not in src.lower() and "role" not in src.lower()


def test_F09_samples_lack_provenance(v01, tmp_path):
    st = v01.storage.Storage(str(tmp_path / "p.sqlite"))
    cols = {r[1] for r in st.db.execute("PRAGMA table_info(samples)")}
    st.close()
    assert not cols & {"origin", "calibration_ref", "device_uid", "session_id", "config_version"}


def test_F10_static_ui_missing_from_wheel(tmp_path):
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w",
                        str(tmp_path), str(LEGACY)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    whl = next(tmp_path.glob("*.whl"))
    names = zipfile.ZipFile(whl).namelist()
    assert not any(n.endswith("index.html") for n in names)


def test_F11_background_poll_failure_unsupervised(v01, tmp_path):
    async def go():
        c = sim_controller(v01, tmp_path)
        await c.start()
        def boom(*a, **k):
            raise OSError("disk I/O error")
        c.storage.sample = boom
        await asyncio.sleep(0)  # let loop run
        c.config = SimpleNamespace(**{**c.config.__dict__, "poll_seconds": 0.01})
        await asyncio.sleep(0.2)
        done, exc = c.task.done(), c.task.exception() if c.task.done() else None
        c.running = False
        c.storage.sample = lambda *a, **k: None
        return done, exc
    done, exc = run(go())
    assert done and isinstance(exc, OSError)   # polling died; nothing restarts or reports it


def test_F12_chart_is_index_based_and_bridges_gaps():
    html = (LEGACY / "static" / "index.html").read_text(encoding="utf-8")
    assert "row.quality==='OK'" in html and "n/(Math.max(1,values.length-1))" in html
