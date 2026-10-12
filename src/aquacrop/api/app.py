"""Authenticated HTTP API and packaged static UI.

Every state-changing route requires: valid session cookie, permission, allowed Origin/Referer,
valid X-CSRF-Token, and passes a per-session rate limit. GET routes never change state.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import ipaddress
import json
import threading
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..config.catalog import CATALOG
from ..config.schema import InstallationConfig
from ..domain.model import AccessPolicy, ControllerState, Outcome, PointId, PointKind, SignalSource, new_id
from ..historian.backup import create_backup
from ..historian.writer import Historian
from ..security.auth import Accounts, AuthError, Perm, Principal, RateLimiter, Role, load_or_create_secret
from ..twin.clock import SPEEDS
from ..twin.scenarios import presets
from .service import SimulationService

COOKIE = "aquacrop_session"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
WEB_DIR = resources.files("aquacrop") / "web"


def _is_loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Body):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)


class PasswordIn(Body):
    old_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class CommandIn(Body):
    backend: Literal["SIMULATED"]
    config_version: str
    target: str
    desired: bool
    request_id: str = Field(min_length=8, max_length=64)
    reason: str = Field(default="manual", max_length=200)


class InjectIn(Body):
    point: str
    raw: float | bool


class OverrideIn(Body):
    point: str
    raw: float | bool
    reason: str = Field(min_length=1, max_length=200)
    duration_s: float | None = Field(default=None, gt=0, le=86400)


class PointIn(Body):
    point: str


class SourceIn(Body):
    source: SignalSource


class DeviceFaultIn(Body):
    device_uid: str
    fault: Literal["TIMEOUT"] | None


class ActuatorFaultIn(Body):
    kind: Literal["pump", "valve"]
    ident: str
    fault: Literal["NO_RUN", "STUCK_ON", "NO_FLOW", "STUCK_CLOSED", "STUCK_OPEN"] | None


class StateIn(Body):
    target: ControllerState


class AlarmRefIn(Body):
    rule_id: str
    target: str


class TwinControlIn(Body):
    action: Literal["run", "pause", "step", "reset", "speed", "acq_stall", "acq_resume"]
    speed: int | None = None
    steps: int = Field(default=1, ge=1, le=600)


class ScenarioIn(Body):
    name: str


class BenchArmIn(Body):
    confirmation: str = Field(max_length=64)
    setup_description: str = Field(max_length=500)
    allowlist: list[str] = Field(max_length=64)
    duration_s: int


class BenchCommandIn(Body):
    backend: Literal["REAL_BENCH"]
    config_version: str
    lease_id: str = Field(max_length=64)
    target: str
    desired: bool
    request_id: str = Field(min_length=8, max_length=64)
    reason: str = Field(default="bench test", max_length=200)


class UserIn(Body):
    username: str
    password: str
    role: Role
    commissioning: bool = False


class UserUpdateIn(Body):
    role: Role | None = None
    commissioning: bool | None = None
    disabled: bool | None = None


class RetentionConfirmIn(Body):
    cutoff_utc: str = Field(min_length=20, max_length=40)   # the cutoff shown by GET /api/retention


def _escape_csv(v):
    """Prevent spreadsheet formula injection in textual fields."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _check_namespace(hist: Historian, cfg: InstallationConfig, session_id: str) -> None:
    """One database per profile: simulated history and limits never mix with a real installation."""
    rows = hist.query("SELECT DISTINCT profile, config_id FROM process_sessions WHERE session_id != ?", (session_id,))
    other = sorted({r["profile"] for r in rows} - {cfg.profile.value})
    if other:
        hist.close()
        raise ValueError(f"database {hist.db_path} belongs to profile(s) {', '.join(other)}; "
                         f"use a separate state directory or db_path for {cfg.profile.value}")


