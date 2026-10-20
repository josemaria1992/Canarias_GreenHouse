"""One controller owner per installation/bus: an exclusive OS file lock held for the process lifetime."""
from __future__ import annotations

import os
import re
from pathlib import Path


class OwnershipError(RuntimeError):
    pass


class OwnerLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> "OwnerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise OwnershipError(f"another process already owns {self.path.name}; refusing to start a second owner")
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def lock_name(kind: str, ident: str) -> str:
    return f"{kind}-{re.sub(r'[^A-Za-z0-9_.-]', '_', ident)}.lock"


def port_key(port: str) -> str:
    """One lock per physical port: a device path is resolved, so /dev/serial/by-id/... and /dev/ttyUSB0 share it."""
    return os.path.realpath(port) if port.startswith("/") else port


def acquire_installation(state_dir: Path, cfg) -> list[OwnerLock]:
    locks = [OwnerLock(state_dir / lock_name("config", cfg.config_id)).acquire()]
    lock_root = Path(os.environ.get("AQUACROP_LOCK_DIR", state_dir))
    try:
        for b in cfg.buses:
            if b.port:  # serial buses: one owner per physical port across configurations
                locks.append(OwnerLock(lock_root / lock_name("port", port_key(b.port))).acquire())
    except OwnershipError:
        for lk in locks:
            lk.release()
        raise
    return locks
