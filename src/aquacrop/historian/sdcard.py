"""Identity of the SD card that receives backups (ADR-0012).

Before anything is written, the configured mount path must be a mounted filesystem, read-write, on a
different filesystem from the live database, backed by the block device whose filesystem UUID the
owner confirmed, and carrying the volume marker written to that card at enrolment. When the card or
its USB reader is missing, the mount path is an empty directory on the eMMC: writing there would
silently fill the eMMC, so every check fails closed and nothing falls back to another location.

The checks use the kernel's view (stat device numbers, /proc/self/mountinfo, /dev/disk/by-uuid);
`VolumeProbe` is replaceable so the failure cases can be tested without a physical card. A physical
card in the USB reader on the CM4 is still to be tested (CP-00 checklist).
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

MARKER = ".aquacrop-backup-volume.json"


class VolumeError(RuntimeError):
    """The configured card cannot be used; `code` is one of SD_CARD_MISSING, SD_CARD_WRONG,
    SD_CARD_READ_ONLY, SD_CARD_NOT_CONFIGURED."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class VolumeProbe:
    """The kernel facts the identity check needs (Linux). Other platforms report the card as missing."""

    def is_mount(self, path: Path) -> bool:
        return os.path.ismount(path)

    def device_of(self, path: Path) -> int:
        return os.stat(path).st_dev

    def device_for_uuid(self, uuid: str) -> int | None:
        node = Path("/dev/disk/by-uuid") / uuid
        try:
            return os.stat(node).st_rdev
        except OSError:
            return None

    mountinfo = "/proc/self/mountinfo"

    def mount_entry(self, path: Path) -> dict | None:
        """The mountinfo line for exactly this mount point: options and filesystem type."""
        try:
            lines = Path(self.mountinfo).read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        want = Path(path).as_posix()           # mountinfo is Linux's; as_posix keeps tests portable
        for line in reversed(lines):          # the last entry for a mount point is the visible one
            left, _, right = line.partition(" - ")
            f = left.split()
            if len(f) < 6:
                continue
            mp = f[4].replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")
            if mp == want:
                r = right.split()
                return {"options": set(f[5].split(",")), "fstype": r[0] if r else None,
                        "source": r[1] if len(r) > 1 else None}
        return None

    def usage(self, path: Path):
        return shutil.disk_usage(path)


@dataclass(frozen=True)
class Card:
    mount_path: Path
    device: int
    filesystem_uuid: str
    volume_id: str
    fstype: str | None
    free_bytes: int
    total_bytes: int

    def view(self) -> dict:
        return {"mount_path": str(self.mount_path), "filesystem_uuid": self.filesystem_uuid,
                "volume_id": self.volume_id, "fstype": self.fstype, "free_bytes": self.free_bytes,
                "total_bytes": self.total_bytes}


def _mounted_device(mount: Path, uuid: str, probe: VolumeProbe, live_paths: list[Path]) -> tuple[int, dict]:
    if not probe.is_mount(mount):
        raise VolumeError("SD_CARD_MISSING", f"{mount} is not a mounted filesystem: the SD card or its USB reader "
                          "is missing or not mounted. Nothing was written, and nothing falls back to the eMMC.")
    dev = probe.device_of(mount)
    for p in live_paths:
        if p.exists() and probe.device_of(p) == dev:
            raise VolumeError("SD_CARD_WRONG", f"{mount} is on the same filesystem as the live database ({p}); "
                              "refusing to write backups there")
    want = probe.device_for_uuid(uuid)
    if want is None:
        raise VolumeError("SD_CARD_WRONG", f"a filesystem is mounted at {mount}, but no attached filesystem has the "
                          f"confirmed UUID {uuid}: another card or disk is mounted there")
    if want != dev:
        raise VolumeError("SD_CARD_WRONG", f"the filesystem mounted at {mount} is not the confirmed card "
                          f"(UUID {uuid} is a different device)")
    entry = probe.mount_entry(mount) or {}
    if "rw" not in entry.get("options", set()):
        raise VolumeError("SD_CARD_READ_ONLY", f"the card at {mount} is mounted read-only (write-protect switch, "
                          "filesystem errors, or mount options); nothing was written")
    return dev, entry


def verify_card(sd, *, live_paths: list[Path], probe: VolumeProbe | None = None) -> Card:
    """Prove that the configured card is the mounted, writable filesystem at sd.mount_path. Raises
    VolumeError; writes nothing."""
    probe = probe or VolumeProbe()
    if not (sd.mount_path and sd.filesystem_uuid and sd.volume_id):
        raise VolumeError("SD_CARD_NOT_CONFIGURED", "the SD card is not confirmed yet (mount path, filesystem UUID "
                          "and volume id are TBD); see `offsite enroll-card`")
    mount = Path(sd.mount_path)
    dev, entry = _mounted_device(mount, sd.filesystem_uuid, probe, live_paths)
    try:
        marker = json.loads((mount / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise VolumeError("SD_CARD_WRONG", f"the card at {mount} has no readable enrolment marker ({MARKER}); "
                          "it was not enrolled with `offsite enroll-card`") from None
    if marker.get("volume_id") != sd.volume_id or marker.get("filesystem_uuid") != sd.filesystem_uuid:
        raise VolumeError("SD_CARD_WRONG", f"the card at {mount} carries volume {marker.get('volume_id')}, not the "
                          f"confirmed {sd.volume_id}")
    u = probe.usage(mount)
    return Card(mount, dev, sd.filesystem_uuid, sd.volume_id, entry.get("fstype"), u.free, u.total)


def on_card(card: Card, path: Path, probe: VolumeProbe | None = None) -> bool:
    """True when `path` (which must exist) is on the verified card's filesystem."""
    probe = probe or VolumeProbe()
    return probe.device_of(path) == card.device


def enroll_card(mount_path: str, filesystem_uuid: str, *, live_paths: list[Path], note: str = "",
                probe: VolumeProbe | None = None, utc: str | None = None) -> dict:
    """Write the enrolment marker on a card the owner chose (after the same mount/UUID/read-write
    checks). Idempotent: a card already enrolled keeps its volume id. Returns the config values."""
    from .schema import utc_iso
    probe = probe or VolumeProbe()
    mount = Path(mount_path)
    _mounted_device(mount, filesystem_uuid, probe, live_paths)
    mk = mount / MARKER
    if mk.exists():
        cur = json.loads(mk.read_text(encoding="utf-8"))
        if cur.get("filesystem_uuid") != filesystem_uuid:
            raise VolumeError("SD_CARD_WRONG", f"{mk} belongs to filesystem {cur.get('filesystem_uuid')}; the card "
                              "was reformatted or cloned. Remove the marker deliberately to enrol it again.")
        marker, created = cur, False
    else:
        marker = {"volume_id": "vol_" + secrets.token_hex(8), "filesystem_uuid": filesystem_uuid,
                  "enrolled_utc": utc or utc_iso(), "purpose": "AquaCrop historian backups", "note": note}
        tmp = mk.with_name(mk.name + ".tmp")
        tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        with open(tmp, "r+b") as f:
            os.fsync(f.fileno())
        os.replace(tmp, mk)
        created = True
    return {"created": created, "marker": marker,
            "config": {"mount_path": str(mount), "filesystem_uuid": filesystem_uuid,
                       "volume_id": marker["volume_id"]}}
