"""Real Modbus backend (brief §4, §6). Origin REAL. Read-only unless a bench gate authorizes a write.

* Each device is acquired on its own: a failed module marks only its own points UNAVAILABLE.
* Repeated failures put a device into bounded exponential backoff; during backoff no frames are
  sent to it and its points stay UNAVAILABLE with the reason.
* AI channel modes are read from the module every cycle. A channel whose observed mode differs from
  the configured mode, or whose mode is UNKNOWN, is UNSCALED; no engineering value is produced.
* Relay control modes are read every cycle and kept for preflight; anything other than NORMAL on
  every channel blocks bench writes to that module.
* `write_output` returns REJECTED unless `authorize` is set (only BenchGate sets it) and approves the
  command. Even then each write is preceded by a fresh relay-mode preflight, sent exactly once
  (FC05), and confirmed by an FC01 readback. A missing or invalid answer is OUTCOME_UNKNOWN; an
  exception response or a readback mismatch is FAILED. Coil readback is not actuator proof.
* A drive with a Modbus register map (`drive:<adapter_id>`, ADR-0015) is read like a module: its status
  word every cycle, with the same backoff. A bench write to it is preceded by a fresh status read
  (refused while it reports a fault), sent as FC06 frames exactly once each, and answered by its echo
  and a status read: ECHO_CONFIRMED, never actuator proof. Nothing is written to it otherwise.
* `close()` releases ports and sends nothing.
"""
from __future__ import annotations

import time
from typing import Callable

from ..config.catalog import CATALOG, Capability, ModelId
from ..config.schema import InstallationConfig
from ..domain.model import (AccessPolicy, AcquisitionResult, DeviceHealth, Origin, OutputCommand, Outcome, PointId,
                            PointKind, WriteResult, utcnow)
from ..drivers.rtu import RtuBus, RtuError, RtuExceptionResponse, RtuRefused
from ..drivers.vfd import VfdCommandError, VfdDevice, VfdStatus
from ..drivers.waveshare import RelayModes, WaveshareDevice
from .sampling import AcqContext, make_sample

BACKOFF_AFTER_FAILURES = 3
BACKOFF_MAX_S = 60.0

Authorizer = Callable[[OutputCommand], "str | None"]


