"""HTTP routes of Hardware → Add module (the USB commissioning wizard) and Hardware → Discovery (0.5.0).

Only a hardware profile has them: on the simulation every route answers `available: false` or 409. Reading
the adapter list, a transaction and the discovery view needs READ; every step of the wizard and a full
discovery sweep need MODULE_SETUP (an operator or administrator with the commissioning flag). Serial work
runs in a worker thread so the event loop keeps serving the dashboard.
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

    @app.get("/api/commissioning/{tx_id}")
    async def tx_view(tx_id: str, p: Principal = Depends(require(Perm.OPS_READ))):
        return await step(need().view, tx_id)

    @app.post("/api/commissioning/open")
    async def tx_open(body: OpenIn, p: Principal = Depends(require(Perm.MODULE_SETUP))):
        v = await step(need().open, p.username, body.adapter, body.bus_id)
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

    @app.get("/api/discovery")
    def discovery(p: Principal = Depends(require(Perm.OPS_READ))):
        if svc is None or not hasattr(svc, "discovery"):
            return {"available": False, "reason": "discovery reads real buses; the simulation's modules are virtual"}
        return {"available": True, "buses": svc.discovery.view(lambda uid: svc.backend.health(uid).online)}

    @app.post("/api/discovery/sweep")
    def discovery_sweep(body: SweepIn, p: Principal = Depends(require(Perm.MODULE_REGISTER))):
        if svc is None or not hasattr(svc, "discovery"):
            raise HTTPException(409, "discovery reads real buses only")
        try:
            svc.discovery.request_full(body.bus_id, p.username)
        except KeyError:
            raise HTTPException(409, f"{body.bus_id} is not a serial bus of this configuration")
        svc.hist.event("DISCOVERY_SWEEP_REQUESTED", {"bus_id": body.bus_id}, actor=p.username)
        return {"requested": body.bus_id, "note": "one address per poll, only while the poll has time to spare"}
