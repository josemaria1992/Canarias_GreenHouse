"""Validated, versioned installation configuration (inventory + profile + mappings).

The configuration is the only source of point identities. Point IDs are derived from
device_uid + kind + channel; there are no global AI1..AI16 arrays anywhere in the code.
"""
from __future__ import annotations

import os

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from ..domain.model import AccessPolicy, PointId, PointKind
from ..twin.installation_params import InstallationTwin
from ..twin.params import TwinParams
from .catalog import CATALOG, AIMode, AIVariant, ModelId

SCHEMA_VERSION = 1
UNICAST_MIN, UNICAST_MAX = 1, 247  # 248-255 are reserved by Modbus even if vendor tables allow them


_INDEX_CACHE: dict[tuple[str, int], tuple[list, dict]] = {}


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
    _points: tuple | None = PrivateAttr(default=None)
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
        priv = self.__pydantic_private__
        if priv["_points"] is None:
            spec = CATALOG[self.model]
            out: list[PointId] = []
            for kind, n in ((PointKind.AI, spec.ai), (PointKind.DI, spec.di), (PointKind.DO, spec.do)):
                out += [PointId(self.device_uid, kind, ch) for ch in range(1, n + 1)]
            priv["_points"] = tuple(out)
        return list(priv["_points"])

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
    kind: Literal["HIGH", "LOW", "DI_ACTIVE", "DI_INACTIVE"]
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


class VfdRegister(_Strict):
    """One read-only diagnostic register of a drive (output frequency, current, fault code)."""
    address: int = Field(ge=0, le=0xFFFF)           # protocol (zero-based) address, as sent on the wire
    function: Literal[3, 4] = 3
    signed: bool = False                            # two's complement 16-bit
    scale: float = 1.0                              # engineering value = raw x scale
    unit: str | None = Field(default=None, max_length=16)


class VfdModbusMap(_Strict):
    """The Modbus RTU interface of one drive, copied from that drive's manual (ADR-0015,
    `docs/model/vfd_modbus.md`). Every number comes from the manual: PROPOSED while CP-06 checks it on the
    bench (reads by `diagnostics read`, writes only under a bench lease), VERIFIED with the CP-06 record
    after it. DEMO is the simulated drive's map and a real profile refuses it. The software writes only
    the control register (FC06, one frame per value, never retried) and reads only the status and
    diagnostic registers; it writes no speed reference, no parameter and no fault reset."""
    bus_id: str
    address: int = Field(ge=UNICAST_MIN, le=UNICAST_MAX)
    drive_model: str = Field(min_length=1, max_length=120)          # make, model and firmware
    manual_reference: str = Field(min_length=1, max_length=200)     # document, edition, section or page
    control_register: int = Field(ge=0, le=0xFFFF)
    run_sequence: list[int] = Field(min_length=1, max_length=3)     # values written in order to start
    stop_value: int = Field(ge=0, le=0xFFFF)
    status_register: int = Field(ge=0, le=0xFFFF)
    status_function: Literal[3, 4] = 3
    running_mask: int = Field(ge=1, le=0xFFFF)                      # status AND mask != 0: running
    fault_mask: int = Field(ge=1, le=0xFFFF)                        # status AND mask != 0: tripped
    ready_mask: int | None = Field(default=None, ge=1, le=0xFFFF)
    frequency: VfdRegister | None = None
    current: VfdRegister | None = None
    fault_code: VfdRegister | None = None
    comm_loss_timeout_s: float = Field(gt=0, le=60)                 # as set in the drive: it stops without frames
    comm_loss_action: str = Field(min_length=1, max_length=120)     # as set in the drive, e.g. "coast stop and trip"
    provenance: Literal["DEMO", "PROPOSED", "VERIFIED"]
    evidence: str | None = Field(default=None, max_length=300)      # CP-06 record: who, when, what was observed

    @field_validator("run_sequence")
    @classmethod
    def _words(cls, v: list[int]) -> list[int]:
        if any(not 0 <= x <= 0xFFFF for x in v):
            raise ValueError("run_sequence values must be 0..65535")
        return v

    @model_validator(mode="after")
    def _distinct(self) -> "VfdModbusMap":
        if self.stop_value == self.run_sequence[-1]:
            raise ValueError("stop_value equals the last run value")
        if self.running_mask & self.fault_mask:
            raise ValueError("running_mask and fault_mask share a bit")
        if self.provenance == "VERIFIED" and not (self.evidence or "").strip():
            raise ValueError("a VERIFIED register map needs its CP-06 evidence")
        return self


