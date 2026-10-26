"""The twin service (0.5.0): runs the user's equipment and programs on virtual modules, beside the live service.

* Its configuration is made from the live snapshot with `profile: SIMULATED`, every bus turned `virtual` (a
  SIMULATED configuration cannot name a serial port: config validation refuses it), safe-state off, its own
  config_id and database. The process that serves it runs as a user without serial access (deployment).
* Its runtime id is `TWIN:<session>`; every state-changing twin request must carry it (stale or wrong pages
  are refused), and live requests carry `LIVE:<session>`.
* Simulated time runs at the chosen speed with a fixed step. The loop is bounded: at most `MAX_STEPS_PER_S`
  steps per wall second; when that bound limits the chosen speed, the view says so.
* Results that a user accepts become evidence files for the live service (`feed.write_evidence`); nothing
  else leaves the twin.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .. import layout
from ..config.schema import InstallationConfig
from ..control.commands import CommandService
from ..domain.model import CommandOrigin, ControllerState, new_id
from ..equipment.definitions import ItemBody, TypeBody
from ..programs.definitions import digest as program_digest, parse
from ..programs.engine import Clock, Engine, ItemView
from ..programs.store import ProgramStore, SqlEngineStore
from .equipment_twin import EquipmentTwin, SimClock
from .graph_water import GraphWater, ModelError, params_from
from .water import Water
from . import feed

SPEEDS = (1, 10, 60, 600, 3600)
DT_S = 0.5
MAX_STEPS_PER_S = 400
FRAMES = 2400                     # timeline: ten minutes of wall time at four frames a second


def twin_config(live: dict, *, allowed_hosts: list[str], allowed_origins: list[str], virtual=None) -> InstallationConfig:
    """`virtual`: the twin's simulated modules (0.7.0); they add a virtual bus and devices to the twin only."""
    d = dict(live)
    d["profile"] = "SIMULATED"
    d["config_id"] = f"{live['config_id']}-twin"
    d["config_version"] = f"{live['config_version']}-twin"
    d["label"] = "DIGITAL TWIN — simulated modules, no real outputs"
    d["buses"] = [{"bus_id": b["bus_id"], "kind": "virtual", "poll_interval_s": b.get("poll_interval_s", 1.0)}
                  for b in live["buses"]]
    d["safe_state"] = {"outputs_off_on": []}
    d.pop("bench_tests", None)                    # physical bench tests are for the real installation only
    d["physical_mapping"] = []
    d["drive_adapters"] = [a for a in live.get("drive_adapters", []) if a.get("kind") == "NOT_CONFIGURED"]
    h = dict(live.get("historian", {}))
    h["db_path"] = "aquacrop-twin.sqlite3"
    d["historian"] = h
    d["security"] = dict(live.get("security", {}), allowed_hosts=allowed_hosts, allowed_origins=allowed_origins)
    d.pop("offsite_backup", None)
    if virtual is not None and virtual.all():
        d = virtual.merge(d)
        d["config_version"] += "+sim-" + "-".join(sorted(virtual.uids()))
    return InstallationConfig.model_validate(d)


class TwinItems:
    """Equipment as imported from the live snapshot; the twin's channels are the same names on virtual modules."""

    def __init__(self):
        self.types: dict[tuple[str, int], TypeBody] = {}
        self.items: dict[str, dict] = {}
        self.bindings: dict[str, dict[str, dict]] = {}
        self._views: dict[str, ItemView] = {}

    def load(self, snap: dict) -> None:
        eq = snap["equipment"]
        self.types = {(t["type_id"], t["version"]): TypeBody.model_validate(t["body"]) for t in eq["types"]}
        self.items = {i["item_id"]: i for i in eq["items"] if i["status"] == "ACTIVE"}
        self.bindings = {}
        for b in snap.get("bindings", []):
            self.bindings.setdefault(b["item_id"], {})[b["role"]] = b
        self._views = {}
        for iid, it in self.items.items():
            tb = self.types.get((it["type_id"], it["type_version"]))
            if tb is None:
                continue
            bs = self.bindings.get(iid, {})
            self._views[iid] = ItemView(iid, it["name"], tb, ItemBody.model_validate(it["body"]),
                                        {r: b["point"] for r, b in bs.items()},
                                        {r: b["status"] == "VERIFIED" for r, b in bs.items()})

    def views(self) -> dict[str, ItemView]:
        return self._views


