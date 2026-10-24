"""HTTP app of the twin service (0.5.0): `aquacrop twin-serve`.

Same request protections as the live app (session cookie, Origin/Referer, CSRF, rate limit, security headers),
with its own cookie name, its own database and its own runtime id. Logins are the live accounts copied one way
from the live snapshot: everyone who can sign in to the real dashboard can sign in to the twin with the same
password; passwords are changed on the real system only. Viewing needs READ (every login, viewers too);
running the simulation, faults, inputs and manual runs need SIM_COMMAND; mode changes, restart approval,
conflict choices and accepting a twin result need PROCESS_CONTROL (operators and administrators, owner
answer 4); activating a program in the twin needs EQUIPMENT_EDIT.
Every state-changing request must carry `X-Runtime-Id` equal to this twin run's id.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..domain.model import new_id
from ..historian.writer import Historian
from ..programs.engine import Refused
from ..programs.schedule import upcoming
from ..programs.store import ProgramRefused
from ..security.auth import Accounts, AuthError, Perm, Principal, RateLimiter, load_or_create_secret
from ..twin import feed
from ..twin.equipment_service import EquipmentTwinService, twin_config

COOKIE = "aquacrop_twin_session"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
WEB_DIR = resources.files("aquacrop") / "web"
USER_SYNC_S = 30.0


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(_B):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)


class TicketIn(_B):
    ticket: str = Field(max_length=512)


class ControlIn(_B):
    action: str = Field(pattern="^(run|pause|reset|import|step)$")
    speed: int | None = None
    steps: int | None = Field(default=None, ge=1, le=20000)
    start_local: str | None = Field(default=None, max_length=32)      # reset: scenario start, site time


class FaultIn(_B):
    target: str = Field(max_length=64)
    fault: str | None = Field(default=None, max_length=24)


class InputIn(_B):
    point: str = Field(max_length=64)
    value: float | bool | None


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


class AcceptIn(ProgramRefIn):
    note: str = Field(default="", max_length=500)


def create_twin_app(feed_dir: str | Path, *, state_dir: str | Path, allowed_hosts: list[str],
                    allowed_origins: list[str], bind_host: str = "127.0.0.1", session_id: str | None = None) -> FastAPI:
    feed_dir, state_dir = Path(feed_dir), Path(state_dir)
    snap = feed.read_snapshot(feed_dir)
    if snap is None:
        raise RuntimeError(f"no live snapshot in {feed_dir}: start the live service first")
    cfg = twin_config(snap["config"], allowed_hosts=allowed_hosts, allowed_origins=allowed_origins)
    session_id = session_id or new_id("twin")
    hist = Historian(state_dir / cfg.historian.db_path, cfg, session_id)
    rows = hist.query("SELECT DISTINCT profile FROM process_sessions WHERE session_id != ?", (session_id,))
    if {r["profile"] for r in rows} - {"SIMULATED"}:
        hist.close()
        raise RuntimeError("this database belongs to a real profile; the twin needs its own state directory")
    sec = cfg.security
    accounts = Accounts(hist, load_or_create_secret(state_dir / "secret.key"),
                        idle_minutes=sec.session_idle_minutes, absolute_hours=sec.session_absolute_hours)
    limiter = RateLimiter()
    tickets = feed.TicketCheck(feed_dir)
    svc = EquipmentTwinService(cfg, hist, session_id, feed_dir)
    svc.sync_users(accounts)
    loopback = sec.loopback_http_cookie_exemption and bind_host in ("127.0.0.1", "localhost", "::1")

    async def user_sync():
        while True:
            await asyncio.sleep(USER_SYNC_S)
            with contextlib.suppress(Exception):
                await run_in_threadpool(svc.sync_users, accounts)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        svc.start()
        loop_task = asyncio.create_task(svc.run_forever())
        sync_task = asyncio.create_task(user_sync())
        hist.event("PROCESS_STARTED", {"session_id": session_id, "runtime": svc.runtime_id, "version": __version__},
                   actor="system")
        try:
            yield
        finally:
            for t in (loop_task, sync_task):
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            hist.event("PROCESS_STOPPING", {"session_id": session_id}, actor="system")
            hist.close()

    app = FastAPI(title="AquaCrop digital twin", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.hist, app.state.svc, app.state.accounts, app.state.cfg = hist, svc, accounts, cfg
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sec.allowed_hosts)

    def origin_ok(request: Request) -> bool:
        o = request.headers.get("origin")
        if o:
            return o in sec.allowed_origins
        ref = request.headers.get("referer")
        return bool(ref) and any(ref == x or ref.startswith(x + "/") for x in sec.allowed_origins)

    @app.middleware("http")
    async def guards(request: Request, call_next):
        path = request.url.path
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > sec.max_request_bytes:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        if path.startswith("/api/") and path not in ("/api/health", "/api/auth/login", "/api/auth/ticket"):
            def deny(status, reason, user=None):
                with contextlib.suppress(Exception):
                    hist.event("REQUEST_REJECTED", {"path": path, "status": status, "reason": reason}, actor=user)
                return JSONResponse({"detail": reason}, status_code=status)
            try:
                p = accounts.resolve(request.cookies.get(COOKIE))
            except AuthError as e:
                return deny(e.status, str(e))
            if request.method in MUTATING:
                if not origin_ok(request):
                    return deny(403, "origin not allowed", p.username)
                try:
                    accounts.check_csrf(p, request.headers.get("x-csrf-token"))
                except AuthError as e:
                    return deny(e.status, str(e), p.username)
                if not limiter.hit(f"mut:{p.session_hash}", sec.mutation_rate_per_minute):
                    return deny(429, "rate limit", p.username)
                if path != "/api/auth/logout" and request.headers.get("x-runtime-id") != svc.runtime_id:
                    return deny(409, "this page belongs to another runtime or an earlier twin run; reload it",
                                p.username)
            request.state.principal = p
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def require(perm: Perm):
        def dep(request: Request) -> Principal:
            p: Principal = request.state.principal
            if not p.can(perm):
                raise HTTPException(403, f"permission {perm.value} required")
            return p
        return dep

    def refused(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (Refused, ProgramRefused, ValueError) as e:
            raise HTTPException(409, str(e))

    # ------------------------------------------------------------------ static
    @app.get("/", include_in_schema=False)
    def index():
        return Response((WEB_DIR / "twin.html").read_bytes(), media_type="text/html; charset=utf-8")

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str):
        allowed = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
        ext = Path(name).suffix
        if "/" in name or ".." in name or ext not in allowed:
            raise HTTPException(404)
        f = WEB_DIR / name
        if not f.is_file():
            raise HTTPException(404)
        return Response(f.read_bytes(), media_type=allowed[ext])

    # ------------------------------------------------------------------ auth
    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        if not origin_ok(request):
            raise HTTPException(403, "origin not allowed")
        if not limiter.hit(f"login:{request.client.host if request.client else '-'}", sec.login_rate_per_minute):
            raise HTTPException(429, "rate limit")
        try:
            token, p = accounts.login(body.username, body.password, request.client.host if request.client else "-")
        except AuthError:
            raise HTTPException(401, "login failed")
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https" or not loopback, path="/",
                            max_age=sec.session_absolute_hours * 3600)
        hist.event("LOGIN", {}, actor=p.username)
        return {"username": p.username, "role": p.role.value, "csrf_token": accounts.csrf_for(p.session_hash),
                "runtime_id": svc.runtime_id}

    @app.post("/api/auth/ticket")
    def ticket_login(body: TicketIn, request: Request, response: Response):
        """Sign-in handed over by the real dashboard: its one-time ticket instead of the password (0.5.5)."""
        if not origin_ok(request):
            raise HTTPException(403, "origin not allowed")
        if not limiter.hit(f"login:{request.client.host if request.client else '-'}", sec.login_rate_per_minute):
            raise HTTPException(429, "rate limit")
        try:
            user = tickets.username(body.ticket)
        except (ValueError, KeyError):
            raise HTTPException(401, "the hand-off from the real dashboard is not valid any more; open the twin again")
        try:
            token, p = accounts.open_session(user, request.client.host if request.client else "-")
        except AuthError:
            raise HTTPException(401, "your login has not reached the twin yet; try again in 30 seconds")
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https" or not loopback, path="/",
                            max_age=sec.session_absolute_hours * 3600)
        hist.event("LOGIN", {"via": "handoff"}, actor=p.username)
        return {"username": p.username, "role": p.role.value}

    @app.post("/api/auth/logout")
    def logout(response: Response, p: Principal = Depends(require(Perm.READ))):
        accounts.revoke(p.session_hash, "logout")
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/session")
    def session(p: Principal = Depends(require(Perm.READ))):
        return {"username": p.username, "role": p.role.value, "csrf_token": accounts.csrf_for(p.session_hash),
                "permissions": [x.value for x in Perm if p.can(x)], "runtime_id": svc.runtime_id}

    @app.get("/api/health")
    def health():
        return {"ok": True, "runtime": "TWIN"}

    # ------------------------------------------------------------------ the twin
    @app.get("/api/twin")
    def twin_view(p: Principal = Depends(require(Perm.READ))):
        v = svc.view()
        v["programs"] = _programs()
        v["checklist"] = svc.engine.checklist() if svc.engine.mode == "RECOVERY" else None
        v["consequences"] = ({t: svc.engine.approval_consequences(t) for t in ("AUTO", "MANUAL")}
                             if svc.engine.mode == "RECOVERY" else None)
        return v

    def _programs() -> list[dict]:
        active = svc.programs.active(svc.store_id)
        out = []
        for v in svc.programs.versions():
            a = active.get(v["program_id"])
            entry = {k: v[k] for k in ("program_id", "version", "status", "name", "digest", "body")} | {
                "active": bool(a and a[0] == v["version"]), "created_by": v["created_by"]}
            t = v["body"]["trigger"]
            if t["type"] in ("WEEKLY",) and entry["active"]:
                entry["next"] = [w.start_local for w in upcoming(f"{v['program_id']}@{v['version']}", t["entries"],
                                                                  cfg.site.timezone, svc.clock.utc(), 3)]
            out.append(entry)
        return out

    @app.post("/api/twin/control")
    def control(body: ControlIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        if body.action == "run":
            if body.speed is not None:
                refused(svc.set_speed, p.username, body.speed)
            svc.set_running(p.username, True)
        elif body.action == "pause":
            svc.set_running(p.username, False)
        elif body.action == "step":
            svc.step(body.steps or 1)
        elif body.action == "import":
            return refused(svc.import_snapshot, p.username)
        elif body.action == "reset":
            start = None
            if body.start_local:
                from zoneinfo import ZoneInfo
                try:
                    start = datetime.fromisoformat(body.start_local).replace(tzinfo=ZoneInfo(cfg.site.timezone)) \
                        .astimezone(timezone.utc)
                except ValueError:
                    raise HTTPException(422, "start time: YYYY-MM-DDTHH:MM")
            svc.reset(p.username, start)
        return {"ok": True}

    @app.post("/api/twin/fault")
    def fault(body: FaultIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        refused(svc.twin.set_fault, body.target, body.fault)
        hist.event("TWIN_FAULT", {"target": body.target, "fault": body.fault}, actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/input")
    def set_input(body: InputIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        svc.twin.set_input(body.point, body.value)
        hist.event("TWIN_INPUT", {"point": body.point, "value": body.value}, actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/mode")
    def mode(body: ModeIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        with svc.lock:
            return refused(svc.engine.request_mode, p.username, body.target, body.reason)

    @app.post("/api/twin/recovery/approve")
    def approve(body: ModeIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        with svc.lock:
            return refused(svc.engine.approve_recovery, p.username, body.target)

    @app.post("/api/twin/run-now")
    def run_now(body: RunNowIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        with svc.lock:
            return refused(svc.engine.run_now, p.username, body.program_id, duration_s=body.duration_s)

    @app.post("/api/twin/stop")
    def stop(body: OpIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        with svc.lock:
            return refused(svc.engine.stop_op, p.username, body.op_id)

    @app.post("/api/twin/conflict")
    def conflict(body: ConflictIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        with svc.lock:
            return refused(svc.engine.resolve_conflict, p.username, body.conflict_id, body.choice)

    @app.post("/api/twin/programs/activate")
    def activate(body: ProgramRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        v = refused(svc.programs.get, body.program_id, body.version)
        if v["status"] != "VALIDATED":
            refused(_validate_here, p.username, body.program_id, body.version)
        return refused(svc.programs.activate, p.username, svc.store_id, body.program_id, body.version,
                       require_simulation=False, overlaps_accepted=True, overlaps=[])

    def _validate_here(actor, pid, ver):
        from ..programs.definitions import parse, validate
        v = svc.programs.get(pid, ver)
        items = {iid: it for iid, it in svc.items.items.items()}
        issues = validate(parse(v["body"]), items, svc.items.types, physical=False, bindings=svc.items.bindings,
                          readiness={})
        r = svc.programs.mark_validated(actor, pid, ver, issues, {"runtime": svc.runtime_id})
        if issues:
            raise ValueError("; ".join(issues))
        return r

    @app.post("/api/twin/programs/deactivate")
    def deactivate(body: ProgramRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        refused(svc.programs.deactivate, p.username, svc.store_id, body.program_id)
        return {"ok": True}

    @app.post("/api/twin/accept")
    def accept(body: AcceptIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        return refused(svc.accept, p.username, body.program_id, body.version, body.note)

    @app.get("/api/twin/events")
    def events(p: Principal = Depends(require(Perm.READ))):
        rows = hist.query("SELECT utc, kind, actor, payload_json FROM events ORDER BY id DESC LIMIT 200")
        return [dict(r) for r in rows]

    return app
