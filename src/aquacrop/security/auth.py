"""Accounts, roles, permissions, server-side sessions, CSRF and rate limits (brief §10).

* Passwords: Argon2id via argon2-cffi. No default or committed credentials; bootstrap is explicit.
* Session cookie holds a random token; only its SHA-256 is stored. Idle and absolute expiry,
  server-side revocation, password change revokes every session of that user.
* CSRF: token = HMAC(server secret, session hash); required in X-CSRF-Token on every mutation,
  together with an Origin/Referer allow-list check.
* Commissioning is a separate flag; neither admin role nor login alone can arm bench writes.
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..historian.schema import utc_iso
from ..historian.writer import Historian


class Role(str, enum.Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


class Perm(str, enum.Enum):
    READ = "read"                        # the viewer's scoped reads: Overview, program summaries, alarms (0.7.0)
    OPS_READ = "ops_read"                # operational reads: history, events, equipment, twin, modules (0.7.0)
    ADMIN_READ = "admin_read"            # settings, users, storage, backups, the API schema (0.7.0)
    MODULE_REGISTER = "module_register"  # bounded read-only discovery and module registration proposals (0.7.0)
    SIM_COMMAND = "sim_command"          # simulation commands, injection, overrides, twin controls
    ALARM_ACK = "alarm_ack"
    LOCKOUT_RESET = "lockout_reset"
    EXPORT_AUDIT = "export_audit"
    CONFIG_ADMIN = "config_admin"
    USER_ADMIN = "user_admin"
    BACKUP = "backup"
    BENCH_ARM = "bench_arm"              # additionally requires the commissioning flag + lease
    PROCESS_CONTROL = "process_control"  # process AUTO/MANUAL/STOP, bounded manual operations, stock readiness
    RECIPE_EDIT = "recipe_edit"          # save and validate recipe drafts
    RECIPE_ACTIVATE = "recipe_activate"  # activate or roll back a recipe version
    IO_ASSIGN = "io_assign"              # assign and verify I/O channels on the I/O board; needs commissioning
    EQUIPMENT_EDIT = "equipment_edit"    # equipment type drafts, items and program drafts (0.5.0)
    EQUIPMENT_ACTIVATE = "equipment_activate"  # activate or retire equipment types; activate programs
    MODULE_SETUP = "module_setup"        # module wizard: reserve and write a new module's address; administrators only (0.7.0)


PERM_MIN_ROLE = {Perm.READ: Role.VIEWER, Perm.OPS_READ: Role.OPERATOR, Perm.ADMIN_READ: Role.ADMIN,
                 Perm.MODULE_REGISTER: Role.OPERATOR, Perm.SIM_COMMAND: Role.OPERATOR, Perm.ALARM_ACK: Role.OPERATOR,
                 Perm.LOCKOUT_RESET: Role.OPERATOR, Perm.EXPORT_AUDIT: Role.ADMIN, Perm.CONFIG_ADMIN: Role.ADMIN,
                 Perm.USER_ADMIN: Role.ADMIN, Perm.BACKUP: Role.ADMIN, Perm.BENCH_ARM: Role.OPERATOR,
                 Perm.PROCESS_CONTROL: Role.OPERATOR, Perm.RECIPE_EDIT: Role.OPERATOR,
                 Perm.RECIPE_ACTIVATE: Role.ADMIN, Perm.IO_ASSIGN: Role.OPERATOR,
                 Perm.EQUIPMENT_EDIT: Role.OPERATOR, Perm.EQUIPMENT_ACTIVATE: Role.ADMIN,
                 Perm.MODULE_SETUP: Role.ADMIN}

# The role matrix of 0.7.0 (UI_UX_SPECIFICATION, ADR-0023): physical address/mode writes are for administrators only,
# including through the older wizard routes; the commissioning flag still lets an operator verify wiring and arm
# approved bench tests.
COMMISSIONING_PERMS = (Perm.BENCH_ARM, Perm.IO_ASSIGN)

# Navigation per role (exactly the specified pages; the web shell and the tests read this).
NAVIGATION = {
    "viewer": ["overview", "programs", "alarms"],
    "operator": ["overview", "blocks", "twin", "hw-add", "programs", "history", "alarms"],
    "admin": ["overview", "blocks", "twin", "hw-add", "programs", "history", "alarms",
              "equipment", "hardware", "settings"],
}

# What each role may do, shown when a user is created or changed. Owner decision 2026-10-24: an administrator
# has every right, commissioning included; the commissioning flag matters for operators only.
ROLE_TEXT = {
    "viewer": {"title": "Viewer", "summary": "Looks at the overview, the programs and the alarms; changes nothing.",
               "can": ["See the overview, the program summaries and schedules, and the alarms"],
               "cannot": ["Change, start, stop or acknowledge anything",
                          "Open the digital twin, history, events, equipment or module pages, or export data"]},
    "operator": {"title": "Operator", "summary": "Runs the installation day to day.",
                 "can": ["Overview, Blocks, Digital Twin, Add Module, Programs, Operation History, Alarms & Events",
                         "Acknowledge alarms and reset process faults",
                         "Switch the installation between AUTO and MANUAL, approve the restart checklist, choose "
                         "between conflicting programs, run or stop a program by hand",
                         "Create and edit equipment, blocks and program drafts, and validate them",
                         "Use the digital twin: run it, inject faults, accept a twin result",
                         "Register a detected module or prepare a module request; export operational history"],
                 "cannot": ["Activate equipment types or programs on the real system",
                            "Manage users, backups, retention or settings",
                            "Write a module's address or modes (administrators only)",
                            "Verify wiring or switch real outputs at the bench, unless Commissioning is ticked"]},
    "admin": {"title": "Administrator", "summary": "Every right, including commissioning.",
              "can": ["Everything an operator can, and commissioning",
                      "Activate and retire equipment types, activate programs",
                      "Create, change, disable and delete users; reset their passwords",
                      "Backups, retention, off-site copies and every other setting"],
              "cannot": []},
}
COMMISSIONING_TEXT = ("Commissioning is the right to work on the real modules at the bench: switch real outputs during an "
                      "armed test session and verify which module channel each piece of equipment uses. Preparing a new "
                      "module (writing its address and modes) is for administrators only. Give it to an operator who "
                      "works at the bench. Administrators always have it.")

MIN_PASSWORD_LEN = 12
_ph = PasswordHasher()  # Argon2id defaults of the maintained library


@dataclass(frozen=True)
class Principal:
    username: str
    role: Role
    commissioning: bool
    session_hash: str
    must_change_password: bool

    @property
    def has_commissioning(self) -> bool:
        return self.commissioning or self.role == Role.ADMIN

    def can(self, perm: Perm) -> bool:
        if perm in COMMISSIONING_PERMS and not self.has_commissioning:
            return False
        if self.must_change_password and perm not in (Perm.READ,):
            return False
        return RANK[self.role] >= RANK[PERM_MIN_ROLE[perm]]


class AuthError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_or_create_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


USERNAME_RULE = "2 to 32 characters: letters a-z or A-Z, digits 0-9, and . _ - (no spaces or accents)"


def validate_username(username: str) -> None:
    if not username.isascii() or not username.replace("_", "").replace("-", "").replace(".", "").isalnum() \
            or not 2 <= len(username) <= 32:
        raise ValueError("username: 2-32 chars, letters/digits/._-")


def validate_password(pw: str, username: str) -> None:
    if len(pw) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if username.lower() in pw.lower():
        raise ValueError("password must not contain the username")


class Accounts:
    def __init__(self, hist: Historian, secret: bytes, *, idle_minutes: int, absolute_hours: int):
        self.h = hist
        self.secret = secret
        self.idle = timedelta(minutes=idle_minutes)
        self.absolute = timedelta(hours=absolute_hours)
        self._touch_lock = threading.Lock()

    # ------------------------------------------------------------------ users
    def count_users(self) -> int:
        return self.h.query("SELECT count(*) FROM users")[0][0]

    def create_user(self, username: str, password: str, role: Role, *, commissioning: bool = False,
                    must_change: bool = False) -> None:
        validate_username(username)
        validate_password(password, username)
        ph = _ph.hash(password)
        now = utc_iso()
        self.h.submit(lambda c: c.execute("INSERT INTO users VALUES(?,?,?,?,0,?,?,?)",
                                          (username, ph, role.value, int(commissioning), now, now, int(must_change))),
                      wait=True)

    def list_users(self) -> list[dict]:
        return [dict(r) for r in self.h.query("SELECT username, role, commissioning, disabled, created_utc, "
                                              "password_changed_utc, must_change_password FROM users")]

    def set_disabled(self, username: str, disabled: bool) -> None:
        self.h.submit(lambda c: c.execute("UPDATE users SET disabled=? WHERE username=?", (int(disabled), username)),
                      wait=True)
        if disabled:
            self.revoke_user_sessions(username, "account disabled")

    def delete_user(self, username: str) -> None:
        """Remove an account. Its name stays in the history it wrote (events, commands, records)."""
        def op(c):
            c.execute("DELETE FROM auth_sessions WHERE username=?", (username,))
            c.execute("DELETE FROM users WHERE username=?", (username,))
        self.h.submit(op, wait=True)

    def reset_password(self, username: str, new: str) -> None:
        """An administrator sets a temporary password; the user must change it at the next login."""
        validate_password(new, username)
        ph = _ph.hash(new)
        self.h.submit(lambda c: c.execute("UPDATE users SET password_hash=?, password_changed_utc=?, "
                                          "must_change_password=1 WHERE username=?", (ph, utc_iso(), username)),
                      wait=True)
        self.revoke_user_sessions(username, "password reset by an administrator")

    def set_role(self, username: str, role: Role, commissioning: bool) -> None:
        self.h.submit(lambda c: c.execute("UPDATE users SET role=?, commissioning=? WHERE username=?",
                                          (role.value, int(commissioning), username)), wait=True)
        self.revoke_user_sessions(username, "role changed")

    def change_password(self, username: str, old: str, new: str) -> None:
        row = self.h.query("SELECT password_hash FROM users WHERE username=?", (username,))
        if not row or not self._verify(row[0][0], old):
            raise AuthError(403, "current password incorrect")
        validate_password(new, username)
        if old == new:
            raise ValueError("new password must differ")
        ph = _ph.hash(new)
        self.h.submit(lambda c: c.execute("UPDATE users SET password_hash=?, password_changed_utc=?, "
                                          "must_change_password=0 WHERE username=?", (ph, utc_iso(), username)),
                      wait=True)
        self.revoke_user_sessions(username, "password changed")

    @staticmethod
    def _verify(stored: str, pw: str) -> bool:
        try:
            return _ph.verify(stored, pw)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    # ------------------------------------------------------------------ sessions
    def login(self, username: str, password: str, client: str) -> tuple[str, Principal]:
        row = self.h.query("SELECT password_hash, role, commissioning, disabled, must_change_password FROM users "
                           "WHERE username=?", (username,))
        if not row:
            _ph.hash("timing-equaliser-not-a-credential")  # similar cost for unknown users
            raise AuthError(401, "invalid credentials")
        ph, role, comm, disabled, must = row[0]
        if not self._verify(ph, password) or disabled:
            raise AuthError(401, "invalid credentials")
        return self.open_session(username, client)

    def open_session(self, username: str, client: str) -> tuple[str, Principal]:
        """A new session for a user already proven (password, or a live dashboard hand-off ticket)."""
        row = self.h.query("SELECT role, commissioning, disabled, must_change_password FROM users WHERE username=?",
                           (username,))
        if not row or row[0][2]:
            raise AuthError(401, "invalid credentials")
        role, comm, _, must = row[0]
        token = secrets.token_urlsafe(32)
        sh = _h(token)
        now = datetime.now(timezone.utc)
        self.h.submit(lambda c: c.execute("INSERT INTO auth_sessions VALUES(?,?,?,?,?,?,NULL,NULL,?)",
                                          (sh, username, self.csrf_for(sh), utc_iso(now), utc_iso(now),
                                           utc_iso(now + self.absolute), client[:64])), wait=True)
        return token, Principal(username, Role(role), bool(comm), sh, bool(must))

    def csrf_for(self, session_hash: str) -> str:
        return hmac.new(self.secret, session_hash.encode(), hashlib.sha256).hexdigest()

    def resolve(self, token: str | None) -> Principal:
        if not token:
            raise AuthError(401, "not authenticated")
        sh = _h(token)
        rows = self.h.query("SELECT s.username, s.last_seen_utc, s.absolute_expiry_utc, s.revoked_utc, u.role, "
                            "u.commissioning, u.disabled, u.must_change_password FROM auth_sessions s "
                            "JOIN users u ON u.username=s.username WHERE s.session_hash=?", (sh,))
        if not rows:
            raise AuthError(401, "session unknown")
        user, last, absexp, revoked, role, comm, disabled, must = rows[0]
        now = datetime.now(timezone.utc)
        if revoked or disabled:
            raise AuthError(401, "session revoked")
        if now >= datetime.fromisoformat(absexp):
            raise AuthError(401, "session expired (absolute)")
        if now - datetime.fromisoformat(last) >= self.idle:
            self.revoke(sh, "idle timeout")
            raise AuthError(401, "session expired (idle)")
        # throttle last-seen writes to at most one per 15 s per session
        if (now - datetime.fromisoformat(last)).total_seconds() > 15:
            self.h.submit(lambda c: c.execute("UPDATE auth_sessions SET last_seen_utc=? WHERE session_hash=?",
                                              (utc_iso(now), sh)))
        return Principal(user, Role(role), bool(comm), sh, bool(must))

    def check_csrf(self, p: Principal, header: str | None) -> None:
        if not header or not hmac.compare_digest(header, self.csrf_for(p.session_hash)):
            raise AuthError(403, "missing or invalid CSRF token")

    def revoke(self, session_hash: str, reason: str) -> None:
        self.h.submit(lambda c: c.execute("UPDATE auth_sessions SET revoked_utc=?, revoke_reason=? "
                                          "WHERE session_hash=? AND revoked_utc IS NULL",
                                          (utc_iso(), reason, session_hash)), wait=True)

    def revoke_user_sessions(self, username: str, reason: str) -> None:
        self.h.submit(lambda c: c.execute("UPDATE auth_sessions SET revoked_utc=?, revoke_reason=? "
                                          "WHERE username=? AND revoked_utc IS NULL", (utc_iso(), reason, username)),
                      wait=True)


class RateLimiter:
    """Sliding 60 s window per key: at most `limit_per_minute` allowed hits in any 60 s, so a burst
    cannot get twice the limit across a minute boundary. In-memory: one process owns the installation."""

    def __init__(self, clock=time.monotonic) -> None:
        self._w: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def hit(self, key: str, limit_per_minute: int) -> bool:
        now = self._clock()
        with self._lock:
            q = self._w.setdefault(key, deque())
            while q and q[0] <= now - 60:
                q.popleft()
            ok = len(q) < limit_per_minute
            if ok:
                q.append(now)
            if len(self._w) > 10000:
                self._w = {k: v for k, v in self._w.items() if v and v[-1] > now - 60}
            return ok