class DriveAdapterConfig(_Strict):
    """A logical actuator interface for the shared VFD (brief §2): its run/stop is not forced onto a
    relay channel. SIMULATED adapters drive the twin directly; MODBUS_RTU adapters talk to the drive's
    own Modbus interface (`modbus`), the simulated drive in a SIMULATED profile; NOT_CONFIGURED is what the
    installation keeps until the real register map has passed CP-06 on the bench (ADR-0013, ADR-0015)."""
    adapter_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")
    kind: Literal["SIMULATED", "NOT_CONFIGURED", "MODBUS_RTU"]
    signals: list[str] = Field(min_length=1)
    modbus: VfdModbusMap | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _map(self) -> "DriveAdapterConfig":
        if (self.kind == "MODBUS_RTU") != (self.modbus is not None):
            raise ValueError(f"drive adapter {self.adapter_id}: a register map (modbus) belongs to MODBUS_RTU "
                             "adapters only, and every MODBUS_RTU adapter needs one")
        if self.kind == "MODBUS_RTU" and len(self.signals) != 1:
            raise ValueError(f"drive adapter {self.adapter_id}: a MODBUS_RTU adapter carries one run signal")
        return self

    @property
    def target(self) -> str:
        """Command target of this adapter on the bench and in the audit (`drive:<id>`)."""
        return f"drive:{self.adapter_id}"


RECORDING_INTERVALS_S = (60, 300)
GIB = 1024**3


class HistorianSettings(_Strict):
    """What the historian records periodically and how long it keeps it (owner requirements of
    2026-10-12, ADR-0008). Acquisition (each bus's poll_interval_s), alarm evaluation, control and
    dashboard updates keep their own timing; events, commands and alarm transitions are written when
    they happen, independent of the recording interval."""
    db_path: str = "var/aquacrop.sqlite3"
    acquisition_interval_s: float = Field(default=3.0, ge=0.2)    # display hint only; buses own their poll rate
    # Complete snapshot of every configured point (unchanged values and quality included) every
    # recording_interval_s of WALL time, in real and simulation profiles alike. 60 s is the owner's
    # active setting; 300 s is allowed but only on the owner's explicit request.
    recording_interval_s: int = 60
    # Optional second recorder for desktop simulation analysis, OFF by default: a complete snapshot every
    # sim_time_recording_s of SIMULATED time, stored as its own series (cadence_basis SIMULATION_TIME).
    # Its rows add to the database size; at high speed its cadence is raised so that it never writes
    # more often than once per sim_time_recording_min_wall_s of wall time.
    sim_time_recording_s: float | None = Field(default=None, gt=0)
    sim_time_recording_min_wall_s: float = Field(default=0.5, gt=0)
    retention_days: int = Field(default=365, ge=365)   # rolling window, owner-approved 2026-10-12
    retention_provenance: Literal["DEMO", "APPROVED"] = "APPROVED"   # DEMO: nothing is ever deleted
    retention_check_interval_s: float = Field(default=3600.0, ge=60.0, le=86400.0)
    db_budget_bytes: int = Field(default=10 * GIB, gt=0)             # planning budget: warns, never deletes
    free_space_warning_bytes: int = Field(default=4 * GIB, ge=0)     # low disk: WARNING
    free_space_reserve_bytes: int = Field(default=2 * GIB, ge=0)     # CRITICAL: new starts and ON refused
    snapshot_floor_bytes: int = Field(default=512 * 1024**2, ge=0)   # periodic snapshots pause below this
    # WAL: about 64 MiB at rest after a checkpoint reset (journal_size_limit); it grows while a long
    # reader holds an old snapshot. Above the warning: STORAGE_CAPACITY warning and retention pruning
    # defers; above the abort size: long readers (backup, restore drill) cancel themselves.
    wal_warning_bytes: int = Field(default=256 * 1024**2, gt=0)
    wal_reader_abort_bytes: int = Field(default=1 * GIB, gt=0)
    backup_keep: int = Field(default=7, ge=1)                        # local compressed backups kept
    backup_max_seconds: float = Field(default=3 * 3600.0, gt=0)      # a backup running longer aborts
    restore_drill_interval_days: int = Field(default=30, ge=1)       # full restore drill cadence

    @model_validator(mode="after")
    def _check(self) -> "HistorianSettings":
        if self.recording_interval_s not in RECORDING_INTERVALS_S:
            raise ValueError(f"recording_interval_s must be one of {RECORDING_INTERVALS_S}")
        if not self.snapshot_floor_bytes <= self.free_space_reserve_bytes <= self.free_space_warning_bytes:
            raise ValueError("free-space thresholds must satisfy snapshot_floor <= reserve <= warning")
        if self.wal_warning_bytes > self.wal_reader_abort_bytes:
            raise ValueError("wal_warning_bytes must not exceed wal_reader_abort_bytes")
        return self


