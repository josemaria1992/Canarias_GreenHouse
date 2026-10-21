"""`aquacrop hw ports`: are the CM4-ETH-RS485-BOX-B ports ready? Read-only (ADR-0017).

The box's COM1 to COM3 are the CM4's PL011 UARTs 3, 4 and 5, which Raspberry Pi OS (Bookworm and later)
names /dev/ttyAMA1 to /dev/ttyAMA3 once `dtoverlay=uart3`, `uart4` and `uart5` are in the boot
configuration. COM0 is the mini-UART (ttyS0), whose speed follows the core clock and which the serial
console uses: AquaCrop does not use it. This command only reads: the boot configuration, the device
nodes, whether this user may open them, the kernel command line, the USB serial adapters and the port
locks. It changes nothing; the report says what to change by hand (docs/installation/cm4.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BOX = {"COM1": ("/dev/ttyAMA1", "dtoverlay=uart3"), "COM2": ("/dev/ttyAMA2", "dtoverlay=uart4"),
       "COM3": ("/dev/ttyAMA3", "dtoverlay=uart5")}
PLAN = {"COM1": "2 Relay (D) + 2 IO 8CH", "COM2": "2 AI 8CH", "COM3": "kept free (the pump drive's Modbus link, CP-06)"}
BOOT_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")


def add_parser(hw) -> None:
    p = hw.add_parser("ports", help="check the RS485 ports of the CM4 box (reads only)")
    p.add_argument("--boot-config", default=None, help="the boot configuration (default: the one this OS uses)")
    p.add_argument("--dev", default="/dev", help=argparse.SUPPRESS)       # tests only
    p.add_argument("--state-dir", default="var")
    p.add_argument("--need", default="COM1,COM2", help="ports that must be ready (default COM1,COM2)")
    p.set_defaults(fn=cmd_ports)


def active_lines(text: str) -> set[str]:
    return {ln.split("#", 1)[0].strip().replace(" ", "") for ln in text.splitlines() if ln.split("#", 1)[0].strip()}


def _held(lock: Path) -> bool:
    """True when another AquaCrop process owns the port (its lock file exists and is locked)."""
    from ..ownership import OwnerLock, OwnershipError
    if not lock.exists():
        return False
    try:
        OwnerLock(lock).acquire().release()
        return False
    except OwnershipError:
        return True
    except OSError:
        return False


def report(boot_config: Path | None, dev: Path, state_dir: str) -> dict:
    from ..ownership import lock_name, port_key
    boot = boot_config if boot_config else next((Path(p) for p in BOOT_CANDIDATES if Path(p).is_file()), None)
    lines = active_lines(boot.read_text(encoding="utf-8", errors="replace")) if boot and boot.is_file() else set()
    ports = {}
    for com, (node, overlay) in BOX.items():
        path = dev / Path(node).name
        exists = path.exists()
        lock = Path(os.environ.get("AQUACROP_LOCK_DIR", state_dir)) / lock_name("port", port_key(str(path)))
        ports[com] = {"device": str(path), "overlay": overlay, "overlay_in_boot_config": overlay in lines,
                      "exists": exists, "can_open": exists and os.access(path, os.R_OK | os.W_OK),
                      "in_use_by_aquacrop": _held(lock), "plan": PLAN[com]}
        ports[com]["ready"] = ports[com]["overlay_in_boot_config"] and ports[com]["can_open"]
    cmdline = Path("/proc/cmdline").read_text() if Path("/proc/cmdline").is_file() else ""
    usb = sorted(str(p) for p in (dev / "serial" / "by-id").glob("*")) if (dev / "serial" / "by-id").is_dir() else []
    usb += sorted(str(p) for p in dev.glob("ttyUSB*"))
    return {"boot_config": str(boot) if boot else None, "ports": ports,
            "com0": {"device": str(dev / "ttyS0"), "used": False,
                     "serial_console": "console=serial0" in cmdline or "console=ttyS0" in cmdline},
            "usb_adapters": usb}


def fixes(rep: dict, need: list[str]) -> list[str]:
    out = []
    miss = [p["overlay"] for p in rep["ports"].values() if not p["overlay_in_boot_config"]]   # COM3 too: harmless, kept ready
    if miss:
        where = rep["boot_config"] or "/boot/firmware/config.txt"
        out.append(f"add these lines at the end of {where} (after a line [all]), then reboot: " + ", ".join(miss))
    for c in need:
        p = rep["ports"][c]
        if p["overlay_in_boot_config"] and not p["exists"]:
            out.append(f"{c}: {p['device']} does not exist; reboot after editing the boot configuration, then "
                       f"check `ls -l /dev/ttyAMA*`")
        elif p["exists"] and not p["can_open"]:
            out.append(f"{c}: this user may not open {p['device']}: add it to the dialout group "
                       f"(sudo usermod -aG dialout $USER, then log in again); the service user already is")
    return out


def cmd_ports(args) -> int:
    need = [x.strip().upper() for x in args.need.split(",") if x.strip()]
    rep = report(Path(args.boot_config) if args.boot_config else None, Path(args.dev), args.state_dir)
    rep["fixes"] = fixes(rep, need)
    rep["ready"] = all(rep["ports"][c]["ready"] for c in need if c in rep["ports"])
    print(json.dumps(rep, indent=1))
    for f in rep["fixes"]:
        print("to do: " + f, file=sys.stderr)
    print("ports ready: " + ", ".join(need) if rep["ready"] else "ports not ready", file=sys.stderr)
    return 0 if rep["ready"] else 1
