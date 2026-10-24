"""The program engine on the real controller (0.5.0, ADR-0018 §9): scheduled physical bench tests.

It exists only when the configuration enables `bench_tests`; otherwise the real system has no engine and every
program route says why. When it exists:
* every program command has origin PROGRAM_BENCH, carries the active bench lease, and goes through the bus's
  worker and the audited CommandService; the lease gate admits it only for an output in both the configuration's
  bench-test allowlist and the lease's allowlist, and asks this engine (mode, recovery) again under the bus lock
  immediately before the frame;
* with no armed lease nothing starts (the occurrence is recorded as FAILED with the reason), and a lease that ends
  (expiry, revocation, logout, restart) stops every operation; the configured LEASE_END safe-state OFF follows;
* only VERIFIED channel bindings are used: an item with a PROPOSED channel has no point for that role here;
* activation of a program version needs that version's accepted twin result and no physical validation problem.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from ..domain.model import CommandOrigin, CommandRecord, Outcome
from ..equipment.definitions import ItemBody, TypeBody
from .engine import Clock, Engine, ItemView
from .store import ProgramStore, SqlEngineStore

STORE_ID = "LIVE"
TICK_S = 0.5


class LiveEngineHost:
    def __init__(self, svc, hist, equipment, programs: ProgramStore, session_id: str):
        self.svc, self.hist, self.equipment, self.programs = svc, hist, equipment, programs
        self.runtime_id = f"LIVE:{session_id}"
        self.store = SqlEngineStore(hist, STORE_ID, session_id)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        host = self

        class IO:
            def sample(self, point):
                return host.svc.samples.get(point)

            def command(self, point, desired, *, actor, reason, priority):
                lease = host.svc.gate.active()
                cmd = host.svc.commands.build(actor=actor, target=point, desired=desired,
                                              origin=CommandOrigin.PROGRAM_BENCH, reason=reason, priority=priority,
                                              lease_id=lease.lease_id if lease else None)
                if lease is None and desired:
                    rec = CommandRecord(cmd, Outcome.ACCEPTED)
                    rec.advance(Outcome.REJECTED, "no armed bench test session")
                    host.svc.commands._audit_rejected(rec)
                    return rec
                bus = host.svc.cfg.device(point.split(".", 1)[0]).bus_id
                return host.svc.workers[bus].submit(cmd, lambda: host.svc.commands.submit(cmd)).result(timeout=40)

            def module_online(self, uid):
                return host.svc.backend.health(uid).online

            def start_refusal(self):
                lease = host.svc.gate.active()
                if lease is None:
                    return "no armed bench test session: arm one on the I/O board (it lasts up to 30 minutes)"
                return None

        self.engine = Engine(STORE_ID, svc.cfg.site.timezone, IO(),
                             Clock(lambda: datetime.now(timezone.utc), time.monotonic), self.store,
                             resolve=self.views, programs=lambda: programs.active(STORE_ID),
                             event=lambda k, p, a: hist.event(k, p | {"runtime": self.runtime_id}, actor=a),
                             alarm=self._alarm, physical=True)
        svc.gate.program_guard = self.engine.on_refusal
        prev = svc.gate.on_end

        def lease_ended(lease):
            self.engine.request_stop_all(f"bench test session ended ({lease.ended_reason})")
            if prev is not None:
                prev(lease)
        svc.gate.on_end = lease_ended

    def _alarm(self, kind, target, active, detail):
        self.hist.event("PROGRAM_ALARM_" + ("RAISED" if active else "CLEARED"), {"rule": kind, "target": target} | detail,
                        actor="system")

    def views(self) -> dict[str, ItemView]:
        types = {(t["type_id"], t["version"]): TypeBody.model_validate(t["body"]) for t in self.equipment.types()}
        binds = self.equipment.bindings()
        out = {}
        for it in self.equipment.items(include_retired=False):
            tb = types.get((it["type_id"], it["type_version"]))
            if tb is None:
                continue
            mine = {r: b for (iid, r), b in binds.items() if iid == it["item_id"] and b["status"] == "VERIFIED"}
            out[it["item_id"]] = ItemView(it["item_id"], it["name"], tb, ItemBody.model_validate(it["body"]),
                                          {r: b["point"] for r, b in mine.items()}, {r: True for r in mine})
        return out

    def start(self) -> None:
        self.engine.start()
        self._thread = threading.Thread(target=self._run, name="program-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self.engine.tick()
            except Exception as e:      # supervised: recorded, the next tick tries again
                self.last_error = repr(e)
                self.hist.event("PROGRAM_ENGINE_ERROR", {"error": repr(e)}, actor="system")
