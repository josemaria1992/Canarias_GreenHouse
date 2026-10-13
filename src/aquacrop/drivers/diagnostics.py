"""`aquacrop diagnostics read`: intrinsically read-only module inspection (brief §6).

Uses only the read operations of `waveshare.WaveshareDevice` (FC01/02/03/04). It cannot write: no
coil, register, address, baud-rate or mode write exists on the path, whatever the arguments. It takes
the installation's port ownership lock, so it cannot run while `serve` owns the bus.

A drive with a Modbus register map (`drive:<id>`, ADR-0015) is read the same way: its status word,
decoded with the configured masks, its diagnostic registers and its control register, through
`vfd.VfdDevice.read_status` and one FC03 read; this is how CP-06 checks a PROPOSED map before any
bench write. Nothing on this path calls a write.

Output is one JSON object per device per cycle. Values are raw; nothing is scaled, and metadata
registers are reported as read, without interpretation (they are unverified vendor facts).
pymodbus and pyserial are imported only when the command runs.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def add_parser(sub) -> None:
    d = sub.add_parser("diagnostics", help="read-only module inspection on a real bus").add_subparsers(
        dest="sub", required=True)
    r = d.add_parser("read", help="read DI/coils/relay modes or AI values/modes; never writes")
    r.add_argument("--config", required=True)
    r.add_argument("--state-dir", default="var")
    r.add_argument("--device", action="append", help="device_uid or drive:<adapter_id> (repeatable); default: all")
    r.add_argument("--cycles", type=int, default=1)
    r.add_argument("--interval", type=float, default=1.0)
    r.add_argument("--metadata", action="store_true", help="also read optional 0x2000/0x4000/0x8000 registers")
    r.set_defaults(fn=cmd_diagnostics_read)


def read_device(dev_cfg, wd, *, metadata: bool) -> dict:
    from ..config.catalog import CATALOG, DIGITAL_IO_MODELS
    out: dict = {"device_uid": dev_cfg.device_uid, "bus_id": dev_cfg.bus_id, "address": dev_cfg.address,
                 "model": dev_cfg.model.value, "utc": _utc(),
                 "output_type": CATALOG[dev_cfg.model].output_type}
    steps = ([("discrete_inputs", wd.read_discrete_inputs), ("coils", wd.read_coils),
              ("relay_modes", lambda: wd.read_relay_modes().describe())]
             if dev_cfg.model in DIGITAL_IO_MODELS else
             [("ai_modes", lambda: [m.name for m in wd.read_ai_modes()]), ("ai_raw", wd.read_analog)])
    ok = True
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as e:
            ok = False
            out[name] = None
            out.setdefault("errors", {})[name] = f"{getattr(e, 'kind', type(e).__name__)}: {e}"
    if metadata:
        out["metadata_unverified"] = wd.read_metadata()
    out["ok"] = ok
    if dev_cfg.model in DIGITAL_IO_MODELS and out.get("relay_modes") is not None:
        out["bench_preflight"] = "PASS: all 8 NORMAL" if all(m == "NORMAL" for m in out["relay_modes"]) else \
            "BLOCKED: not all channels NORMAL"
    return out


def read_drive(adapter, dv) -> dict:
    m = adapter.modbus
    out: dict = {"device_uid": adapter.target, "bus_id": m.bus_id, "address": m.address, "model": m.drive_model,
                 "register_map": m.provenance, "manual_reference": m.manual_reference, "utc": _utc()}
    try:
        out["status"] = dv.read_status().view()
        ok = True
    except Exception as e:
        out["status"], ok = None, False
        out.setdefault("errors", {})["status"] = f"{getattr(e, 'kind', type(e).__name__)}: {e}"
    try:      # informational: many drives let the control word be read back, some do not
        out["control_register"] = dv.bus.read_registers(3, m.address, m.control_register, 1)[0]
    except Exception as e:
        out["control_register"] = f"unavailable ({getattr(e, 'kind', type(e).__name__)})"
    out["ok"] = ok
    return out


def cmd_diagnostics_read(args) -> int:
    from ..cli import _cfg
    from ..ownership import OwnershipError, acquire_installation
    cfg = _cfg(args.config)
    if any(b.kind != "rtu" for b in cfg.buses):
        print("diagnostics read needs a configuration with rtu buses only (simulation has nothing to read)",
              file=sys.stderr)
        return 2
    drives = {a.target: a for a in cfg.drive_adapters if a.kind == "MODBUS_RTU"}
    wanted = set(args.device or [d.device_uid for d in cfg.devices] + list(drives))
    unknown = wanted - {d.device_uid for d in cfg.devices} - set(drives)
    if unknown:
        print(f"unknown device(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if not 1 <= args.cycles <= 10_000 or not 0.1 <= args.interval <= 3600:
        print("cycles must be 1..10000 and interval 0.1..3600 s", file=sys.stderr)
        return 2
    try:
        locks = acquire_installation(Path(args.state_dir).resolve(), cfg)
    except OwnershipError as e:
        print(str(e), file=sys.stderr)
        return 4
    from .rtu import RtuBus, RtuRefused
    from .vfd import VfdDevice
    from .waveshare import WaveshareDevice
    buses = {}
    failures = 0
    try:
        try:
            buses = {b.bus_id: RtuBus(b) for b in cfg.buses}
        except RtuRefused as e:
            print(str(e), file=sys.stderr)
            return 2
        devs = [(d, WaveshareDevice(buses[d.bus_id], d.model, d.address)) for d in cfg.devices
                if d.device_uid in wanted]
        vfds = [(a, VfdDevice(buses[a.modbus.bus_id], a.modbus)) for t, a in drives.items() if t in wanted]
        for i in range(args.cycles):
            recs = [read_device(d, wd, metadata=args.metadata and i == 0) for d, wd in devs]
            recs += [read_drive(a, dv) for a, dv in vfds]
            for rec in recs:
                rec["cycle"] = i + 1
                failures += not rec["ok"]
                print(json.dumps(rec), flush=True)
            if i + 1 < args.cycles:
                time.sleep(args.interval)
        print(json.dumps({"summary": {"reads_failed": failures, "frames": {k: vars(b.stats) for k, b in buses.items()},
                                      "writes_sent": sum(b.stats.write_requests for b in buses.values())}}))
        return 0 if failures == 0 else 1
    finally:
        for b in buses.values():
            b.close()
        for lk in locks:
            lk.release()


def _utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
