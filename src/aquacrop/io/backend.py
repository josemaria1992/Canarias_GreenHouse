"""Backend contract (brief §4). No caller ever receives a raw serial client or register writer."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config.catalog import Capability
from ..domain.model import AcquisitionResult, DeviceHealth, OutputCommand, WriteResult


@runtime_checkable
class Backend(Protocol):
    #: True only for backends that can ever emit a physical write frame.
    physical: bool

    def capabilities(self, device_uid: str) -> frozenset[Capability]: ...

    def read_inputs(self, device_uid: str) -> AcquisitionResult: ...

    def read_outputs(self, device_uid: str) -> AcquisitionResult: ...

    def write_output(self, command: OutputCommand) -> WriteResult: ...

    def health(self, device_uid: str) -> DeviceHealth: ...

    def close(self) -> None: ...


class WriteNotPermitted(Exception):
    """Raised by policy layers; backends return structured REJECTED outcomes instead."""
