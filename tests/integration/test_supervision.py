"""AT-OPS-04: background acquisition failures are supervised, bounded and visible."""
import asyncio
import time

import pytest

from aquacrop.api import service as service_mod
from aquacrop.api.hardware import BusWorker
from aquacrop.api.service import SimulationService
from aquacrop.historian.writer import Historian


def test_simulation_driver_restarts_then_fails_visibly(tmp_path, cfg24, monkeypatch):
    h = Historian(tmp_path / "s.sqlite3", cfg24, "s_sup")
    svc = SimulationService(cfg24, h, "s_sup")
    real_sleep = asyncio.sleep

    async def fast_sleep(_s):
        await real_sleep(0)
    monkeypatch.setattr(service_mod.asyncio, "sleep", fast_sleep)

    def boom(_dt):
        raise OSError("disk I/O error (simulated)")
    svc.clock.advance_wall = boom
    asyncio.run(asyncio.wait_for(svc.run_forever(), timeout=10))   # returns once FAILED
    th = svc.task_health()
    assert th["state"] == "FAILED" and not th["ok"] and th["restarts"] == service_mod.MAX_FAILURES_PER_10_MIN
    assert "disk I/O error" in th["last_error"]
    h.wait_idle()
    n = h.query("SELECT count(*) FROM events WHERE kind='ACQUISITION_TASK_FAILED'")[0][0]
    assert n == service_mod.MAX_FAILURES_PER_10_MIN + 1
    h.close()


def test_bus_worker_failures_bounded_and_reported():
    calls = []

    def poll(bus):
        calls.append(bus)
        raise RuntimeError("bus exploded")
    w = BusWorker("b1", 0.01, poll)
    w.start()
    deadline = time.monotonic() + 5
    while w.state != "FAILED" and time.monotonic() < deadline:
        time.sleep(0.02)
    h = w.health()
    assert h["state"] == "FAILED" and not h["ok"] and "bus exploded" in h["last_error"]
    assert len(calls) == 6                       # 5 tolerated within 10 minutes, the 6th stops it
    w.stop(lambda c, why: None)


def test_bus_worker_recovers_from_transient_failure():
    n = {"i": 0}

    def poll(bus):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("transient")
    w = BusWorker("b2", 0.01, poll)
    w.start()
    deadline = time.monotonic() + 5
    while w.polls < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert w.state == "RUNNING" and w.health()["ok"] and w.last_error
    w.stop(lambda c, why: None)
