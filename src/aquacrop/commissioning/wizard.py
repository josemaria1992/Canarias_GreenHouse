"""Hardware → Add module: prepare one new module on the controller's USB-to-RS485 adapter (0.5.0).

The installation buses stay connected and polled; only the new module is wired to the adapter, with its
own supply and a GND reference. The wizard is a persisted transaction (`commissioning_tx`), so cancel,
unplug, a browser refresh and a service restart all leave a record that says what happened:

  OPEN ─read─▶ READ ─identity─▶ IDENTIFIED ─reserve─▶ RESERVED ─commit─▶ WRITING ─▶ PREPARED ─detect─▶ DETECTED
    │            │                  │                    │                  │
    └────────────┴──────cancel──────┴────────────────────┘                  └─▶ UNCERTAIN (comm lost after a write)
                                                                            └─▶ FAILED (nothing changed, address released)

Rules, whatever the request:
* Reads and writes go only to the adapter's port, under its AquaCrop port lock, which the wizard holds
  while the transaction is open. A port that a configured installation bus uses cannot be chosen.
* `read` refuses unless exactly one module answers in the scanned range; the user must also confirm that
  only the new module is wired to the adapter, because two modules at one address can answer as one.
* The SKU, the marked name and the analog variant are the user's statement (the bus cannot tell a Relay (D)
  from an IO 8CH); they are recorded as such.
* The address must be 1..247, not configured on the destination bus, not reserved by another transaction
  (a partial unique index makes the reservation atomic) and not answering there now. No answer is not proof
  that an address is free; the preview says so.
* `commit` writes the same registers `hw setup` writes (mode registers, then the address register), FC06
  once each, to the module's current unit, never broadcast, never the UART register, through a bus that
  accepts only those registers. A write without a clear outcome makes the transaction UNCERTAIN: nothing
  is retried and the address stays reserved until `recheck` reads the module again.
* Nothing here switches an output, and nothing touches an installation bus except `detect`, which reads the
  new address once through that bus's own worker. Adding the module to the configuration is a separate,
  reviewed step: `detect` writes a configuration proposal; it does not change the running configuration.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..domain.model import new_id
from ..drivers import setup as hw
from ..ownership import OwnerLock, OwnershipError, lock_name, port_key
from .adapters import enumerate_adapters

OPEN_STATES = ("OPEN", "READ", "IDENTIFIED", "RESERVED")
HOLDS_ADDRESS = ("RESERVED", "WRITING", "UNCERTAIN", "PREPARED")
FIRST_SUGGESTED = 7          # 1..6 are the existing modules (owner answer, 2026-10-24)
CONFIRM_PHRASE = "ONLY THE NEW MODULE IS ON THE ADAPTER"
MOVE_STEPS = [
    "Switch off the new module's supply.",
    "Disconnect its A, B and GND from the USB adapter.",
    "Put the installation into a safe maintenance stop before touching the installation bus: no program "
    "running, outputs OFF. Wiring a live RS485 bus is not guaranteed to be electrically safe.",
    "Connect the module's A, B and GND to the destination bus port, in parallel with the modules already there "
    "(same A to A and B to B), and give it its supply.",
    "Press Detect: AquaCrop reads the new address once on that bus.",
]


class WizardRefused(Exception):
    pass


def _outcome_known(log: list[dict], writes_sent: int) -> bool:
    """After a stopped setup: is the module's address certainly unchanged? True when nothing was sent, when the
    last mode write was read back (the module answered at its old address), or when the address write left the
    module answering at the old address and not at the new one."""
    if writes_sent == 0:
        return True
    if not log:
        return False
    last = log[-1]
    if last.get("register") == f"0x{hw.ADDRESS_REGISTER:04X}":
        return bool(last.get("answers_at_old")) and last.get("readback_at_new") is None
    return last.get("readback") is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Wizard:
    def __init__(self, hist, cfg, *, state_dir: Path, lock_root: Path, probe_destination: Callable[[str, int], dict | None],
                 bus_factory=None, adapter_root: str | Path = "/", scan_last: int = 32, adapter_list=None):
        """`probe_destination(bus_id, unit)` reads one unit on an installation bus through its own worker and
        returns `setup.identify`'s dict, or None when nothing answers; it is the only installation-bus access."""
        self.h, self.cfg = hist, cfg
        self.state_dir, self.lock_root = Path(state_dir), Path(lock_root)
        self.probe_destination = probe_destination
        self.bus_factory = bus_factory            # tests: a pyserial-compatible client factory
        self.adapter_root, self.scan_last = adapter_root, scan_last
        self.adapter_list = adapter_list or (lambda: enumerate_adapters(self.cfg, root=self.adapter_root))
        self._locks: dict[str, OwnerLock] = {}     # tx_id -> the adapter's port lock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ persistence
    def _load(self, tx_id: str) -> dict:
        rows = self.h.query("SELECT tx_id, state, bus_id, address, actor, created_utc, updated_utc, json "
                            "FROM commissioning_tx WHERE tx_id=?", (tx_id,))
        if not rows:
            raise WizardRefused(f"no commissioning transaction {tx_id}")
        r = rows[0]
        return dict(r) | {"doc": json.loads(r["json"])}

    def _save(self, tx: dict, state: str, actor: str, step: str, **extra) -> dict:
        doc = tx["doc"]
        doc.setdefault("log", []).append({"utc": _now(), "actor": actor, "step": step, "state": state} | extra)
        address = doc.get("address") if state in HOLDS_ADDRESS + ("DETECTED",) else None
        row = (state, tx["bus_id"], address, _now(), json.dumps(doc, sort_keys=True), tx["tx_id"])
        try:
            self.h.submit(lambda c: c.execute("UPDATE commissioning_tx SET state=?, bus_id=?, address=?, updated_utc=?, "
                                              "json=? WHERE tx_id=?", row), wait=True)
        except Exception as e:
            if "UNIQUE" in str(e):
                raise WizardRefused(f"address {address} on {tx['bus_id']} is reserved by another module being prepared")
            raise
        self.h.event("COMMISSIONING_" + step, {"tx_id": tx["tx_id"], "state": state, "bus_id": tx["bus_id"],
                                                "address": doc.get("address")} | extra, actor=actor)
        return self.view(tx["tx_id"])

    def view(self, tx_id: str) -> dict:
        t = self._load(tx_id)
        d = t["doc"]
        return {"tx_id": t["tx_id"], "state": t["state"], "bus_id": t["bus_id"], "address": d.get("address"),
                "actor": t["actor"], "created_utc": t["created_utc"], "updated_utc": t["updated_utc"],
                "adapter": d.get("adapter"), "found": d.get("found"), "identity": d.get("identity"),
                "preview": d.get("preview"), "result": d.get("result"), "log": d.get("log", []),
                "holds_adapter": tx_id in self._locks, "move_steps": MOVE_STEPS if t["state"] == "PREPARED" else None,
                "recovery": self._recovery(t)}

    def list(self, limit: int = 20) -> list[dict]:
        rows = self.h.query("SELECT tx_id FROM commissioning_tx ORDER BY created_utc DESC LIMIT ?", (limit,))
        return [self.view(r["tx_id"]) for r in rows]

    @staticmethod
    def _recovery(t: dict) -> list[str] | None:
        if t["state"] == "UNCERTAIN":
            return ["Do not connect the module to the installation bus yet.",
                    "Leave it on the USB adapter, switch its supply off and on again, then press Re-check.",
                    "Re-check reads the old and the new address. The address stays reserved until the module's "
                    "address is known."]
        if t["state"] == "WRITING":
            return ["The service stopped during the write. Press Re-check with the module still on the adapter."]
        return None

    def startup(self) -> list[str]:
        """After a restart nothing holds an adapter. A write in progress becomes UNCERTAIN."""
        changed = []
        for r in self.h.query("SELECT tx_id FROM commissioning_tx WHERE state='WRITING'"):
            t = self._load(r["tx_id"])
            self._save(t, "UNCERTAIN", "system", "INTERRUPTED", reason="service restarted during the write")
            changed.append(r["tx_id"])
        return changed

    # ------------------------------------------------------------------ adapter
    def adapters(self) -> list[dict]:
        held = {self._load(t)["doc"]["adapter"]["path"] for t in self._locks}
        out = []
        for a in self.adapter_list():
            d = a.public()
            if a.path in held:
                d |= {"selectable": False, "why_not": "this wizard is using it", "owned_by_other": False}
            else:
                d["owned_by_other"] = self._held_elsewhere(a.path)
                if d["owned_by_other"]:
                    d |= {"selectable": False, "why_not": "another process holds this port"}
            out.append(d)
        return out

    def _held_elsewhere(self, path: str) -> bool:
        lk = OwnerLock(self.lock_root / lock_name("port", port_key(path)))
        try:
            lk.acquire()
        except OwnershipError:
            return True
        lk.release()
        return False

    def _bus(self, path: str, writes: frozenset = frozenset()):
        return hw._bus(path, 9600, 0.2, writes, client_factory=self.bus_factory)

    def _hold(self, tx_id: str, path: str) -> None:
        if tx_id in self._locks:
            return
        try:
            self._locks[tx_id] = OwnerLock(self.lock_root / lock_name("port", port_key(path))).acquire()
        except OwnershipError:
            raise WizardRefused(f"{path} is in use by another process or another module being prepared")

    def _release(self, tx_id: str) -> None:
        lk = self._locks.pop(tx_id, None)
        if lk:
            lk.release()

    # ------------------------------------------------------------------ steps
    def open(self, actor: str, adapter_id: str, bus_id: str) -> dict:
        with self._lock:
            try:
                dest = next(b for b in self.cfg.buses if b.bus_id == bus_id)
            except StopIteration:
                raise WizardRefused(f"{bus_id} is not an installation bus of this configuration")
            a = next((x for x in self.adapters() if adapter_id in (x["stable_id"], x["path"])), None)
            if a is None:
                raise WizardRefused("that adapter is not plugged into the controller now; refresh the list")
            if not a["selectable"]:
                raise WizardRefused(f"{a['path']}: {a['why_not']}")
            tx_id = new_id("ctx")
            now = _now()
            doc = {"adapter": {k: a[k] for k in ("path", "by_id", "manufacturer", "product", "serial", "vendor_id",
                                                 "product_id")}, "destination": {"bus_id": bus_id, "port": dest.port}}
            self.h.submit(lambda c: c.execute("INSERT INTO commissioning_tx(tx_id, state, bus_id, address, actor, "
                                              "created_utc, updated_utc, json) VALUES(?,?,?,?,?,?,?,?)",
                                              (tx_id, "OPEN", bus_id, None, actor, now, now, json.dumps(doc))), wait=True)
            self._hold(tx_id, a["path"])
            return self._save(self._load(tx_id), "OPEN", actor, "OPENED")

    def _tx(self, tx_id: str, *states: str) -> dict:
        t = self._load(tx_id)
        if states and t["state"] not in states:
            raise WizardRefused(f"this module is {t['state']}; that step is not possible now")
        return t

    def read(self, actor: str, tx_id: str, *, only_new_module: str, last: int | None = None,
             confirmed_how: str = "typed confirmation") -> dict:
        if only_new_module.strip() != CONFIRM_PHRASE:
            raise WizardRefused(f"type exactly: {CONFIRM_PHRASE}")
        last = last or self.scan_last
        with self._lock:
            t = self._tx(tx_id, "OPEN", "READ", "IDENTIFIED")
            self._hold(tx_id, t["doc"]["adapter"]["path"])
            bus = self._bus(t["doc"]["adapter"]["path"])
            try:
                found = hw.scan(bus, 1, last)
                sent = bus.stats.write_requests
            except Exception as e:
                return self._save(t, t["state"], actor, "READ_FAILED", reason=str(e))
            finally:
                bus.close()
            t["doc"]["found"] = found
            t["doc"]["scan"] = {"range": [1, last], "writes_sent": sent, "confirmation": CONFIRM_PHRASE,
                                "confirmed_how": confirmed_how}
            if len(found) != 1:
                why = ("no module answers on the adapter: check its supply, A/B/GND wiring and that it is at 9600 baud"
                       if not found else f"{len(found)} modules answer (addresses "
                       f"{', '.join(str(f['address']) for f in found)}): connect only the new module")
                return self._save(t, "OPEN", actor, "READ_REFUSED", reason=why)
            if found[0]["kind"] == "unknown":
                return self._save(t, "OPEN", actor, "READ_REFUSED", reason="the module answers but is not a supported model")
            return self._save(t, "READ", actor, "READ", kind=found[0]["kind"], at=found[0]["address"],
                              note="one responder does not prove one device: two modules at one address can answer as one")

    def identify(self, actor: str, tx_id: str, *, name: str, sku: str, label: str, ai_variant: str | None = None,
                 ai_range: str | None = None) -> dict:
        import re
        with self._lock:
            t = self._tx(tx_id, "READ", "IDENTIFIED", "RESERVED")      # a changed identity drops the reservation
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{0,47}", name or ""):
                raise WizardRefused("the name starts with a letter and uses letters, digits, _ or - (e.g. digital_C)")
            if any(d.device_uid == name for d in self.cfg.devices):
                raise WizardRefused(f"{name} is already a module of this installation")
            model = {v: k for k, v in hw.SKUS.items()}.get(sku)
            if model is None:
                raise WizardRefused(f"SKU {sku} is not a supported module (26517 Relay (D), 26244 IO 8CH, 25821 AI 8CH)")
            try:
                ident = hw.physical_identity(model, sku, label, name)
                ranges = hw.parse_ai_ranges([f"all={ai_range}"] if ai_range else [], ai_variant) if model == "ai" else {}
            except hw.SetupError as e:
                raise WizardRefused(str(e))
            if model == "ai" and not ranges:
                raise WizardRefused("an analog module needs its range (e.g. 4-20mA)")
            if model == "ai" and ai_variant is None:
                raise WizardRefused("state the analog version printed on the module (A = 0-5 V, B = 0-10 V)")
            want = "analog" if model == "ai" else "digital"
            if t["doc"]["found"][0]["kind"] != want:
                raise WizardRefused(f"the module on the adapter looks {t['doc']['found'][0]['kind']}, not {want}: "
                                    "check the label")
            t["doc"]["identity"] = ident | {"name": name, "model": hw.MODELS[model], "model_key": model,
                                            "ai_variant": ai_variant, "ai_ranges": {str(k): v for k, v in ranges.items()}}
            return self._save(t, "IDENTIFIED", actor, "IDENTIFIED", name=name, sku=sku)

    def _taken(self, bus_id: str, exclude_tx: str | None = None) -> dict[int, str]:
        taken = {d.address: f"configured: {d.device_uid}" for d in self.cfg.devices if d.bus_id == bus_id}
        for a in self.cfg.drive_adapters:
            if a.modbus is not None and a.modbus.bus_id == bus_id:
                taken[a.modbus.address] = f"drive {a.adapter_id}"
        for r in self.h.query("SELECT tx_id, address, state FROM commissioning_tx WHERE bus_id=? AND address IS NOT NULL "
                              "AND state IN ('RESERVED','WRITING','UNCERTAIN','PREPARED','DETECTED')", (bus_id,)):
            if r["tx_id"] != exclude_tx:
                taken[r["address"]] = f"{r['state'].lower()} by another module being prepared"
        return taken

    def suggest(self, tx_id: str, *, probe_limit: int = 5) -> dict:
        t = self._tx(tx_id, "READ", "IDENTIFIED", "RESERVED")
        taken = self._taken(t["bus_id"], tx_id)
        probed = []
        for a in range(FIRST_SUGGESTED, 248):
            if a in taken:
                continue
            if len(probed) >= probe_limit:
                break
            ans = self.probe_destination(t["bus_id"], a)
            probed.append({"address": a, "answers": ans is not None})
            if ans is None:
                return {"address": a, "taken": {str(k): v for k, v in sorted(taken.items())}, "probed": probed,
                        "note": "no module answered at this address on the destination bus just now; that is not proof "
                                "it is free (a module may be off or disconnected)"}
        raise WizardRefused("no free address found near the suggested range; enter one yourself")

    def reserve(self, actor: str, tx_id: str, address: int) -> dict:
        with self._lock:
            t = self._tx(tx_id, "IDENTIFIED", "RESERVED")
            if not isinstance(address, int) or not 1 <= address <= 247:
                raise WizardRefused("the address must be 1..247 (0 is broadcast and never used)")
            taken = self._taken(t["bus_id"], tx_id)
            if address in taken:
                raise WizardRefused(f"address {address} on {t['bus_id']} is {taken[address]}")
            ans = self.probe_destination(t["bus_id"], address)
            if ans is not None:
                raise WizardRefused(f"a module answers at address {address} on {t['bus_id']} now; choose another")
            cur = t["doc"]["found"][0]
            ident = t["doc"]["identity"]
            ranges = {int(k): v for k, v in ident["ai_ranges"].items()}
            try:
                plan = hw.plan_setup(t["doc"]["found"], ident["model_key"], address, ranges)
            except hw.SetupError as e:
                raise WizardRefused(str(e))
            writes = [f"address register 0x4000: {plan['current_address']} → {address}"] if address != cur["address"] else []
            writes = [f"mode register 0x{r:04X} = {v}" for r, v in plan["mode_writes"]] + writes
            t["doc"]["address"] = address
            t["doc"]["plan"] = {"current_address": plan["current_address"], "new_address": address,
                                "mode_writes": plan["mode_writes"]}
            t["doc"]["preview"] = {"port": t["doc"]["adapter"]["path"], "unit": plan["current_address"], "writes": writes,
                                   "destination": t["bus_id"], "probe_at_destination": "no answer (not proof it is free)",
                                   "never": "broadcast, the UART register, any installation bus, any output"}
            return self._save(t, "RESERVED", actor, "RESERVED", address=address)

    def commit(self, actor: str, tx_id: str, *, confirm: bool) -> dict:
        if not confirm:
            raise WizardRefused("confirm the change preview first")
        with self._lock:
            t = self._tx(tx_id, "RESERVED")
            path = t["doc"]["adapter"]["path"]
            self._hold(tx_id, path)
            plan = t["doc"]["plan"]
            # re-check just before transmission: the module and only it, at the same address and kind
            probe = self._bus(path)
            try:
                again = hw.scan(probe, 1, max(self.scan_last, plan["current_address"]))
            finally:
                probe.close()
            if len(again) != 1 or again[0]["address"] != plan["current_address"] or again[0]["kind"] != t["doc"]["found"][0]["kind"]:
                return self._save(t, "IDENTIFIED", actor, "COMMIT_REFUSED",
                                  reason="the adapter no longer shows the same single module; read it again")
            t["doc"]["before"] = again[0]
            self._save(t, "WRITING", actor, "WRITING")       # persisted before the first frame
            t = self._load(tx_id)
            cur = plan["current_address"]
            allowed = frozenset({(cur, r) for r, _ in plan["mode_writes"]} | {(cur, hw.ADDRESS_REGISTER)})
            bus = self._bus(path, allowed)
            plan_full = {"current_address": cur, "new_address": plan["new_address"],
                         "mode_writes": [tuple(x) for x in plan["mode_writes"]], "before": again[0]}
            try:
                result = hw.apply_setup(bus, plan_full)
                t["doc"]["result"] = result | {"writes_sent": bus.stats.write_requests}
                return self._save(t, "PREPARED", actor, "PREPARED", writes=result["writes"])
            except hw.SetupError as e:
                log = e.args[1] if len(e.args) > 1 else []
                t["doc"]["result"] = {"writes": log, "reason": str(e.args[0]), "writes_sent": bus.stats.write_requests}
                if _outcome_known(log, bus.stats.write_requests):
                    # the module still answers at its old address and says what it holds: nothing uncertain
                    return self._release_and(t, "FAILED", actor, "FAILED", reason=str(e.args[0]))
                return self._save(t, "UNCERTAIN", actor, "UNCERTAIN", reason=str(e.args[0]))
            except Exception as e:
                t["doc"]["result"] = {"reason": repr(e), "writes_sent": bus.stats.write_requests}
                state = "FAILED" if bus.stats.write_requests == 0 else "UNCERTAIN"
                if state == "FAILED":
                    return self._release_and(t, state, actor, "FAILED", reason=repr(e))
                return self._save(t, state, actor, "UNCERTAIN", reason=repr(e))
            finally:
                bus.close()

    def _release_and(self, t: dict, state: str, actor: str, step: str, **kw) -> dict:
        self._save(t, state, actor, step, **kw)
        self._release(t["tx_id"])
        return self.view(t["tx_id"])

    def recheck(self, actor: str, tx_id: str) -> dict:
        """UNCERTAIN/WRITING: read the module on the adapter again (no write) to learn its address."""
        with self._lock:
            t = self._tx(tx_id, "UNCERTAIN", "WRITING")
            path = t["doc"]["adapter"]["path"]
            self._hold(tx_id, path)
            plan = t["doc"]["plan"]
            bus = self._bus(path)
            try:
                at_new = hw.read_one(bus, plan["new_address"], hw.ADDRESS_REGISTER)
                at_old = hw.read_one(bus, plan["current_address"], hw.ADDRESS_REGISTER)
                after = hw.identify(bus, plan["new_address"]) if at_new is not None else None
            finally:
                bus.close()
            if at_new == plan["new_address"] and at_old is None:
                modes_ok = after and after.get("modes") is not None and all(
                    after["modes"][r - hw.MODE_REGISTER_START] == v for r, v in plan["mode_writes"])
                if modes_ok:
                    t["doc"]["result"] = (t["doc"].get("result") or {}) | {"after": after, "rechecked": True}
                    return self._save(t, "PREPARED", actor, "RECHECKED", found="new address only")
                return self._save(t, "UNCERTAIN", actor, "RECHECKED", found="new address, modes not as planned")
            if at_old is not None and at_new is None and plan["current_address"] != plan["new_address"]:
                return self._release_and(t, "FAILED", actor, "RECHECKED", found="old address only: nothing applied")
            return self._save(t, "UNCERTAIN", actor, "RECHECKED",
                              found=f"new answers: {at_new is not None}, old answers: {at_old is not None}")

    def cancel(self, actor: str, tx_id: str, reason: str) -> dict:
        with self._lock:
            t = self._load(tx_id)
            if t["state"] in OPEN_STATES:
                return self._release_and(t, "CANCELLED", actor, "CANCELLED", reason=reason)
            if t["state"] == "PREPARED":
                # the module now has that address: keep it reserved, free the adapter
                self._release(tx_id)
                return self._save(t, "PREPARED", actor, "ADAPTER_RELEASED", reason=reason,
                                  note="the module keeps its new address; the address stays reserved")
            raise WizardRefused(f"a {t['state']} module cannot be cancelled; " +
                                ("use Re-check" if t["state"] in ("UNCERTAIN", "WRITING") else "it is finished"))

    def detect(self, actor: str, tx_id: str) -> dict:
        """Read the prepared module once at its new address on the destination bus, and write a configuration
        proposal with it added. The running configuration is not changed."""
        with self._lock:
            t = self._tx(tx_id, "PREPARED", "DETECTED")
            self._release(tx_id)
            addr = t["doc"]["address"]
            got = self.probe_destination(t["bus_id"], addr)
            ident = t["doc"]["identity"]
            want = "analog" if ident["model_key"] == "ai" else "digital"
            if got is None:
                return self._save(t, "PREPARED", actor, "NOT_DETECTED",
                                  reason=f"nothing answers at {addr} on {t['bus_id']}: check its wiring and supply")
            if got.get("kind") != want:
                return self._save(t, "PREPARED", actor, "NOT_DETECTED",
                                  reason=f"address {addr} answers as {got.get('kind')}, not {want}: another device?")
            proposal = self._proposal(t, got)
            t["doc"]["detected"] = got
            t["doc"]["proposal"] = proposal
            return self._save(t, "DETECTED", actor, "DETECTED", proposal=proposal["path"])

    def _proposal(self, t: dict, got: dict) -> dict:
        import yaml
        ident = t["doc"]["identity"]
        dev = {"device_uid": ident["name"], "bus_id": t["bus_id"], "address": t["doc"]["address"],
               "model": ident["model"], "sku": ident["sku"], "label": f"{ident['name']} (SKU {ident['sku']}, from its label)",
               "firmware_evidence": f"version register {got.get('version_register')} (dashboard wizard {t['tx_id']})"}
        if ident["model_key"] == "ai":
            dev["hardware_variant"] = "STANDARD" if ident["ai_variant"] == "A" else "B"
            dev["ai"] = {int(k): {"mode": str(hw.AI_RANGES[v])} for k, v in ident["ai_ranges"].items()}
        raw = self.cfg.model_dump(mode="json", exclude_none=True)
        raw["devices"] = [*raw["devices"], dev]
        raw["config_version"] = f"{self.cfg.config_version}+{ident['name']}"
        from ..config.schema import InstallationConfig
        InstallationConfig.model_validate(raw)                 # the proposal must load
        out = self.state_dir / "proposals" / f"{self.cfg.config_id}-add-{ident['name']}.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        head = (f"# Proposed configuration: {self.cfg.config_version} plus {ident['name']} at address "
                f"{t['doc']['address']} on {t['bus_id']}\n# made by the dashboard wizard ({t['tx_id']}). Its channels "
                "start unassigned and unverified. Review, then install it as described in the upgrade guide.\n")
        out.write_text(head + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return {"path": str(out), "device": dev, "config_version": raw["config_version"]}
