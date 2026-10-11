"""Virtual module backend. Imports nothing serial; cannot emit a physical frame.

Signal precedence for every AI/DI point (highest first):
    1. explicit override (operator or scenario; owner, reason, base value recorded)
    2. base source: IO_TEST injection table, or TWIN observation via the virtual mapping
DO coils hold the requested state. In TWIN mode a mapped DO drives the plant actuator; in IO_TEST
mode process control is inactive and coils drive nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..config.catalog import CATALOG, Capability
from ..config.schema import InstallationConfig
from ..domain.model import (AcquisitionResult, DeviceHealth, Origin, OutputCommand, Outcome, PointId,
                            PointKind, SignalSource, WriteResult, utcnow)
from .sampling import AcqContext, make_sample


@dataclass
class Override:
    point: str
    raw: float | int | bool
    owner: str
    reason: str
    set_utc: datetime
    expires_sim_s: float | None = None   # expiry is in simulation time so replay is deterministic
    kind: str = "VALUE"   # VALUE (operator/scenario) or FROZEN (scenario-frozen observation)

    def describe(self, base) -> dict:
        return {"owner": self.owner, "reason": self.reason, "set_utc": self.set_utc.isoformat(),
                "kind": self.kind, "base_raw": base, "override_raw": self.raw,
                "expires_sim_s": self.expires_sim_s}


@dataclass
class VirtualState:
    coils: dict[str, bool] = field(default_factory=dict)
    injections: dict[str, float | int | bool] = field(default_factory=dict)
    overrides: dict[str, Override] = field(default_factory=dict)
    device_faults: dict[str, str] = field(default_factory=dict)   # device_uid -> "TIMEOUT"


class VirtualBackend:
    physical = False

    def __init__(self, cfg: InstallationConfig, *, observations: Callable[[], dict[str, bool | int]],
                 actuate: Callable[[str, bool], None], context: Callable[[], AcqContext],
                 source: SignalSource = SignalSource.TWIN):
        if any(b.kind != "virtual" for b in cfg.buses):
            raise ValueError("VirtualBackend only serves virtual buses")
        self.cfg = cfg
        self._observations = observations
        self._actuate = actuate
        self._context = context
        self.source = source
        self.state = VirtualState()
        self._health = {d.device_uid: DeviceHealth(d.device_uid, d.bus_id) for d in cfg.devices}
        for p in cfg.all_points():
            if p.kind == PointKind.DO:
                self.state.coils[str(p)] = False
            else:
                self.state.injections[str(p)] = False if p.kind == PointKind.DI else 0

    # ------------------------------------------------------------------ contract
    def capabilities(self, device_uid: str) -> frozenset[Capability]:
        return CATALOG[self.cfg.device(device_uid).model].capabilities

    def _base_raw(self, pid: PointId, obs: dict) -> float | int | bool | None:
        if self.source == SignalSource.IO_TEST:
            return self.state.injections[str(pid)]
        m = self.cfg.mapping_for_point(str(pid))
        if m is None:
            return None  # UNMAPPED in twin mode: visibly unknown, not a fictional reading
        return obs.get(m.signal)

    def _fail(self, device_uid: str, error: str, kinds: tuple[PointKind, ...]) -> AcquisitionResult:
        dev = self.cfg.device(device_uid)
        ctx, now = self._context(), utcnow()
        h = self._health[device_uid]
        h.online, h.consecutive_failures = False, h.consecutive_failures + 1
        h.last_error, h.last_error_utc = error, now
        samples = tuple(make_sample(self.cfg, p, None, acquired=now, ctx=ctx, origin=Origin.SIMULATED, reason=error)
                        for p in dev.point_ids() if p.kind in kinds)
        return AcquisitionResult(device_uid, dev.bus_id, False, now, samples, error)

    def read_inputs(self, device_uid: str) -> AcquisitionResult:
        dev = self.cfg.device(device_uid)
        if self.state.device_faults.get(device_uid) == "TIMEOUT":
            return self._fail(device_uid, "virtual RTU timeout (scenario)", (PointKind.AI, PointKind.DI))
        ctx, now = self._context(), utcnow()
        obs = self._observations() if self.source == SignalSource.TWIN else {}
        samples = []
        for p in dev.point_ids():
            if p.kind == PointKind.DO:
                continue
            base = self._base_raw(p, obs)
            ov = self.state.overrides.get(str(p))
            if ov is not None:
                samples.append(make_sample(self.cfg, p, ov.raw, acquired=now, ctx=ctx, origin=Origin.SIMULATED,
                                           override=ov.describe(base), reason=f"override: {ov.reason}"))
            elif base is None:
                samples.append(make_sample(self.cfg, p, None, acquired=now, ctx=ctx, origin=Origin.SIMULATED,
                                           reason="UNMAPPED"))
            else:
                samples.append(make_sample(self.cfg, p, base, acquired=now, ctx=ctx, origin=Origin.SIMULATED))
        self._ok(device_uid, now)
        return AcquisitionResult(device_uid, dev.bus_id, True, now, tuple(samples))

    def read_outputs(self, device_uid: str) -> AcquisitionResult:
        dev = self.cfg.device(device_uid)
        if self.state.device_faults.get(device_uid) == "TIMEOUT":
            return self._fail(device_uid, "virtual RTU timeout (scenario)", (PointKind.DO,))
        ctx, now = self._context(), utcnow()
        samples = tuple(make_sample(self.cfg, p, self.state.coils[str(p)], acquired=now, ctx=ctx,
                                    origin=Origin.SIMULATED)
                        for p in dev.point_ids() if p.kind == PointKind.DO)
        self._ok(device_uid, now)
        return AcquisitionResult(device_uid, dev.bus_id, True, now, samples)

    def write_output(self, command: OutputCommand) -> WriteResult:
        pid = PointId.parse(command.target)
        if pid.kind != PointKind.DO or command.target not in self.state.coils:
            return WriteResult(Outcome.REJECTED, None, "target is not a configured DO point")
        if Capability.WRITE_SINGLE_COIL not in self.capabilities(pid.device_uid):
            return WriteResult(Outcome.REJECTED, None, "device lacks write capability")
        if self.state.device_faults.get(pid.device_uid) == "TIMEOUT":
            # The request may or may not have reached the module: never invent success or failure.
            return WriteResult(Outcome.OUTCOME_UNKNOWN, None, "virtual RTU timeout during write (scenario)")
        self.state.coils[command.target] = command.desired
        if self.source == SignalSource.TWIN:
            m = self.cfg.mapping_for_point(command.target)
            if m is not None:
                self._actuate(m.signal, command.desired)
        readback = self.state.coils[command.target]
        if readback == command.desired:
            return WriteResult(Outcome.READBACK_CONFIRMED, readback, "virtual coil readback")
        return WriteResult(Outcome.FAILED, readback, "readback mismatch")

    def health(self, device_uid: str) -> DeviceHealth:
        return self._health[device_uid]

    def close(self) -> None:  # nothing to release; deliberately issues no output changes
        pass

    # ------------------------------------------------------------------ virtual-only controls
    def _ok(self, device_uid: str, now: datetime) -> None:
        h = self._health[device_uid]
        h.online, h.consecutive_failures, h.last_ok_utc = True, 0, now

    def inject(self, point: str, raw: float | int | bool) -> None:
        pid = PointId.parse(point)
        if point not in self.state.injections:
            raise KeyError(point)
        if pid.kind == PointKind.DI:
            raw = bool(raw)
        else:
            if isinstance(raw, bool) or not 0 <= float(raw) <= 65535:
                raise ValueError("AI raw register value must be 0..65535")
            raw = int(round(float(raw)))
        self.state.injections[point] = raw

    def set_override(self, ov: Override) -> None:
        pid = PointId.parse(ov.point)
        if pid.kind == PointKind.DO:
            raise ValueError("overrides apply to observations (AI/DI), not to output commands")
        if ov.point not in self.state.injections:
            raise KeyError(ov.point)
        self.state.overrides[ov.point] = ov

    def clear_override(self, point: str) -> bool:
        return self.state.overrides.pop(point, None) is not None

    def current_base_raw(self, point: str):
        pid = PointId.parse(point)
        obs = self._observations() if self.source == SignalSource.TWIN else {}
        return self._base_raw(pid, obs)
