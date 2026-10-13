"""Audited output command service (brief §4).

Order of operations for every command, operator or controller:
  1. shape validation (DO target, explicit desired state, not expired, config version)
  2. policy check at acceptance (controller state, ownership, lease)  -> REJECTED is audited too
  3. durable audit INTENT                                             -> if it fails, ON is refused
  4. policy re-check immediately before dispatch
  5. EXECUTING -> backend.write_output -> structured outcome
  6. durable audit OUTCOME; if that fails the outcome is reported as OUTCOME_UNKNOWN and the
     durable intent is reconciled on restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Protocol

from ..config.schema import InstallationConfig
from ..domain.model import (CommandOrigin, CommandRecord, ControllerState, OutputCommand, Outcome, PointId,
                            PointKind, Priority, new_id, utcnow)
from ..io.backend import Backend


class AuditSink(Protocol):
    def command_intent(self, rec: CommandRecord) -> None: ...

    def command_outcome(self, rec: CommandRecord) -> None: ...

    def event(self, kind: str, payload: dict, *, actor: str | None = None, sim_s: float | None = None) -> None: ...


class MemoryAudit:
    """In-memory audit used by headless verification and tests."""

    def __init__(self) -> None:
        self.intents: list[CommandRecord] = []
        self.outcomes: list[tuple[str, Outcome]] = []
        self.events: list[tuple[str, dict, str | None, float | None]] = []
        self.fail_intent = False
        self.fail_outcome = False

    def command_intent(self, rec: CommandRecord) -> None:
        if self.fail_intent:
            raise OSError("audit store unavailable (test)")
        self.intents.append(rec)

    def command_outcome(self, rec: CommandRecord) -> None:
        if self.fail_outcome:
            raise OSError("audit store unavailable (test)")
        self.outcomes.append((rec.command.request_id, rec.outcome))

    def event(self, kind, payload, *, actor=None, sim_s=None) -> None:
        self.events.append((kind, payload, actor, sim_s))


Policy = Callable[[OutputCommand], str | None]   # returns a rejection reason, or None


@dataclass
class CommandService:
    cfg: InstallationConfig
    backend: Backend
    audit: AuditSink
    policy: Policy
    controller_state: Callable[[], ControllerState]

    def build(self, *, actor: str, target: str, desired: bool, origin: CommandOrigin, reason: str,
              ttl_s: float = 10.0, lease_id: str | None = None, request_id: str | None = None,
              expected_state: ControllerState | None = None, priority: Priority | None = None) -> OutputCommand:
        now = utcnow()
        if priority is None:
            priority = Priority.STOP if not desired else Priority.NORMAL
        return OutputCommand(request_id=request_id or new_id("cmd"), actor=actor, target=target, desired=desired,
                             expected_config_version=self.cfg.config_version, expected_controller_state=expected_state,
                             issued_utc=now, expires_utc=now + timedelta(seconds=ttl_s), origin=origin,
                             reason=reason, lease_id=lease_id, priority=priority)

    def _shape(self, cmd: OutputCommand) -> str | None:
        if not isinstance(cmd.desired, bool):
            return "desired state must be an explicit boolean"
        if cmd.target.startswith("drive:"):
            if self.cfg.drive_target(cmd.target) is None:
                return "unknown target (no Modbus drive adapter by that name)"
        else:
            try:
                pid = PointId.parse(cmd.target)
            except ValueError as e:
                return str(e)
            if pid.kind != PointKind.DO:
                return "target is not a DO point"
            if pid not in {p for p in self.cfg.all_points()}:
                return "unknown target"
        if cmd.expected_config_version != self.cfg.config_version:
            return "configuration version changed (command superseded)"
        if cmd.expected_controller_state is not None and cmd.expected_controller_state != self.controller_state():
            return f"controller state is {self.controller_state().value}, expected {cmd.expected_controller_state.value}"
        if cmd.expires_utc <= utcnow():
            return "command expired"
        return None

    def submit(self, cmd: OutputCommand) -> CommandRecord:
        rec = CommandRecord(cmd, Outcome.ACCEPTED)
        reason = self._shape(cmd) or self.policy(cmd)
        if reason:
            rec.advance(Outcome.REJECTED, reason)
            self._audit_rejected(rec)
            return rec
        rec.advance(Outcome.ACCEPTED)
        try:
            self.audit.command_intent(rec)
        except Exception as e:  # durable audit unavailable
            if cmd.desired:
                rec.advance(Outcome.REJECTED, f"durable audit unavailable: {e}")
                return rec
            rec.detail = f"intent not persisted ({e}); OFF command allowed"
        reason = self._shape(cmd) or self.policy(cmd)   # re-check immediately before dispatch
        if reason:
            rec.advance(Outcome.CANCELLED, reason)
            self._persist_outcome(rec)
            return rec
        rec.advance(Outcome.EXECUTING)
        try:
            res = self.backend.write_output(cmd)
            rec.readback = res.readback
            rec.advance(res.outcome, res.detail)
        except Exception as e:  # anything unexpected after dispatch began: do not guess
            rec.advance(Outcome.OUTCOME_UNKNOWN, f"exception during dispatch: {e!r}")
        self._persist_outcome(rec)
        return rec

    def _audit_rejected(self, rec: CommandRecord) -> None:
        try:
            self.audit.command_intent(rec)
            self.audit.command_outcome(rec)
        except Exception:
            pass  # a rejected command had no side effect; nothing to reconcile

    def _persist_outcome(self, rec: CommandRecord) -> None:
        try:
            self.audit.command_outcome(rec)
        except Exception as e:
            rec.advance(Outcome.OUTCOME_UNKNOWN, f"outcome not persisted ({e}); reconcile from durable intent")
