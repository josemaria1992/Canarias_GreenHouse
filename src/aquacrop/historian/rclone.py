"""The only path from the controller to the cloud copy: a narrow wrapper around the rclone CLI (ADR-0012).

- Uploads are `copyto` of one file to one name. Mirror operations (`sync`, `bisync`, `move`, `delete`,
  `purge`, ...) are not in the allowlist and cannot be issued from here: a local deletion or an empty
  card never propagates to the cloud.
- The remote must be an rclone `crypt` remote (client-side encryption: contents and file names are
  encrypted before they leave the controller). This is checked from `listremotes --long` before every
  upload batch, and `cryptcheck` (which only works through a crypt remote) verifies each uploaded file
  against the local one through the provider's checksums.
- Remote deletions happen only through `deletefile` of a single named copy, and only when the
  confirmed copy policy selects it (see offsite.py).
- Credentials stay in the rclone configuration file; nothing here logs or stores them.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ALLOWED = frozenset({"version", "listremotes", "copyto", "cryptcheck", "lsjson", "deletefile"})
FOLDER = "backups"


class RcloneError(RuntimeError):
    def __init__(self, message: str, *, code: str = "RCLONE_FAILED", returncode: int | None = None):
        super().__init__(message)
        self.code, self.returncode = code, returncode


def _tail(text: str, n: int = 600) -> str:
    t = (text or "").strip()
    return t[-n:]


@dataclass
class Rclone:
    binary: str
    config: str
    remote: str
    timeout_s: float = 3 * 3600.0

    def remote_path(self, name: str = "") -> str:
        base = self.remote if self.remote.endswith(":") else self.remote + "/"
        return f"{base}{FOLDER}/{name}" if name else f"{base}{FOLDER}"

    def _run(self, sub: str, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        if sub not in ALLOWED:
            raise ValueError(f"rclone {sub} is not allowed here (copy-only design; no mirror sync or bulk delete)")
        cmd = [self.binary, sub, *args, "--config", self.config, "--retries", "3", "--low-level-retries", "10",
               "--contimeout", "30s", "--timeout", "5m", "--stats", "0"]
        # Only the configuration file decides remotes and flags; RCLONE_CONFIG_PASS may unlock an
        # encrypted configuration file.
        env = {k: v for k, v in os.environ.items() if not k.startswith("RCLONE_") or k == "RCLONE_CONFIG_PASS"}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or self.timeout_s, env=env)
        except FileNotFoundError:
            raise RcloneError(f"rclone not found ({self.binary})", code="RCLONE_MISSING") from None
        except subprocess.TimeoutExpired:
            raise RcloneError(f"rclone {sub} timed out", code="TIMEOUT") from None
        if r.returncode != 0:
            raise RcloneError(f"rclone {sub} exited {r.returncode}: {_tail(r.stderr)}", returncode=r.returncode,
                              code=_classify(r.stderr))
        return r

    def check_crypt(self) -> None:
        """The configured remote must be a crypt remote (contents and names encrypted client-side)."""
        name = self.remote.split(":", 1)[0]
        out = self._run("listremotes", "--long", timeout=60).stdout
        kinds = {ln.split(":", 1)[0].strip(): ln.split(":", 1)[1].strip() for ln in out.splitlines() if ":" in ln}
        if name not in kinds:
            raise RcloneError(f"remote {name}: is not defined in {self.config}", code="NOT_CONFIGURED")
        if kinds[name] != "crypt":
            raise RcloneError(f"remote {name}: is type {kinds[name]!r}, not 'crypt'; refusing to upload "
                              "unencrypted backups", code="NOT_ENCRYPTED")

    def upload(self, local: Path, name: str) -> None:
        self._run("copyto", str(local), self.remote_path(name))

    def cryptcheck(self, local_dir: Path, names: list[str]) -> None:
        """Every named local file must exist in the cloud folder with matching (encrypted) checksums."""
        inc = [a for n in names for a in ("--include", "/" + _glob_escape(n))]
        self._run("cryptcheck", str(local_dir), self.remote_path(), "--one-way", *inc)

    def listing(self) -> list[dict]:
        out = self._run("lsjson", self.remote_path(), "--files-only", timeout=600).stdout
        return [{"name": e["Name"], "bytes": e.get("Size"), "modified": e.get("ModTime")}
                for e in json.loads(out or "[]")]

    def download(self, name: str, local: Path) -> None:
        self._run("copyto", self.remote_path(name), str(local))

    def delete(self, name: str) -> None:
        self._run("deletefile", self.remote_path(name), timeout=600)


def _glob_escape(name: str) -> str:
    return "".join("\\" + ch if ch in "*?[]{}\\" else ch for ch in name)


def _classify(stderr: str) -> str:
    s = (stderr or "").lower()
    if any(k in s for k in ("no such host", "network is unreachable", "dial tcp", "connection refused",
                            "temporary failure in name resolution", "i/o timeout", "tls handshake")):
        return "OFFLINE"
    if any(k in s for k in ("401", "unauthorized", "invalid_grant", "token", "couldn't fetch token")):
        return "AUTHORIZATION"
    if any(k in s for k in ("quota", "insufficient storage", "507")):
        return "CLOUD_FULL"
    return "RCLONE_FAILED"