class SdCardSettings(_Strict):
    """The SD card (in a USB reader) that holds the daily local backups and the restore-drill scratch
    space (owner request of 2026-10-12, ADR-0012). Its identity is TBD until the owner confirms the
    actual card: the filesystem UUID (`blkid`) and the volume id written to the card at enrolment
    (`offsite enroll-card`). Nothing is written unless the mounted filesystem proves to be this card."""
    mount_path: str | None = None            # TBD, e.g. /media/aquacrop-sd (an fstab entry with nofail)
    filesystem_uuid: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f][0-9A-Fa-f\-]{3,35}$")
    volume_id: str | None = Field(default=None, pattern=r"^vol_[0-9a-f]{16}$")
    backup_subdir: str = "aquacrop/backups"
    drill_subdir: str = "aquacrop/drill-scratch"
    reserve_bytes: int = Field(default=1 * GIB, ge=0)   # kept free on the card after a backup


class CloudBackupSettings(_Strict):
    """Encrypted copies through rclone: `remote` must name an rclone *crypt* remote that wraps the
    OneDrive folder (client-side encryption; checked before every upload). Account and folder are TBD
    until the owner confirms them; credentials live only in `rclone_config` (mode 0600, never in git)."""
    remote: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_\- ]*:[^\s]*$")
    account_note: str | None = None          # documentation only: which OneDrive account and folder
    rclone_binary: str = "rclone"
    rclone_config: str | None = None         # e.g. /etc/aquacrop/rclone.conf
    transfer_timeout_s: float = Field(default=3 * 3600.0, gt=0)
    retry_initial_s: float = Field(default=300.0, ge=10)
    retry_max_s: float = Field(default=3600.0, ge=60)


class BackupCopyPolicy(_Strict):
    """PROPOSED copy counts. Nothing is ever pruned until an administrator confirms exactly these
    values (Settings); a changed policy needs a new confirmation."""
    local_daily: int = Field(default=7, ge=1)
    cloud_daily: int = Field(default=30, ge=1)
    cloud_monthly: int = Field(default=12, ge=0)


class RestoreDrillSettings(_Strict):
    interval_days: int = Field(default=30, ge=1)
    min_scratch_bytes: int = Field(default=10 * GIB, ge=0)   # owner: preflight at least 10 GiB ...
    headroom_bytes: int = Field(default=1 * GIB, ge=0)       # ... plus headroom that must stay free
    max_seconds: float = Field(default=4 * 3600.0, gt=0)


