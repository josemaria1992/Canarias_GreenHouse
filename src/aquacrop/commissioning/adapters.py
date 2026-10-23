"""USB serial adapters on the controller, for the commissioning wizard.

Reads `/dev/serial/by-id` and sysfs only; opens nothing. What it reports (path, by-id name, USB vendor,
product, serial) identifies the adapter, never the module wired to it. No vendor or product id is
required: any USB serial device is listed, and a port that a configured installation bus uses is shown as
such and cannot be chosen. The root directory is a parameter so tests can build a fake /dev and /sys.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from ..ownership import OwnerLock, OwnershipError, lock_name, port_key


@dataclass(frozen=True)
class Adapter:
    path: str                       # /dev/ttyUSB0
    by_id: str | None               # /dev/serial/by-id/usb-FTDI_..._BG03Z87K-if00-port0 (stable across reboots)
    manufacturer: str | None
    product: str | None
    serial: str | None
    vendor_id: str | None
    product_id: str | None
    driver: str | None
    readable: bool                  # the service user may open it (dialout)
    writable: bool
    used_by_bus: str | None         # a configured installation bus already uses this port
    owned_by_other: bool            # another process holds AquaCrop's lock for this port

    @property
    def stable_id(self) -> str:
        return self.by_id or self.path

    def public(self) -> dict:
        d = asdict(self)
        d["stable_id"] = self.stable_id
        d["selectable"] = self.readable and self.writable and not self.used_by_bus and not self.owned_by_other
        d["why_not"] = (None if d["selectable"] else
                        f"installation bus {self.used_by_bus} uses this port" if self.used_by_bus else
                        "another process holds this port" if self.owned_by_other else
                        "the service has no permission to open it (dialout group)")
        d["note"] = "USB details identify the adapter, not the module wired to it"
        return d


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


def _usb_attrs(sys_root: Path, tty: str) -> dict:
    """Walk up from /sys/class/tty/<tty>/device to the USB device that has idVendor."""
    dev = sys_root / "class" / "tty" / tty / "device"
    try:
        cur = dev.resolve()
    except OSError:
        return {}
    driver = None
    drv = dev / "driver"
    if drv.exists():
        driver = drv.resolve().name
    for _ in range(6):
        if (cur / "idVendor").exists():
            return {"manufacturer": _read(cur / "manufacturer"), "product": _read(cur / "product"),
                    "serial": _read(cur / "serial"), "vendor_id": _read(cur / "idVendor"),
                    "product_id": _read(cur / "idProduct"), "driver": driver}
        if cur.parent == cur:
            break
        cur = cur.parent
    return {"driver": driver}


def _held(lock_root: Path, port: str) -> bool:
    lk = OwnerLock(lock_root / lock_name("port", port_key(port)))
    try:
        lk.acquire()
    except OwnershipError:
        return True
    except OSError:
        return False
    lk.release()
    return False


def enumerate_adapters(cfg, *, root: str | Path = "/", lock_root: str | Path | None = None) -> list[Adapter]:
    root = Path(root)
    dev, sys_root = root / "dev", root / "sys"
    by_id = {}
    sid = dev / "serial" / "by-id"
    if sid.is_dir():
        for link in sorted(sid.iterdir()):
            try:
                target = Path(os.path.realpath(link))
            except OSError:
                continue
            by_id[target.name] = "/dev/serial/by-id/" + link.name
    names = sorted({p.name for pat in ("ttyUSB*", "ttyACM*") for p in dev.glob(pat)} | set(by_id))
    used = {}
    for b in cfg.buses:
        if b.port:
            used[Path(os.path.realpath(b.port)).name if b.port.startswith("/") else b.port] = b.bus_id
    out = []
    for name in names:
        if not (name.startswith("ttyUSB") or name.startswith("ttyACM")):
            continue
        path = dev / name
        attrs = _usb_attrs(sys_root, name)
        out.append(Adapter(path=f"/dev/{name}", by_id=by_id.get(name), manufacturer=attrs.get("manufacturer"),
                           product=attrs.get("product"), serial=attrs.get("serial"), vendor_id=attrs.get("vendor_id"),
                           product_id=attrs.get("product_id"), driver=attrs.get("driver"),
                           readable=os.access(path, os.R_OK), writable=os.access(path, os.W_OK),
                           used_by_bus=used.get(name),
                           owned_by_other=_held(Path(lock_root), f"/dev/{name}") if lock_root else False))
    return out
