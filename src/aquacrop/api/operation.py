"""HTTP routes of the real system's program operation (0.5.0): installation mode, restart approval, running
operations, conflicts and program activation. They work only when the configuration enables `bench_tests`
(ADR-0018 §9); otherwise they say why and change nothing.

Permissions as in the twin (owner answer 4): mode, restart approval and conflict choices need PROCESS_CONTROL
(operators and administrators); run now and stop need PROCESS_CONTROL as well on the real system; activation
needs EQUIPMENT_ACTIVATE (administrators). Every state-changing request must carry `X-Runtime-Id` equal to the
live runtime id, so a page of the twin or of an earlier process cannot act here.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from ..equipment.definitions import TypeBody
from ..programs.definitions import actuators, parse, validate
from ..programs.engine import Refused
from ..programs.live import STORE_ID
from ..programs.store import ProgramRefused
from ..security.auth import Perm, Principal


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModeIn(_B):
    target: str = Field(pattern="^(AUTO|MANUAL)$")
    reason: str = Field(default="", max_length=200)


class RunNowIn(_B):
    program_id: str = Field(max_length=48)
    duration_s: float | None = Field(default=None, gt=0, le=86400)


class OpIn(_B):
    op_id: str = Field(max_length=64)


class ConflictIn(_B):
    conflict_id: str = Field(max_length=64)
    choice: str = Field(pattern="^(KEEP_FIRST|SWITCH)$")


class ProgramRefIn(_B):
    program_id: str = Field(max_length=48)
    version: int = Field(ge=1)
    accept_overlaps: bool = False


def register(app, require, host, programs, equipment, cfg, *, proof_context=None) -> None:
    why_not = ("Programs cannot run on this real system: scheduled bench tests are not enabled in its configuration. "
               "They need an approved IO 8CH allowlist and an armed test session (ADR-0018).")

    def need(request: Request):
        if host is None:
            raise HTTPException(409, why_not)
        if request.headers.get("x-runtime-id") != host.runtime_id:
            raise HTTPException(409, "this page belongs to another runtime or an earlier start; reload it")
        return host

    async def call(fn, *a, **kw):
        try:
            return await run_in_threadpool(fn, *a, **kw)
        except (Refused, ProgramRefused, ValueError) as e:
            raise HTTPException(409, str(e))

    @app.get("/api/operation")
    def view(p: Principal = Depends(require(Perm.READ))):
        if host is None:
            return {"available": False, "reason": why_not}
        e = host.engine
        v = e.view()
        lease = host.svc.gate.active()
        return v | {"available": True, "runtime_id": host.runtime_id,
                    "allowlist": cfg.bench_tests.allowlist, "loads": cfg.bench_tests.loads,
                    "approved_by": cfg.bench_tests.approved_by,
                    "session": None if lease is None else {"username": lease.username, "allowlist": sorted(lease.allowlist),
                                                           "expires_utc": lease.expires_utc.isoformat()},
                    "checklist": e.checklist() if e.mode == "RECOVERY" else None,
                    "consequences": {t: e.approval_consequences(t) for t in ("AUTO", "MANUAL")} if e.mode == "RECOVERY" else None,
                    "active": {pid: ver for pid, (ver, _) in programs.active(STORE_ID).items()},
                    "last_error": host.last_error,
                    "meaning": ("Test loads on the bench (LEDs, buttons). A pass shows bench behaviour, not pump "
                                "performance, water delivery or field safety.")}

    @app.post("/api/operation/mode")
    async def mode(body: ModeIn, request: Request, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        h = need(request)
        return await call(h.engine.request_mode, p.username, body.target, body.reason)

    @app.post("/api/operation/recovery/approve")
    async def approve(body: ModeIn, request: Request, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        h = need(request)
        return await call(h.engine.approve_recovery, p.username, body.target)

    @app.post("/api/operation/run-now")
    async def run_now(body: RunNowIn, request: Request, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        h = need(request)
        return await call(h.engine.run_now, p.username, body.program_id, duration_s=body.duration_s)

    @app.post("/api/operation/stop")
    async def stop(body: OpIn, request: Request, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        h = need(request)
        return await call(h.engine.stop_op, p.username, body.op_id)

    @app.post("/api/operation/conflict")
    async def conflict(body: ConflictIn, request: Request, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        h = need(request)
        return await call(h.engine.resolve_conflict, p.username, body.conflict_id, body.choice)

    @app.post("/api/operation/activate")
    async def activate(body: ProgramRefIn, request: Request, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        need(request)
        v = await call(programs.get, body.program_id, body.version)
        items = {i["item_id"]: i for i in equipment.items()}
        types = {(t["type_id"], t["version"]): TypeBody.model_validate(t["body"]) for t in equipment.types()}
        bindings: dict[str, dict] = {}
        for b in equipment.bindings().values():
            bindings.setdefault(b["item_id"], {})[b["role"]] = b
        readiness = {iid: equipment.readiness(iid) for iid, it in items.items() if it["status"] == "ACTIVE"}
        problems = validate(parse(v["body"]), items, types, physical=True, bindings=bindings, readiness=readiness)
        host_items = host.views()
        for iid in actuators(parse(v["body"])):
            it = host_items.get(iid)
            if it and it.command_point and it.command_point not in cfg.bench_tests.allowlist:
                problems.append(f"{it.name}: {it.command_point} is not an approved bench test output")
        if problems:
            raise HTTPException(409, "not ready for a physical test: " + "; ".join(problems))
        return await call(programs.activate, p.username, STORE_ID, body.program_id, body.version,
                          require_simulation=True, overlaps_accepted=body.accept_overlaps,
                          overlaps=(v["validation"] or {}).get("context", {}).get("overlaps", []),
                          context=proof_context(body.program_id, body.version) if proof_context else None)

    @app.post("/api/operation/deactivate")
    async def deactivate(body: ProgramRefIn, request: Request, p: Principal = Depends(require(Perm.EQUIPMENT_ACTIVATE))):
        need(request)
        await call(programs.deactivate, p.username, STORE_ID, body.program_id)
        return {"ok": True}