class EquipmentTwinService:
    kind = "twin"

    def __init__(self, cfg: InstallationConfig, hist, session_id: str, feed_dir: Path, *, start_utc: datetime | None = None):
        self.cfg, self.hist, self.session_id, self.feed_dir = cfg, hist, session_id, Path(feed_dir)
        self.store_id = "TWIN"                      # activations, occurrences and mode persist across twin restarts
        self.lock = threading.RLock()
        self.items = TwinItems()
        self.clock = SimClock(start_utc or datetime.now(timezone.utc).replace(microsecond=0))
        self.twin = EquipmentTwin(cfg, self.clock, self.items.views)
        self.commands = CommandService(cfg, self.twin.backend, hist, lambda c: None, lambda: ControllerState.AUTO)
        self.programs = ProgramStore(hist)
        self.speed, self.running = 1, False
        self.effective_speed = 0.0
        self.snapshot_utc: str | None = None
        self.snap: dict | None = None             # the last imported live snapshot, before the twin's changes
        self.changes = None                       # TwinChanges (0.6.0), set by the app
        self.run_id = new_id("twinrun")
        self.store = SqlEngineStore(hist, self.store_id, session_id, key_prefix=self.run_id)
        self.results: list[dict] = []
        self.frames: deque[dict] = deque(maxlen=FRAMES)
        self.water = Water()                      # 0.6.4: water along the drawing's pipes
        self.layout_fn = None                     # () -> (digest, layout doc), set by the app
        self.blocks = None                        # BlockStore of the twin (0.7.0), set by the app
        self.virtual = None                       # VirtualModules of the twin (0.7.0), set by the app
        self.gwater = GraphWater()                # 0.7.0: water over the explicit graph, when the twin has one
        self.model_error: str | None = None
        self.loop_error: str | None = None
        self._op_context: dict[str, str] = {}     # op -> the context digest it started in
        self.engine = self._engine()
        self.alarms = self._alarm_engine()        # 0.7.0: item alarm limits evaluated in the twin as on the real system

    def _alarm_engine(self):
        from ..control.alarms import AlarmEngine
        return AlarmEngine(self.cfg, emit=lambda kind, payload: self.hist.event(
            "TWIN_" + kind, payload | {"run_id": self.run_id}, actor="system"))

    @property
    def runtime_id(self) -> str:
        """What every state-changing request must carry: this process and this twin run (a reset changes it)."""
        return f"TWIN:{self.session_id}:{self.run_id}"

    def _engine(self) -> Engine:
        svc = self

        class IO:
            def sample(self, point):
                return svc.twin.samples().get(point)

            def command(self, point, desired, *, actor, reason, priority):
                cmd = svc.commands.build(actor=actor, target=point, desired=desired, origin=CommandOrigin.CONTROLLER,
                                         reason=f"[twin] {reason}", priority=priority)
                return svc.commands.submit(cmd)

            def module_online(self, uid):
                return svc.twin.backend.health(uid).online

        def on_alarm(kind, target, active, detail):
            self.hist.event("TWIN_ALARM_" + ("RAISED" if active else "CLEARED"), {"rule": kind, "target": target} | detail,
                            actor="system")

        def on_event(kind, payload, actor):
            self.hist.event(kind, payload | {"runtime": self.runtime_id, "sim_utc": self.clock.utc().isoformat()},
                            actor=actor)
            if kind == "PROGRAM_OP_STARTED":
                self._op_context[payload["op_id"]] = self.context(payload["program"], payload["version"])["digest"]
            if kind == "PROGRAM_OP_ENDED":
                # the proof belongs to the context the run started in, and only if nothing semantic changed during it
                start = self._op_context.pop(payload["op_id"], None)
                end = self.context(payload["program_id"], payload["version"])["digest"]
                self.results.append(payload | {"twin_run_id": self.run_id, "context": start if start == end else None,
                                               "context_changed": start != end})
                del self.results[:-500]
        return Engine(self.store_id, self.cfg.site.timezone, IO(), Clock(self.clock.utc, self.clock.mono), self.store,
                      resolve=self.items.views, programs=lambda: self.programs.active(self.store_id),
                      event=on_event, alarm=on_alarm, physical=False)

    # ------------------------------------------------------------------ snapshot import
    def import_snapshot(self, actor: str) -> dict:
        snap = feed.read_snapshot(self.feed_dir)
        if snap is None:
            raise ValueError("no snapshot from the real system yet; open the dashboard of the real system once")
        with self.lock:
            self.snap = snap
            if self.changes is not None:
                self.changes.reconcile(snap)         # what the real system now has is no longer a change
            self.items.load(self.changes.overlay(snap) if self.changes is not None else snap)
            hyd = self.blocks.import_snapshot(snap.get("hydraulics")) if self.blocks is not None else None
            rebuilt = False
            if f"{snap['config_version']}-twin" != self.cfg.config_version.split("+sim-")[0]:
                self._rebuild(snap["config"], actor)  # modules changed on the real system: a controlled reset
                rebuilt = True
            have = {(v["program_id"], v["version"]): v for v in self.programs.versions()}
            imported = 0
            for p in snap["programs"]:
                key = (p["program_id"], p["version"])
                if key in have and have[key]["digest"] == p["digest"]:
                    continue
                if key in have:
                    mine = have[key]
                    untouched = (mine["created_by"] == "live-snapshot" and mine["status"] == "DRAFT"
                                 and mine["updated_utc"] == mine["created_utc"])
                    if not untouched:
                        continue                      # a local edit of the same version number stays local
                    self._refresh_draft(p)            # the real system corrected its draft: follow it
                    imported += 1
                    continue
                now = datetime.now(timezone.utc).isoformat()
                row = (p["program_id"], p["version"], p["status"] if p["status"] in ("VALIDATED", "INVALID") else "DRAFT",
                       p["name"], p["digest"], now, "live-snapshot", now, json.dumps(p["body"], sort_keys=True))
                self.hist.submit(lambda c, row=row: c.execute(
                    "INSERT INTO programs(program_id, version, status, name, digest, created_utc, created_by, updated_utc, "
                    "json) VALUES(?,?,?,?,?,?,?,?,?)", row), wait=True)
                imported += 1
            self.snapshot_utc = snap["written_utc"]
            self.hist.event("TWIN_SNAPSHOT_IMPORTED", {"written_utc": snap["written_utc"], "items": len(self.items.items),
                                                       "programs_new": imported, "config_rebuilt": rebuilt,
                                                       "hydraulics": hyd}, actor=actor)
            return {"written_utc": snap["written_utc"], "items": len(self.items.items), "programs_new": imported,
                    "config_rebuilt": rebuilt}

    def _refresh_draft(self, p: dict) -> None:
        """Replace an untouched copy of a live draft with its corrected body; its twin results no longer count."""
        row = (p["name"], p["digest"], json.dumps(p["body"], sort_keys=True), p["program_id"], p["version"])
        self.hist.submit(lambda c: c.execute(
            "UPDATE programs SET name=?, digest=?, json=?, validation_json=NULL, simulation_json=NULL "
            "WHERE program_id=? AND version=? AND status='DRAFT'", row), wait=True)
        self.results = [r for r in self.results
                        if not (r.get("program_id") == p["program_id"] and r.get("version") == p["version"])]

    def _rebuild(self, live_config: dict, actor: str) -> None:
        sec = self.cfg.security
        self.cfg = twin_config(live_config, allowed_hosts=list(sec.allowed_hosts), allowed_origins=list(sec.allowed_origins),
                               virtual=self.virtual)
        self.twin = EquipmentTwin(self.cfg, self.clock, self.items.views)
        self.commands = CommandService(self.cfg, self.twin.backend, self.hist, lambda c: None, lambda: ControllerState.AUTO)
        self.reset(actor, self.clock.start_utc)

    def rebuild_modules(self, actor: str) -> None:
        """After a simulated module is added or removed: rebuild the twin's configuration and reset (a new run)."""
        if self.snap is None:
            raise ValueError("no snapshot from the real system yet")
        with self.lock:
            self._rebuild(self.snap["config"], actor)
            self.items.load(self.changes.overlay(self.snap) if self.changes is not None else self.snap)

    def sync_users(self, accounts) -> int:
        """One-way copy of the live accounts. A changed password, role, commissioning flag or a disabled or removed
        account ends that user's twin sessions; a removed account stays disabled here."""
        snap = feed.read_snapshot(self.feed_dir)
        if snap is None:
            return 0
        before = {r["username"]: dict(r) for r in self.hist.query(
            "SELECT username, password_hash, role, commissioning, disabled FROM users")}
        n = 0
        for u in snap.get("users", []):
            row = (u["username"], u["password_hash"], u["role"], int(u["commissioning"]), int(u["disabled"]),
                   datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
                   int(u["must_change_password"]))
            self.hist.submit(lambda c, row=row: c.execute(
                "INSERT INTO users(username, password_hash, role, commissioning, disabled, created_utc, "
                "password_changed_utc, must_change_password) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE "
                "SET password_hash=excluded.password_hash, role=excluded.role, commissioning=excluded.commissioning, "
                "disabled=excluded.disabled, must_change_password=excluded.must_change_password", row), wait=True)
            old = before.get(u["username"])
            if old is not None and ((old["password_hash"], old["role"], int(old["commissioning"])) !=
                                    (u["password_hash"], u["role"], int(u["commissioning"])) or u["disabled"]):
                accounts.revoke_user_sessions(u["username"], "account changed on the real system")
            n += 1
        live = {u["username"] for u in snap.get("users", [])}
        for name in before:
            if name not in live:
                self.hist.submit(lambda c, n_=name: c.execute("UPDATE users SET disabled=1 WHERE username=?",
                                                              (n_,)), wait=True)
                accounts.revoke_user_sessions(name, "account removed on the real system")
        return n

    # ------------------------------------------------------------------ control of the simulation
    def set_running(self, actor: str, running: bool) -> None:
        self.running = running
        self.hist.event("TWIN_RUN" if running else "TWIN_PAUSE", {"speed": self.speed}, actor=actor)

    def set_speed(self, actor: str, speed: int) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed is one of {SPEEDS}")
        self.speed = speed
        self.hist.event("TWIN_SPEED", {"speed": speed}, actor=actor)

    def reset(self, actor: str, start_utc: datetime | None = None) -> None:
        with self.lock:
            self.running = False
            self.twin.reset()
            self.clock.start_utc = start_utc or datetime.now(timezone.utc).replace(microsecond=0)
            self.clock.sim_s = 0.0
            self.run_id = new_id("twinrun")
            self.store = SqlEngineStore(self.hist, self.store_id, self.session_id, key_prefix=self.run_id)
            self.engine = self._engine()
            self.twin.step(0.0)
            self.engine.start()
            self.frames.clear()
            self.water.reset()
            self.gwater.reset()
            self.alarms = self._alarm_engine()
            self.model_error = None
            self._frame()
            self.hist.event("TWIN_RESET", {"start_utc": self.clock.start_utc.isoformat(), "run_id": self.run_id},
                            actor=actor)

    def step(self, n: int, budget_s: float | None = None) -> int:
        """Advance up to `n` steps; with a budget, stop when it is spent. The lock is taken per step, so the
        dashboard's requests get through between steps even at high speed. Returns the steps done."""
        t0, done = time.monotonic(), 0
        for _ in range(n):
            # One fixed step in a causal order that does not depend on the batch size (0.7.0, F04): the clock
            # advances with the outputs committed so far, the water moves for that step, the inputs (with the
            # water's readings of this step) are published, then the programs act on them for the next step.
            with self.lock:
                self.twin.step(DT_S, prepare=lambda: self._water_observed(DT_S))
                self.engine.tick()
            done += 1
            if budget_s is not None and time.monotonic() - t0 > budget_s:
                break
        if done:
            with self.lock:
                self.water.publish()                  # the page's picture: once per batch
                if self.alarms is not None:
                    from ..equipment.views import limit_inputs
                    self.alarms.evaluate_item_limits(limit_inputs(self.items.views(), self.twin.samples(), self.clock.utc()),
                                                     self.clock.sim_s, self.clock.sim_s)
                self._frame()
        return done

    def graph(self):
        """The twin's explicit graph when it has one (0.7.0); None: the older drawing model runs."""
        if self.blocks is None:
            return None
        g = self.blocks.graph()
        return g if g.nodes else None

    def _water_observed(self, dt: float) -> None:
        g = self.graph()
        if g is None:
            self.step_water(dt, publish=False)
            if self.layout_fn is not None:
                self.twin.external = self.water.observe()
            return
        if self.model_error is not None:
            return
        views = self.items.views()
        p = params_from(g, views)
        node_item = {n.id: n.item for n in g.nodes}
        try:
            self.gwater.step(dt, g, p, lambda n: bool(node_item.get(n)) and self._item_on(node_item[n]))
        except ModelError as e:                    # an invalid model run: paused and reported, never evidence
            self.model_error = str(e)
            self.running = False
            self.hist.event("TWIN_MODEL_ERROR", {"error": str(e), "run_id": self.run_id}, actor="system")
            return
        ext = {}
        for nid, v in self.gwater.readings(g, p).items():
            if v is not None and node_item.get(nid):
                ext[node_item[nid]] = v
        self.twin.external = ext

    def _item_on(self, iid: str) -> bool:
        """A pump runs or a valve is open: its twin output is on and its start or travel time has passed."""
        it = self.items.views().get(iid)
        cp = it.command_point if it else None
        if not cp:
            return False                              # no channel: nothing can switch it in the twin
        fault = self.twin.s.faults.get(iid)
        if fault == "STUCK_ON":
            return True
        if fault == "NO_FLOW" or not self.twin.s.coils.get(cp):
            return False
        beh = it.body.simulation or it.type.simulation
        return self.clock.sim_s - self.twin.s.on_since.get(cp, 0.0) >= beh.delay_s

    def _item_rate(self, iid: str) -> float:
        it = self.items.views().get(iid)
        if it is None:
            return 0.0
        beh = it.body.simulation or it.type.simulation
        return float(it.body.properties.get("rated_flow_l_min") or beh.demo_flow_L_min or 20.0)

    def step_water(self, dt: float, publish: bool = True) -> None:
        if self.layout_fn is None:
            return
        try:
            digest, doc = self.layout_fn()
            self.water.step(dt, digest, doc, self._item_on, self._item_rate, publish=publish)
            if publish:
                self.twin.external = self.water.item_values()
        except Exception as e:                       # a drawing being edited never stops the simulation
            self.water.state["notes"] = [f"water not simulated: {e}"]

    def reload_items(self) -> None:
        """After a change in the twin's change set: the twin runs the snapshot with the changes applied."""
        with self.lock:
            if self.snap is not None:
                self.items.load(self.changes.overlay(self.snap) if self.changes is not None else self.snap)

    # ------------------------------------------------------------------ the diagram and its timeline (0.5.6)
    def readings(self, compact: bool = False) -> dict[str, dict]:
        with self.lock:
            views = self.items.views()
            cmd = {iid: self.engine.item_state(iid).get("commanded", False) for iid in views}
            r = layout.item_readings(views, self.twin.samples(), commanded=cmd, faults=dict(self.twin.s.faults))
        if compact:                                   # timeline pictures keep only items that show something
            r = {i: x for i, x in r.items() if x["coil"] != "NONE" or x["commanded"] or x["fault"]
                 or any(v["value"] is not None for v in x["values"].values())}
        return r

    def _frame(self) -> None:
        """One picture of every item for the timeline: recorded after each batch of steps (four a second while
        running), so a faster simulation keeps proportionally more simulated time."""
        ops = [{"program_id": o["program_id"], "version": o["version"], "step": o.get("step"), "state": o["state"]}
               for o in self.engine.view()["ops"]]
        self.frames.append({"sim_utc": self.clock.utc().isoformat(), "sim_s": round(self.clock.sim_s, 1),
                            "run_id": self.run_id, "items": self.readings(compact=True), "ops": ops,
                            "water": self.water.state})

    def start(self) -> None:
        with contextlib.suppress(ValueError):          # no snapshot yet: the twin starts empty
            self.import_snapshot("system")
        self.twin.step(0.0)
        self.engine.start()
        self.step_water(0.0)
        self.twin.step(0.0)                            # the sensors read the water before the restart checklist
        self._frame()

    async def run_forever(self) -> None:
        carry = 0.0
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            wall = now - last
            last = now
            if not self.running:
                self.effective_speed = 0.0
                continue
            want = self.speed * wall / DT_S + carry
            n = int(want)
            cap = int(MAX_STEPS_PER_S * wall)
            carry = want - n if n <= cap else 0.0
            n = min(n, cap)
            t0 = time.monotonic()
            try:
                done = await asyncio.to_thread(self.step, n, 0.2)   # at most 0.2 s of work per round
                if done < n:
                    carry = 0.0                         # behind: drop the backlog instead of piling it up
                n = done
                self.loop_error = None
            except Exception as e:                     # supervised: paused, reported by health, never silent
                self.loop_error = repr(e)
                self.running = False
                self.hist.event("TWIN_LOOP_ERROR", {"error": repr(e)}, actor="system")
                continue
            self.effective_speed = n * DT_S / wall if wall > 0 else 0.0
            if time.monotonic() - t0 > 0.2:
                await asyncio.sleep(0.05)              # leave the event loop and the CPU to the dashboard

    # ------------------------------------------------------------------ results and evidence
    def context(self, program_id: str, version: int) -> dict:
        """What a run of this program version tests (0.7.0, F01): see programs/proof.py, shared with the real
        system. A result counts as proof only in this same context and in the current twin run."""
        from ..programs import proof
        try:
            v = self.programs.get(program_id, version)
        except Exception:
            return {"digest": None, "items": []}
        g, lay = self.graph(), None
        if g is None and self.layout_fn is not None:
            with contextlib.suppress(Exception):
                lay = self.layout_fn()[0]
        return proof.context(v, self.items.views(), g, lay)

    def current_proof(self, program_id: str, version: int) -> list[dict]:
        """Results of this program version from the current twin run in the current context; older ones are history."""
        ctx = self.context(program_id, version)["digest"]
        return [r for r in self.results if r.get("program_id") == program_id and r.get("version") == version
                and r.get("twin_run_id") == self.run_id and ctx is not None and r.get("context") == ctx]

    def accept(self, actor: str, program_id: str, version: int, note: str) -> dict:
        v = self.programs.get(program_id, version)
        runs = self.current_proof(program_id, version)          # this run and this context only
        passed = [r for r in runs if r.get("result") == "DONE"]
        if not passed:
            raise ValueError(f"no operation of {program_id} v{version} ended DONE in this twin run with the current "
                             "equipment, channels and drawing")
        if any(r.get("result") in ("FAILED", "STOP_UNVERIFIED") for r in runs) and not note.strip():
            raise ValueError("some runs of this version failed in the twin: say why you accept it anyway")
        ev = {"kind": "AQUACROP_TWIN_EVIDENCE", "program_id": program_id, "version": version, "digest": v["digest"],
              "passed": True, "accepted_by": actor, "accepted_utc": datetime.now(timezone.utc).isoformat(),
              "twin_run_id": self.run_id, "context": self.context(program_id, version)["digest"],
              "runs": runs[-20:], "note": note,
              "meaning": "logic test on virtual modules with DEMO dynamics; not physical verification"}
        if v["digest"] != program_digest(parse(v["body"]).model_dump(mode="json")):
            raise ValueError("this version's body does not match its digest")
        path = feed.write_evidence(self.feed_dir, ev)
        self.hist.event("TWIN_EVIDENCE_WRITTEN", {"program": f"{program_id}@{version}", "path": str(path)}, actor=actor)
        return ev

    def _graph_view(self) -> dict | None:
        g = self.graph()
        if g is None:
            return None
        out = self.gwater.view(g, params_from(g, self.items.views()))
        # which graph and drawing these numbers belong to: the page draws them again when either changes
        out["revs"] = f"{self.blocks.topology()['rev']}/{self.blocks.diagram()['rev']}"
        return out

    def view(self) -> dict:
        with self.lock:
            e = self.engine.view()
            items = []
            for iid, it in self.items.views().items():
                st = self.engine.item_state(iid)
                ins = []
                for r in it.type.roles:
                    p = it.points.get(r.role)
                    s = self.twin.samples().get(p) if p else None
                    val = None
                    if s is not None and s.value is not None:
                        val = bool(s.value)
                    elif s is not None and s.raw_value is not None:
                        sc = it.body.scaling.get(r.role)
                        val = round(sc.apply(float(s.raw_value) / 1000.0), 3) if sc else f"{s.raw_value} {s.raw_unit}"
                    ins.append({"role": r.role, "label": r.label, "point": p, "direction": r.direction.value,
                                "value": val, "quality": s.quality.value if s else "NO CHANNEL",
                                "why": s.reason if s else "no channel", "unit": r.unit})
                beh = (it.body.simulation or it.type.simulation)
                items.append(st | {"icon": self.items.items[iid]["icon"], "type": self.items.items[iid]["type_id"],
                                   "behaviour": beh.behavior.value, "roles": ins,
                                   "fault": self.twin.s.faults.get(iid)})
            return {"runtime_id": self.runtime_id, "run_id": self.run_id, "running": self.running, "speed": self.speed,
                    "effective_speed": round(self.effective_speed, 1), "speeds": SPEEDS,
                    "sim_utc": self.clock.utc().isoformat(), "sim_s": round(self.clock.sim_s, 1),
                    "timezone": self.cfg.site.timezone, "snapshot_utc": self.snapshot_utc, "engine": e, "items": items,
                    "results": self.results[-50:], "model_error": self.model_error,
                    "alarms": [o.to_dict() for o in self.alarms.open.values()],
                    "graph_water": self._graph_view(),
                    "label": "DIGITAL TWIN: virtual modules and DEMO dynamics. A pass here is a logic test, not "
                             "physical verification."}

