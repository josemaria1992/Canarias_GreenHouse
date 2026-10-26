"""One-way files between the live service and the twin service (0.5.0).

The two runtimes share no database, no process and no command path. They exchange two kinds of files in a
directory both can reach (`/var/lib/aquacrop-feed` on the controller), each with one writer:

* `live-snapshot.json`, written by the live service only: the configuration, the equipment types, items and
  channel bindings, the program versions, the accepted installation diagram (0.5.6), and the login accounts (names, roles and password hashes, so every
  user can sign in to the twin with the same password). The twin reads it; importing it never changes the live
  system. Bindings arrive as records; the twin's channels are virtual.
* `twin-handoff.key`, written by the live service only (new at every live start): the key of the one-time
  tickets with which a user signed in to the real dashboard opens the twin without a second login (0.5.5).
  A ticket names the user, is valid for 60 seconds and is accepted once; the twin still checks that the user
  exists and is enabled in its copy of the accounts.
* `evidence/<program>@<version>-<digest12>.json`, written by the twin only, when a user accepts a twin result
  for a program version. The live service reads it and records it against the same version only if the digest
  matches, so an edited copy in the twin can never become evidence for the live version.

Writes are atomic (write to a temporary name, then rename).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = "live-snapshot.json"
EVIDENCE_DIR = "evidence"
KIND = "AQUACROP_LIVE_SNAPSHOT"
HANDOFF_KEY = "twin-handoff.key"
TICKET_S = 60


def _atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.chmod(tmp, 0o640)              # the services run with umask 077; the other side reads through its group
    os.replace(tmp, path)


def publish_snapshot(feed_dir: Path, *, cfg, equipment, programs, hist, software_version: str,
                     layout: dict | None = None, hydraulics: dict | None = None) -> dict:
    users = [dict(r) for r in hist.query("SELECT username, password_hash, role, commissioning, disabled, "
                                         "must_change_password FROM users")]
    doc = {"kind": KIND, "software_version": software_version, "written_utc": datetime.now(timezone.utc).isoformat(),
           "config": cfg.model_dump(mode="json", exclude_none=True), "config_version": cfg.config_version,
           "equipment": equipment.export(),
           "bindings": [{"item_id": v["item_id"], "role": v["role"], "point": v["point"], "status": v["status"]}
                        for v in equipment.bindings().values()],
           "programs": [{k: v[k] for k in ("program_id", "version", "status", "name", "digest", "body")}
                        for v in programs.versions()],
           "users": users, "layout": layout, "hydraulics": hydraulics}     # 0.7.0: graph, drawing and blocks
    _atomic(Path(feed_dir) / SNAPSHOT, doc)
    hist.event("TWIN_SNAPSHOT_PUBLISHED", {"config_version": cfg.config_version, "items": len(doc["equipment"]["items"]),
                                           "programs": len(doc["programs"]), "users": len(users)}, actor="system")
    return {"written_utc": doc["written_utc"], "items": len(doc["equipment"]["items"]), "programs": len(doc["programs"])}


def read_snapshot(feed_dir: Path) -> dict | None:
    p = Path(feed_dir) / SNAPSHOT
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("kind") == KIND else None


def write_evidence(feed_dir: Path, evidence: dict) -> Path:
    name = f"{evidence['program_id']}@{evidence['version']}-{evidence['digest'][:12]}.json"
    path = Path(feed_dir) / EVIDENCE_DIR / name
    _atomic(path, evidence)
    return path


def read_evidence(feed_dir: Path) -> list[dict]:
    d = Path(feed_dir) / EVIDENCE_DIR
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


# ---------------------------------------------------------------------- sign-in hand-off (0.5.5)
def write_handoff_key(feed_dir: Path) -> bytes:
    key = secrets.token_bytes(32)
    path = Path(feed_dir) / HANDOFF_KEY
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(key.hex().encode())
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
    return key


def _read_handoff_key(feed_dir: Path) -> bytes | None:
    try:
        return bytes.fromhex((Path(feed_dir) / HANDOFF_KEY).read_text().strip())
    except (OSError, ValueError):
        return None


def issue_ticket(key: bytes, username: str, now: float | None = None) -> str:
    body = json.dumps({"u": username, "exp": int((now or time.time()) + TICKET_S), "n": secrets.token_hex(12)},
                      separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return b + "." + hmac.new(key, b.encode(), hashlib.sha256).hexdigest()


class TicketCheck:
    """The twin's side: a ticket is valid once, before it expires, under the live service's current key."""

    def __init__(self, feed_dir: Path, clock=time.time):
        self.feed_dir, self.clock = Path(feed_dir), clock
        self.used: dict[str, float] = {}

    def username(self, ticket: str) -> str:
        key = _read_handoff_key(self.feed_dir)
        b, _, sig = ticket.partition(".")
        if key is None or not b or not hmac.compare_digest(sig, hmac.new(key, b.encode(), hashlib.sha256).hexdigest()):
            raise ValueError("ticket not valid")
        doc = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        now = self.clock()
        self.used = {n: e for n, e in self.used.items() if e > now}
        if doc["exp"] <= now:
            raise ValueError("ticket expired")
        if doc["n"] in self.used:
            raise ValueError("ticket already used")
        self.used[doc["n"]] = doc["exp"]
        return doc["u"]
