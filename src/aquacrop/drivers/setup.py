"""`aquacrop hw scan` and `aquacrop hw setup`: give one Waveshare module its address, modes and analog
ranges before it joins the shared bus (ADR-0017, CP-01). This is the only code that writes a module's
address register (0x4000) or its channel mode registers (0x1000..0x1007).

Rules, whatever the arguments:
* `scan` only reads. `setup` refuses unless exactly one module answers in the scanned address range,
  so it cannot change the address of a module it was not meant for. Nothing is ever sent to the
  broadcast address 0, which every module on the wire would obey.
* Each register write is FC06, sent once, to the module's own address, through a bus that accepts only
  the registers this run needs. Every change is read back; the address change is proven by the module
  answering at the new address and no longer at the old one.
* The UART register (0x2000) is never written: the bus speed stays what the module already uses.
* It takes the port's ownership lock, so it cannot run while `serve` or `diagnostics` owns the port.

Output is a JSON record per run (before, after, each write and its readback): the CP-01 evidence.
pymodbus and pyserial are imported only when a command runs.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ADDRESS_REGISTER = 0x4000
UART_REGISTER = 0x2000
VERSION_REGISTER = 0x8000
MODE_REGISTER_START = 0x1000
CHANNELS = 8

MODELS = {"relay": "WAVESHARE_MODBUS_RTU_RELAY_D", "io": "WAVESHARE_MODBUS_RTU_IO_8CH",
          "ai": "WAVESHARE_MODBUS_RTU_ANALOG_INPUT_8CH"}
# AI 8CH data type register values (docs/vendor_protocol.md); the jumper inside the module must match.
AI_RANGES = {"0-5V": 0, "0-10V": 0, "1-5V": 1, "2-10V": 1, "0-20mA": 2, "4-20mA": 3, "raw": 4}
AI_VOLTAGE_BY_VARIANT = {"A": {"0-5V", "1-5V"}, "B": {"0-10V", "2-10V"}}


def add_parser(sub) -> None:
    hw = sub.add_parser("hw", help="set up modules and make a bench configuration").add_subparsers(
        dest="sub", required=True)
    sc = hw.add_parser("scan", help="list the modules that answer on a port (reads only)")
    _port_args(sc)
    sc.add_argument("--first", type=int, default=1)
    sc.add_argument("--last", type=int, default=32)
    sc.set_defaults(fn=cmd_scan)

    st = hw.add_parser("setup", help="set one module's address, modes and analog ranges; only one module connected")
    _port_args(st)
    st.add_argument("--model", required=True, choices=sorted(MODELS), help="relay = Relay (D), io = IO 8CH, ai = AI 8CH")
    st.add_argument("--address", type=int, required=True, help="the new address, 1..247")
    st.add_argument("--name", required=True, help="a short name, e.g. relay_A; it becomes the device id")
    st.add_argument("--label", default=None, help="what is written on the module's sticker")
    st.add_argument("--ai-range", action="append", default=[], metavar="CH=RANGE",
                    help=f"AI only: e.g. 1=4-20mA or all=4-20mA; ranges {', '.join(AI_RANGES)}")
    st.add_argument("--ai-variant", choices=["A", "B"], help="AI only: A = 0-5 V version, B = 0-10 V version")
    st.add_argument("--first", type=int, default=1)
    st.add_argument("--last", type=int, default=32)
    st.add_argument("--record", default=None, help="where to write the JSON record (default: setup-<name>.json)")
    st.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    st.set_defaults(fn=cmd_setup)

    from .make_config import add_parser as add_make
    from .ports import add_parser as add_ports
    add_make(hw)
    add_ports(hw)


def _port_args(p) -> None:
    p.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0 (USB TO RS485) or /dev/ttyAMA1 (COM1)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--timeout", type=float, default=0.2)
    p.add_argument("--state-dir", default="var")


class SetupError(Exception):
    pass


def _bus(port: str, baud: int, timeout: float, writes: frozenset = frozenset(), *, client_factory=None):
    from ..config.schema import BusConfig, SerialSettings
    from .rtu import RtuBus
    cfg = BusConfig(bus_id="setup", kind="rtu", port=port, poll_interval_s=1.0,
                    serial=SerialSettings(baudrate=baud, timeout_s=timeout, read_retries=1))
    kw = {"client_factory": client_factory} if client_factory else {}
    return RtuBus(cfg, register_writes=writes, **kw)


def _lock(state_dir: str, port: str):
    from ..ownership import OwnerLock, lock_name, port_key
    root = Path(os.environ.get("AQUACROP_LOCK_DIR", Path(state_dir).resolve()))
    return OwnerLock(root / lock_name("port", port_key(port))).acquire()


def read_one(bus, unit: int, register: int) -> int | None:
    from .rtu import FC_READ_HOLDING_REGISTERS, RtuError
    try:
        return bus.read_registers(FC_READ_HOLDING_REGISTERS, unit, register, 1)[0]
    except RtuError:
        return None


def identify(bus, unit: int) -> dict:
    """What answers at `unit`: its address register, version, UART register, kind and channel modes."""
    from .rtu import FC_READ_DISCRETE_INPUTS, FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS, RtuError
    out: dict = {"address": unit, "address_register": read_one(bus, unit, ADDRESS_REGISTER),
                 "version_register": read_one(bus, unit, VERSION_REGISTER),
                 "uart_register": read_one(bus, unit, UART_REGISTER)}
    try:
        bus.read_registers(FC_READ_INPUT_REGISTERS, unit, 0, CHANNELS)
        out["kind"] = "analog"
    except RtuError:
        try:
            bus.read_bits(FC_READ_DISCRETE_INPUTS, unit, 0, CHANNELS)
            out["kind"] = "digital"
        except RtuError:
            out["kind"] = "unknown"
    try:
        out["modes"] = bus.read_registers(FC_READ_HOLDING_REGISTERS, unit, MODE_REGISTER_START, CHANNELS)
    except RtuError:
        out["modes"] = None
    return out


def scan(bus, first: int, last: int) -> list[dict]:
    if not 1 <= first <= last <= 247:
        raise SetupError("scan range must be within 1..247")
    found = []
    for unit in range(first, last + 1):
        if read_one(bus, unit, ADDRESS_REGISTER) is not None:
            found.append(identify(bus, unit))
    return found


def parse_ai_ranges(items: list[str], variant: str | None) -> dict[int, str]:
    out: dict[int, str] = {}
    for item in items:
        ch, _, rng = item.partition("=")
        if rng not in AI_RANGES:
            raise SetupError(f"unknown analog range {rng!r}: use one of {', '.join(AI_RANGES)}")
        if rng.endswith("V") and (variant is None or rng not in AI_VOLTAGE_BY_VARIANT[variant]):
            raise SetupError(f"{rng} needs --ai-variant {'A' if rng in AI_VOLTAGE_BY_VARIANT['A'] else 'B'} "
                             "(it depends on the module version printed on it)")
        chans = range(1, CHANNELS + 1) if ch == "all" else [int(ch)] if ch.isdigit() else []
        if not chans or any(not 1 <= c <= CHANNELS for c in chans):
            raise SetupError(f"channel {ch!r} must be 1..8 or all")
        for c in chans:
            out[c] = rng
    return out


def plan_setup(found: list[dict], model: str, new_address: int, ai_ranges: dict[int, str]) -> dict:
    """Checks before anything is written; returns what will be written."""
    if not 1 <= new_address <= 247:
        raise SetupError("the new address must be 1..247")
    if not found:
        raise SetupError("no module answers in the scanned range; check the wiring, power, port and baud rate")
    if len(found) > 1:
        raise SetupError(f"{len(found)} modules answer (addresses {', '.join(str(f['address']) for f in found)}); "
                         "connect only the module to set up")
    mod = found[0]
    want = "analog" if model == "ai" else "digital"
    if mod["kind"] != want:
        raise SetupError(f"the connected module looks {mod['kind']}, not {want}; check --model")
    if model != "ai" and ai_ranges:
        raise SetupError("--ai-range applies to the analog module only")
    if mod["modes"] is None:
        raise SetupError("the module did not report its channel modes; nothing was written")
    modes = {c: 0 for c in range(1, CHANNELS + 1)} if model != "ai" else \
        {c: AI_RANGES[r] for c, r in ai_ranges.items()}
    writes = [(MODE_REGISTER_START + c - 1, v) for c, v in sorted(modes.items()) if mod["modes"][c - 1] != v]
    return {"current_address": mod["address"], "new_address": new_address, "mode_writes": writes, "before": mod}


def apply_setup(bus, plan: dict) -> dict:
    """The writes, each once, each read back. Mode registers are written at the current address first,
    then the address register; the result is read at the new address."""
    from .rtu import RtuError
    cur, new = plan["current_address"], plan["new_address"]
    log = []
    for reg, val in plan["mode_writes"]:
        try:
            bus.write_single_register(cur, reg, val)
            echo = "echoed"
        except RtuError as e:
            echo = f"{e.kind}: {e}"
        rb = read_one(bus, cur, reg)
        log.append({"register": f"0x{reg:04X}", "value": val, "echo": echo, "readback": rb, "ok": rb == val})
        if rb != val:
            raise SetupError(f"mode register 0x{reg:04X} reads {rb} after writing {val}; stopped", log)
    if new != cur:
        try:
            bus.write_single_register(cur, ADDRESS_REGISTER, new)
            echo = "echoed"
        except RtuError as e:     # some firmware answers from the new address: verify below
            echo = f"{e.kind}: {e} (verified below)"
        at_new = read_one(bus, new, ADDRESS_REGISTER)
        at_old = read_one(bus, cur, ADDRESS_REGISTER)
        ok = at_new == new and at_old is None
        log.append({"register": f"0x{ADDRESS_REGISTER:04X}", "value": new, "echo": echo,
                    "readback_at_new": at_new, "answers_at_old": at_old is not None, "ok": ok})
        if not ok:
            raise SetupError(f"the module does not answer at address {new} only (new: {at_new}, old answers: "
                             f"{at_old is not None}); power-cycle it and run hw scan", log)
    after = identify(bus, new)
    return {"writes": log, "after": after}


def _confirm(text: str) -> bool:
    print(text, file=sys.stderr)
    print("Type YES to continue: ", end="", file=sys.stderr, flush=True)
    try:
        return input().strip() == "YES"
    except EOFError:
        return False


def cmd_scan(args) -> int:
    from .rtu import RtuRefused
    from ..ownership import OwnershipError
    try:
        lk = _lock(args.state_dir, args.port)
    except OwnershipError as e:
        print(str(e), file=sys.stderr)
        return 4
    bus = None
    try:
        bus = _bus(args.port, args.baud, args.timeout)
        found = scan(bus, args.first, args.last)
        print(json.dumps({"port": args.port, "baud": args.baud, "range": [args.first, args.last], "found": found,
                          "writes_sent": bus.stats.write_requests}, indent=1))
        return 0 if found else 1
    except (SetupError, RtuRefused) as e:
        print(str(e), file=sys.stderr)
        return 2
    finally:
        if bus is not None:
            bus.close()
        lk.release()


def cmd_setup(args) -> int:
    from .rtu import RtuRefused
    from ..ownership import OwnershipError
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{0,47}", args.name):
        print("--name must start with a letter and use letters, digits, _ or - (e.g. relay_A)", file=sys.stderr)
        return 2
    try:
        ranges = parse_ai_ranges(args.ai_range, args.ai_variant)
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        lk = _lock(args.state_dir, args.port)
    except OwnershipError as e:
        print(str(e), file=sys.stderr)
        return 4
    rec: dict = {"record": "aquacrop module setup (CP-01)", "utc": datetime.now(timezone.utc).isoformat(),
                 "port": args.port, "baud": args.baud, "name": args.name, "label": args.label,
                 "model": MODELS[args.model], "ai_variant": args.ai_variant,
                 "ai_ranges": {str(k): v for k, v in sorted(ranges.items())}}
    path = Path(args.record or f"setup-{args.name}.json")
    bus = None
    try:
        probe = _bus(args.port, args.baud, args.timeout)
        try:
            found = scan(probe, args.first, args.last)
        finally:
            probe.close()
        plan = plan_setup(found, args.model, args.address, ranges)
        rec["before"] = plan["before"]
        what = [f"address {plan['current_address']} -> {plan['new_address']}"] if plan["new_address"] != plan["current_address"] else []
        what += [f"register 0x{r:04X} = {v}" for r, v in plan["mode_writes"]]
        if not what:
            print("the module already has this address and these modes; nothing to write", file=sys.stderr)
        elif not args.yes and not _confirm(f"One module answers on {args.port}, at address {plan['current_address']}. "
                                           f"This will write: {'; '.join(what)}."):
            print("cancelled; nothing was written", file=sys.stderr)
            return 3
        cur, new = plan["current_address"], plan["new_address"]
        writes = frozenset({(cur, r) for r, _ in plan["mode_writes"]} | {(cur, ADDRESS_REGISTER)})
        bus = _bus(args.port, args.baud, args.timeout, writes)
        result = apply_setup(bus, plan) if what else {"writes": [], "after": plan["before"]}
        rec |= result | {"result": "DONE", "writes_sent": bus.stats.write_requests}
        if args.model == "ai" and ranges:
            rec["note"] = ("set the current/voltage jumper of each channel inside the module to match its range; "
                           "the software cannot see the jumper")
        return 0
    except SetupError as e:
        rec |= {"result": "STOPPED", "reason": str(e.args[0])}
        if len(e.args) > 1:
            rec["writes"] = e.args[1]
        print(str(e.args[0]), file=sys.stderr)
        return 2
    except RtuRefused as e:
        rec |= {"result": "STOPPED", "reason": str(e)}
        print(str(e), file=sys.stderr)
        return 2
    finally:
        if bus is not None:
            rec.setdefault("writes_sent", bus.stats.write_requests)
            bus.close()
        lk.release()
        path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        print(f"record written to {path}", file=sys.stderr)