class OffsiteBackupSettings(_Strict):
    """SD-card daily backups plus encrypted OneDrive copies (ADR-0012). Disabled until the owner confirms
    the actual OneDrive account/folder and SD card; while disabled the earlier local eMMC backups
    (ADR-0008) continue unchanged."""
    enabled: bool = False
    sd_card: SdCardSettings = SdCardSettings()
    cloud: CloudBackupSettings = CloudBackupSettings()
    policy: BackupCopyPolicy = BackupCopyPolicy()
    drill: RestoreDrillSettings = RestoreDrillSettings()
    stale_after_hours: float = Field(default=36.0, gt=0)     # no new verified backup / upload: alarm

    @model_validator(mode="after")
    def _check(self) -> "OffsiteBackupSettings":
        sd = self.sd_card
        if self.enabled and not (sd.mount_path and sd.filesystem_uuid and sd.volume_id):
            raise ValueError("offsite_backup.enabled needs the confirmed SD card: sd_card.mount_path, "
                             "filesystem_uuid and volume_id (see `offsite enroll-card`)")
        if self.enabled and not self.cloud.remote:
            raise ValueError("offsite_backup.enabled needs the encrypted cloud copy: cloud.remote (an rclone crypt "
                             "remote) and cloud.rclone_config; there is no card-only mode")
        if sd.mount_path is not None and not (sd.mount_path.startswith("/") or os.path.isabs(sd.mount_path)):
            raise ValueError("sd_card.mount_path must be an absolute path")
        for sub in (sd.backup_subdir, sd.drill_subdir):
            if sub.startswith("/") or ".." in sub.split("/"):
                raise ValueError("sd_card subdirectories must be relative and stay on the card")
        if self.cloud.remote is not None and not self.cloud.rclone_config:
            raise ValueError("cloud.remote needs cloud.rclone_config (the protected rclone configuration)")
        if self.cloud.retry_initial_s > self.cloud.retry_max_s:
            raise ValueError("cloud.retry_initial_s must not exceed retry_max_s")
        return self


SAFE_STATE_TRIGGERS = ("START", "STOP", "LEASE_END", "MODULE_RETURN")


class SafeStateSettings(_Strict):
    """When the hardware service switches digital outputs OFF by itself (ADR-0017). OFF only, each coil that
    reads ON, with an FC01 readback; never ON, never a drive. Empty (the default) keeps the read-only rule
    that nothing is written without a bench lease: start, stop, lease end and comm loss send no frame."""
    outputs_off_on: list[Literal["START", "STOP", "LEASE_END", "MODULE_RETURN"]] = Field(default_factory=list)

    @field_validator("outputs_off_on")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("each safe-state trigger may be listed once")
        return v


class SecuritySettings(_Strict):
    session_idle_minutes: int = Field(default=30, ge=1, le=24 * 60)
    session_absolute_hours: int = Field(default=12, ge=1, le=7 * 24)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8000", "http://localhost:8000"])
    loopback_http_cookie_exemption: bool = True  # desktop demo only; refused for non-loopback binds
    login_rate_per_minute: int = Field(default=10, ge=1)
    mutation_rate_per_minute: int = Field(default=600, ge=1)
    max_request_bytes: int = Field(default=64 * 1024, ge=1024)


SITE_TIMEZONES = {"Atlantic/Canary": "the installation in the Canary Islands",
                  "Europe/Stockholm": "the test bench in Malmö"}     # owner, 2026-10-13