def create_app(cfg: InstallationConfig, *, state_dir: str | Path = "var", bind_host: str = "127.0.0.1",
               session_id: str | None = None) -> FastAPI:
    state_dir = Path(state_dir)
    session_id = session_id or new_id("sess")
    sec = cfg.security
    loopback_http_ok = sec.loopback_http_cookie_exemption and _is_loopback(bind_host)
    tz = ZoneInfo(cfg.site.timezone)
    hist = Historian(Path(cfg.historian.db_path) if Path(cfg.historian.db_path).is_absolute()
                     else state_dir / Path(cfg.historian.db_path).name, cfg, session_id)
    reconciled = hist.reconcile_pending_commands()
    accounts = Accounts(hist, load_or_create_secret(state_dir / "secret.key"),
                        idle_minutes=sec.session_idle_minutes, absolute_hours=sec.session_absolute_hours)
    limiter = RateLimiter()
    _check_namespace(hist, cfg, session_id)
    hist.record_config(cfg)
    is_sim = cfg.profile == AccessPolicy.SIMULATED
    if is_sim:
        svc = SimulationService(cfg, hist, session_id)
    elif cfg.profile == AccessPolicy.HARDWARE_READ_ONLY:
        from .hardware import HardwareService   # loads pymodbus only for a real profile
        svc = HardwareService(cfg, hist, session_id)
    else:
        hist.close()
        raise ValueError(f"profile {cfg.profile.value} is not served by this release")
    if reconciled:
        hist.event("COMMANDS_RECONCILED", {"request_ids": reconciled}, actor="system")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(svc.run_forever()) if is_sim else None
        if not is_sim:
            svc.start()
        hist.event("PROCESS_STARTED", {"session_id": session_id, "config_id": cfg.config_id,
                                       "profile": cfg.profile.value, "version": __version__}, actor="system")
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            else:
                await run_in_threadpool(svc.stop)   # releases ports; sends nothing
            # Shutdown issues no output commands; it only records the event and closes storage.
            hist.event("PROCESS_STOPPING", {"session_id": session_id}, actor="system")
            hist.close()

    app = FastAPI(title="AquaCrop greenhouse platform", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hist, app.state.svc, app.state.accounts, app.state.cfg = hist, svc, accounts, cfg
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sec.allowed_hosts)

    @app.middleware("http")
    async def guards(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > sec.max_request_bytes:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        if request.method in MUTATING and not cl and request.headers.get("transfer-encoding"):
            return JSONResponse({"detail": "chunked bodies not accepted"}, status_code=411)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(AuthError)
    async def _auth_err(request: Request, exc: AuthError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    # ------------------------------------------------------------------ auth dependencies
    def _origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin:
            return origin in sec.allowed_origins
        ref = request.headers.get("referer")
        return bool(ref) and any(ref == o or ref.startswith(o + "/") for o in sec.allowed_origins)

    def _reject(request: Request, status: int, reason: str, user: str | None = None):
        try:
            hist.event("REQUEST_REJECTED", {"path": request.url.path, "method": request.method, "status": status,
                                            "reason": reason, "client": request.client.host if request.client else None},
                       actor=user)
        except Exception:
            pass
        raise HTTPException(status, reason)

    PUBLIC = {"/api/health", "/api/auth/login"}
    SIM_ONLY = ("/api/twin", "/api/scenarios", "/api/io", "/api/controller")   # and their sub-paths
    SIM_ONLY_EXACT = {"/api/commands"}                                          # /api/commands/history is shared

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        """Authentication, origin, CSRF and rate limits run before any body parsing or validation."""
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC:
            return await call_next(request)

        def deny(status: int, reason: str, user: str | None = None):
            try:
                hist.event("REQUEST_REJECTED", {"path": path, "method": request.method, "status": status,
                                                "reason": reason,
                                                "client": request.client.host if request.client else None}, actor=user)
            except Exception:
                pass
            return JSONResponse({"detail": reason}, status_code=status)
        try:
            p = accounts.resolve(request.cookies.get(COOKIE))
        except AuthError as e:
            return deny(e.status, str(e))
        if request.method in MUTATING:
            if not _origin_ok(request):
                return deny(403, "origin not allowed", p.username)
            try:
                accounts.check_csrf(p, request.headers.get("x-csrf-token"))
            except AuthError as e:
                return deny(e.status, str(e), p.username)
            if not limiter.hit(f"mut:{p.session_hash}", sec.mutation_rate_per_minute):
                return deny(429, "rate limit", p.username)
        request.state.principal = p
        if not is_sim and (path in SIM_ONLY_EXACT or any(path == x or path.startswith(x + "/") for x in SIM_ONLY)):
            return JSONResponse({"detail": f"simulation-only endpoint; this installation is {cfg.profile.value}"},
                                status_code=409)
        if is_sim and path.startswith("/api/bench/") and request.method in MUTATING:
            if path != "/api/bench/lease/revoke" and not p.can(Perm.BENCH_ARM):
                return deny(403, "commissioning permission required", p.username)
            return JSONResponse({"detail": "bench writes are unavailable in a SIMULATED profile; the hardware "
                                           "read-only profile is required, and bench endpoints never target "
                                           "simulation"}, status_code=409)
        return await call_next(request)

    def require(perm: Perm):
        def dep(request: Request) -> Principal:
            p: Principal = request.state.principal
            if not p.can(perm):
                why = "password change required" if p.must_change_password else f"permission {perm.value} required"
                _reject(request, 403, why, p.username)
            return p
        return dep

    def rt():
        return svc.runtime

    def _samples() -> dict:
        return rt().samples if is_sim else svc.samples

    def _health(uid: str):
        return rt().backend.health(uid) if is_sim else svc.backend.health(uid)

    def _alarm_engine():
        return rt().alarms if is_sim else svc.alarms

    # ------------------------------------------------------------------ static UI
    @app.get("/", include_in_schema=False)
    def index():
        return Response((WEB_DIR / "index.html").read_bytes(), media_type="text/html; charset=utf-8")

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

    # ------------------------------------------------------------------ auth routes
    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        client = request.client.host if request.client else "?"
        if not _origin_ok(request):
            _reject(request, 403, "origin not allowed")
        if not limiter.hit(f"login:{client}", sec.login_rate_per_minute) or \
                not limiter.hit(f"login-user:{body.username}", sec.login_rate_per_minute):
            _reject(request, 429, "too many login attempts", body.username)
        try:
            token, p = accounts.login(body.username, body.password, client)
        except AuthError as e:
            _reject(request, e.status, "login failed", body.username)
        secure = request.url.scheme == "https" or not loopback_http_ok
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, path="/",
                            max_age=sec.session_absolute_hours * 3600)
        hist.event("LOGIN", {"client": client}, actor=p.username)
        return {"username": p.username, "role": p.role.value, "commissioning": p.commissioning,
                "csrf_token": accounts.csrf_for(p.session_hash), "must_change_password": p.must_change_password}

    @app.post("/api/auth/logout")
    async def logout(response: Response, p: Principal = Depends(require(Perm.READ))):
        accounts.revoke(p.session_hash, "logout")
        if not is_sim:
            svc.gate.session_ended(p.session_hash)
        response.delete_cookie(COOKIE, path="/")
        hist.event("LOGOUT", {}, actor=p.username)
        return {"ok": True}

    @app.get("/api/auth/session")
    async def session(p: Principal = Depends(require(Perm.READ))):
        return {"username": p.username, "role": p.role.value, "commissioning": p.commissioning,
                "csrf_token": accounts.csrf_for(p.session_hash), "must_change_password": p.must_change_password,
                "permissions": [x.value for x in Perm if p.can(x)]}

    @app.post("/api/auth/password")
    def change_password(body: PasswordIn, request: Request, response: Response):
        p: Principal = request.state.principal  # allowed while must_change_password
        try:
            accounts.change_password(p.username, body.old_password, body.new_password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        response.delete_cookie(COOKIE, path="/")
        hist.event("PASSWORD_CHANGED", {"sessions_revoked": True}, actor=p.username)
        if not is_sim:
            svc.gate.session_ended(p.session_hash)
        return {"ok": True, "detail": "password changed; all sessions revoked, log in again"}

    @app.get("/api/openapi.json", include_in_schema=False)
    def openapi(p: Principal = Depends(require(Perm.READ))):
        return app.openapi()

    # ------------------------------------------------------------------ health / status
    @app.get("/api/health")
    def health():
        """Unauthenticated, minimal: no values, no identities."""
        ok_store, _ = hist.can_accept_starts()
        task = svc.task_health()
        return {"ok": ok_store and task["ok"], "storage_ok": ok_store, "acquisition_ok": task["ok"]}

    def _freshness() -> dict:
        now = datetime.now(timezone.utc)
        ages = [(now - s.acquired_utc).total_seconds() for s in list(_samples().values())]
        return {"server_utc": now.isoformat(), "newest_sample_age_s": min(ages) if ages else None,
                "oldest_sample_age_s": max(ages) if ages else None}

    @app.get("/api/status")
    async def status(p: Principal = Depends(require(Perm.READ))):
        if not is_sim:
            return _hw_status()
        r = rt()
        ok_store, why = hist.can_accept_starts()
        devs = {}
        for d in cfg.devices:
            h = r.backend.health(d.device_uid)
            devs[d.device_uid] = {"bus_id": d.bus_id, "address": d.address, "model": d.model.value,
                                  "online": h.online, "consecutive_failures": h.consecutive_failures,
                                  "last_ok_utc": h.last_ok_utc.isoformat() if h.last_ok_utc else None,
                                  "last_error": h.last_error}
        alarms = list(r.alarms.open.values())
        return {"banner": svc.banner, "profile": cfg.profile.value, "config_id": cfg.config_id,
                "config_version": cfg.config_version, "software_version": __version__, "session_id": session_id,
                "timezone": cfg.site.timezone, "controller": {"state": r.controller.state.value,
                                                              "phase": r.controller.phase.value,
                                                              "fault_reason": r.controller.fault_reason,
                                                              "sector": r.controller.sector},
                "twin": {"activity": svc.clock.activity.value, "speed": svc.clock.speed, "run_id": r.run_id,
                         "sim_s": r.sim_s, "tick": r.tick, "source": r.backend.source.value, "scenario": svc.scenario,
                         "seed": r.seed, "acquisition_stalled": r.acquisition_stalled, "overruns": svc.clock.overruns,
                         "recording_interval_s": cfg.historian.recording_interval_s, "recording_basis": "WALL",
                         "sim_time_recording_s": r.sim_time_recording_s,
                         "history_cadence_note": "complete snapshot every recording_interval_s of wall time, at any "
                                                 "speed or while paused (run id and simulated time stored with each); "
                                                 "sim_time_recording_s is the optional simulation-time series "
                                                 "(null = off)"},
                "devices": devs, "storage": hist.storage() | {"ok": ok_store, "reason": why},
                "historian": {k: v for k, v in hist.health.items()}, "acquisition_task": svc.task_health(),
                "alarms": {"active": sum(o.active for o in alarms), "unacknowledged": sum(not o.acknowledged for o in alarms),
                           "reset_required": sum(o.reset_required for o in alarms)},
                "freshness": _freshness(), "physical_writes": "IMPOSSIBLE (simulation backend)",
                "live_control": "UNAVAILABLE in this release"}

    def _devices_view() -> dict:
        devs = {}
        for d in cfg.devices:
            h = _health(d.device_uid)
            devs[d.device_uid] = {"bus_id": d.bus_id, "address": d.address, "model": d.model.value,
                                  "online": h.online, "consecutive_failures": h.consecutive_failures,
                                  "last_ok_utc": h.last_ok_utc.isoformat() if h.last_ok_utc else None,
                                  "last_error": h.last_error}
        return devs

    def _hw_status() -> dict:
        ok_store, why = hist.can_accept_starts()
        alarms = list(svc.alarms.open.values())
        lease = svc.lease_view()
        return {"banner": svc.banner, "profile": cfg.profile.value, "config_id": cfg.config_id,
                "config_version": cfg.config_version, "software_version": __version__, "session_id": session_id,
                "timezone": cfg.site.timezone, "controller": None, "twin": None,
                "devices": _devices_view(), "relay_modes": svc.relay_mode_view(), "bench_lease": lease,
                "storage": hist.storage() | {"ok": ok_store, "reason": why},
                "historian": {k: v for k, v in hist.health.items()}, "acquisition_task": svc.task_health(),
                "alarms": {"active": sum(o.active for o in alarms),
                           "unacknowledged": sum(not o.acknowledged for o in alarms),
                           "reset_required": sum(o.reset_required for o in alarms)},
                "freshness": _freshness(),
                "physical_writes": "BENCH LEASE ARMED (allowlisted coils only)" if lease else
                                   "NONE (read-only: no bench lease armed)",
                "live_control": "UNAVAILABLE in this release"}

    # ------------------------------------------------------------------ points / twin
    def _point_meta(pid: PointId) -> dict:
        dev = cfg.device(pid.device_uid)
        m = cfg.mapping_for_point(str(pid))
        meta = {"id": str(pid), "device_uid": dev.device_uid, "bus_id": dev.bus_id, "kind": pid.kind.value,
                "channel": pid.channel, "register_address": pid.register_address,
                "label": dev.label_for(pid), "model": dev.model.value,
                "mapping": ({"signal": m.signal, "provenance": "SIMULATED"} if m and is_sim else None)}
        if pid.kind == PointKind.AI:
            ch = dev.ai_channel(pid.channel)
            meta |= {"mode": ch.mode.name, "scaling": ch.scaling, "hardware_variant": dev.hardware_variant.value}
        return meta

    def _sample(s) -> dict | None:
        if s is None:
            return None
        return {"quality": s.quality.value, "reason": s.reason, "raw": s.raw_value, "raw_unit": s.raw_unit,
                "value": s.value, "unit": s.unit, "calibration_ref": s.calibration_ref, "origin": s.origin.value,
                "acquired_utc": s.acquired_utc.isoformat(), "sim_s": s.sim_time_s, "override": s.override,
                "cycle_id": s.cycle_id, "sequence": s.sequence}

    @app.get("/api/points")
    async def points(p: Principal = Depends(require(Perm.READ))):
        samples = _samples()
        groups = []
        for d in cfg.devices:
            pts = [_point_meta(x) | {"sample": _sample(samples.get(str(x)))} for x in d.point_ids()]
            h = _health(d.device_uid)
            groups.append({"device_uid": d.device_uid, "label": d.label, "model": CATALOG[d.model].vendor_name,
                           "sku": d.sku, "bus_id": d.bus_id, "address": d.address, "online": h.online,
                           "last_error": h.last_error, "points": pts})
        return {"freshness": _freshness(), "source": rt().backend.source.value if is_sim else "REAL",
                "devices": groups}

    @app.get("/api/twin")
    async def twin(p: Principal = Depends(require(Perm.READ))):
        r = rt()
        return {"snapshot": r.snapshot(), "freshness": _freshness(), "activity": svc.clock.activity.value,
                "speed": svc.clock.speed, "scenario": svc.scenario,
                "params": cfg.twin.model_dump(mode="json") if cfg.twin else None,
                "mapping": [m.model_dump() | {"provenance": "SIMULATED"} for m in cfg.virtual_mapping]}

    @app.get("/api/scenarios")
    async def scenarios(p: Principal = Depends(require(Perm.READ))):
        return [{"name": s.name, "description": s.description, "duration_s": s.duration_s,
                 "twin_overrides": s.twin_overrides} for s in presets().values()]

    # ------------------------------------------------------------------ mutations: simulation only
    def _guard(fn):
        try:
            return fn()
        except PermissionError as e:
            raise HTTPException(409, str(e))
        except KeyError as e:
            raise HTTPException(404, f"unknown: {e}")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/commands")
    async def command(body: CommandIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        if body.config_version != cfg.config_version:
            raise HTTPException(409, "configuration version mismatch")
        rec = _guard(lambda: rt().operator_command(p.username, body.target, body.desired, body.reason,
                                                   request_id=body.request_id))
        return {"request_id": rec.command.request_id, "outcome": rec.outcome.value, "detail": rec.detail,
                "readback": rec.readback, "history": [(t.isoformat(), o.value, d) for t, o, d in rec.history],
                "note": "coil readback is not actuator proof"}

    @app.post("/api/io/inject")
    async def inject(body: InjectIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        _guard(lambda: rt().inject(p.username, body.point, body.raw))
        return {"ok": True}

    @app.post("/api/io/override")
    async def override(body: OverrideIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        _guard(lambda: rt().set_override(p.username, body.point, body.raw, body.reason, duration_s=body.duration_s))
        return {"ok": True}

    @app.post("/api/io/override/clear")
    async def override_clear(body: PointIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        return {"cleared": _guard(lambda: rt().clear_override(p.username, body.point))}

    @app.post("/api/io/source")
    async def source(body: SourceIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        ok, msg = rt().set_source(p.username, body.source)
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True, "source": msg}

    @app.post("/api/io/device-fault")
    async def device_fault(body: DeviceFaultIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        _guard(lambda: rt().set_device_fault(p.username, body.device_uid, body.fault))
        return {"ok": True}

    @app.post("/api/twin/actuator-fault")
    async def actuator_fault(body: ActuatorFaultIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        _guard(lambda: rt().set_actuator_fault(p.username, body.kind, body.ident, body.fault))
        return {"ok": True}

    @app.post("/api/twin/control")
    async def twin_control(body: TwinControlIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        c = svc.clock
        if body.action == "run":
            c.resume()
        elif body.action == "pause":
            c.pause()
        elif body.action == "step":
            if c.activity.value != "PAUSE":
                raise HTTPException(409, "pause before stepping")
            for _ in range(body.steps):
                c.step_once()
        elif body.action == "speed":
            if body.speed not in SPEEDS:
                raise HTTPException(422, f"speed must be one of {SPEEDS}")
            svc.apply_speed(body.speed)
        elif body.action == "reset":
            svc.new_run(f"reset by {p.username}", actor=p.username)
        elif body.action == "acq_stall":
            rt().set_acquisition_stall(p.username, True)
        elif body.action == "acq_resume":
            rt().set_acquisition_stall(p.username, False)
        hist.event("TWIN_CONTROL", body.model_dump(mode="json"), actor=p.username)
        return {"activity": svc.clock.activity.value, "speed": svc.clock.speed, "run_id": rt().run_id}

    @app.post("/api/twin/scenario")
    async def scenario(body: ScenarioIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        try:
            r = svc.new_run(f"scenario {body.name} by {p.username}", scenario=body.name, actor=p.username)
        except KeyError:
            raise HTTPException(404, "unknown scenario")
        return {"run_id": r.run_id, "scenario": body.name}

    @app.post("/api/controller/state")
    async def controller_state(body: StateIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        ok, msg = rt().request_state(p.username, body.target)
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True, "detail": msg}

    @app.post("/api/controller/reset")
    async def controller_reset(p: Principal = Depends(require(Perm.LOCKOUT_RESET))):
        ok, msg = rt().reset_lockout(p.username)
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True, "detail": msg}

    @app.get("/api/controller")
    async def controller(p: Principal = Depends(require(Perm.READ))):
        return rt().controller.snapshot()

    # ------------------------------------------------------------------ alarms / events
    @app.get("/api/alarms")
    async def alarms(p: Principal = Depends(require(Perm.READ)), limit: int = Query(200, ge=1, le=1000)):
        hist_rows = hist.query("SELECT json FROM alarm_occurrences WHERE closed=1 ORDER BY updated_utc DESC LIMIT ?",
                               (limit,))
        return {"open": [o.to_dict() for o in list(_alarm_engine().open.values())],
                "history": [json.loads(x[0]) for x in hist_rows]}

    @app.post("/api/alarms/ack")
    async def ack(body: AlarmRefIn, p: Principal = Depends(require(Perm.ALARM_ACK))):
        if not is_sim:
            return _guard(lambda: _hw_alarm("ack", p.username, body))
        return _guard(lambda: rt().acknowledge(p.username, body.rule_id, body.target).to_dict())

    @app.post("/api/alarms/reset")
    async def alarm_reset(body: AlarmRefIn, p: Principal = Depends(require(Perm.LOCKOUT_RESET))):
        if not is_sim:
            return _guard(lambda: _hw_alarm("reset", p.username, body))
        return _guard(lambda: rt().reset_alarm(p.username, body.rule_id, body.target).to_dict())

    def _hw_alarm(action: str, user: str, body: AlarmRefIn) -> dict:
        with svc.lock:
            occ = svc.alarms.open.get((body.rule_id, body.target))
            if occ is None:
                raise KeyError(f"no open alarm {body.rule_id}/{body.target}")
            out = (svc.alarms.acknowledge if action == "ack" else svc.alarms.reset)(occ.occurrence_id, user)
            dirty = svc.alarms.take_dirty()
        hist.alarms(dirty)
        return out.to_dict()

    def _range(start: str | None, end: str | None, default_minutes: int = 30) -> tuple[str, str]:
        e = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        s = datetime.fromisoformat(start) if start else e - timedelta(minutes=default_minutes)
        if s.tzinfo is None or e.tzinfo is None:
            raise HTTPException(422, "timestamps must include a UTC offset")
        return s.astimezone(timezone.utc).isoformat(), e.astimezone(timezone.utc).isoformat()

    @app.get("/api/events")
    def events(p: Principal = Depends(require(Perm.READ)), start: str | None = None, end: str | None = None,
               kind: str | None = None, limit: int = Query(500, ge=1, le=5000)):
        s, e = _range(start, end, 120)
        sql = "SELECT id, utc, kind, actor, run_id, sim_s, payload_json FROM events WHERE utc>=? AND utc<=?"
        args: list = [s, e]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        rows = hist.query(sql + " ORDER BY id DESC LIMIT ?", tuple(args + [limit + 1]))
        out = [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows[:limit]]
        for o in out:
            o.pop("payload_json")
            if not p.can(Perm.EXPORT_AUDIT) and o["kind"] in ("LOGIN", "LOGOUT", "REQUEST_REJECTED"):
                o["payload"] = {"redacted": True}
        return {"events": out, "truncated": len(rows) > limit, "recent_runtime": rt().events[-50:] if is_sim else []}

    @app.get("/api/commands/history")
    def command_history(p: Principal = Depends(require(Perm.READ)), limit: int = Query(200, ge=1, le=2000)):
        rows = hist.query("SELECT request_id, actor, target, desired, origin, reason, intent_utc, outcome, detail, "
                          "readback, outcome_utc, backend FROM commands ORDER BY intent_utc DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ trends / export / analytics
    @app.get("/api/trends")
    def trends(p: Principal = Depends(require(Perm.READ)), points: str = Query(..., max_length=2000),
               start: str | None = None, end: str | None = None, limit: int = Query(20000, ge=1, le=100000),
               basis: str | None = Query(None, pattern="^(WALL|SIMULATION_TIME|LEGACY_UNKNOWN)$")):
        # `basis` selects one series when the optional simulation-time recorder is also on.
        s, e = _range(start, end)
        ids = [x for x in points.split(",") if x][:16]
        series, truncated = {}, False
        for pid in ids:
            rows = hist.query("SELECT s.acquired_utc, s.value, s.raw_value, s.quality, s.origin, s.unit, s.sim_s, "
                              "s.sim_run_id, c.cadence_s, c.cadence_basis FROM samples s JOIN cycles c ON "
                              "c.cycle_pk=s.cycle_pk WHERE s.point_id=? AND s.acquired_utc>=? AND s.acquired_utc<=? "
                              "AND (? IS NULL OR c.cadence_basis=?) ORDER BY s.acquired_utc LIMIT ?",
                              (pid, s, e, basis, basis, limit + 1))
            truncated |= len(rows) > limit
            out, prev = [], None
            for r in rows[:limit]:
                gap = False
                if prev is not None:
                    if r["sim_run_id"] != prev["sim_run_id"] or r["origin"] != prev["origin"]:
                        gap = True
                    elif r["cadence_basis"] != prev["cadence_basis"]:
                        gap = True
                    elif r["cadence_basis"] == "SIMULATION_TIME" and r["sim_s"] is not None and prev["sim_s"] is not None:
                        gap = (r["sim_s"] - prev["sim_s"]) > 1.5 * max(r["cadence_s"] or 0, prev["cadence_s"] or 0)
                    else:
                        dt = (datetime.fromisoformat(r["acquired_utc"]) - datetime.fromisoformat(prev["acquired_utc"]))
                        gap = dt.total_seconds() > 2.5 * (r["cadence_s"] or cfg.historian.acquisition_interval_s)
                out.append([r["acquired_utc"], r["value"], r["raw_value"], r["quality"], r["origin"], r["unit"],
                            r["sim_s"], r["sim_run_id"], gap])
                prev = r
            series[pid] = out
        ev = hist.query("SELECT utc, kind, actor FROM events WHERE utc>=? AND utc<=? AND kind IN "
                        "('CONTROLLER_TRANSITION','ALARM_RAISED','ALARM_CLEARED','RUN_STARTED','RUN_RESET',"
                        "'OVERRIDE_SET','ACTUATOR_FAULT','DEVICE_FAULT') ORDER BY utc LIMIT 2000", (s, e))
        return {"start": s, "end": e, "columns": ["acquired_utc", "value", "raw", "quality", "origin", "unit",
                                                  "sim_s", "sim_run_id", "gap_before"],
                "series": series, "events": [list(x) for x in ev], "truncated": truncated, "limit": limit,
                "display_timezone": cfg.site.timezone}

    @app.get("/api/export.csv")
    def export_csv(p: Principal = Depends(require(Perm.READ)), points: str | None = None, start: str | None = None,
                   end: str | None = None, after_id: int = 0, limit: int = Query(200000, ge=1, le=1000000),
                   display_tz: str | None = None):
        s, e = _range(start, end, 60)
        zone = ZoneInfo(display_tz) if display_tz else tz
        ids = [x for x in (points or "").split(",") if x]
        sql = ("SELECT id, acquired_utc, point_id, raw_value, raw_type, raw_unit, value, unit, quality, reason, origin, "
               "calibration_ref, config_version, source_profile, device_uid, bus_id, session_id, sim_run_id, sim_s, "
               "override_json FROM samples WHERE acquired_utc>=? AND acquired_utc<=? AND id>?")
        args: list = [s, e, after_id]
        if ids:
            sql += f" AND point_id IN ({','.join('?' * len(ids))})"
            args += ids
        sql += " ORDER BY id LIMIT ?"
        args.append(limit + 1)
        rows = hist.query(sql, tuple(args))
        more = len(rows) > limit
        rows = rows[:limit]

        def gen():
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["# AquaCrop export", f"range_utc={s}..{e}", f"display_tz={zone.key}",
                        f"config={cfg.config_id}@{cfg.config_version}", f"exported_by={p.username}"])
            w.writerow(["id", "acquired_utc", "acquired_local", "local_tz_abbrev", "point_id", "raw_value", "raw_type",
                        "raw_unit", "value", "unit", "quality", "reason", "origin", "calibration_ref",
                        "config_version", "source_profile", "device_uid", "bus_id", "session_id", "sim_run_id",
                        "sim_s", "override"])
            for r in rows:
                loc = datetime.fromisoformat(r["acquired_utc"]).astimezone(zone)
                vals = [r["id"], r["acquired_utc"], loc.isoformat(), loc.tzname()] + [r[k] for k in (
                    "point_id", "raw_value", "raw_type", "raw_unit", "value", "unit", "quality", "reason", "origin",
                    "calibration_ref", "config_version", "source_profile", "device_uid", "bus_id", "session_id",
                    "sim_run_id", "sim_s", "override_json")]
                w.writerow([_escape_csv(v) for v in vals])
                if buf.tell() > 65536:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            if more:
                w.writerow([f"# TRUNCATED at {limit} rows; continue with after_id={rows[-1]['id']}"])
            else:
                w.writerow([f"# COMPLETE: {len(rows)} rows"])
            yield buf.getvalue()

        headers = {"Content-Disposition": "attachment; filename=aquacrop-samples.csv",
                   "X-Truncated": "true" if more else "false"}
        if more:
            headers["X-Next-After-Id"] = str(rows[-1]["id"])
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers=headers)

    @app.get("/api/export/audit.csv")
    def export_audit(p: Principal = Depends(require(Perm.EXPORT_AUDIT)), start: str | None = None,
                     end: str | None = None):
        s, e = _range(start, end, 24 * 60)
        rows = hist.query("SELECT 'event' AS type, utc AS t, kind AS what, actor, payload_json AS detail FROM events "
                          "WHERE utc>=? AND utc<=? UNION ALL SELECT 'command', intent_utc, target||'='||desired||' '||"
                          "outcome, actor, coalesce(detail,'') FROM commands WHERE intent_utc>=? AND intent_utc<=? "
                          "ORDER BY 2", (s, e, s, e))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["type", "utc", "what", "actor", "detail"])
        for r in rows:
            w.writerow([_escape_csv(v) for v in r])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=aquacrop-audit.csv"})

    @app.get("/api/analytics")
    def analytics(p: Principal = Depends(require(Perm.READ)), start: str | None = None, end: str | None = None):
        """Runtime / delivered-volume estimates per origin with coverage disclosure. Origins are never summed."""
        s, e = _range(start, end, 60)
        out = {}
        for m in cfg.virtual_mapping:
            if m.signal.endswith(".running") or m.signal.endswith("flow_transmitter"):
                rows = hist.query("SELECT acquired_utc, value, quality, origin FROM samples WHERE point_id=? AND "
                                  "acquired_utc>=? AND acquired_utc<=? ORDER BY acquired_utc", (m.point, s, e))
                by_origin: dict[str, dict] = {}
                prev = None
                for t, v, q, o in rows:
                    d = by_origin.setdefault(o, {"samples": 0, "valid": 0, "estimate": 0.0, "covered_s": 0.0})
                    d["samples"] += 1
                    ts = datetime.fromisoformat(t)
                    if q in ("GOOD", "OVERRIDDEN") and v is not None:
                        d["valid"] += 1
                        if prev and prev[2] == o and prev[3]:
                            dt = (ts - prev[0]).total_seconds()
                            d["covered_s"] += dt
                            d["estimate"] += (prev[1] * dt / 60.0) if m.signal.endswith("transmitter") else \
                                (dt if prev[1] else 0.0)
                    prev = (ts, v, o, q in ("GOOD", "OVERRIDDEN") and v is not None)
                for d in by_origin.values():
                    d["coverage"] = d["valid"] / d["samples"] if d["samples"] else 0.0
                out[m.signal] = {"point": m.point, "kind": "volume_L (wall-time integration)" if
                                 m.signal.endswith("transmitter") else "runtime_s", "by_origin": by_origin}
        alarm_counts = hist.query("SELECT rule_id, count(*) FROM alarm_occurrences WHERE first_seen_utc>=? AND "
                                  "first_seen_utc<=? GROUP BY rule_id", (s, e))
        return {"start": s, "end": e, "estimates": out, "alarm_counts": {r[0]: r[1] for r in alarm_counts},
                "note": "Estimates from sampled history; gaps reduce coverage. Simulated and measured origins are "
                        "reported separately and never summed. Twin ledger totals are in /api/twin."}

    # ------------------------------------------------------------------ settings / admin
    @app.get("/api/settings")
    async def settings(p: Principal = Depends(require(Perm.READ))):
        import platform
        import sys
        d = {"config": json.loads(cfg.model_dump_json()), "software_version": __version__,
             "python": sys.version.split()[0], "platform": platform.platform(),
             "storage": hist.storage(), "migrations": hist.applied_migrations,
             "commissioning_evidence": "No hardware commissioning performed. Templates: docs/commissioning/",
             "production_fields": {"hydraulic_topology": "TBD — requires operator confirmation",
                                   "physical_mapping": "empty (no approved wiring)",
                                   "calibration": "none verified", "safe_state_matrix": "TBD",
                                   "retention_policy": (f"rolling {cfg.historian.retention_days} days, snapshots every "
                                                        f"{cfg.historian.recording_interval_s} s "
                                                        f"({cfg.historian.retention_provenance})")}}
        if p.can(Perm.USER_ADMIN):
            d["users"] = accounts.list_users()
        return d

    @app.post("/api/users")
    def create_user(body: UserIn, p: Principal = Depends(require(Perm.USER_ADMIN))):
        try:
            accounts.create_user(body.username, body.password, body.role, commissioning=body.commissioning,
                                 must_change=True)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            raise HTTPException(409, f"cannot create user: {e}")
        hist.event("USER_CREATED", {"username": body.username, "role": body.role.value,
                                    "commissioning": body.commissioning}, actor=p.username)
        return {"ok": True}

    @app.post("/api/users/{username}")
    def update_user(username: str, body: UserUpdateIn, p: Principal = Depends(require(Perm.USER_ADMIN))):
        if username == p.username and (body.disabled or (body.role and body.role != Role.ADMIN)):
            raise HTTPException(409, "cannot demote or disable yourself")
        cur = [u for u in accounts.list_users() if u["username"] == username]
        if not cur:
            raise HTTPException(404, "unknown user")
        if body.role is not None or body.commissioning is not None:
            accounts.set_role(username, body.role or Role(cur[0]["role"]),
                              cur[0]["commissioning"] if body.commissioning is None else body.commissioning)
        if body.disabled is not None:
            accounts.set_disabled(username, body.disabled)
        if not is_sim and (lease := svc.gate.active()) is not None and lease.username == username and \
                (body.disabled or body.commissioning is False or body.role is not None):
            svc.revoke(f"user changed by {p.username}", p.username)
        hist.event("USER_UPDATED", {"username": username} | body.model_dump(mode="json", exclude_none=True),
                   actor=p.username)
        return {"ok": True}

    @app.post("/api/users/{username}/revoke-sessions")
    def revoke_sessions(username: str, p: Principal = Depends(require(Perm.USER_ADMIN))):
        accounts.revoke_user_sessions(username, f"revoked by {p.username}")
        if not is_sim and (lease := svc.gate.active()) is not None and lease.username == username:
            svc.revoke(f"sessions revoked by {p.username}", p.username)
        hist.event("SESSIONS_REVOKED", {"username": username}, actor=p.username)
        return {"ok": True}

    # ------------------------------------------------------------------ retention hold / confirmation
    @app.get("/api/retention")
    def retention_state(p: Principal = Depends(require(Perm.READ))):
        """Retention state; while held, also the proposed cutoff and the rows it would delete."""
        v = svc.retention.view()
        if v["held"] is not None:
            v["proposal"] = svc.retention.proposal()
        return v

    @app.post("/api/retention/confirm-clock")
    def retention_confirm(body: RetentionConfirmIn, p: Principal = Depends(require(Perm.CONFIG_ADMIN))):
        """Administrator's release of a retention hold. It is separate from alarm acknowledgement and
        must echo the cutoff that was shown; a stale or different cutoff is refused with the new one."""
        from ..historian.retention import ProposalMismatch
        try:
            prop = svc.retention.confirm(p.username, body.cutoff_utc)
        except LookupError as e:
            raise HTTPException(409, str(e))
        except ProposalMismatch as e:
            raise HTTPException(409, {"error": str(e), "proposal": e.proposal})
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"released": True, "cutoff_utc": prop["cutoff_utc"], "would_delete": prop["would_delete"]}

    backup_job: dict = {"running": False, "last": None}
    backup_lock = threading.Lock()

    def _run_backup(user: str) -> None:
        try:
            hist.wait_idle()
            man = create_backup(hist.db_path, state_dir / "backups", config_id=cfg.config_id,
                                config_version=cfg.config_version,
                                reserve_bytes=cfg.historian.free_space_reserve_bytes)
            hist.event("BACKUP_CREATED", {k: v for k, v in man.items() if k != "row_counts"}, actor=user)
            backup_job["last"] = {"ok": True, "file": man["file"], "bytes": man["bytes"]}
        except Exception as e:   # refused for space, or failed: recorded, never silent
            hist.event("BACKUP_FAILED", {"error": str(e)}, actor=user)
            backup_job["last"] = {"ok": False, "error": str(e)}
        finally:
            backup_job["running"] = False

    @app.post("/api/backup", status_code=202)
    def backup(p: Principal = Depends(require(Perm.BACKUP))):
        """A one-year database takes minutes to dump: the backup runs in the background and ends in a
        BACKUP_CREATED or BACKUP_FAILED event."""
        with backup_lock:
            if backup_job["running"]:
                raise HTTPException(409, "a backup is already running")
            backup_job["running"] = True
        threading.Thread(target=_run_backup, args=(p.username,), name="api-backup", daemon=True).start()
        return {"started": True, "destination": str(state_dir / "backups")}

    @app.get("/api/backup")
    def backup_state(p: Principal = Depends(require(Perm.READ))):
        return backup_job

    # ------------------------------------------------------------------ bench (real, read-only profile)
    @app.get("/api/bench")
    async def bench_state(p: Principal = Depends(require(Perm.READ))):
        if is_sim:
            return {"available": False, "reason": "SIMULATED profile: bench writes do not exist here"}
        from ..control.bench import CONFIRMATION_PHRASE, MAX_LEASE_S, MIN_LEASE_S
        return {"available": True, "lease": svc.lease_view(), "relay_modes": svc.relay_mode_view(),
                "confirmation_phrase": CONFIRMATION_PHRASE, "duration_range_s": [MIN_LEASE_S, MAX_LEASE_S],
                "relay_outputs": [str(x) for x in cfg.all_points() if x.kind == PointKind.DO],
                "can_arm": p.can(Perm.BENCH_ARM)}

    @app.post("/api/bench/lease")
    async def bench_arm(body: BenchArmIn, p: Principal = Depends(require(Perm.BENCH_ARM))):
        from ..control.bench import BenchRefused
        try:
            lease = await run_in_threadpool(lambda: svc.arm(p, confirmation=body.confirmation,
                                                            setup_description=body.setup_description,
                                                            allowlist=body.allowlist, duration_s=body.duration_s))
        except BenchRefused as e:
            raise HTTPException(409, str(e))
        return lease.public(svc._mono())

    @app.post("/api/bench/lease/revoke")
    async def bench_revoke(p: Principal = Depends(require(Perm.READ))):
        # Any signed-in user may end a lease early: ending only stops further writes and sends nothing.
        return {"revoked": svc.revoke(f"revoked by {p.username}", p.username)}

    @app.post("/api/bench/command")
    async def bench_command(body: BenchCommandIn, p: Principal = Depends(require(Perm.BENCH_ARM))):
        if body.config_version != cfg.config_version:
            raise HTTPException(409, "configuration version mismatch")
        rec = await run_in_threadpool(lambda: svc.bench_command(p, target=body.target, desired=body.desired,
                                                                reason=body.reason, lease_id=body.lease_id,
                                                                request_id=body.request_id))
        return {"request_id": rec.command.request_id, "outcome": rec.outcome.value, "detail": rec.detail,
                "readback": rec.readback, "history": [(t.isoformat(), o.value, d) for t, o, d in rec.history],
                "note": "coil readback is not actuator proof"}

    return app
