"""Bench write lease (brief §6, §8). The only path by which a physical FC05 frame can be authorized.

A lease is armed only when ALL of these hold:
  * the installation profile is HARDWARE_READ_ONLY (never SIMULATED, never a persisted bench profile);
  * the principal holds the separate commissioning flag (role, login or password alone never suffice);
  * the operator typed the exact confirmation that production loads are disconnected, and described
    the isolated setup;
  * an exact, non-empty allowlist of relay DO points and Modbus drives (`drive:<id>`, ADR-0015) is given;
  * the duration is between 1 and 30 minutes;
  * a fresh preflight shows every involved relay module online with all 8 channel modes NORMAL, and
    every involved drive answering with no fault;
  * the lease is persisted (bench_leases) before it becomes active.

The lease is bound to the browser session, this process and the configuration version. It lives in
memory only: a restart can never restore it (stale rows are closed at startup), and nothing in a
config file or the environment can arm or renew it. On expiry or revocation writes stop being
accepted; no automatic OFF sequence is sent.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from ..config.catalog import ModelId
from ..config.schema import InstallationConfig
from ..domain.model import AccessPolicy, CommandOrigin, OutputCommand, PointId, PointKind, new_id, utcnow
from ..security.auth import Perm, Principal

CONFIRMATION_PHRASE = "PRODUCTION LOADS DISCONNECTED"
MIN_LEASE_S = 60
MAX_LEASE_S = 30 * 60
BENCH_ORIGINS = {CommandOrigin.BENCH}


class BenchRefused(Exception):
    pass


@dataclass
class BenchLease:
    lease_id: str
    username: str
    session_hash: str
    process_session_id: str
    config_version: str
    allowlist: frozenset[str]
    setup_description: str
    created_utc: datetime
    expires_utc: datetime
    expires_monotonic: float
    ended_reason: str | None = None

    def public(self, now_mono: float) -> dict:
        return {"lease_id": self.lease_id, "username": self.username, "allowlist": sorted(self.allowlist),
                "setup_description": self.setup_description, "created_utc": self.created_utc.isoformat(),
                "expires_utc": self.expires_utc.isoformat(),
                "remaining_s": max(0.0, round(self.expires_monotonic - now_mono, 1)),
                "config_version": self.config_version}


@dataclass
class BenchGate:
    cfg: InstallationConfig
    process_session_id: str
    persist_arm: Callable[[BenchLease], None]
    persist_end: Callable[[BenchLease], None]
    preflight: Callable[[set[str]], str | None]          # device_uids -> refusal reason or None
    event: Callable[[str, dict, str | None], None]
    monotonic: Callable[[], float] = time.monotonic
    _lease: BenchLease | None = field(default=None, init=False)

    # ------------------------------------------------------------------ arming
    def arm(self, p: Principal, *, confirmation: str, setup_description: str, allowlist: list[str],
            duration_s: int) -> BenchLease:
        try:
            lease = self._arm(p, confirmation=confirmation, setup_description=setup_description,
                              allowlist=allowlist, duration_s=duration_s)
        except BenchRefused as e:
            self.event("BENCH_ARM_REFUSED", {"reason": str(e), "allowlist": list(allowlist)[:16]}, p.username)
            raise
        self.event("BENCH_LEASE_ARMED", lease.public(self.monotonic()), p.username)
        return lease

    def _arm(self, p: Principal, *, confirmation, setup_description, allowlist, duration_s) -> BenchLease:
        if self.cfg.profile != AccessPolicy.HARDWARE_READ_ONLY:
            raise BenchRefused(f"bench writes need a HARDWARE_READ_ONLY installation, not {self.cfg.profile.value}")
        if not p.can(Perm.BENCH_ARM):
            raise BenchRefused("commissioning permission required (role or login alone is not enough)")
        if confirmation != CONFIRMATION_PHRASE:
            raise BenchRefused(f"type the exact confirmation: {CONFIRMATION_PHRASE}")
        if not isinstance(setup_description, str) or len(setup_description.strip()) < 10:
            raise BenchRefused("describe the isolated bench setup (at least 10 characters)")
        if not isinstance(duration_s, int) or isinstance(duration_s, bool) or not MIN_LEASE_S <= duration_s <= MAX_LEASE_S:
            raise BenchRefused(f"duration must be {MIN_LEASE_S}..{MAX_LEASE_S} s")
        if self.active() is not None:
            raise BenchRefused("a bench lease is already armed; revoke it first")
        if not allowlist or len(set(allowlist)) != len(allowlist):
            raise BenchRefused("allowlist must name each relay output or drive exactly once")
        known = {str(x) for x in self.cfg.all_points()}
        devices: set[str] = set()
        for t in allowlist:
            if isinstance(t, str) and t.startswith("drive:"):
                if self.cfg.drive_target(t) is None:
                    raise BenchRefused(f"{t} is not a drive with a Modbus register map")
                devices.add(t)
                continue
            try:
                pid = PointId.parse(t)
            except ValueError as e:
                raise BenchRefused(str(e)) from e
            if t not in known or pid.kind != PointKind.DO:
                raise BenchRefused(f"{t} is not a configured relay output")
            if self.cfg.device(pid.device_uid).model != ModelId.WAVESHARE_RELAY_D:
                raise BenchRefused(f"{t} is not on a relay module")
            devices.add(pid.device_uid)
        if (why := self.preflight(devices)) is not None:
            raise BenchRefused(f"preflight: {why}")
        now, mono = utcnow(), self.monotonic()
        lease = BenchLease(new_id("lease"), p.username, p.session_hash, self.process_session_id,
                           self.cfg.config_version, frozenset(allowlist), setup_description.strip()[:500], now,
                           now + timedelta(seconds=duration_s), mono + duration_s)
        try:
            self.persist_arm(lease)
        except Exception as e:
            raise BenchRefused(f"lease could not be recorded durably ({e}); not armed") from e
        self._lease = lease
        return lease

    # ------------------------------------------------------------------ state
    def active(self) -> BenchLease | None:
        lease = self._lease
        if lease is None:
            return None
        if self.monotonic() >= lease.expires_monotonic:
            self._end(lease, "expired", "system")
            return None
        return lease

    def revoke(self, reason: str, actor: str | None) -> bool:
        lease = self._lease
        if lease is None:
            return False
        self._end(lease, reason, actor)
        return True

    def session_ended(self, session_hash: str) -> None:
        lease = self._lease
        if lease is not None and lease.session_hash == session_hash:
            self._end(lease, "session ended", lease.username)

    def _end(self, lease: BenchLease, reason: str, actor: str | None) -> None:
        self._lease = None          # stop accepting writes first; nothing is sent to the modules
        lease.ended_reason = reason
        try:
            self.persist_end(lease)
        finally:
            self.event("BENCH_LEASE_ENDED", {"lease_id": lease.lease_id, "reason": reason,
                                             "note": "no automatic output change is sent on lease end"}, actor)

    # ------------------------------------------------------------------ per-request checks
    def check_session(self, p: Principal, lease_id: str | None) -> str | None:
        lease = self.active()
        if lease is None:
            return "no bench lease armed"
        if lease_id != lease.lease_id:
            return "command does not carry the active lease id"
        if p.session_hash != lease.session_hash or p.username != lease.username:
            return "bench lease belongs to another session"
        if not p.can(Perm.BENCH_ARM):
            return "commissioning permission no longer held"
        return None

    def authorize(self, cmd: OutputCommand) -> str | None:
        """Policy for CommandService and ModbusBackend; evaluated before intent and again at dispatch."""
        lease = self.active()
        if lease is None:
            return "no bench lease armed"
        if cmd.lease_id != lease.lease_id:
            return "command does not carry the active lease id"
        if cmd.origin not in BENCH_ORIGINS:
            return f"origin {cmd.origin.value} cannot write on the bench"
        if cmd.target not in lease.allowlist:
            return f"{cmd.target} is not in the lease allowlist"
        if cmd.expected_config_version != lease.config_version or lease.config_version != self.cfg.config_version:
            return "configuration version differs from the lease"
        if lease.process_session_id != self.process_session_id:
            return "lease belongs to another process"
        return None


def close_stale_leases(hist, process_session_id: str) -> int:
    """Startup: any lease row still open belongs to a previous process and is closed, never restored."""
    from ..historian.schema import utc_iso

    def op(c):
        cur = c.execute("UPDATE bench_leases SET revoked_utc=?, revoke_reason=? WHERE revoked_utc IS NULL "
                        "AND process_session_id != ?", (utc_iso(), "process restart (leases are never restored)",
                                                        process_session_id))
        return cur.rowcount
    return hist.submit(op, wait=True)


def historian_persistence(hist):
    from ..historian.schema import utc_iso

    def arm(lease: BenchLease) -> None:
        def op(c):
            c.execute("INSERT INTO bench_leases(lease_id,username,session_hash,process_session_id,config_version,"
                      "allowlist_json,loads_disconnected_confirmation,setup_description,created_utc,expires_utc)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (lease.lease_id, lease.username, lease.session_hash, lease.process_session_id,
                       lease.config_version, json.dumps(sorted(lease.allowlist)), CONFIRMATION_PHRASE,
                       lease.setup_description, utc_iso(lease.created_utc), utc_iso(lease.expires_utc)))
        hist.submit(op, wait=True)

    def end(lease: BenchLease) -> None:
        def op(c):
            c.execute("UPDATE bench_leases SET revoked_utc=?, revoke_reason=? WHERE lease_id=? AND revoked_utc IS NULL",
                      (utc_iso(), lease.ended_reason, lease.lease_id))
        try:
            hist.submit(op, wait=True)
        except Exception:
            pass   # the in-memory lease is already gone; writes are refused regardless
    return arm, end