class Site(_Strict):
    """Where the controller runs. Stored times are UTC; the time zone only decides what the pages, exports
    and daily boundaries show. A real profile names it explicitly (`SITE_TIMEZONES`)."""
    name: str = "AquaCrop greenhouse"
    timezone: str = "Europe/Stockholm"
    facts: dict[str, str] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown time zone {v!r}: use an IANA name such as Atlantic/Canary or "
                             "Europe/Stockholm") from e
        return v


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
    drive_adapters: list[DriveAdapterConfig] = Field(default_factory=list)
    twin: TwinParams | InstallationTwin | None = None
    historian: HistorianSettings = HistorianSettings()
    offsite_backup: OffsiteBackupSettings = OffsiteBackupSettings()
    security: SecuritySettings = SecuritySettings()
    safe_state: SafeStateSettings = SafeStateSettings()

    @field_validator("twin", mode="before")
    @classmethod
    def _twin_topology(cls, v):
        """`topology` selects the twin: the small legacy demo (default, demo-24) or the installation."""
        if isinstance(v, dict):
            topo = v.get("topology", "demo_small")
            if topo == "installation":
                return InstallationTwin.model_validate(v)
            if topo != "demo_small":
                raise ValueError(f"unknown twin topology {topo!r}")
            return TwinParams.model_validate(v)
        return v

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
        if self.safe_state.outputs_off_on and self.profile != AccessPolicy.HARDWARE_READ_ONLY:
            errs.append("safe_state applies to a HARDWARE_READ_ONLY profile only (the simulation has no real outputs)")
        ports = [b.port for b in self.buses if b.port]
        if len(ports) != len(set(ports)):
            errs.append("two buses name the same serial port (one owner per bus)")

        scal = {}
        for s in self.scaling_profiles:
            if s.profile_id in scal:
                errs.append(f"duplicate scaling profile {s.profile_id}")
            scal[s.profile_id] = s
        real = self.profile != AccessPolicy.SIMULATED
        if real and "timezone" not in self.site.model_fields_set:
            errs.append("site.timezone must be set on a real profile: "
                        + "; ".join(f"{z} for {where}" for z, where in SITE_TIMEZONES.items()))
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
            ids = [a.adapter_id for a in self.drive_adapters]
            if len(ids) != len(set(ids)):
                errs.append("duplicate drive adapter id")
            for a in self.drive_adapters:
                for sig in a.signals:
                    if sig not in act:
                        errs.append(f"drive adapter {a.adapter_id}: {sig} is not an actuator signal of this twin")
                    if sig in mapped_sig:
                        errs.append(f"drive adapter {a.adapter_id}: {sig} is also mapped to point {mapped_sig[sig]}")
                    mapped_sig.setdefault(sig, f"adapter:{a.adapter_id}")
            if real and self.twin.provenance == "DEMO":
                # HYBRID_SHADOW may carry a predictive model, but never DEMO parameters as production config
                errs.append("real profile may not carry DEMO twin parameters")
        errs += self._drive_errors(buses, seen, real)
        for r in self.alarm_rules:
            p = points.get(r.point)
            if p is None:
                errs.append(f"alarm {r.rule_id}: unknown point {r.point}")
            elif r.kind in ("HIGH", "LOW") and p.kind != PointKind.AI:
                errs.append(f"alarm {r.rule_id}: {r.kind} needs an AI point")
            elif r.kind in ("DI_ACTIVE", "DI_INACTIVE") and p.kind != PointKind.DI:
                errs.append(f"alarm {r.rule_id}: {r.kind} needs a DI point")
        if errs:
            raise ValueError("; ".join(errs))
        return self

    def _drive_errors(self, buses: dict, used: dict[tuple[str, int], str], real: bool) -> list[str]:
        errs = []
        ids = [a.adapter_id for a in self.drive_adapters]
        if self.twin is None and len(ids) != len(set(ids)):
            errs.append("duplicate drive adapter id")
        for a in self.drive_adapters:
            if a.kind == "SIMULATED" and real:
                errs.append(f"drive adapter {a.adapter_id}: SIMULATED adapters exist only in a SIMULATED profile")
            m = a.modbus
            if m is None:
                continue
            b = buses.get(m.bus_id)
            if b is None:
                errs.append(f"drive adapter {a.adapter_id}: unknown bus {m.bus_id}")
                continue
            key = (m.bus_id, m.address)
            if key in used:
                errs.append(f"drive adapter {a.adapter_id}: address {m.address} on {m.bus_id} already used by "
                            f"{used[key]}")
            used[key] = a.target
            if m.comm_loss_timeout_s < 2 * b.poll_interval_s:      # a healthy link must not trip the drive
                errs.append(f"drive adapter {a.adapter_id}: comm_loss_timeout_s {m.comm_loss_timeout_s:g} is less "
                            f"than twice the poll interval of {m.bus_id} ({b.poll_interval_s:g} s); the status "
                            "reads are what keep the drive's comm-loss timer from expiring")
            if real and m.provenance == "DEMO":
                errs.append(f"drive adapter {a.adapter_id}: a real profile may not use the DEMO register map of "
                            "the simulated drive; copy the real drive's map from its manual (PROPOSED for "
                            "CP-06 on the bench, VERIFIED after it) or leave the adapter NOT_CONFIGURED")
        return errs

    # ---------------------------------------------------------------------------------------
    def _index(self, name: str, items: list, key) -> dict:
        """Lookup table over a (frozen) list; keyed by the list object, so a copy that replaces the list
        gets its own table. First entry wins, as in a linear search."""
        k = (name, id(items))
        c = _INDEX_CACHE.get(k)
        if c is None or c[0] is not items:
            if len(_INDEX_CACHE) > 512:
                _INDEX_CACHE.clear()
            table: dict = {}
            for x in items:
                table.setdefault(key(x), x)
            c = _INDEX_CACHE[k] = (items, table)   # holding `items` keeps its id from being reused
        return c[1]

    def device(self, uid: str) -> DeviceConfig:
        d = self._index("device", self.devices, lambda d: d.device_uid).get(uid)
        if d is None:
            raise KeyError(uid)
        return d

    def bus(self, bus_id: str) -> BusConfig:
        for b in self.buses:
            if b.bus_id == bus_id:
                return b
        raise KeyError(bus_id)

    def all_points(self) -> list[PointId]:
        return [p for d in self.devices for p in d.point_ids()]

    def scaling(self, profile_id: str | None) -> ScalingProfile | None:
        return self._index("scaling", self.scaling_profiles, lambda s: s.profile_id).get(profile_id)

    def mapping_for_point(self, point: str) -> Mapping | None:
        return self._index("map_point", self.virtual_mapping, lambda m: m.point).get(point)

    def point_for_signal(self, signal: str) -> str | None:
        m = self._index("map_signal", self.virtual_mapping, lambda m: m.signal).get(signal)
        return m.point if m else None

    def adapter_for_signal(self, signal: str) -> DriveAdapterConfig | None:
        for a in self.drive_adapters:
            if signal in a.signals:
                return a
        return None

    def drive_of_adapter(self, adapter_id: str) -> str | None:
        """The drive identifier (`MAIN` in `drive.MAIN.run`) an adapter's run signal names."""
        for a in self.drive_adapters:
            if a.adapter_id == adapter_id:
                parts = a.signals[0].split(".")
                return parts[1] if len(parts) == 3 and parts[0] == "drive" else None
        return None

    def drive_target(self, target: str) -> DriveAdapterConfig | None:
        """The MODBUS_RTU adapter a command target `drive:<id>` names, or None."""
        for a in self.drive_adapters:
            if a.kind == "MODBUS_RTU" and a.target == target:
                return a
        return None

    @property
    def topology(self) -> str | None:
        return None if self.twin is None else getattr(self.twin, "topology", "demo_small")

    def expected_point_count(self) -> int:
        return sum(CATALOG[d.model].ai + CATALOG[d.model].di + CATALOG[d.model].do for d in self.devices)


def load_config(path: str | Path) -> InstallationConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a mapping")
    return InstallationConfig.model_validate(data)
