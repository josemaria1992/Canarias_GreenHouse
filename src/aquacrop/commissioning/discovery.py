"""Bounded, read-only discovery on the installation buses (0.5.0).

Discovery runs inside each bus's own worker thread, after that bus's regular poll, so it never races a
second serial client against acquisition. Each step reads one address register (FC03, one frame, plus the
bus's read retries); an answering address is then identified with the same reads `hw scan` uses (FC03/FC04/
FC02, never a write). A step runs only when the poll just finished within half its interval; otherwise the
sweep waits and the view says why. A full sweep of the range is on request; in the background one address
is read every `background_every` polls.

What it reports, and what it cannot know:
* KNOWN: a configured module answers at its address with the expected kind (digital/analog).
* CHANGED: a configured address answers with another kind.
* MISSING: a configured module did not answer its last regular poll (from acquisition, not from discovery).
* NEW: an unconfigured address answers; it gets no channel, no binding and no output permission.
* Nothing answering is not proof an address is free, and two modules at one address can answer as one.
A missing module and its records are kept until a reviewed configuration removes it.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from ..config.catalog import CATALOG
from ..drivers import setup as hw

UNIT_RANGE = (1, 32)
BUDGET_FRACTION = 0.5


class Discovery:
    def __init__(self, cfg, buses: dict, *, background_every: int = 30, monotonic=time.monotonic,
                 unit_range: tuple[int, int] = UNIT_RANGE):
        self.cfg, self.buses, self._mono = cfg, buses, monotonic
        self.background_every, self.unit_range = background_every, unit_range
        self._lock = threading.Lock()
        self._state = {b.bus_id: {"cursor": unit_range[0], "full": False, "full_started": None, "polls": 0,
                                  "seen": {}, "held": None, "steps": 0, "frames": 0} for b in cfg.buses if b.port}

    def request_full(self, bus_id: str, actor: str) -> dict:
        with self._lock:
            st = self._state.get(bus_id)
            if st is None:
                raise KeyError(bus_id)
            st.update(full=True, cursor=self.unit_range[0], full_started=_now(), held=None)
        return self.view()

    def after_poll(self, bus_id: str, poll_took_s: float, interval_s: float) -> None:
        """Called by the bus worker after its poll; does at most one discovery step."""
        st = self._state.get(bus_id)
        if st is None:
            return
        with self._lock:
            st["polls"] += 1
            due = st["full"] or st["polls"] % self.background_every == 0
            if not due:
                return
            if poll_took_s > BUDGET_FRACTION * interval_s:
                st["held"] = (f"the regular poll took {poll_took_s:.2f} s of its {interval_s:g} s interval; discovery "
                              "waits so acquisition and safety polling keep their period")
                return
            st["held"] = None
            unit = st["cursor"]
        bus = self.buses[bus_id]
        before = bus.stats.requests
        found = None
        if hw.read_one(bus, unit, hw.ADDRESS_REGISTER) is not None:
            found = hw.identify(bus, unit)
        with self._lock:
            st["frames"] += bus.stats.requests - before
            st["steps"] += 1
            if found:
                st["seen"][unit] = found | {"utc": _now()}
            elif unit in st["seen"]:
                if self._configured(bus_id, unit):
                    st["seen"][unit]["last_silent_utc"] = _now()
                else:
                    del st["seen"][unit]             # an unconfigured address that went quiet is not kept
            nxt = unit + 1
            if nxt > self.unit_range[1]:
                nxt = self.unit_range[0]
                st["full"] = False
                st["last_full_utc"] = _now()
            st["cursor"] = nxt

    def _configured(self, bus_id: str, unit: int):
        return next((d for d in self.cfg.devices if d.bus_id == bus_id and d.address == unit), None)

    def view(self, health=None) -> dict:
        out = {}
        with self._lock:
            for bus_id, st in self._state.items():
                rows = []
                for d in self.cfg.devices:
                    if d.bus_id != bus_id:
                        continue
                    want = "analog" if CATALOG[d.model].ai else "digital"
                    seen = st["seen"].get(d.address)
                    answering = health(d.device_uid) if health else None
                    if answering is False:
                        status = "MISSING"
                    elif seen and seen.get("kind") != want:
                        status = "CHANGED"
                    else:
                        status = "KNOWN"
                    rows.append({"address": d.address, "status": status, "device_uid": d.device_uid,
                                 "model": d.model.value, "expected_kind": want,
                                 "seen_kind": seen.get("kind") if seen else None,
                                 "version_register": seen.get("version_register") if seen else None})
                for unit, seen in sorted(st["seen"].items()):
                    if not self._configured(bus_id, unit):
                        rows.append({"address": unit, "status": "NEW", "device_uid": None, "model": None,
                                     "seen_kind": seen.get("kind"), "version_register": seen.get("version_register"),
                                     "utc": seen.get("utc"),
                                     "note": "not configured: no channels, no bindings, no output permission"})
                out[bus_id] = {"rows": sorted(rows, key=lambda r: r["address"]), "full_sweep": st["full"],
                               "cursor": st["cursor"], "range": list(self.unit_range), "held": st["held"],
                               "steps": st["steps"], "frames_sent": st["frames"],
                               "last_full_utc": st.get("last_full_utc"),
                               "limits": ("no answer is not proof an address is free; two modules at one address can "
                                          "answer as one; discovery reads only")}
        return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
