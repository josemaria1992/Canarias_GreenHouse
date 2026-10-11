"""Core domain contracts: point identity, samples, commands, outcomes, operating dimensions.

These types are deliberately free of I/O. Nothing here knows about serial ports, SQLite or HTTP.
"""
from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# --------------------------------------------------------------------------------------------
# Operating dimensions (brief §5). Each is independent; none implies another.
# --------------------------------------------------------------------------------------------
class AccessPolicy(str, enum.Enum):
    SIMULATED = "SIMULATED"
    HARDWARE_READ_ONLY = "HARDWARE_READ_ONLY"
    HYBRID_SHADOW = "HYBRID_SHADOW"
    BENCH_MANUAL_WRITE = "BENCH_MANUAL_WRITE"
    # Reserved. Unavailable in milestones 1-5; config validation and the runtime both refuse it.
    COMMISSIONED_LIVE_CONTROL = "COMMISSIONED_LIVE_CONTROL"


class ControllerState(str, enum.Enum):
    STOPPED = "STOPPED"
    MANUAL = "MANUAL"
    AUTO = "AUTO"
    FAULT_LOCKOUT = "FAULT_LOCKOUT"


class TwinActivity(str, enum.Enum):
    RUN = "RUN"
    PAUSE = "PAUSE"
    REPLAY = "REPLAY"


class SignalSource(str, enum.Enum):
    IO_TEST = "IO_TEST"
    TWIN = "TWIN"


class Origin(str, enum.Enum):
    SIMULATED = "SIMULATED"        # virtual module driven by twin or I/O test injection
    REAL = "REAL"                  # acquired from a physical device
    PREDICTED = "PREDICTED"        # shadow model output
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"  # migrated v0.1 history without provenance


class Quality(str, enum.Enum):
    GOOD = "GOOD"
    OVERRIDDEN = "OVERRIDDEN"          # explicit operator / scenario override is in force
    UNAVAILABLE = "UNAVAILABLE"        # acquisition failed; value is null
    INVALID = "INVALID"                # acquired, but out of range / unscalable
    UNSCALED = "UNSCALED"              # raw only: unknown mode/revision or no verified scaling


class PointKind(str, enum.Enum):
    AI = "AI"
    DI = "DI"
    DO = "DO"


_POINT_RE = re.compile(r"^(?P<dev>[A-Za-z][A-Za-z0-9_\-]{0,47})\.(?P<kind>AI|DI|DO)(?P<ch>[1-8])$")


@dataclass(frozen=True, order=True)
class PointId:
    device_uid: str
    kind: PointKind
    channel: int  # 1..8

    def __post_init__(self) -> None:
        if not 1 <= self.channel <= 8:
            raise ValueError(f"channel must be 1..8, got {self.channel}")

    @property
    def register_address(self) -> int:
        """Zero-based Modbus address within the function's table (coil, DI or input register)."""
        return self.channel - 1

    def __str__(self) -> str:
        return f"{self.device_uid}.{self.kind.value}{self.channel}"

    @classmethod
    def parse(cls, text: str) -> "PointId":
        m = _POINT_RE.match(text)
        if not m:
            raise ValueError(f"invalid point id {text!r}")
        return cls(m["dev"], PointKind(m["kind"]), int(m["ch"]))


# --------------------------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Sample:
    point_id: str
    acquired_utc: datetime
    cycle_id: str
    sequence: int
    quality: Quality
    reason: str | None
    raw_value: float | int | bool | None
    raw_unit: str | None
    value: float | bool | None           # derived/engineering value, None when not derivable
    unit: str | None
    calibration_ref: str | None           # "<profile_id>@<version>"
    origin: Origin
    source_profile: str                   # config_id of the inventory that produced it
    config_version: str
    device_uid: str
    bus_id: str
    session_id: str                       # process run (server start) identity
    sim_run_id: str | None = None
    sim_time_s: float | None = None
    override: dict[str, Any] | None = None  # owner/reason/base value when quality=OVERRIDDEN
    last_good_value: float | bool | None = None   # labelled separately, never as current value
    last_good_utc: datetime | None = None


@dataclass(frozen=True)
class AcquisitionResult:
    device_uid: str
    bus_id: str
    ok: bool
    acquired_utc: datetime
    samples: tuple[Sample, ...]
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class DeviceHealth:
    device_uid: str
    bus_id: str
    online: bool = False
    consecutive_failures: int = 0
    last_ok_utc: datetime | None = None
    last_error: str | None = None
    last_error_utc: datetime | None = None
    backoff_until_monotonic: float = 0.0


# --------------------------------------------------------------------------------------------
# Commands and outcomes (brief §4)
# --------------------------------------------------------------------------------------------
class CommandOrigin(str, enum.Enum):
    OPERATOR = "OPERATOR"
    CONTROLLER = "CONTROLLER"     # AUTO sequence (simulation only in this release)
    SCENARIO = "SCENARIO"
    REPLAY = "REPLAY"
    SYSTEM = "SYSTEM"             # stop / fault sequences
    BENCH = "BENCH"               # operator command under an armed bench lease (real coil)


class Outcome(str, enum.Enum):
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    EXECUTING = "EXECUTING"
    READBACK_CONFIRMED = "READBACK_CONFIRMED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    CANCELLED = "CANCELLED"


TERMINAL_OUTCOMES = {Outcome.REJECTED, Outcome.READBACK_CONFIRMED, Outcome.FAILED,
                     Outcome.OUTCOME_UNKNOWN, Outcome.CANCELLED}


class Priority(enum.IntEnum):
    STOP = 0      # stop / fault actions jump the queue
    NORMAL = 10


@dataclass(frozen=True)
class OutputCommand:
    """An explicit desired state for one DO point. There is no toggle."""
    request_id: str
    actor: str
    target: str               # point id
    desired: bool
    expected_config_version: str
    expected_controller_state: ControllerState | None
    issued_utc: datetime
    expires_utc: datetime
    origin: CommandOrigin
    reason: str
    lease_id: str | None = None
    priority: Priority = Priority.NORMAL

    def with_priority(self, p: Priority) -> "OutputCommand":
        return replace(self, priority=p)


@dataclass
class CommandRecord:
    command: OutputCommand
    outcome: Outcome
    detail: str | None = None
    readback: bool | None = None
    history: list[tuple[datetime, Outcome, str | None]] = field(default_factory=list)

    def advance(self, outcome: Outcome, detail: str | None = None) -> None:
        self.outcome = outcome
        self.detail = detail
        self.history.append((utcnow(), outcome, detail))


@dataclass(frozen=True)
class WriteResult:
    """Structured backend write outcome. `readback` is coil state only, never actuator proof."""
    outcome: Outcome
    readback: bool | None
    detail: str | None = None
