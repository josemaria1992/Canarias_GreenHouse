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
import time
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
from ..historian.recording import slot_clock_epoch
from ..historian.trends import TrendCache, trend_buckets, trend_level
from ..historian.writer import Historian
from ..security.auth import Accounts, AuthError, Perm, Principal, RateLimiter, Role, load_or_create_secret
from ..twin.clock import SPEEDS
from .service import SimulationService

COOKIE = "aquacrop_session"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
WEB_DIR = resources.files("aquacrop") / "web"
# Bounded readers (ADR-0008, WAL): every user query ends in one short read transaction.
AUDIT_EXPORT_MAX_ROWS = 200000
ANALYTICS_MAX_DAYS = 31
ANALYTICS_MAX_ROWS_PER_POINT = 100000


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
    kind: Literal["pump", "valve", "drive", "drive_comm", "contactor", "mains", "safety"]
    ident: str
    fault: Literal["NO_RUN", "STUCK_ON", "NO_FLOW", "STUCK_CLOSED", "STUCK_OPEN", "TRIP", "WELDED", "NO_CLOSE",
                   "NO_SUPPLY", "CHAIN_OPEN", "NO_RESPONSE", "EXCEPTION", "IGNORES_WRITES"] | None


class ProcessStateIn(Body):
    target: ControllerState


class ManualOpIn(Body):
    op: dict[str, str | float | int]


class ReasonIn(Body):
    reason: str = Field(default="", max_length=200)


class RecipeDraftIn(Body):
    recipe_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,47}$")
    name: str = Field(min_length=1, max_length=80)
    body: dict
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=500)


class StockAckIn(Body):
    note: str = Field(default="", max_length=200)


class StockInvalidateIn(Body):
    reason: str = Field(min_length=1, max_length=200)


class OutletIn(Body):
    channel: int = Field(ge=1, le=64)
    outlet: int = Field(ge=1, le=16)
    opening: float = Field(ge=0.0, le=1.0)


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


class OffsitePolicyIn(Body):
    policy_hash: str = Field(min_length=16, max_length=16, pattern="^[0-9a-f]{16}$")   # from GET /api/offsite
    confirm: Literal[True]


class QuarantineConfirmIn(Body):
    proposal_id: str = Field(min_length=16, max_length=16, pattern="^[0-9a-f]{16}$")   # from GET /api/history/quarantine
    clock_checked: bool          # the administrator checked the date and time against an independent source


