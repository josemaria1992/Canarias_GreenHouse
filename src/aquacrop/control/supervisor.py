"""Process supervisor: owns the process controllers, the arbiter, stock readiness and mixing readiness
(brief §3-§6; ADR-0014; docs/model/cause_and_effect.md).

The supervisor is the only path from a process to equipment. `issue()` checks ownership in the
arbiter, then routes the command:

* a signal bound to a DO point     -> the audited CommandService (origin CONTROLLER, reason names the
                                      process and the recipe version);
* a signal bound to a drive adapter -> an audited SIMULATED adapter command (intent, write, outcome);
* an unbound signal                 -> refused; a process can never act on equipment it cannot address.

Global trips are applied before any process ticks: an open safety chain faults every process that has
equipment in use. Processes tick in a fixed order so a run is reproducible.

Stock readiness is an operator acknowledgement ("stock A prepared") that is recorded, persisted and
invalidated automatically by a stock fill, by a level rise the acknowledgement did not cover, or by
the operator. Mixing readiness is SIMULATED settling only (time since the last observed addition);
on any physical profile it is NOT_VERIFIED because no agitator or verified mixing method exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Callable, Protocol

from ..domain.model import ControllerState, Priority, utcnow
from .process_base import FLOW_ZERO_L_MIN, Arbiter, Ctx, Process
from .processes import Acid, Irrigation, Refill, Return, StockFill

if TYPE_CHECKING:
    from ..twin.installation_params import InstallationFacts
    from .alarms import AlarmEngine

STOCK_LEVEL_RISE_INVALIDATES_L = 20.0   # software default: an unexplained rise means water was added

# Additions whose mixing a measurement must wait for (SIMULATED settling windows):
#   EC           staged A:B steps during a refill: wait after the last nutrient addition (water may still
#                be running: it only dilutes, so dosing converges from below and never overshoots)
#   EC_VALIDATE  final EC check: wait after the last nutrient or mains-water addition
#   PH           acid dosing: wait after any acid, nutrient or mains-water addition
# Drainage return is treated as an operating disturbance, not an addition (synthetic-fixture limitation).
ADDITION_FLOWS = {
    "EC": ("flow.TP_A", "flow.TP_B"),
    "EC_VALIDATE": ("flow.TP_A", "flow.TP_B", "flow.FILL_MAIN"),
    "PH": ("flow.TP_A", "flow.TP_B", "flow.TP_ACID", "flow.FILL_MAIN"),
}

# Cause-and-effect: which observed conditions block (re)starts of which processes. The processes also
# check these conditions themselves while running; this table is what the documentation renders.
START_INTERLOCKS: list[dict] = [
    {"signal": "safety.chain_ok", "when": False, "blocks": "ALL", "effect": "every process stops; no starts"},
    {"signal": "drive.MAIN.fault", "when": True, "blocks": ["IRRIGATION"], "effect": "irrigation faults/stays stopped"},
    {"signal": "tank.MAIN.high_high", "when": True, "blocks": ["REFILL", "RETURN", "ACID"],
     "effect": "no inflow to the main tank"},
    {"signal": "tank.MAIN.low_low", "when": True, "blocks": ["IRRIGATION", "ACID"],
     "effect": "no irrigation (dry run) and no dosing"},
    {"signal": "tank.SUMP.low", "when": True, "blocks": ["RETURN"], "effect": "no return pump start (dry run)"},
]


class ReadinessBackend(Protocol):
    def load_stock_readiness(self) -> list[dict]: ...

    def save_stock_readiness(self, entry: dict) -> None: ...


class MemoryReadinessBackend:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def load_stock_readiness(self) -> list[dict]:
        return list(self.entries)

    def save_stock_readiness(self, entry: dict) -> None:
        self.entries.append(dict(entry))


@dataclass
class StockReadiness:
    stock: str
    status: str = "NOT_PREPARED"        # NOT_PREPARED / PREPARED / INVALIDATED
    actor: str | None = None
    utc: str | None = None
    sim_s: float | None = None
    level_at_ack_L: float | None = None
    note: str = ""
    reason: str = "no preparation has been acknowledged"
    seq: int = 0

    def view(self) -> dict:
        return asdict(self)


class Supervisor:
    def __init__(self, facts: "InstallationFacts", *, alarms: "AlarmEngine",
                 emit: Callable[[str, dict, str | None], None],
                 route: Callable[[Process, str, bool, str, Priority], bool],
                 start_permitted: Callable[[], tuple[bool, str | None]],
                 source_ok: Callable[[], str | None],
                 simulated: bool = True,
                 readiness: ReadinessBackend | None = None):
        self.facts = facts
        self.alarms = alarms
        self._emit = emit
        self._route = route
        self.start_permitted = start_permitted
        self.source_ok = source_ok
        self.simulated = simulated
        self.arbiter = Arbiter()
        self.readiness_backend = readiness or MemoryReadinessBackend()
        self.stocks: dict[str, StockReadiness] = {s: StockReadiness(s) for s in ("A", "B", "ACID")}
        for e in sorted(self.readiness_backend.load_stock_readiness(), key=lambda e: e.get("seq", 0)):
            if e.get("stock") in self.stocks:
                self.stocks[e["stock"]] = StockReadiness(**{k: e[k] for k in StockReadiness.__dataclass_fields__
                                                            if k in e})
        self._seq = max((s.seq for s in self.stocks.values()), default=0)
        self.last_addition_s: dict[str, float | None] = {k: None for k in ADDITION_FLOWS}
        self.addition_unobservable: dict[str, str | None] = {k: None for k in ADDITION_FLOWS}
        self.processes: list[Process] = [Irrigation(self), Return(self), Refill(self), Acid(self)]
        self.processes += [StockFill(self, s) for s in ("A", "B", "ACID")]
        self.by_id = {p.pid: p for p in self.processes}

    # ------------------------------------------------------------------ plumbing used by processes
    def emit(self, kind: str, payload: dict, actor: str | None = "controller") -> None:
        self._emit(kind, payload, actor)

    def issue(self, proc: Process, signal: str, on: bool, reason: str, priority: Priority) -> bool:
        owner = self.arbiter.owner.get(signal)
        if owner is not None and owner != proc.pid:
            self.emit("PROCESS_COMMAND_REFUSED", {"process": proc.pid, "signal": signal, "desired": on,
                                                  "reason": f"owned by {owner}"}, "controller")
            return False
        if on:
            why = self.arbiter.acquire(proc.pid, [signal])
            if why:
                self.emit("PROCESS_COMMAND_REFUSED", {"process": proc.pid, "signal": signal, "desired": on,
                                                      "reason": why}, "controller")
                return False
        return self._route(proc, signal, on, f"{proc.pid}: {reason} [recipe {proc.recipe_ref}]", priority)

    def _own_alarms(self, pid: str) -> list[str]:
        out = []
        for o in self.alarms.open.values():
            if o.target == f"process.{pid}" or o.target.startswith(f"process.{pid}."):
                if o.reset_required or (o.active and o.severity == "CRITICAL"):
                    out.append(f"{o.rule_id} ({'active' if o.active else 'awaiting reset'})")
        return out

    def global_blockers(self, ctx: Ctx) -> list[str]:
        out = []
        why = self.source_ok()
        if why:
            out.append(why)
        ok, why = self.start_permitted()
        if not ok:
            out.append(f"new starts inhibited: {why}")
        chain = ctx.obs.get("safety.chain_ok")
        if not ctx.obs.bound("safety.chain_ok"):
            out.append("safety chain feedback is not bound in this configuration")
        elif chain is None:
            out.append(f"safety chain feedback unusable ({ctx.obs.why('safety.chain_ok')})")
        elif not chain:
            out.append("safety chain open")
        return out

    def interlocks_for(self, pid: str, ctx: Ctx) -> list[str]:
        out = []
        for il in START_INTERLOCKS[1:]:
            if il["blocks"] != "ALL" and pid not in il["blocks"]:
                continue
            if ctx.obs.bound(il["signal"]) and ctx.obs.get(il["signal"]) is il["when"]:
                out.append(f"{il['signal']} {'active' if il['when'] else 'inactive'}: {il['effect']}")
        return out

    def start_blockers(self, proc: Process, ctx: Ctx, auto: bool) -> list[str]:
        out = self.global_blockers(ctx)
        own = self._own_alarms(proc.pid)
        if own:
            out.append(f"unresolved {proc.title} alarms {own}: acknowledge and reset them first")
        if ctx.recipe is None:
            out.append("no active recipe")
        return out + self.interlocks_for(proc.pid, ctx)

    # ------------------------------------------------------------------ stock readiness
    def _save_stock(self, st: StockReadiness, kind: str, actor: str | None) -> None:
        self._seq += 1
        st.seq = self._seq
        self.readiness_backend.save_stock_readiness(st.view())
        self.emit(kind, st.view(), actor)

    def stock_ready(self, stock: str, level: float | None) -> tuple[bool, str]:
        st = self.stocks[stock]
        if st.status != "PREPARED":
            return False, f"stock {stock} {st.status}: {st.reason}"
        if level is not None and st.level_at_ack_L is not None \
                and level > st.level_at_ack_L + STOCK_LEVEL_RISE_INVALIDATES_L:
            self.invalidate_stock(stock, f"level rose from {st.level_at_ack_L:.0f} L to {level:.0f} L after the "
                                         "acknowledgement (water added?)")
            return False, f"stock {stock} INVALIDATED: {self.stocks[stock].reason}"
        return True, "prepared (acknowledged)"

    def invalidate_stock(self, stock: str, reason: str, actor: str | None = "controller") -> None:
        st = self.stocks[stock]
        if st.status == "INVALIDATED" and st.reason == reason:
            return
        self.stocks[stock] = StockReadiness(stock, "INVALIDATED", actor, utcnow().isoformat(), st.sim_s, None, "",
                                            reason)
        self._save_stock(self.stocks[stock], "STOCK_READINESS_INVALIDATED", actor)

    def acknowledge_stock(self, stock: str, actor: str, note: str, ctx: Ctx) -> tuple[bool, str]:
        if stock not in self.stocks:
            return False, f"unknown stock {stock}"
        fill = self.by_id.get(f"STOCK_FILL_{stock}")
        if fill is not None and (fill.phase == "FILLING" or fill.stopping):
            return False, f"stock {stock} is being filled; acknowledge preparation after the fill"
        if any(f"stock.{stock}" in self.arbiter.held_by(p.pid) for p in self.processes):
            return False, f"stock {stock} is in use by a transfer"
        lv = ctx.obs.num(f"tank.{stock}.level")
        if lv is None:
            return False, f"stock {stock} level unavailable ({ctx.obs.why(f'tank.{stock}.level')}); the " \
                          "acknowledgement records the level it covers"
        self.stocks[stock] = StockReadiness(stock, "PREPARED", actor, utcnow().isoformat(), ctx.now, lv, note,
                                            "acknowledged by the operator")
        self._save_stock(self.stocks[stock], "STOCK_READINESS_ACKNOWLEDGED", actor)
        return True, f"stock {stock} acknowledged prepared at {lv:.0f} L"

    # ------------------------------------------------------------------ mixing readiness
    def _track_additions(self, ctx: Ctx) -> None:
        for kind, flows in ADDITION_FLOWS.items():
            unobs = None
            for f in flows:
                if not ctx.obs.bound(f):
                    continue
                q = ctx.obs.num(f)
                if q is None:
                    unobs = f"{f} unusable ({ctx.obs.why(f)})"
                elif q > FLOW_ZERO_L_MIN:
                    self.last_addition_s[kind] = ctx.now
            self.addition_unobservable[kind] = unobs

    def mixing_ready(self, kind: str, settle_s: float, ctx: Ctx) -> tuple[bool, str]:
        if not self.simulated:
            return False, "mixing readiness NOT_VERIFIED: no agitator or verified mixing method (commissioning item)"
        if self.addition_unobservable[kind]:
            return False, f"additions cannot be observed: {self.addition_unobservable[kind]}"
        last = self.last_addition_s[kind]
        if last is None:
            return True, "no addition observed in this run (SIMULATED settling)"
        waited = ctx.now - last
        if waited + 1e-9 >= settle_s:
            return True, f"SIMULATED settling: {waited:.0f} s since the last addition"
        return False, f"SIMULATED settling: {waited:.0f} of {settle_s:g} s since the last addition"

    # ------------------------------------------------------------------ the scan
    def _global_trips(self, ctx: Ctx) -> None:
        """An open safety chain locks out every process in AUTO or MANUAL: restoring the chain never
        restarts anything; each process needs acknowledge, reset and a deliberate start."""
        if ctx.obs.get("safety.chain_ok") is False:
            for p in self.processes:
                if p.state in (ControllerState.AUTO, ControllerState.MANUAL):
                    p.now, p._ctx = ctx.now, ctx
                    p.stopping = False
                    p.fault("SAFETY_CHAIN_OPEN", "safety chain reported open: process outputs released")

    def tick(self, ctx: Ctx) -> None:
        self._track_additions(ctx)
        self._global_trips(ctx)
        for p in self.processes:
            p.tick(ctx)

    # ------------------------------------------------------------------ operator actions
    def process(self, pid: str) -> Process:
        try:
            return self.by_id[pid]
        except KeyError:
            raise KeyError(f"unknown process {pid}; known: {sorted(self.by_id)}") from None

    def request(self, pid: str, target: ControllerState, actor: str, ctx: Ctx) -> tuple[bool, str]:
        p = self.process(pid)
        p.now = ctx.now
        ok, msg = p.request(target, actor, ctx)
        if not ok:
            self.emit("PROCESS_REQUEST_REJECTED", {"process": pid, "target": target.value, "reason": msg}, actor)
        return ok, msg

    def manual(self, pid: str, op: dict, actor: str, ctx: Ctx) -> tuple[bool, str]:
        p = self.process(pid)
        p.now = ctx.now
        ok, msg = p.manual_request(op, actor, ctx)
        if not ok:
            self.emit("PROCESS_MANUAL_OP_REJECTED", {"process": pid, "op": op, "reason": msg}, actor)
        return ok, msg

    def reset(self, pid: str, actor: str, ctx: Ctx) -> tuple[bool, str]:
        p = self.process(pid)
        p.now = ctx.now
        ok, msg = p.reset(actor, ctx)
        if not ok:
            self.emit("PROCESS_RESET_REJECTED", {"process": pid, "reason": msg}, actor)
        return ok, msg

    def stop_all(self, actor: str, ctx: Ctx, reason: str = "stop all") -> list[dict]:
        out = []
        for p in self.processes:
            p.now = ctx.now
            if p.state == ControllerState.FAULT_LOCKOUT:
                out.append({"process": p.pid, "ok": True, "detail": "in FAULT_LOCKOUT (already stopping/stopped)"})
                continue
            ok, msg = p.request(ControllerState.STOPPED, actor, ctx)
            out.append({"process": p.pid, "ok": ok, "detail": msg})
        self.emit("PROCESS_STOP_ALL", {"reason": reason, "results": out}, actor)
        return out

    def all_idle(self) -> bool:
        return all(p.state in (ControllerState.STOPPED, ControllerState.FAULT_LOCKOUT) and not p.stopping
                   and not p.on and p.phase == "IDLE" for p in self.processes)

    def fingerprint(self) -> tuple:
        return tuple((p.pid, p.state.value, p.phase, round(p.phase_since, 6), tuple(sorted(p.on)), p.stopping,
                      p.fault_reason) for p in self.processes) + (tuple(sorted(self.arbiter.owner.items())),
                                                                   tuple((s, r.status) for s, r in self.stocks.items()))

    def snapshot(self) -> dict:
        return {"processes": {p.pid: p.view() for p in self.processes},
                "order": [p.pid for p in self.processes],
                "arbiter": dict(sorted(self.arbiter.owner.items())),
                "stocks": {s: r.view() for s, r in self.stocks.items()},
                "mixing": {k: {"last_addition_sim_s": self.last_addition_s[k],
                               "unobservable": self.addition_unobservable[k],
                               "method": "SIMULATED settling window" if self.simulated else "NOT_VERIFIED"}
                           for k in ADDITION_FLOWS},
                "interlocks": START_INTERLOCKS}
