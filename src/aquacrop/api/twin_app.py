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
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fastapi.exceptions import RequestValidationError

from ..twin.nutrient_sim import Preparation, simulate as simulate_preparation
from .. import __version__, layout
from ..domain.model import new_id
from ..historian.writer import Historian
from ..programs.engine import Refused
from ..programs.schedule import upcoming
from ..programs.store import ProgramRefused
from ..security.auth import Accounts, AuthError, Perm, Principal, RateLimiter, load_or_create_secret
from ..twin import feed
from . import pages
from ..equipment.definitions import TypeBody, channel_issues
from ..blocks.store import BlockRefused
from ..build import identity as build_identity, publish as publish_identity
from ..twin.changes import ChangeRefused, TwinChanges, preview as changes_preview, program_items, write_export
from ..twin.equipment_service import EquipmentTwinService, twin_config
from .app import safe_validation_response

COOKIE = "aquacrop_twin_session"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
USER_SYNC_S = 30.0


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LoginIn(_B):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)


class TicketIn(_B):
    ticket: str = Field(max_length=512)


class ModuleIn(_B):
    device_uid: str = Field(max_length=32)
    model: str = Field(max_length=64)
    label: str = Field(default="", max_length=80)


class ModuleRefIn(_B):
    device_uid: str = Field(max_length=32)


class GraphLevelIn(_B):
    node: str = Field(max_length=48)
    litres: float = Field(ge=0, le=1e7)


class LayoutIn(_B):
    layout: dict


class LayoutStartIn(_B):
    source: str = Field(pattern="^(live|default)$")


class WaterLevelIn(_B):
    node: str = Field(max_length=48)
    pct: float = Field(ge=0, le=100)


class SendLayoutIn(_B):
    note: str = Field(default="", max_length=500)


class ItemSaveIn(_B):
    item_id: str = Field(max_length=48)
    name: str = Field(max_length=80)
    type_id: str = Field(max_length=48)
    type_version: int = Field(ge=1)
    icon: str = Field(max_length=24)
    body: dict = Field(default_factory=dict)


class ItemRefIn(_B):
    item_id: str = Field(max_length=48)


class BindIn(_B):
    item_id: str = Field(max_length=48)
    role: str = Field(max_length=48)
    point: str | None = Field(default=None, max_length=64)


class ProgramDraftIn(_B):
    program_id: str = Field(max_length=48)
    name: str = Field(max_length=80)
    body: dict


class ProgramIdIn(_B):
    program_id: str = Field(max_length=48)


class ExportIn(_B):
    note: str = Field(default="", max_length=1000)


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


class DesignImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replace: bool = False                 # replace a twin design that was not exported (the second press)


