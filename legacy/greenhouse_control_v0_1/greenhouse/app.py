"""Password-protected local dashboard and JSON API."""
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .config import Config
from .controller import Controller

security = HTTPBasic()
WEB = Path(__file__).resolve().parent.parent / "static" / "index.html"


class ModeBody(BaseModel):
    mode: str


class BoolBody(BaseModel):
    on: bool


class AnalogBody(BaseModel):
    value: int = Field(ge=0, le=25000)


class FlagBody(BaseModel):
    active: bool


class ThresholdBody(BaseModel):
    minimum: Optional[int] = Field(default=None, ge=0, le=65535)
    maximum: Optional[int] = Field(default=None, ge=0, le=65535)


class AutoBody(BaseModel):
    run_seconds: int = Field(ge=1, le=3600)
    pause_seconds: int = Field(ge=1, le=3600)


def create_app(cfg: Config | None = None):
    cfg = cfg or Config.from_env()
    cfg.validate()
    control = Controller(cfg)

    @asynccontextmanager
    async def lifespan(app):
        await control.start()
        try:
            yield
        finally:
            await control.stop()

    app = FastAPI(title="AquaCrop Greenhouse Control", version="0.1.0", lifespan=lifespan)
    app.state.controller = control

    def auth(credentials: HTTPBasicCredentials = Depends(security)):
        correct_user = secrets.compare_digest(credentials.username.encode(), cfg.username.encode())
        correct_pass = secrets.compare_digest(credentials.password.encode(), cfg.password.encode())
        if not (correct_user and correct_pass):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})
        return credentials.username

    def apply(coro):
        return coro

    def translate_error(exc):
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, (ValueError, IOError, ConnectionError)):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise exc

    @app.get("/", include_in_schema=False)
    def home(user=Depends(auth)):
        return FileResponse(WEB, headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def state(user=Depends(auth)):
        return control.state()

    @app.get("/api/history")
    def history(limit: int = 250, user=Depends(auth)):
        return control.storage.samples(min(max(limit, 1), 20000))

    @app.get("/api/events")
    def events(limit: int = 80, user=Depends(auth)):
        return control.storage.events(min(max(limit, 1), 1000))

    @app.get("/api/export.csv")
    def export(limit: int = 20000, user=Depends(auth)):
        return PlainTextResponse(control.storage.export_csv(min(max(limit, 1), 20000)),
                                 media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="aquacrop_samples.csv"'})

    @app.post("/api/mode")
    async def mode(body: ModeBody, user=Depends(auth)):
        try:
            return await control.set_mode(body.mode, user)
        except Exception as exc:
            translate_error(exc)

    @app.post("/api/relay/{channel}")
    async def relay(channel: int, body: BoolBody, user=Depends(auth)):
        try:
            return await control.set_relay(channel-1, body.on, user)
        except Exception as exc:
            translate_error(exc)

    @app.post("/api/sim/input/{channel}")
    async def sim_input(channel: int, body: BoolBody, user=Depends(auth)):
        try:
            return await control.set_input(channel-1, body.on, user)
        except Exception as exc:
            translate_error(exc)

    @app.post("/api/sim/analog/{channel}")
    async def sim_analog(channel: int, body: AnalogBody, user=Depends(auth)):
        try:
            return await control.set_analog(channel-1, body.value, user)
        except Exception as exc:
            translate_error(exc)

    @app.post("/api/sim/flag/{name}")
    async def flag(name: str, body: FlagBody, user=Depends(auth)):
        try:
            return await control.set_flag(name, body.active, user)
        except Exception as exc:
            translate_error(exc)

    @app.post("/api/threshold/{channel}")
    async def threshold(channel: int, body: ThresholdBody, user=Depends(auth)):
        try:
            return await control.set_threshold(channel-1, body.minimum, body.maximum, user)
        except Exception as exc:
            translate_error(exc)

    @app.post("/api/auto/config")
    async def auto_config(body: AutoBody, user=Depends(auth)):
        try:
            return await control.set_auto_config(body.run_seconds, body.pause_seconds, user)
        except Exception as exc:
            translate_error(exc)

    return app
