"""Serve the HARDWARE_READ_ONLY profile over the in-memory fake RS485 bus for the browser checks.

Test infrastructure, run from the repository root: it uses `tests/fakes` (relay module at address 1,
analog module at address 2) and `tests/fixtures/hw-fake.yaml`, so no serial port or real module is
involved. A register-level drive stand-in answers at address 9 with a PROPOSED map, as on the Malmö bench
before CP-06. On exit it prints how many write frames reached the fake bus; the browser checks expect 0.

    AQ_PASS=... python scripts/ui_check/serve_fake_hardware.py --state-dir DIR
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    import serial
    import uvicorn
    import yaml

    from aquacrop.api.app import create_app
    from aquacrop.config.schema import InstallationConfig
    from aquacrop.security.auth import Role
    from tests.fakes import protocol_aquafake as pf

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    serial.protocol_handler_packages.append("tests.fakes")
    bus = pf.bus("uicheck")
    bus.add("relay", 1)
    bus.add("ai", 2)
    vfd = bus.add("vfd", 9)
    vfd.holding = {100: 0, 101: 1, 102: 0, 103: 0, 104: 0}
    data = yaml.safe_load((ROOT / "tests" / "fixtures" / "hw-fake.yaml").read_text(encoding="utf-8"))
    data["buses"][0]["port"] = "aquafake://uicheck"
    data["drive_adapters"] = [{"adapter_id": "MAIN", "kind": "MODBUS_RTU", "signals": ["drive.MAIN.run"], "modbus": {
        "bus_id": "rs485_a", "address": 9, "drive_model": "test double", "manual_reference": "tests/fakes",
        "control_register": 100, "run_sequence": [1, 3], "stop_value": 0, "status_register": 101,
        "running_mask": 4, "fault_mask": 8, "ready_mask": 1, "frequency": {"address": 102, "scale": 0.1, "unit": "Hz"},
        "comm_loss_timeout_s": 5, "comm_loss_action": "test double", "provenance": "PROPOSED"}}]
    cfg = InstallationConfig.model_validate(data)
    app = create_app(cfg, state_dir=args.state_dir, bind_host="127.0.0.1")
    app.state.accounts.create_user("admin", os.environ["AQ_PASS"], Role.ADMIN)

    def report(*_):
        print(f"fake bus write frames: {len(bus.write_frames())}", flush=True)
        raise SystemExit(0)
    # uvicorn shuts down gracefully on SIGTERM/SIGINT and then re-raises the signal to the handler that was
    # installed before it started, so the count is printed after the shutdown in either case
    signal.signal(signal.SIGTERM, report)
    signal.signal(signal.SIGINT, report)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    report()


if __name__ == "__main__":
    raise SystemExit(main())