class QuarantineReviewIn(Body):
    quarantine_id: str = Field(min_length=3, max_length=40, pattern="^q_[0-9a-f]{16}$")
    disposition: Literal["KEEP", "RELEASE"]
    reason: str = Field(min_length=3, max_length=500)


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
    from ..historian.offsite import Busy, NotActivated, Offsite, OffsiteMonitor, PolicyMismatch
    offsite = OffsiteMonitor(cfg, state_dir, hist)
    svc.offsite = offsite

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
    SIM_ONLY = ("/api/twin", "/api/scenarios", "/api/io", "/api/controller", "/api/processes",
                "/api/stocks", "/api/recipes")                                  # and their sub-paths
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
                                  "output_type": CATALOG[d.model].output_type,
                                  "online": h.online, "consecutive_failures": h.consecutive_failures,
                                  "last_ok_utc": h.last_ok_utc.isoformat() if h.last_ok_utc else None,
                                  "last_error": h.last_error}
        alarms = list(r.alarms.open.values())
        return {"banner": svc.banner, "profile": cfg.profile.value, "config_id": cfg.config_id,
                "config_version": cfg.config_version, "software_version": __version__, "session_id": session_id,
                "timezone": cfg.site.timezone, "topology": cfg.topology, "controller": _controller_summary(r),
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

    def _controller_summary(r) -> dict:
        if getattr(r, "sup", None) is not None:
            a = r.recipes.active()
            return {"state": r._controller_state().value, "recipe": a.ref if a else None,
                    "processes": {p.pid: {"state": p.state.value, "phase": p.phase, "fault_reason": p.fault_reason,
                                          "inhibit": p.inhibit} for p in r.sup.processes}}
        c = r.controller
        return {"state": c.state.value, "phase": c.phase.value, "fault_reason": c.fault_reason, "sector": c.sector}

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
                "devices": _devices_view(), "drives": svc.drive_view(), "relay_modes": svc.relay_mode_view(),
                "bench_lease": lease,
                "storage": hist.storage() | {"ok": ok_store, "reason": why},
                "historian": {k: v for k, v in hist.health.items()}, "acquisition_task": svc.task_health(),
                "alarms": {"active": sum(o.active for o in alarms),
                           "unacknowledged": sum(not o.acknowledged for o in alarms),
                           "reset_required": sum(o.reset_required for o in alarms)},
                "freshness": _freshness(),
                "physical_writes": "BENCH LEASE ARMED (allowlisted coils and drives only)" if lease else
                                   "NONE (read-only: no bench lease armed)",
                "live_control": "UNAVAILABLE in this release"}

    # ------------------------------------------------------------------ points / twin
    def _point_meta(pid: PointId) -> dict:
        dev = cfg.device(pid.device_uid)
        m = cfg.mapping_for_point(str(pid))
        meta = {"id": str(pid), "device_uid": dev.device_uid, "bus_id": dev.bus_id, "kind": pid.kind.value,
                "channel": pid.channel, "register_address": pid.register_address,
                "label": dev.label_for(pid), "model": dev.model.value,
                "output_type": CATALOG[dev.model].output_type if pid.kind == PointKind.DO else None,
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
                           "output_type": CATALOG[d.model].output_type,
                           "output_current_limit_ma": CATALOG[d.model].output_current_limit_ma,
                           "sku": d.sku, "bus_id": d.bus_id, "address": d.address, "online": h.online,
                           "last_error": h.last_error, "points": pts})
        return {"freshness": _freshness(), "source": rt().backend.source.value if is_sim else "REAL",
                "devices": groups}

    @app.get("/api/plant")
    async def plant_view(p: Principal = Depends(require(Perm.READ))):
        """The installation as the controllers see it: every logical signal of the confirmed layout with
        its binding (point, drive adapter or UNBOUND) and the acquired sample (value, unit, quality, age,
        origin). Requested states are the command points' readback; observed states are the feedbacks.
        On a hardware profile the view is read-only and every signal stays UNBOUND until a physical
        mapping is approved (ADR-0009, ADR-0013). Twin truth is not part of this view."""
        from ..twin.installation_params import TANKS, InstallationFacts
        if is_sim and cfg.topology != "installation":
            raise HTTPException(409, "the installation view needs the installation topology (this is the demo)")
        facts = cfg.twin.facts if is_sim else InstallationFacts()
        cat = facts.signal_catalogue(extra_flows=cfg.twin.dynamics.flow_span_L_min if is_sim else ())
        samples, now = _samples(), datetime.now(timezone.utc)
        r = rt() if is_sim else None
        signals = {}
        for kind, names in cat.items():
            for name in sorted(names):
                pt = cfg.point_for_signal(name) if is_sim else None
                ad = cfg.adapter_for_signal(name) if is_sim else None    # NOT_CONFIGURED on hardware: unbound
                e = {"kind": kind, "point": pt, "binding": "POINT" if pt else "DRIVE_ADAPTER" if ad else "UNBOUND"}
                if pt:
                    pid = PointId.parse(pt)
                    s = samples.get(pt)
                    e |= {"label": cfg.device(pid.device_uid).label_for(pid),
                          "sample": None if s is None else _sample(s) | {"age_s": (now - s.acquired_utc).total_seconds()}}
                elif ad:
                    e |= {"adapter": {"adapter_id": ad.adapter_id, "kind": ad.kind,
                                      "requested": r.adapter_state.get(name) if r is not None else None,
                                      "modbus": r.drive_view(ad.adapter_id) if r is not None else None,
                                      "provenance": ad.modbus.provenance if ad.modbus else None}}
                signals[name] = e
        procs = None
        if r is not None:
            a = r.recipes.active()
            procs = {"aggregate_state": r._controller_state().value, "recipe": a.ref if a else None,
                     "processes": {x.pid: {"title": x.title, "state": x.state.value, "phase": x.phase,
                                           "fault_reason": x.fault_reason, "inhibit": x.inhibit, "next": x.next_step,
                                           "outputs_on": sorted(x.on)} for x in r.sup.processes},
                     "arbiter": dict(sorted(r.sup.arbiter.owner.items())),
                     "stocks": {k: v.view() for k, v in r.sup.stocks.items()}}
        return {"profile": cfg.profile.value, "read_only": not is_sim, "freshness": _freshness(),
                "layout": {"provenance": "OWNER_CONFIRMED (approximate where stated, 2026-10-12)",
                           "tanks_L": {t: facts.tank_capacity(t) for t in TANKS},
                           "sectors": [s.model_dump() for s in facts.sectors],
                           "main_pumps": facts.main_pumps, "return_pumps": facts.return_pumps,
                           "shared_drive": facts.shared_drive, "outlets_per_channel": facts.outlets_per_channel,
                           "channel": facts.channel.model_dump() | {"retained_L": facts.channel.retained_L,
                                                                     "capacity_L": facts.channel.capacity_L},
                           "agitators": facts.agitators},
                "mapping": ("SIMULATED proposal (configs/installation-48.yaml); not wiring" if is_sim else
                            "physical mapping empty: no approved wiring, every signal UNBOUND"),
                "signals": signals, "processes": procs,
                "alarms": [o.to_dict() for o in list(_alarm_engine().open.values())]}

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
                 "twin_overrides": s.twin_overrides} for s in svc.presets().values()]

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
        r = rt()
        return r.sup.snapshot() if getattr(r, "sup", None) is not None else r.controller.snapshot()

    # ------------------------------------------------------------------ installation processes (simulation)
    def irt():
        r = rt()
        if getattr(r, "sup", None) is None:
            raise HTTPException(409, "this configuration has no process controllers (demo topology)")
        return r

    def _ok(res):
        ok, msg = res
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True, "detail": msg}

    @app.get("/api/processes")
    async def processes(p: Principal = Depends(require(Perm.READ))):
        r = irt()
        a = r.recipes.active()
        return r.sup.snapshot() | {"recipe": r.snapshot()["recipe"], "active_recipe_body": a.body_json() if a else None,
                                   "aggregate_state": r._controller_state().value}

    @app.post("/api/processes/stop-all")
    async def processes_stop_all(body: ReasonIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        return {"results": irt().stop_all(p.username, body.reason or "stop all")}

    @app.post("/api/processes/{pid}/state")
    async def process_state(pid: str, body: ProcessStateIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        return _ok(_guard(lambda: irt().process_request(p.username, pid, body.target)))

    @app.post("/api/processes/{pid}/manual")
    async def process_manual(pid: str, body: ManualOpIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        return _ok(_guard(lambda: irt().process_manual(p.username, pid, body.op)))

    @app.post("/api/processes/{pid}/reset")
    async def process_reset(pid: str, p: Principal = Depends(require(Perm.LOCKOUT_RESET))):
        return _ok(_guard(lambda: irt().process_reset(p.username, pid)))

    @app.post("/api/stocks/{stock}/acknowledge")
    async def stock_ack(stock: str, body: StockAckIn, p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        return _ok(_guard(lambda: irt().stock_acknowledge(p.username, stock, body.note)))

    @app.post("/api/stocks/{stock}/invalidate")
    async def stock_invalidate(stock: str, body: StockInvalidateIn,
                               p: Principal = Depends(require(Perm.PROCESS_CONTROL))):
        _guard(lambda: irt().stock_invalidate(p.username, stock, body.reason))
        return {"ok": True}

    @app.post("/api/twin/stock/{stock}/prepare")
    async def twin_stock_prepare(stock: str, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        return _guard(lambda: irt().stock_prepare(p.username, stock))

    @app.post("/api/twin/outlet")
    async def twin_outlet(body: OutletIn, p: Principal = Depends(require(Perm.SIM_COMMAND))):
        _guard(lambda: irt().set_outlet(p.username, body.channel, body.outlet, body.opening))
        return {"ok": True}

    # ------------------------------------------------------------------ recipes
    def _recipe_guard(fn):
        from ..control.recipes import RecipeError

        def call():
            try:
                return fn()
            except RecipeError as e:            # a state conflict (immutable, already active), not bad input
                raise HTTPException(409, str(e))
        return _guard(call)

    @app.get("/api/recipes")
    async def recipes(p: Principal = Depends(require(Perm.READ))):
        from ..control.recipes import form_schema
        r = irt()
        a = r.recipes.active()
        return {"active": a.ref if a else None, "recipes": r.recipes.list(), "activations": r.recipes.activations,
                "form": form_schema(r.capacities), "sectors": r.sector_ids}

    @app.post("/api/recipes/draft")
    async def recipe_draft(body: RecipeDraftIn, p: Principal = Depends(require(Perm.RECIPE_EDIT))):
        from pydantic import ValidationError
        try:
            rec = _recipe_guard(lambda: irt().recipe_save_draft(p.username, body.recipe_id, body.name, body.body,
                                                                version=body.version, note=body.note))
        except ValidationError as e:
            raise HTTPException(422, [{"loc": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()])
        return rec.view()

    @app.post("/api/recipes/{recipe_id}/{version}/validate")
    async def recipe_validate(recipe_id: str, version: int, p: Principal = Depends(require(Perm.RECIPE_EDIT))):
        return _recipe_guard(lambda: irt().recipe_validate(p.username, recipe_id, version)).view()

    @app.post("/api/recipes/{recipe_id}/{version}/activate")
    async def recipe_activate(recipe_id: str, version: int, body: ReasonIn,
                              p: Principal = Depends(require(Perm.RECIPE_ACTIVATE))):
        return _recipe_guard(lambda: irt().recipe_activate(p.username, recipe_id, version, body.reason)).view()

    @app.post("/api/recipes/rollback")
    async def recipe_rollback(body: ReasonIn, p: Principal = Depends(require(Perm.RECIPE_ACTIVATE))):
        return _recipe_guard(lambda: irt().recipe_rollback(p.username, body.reason or "rollback")).view()

    # ------------------------------------------------------------------ alarms / events
    @app.get("/api/alarms")
    async def alarms(p: Principal = Depends(require(Perm.READ)), limit: int = Query(200, ge=1, le=1000)):
        hist_rows = hist.query("SELECT json FROM alarm_occurrences WHERE closed=1 ORDER BY updated_utc DESC LIMIT ?",
                               (limit,))
        reg = hist.quarantines

        def flag(d: dict) -> dict:
            q = reg.find_occurrence(d.get("occurrence_id", ""))
            return d | {"timestamp_suspect": q is not None, "quarantine_id": q.quarantine_id if q else None}
        return {"open": [flag(o.to_dict()) for o in list(_alarm_engine().open.values())],
                "history": [flag(json.loads(x[0])) for x in hist_rows]}

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
        reg = hist.quarantines
        out = [dict(r) | {"payload": json.loads(r["payload_json"])} | reg.flags("events", r["id"]) for r in rows[:limit]]
        for o in out:
            o.pop("payload_json")
            if not p.can(Perm.EXPORT_AUDIT) and o["kind"] in ("LOGIN", "LOGOUT", "REQUEST_REJECTED"):
                o["payload"] = {"redacted": True}
        return {"events": out, "truncated": len(rows) > limit, "recent_runtime": rt().events[-50:] if is_sim else []}

    @app.get("/api/commands/history")
    def command_history(p: Principal = Depends(require(Perm.READ)), limit: int = Query(200, ge=1, le=2000)):
        rows = hist.query("SELECT rowid AS seq, request_id, actor, target, desired, origin, reason, intent_utc, outcome, "
                          "detail, readback, outcome_utc, backend FROM commands ORDER BY intent_utc DESC LIMIT ?", (limit,))
        reg = hist.quarantines
        return [dict(r) | reg.flags("commands", r["seq"]) for r in rows]

    # ------------------------------------------------------------------ trends / export / analytics
    trend_cache = TrendCache()       # final buckets of year-long charts (historian/trends.py)

    @app.get("/api/trends")
    def trends(p: Principal = Depends(require(Perm.READ)), points: str = Query(..., max_length=2000),
               start: str | None = None, end: str | None = None, limit: int = Query(20000, ge=1, le=100000),
               basis: str | None = Query(None, pattern="^(WALL|SIMULATION_TIME|LEGACY_UNKNOWN)$"),
               max_points: int = Query(2000, ge=100, le=20000)):
        # `basis` selects one series when the optional simulation-time recorder is also on. A point with
        # more than `max_points` samples in the range is answered aggregated (mean, min, max per bucket of
        # 10 min, 1 h or 1 day: the smallest that fits), so a year-long chart covers the whole year; every
        # sample stays available through the paged CSV export.
        s, e = _range(start, end)
        ids = [x for x in points.split(",") if x][:16]
        span = (datetime.fromisoformat(e) - datetime.fromisoformat(s)).total_seconds()
        series, truncated, aggregated = {}, False, {}
        reg = hist.quarantines
        for pid in ids:
            n = hist.query("SELECT count(*) FROM samples WHERE point_id=? AND acquired_utc>=? AND acquired_utc<=?",
                           (pid, s, e))[0][0] if not basis else \
                hist.query("SELECT count(*) FROM samples s JOIN cycles c ON c.cycle_pk=s.cycle_pk AND c.cadence_basis=? "
                           "WHERE s.point_id=? AND s.acquired_utc>=? AND s.acquired_utc<=?", (basis, pid, s, e))[0][0]
            if n > max_points:
                # buckets keep runs, recorders and suspect stamps apart; without `basis` they are drawn from
                # the wall-clock snapshots only, so `samples` counts what the chart holds
                level = trend_level(span, max_points)
                series[pid] = trend_buckets(hist, pid, s, e, level, basis, cache=trend_cache)
                aggregated[pid] = {"bucket": level[0], "bucket_s": level[1], "buckets": len(series[pid]),
                                   "samples": sum(r[13] for r in series[pid]), "samples_in_range": n,
                                   "cadence_basis": [basis] if basis else ["WALL", "LEGACY_UNKNOWN"]}
                continue
            rows = hist.query("SELECT s.id, s.acquired_utc, s.value, s.raw_value, s.quality, s.origin, s.unit, s.sim_s, "
                              "s.sim_run_id, c.cadence_s, c.cadence_basis, c.cycle_id FROM samples s JOIN cycles c ON "
                              "c.cycle_pk=s.cycle_pk WHERE s.point_id=? AND s.acquired_utc>=? AND s.acquired_utc<=? "
                              "AND (? IS NULL OR c.cadence_basis=?) ORDER BY s.acquired_utc LIMIT ?",
                              (pid, s, e, basis, basis, limit + 1))
            truncated |= len(rows) > limit
            out, prev, prev_q = [], None, None
            for r in rows[:limit]:
                q = reg.find("samples", r["id"])
                gap = False
                if prev is not None:
                    if r["sim_run_id"] != prev["sim_run_id"] or r["origin"] != prev["origin"]:
                        gap = True
                    elif q is not prev_q:      # never draw a line between suspect and trusted stamps
                        gap = True
                    elif r["cadence_basis"] != prev["cadence_basis"]:
                        gap = True
                    elif r["cadence_basis"] == "SIMULATION_TIME" and r["sim_s"] is not None and prev["sim_s"] is not None:
                        gap = (r["sim_s"] - prev["sim_s"]) > 1.5 * max(r["cadence_s"] or 0, prev["cadence_s"] or 0)
                    else:
                        dt = (datetime.fromisoformat(r["acquired_utc"]) - datetime.fromisoformat(prev["acquired_utc"]))
                        gap = dt.total_seconds() > 2.5 * (r["cadence_s"] or cfg.historian.acquisition_interval_s)
                out.append([r["acquired_utc"], r["value"], r["raw_value"], r["quality"], r["origin"], r["unit"],
                            r["sim_s"], r["sim_run_id"], gap, q is not None, slot_clock_epoch(r["cycle_id"]),
                            None, None, None, None])
                prev, prev_q = r, q
            series[pid] = out
        ev = hist.query("SELECT utc, kind, actor FROM events WHERE utc>=? AND utc<=? AND kind IN "
                        "('CONTROLLER_TRANSITION','ALARM_RAISED','ALARM_CLEARED','RUN_STARTED','RUN_RESET',"
                        "'OVERRIDE_SET','ACTUATOR_FAULT','DEVICE_FAULT') ORDER BY utc LIMIT 2000", (s, e))
        return {"start": s, "end": e, "columns": ["acquired_utc", "value", "raw", "quality", "origin", "unit",
                                                  "sim_s", "sim_run_id", "gap_before", "timestamp_suspect",
                                                  "clock_epoch", "min", "max", "samples", "valid_samples"],
                "series": series, "events": [list(x) for x in ev], "truncated": truncated, "limit": limit,
                "aggregated": aggregated, "max_points": max_points,
                "aggregation_note": "An aggregated point is a bucket starting at acquired_utc: value is the mean of "
                                    "its GOOD or OVERRIDDEN samples, min and max their range; quality GOOD (all "
                                    "samples valid), PARTIAL (some) or UNAVAILABLE (none). Every sample: the CSV export.",
                "display_timezone": cfg.site.timezone}

    @app.get("/api/export.csv")
    def export_csv(p: Principal = Depends(require(Perm.READ)), points: str | None = None, start: str | None = None,
                   end: str | None = None, after_id: int = 0, limit: int = Query(100000, ge=1, le=200000),
                   display_tz: str | None = None):
        # Bounded reader: at most 200 000 rows (one short read transaction) per page; continue with after_id.
        s, e = _range(start, end, 60)
        zone = ZoneInfo(display_tz) if display_tz else tz
        ids = [x for x in (points or "").split(",") if x]
        sql = ("SELECT id, acquired_utc, point_id, raw_value, raw_type, raw_unit, value, unit, quality, reason, origin, "
               "calibration_ref, config_version, source_profile, device_uid, bus_id, session_id, sim_run_id, sim_s, "
               "override_json, cycle_pk FROM samples WHERE acquired_utc>=? AND acquired_utc<=? AND id>?")
        args: list = [s, e, after_id]
        if ids:
            sql += f" AND point_id IN ({','.join('?' * len(ids))})"
            args += ids
        sql += " ORDER BY id LIMIT ?"
        args.append(limit + 1)
        rows = hist.query(sql, tuple(args))
        more = len(rows) > limit
        rows = rows[:limit]
        reg = hist.quarantines

        def gen():
            buf = io.StringIO()
            w = csv.writer(buf)
            if reg.items:
                w.writerow(["# timestamp_suspect=SUSPECT marks rows whose UTC stamps were quarantined (written while the "
                            "clock was wrong); values and stamps are as recorded"])
            w.writerow(["# AquaCrop export", f"range_utc={s}..{e}", f"display_tz={zone.key}",
                        f"config={cfg.config_id}@{cfg.config_version}", f"exported_by={p.username}"])
            w.writerow(["id", "acquired_utc", "acquired_local", "local_tz_abbrev", "point_id", "raw_value", "raw_type",
                        "raw_unit", "value", "unit", "quality", "reason", "origin", "calibration_ref",
                        "config_version", "source_profile", "device_uid", "bus_id", "session_id", "sim_run_id",
                        "sim_s", "override", "snapshot_seq", "timestamp_suspect", "quarantine_id"])
            for r in rows:
                loc = datetime.fromisoformat(r["acquired_utc"]).astimezone(zone)
                vals = [r["id"], r["acquired_utc"], loc.isoformat(), loc.tzname()] + [r[k] for k in (
                    "point_id", "raw_value", "raw_type", "raw_unit", "value", "unit", "quality", "reason", "origin",
                    "calibration_ref", "config_version", "source_profile", "device_uid", "bus_id", "session_id",
                    "sim_run_id", "sim_s", "override_json")]
                q = reg.find("samples", r["id"])
                vals += [r["cycle_pk"], "SUSPECT" if q else "", q.quarantine_id if q else ""]
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
        cap = AUDIT_EXPORT_MAX_ROWS   # bounded reader; narrow the range for more
        rows = hist.query("SELECT 'event' AS type, utc AS t, kind AS what, actor, payload_json AS detail, id AS seq "
                          "FROM events WHERE utc>=? AND utc<=? UNION ALL SELECT 'command', intent_utc, target||'='||"
                          "desired||' '||outcome, actor, coalesce(detail,''), rowid FROM commands WHERE intent_utc>=? "
                          "AND intent_utc<=? ORDER BY 2 LIMIT ?", (s, e, s, e, cap + 1))
        reg = hist.quarantines
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["type", "utc", "what", "actor", "detail", "seq", "timestamp_suspect", "quarantine_id"])
        for r in rows[:cap]:
            q = reg.find("events" if r["type"] == "event" else "commands", r["seq"])
            w.writerow([_escape_csv(v) for v in list(r)] + ["SUSPECT" if q else "", q.quarantine_id if q else ""])
        if len(rows) > cap:
            w.writerow([f"# TRUNCATED at {cap} rows; narrow the time range"])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=aquacrop-audit.csv"})

    @app.get("/api/analytics")
    def analytics(p: Principal = Depends(require(Perm.READ)), start: str | None = None, end: str | None = None):
        """Runtime / delivered-volume estimates per origin with coverage disclosure. Origins are never summed.
        Bounded reader: at most ANALYTICS_MAX_DAYS per request and ANALYTICS_MAX_ROWS_PER_POINT rows a point."""
        s, e = _range(start, end, 60)
        if datetime.fromisoformat(e) - datetime.fromisoformat(s) > timedelta(days=ANALYTICS_MAX_DAYS):
            raise HTTPException(422, f"range longer than {ANALYTICS_MAX_DAYS} days; request it in parts")
        out, truncated = {}, False
        for m in cfg.virtual_mapping:
            if m.signal.endswith(".running") or m.signal.endswith("flow_transmitter"):
                rows = hist.query("SELECT acquired_utc, value, quality, origin FROM samples WHERE point_id=? AND "
                                  "acquired_utc>=? AND acquired_utc<=? ORDER BY acquired_utc LIMIT ?",
                                  (m.point, s, e, ANALYTICS_MAX_ROWS_PER_POINT + 1))
                truncated |= len(rows) > ANALYTICS_MAX_ROWS_PER_POINT
                rows = rows[:ANALYTICS_MAX_ROWS_PER_POINT]
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
                "truncated": truncated,
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

    # ------------------------------------------------------------------ timestamp quarantine (ADR-0011)
    @app.get("/api/history/quarantine")
    def quarantine_state(p: Principal = Depends(require(Perm.READ))):
        """Quarantines in force and, when records are stamped later than the current clock, the proposal
        an administrator can confirm. Read-only."""
        from ..historian.quarantine import propose, review_due
        prop = hist.read(lambda c: propose(c, hist.quarantines, wall_s=time.time()))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg.historian.retention_days)).isoformat()
        return {"clock_epoch": hist.quarantines.clock_epoch, "quarantines": hist.quarantines.view(),
                "proposal": prop, "recorder": svc.recorder.health(), "retention_held": svc.retention.held,
                "review_due": review_due(hist.quarantines, cutoff), "retention_days": cfg.historian.retention_days}

    @app.post("/api/history/quarantine")
    def quarantine_confirm(body: QuarantineConfirmIn, p: Principal = Depends(require(Perm.CONFIG_ADMIN))):
        """Administrator's audited quarantine of records with future-dated stamps: flags them, starts a new
        clock epoch so recording resumes, and holds retention. Deletes and rewrites nothing."""
        from ..historian.quarantine import QuarantineMismatch, confirm
        try:
            q = confirm(hist, body.proposal_id, actor=p.username, wall_s=time.time(), clock_checked=body.clock_checked)
        except LookupError as e:
            raise HTTPException(409, str(e))
        except QuarantineMismatch as e:
            raise HTTPException(409, {"error": str(e), "proposal": e.proposal})
        except ValueError as e:
            raise HTTPException(422, str(e))
        svc.retention.hold_for_quarantine(q.quarantine_id)
        svc.recorder.rebase(f"timestamp quarantine {q.quarantine_id}")
        return {"quarantined": q.view(), "retention_held": svc.retention.held}

    @app.post("/api/history/quarantine/review")
    def quarantine_review(body: QuarantineReviewIn, p: Principal = Depends(require(Perm.CONFIG_ADMIN))):
        """Administrator's audited disposition of a quarantine's records (brief §9): KEEP keeps them until a
        later review; RELEASE lets rolling retention remove them once the quarantine's own time is older
        than the window. Deletes nothing now, and does not release a retention hold."""
        from ..historian.quarantine import review
        try:
            q = review(hist, body.quarantine_id, body.disposition, actor=p.username, reason=body.reason)
        except LookupError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"quarantine": q.view(), "retention_held": svc.retention.held}

    backup_job: dict = {"running": False, "last": None}
    backup_lock = threading.Lock()

    def _run_backup(user: str) -> None:
        h = cfg.historian
        if cfg.offsite_backup.enabled:      # SD card only (verified identity); never the eMMC (ADR-0012)
            try:
                with Offsite(cfg, state_dir, hist.db_path) as off:
                    res = off.backup(actor=user)
                b = res.get("backup", res)
                backup_job["last"] = ({"ok": True, "file": b["file"], "bytes": b["bytes"]} if b.get("ok") else
                                      {"ok": False, "error": f"{b.get('code')}: {b.get('error')}"})
            except Exception as e:     # Busy, NotActivated or an error: recorded, never silent
                hist.event("BACKUP_FAILED", {"error": str(e), "destination": "sd_card"}, actor=user)
                backup_job["last"] = {"ok": False, "error": str(e)}
            finally:
                backup_job["running"] = False
            return
        try:
            hist.wait_idle()
            man = create_backup(hist.db_path, state_dir / "backups", config_id=cfg.config_id,
                                config_version=cfg.config_version, reserve_bytes=h.free_space_reserve_bytes,
                                wal_abort_bytes=h.wal_reader_abort_bytes, max_seconds=h.backup_max_seconds)
            hist.event("BACKUP_CREATED", {k: v for k, v in man.items() if k not in ("row_counts", "fingerprints")},
                       actor=user)
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
        return {"started": True, "destination": "sd_card" if cfg.offsite_backup.enabled else str(state_dir / "backups")}

    @app.get("/api/backup")
    def backup_state(p: Principal = Depends(require(Perm.READ))):
        """Local backups, the latest restore drill and the off-device copy (an owner decision)."""
        from ..historian.backup import latest_drill, list_backups
        dest = state_dir / "backups"
        local = [b for b in list_backups(dest) if b.name.endswith(".sql.gz")] if dest.exists() else []
        drill = latest_drill(dest / "restore-drills")
        due = None
        if drill and drill.get("utc"):
            age = datetime.now(timezone.utc) - datetime.fromisoformat(drill["utc"])
            due = age.days >= cfg.historian.restore_drill_interval_days
        return backup_job | {
            "destination": str(dest), "local_count": len(local), "keep": cfg.historian.backup_keep,
            "local_bytes": sum(b.stat().st_size for b in local), "newest": local[-1].name if local else None,
            "restore_drill": drill, "restore_drill_due": True if drill is None else due,
            "restore_drill_interval_days": cfg.historian.restore_drill_interval_days,
            "offsite_enabled": cfg.offsite_backup.enabled,
            "off_device": ("SD card plus encrypted cloud copies (see Off-device backups)" if cfg.offsite_backup.enabled
                           else "NOT ACTIVATED: SD-card and encrypted OneDrive backups are implemented but wait for the "
                                "owner to confirm the card and the account/folder; until then these eMMC backups do "
                                "not survive loss of this device")}

    @app.get("/api/offsite")
    def offsite_state(p: Principal = Depends(require(Perm.READ))):
        """SD-card backups, upload queue, cloud copies, restore drills and the copy policy (ADR-0012).
        Read from state files; nothing is contacted."""
        return offsite.view()

    @app.post("/api/offsite/policy")
    def offsite_policy(body: OffsitePolicyIn, p: Principal = Depends(require(Perm.CONFIG_ADMIN))):
        """An administrator confirms the configured copy policy; only then is pruning enabled. A later
        change to the policy needs a new confirmation."""
        try:
            entry = Offsite(cfg, state_dir, hist.db_path).confirm_policy(p.username, body.policy_hash)
        except PolicyMismatch as e:
            raise HTTPException(409, {"error": str(e), "policy": e.policy})
        offsite.record(entry)
        return {"confirmed": entry["payload"]}

    # ------------------------------------------------------------------ bench (real, read-only profile)
    @app.get("/api/bench")
    async def bench_state(p: Principal = Depends(require(Perm.READ))):
        if is_sim:
            return {"available": False, "reason": "SIMULATED profile: bench writes do not exist here"}
        from ..control.bench import CONFIRMATION_PHRASE, MAX_LEASE_S, MIN_LEASE_S
        return {"available": True, "lease": svc.lease_view(), "relay_modes": svc.relay_mode_view(),
                "confirmation_phrase": CONFIRMATION_PHRASE, "duration_range_s": [MIN_LEASE_S, MAX_LEASE_S],
                "relay_outputs": [str(x) for x in cfg.all_points() if x.kind == PointKind.DO],
                "drive_targets": [a.target for a in cfg.drive_adapters if a.kind == "MODBUS_RTU"],
                "drives": svc.drive_view(), "can_arm": p.can(Perm.BENCH_ARM)}

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
                "note": "coil readback and drive echo are not actuator proof"}

    return app
