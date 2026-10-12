"""Validated, versioned installation configuration (inventory + profile + mappings).

The configuration is the only source of point identities. Point IDs are derived from
device_uid + kind + channel; there are no global AI1..AI16 arrays anywhere in the code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.model import AccessPolicy, PointId, PointKind
from ..twin.params import TwinParams
from .catalog import CATALOG, AIMode, AIVariant, ModelId

SCHEMA_VERSION = 1
UNICAST_MIN, UNICAST_MAX = 1, 247  # 248-255 are reserved by Modbus even if vendor tables allow them


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SerialSettings(_Strict):
    baudrate: int = Field(default=9600, ge=1200, le=115200)
    parity: Literal["N", "E", "O"] = "N"
    bytesize: Literal[7, 8] = 8
    stopbits: Literal[1, 2] = 1
    timeout_s: float = Field(default=0.5, gt=0, le=5)
    read_retries: int = Field(default=1, ge=0, le=3)


class BusConfig(_Strict):
    bus_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,31}$")
    kind: Literal["virtual", "rtu"]
    port: str | None = None
    port_evidence: str | None = None  # how the terminal mapping was verified, e.g. "udev by-id 2026-…"
    serial: SerialSettings = SerialSettings()
    poll_interval_s: float = Field(default=3.0, ge=0.2, le=3600)

    @model_validator(mode="after")
    def _port(self) -> "BusConfig":
        if self.kind == "rtu" and not self.port:
            raise ValueError(f"bus {self.bus_id}: rtu bus requires an explicit port")
        if self.kind == "virtual" and self.port:
            raise ValueError(f"bus {self.bus_id}: virtual bus must not name a serial port")
        return self


class ChannelLabel(_Strict):
    label: str | None = Field(default=None, max_length=80)


class AIChannel(ChannelLabel):
    mode: AIMode = AIMode.UNKNOWN
    scaling: str | None = None  # scaling profile id


class DeviceConfig(_Strict):
    device_uid: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,47}$")
    bus_id: str
    address: int
    model: ModelId
    sku: str
    hardware_variant: AIVariant = AIVariant.UNKNOWN
    revision_evidence: str | None = None
    firmware_evidence: str | None = None
    label: str | None = Field(default=None, max_length=80)
    ai: dict[int, AIChannel] = Field(default_factory=dict)
    di: dict[int, ChannelLabel] = Field(default_factory=dict)
    do: dict[int, ChannelLabel] = Field(default_factory=dict)

    @field_validator("address")
    @classmethod
    def _unicast(cls, v: int) -> int:
        if not UNICAST_MIN <= v <= UNICAST_MAX:
            raise ValueError(f"address {v} outside standard unicast range 1-247")
        return v

    @model_validator(mode="after")
    def _model_facts(self) -> "DeviceConfig":
        spec = CATALOG[self.model]
        if self.sku != spec.sku:
            raise ValueError(f"{self.device_uid}: SKU {self.sku} does not match {spec.vendor_name} ({spec.sku})")
        for kind, chans, n in (("ai", self.ai, spec.ai), ("di", self.di, spec.di), ("do", self.do, spec.do)):
            bad = [c for c in chans if not 1 <= c <= n]
            if bad:
                raise ValueError(f"{self.device_uid}: {kind} channels {bad} not present on {spec.vendor_name}")
        if spec.ai == 0 and self.hardware_variant != AIVariant.UNKNOWN:
            raise ValueError(f"{self.device_uid}: hardware_variant applies to AI modules only")
        return self

    def point_ids(self) -> list[PointId]:
        spec = CATALOG[self.model]
        out: list[PointId] = []
        for kind, n in ((PointKind.AI, spec.ai), (PointKind.DI, spec.di), (PointKind.DO, spec.do)):
            out += [PointId(self.device_uid, kind, ch) for ch in range(1, n + 1)]
        return out

    def ai_channel(self, ch: int) -> AIChannel:
        return self.ai.get(ch, AIChannel())

    def label_for(self, pid: PointId) -> str | None:
        table = {PointKind.AI: self.ai, PointKind.DI: self.di, PointKind.DO: self.do}[pid.kind]
        c = table.get(pid.channel)
        return c.label if c else None


class ScalingProfile(_Strict):
    profile_id: str
    version: int = Field(ge=1)
    status: Literal["DEMO", "PROPOSED", "VERIFIED"]
    input_unit: Literal["uA", "mV", "code"]
    raw_low: float
    raw_high: float
    eng_low: float
    eng_high: float
    unit: str
    valid_raw_min: float | None = None  # outside => INVALID quality (raw retained, never clamped)
    valid_raw_max: float | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def _denominator(self) -> "ScalingProfile":
        if self.raw_high == self.raw_low:
            raise ValueError(f"scaling {self.profile_id}: raw_high == raw_low (zero denominator)")
        return self

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"

    def scale(self, raw: float) -> float:
        return self.eng_low + (raw - self.raw_low) * (self.eng_high - self.eng_low) / (self.raw_high - self.raw_low)

    def raw_valid(self, raw: float) -> bool:
        if self.valid_raw_min is not None and raw < self.valid_raw_min:
            return False
        if self.valid_raw_max is not None and raw > self.valid_raw_max:
            return False
        return True


class Mapping(_Strict):
    point: str
    signal: str
    note: str | None = None


class AlarmRule(_Strict):
    rule_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,47}$")
    kind: Literal["HIGH", "LOW", "DI_ACTIVE"]
    point: str
    threshold: float | None = None
    hysteresis: float = Field(default=0.0, ge=0)
    debounce_s: float = Field(default=0.0, ge=0)
    severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING"
    latch: bool = False
    provenance: Literal["DEMO", "PROPOSED", "VERIFIED"] = "DEMO"

    @model_validator(mode="after")
    def _thr(self) -> "AlarmRule":
        if self.kind in ("HIGH", "LOW") and self.threshold is None:
            raise ValueError(f"alarm {self.rule_id}: {self.kind} needs a threshold")
        return self


RECORDING_INTERVALS_S = (60, 300)
GIB = 1024**3


class HistorianSettings(_Strict):
    """What the historian records periodically and how long it keeps it (owner requirements of
    2026-10-12, ADR-0008). Acquisition (each bus's poll_interval_s), alarm evaluation, control and
    dashboard updates keep their own timing; events, commands and alarm transitions are written when
    they happen, independent of the recording interval."""
    db_path: str = "var/aquacrop.sqlite3"
    acquisition_interval_s: float = Field(default=3.0, ge=0.2)    # display hint only; buses own their poll rate
    # Complete snapshot of every configured point (unchanged values and quality included). 60 s is the
    # owner's active setting; 300 s is allowed but only on the owner's explicit request.
    recording_interval_s: int = 60
    # Simulation only: snapshot stride in simulated time. Unset = recording_interval_s.
    sim_history_stride_s: float | None = Field(default=None, gt=0)
    sim_history_min_wall_interval_s: float = Field(default=0.5, gt=0)  # caps write rate at high speed
    retention_days: int = Field(default=365, ge=365)   # rolling window, owner-approved 2026-10-12
    retention_provenance: Literal["DEMO", "APPROVED"] = "APPROVED"   # DEMO: nothing is ever deleted
    retention_check_interval_s: float = Field(default=3600.0, ge=60.0, le=86400.0)
    db_budget_bytes: int = Field(default=10 * GIB, gt=0)             # planning budget: warns, never deletes
    free_space_warning_bytes: int = Field(default=4 * GIB, ge=0)     # low disk: WARNING
    free_space_reserve_bytes: int = Field(default=2 * GIB, ge=0)     # CRITICAL: new starts and ON refused
    snapshot_floor_bytes: int = Field(default=512 * 1024**2, ge=0)   # periodic snapshots pause below this

    @model_validator(mode="after")
    def _check(self) -> "HistorianSettings":
        if self.recording_interval_s not in RECORDING_INTERVALS_S:
            raise ValueError(f"recording_interval_s must be one of {RECORDING_INTERVALS_S}")
        if not self.snapshot_floor_bytes <= self.free_space_reserve_bytes <= self.free_space_warning_bytes:
            raise ValueError("free-space thresholds must satisfy snapshot_floor <= reserve <= warning")
        return self


class SecuritySettings(_Strict):
    session_idle_minutes: int = Field(default=30, ge=1, le=24 * 60)
    session_absolute_hours: int = Field(default=12, ge=1, le=7 * 24)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8000", "http://localhost:8000"])
    loopback_http_cookie_exemption: bool = True  # desktop demo only; refused for non-loopback binds
    login_rate_per_minute: int = Field(default=10, ge=1)
    mutation_rate_per_minute: int = Field(default=600, ge=1)
    max_request_bytes: int = Field(default=64 * 1024, ge=1024)


class Site(_Strict):
    name: str = "AquaCrop greenhouse (Malmö)"
    timezone: str = "Europe/Stockholm"
    facts: dict[str, str] = Field(default_factory=dict)


class InstallationConfig(_Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    config_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{0,63}$")
    config_version: str = Field(min_length=1, max_length=64)
    profile: AccessPolicy
    label: str
    site: Site = Site()
    buses: list[BusConfig] = Field(min_length=1)
    devices: list[DeviceConfig] = Field(min_length=1)
    scaling_profiles: list[ScalingProfile] = Field(default_factory=list)
    virtual_mapping: list[Mapping] = Field(default_factory=list)
    physical_mapping: list[Mapping] = Field(default_factory=list)
    alarm_rules: list[AlarmRule] = Field(default_factory=list)
    twin: TwinParams | None = None
    historian: HistorianSettings = HistorianSettings()
    security: SecuritySettings = SecuritySettings()

    # ---------------------------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate(self) -> "InstallationConfig":
        errs: list[str] = []
        if self.profile == AccessPolicy.COMMISSIONED_LIVE_CONTROL:
            errs.append("profile COMMISSIONED_LIVE_CONTROL is reserved and unavailable in this release")
        if self.profile == AccessPolicy.BENCH_MANUAL_WRITE:
            errs.append("BENCH_MANUAL_WRITE cannot be a persisted boot profile; start HARDWARE_READ_ONLY "
                        "and arm a server-side bench lease")
        bus_ids = [b.bus_id for b in self.buses]
        if len(bus_ids) != len(set(bus_ids)):
            errs.append("duplicate bus_id")
        buses = {b.bus_id: b for b in self.buses}
        uids = [d.device_uid for d in self.devices]
        if len(uids) != len(set(uids)):
            errs.append("duplicate device_uid")
        seen: dict[tuple[str, int], str] = {}
        for d in self.devices:
            if d.bus_id not in buses:
                errs.append(f"{d.device_uid}: unknown bus {d.bus_id}")
                continue
            key = (d.bus_id, d.address)
            if key in seen:
                errs.append(f"{d.device_uid}: address {d.address} on {d.bus_id} already used by {seen[key]}")
            seen[key] = d.device_uid
        kinds = {b.kind for b in self.buses}
        if self.profile == AccessPolicy.SIMULATED and kinds != {"virtual"}:
            errs.append("SIMULATED profile allows virtual buses only")
        if self.profile in (AccessPolicy.HARDWARE_READ_ONLY, AccessPolicy.HYBRID_SHADOW) and "virtual" in kinds:
            errs.append(f"{self.profile.value} profile allows rtu buses only")
        ports = [b.port for b in self.buses if b.port]
        if len(ports) != len(set(ports)):
            errs.append("two buses name the same serial port (one owner per bus)")

        scal = {}
        for s in self.scaling_profiles:
            if s.profile_id in scal:
                errs.append(f"duplicate scaling profile {s.profile_id}")
            scal[s.profile_id] = s
        real = self.profile != AccessPolicy.SIMULATED
        for d in self.devices:
            for ch, a in d.ai.items():
                if a.scaling is None:
                    continue
                s = scal.get(a.scaling)
                if s is None:
                    errs.append(f"{d.device_uid}.AI{ch}: unknown scaling {a.scaling}")
                    continue
                if a.mode.raw_unit != s.input_unit:
                    errs.append(f"{d.device_uid}.AI{ch}: scaling unit {s.input_unit} incompatible with mode "
                                f"{a.mode.name} ({a.mode.raw_unit})")
                if real and s.status != "VERIFIED":
                    errs.append(f"{d.device_uid}.AI{ch}: real profile may not use {s.status} scaling {s.profile_id}")

        points = {str(p): p for d in self.devices for p in d.point_ids()}
        if self.physical_mapping:
            errs.append("physical_mapping must stay empty: no approved field topology/wiring exists")
        if self.virtual_mapping:
            if real:
                errs.append("virtual_mapping is only valid in a SIMULATED profile")
            if self.twin is None:
                errs.append("virtual_mapping requires a twin section")
        mapped_pts: set[str] = set()
        mapped_sig: dict[str, str] = {}
        if self.twin is not None:
            act, dig, ana = (self.twin.actuator_signals(), self.twin.digital_observations(),
                             self.twin.analog_observations())
            for m in self.virtual_mapping:
                p = points.get(m.point)
                if p is None:
                    errs.append(f"mapping: unknown point {m.point}")
                    continue
                if m.point in mapped_pts:
                    errs.append(f"mapping: point {m.point} mapped twice")
                mapped_pts.add(m.point)
                allowed = {PointKind.DO: act, PointKind.DI: dig, PointKind.AI: ana}[p.kind]
                if m.signal not in allowed:
                    errs.append(f"mapping: signal {m.signal} is not a valid {p.kind.value} signal")
                if p.kind == PointKind.DO and m.signal in mapped_sig:
                    errs.append(f"mapping: actuator {m.signal} commanded by both {mapped_sig[m.signal]} and {m.point}")
                mapped_sig.setdefault(m.signal, m.point)
            if real and self.twin.provenance == "DEMO":
                # HYBRID_SHADOW may carry a predictive model, but never DEMO parameters as production config
                errs.append("real profile may not carry DEMO twin parameters")
        for r in self.alarm_rules:
            p = points.get(r.point)
            if p is None:
                errs.append(f"alarm {r.rule_id}: unknown point {r.point}")
            elif r.kind in ("HIGH", "LOW") and p.kind != PointKind.AI:
                errs.append(f"alarm {r.rule_id}: {r.kind} needs an AI point")
            elif r.kind == "DI_ACTIVE" and p.kind != PointKind.DI:
                errs.append(f"alarm {r.rule_id}: DI_ACTIVE needs a DI point")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    # ---------------------------------------------------------------------------------------
    def device(self, uid: str) -> DeviceConfig:
        for d in self.devices:
            if d.device_uid == uid:
                return d
        raise KeyError(uid)

    def bus(self, bus_id: str) -> BusConfig:
        for b in self.buses:
            if b.bus_id == bus_id:
                return b
        raise KeyError(bus_id)

    def all_points(self) -> list[PointId]:
        return [p for d in self.devices for p in d.point_ids()]

    def scaling(self, profile_id: str | None) -> ScalingProfile | None:
        for s in self.scaling_profiles:
            if s.profile_id == profile_id:
                return s
        return None

    def mapping_for_point(self, point: str) -> Mapping | None:
        for m in self.virtual_mapping:
            if m.point == point:
                return m
        return None

    def point_for_signal(self, signal: str) -> str | None:
        for m in self.virtual_mapping:
            if m.signal == signal:
                return m.point
        return None

    def expected_point_count(self) -> int:
        n_ai = sum(1 for d in self.devices if d.model == ModelId.WAVESHARE_AI_8CH)
        n_rd = sum(1 for d in self.devices if d.model == ModelId.WAVESHARE_RELAY_D)
        return 8 * n_ai + 16 * n_rd


def load_config(path: str | Path) -> InstallationConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a mapping")
    return InstallationConfig.model_validate(data)