class ModbusBackend:
    physical = True

    def __init__(self, cfg: InstallationConfig, *, context: Callable[[], AcqContext],
                 buses: dict[str, RtuBus] | None = None, monotonic: Callable[[], float] = time.monotonic):
        if cfg.profile not in (AccessPolicy.HARDWARE_READ_ONLY,):
            raise RtuRefused(f"profile {cfg.profile.value} cannot open a real bus in this release")
        if any(b.kind != "rtu" for b in cfg.buses):
            raise RtuRefused("ModbusBackend serves rtu buses only")
        self.cfg = cfg
        self._context = context
        self._mono = monotonic
        writes = {b.bus_id: frozenset((a.modbus.address, a.modbus.control_register) for a in cfg.drive_adapters
                                      if a.kind == "MODBUS_RTU" and a.modbus.bus_id == b.bus_id) for b in cfg.buses}
        self.buses = buses if buses is not None else {b.bus_id: RtuBus(b, register_writes=writes[b.bus_id])
                                                      for b in cfg.buses}
        self.devices = {d.device_uid: WaveshareDevice(self.buses[d.bus_id], d.model, d.address) for d in cfg.devices}
        self._health = {d.device_uid: DeviceHealth(d.device_uid, d.bus_id) for d in cfg.devices}
        self.relay_modes: dict[str, RelayModes | None] = {d.device_uid: None for d in cfg.devices
                                                          if d.model == ModelId.WAVESHARE_RELAY_D}
        self.ai_modes: dict[str, list | None] = {d.device_uid: None for d in cfg.devices
                                                 if d.model == ModelId.WAVESHARE_AI_8CH}
        self.drives = {a.target: VfdDevice(self.buses[a.modbus.bus_id], a.modbus) for a in cfg.drive_adapters
                       if a.kind == "MODBUS_RTU"}
        self.drive_status: dict[str, VfdStatus | None] = {t: None for t in self.drives}
        for t, dv in self.drives.items():
            self._health[t] = DeviceHealth(t, dv.m.bus_id)
        self.authorize: Authorizer | None = None   # set only by BenchGate while a lease is armed

    # ------------------------------------------------------------------ contract
    def capabilities(self, device_uid: str) -> frozenset[Capability]:
        return CATALOG[self.cfg.device(device_uid).model].capabilities

    def health(self, device_uid: str) -> DeviceHealth:
        return self._health[device_uid]

    def read_inputs(self, device_uid: str) -> AcquisitionResult:
        dev = self.cfg.device(device_uid)
        kinds = (PointKind.AI, PointKind.DI)
        if (why := self._in_backoff(device_uid)) is not None:
            return self.unavailable(device_uid, why, kinds)
        wd = self.devices[device_uid]
        t0 = self._mono()
        try:
            if dev.model == ModelId.WAVESHARE_RELAY_D:
                bits = wd.read_discrete_inputs()
                self.relay_modes[device_uid] = wd.read_relay_modes()
                raws = {PointId(device_uid, PointKind.DI, i + 1): v for i, v in enumerate(bits)}
                observed = {}
            else:
                modes = wd.read_ai_modes()
                regs = wd.read_analog()
                self.ai_modes[device_uid] = modes
                raws = {PointId(device_uid, PointKind.AI, i + 1): v for i, v in enumerate(regs)}
                observed = {PointId(device_uid, PointKind.AI, i + 1): m for i, m in enumerate(modes)}
        except RtuError as e:
            if dev.model == ModelId.WAVESHARE_RELAY_D:
                self.relay_modes[device_uid] = None
            return self._failed(device_uid, f"{e.kind}: {e}", kinds)
        ctx, now = self._context(), utcnow()
        samples = tuple(make_sample(self.cfg, p, raws[p], acquired=now, ctx=ctx, origin=Origin.REAL,
                                    observed_mode=observed.get(p))
                        for p in dev.point_ids() if p.kind in kinds)
        self._ok(device_uid, now)
        return AcquisitionResult(device_uid, dev.bus_id, True, now, samples, duration_s=self._mono() - t0)

    def read_outputs(self, device_uid: str) -> AcquisitionResult:
        dev = self.cfg.device(device_uid)
        kinds = (PointKind.DO,)
        if dev.model != ModelId.WAVESHARE_RELAY_D:
            return AcquisitionResult(device_uid, dev.bus_id, True, utcnow(), ())
        if (why := self._in_backoff(device_uid)) is not None:
            return self.unavailable(device_uid, why, kinds)
        try:
            bits = self.devices[device_uid].read_coils()
        except RtuError as e:
            return self._failed(device_uid, f"{e.kind}: {e}", kinds)
        ctx, now = self._context(), utcnow()
        samples = tuple(make_sample(self.cfg, p, bits[p.channel - 1], acquired=now, ctx=ctx, origin=Origin.REAL,
                                    reason="coil state (not actuator proof)")
                        for p in dev.point_ids() if p.kind == PointKind.DO)
        self._ok(device_uid, now)
        return AcquisitionResult(device_uid, dev.bus_id, True, now, samples)

    def read_drive(self, target: str) -> tuple[VfdStatus | None, str | None]:
        """One status read of a Modbus drive (reads only); None and the reason when it does not answer."""
        if (why := self._in_backoff(target)) is not None:
            return None, why
        try:
            st = self.drives[target].read_status()
        except RtuError as e:
            self.drive_status[target] = None
            return None, self._note_failure(target, f"{e.kind}: {e}")
        self.drive_status[target] = st
        self._ok(target, utcnow())
        return st, None

    def write_output(self, command: OutputCommand) -> WriteResult:
        if self.authorize is None:
            return WriteResult(Outcome.REJECTED, None, "read-only profile: no bench lease armed")
        if (why := self.authorize(command)) is not None:
            return WriteResult(Outcome.REJECTED, None, why)
        if command.target in self.drives:
            return self._write_drive(command)
        pid = PointId.parse(command.target)
        dev = self.cfg.device(pid.device_uid)
        if pid.kind != PointKind.DO or dev.model != ModelId.WAVESHARE_RELAY_D:
            return WriteResult(Outcome.REJECTED, None, "target is not a relay coil")
        wd = self.devices[dev.device_uid]
        try:   # preflight immediately before dispatch: modes must be NORMAL on all 8 channels
            modes = wd.read_relay_modes()
        except RtuError as e:
            return WriteResult(Outcome.REJECTED, None, f"preflight mode read failed ({e.kind}); nothing sent")
        self.relay_modes[dev.device_uid] = modes
        if not modes.all_normal:
            return WriteResult(Outcome.REJECTED, None, f"preflight: relay modes {modes.describe()} not all NORMAL")
        try:
            wd.write_coil(pid.channel, command.desired)
        except RtuExceptionResponse as e:
            return WriteResult(Outcome.FAILED, None, f"module refused write: {e}")
        except RtuRefused as e:
            return WriteResult(Outcome.REJECTED, None, str(e))
        except RtuError as e:   # no/invalid answer: the module may have applied it
            return WriteResult(Outcome.OUTCOME_UNKNOWN, None, f"{e.kind} after FC05 was sent: {e}")
        try:
            bits = wd.read_coils()
        except RtuError as e:
            return WriteResult(Outcome.OUTCOME_UNKNOWN, None, f"FC05 echoed but FC01 readback failed ({e.kind})")
        rb = bits[pid.channel - 1]
        if rb != command.desired:
            return WriteResult(Outcome.FAILED, rb, "FC01 readback does not match requested state")
        return WriteResult(Outcome.READBACK_CONFIRMED, rb, "FC05 echo + FC01 readback (coil state, not actuator proof)")

    def _write_drive(self, command: OutputCommand) -> WriteResult:
        dv = self.drives[command.target]
        try:     # preflight immediately before dispatch: the drive answers and reports no fault
            st = dv.read_status()
        except RtuError as e:
            return WriteResult(Outcome.REJECTED, None, f"preflight status read failed ({e.kind}); nothing sent")
        self.drive_status[command.target] = st
        if command.desired and st.fault:
            return WriteResult(Outcome.REJECTED, None, f"drive reports a fault (status 0x{st.word:04X}); reset it "
                                                       "at the drive; nothing sent")
        if command.desired and st.ready is False:
            return WriteResult(Outcome.REJECTED, None, f"drive not ready (status 0x{st.word:04X}); nothing sent")
        try:
            n = dv.command(command.desired)
        except VfdCommandError as e:
            if e.sent == 0 and isinstance(e.cause, RtuRefused):
                return WriteResult(Outcome.REJECTED, None, str(e.cause))
            if e.sent == 0 and isinstance(e.cause, RtuExceptionResponse):
                return WriteResult(Outcome.FAILED, None, f"drive refused the control value: {e.cause}")
            return WriteResult(Outcome.OUTCOME_UNKNOWN, None, f"{e} (FC06 sent; the drive may have applied it)")
        try:
            after = dv.read_status()
        except RtuError as e:
            return WriteResult(Outcome.ECHO_CONFIRMED, None, f"FC06 echo of {n} value(s); the status read "
                                                             f"afterwards failed ({e.kind})")
        self.drive_status[command.target] = after
        return WriteResult(Outcome.ECHO_CONFIRMED, after.running,
                           f"FC06 echo of {n} value(s); status 0x{after.word:04X}: running={after.running}, "
                           f"fault={after.fault} (an echo is not motor proof; the drive ramps)")

    def close(self) -> None:
        for b in self.buses.values():
            b.close()

    # ------------------------------------------------------------------ health
    def _in_backoff(self, device_uid: str) -> str | None:
        h = self._health[device_uid]
        if h.backoff_until_monotonic and self._mono() < h.backoff_until_monotonic:
            return f"backoff after {h.consecutive_failures} consecutive failures ({h.last_error})"
        return None

    def _ok(self, device_uid: str, now) -> None:
        h = self._health[device_uid]
        h.online, h.consecutive_failures, h.last_ok_utc, h.backoff_until_monotonic = True, 0, now, 0.0

    def _failed(self, device_uid: str, error: str, kinds) -> AcquisitionResult:
        return self.unavailable(device_uid, self._note_failure(device_uid, error), kinds)

    def _note_failure(self, device_uid: str, error: str) -> str:
        h = self._health[device_uid]
        h.online = False
        h.consecutive_failures += 1
        h.last_error, h.last_error_utc = error, utcnow()
        if h.consecutive_failures >= BACKOFF_AFTER_FAILURES:
            base = self.cfg.bus(h.bus_id).poll_interval_s
            delay = min(BACKOFF_MAX_S, base * 2 ** (h.consecutive_failures - BACKOFF_AFTER_FAILURES))
            h.backoff_until_monotonic = self._mono() + delay
        return error

    def unavailable(self, device_uid: str, why: str, kinds) -> AcquisitionResult:
        dev = self.cfg.device(device_uid)
        ctx, now = self._context(), utcnow()
        samples = tuple(make_sample(self.cfg, p, None, acquired=now, ctx=ctx, origin=Origin.REAL, reason=why)
                        for p in dev.point_ids() if p.kind in kinds)
        return AcquisitionResult(device_uid, dev.bus_id, False, now, samples, why)
