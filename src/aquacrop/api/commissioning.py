"""HTTP routes of Add Module (the USB commissioning wizard), module requests and Discovery (0.5.0; 0.7.1).

Only a hardware profile has the wizard: on the simulation it answers `available: false` or 409. Every step of the
wizard needs MODULE_SETUP: administrators, and operators with the commissioning flag (owner decision 26 October
2026, ADR-0023 amended), checked on the server for every request.

Module requests (0.7.1, MOD-03) are a tracked workflow on this bench, not a message: they are stored in this
installation's database and shown on Add Module to whoever may prepare modules, and to the person who asked. Nothing
is emailed or sent anywhere. The states are REQUESTED -> PREPARING (a wizard run started from it) -> AWAITING_APPLICATION
(the module was detected on its bus and a configuration proposal was written; an administrator applies it during
maintenance) -> ADDED, which is shown only when the running configuration really contains the module. REJECTED and
CLOSED end a request without adding anything. Serial work runs in a worker thread.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from ..commissioning.wizard import CONFIRM_PHRASE, MOVE_STEPS, WizardRefused
from ..security.auth import Perm, Principal


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenIn(_B):
    adapter: str = Field(max_length=200)
    bus_id: str = Field(max_length=48)
    read: bool = True          # Start reads the module at once (owner, 2026-10-24: no typed confirmation)
    request_id: int | None = Field(default=None, ge=1)   # 0.7.1: prepared from this module request


class ReadIn(_B):
    confirmation: str = Field(max_length=80)


class IdentityIn(_B):
    name: str = Field(max_length=48)
    sku: str = Field(max_length=8)
    label: str = Field(max_length=48)
    ai_variant: str | None = Field(default=None, pattern="^(A|B)$")
    ai_range: str | None = Field(default=None, max_length=8)


class ReserveIn(_B):
    address: int


class CommitIn(_B):
    confirm: bool


class CancelIn(_B):
    reason: str = Field(default="cancelled in the dashboard", max_length=200)


class SweepIn(_B):
    bus_id: str = Field(max_length=48)


class ModuleRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(max_length=64)
    purpose: str = Field(min_length=3, max_length=200)
    location: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


class ModuleRequestCloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: int = Field(ge=1)
    outcome: str = Field(min_length=3, max_length=200)
    decision: str = Field(default="CLOSED", pattern="^(CLOSED|REJECTED)$")


REQUEST_NEXT = {
    "REQUESTED": "Waiting for an administrator or an operator with commissioning to prepare the module on Add Module.",
    "PREPARING": "Being prepared with the USB adapter on the controller (address and modes).",
    "AWAITING_APPLICATION": ("Prepared and found on its bus. An administrator applies the configuration proposal during "
                             "maintenance; its channels then appear unassigned and unverified."),
    "ADDED": "In the running configuration. Its channels are unassigned and unverified until someone assigns them.",
    "REJECTED": "Not added.", "CLOSED": "Closed without adding a module.",
}


def register(app, require, wizard, svc) -> None:
    def need():
        if wizard is None:
            raise HTTPException(409, "module commissioning exists on a hardware profile only; the simulation has "
                                     "no USB adapter")
        return wizard

    async def step(fn, *a, **kw):
        try:
            return await run_in_threadpool(fn, *a, **kw)
        except WizardRefused as e:
            raise HTTPException(409, str(e))

    @app.get("/api/commissioning")
    async def overview(p: Principal = Depends(require(Perm.OPS_READ))):
        if wizard is None:
            return {"available": False, "reason": "the simulation has no USB adapter; the wizard runs on the controller"}
        return {"available": True, "adapters": await run_in_threadpool(wizard.adapters),
                "buses": [{"bus_id": b.bus_id, "port": b.port,
                           "modules": [{"device_uid": d.device_uid, "address": d.address, "model": d.model.value}
                                       for d in wizard.cfg.devices if d.bus_id == b.bus_id]}
                          for b in wizard.cfg.buses if b.port],
                "transactions": await run_in_threadpool(wizard.list),
                "confirmation_phrase": CONFIRM_PHRASE, "move_steps": MOVE_STEPS,
                "where": ("Plug the USB-to-RS485 adapter into a USB port of the controller (the CM4), not into this PC: "
                          "the dashboard is a web page and cannot see adapters on the computer showing it.")}

    def link_request(request_id: int | None, tx: dict, actor: str) -> None:
        if request_id is None:
            return
        cur = hist.query("SELECT status FROM module_requests WHERE request_id=?", (request_id,))
        if cur and cur[0]["status"] in ("REQUESTED", "PREPARING"):
            _set(request_id, "PREPARING", actor, tx_id=tx["tx_id"])

    @app.get("/api/commissioning/{tx_id}")
    async def tx_view(tx_id: str, p: Principal = Depends(require(Perm.OPS_READ))):
        return await step(need().view, tx_id)

    @app.post("/api/commissioning/open")
    async def tx_open(body: OpenIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        v = await step(need().open, p.username, body.adapter, body.bus_id)
        link_request(body.request_id, v, p.username)
        if not body.read:
            return v
        # Pressing Start is the user's statement that only the new module is wired to the adapter (owner decision
        # 2026-10-24). The server still refuses when more or fewer than one module answers.
        return await step(need().read, p.username, v["tx_id"], only_new_module=CONFIRM_PHRASE,
                          confirmed_how="the Start button: the user wired only the new module to the adapter")

    @app.post("/api/commissioning/{tx_id}/read")
    async def tx_read(tx_id: str, body: ReadIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().read, p.username, tx_id, only_new_module=body.confirmation)

    @app.post("/api/commissioning/{tx_id}/identity")
    async def tx_identity(tx_id: str, body: IdentityIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().identify, p.username, tx_id, name=body.name, sku=body.sku, label=body.label,
                          ai_variant=body.ai_variant, ai_range=body.ai_range)

    @app.get("/api/commissioning/{tx_id}/suggest")
    async def tx_suggest(tx_id: str, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().suggest, tx_id)

    @app.post("/api/commissioning/{tx_id}/reserve")
    async def tx_reserve(tx_id: str, body: ReserveIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().reserve, p.username, tx_id, body.address)

    @app.post("/api/commissioning/{tx_id}/commit")
    async def tx_commit(tx_id: str, body: CommitIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().commit, p.username, tx_id, confirm=body.confirm)

    @app.post("/api/commissioning/{tx_id}/recheck")
    async def tx_recheck(tx_id: str, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().recheck, p.username, tx_id)

    @app.post("/api/commissioning/{tx_id}/cancel")
    async def tx_cancel(tx_id: str, body: CancelIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().cancel, p.username, tx_id, body.reason)

    @app.post("/api/commissioning/{tx_id}/detect")
    async def tx_detect(tx_id: str, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        return await step(need().detect, p.username, tx_id)

    # 0.7.1 (MOD-03): module requests as a tracked workflow on this bench; nothing here changes the configuration
    hist = app.state.hist

    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _set(rid: int, status: str, actor: str, **cols) -> None:
        sets = ", ".join(f"{k}=?" for k in ("status", "status_utc", "handler", *cols))
        vals = (status, _now(), actor, *cols.values(), rid)
        hist.submit(lambda c: c.execute(f"UPDATE module_requests SET {sets} WHERE request_id=?", vals), wait=True)
        hist.event("MODULE_REQUEST_" + status, {"request_id": rid, **cols}, actor=actor)

    def _sync(r: dict) -> dict:
        """Follow a request's wizard run, and say ADDED only when the running configuration has the module."""
        if r["status"] in ("REJECTED", "CLOSED", "ADDED"):
            return r
        uid = r.get("device_uid")
        if uid and any(d.device_uid == uid for d in app.state.cfg.devices):
            _set(r["request_id"], "ADDED", "system", device_uid=uid)
            return r | {"status": "ADDED"}
        if r.get("tx_id") and wizard is not None:
            try:
                t = wizard.view(r["tx_id"])
            except WizardRefused:
                return r
            name = (t.get("identity") or {}).get("name")
            if t["state"] == "DETECTED" and r["status"] != "AWAITING_APPLICATION":
                _set(r["request_id"], "AWAITING_APPLICATION", "system", device_uid=name)
                return r | {"status": "AWAITING_APPLICATION", "device_uid": name}
            if t["state"] in ("CANCELLED", "FAILED") and r["status"] == "PREPARING":
                _set(r["request_id"], "REQUESTED", "system", reason=f"preparation {t['state'].lower()}; the request is open again")
                return r | {"status": "REQUESTED"}
        return r

    @app.get("/api/modules/requests")
    def module_requests(p: Principal = Depends(require(Perm.OPS_READ))):
        handler = p.can(Perm.MODULE_SETUP)
        rows = [dict(r) for r in hist.query("SELECT * FROM module_requests ORDER BY request_id DESC LIMIT 100")]
        if not handler:
            rows = [r for r in rows if r["requester"] == p.username]
        out = []
        for r in rows:
            r = _sync(r)
            out.append({k: r.get(k) for k in ("request_id", "utc", "requester", "model", "purpose", "location", "note",
                                               "status", "status_utc", "handler", "reason", "tx_id", "device_uid")}
                       | {"by": r["requester"], "closed": r["status"] in ("REJECTED", "CLOSED", "ADDED"),
                          "next": REQUEST_NEXT.get(r["status"], "")})
        return {"requests": out, "can_handle": handler,
                "where": ("Requests are recorded on this bench and shown here to administrators and operators with "
                          "commissioning, who prepare the module; you see the state of your own requests here. They are "
                          "not emailed or sent anywhere else.")}

    @app.post("/api/modules/requests")
    def module_request(body: ModuleRequestIn, p: Principal = Depends(require(Perm.MODULE_REGISTER))):
        row = (_now(), p.username, body.model, body.purpose, body.location, body.note, "REQUESTED", _now())

        def tx(c):
            return c.execute("INSERT INTO module_requests(utc, requester, model, purpose, location, note, status, "
                             "status_utc) VALUES(?,?,?,?,?,?,?,?)", row).lastrowid
        rid = hist.submit(tx, wait=True)
        hist.event("MODULE_REGISTRATION_REQUESTED", body.model_dump() | {"request_id": rid}, actor=p.username)
        return {"ok": True, "request_id": rid, "status": "REQUESTED",
                "next": ("Recorded on this bench as request " + str(rid) + ". Administrators and operators with "
                         "commissioning see it on Add Module and prepare the module with the USB adapter on the "
                         "controller; you can follow its state here. Nothing changed yet, and nothing was emailed.")}

    @app.post("/api/modules/requests/close")
    def module_request_close(body: ModuleRequestCloseIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        """Reject or close a request. This never adds a module: ADDED comes only from the running configuration."""
        cur = hist.query("SELECT status FROM module_requests WHERE request_id=?", (body.request_id,))
        if not cur:
            raise HTTPException(404, "no such module request")
        if cur[0]["status"] in ("ADDED", "REJECTED", "CLOSED"):
            raise HTTPException(409, f"request {body.request_id} is already {cur[0]['status'].lower()}")
        _set(body.request_id, body.decision, p.username, reason=body.outcome)
        hist.event("MODULE_REGISTRATION_CLOSED", body.model_dump(), actor=p.username)
        return {"ok": True, "status": body.decision}

    @app.get("/api/discovery")
    def discovery(p: Principal = Depends(require(Perm.OPS_READ))):
        if svc is None or not hasattr(svc, "discovery"):
            return {"available": False, "reason": "discovery reads real buses; the simulation's modules are virtual"}
        return {"available": True, "buses": svc.discovery.view(lambda uid: svc.backend.health(uid).online)}

    @app.post("/api/discovery/sweep")
    def discovery_sweep(body: SweepIn, p: Principal = Depends(require(Perm.MODULE_REGISTER))):
        if svc is None or not hasattr(svc, "discovery"):
            raise HTTPException(409, "Discovery works on the real RS485 buses only, not in the simulation")
        try:
            svc.discovery.request_full(body.bus_id, p.username)
        except KeyError:
            raise HTTPException(409, f"{body.bus_id} is not a serial bus of this configuration")
        svc.hist.event("DISCOVERY_SWEEP_REQUESTED", {"bus_id": body.bus_id}, actor=p.username)
        return {"requested": body.bus_id, "note": "one address per poll, only while the poll has time to spare"}