def create_twin_app(feed_dir: str | Path, *, state_dir: str | Path, allowed_hosts: list[str],
                    allowed_origins: list[str], bind_host: str = "127.0.0.1", session_id: str | None = None) -> FastAPI:
    feed_dir, state_dir = Path(feed_dir), Path(state_dir)
    publish_identity(feed_dir, "TWIN")
    snap = feed.read_snapshot(feed_dir)
    if snap is None:
        raise RuntimeError(f"no live snapshot in {feed_dir}: start the live service first")
    from ..twin.virtual_modules import VirtualModules, VirtualRefused, WORDS as MODULE_WORDS
    virtual = VirtualModules(state_dir / "virtual-modules.json")       # 0.7.0: modules that exist only in the twin
    cfg = twin_config(snap["config"], allowed_hosts=allowed_hosts, allowed_origins=allowed_origins, virtual=virtual)
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
    svc.virtual = virtual
    svc.changes = TwinChanges(state_dir / "changes.json")
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
    app.add_exception_handler(RequestValidationError, lambda request, exc: safe_validation_response(exc))

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
        resp.headers[pages.VERSION_HEADER] = build_identity()["build"]
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
        return pages.page("twin.html")

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str, request: Request):
        return pages.static(name, request, {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"})

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
        if not p.can(Perm.OPS_READ):                 # 0.7.0 role matrix: no twin for viewers (ADR-0023)
            accounts.revoke(p.session_hash, "viewer: no digital twin")
            raise HTTPException(403, "the digital twin is for operators and administrators; a viewer account sees the Overview, Programs and Alarms of the real system")
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
        if not p.can(Perm.OPS_READ):
            accounts.revoke(p.session_hash, "viewer: no digital twin")
            raise HTTPException(403, "the digital twin is for operators and administrators; a viewer account sees the Overview, Programs and Alarms of the real system")
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
        if svc.loop_error:
            return JSONResponse({"ok": False, "runtime": "TWIN", "error": "simulation loop failed; it is paused"},
                                status_code=503)
        return {"ok": True, "runtime": "TWIN", "version": __version__, "build": build_identity()["build"]}

    # ------------------------------------------------------------------ the twin
    @app.get("/api/twin")
    def twin_view(p: Principal = Depends(require(Perm.OPS_READ))):
        v = svc.view()
        v["programs"] = _programs()
        v["checklist"] = svc.engine.checklist() if svc.engine.mode == "RECOVERY" else None
        v["consequences"] = ({t: svc.engine.approval_consequences(t) for t in ("AUTO", "MANUAL")}
                             if svc.engine.mode == "RECOVERY" else None)
        from ..build import partner, web_drift
        v["identity"] = {"runtime": "TWIN", "build": build_identity()["build"], "web_drift": web_drift(),
                         "partner": partner(feed_dir, "MAIN"),
                         "project": {"config_id": cfg.config_id, "config_version": cfg.config_version}}
        return v

    def _programs() -> list[dict]:
        active = svc.programs.active(svc.store_id)
        out = []
        for v in svc.programs.versions():
            if v["status"] == "RETIRED":
                continue                               # removed from the twin (0.6.9)
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

    @app.post("/api/twin/nutrient/simulate")
    def nutrient_simulate(body: Preparation, p: Principal = Depends(require(Perm.OPS_READ))):
        """PROG-10: a labelled simulation of a nutrient preparation (synthetic fixture). Nothing is dosed or stored."""
        return simulate_preparation(body)

    @app.post("/api/twin/fault")
    def fault(body: FaultIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        refused(svc.twin.set_fault, body.target, body.fault)
        hist.event("TWIN_FAULT", {"target": body.target, "fault": body.fault}, actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/input")
    def set_input(body: InputIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        refused(svc.twin.set_input, body.point, body.value)
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

    # ------------------------------------------------------------------ the diagram (0.5.6)
    draft_path = state_dir / "layout-draft.json"

    def live_layout() -> tuple[layout.Layout, str]:
        snap = feed.read_snapshot(feed_dir) or {}
        with contextlib.suppress(Exception):
            if snap.get("layout"):
                return layout.parse(snap["layout"]), "the real system's layout"
        return layout.default_layout(), "the standard drawing"

    def draft() -> tuple[layout.Layout, str]:
        with contextlib.suppress(OSError, ValueError):
            return layout.parse(json.loads(draft_path.read_text(encoding="utf-8"))), "the twin's draft"
        return live_layout()

    def save_draft(lay: layout.Layout) -> None:
        tmp = draft_path.with_name(draft_path.name + ".tmp")
        tmp.write_text(json.dumps(layout.canonical(lay), indent=1), encoding="utf-8")
        tmp.replace(draft_path)

    _lay_cache: dict = {}

    def layout_now():                       # the twin's water follows the twin's own drawing
        try:
            mt = draft_path.stat().st_mtime
        except OSError:
            mt = None
        if _lay_cache.get("mt") != mt or "doc" not in _lay_cache:
            lay = draft()[0]
            _lay_cache.update(mt=mt, digest=layout.digest(lay), doc=layout.canonical(lay))
        return _lay_cache["digest"], _lay_cache["doc"]
    svc.layout_fn = layout_now

    # ------------------------------------------------------------------ blocks and the explicit graph (0.7.0)
    from ..blocks.store import BlockStore
    from ..equipment.views import views_hydraulic
    from . import blocks as block_routes
    def public_ids() -> dict[str, str]:              # 0.7.1: the IDs people see, as the main dashboard has them
        return {i["item_id"]: i.get("public_id") or i["item_id"]
                for i in ((svc.snap or {}).get("equipment") or {}).get("items", [])}
    svc.blocks = BlockStore(hist, items=lambda: {k: v | {"public_id": public_ids().get(k, k)}
                                                 for k, v in views_hydraulic(svc.items.views()).items()})
    app.state.blocks = svc.blocks
    block_routes.register(app, require, svc.blocks, legacy_layout=lambda: (draft()[0], draft()[1]))
    from . import runs as run_routes
    run_routes.register(app, require, hist, svc.programs)

    @app.get("/api/twin/modules")
    def modules_view(p: Principal = Depends(require(Perm.OPS_READ))):
        sim = virtual.uids()
        return {"simulated": virtual.all(), "models": MODULE_WORDS,
                "real": [{"device_uid": d.device_uid, "model": d.model.value, "label": d.label} for d in svc.cfg.devices
                         if d.device_uid not in sim],
                "meaning": "A simulated module exists in this twin only: the real system never sees it, and equipment "
                           "on its channels cannot be sent to the real system."}

    @app.post("/api/twin/modules")
    def module_add(body: ModuleIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        live = {d["device_uid"] for d in (svc.snap or {}).get("config", {}).get("devices", [])}
        try:
            dev = virtual.add(body.device_uid, body.model, body.label, live)
        except VirtualRefused as e:
            raise HTTPException(409, str(e))
        svc.rebuild_modules(p.username)
        hist.event("TWIN_MODULE_ADDED", dev, actor=p.username)
        return {"module": dev, "runtime_id": svc.runtime_id,
                "note": "added; the twin was reset to a new run with the new channels"}

    @app.post("/api/twin/modules/remove")
    def module_remove(body: ModuleRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            virtual.remove(body.device_uid)
        except VirtualRefused as e:
            raise HTTPException(409, str(e))
        svc.rebuild_modules(p.username)
        hist.event("TWIN_MODULE_REMOVED", {"device_uid": body.device_uid}, actor=p.username)
        return {"removed": body.device_uid, "runtime_id": svc.runtime_id}

    @app.post("/api/twin/graph/level")
    def graph_level(body: GraphLevelIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        """A tank's water in the graph model, as a recorded mass adjustment of this run (0.7.0)."""
        from ..twin.graph_water import params_from
        with svc.lock:
            g = svc.graph()
            if g is None:
                raise HTTPException(409, "this twin has no installation graph yet")
            try:
                svc.gwater.set_level(body.node, body.litres, params_from(g, svc.items.views()), f"set by {p.username}")
            except ValueError as e:
                raise HTTPException(409, str(e))
        hist.event("TWIN_GRAPH_LEVEL", {"tank": body.node, "litres": body.litres, "run_id": svc.run_id}, actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/water/level")
    def water_level(body: WaterLevelIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        with svc.lock:
            refused(svc.water.set_level, body.node, body.pct, layout_now()[1])
            svc.step_water(0.0)
        hist.event("TWIN_WATER_LEVEL", {"tank": body.node, "pct": body.pct}, actor=p.username)
        return {"ok": True}

    _dia_cache: dict = {}

    @app.get("/api/twin/diagram")
    def diagram(p: Principal = Depends(require(Perm.OPS_READ))):
        # the drawing, its comparison with the real system's and its issues change only with the draft, the
        # snapshot or the equipment: computed once for each, not on every refresh of the page
        try:
            mt = draft_path.stat().st_mtime
        except OSError:
            mt = None
        try:
            smt = (feed_dir / feed.SNAPSHOT).stat().st_mtime    # the real system's layout arrives with its snapshot
        except OSError:
            smt = None
        key = (mt, smt, svc.snapshot_utc, json.dumps(svc.changes.doc, sort_keys=True))
        if _dia_cache.get("key") != key:
            _dia_cache.update(key=key, body=_diagram_static())
        return _dia_cache["body"] | {"readings": svc.readings(), "water": svc.water.state}

    def _diagram_static() -> dict:
        lay, src = draft()
        live, _ = live_layout()
        items = []
        for iid, it in svc.items.items.items():
            v = svc.items.views().get(iid)
            items.append({"item_id": iid, "name": it["name"], "type": it["type_id"], "icon": it["icon"],
                          "inputs": [{"role": r.role, "label": r.label, "unit": r.unit} for r in v.type.roles
                                     if r.direction.value == "INPUT"] if v else []})
        return {"layout": layout.canonical(lay), "source": src, "digest": layout.digest(lay),
                "live_digest": layout.digest(live), "changes": layout.summary(live, lay),
                "issues": layout.issues(lay, svc.items.items), "items": items}

    @app.get("/api/twin/frames")
    def frames(after: float = -1.0, p: Principal = Depends(require(Perm.OPS_READ))):
        with svc.lock:
            frames = list(svc.frames)
            run = svc.run_id
        # at most 600 pictures a request, the oldest first: the page asks again for the rest
        return [f for f in frames if f["sim_s"] > after and f["run_id"] == run][:600]

    @app.put("/api/twin/layout")
    def put_layout(body: LayoutIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        try:
            lay = layout.parse(body.layout)
        except ValueError as e:
            raise HTTPException(422, f"the layout is not valid: {e}")
        save_draft(lay)
        hist.event("TWIN_LAYOUT_SAVED", {"digest": layout.digest(lay), "symbols": len(lay.nodes)}, actor=p.username)
        return {"digest": layout.digest(lay), "issues": layout.issues(lay, svc.items.items)}

    @app.post("/api/twin/design/import")
    def design_import(body: DesignImportIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        """Import from Overview (0.7.1): bring the real system's equipment, programs and approved drawing into the twin
        as the working design. When the real system has an installation graph, the twin's drawing becomes that graph
        (a design changed in the twin and not exported is replaced only with `replace`). When it still has only the
        earlier drawing, the answer is a migration proposal for the editor: nothing is saved until the user saves it,
        and every ambiguous or unlinked part is listed for the user to decide. Nothing on the real system changes."""
        refused(svc.import_snapshot, p.username)
        live = (svc.snap or {}).get("hydraulics") or {}
        lt, ld = live.get("topology") or {}, live.get("diagram") or {}
        cur_t, cur_d = svc.blocks.topology(), svc.blocks.diagram("installation")
        if (lt.get("graph") or {}).get("nodes"):
            own = cur_t.get("actor") not in (None, "live-snapshot") or cur_d.get("actor") not in (None, "live-snapshot")
            differs = cur_t["digest"] != lt.get("digest") or cur_d.get("digest") != ld.get("digest")
            if own and differs and not body.replace:
                raise HTTPException(409, "the twin's design has changes that were not exported to the Overview; "
                                         "Import from Overview replaces them: press it again to replace them")
            try:
                if differs:
                    svc.blocks.save_drawing(p.username, "installation", lt["graph"], ld.get("diagram") or {},
                                            expected_topology_rev=cur_t["rev"], expected_diagram_rev=cur_d["rev"],
                                            note="imported from the Overview")
            except BlockRefused as e:
                raise HTTPException(409, f"the Overview's drawing could not be loaded: {e}")
            hist.event("TWIN_DESIGN_IMPORTED", {"source": "overview graph", "replaced": bool(own and differs)},
                       actor=p.username)
            return {"source": "graph", "topology_rev": svc.blocks.topology()["rev"], "replaced": bool(own and differs)}
        # 0.7.3: the earlier drawing, kept as drawn, becomes the twin's saved design (the same guard against replacing
        # a design that was not exported); its placeholders and sketches stay visible until they are resolved
        prop = legacy_proposal()
        own = cur_t.get("actor") not in (None, "live-snapshot") or cur_d.get("actor") not in (None, "live-snapshot")
        has = bool(cur_t["graph"].get("nodes")) or bool((cur_d.get("diagram") or {}).get("placeholders"))
        if own and has and not body.replace:
            raise HTTPException(409, "the twin's design has changes that were not exported to the Overview; "
                                     "Import from Overview replaces them: press it again to replace them")
        try:
            svc.blocks.save_drawing(p.username, "installation", prop["topology"], prop["diagram"],
                                    expected_topology_rev=cur_t["rev"], expected_diagram_rev=cur_d["rev"],
                                    note="imported from the Overview's earlier drawing")
        except BlockRefused as e:
            raise HTTPException(409, f"the Overview's drawing could not be loaded: {e}")
        hist.event("TWIN_DESIGN_IMPORTED", {"source": "earlier drawing", "kept": prop["kept"],
                                            "ambiguous": len(prop["ambiguous"]), "unresolved": len(prop["unresolved"])},
                   actor=p.username)
        return {"source": "legacy", "proposal": {k: prop[k] for k in ("ambiguous", "unresolved", "matched", "to_create",
                                                                        "kept", "source", "meaning")},
                "replaced": bool(own and has)}

    def legacy_proposal() -> dict:
        from ..topology.migrate import propose
        lay, _ = live_layout()
        items = svc.blocks.items()
        return propose(lay, {k: v.get("kind") for k, v in items.items()}, {k: v.get("name") for k, v in items.items()})

    @app.get("/api/twin/design/reference")
    def design_reference(p: Principal = Depends(require(Perm.OPS_READ))):
        """The Overview's current drawing, to show when the twin has no design yet (never a blank editor)."""
        live = (svc.snap or {}).get("hydraulics") or {}
        lt, ld = live.get("topology") or {}, live.get("diagram") or {}
        if (lt.get("graph") or {}).get("nodes"):
            return {"source": "graph", "graph": lt["graph"], "diagram": ld.get("diagram") or {}}
        pr = legacy_proposal()
        return {"source": "legacy", "graph": pr["topology"], "diagram": pr["diagram"], "kept": pr["kept"]}

    @app.post("/api/twin/layout/start-from")
    def start_from(body: LayoutStartIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        if body.source == "live":
            lay = live_layout()[0]
        else:                                           # the standard drawing, linked to the equipment there is (0.6.13)
            from ..twin import starter as st
            have = set(svc.items.views())
            lay = st.link_layout(layout.default_layout(), have)[0]
        save_draft(lay)
        hist.event("TWIN_LAYOUT_RESET", {"source": body.source}, actor=p.username)
        return {"digest": layout.digest(lay)}

    @app.post("/api/twin/layout/send")
    def send_layout(body: SendLayoutIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        lay, _ = draft()
        live, _ = live_layout()
        if layout.digest(lay) == layout.digest(live):
            raise HTTPException(409, "the twin's layout is the same as the real system's: nothing to send")
        try:
            r = layout.write_proposal(feed_dir, lay, by=p.username, note=body.note, twin_run=svc.run_id)
        except OSError as e:
            raise HTTPException(409, f"could not write the proposal: {e}")
        hist.event("TWIN_LAYOUT_SENT", {"digest": r["digest"]}, actor=p.username)
        return r

    # ------------------------------------------------------------------ changes for the real system (0.6.0)
    def changed(fn, *a):
        if svc.snap is None:
            raise HTTPException(409, "no snapshot from the real system yet")
        with svc.lock:
            if any(o["state"] == "RUNNING" for o in svc.engine.view()["ops"]):
                raise HTTPException(409, "a program is running in the twin: stop it before changing equipment")
            try:
                r = fn(*a)
            except ChangeRefused as e:
                raise HTTPException(409, str(e))
            svc.reload_items()
            return r

    def users_of(item_id: str) -> list[str]:
        out = []
        for pid, (ver, body) in svc.programs.active(svc.store_id).items():
            if item_id in program_items(body.model_dump(mode="json")):
                out.append(f"{pid} v{ver}")
        return out

    # the channel lists follow the twin's current configuration (simulated modules rebuild it), never the boot one
    _fits: dict[tuple, list[str]] = {}

    @app.get("/api/twin/changes")
    def changes_view(p: Principal = Depends(require(Perm.OPS_READ))):
        snap = svc.snap or {"equipment": {"types": [], "items": []}, "bindings": [], "programs": []}
        eff = svc.changes.overlay(snap) if svc.snap else snap
        if svc.snap:
            pv = changes_preview(snap, svc.changes, svc.programs.versions(), svc.results, svc.current_proof,
                                 virtual.uids(), svc.blocks.export())
        else:
            pv = {"items": [], "bindings": [], "programs": [], "missing": [], "empty": True}
        cur = svc.cfg
        all_points = sorted(str(x) for x in cur.all_points())

        def fitting(role) -> list[str]:          # channels whose kind, direction and interface suit the role
            key = (cur.config_version, role.model_dump_json())
            if key not in _fits:
                _fits[key] = [pt for pt in all_points if not channel_issues(cur, role, pt, None)]
            return _fits[key]
        types = []
        for t in snap["equipment"]["types"]:
            if t["status"] not in ("ACTIVE", "SUPERSEDED"):
                continue
            tb = TypeBody.model_validate(t["body"])
            types.append({"type_id": t["type_id"], "version": t["version"], "name": t["name"], "status": t["status"],
                          "default_icon": t["body"].get("default_icon", "generic"),
                          "roles": [{"role": r.role, "label": r.label, "direction": r.direction.value,
                                     "points": fitting(r)} for r in tb.roles]})
        binds: dict[str, dict] = {}
        for b in eff.get("bindings", []):
            binds.setdefault(b["item_id"], {})[b["role"]] = b["point"]
        doc = svc.changes.doc
        items = [{k: i[k] for k in ("item_id", "name", "type_id", "type_version", "icon", "body", "status")}
                 | {"public_id": i.get("public_id") or i["item_id"]}
                 | {"bindings": binds.get(i["item_id"], {}),
                    "changed": i["item_id"] in doc["items"] or i["item_id"] in doc["retire"]
                    or any(k.startswith(i["item_id"] + ".") for k in doc["bindings"])}
                 for i in eff["equipment"]["items"] if i["status"] != "RETIRED" or i["item_id"] in doc["retire"]]
        return {"preview": pv, "types": types, "items": items, "points": all_points,
                "snapshot_utc": svc.snapshot_utc}

    @app.post("/api/twin/equipment/save")
    def eq_save(body: ItemSaveIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        r = changed(svc.changes.save_item, svc.snap, svc.cfg, body.item_id, body.name, body.type_id, body.type_version,
                    body.icon, body.body)
        hist.event("TWIN_EQUIPMENT_CHANGED", {"item": body.item_id}, actor=p.username)
        return r

    @app.post("/api/twin/equipment/retire")
    def eq_retire(body: ItemRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        changed(svc.changes.retire_item, svc.snap, body.item_id, users_of(body.item_id))
        hist.event("TWIN_EQUIPMENT_RETIRED", {"item": body.item_id}, actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/equipment/bind")
    def eq_bind(body: BindIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        changed(svc.changes.bind, svc.snap, svc.cfg, body.item_id, body.role, body.point)
        hist.event("TWIN_CHANNEL_CHANGED", {"item": body.item_id, "role": body.role, "point": body.point},
                   actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/equipment/revert")
    def eq_revert(body: ItemRefIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        changed(svc.changes.revert, body.item_id)
        return {"ok": True}

    @app.post("/api/twin/changes/discard")
    def changes_discard(p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        changed(svc.changes.reset)
        hist.event("TWIN_CHANGES_DISCARDED", {}, actor=p.username)
        return {"ok": True}

    @app.post("/api/twin/programs/draft")
    def program_draft(body: ProgramDraftIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        have = [v for v in svc.programs.versions() if v["program_id"] == body.program_id]
        draft_v = next((v["version"] for v in have if v["status"] == "DRAFT"), None)
        v = refused(svc.programs.save_draft, p.username, body.program_id, body.name, body.body, version=draft_v)
        try:
            _validate_here(p.username, v["program_id"], v["version"])
            issues = []
        except ValueError as e:
            issues = str(e).split("; ")
        return refused(svc.programs.get, v["program_id"], v["version"]) | {"issues": issues}

    @app.post("/api/twin/starter")
    def starter(p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        """Every part of the standard installation as equipment, with the drawing linked to it (0.6.2)."""
        from ..equipment.definitions import TEMPLATES
        from ..twin import starter as st
        if svc.snap is None:
            raise HTTPException(409, "no snapshot from the real system yet")
        missing = st.missing_types(svc.snap)
        if missing:
            names = ", ".join(TEMPLATES[t]["name"] for t in missing)
            raise HTTPException(409, f"activate these equipment types on the real system first (Equipment types: "
                                     f"Add built-in templates, then Activate each): {names}")
        active = {t["type_id"]: t["version"] for t in svc.snap["equipment"]["types"] if t["status"] == "ACTIVE"}

        def make():
            items = [i for i in svc.changes.overlay(svc.snap)["equipment"]["items"] if i["status"] != "RETIRED"]
            have = {i["item_id"] for i in items}
            todo = [part for part in st.parts() if part["item_id"] not in have]
            # all or nothing: every name is checked before anything is created
            taken = {i["name"].casefold(): i for i in items}
            clashes = [f"{part['name']} ({part['item_id']}): the name is used by {taken[part['name'].casefold()]['item_id']}"
                       for part in todo if part["name"].casefold() in taken]
            if clashes:
                raise ChangeRefused("nothing was created. Rename or retire these items first, then press again: "
                                    + "; ".join(clashes))
            before = json.loads(json.dumps(svc.changes.doc))
            made = []
            try:
                for part in todo:
                    svc.changes.save_item(svc.snap, svc.cfg, part["item_id"], part["name"], part["type_id"],
                                          active[part["type_id"]], part["icon"], {})
                    made.append(part["item_id"])
            except ChangeRefused as e:
                svc.changes.doc = before                     # one part failed: the others are taken back too
                svc.changes.save()
                raise ChangeRefused(f"nothing was created: {e}")
            return made, have | set(made)
        made, have = changed(make)
        lay, linked = st.link_layout(draft()[0], have)
        save_draft(lay)
        hist.event("TWIN_STARTER", {"items": made, "links": linked}, actor=p.username)
        return {"created": made, "skipped": len(st.parts()) - len(made), "links": linked}

    @app.post("/api/twin/programs/remove")
    def program_remove(body: ProgramIdIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        """Remove the versions of a program made in the twin, so they are not sent to the real system (0.6.9).
        The real system's versions are untouched; history keeps the removed versions as RETIRED."""
        with svc.lock:
            if any(o["program_id"] == body.program_id and o["state"] == "RUNNING" for o in svc.engine.view()["ops"]):
                raise HTTPException(409, "the program is running in the twin: stop it first")
            mine = [v for v in svc.programs.versions() if v["program_id"] == body.program_id
                    and v["created_by"] != "live-snapshot" and v["status"] != "RETIRED"]
            if not mine:
                raise HTTPException(409, "only program versions made in the twin can be removed here")
            if body.program_id in svc.programs.active(svc.store_id):
                refused(svc.programs.deactivate, p.username, svc.store_id, body.program_id)
            now = datetime.now(timezone.utc).isoformat()
            for v in mine:
                hist.submit(lambda c, v=v: c.execute(
                    "UPDATE programs SET status='RETIRED', updated_utc=? WHERE program_id=? AND version=?",
                    (now, v["program_id"], v["version"])), wait=True)
        hist.event("TWIN_PROGRAM_REMOVED", {"program": body.program_id, "versions": [v["version"] for v in mine]},
                   actor=p.username)
        return {"removed": [v["version"] for v in mine]}

    @app.post("/api/twin/export")
    def export(body: ExportIn, p: Principal = Depends(require(Perm.EQUIPMENT_EDIT))):
        if svc.snap is None:
            raise HTTPException(409, "no snapshot from the real system yet")
        pv = changes_preview(svc.snap, svc.changes, svc.programs.versions(), svc.results, svc.current_proof,
                             virtual.uids(), svc.blocks.export())
        try:
            r = write_export(feed_dir, pv, by=p.username, note=body.note, twin_run=svc.run_id,
                             snapshot_utc=svc.snapshot_utc)
        except ChangeRefused as e:
            raise HTTPException(409, str(e))
        except OSError as e:
            raise HTTPException(409, f"could not write the export: {e}")
        hist.event("TWIN_EXPORT_WRITTEN", r, actor=p.username)
        return r

    @app.get("/api/twin/events")
    def events(p: Principal = Depends(require(Perm.OPS_READ))):
        rows = hist.query("SELECT utc, kind, actor, payload_json FROM events ORDER BY id DESC LIMIT 200")
        return [dict(r) for r in rows]

    return app
