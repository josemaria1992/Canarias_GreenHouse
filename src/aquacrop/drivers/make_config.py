"""`aquacrop hw make-config`: a bench configuration from the `hw setup` records (ADR-0016).

The configuration is HARDWARE_READ_ONLY: it reads every module, and writes only under a bench lease or
as a safe-state OFF. Device ids, addresses, models, analog ranges and labels come from the records,
the ports from the arguments (each one proven with `hw scan`). Digital modules (Relay (D), IO 8CH) go
on one port and the analog modules on another, when a second port is given. The result is validated
before it is written; nothing is sent to any module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HEADER = """\
# Bench configuration made by `aquacrop hw make-config` from the module setup records:
#   {records}
# HARDWARE_READ_ONLY: AquaCrop reads every module. Outputs switch only under a bench lease armed in the
# browser by a commissioning user, or OFF at the safe-state moments below (ADR-0016).
# Nothing here assigns a channel to a plant signal: assignments are made and verified on the I/O board.
"""


def add_parser(hw) -> None:
    mk = hw.add_parser("make-config", help="write a bench configuration from hw setup records (sends nothing)")
    mk.add_argument("records", nargs="+", help="setup-*.json files written by hw setup")
    mk.add_argument("--digital-port", required=True, help="port of the Relay (D) and IO 8CH modules, e.g. /dev/ttyAMA1")
    mk.add_argument("--analog-port", default=None, help="port of the AI 8CH modules (default: the digital port)")
    mk.add_argument("--port-evidence", default=None, help="how the ports were checked, e.g. 'hw scan 2026-11-02'")
    mk.add_argument("--config-id", default="bench-malmo")
    mk.add_argument("--timezone", default="Europe/Stockholm")
    mk.add_argument("--host", action="append", default=[], help="LAN name or address the browser uses (repeatable)")
    mk.add_argument("--out", required=True)
    mk.set_defaults(fn=cmd_make_config)


def build(records: list[dict], *, digital_port: str, analog_port: str | None, config_id: str, timezone: str,
          port_evidence: str | None, hosts: list[str], version: str) -> dict:
    from ..config.catalog import CATALOG, ModelId
    two = analog_port is not None and analog_port != digital_port
    serial = {"baudrate": 9600, "parity": "N", "bytesize": 8, "stopbits": 1, "timeout_s": 0.3, "read_retries": 1}
    buses = [{"bus_id": "rs485_a", "kind": "rtu", "port": digital_port, "port_evidence": port_evidence,
              "serial": serial, "poll_interval_s": 1.0}]
    if two:
        buses.append({"bus_id": "rs485_b", "kind": "rtu", "port": analog_port, "port_evidence": port_evidence,
                      "serial": serial, "poll_interval_s": 1.0})
    devices, seen_names = [], set()
    for r in records:
        if r.get("result") != "DONE":
            raise ValueError(f"{r.get('name')}: the setup record says {r.get('result')}, not DONE")
        model = ModelId(r["model"])
        after = r["after"]
        name = r["name"]
        if name in seen_names:
            raise ValueError(f"two records are named {name}")
        seen_names.add(name)
        d = {"device_uid": name, "bus_id": "rs485_b" if two and model == ModelId.WAVESHARE_AI_8CH else "rs485_a",
             "address": after["address"], "model": model.value, "sku": CATALOG[model].sku,
             "label": r.get("label") or name,
             "firmware_evidence": f"version register {after.get('version_register')} (hw setup {r['utc'][:10]})"}
        if model == ModelId.WAVESHARE_AI_8CH:
            d["hardware_variant"] = {"A": "STANDARD", "B": "B"}.get(r.get("ai_variant") or "", "UNKNOWN")
            modes = after.get("modes") or []
            d["ai"] = {i + 1: {"mode": str(m) if 0 <= m <= 4 else "UNKNOWN"} for i, m in enumerate(modes)}
        devices.append(d)
    hosts = list(dict.fromkeys(["127.0.0.1", "localhost"] + hosts))
    origins = ["http://127.0.0.1:8000", "http://localhost:8000"] + [f"https://{h}" for h in hosts[2:]]
    return {
        "schema_version": 1, "config_id": config_id, "config_version": version, "profile": "HARDWARE_READ_ONLY",
        "label": "REAL BENCH — read only, outputs only under a bench lease",
        "site": {"name": "AquaCrop bench", "timezone": timezone},
        "buses": buses, "devices": devices,
        "scaling_profiles": [], "virtual_mapping": [], "physical_mapping": [], "alarm_rules": [],
        "drive_adapters": [{"adapter_id": "MAIN", "kind": "NOT_CONFIGURED", "signals": ["drive.MAIN.run"],
                            "note": "the drive's register map is pending CP-06"}],
        "safe_state": {"outputs_off_on": ["START", "STOP", "LEASE_END", "MODULE_RETURN"]},
        "historian": {"db_path": f"aquacrop-{config_id}.sqlite3", "recording_interval_s": 60, "retention_days": 365,
                      "retention_provenance": "APPROVED", "db_budget_bytes": 18253611008,
                      "free_space_warning_bytes": 4294967296, "free_space_reserve_bytes": 2147483648,
                      "snapshot_floor_bytes": 536870912},
        "security": {"allowed_hosts": hosts, "allowed_origins": origins},
    }


def cmd_make_config(args) -> int:
    import yaml

    from ..config.schema import InstallationConfig
    try:
        records = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.records]
    except (OSError, ValueError) as e:
        print(f"cannot read a record: {e}", file=sys.stderr)
        return 2
    from datetime import datetime, timezone
    version = "bench-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    try:
        data = build(records, digital_port=args.digital_port, analog_port=args.analog_port, config_id=args.config_id,
                     timezone=args.timezone, port_evidence=args.port_evidence, hosts=args.host, version=version)
        InstallationConfig.model_validate(data)
    except ValueError as e:
        print(f"not written: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.write_text(HEADER.format(records=", ".join(Path(p).name for p in args.records))
                   + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{out}: {len(data['devices'])} modules on {len(data['buses'])} port(s), config_version {version}",
          file=sys.stderr)
    return 0
