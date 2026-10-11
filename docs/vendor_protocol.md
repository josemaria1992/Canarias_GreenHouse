# Vendor Modbus protocol: sources and limits

**Status: indexed vendor documentation, not a physically validated firmware map.** Every value
below must be confirmed against the shipped modules during milestone 5 (procedure CP-01) and the
firmware version recorded per device.

## Sources consulted

| Source | URL | Retrieved | Revision / version | Access notes |
|---|---|---|---|---|
| Modbus RTU Relay (D) wiki | <https://www.waveshare.com/wiki/Modbus_RTU_Relay_(D)> | 2026-10-11 | MediaWiki `oldid=107665`; "Development Protocol V2" | Fetched through a summarising web reader. Direct HTTP from the build container was refused by its network policy (proxy 403). No page date shown |
| Modbus RTU Analog Input 8CH wiki | <https://www.waveshare.com/wiki/Modbus_RTU_Analog_Input_8CH> | 2026-10-11 | `oldid=110988`; "Development Protocol V2" | Same as above |
| Analog 8CH product page (variant table) | <https://www.waveshare.com/product/modbus-rtu-analog-input-8ch.htm> | not retrieved | — | Blocked by network policy; variant facts below come from the wiki |
| CM4-ETH-RS485-BOX-B wiki | <https://www.waveshare.com/wiki/CM4-ETH-RS485-BOX-B> | 2026-10-11 | Redirects to "CM4-ETH-RS485-BASE-B", `oldid=104318` | Fetched through the summarising reader. UART/tty facts are recorded in `installation/cm4.md` as candidates to verify on the unit |
| USB TO RS485 (C) | <https://docs.waveshare.com/USB_TO_RS485_C> | not retrieved | — | Blocked |
| PyModbus 3.11.3 | installed package source (`pymodbus==3.11.3`) | 2026-10-11 | 3.11.3 | API checked against the installed code, not the website |
| Modbus over serial line V1.02 | <https://www.modbus.org/file/secure/modbusoverserial.pdf> | not retrieved | V1.02 | Standard facts used: unicast 1–247, CRC low byte first |

The V1 protocol is a separate vendor document. Modules shipping V1 firmware may not match the V2
tables. An unknown firmware version needs an explicit compatible profile before use.

## Relay (D), SKU 26517 (V2 table)

| Operation | FC | Address (zero-based) / count | Used by this software |
|---|---:|---|---|
| Read coils (relay state) | 01 | `0x0000`, 8 | Every poll (read-only) |
| Read discrete inputs | 02 | `0x0000`, 8 | Every poll |
| Read control modes | 03 | `0x1000`, 8 | Every poll and immediately before each bench write |
| Write single coil | 05 | `channel-1`; `0xFF00` ON, `0x0000` OFF | Bench lease only |
| Toggle value `0x5500` on FC05 | 05 | — | **Never used** (no toggle commands exist) |
| All relays `0x00FF` | 05 | — | **Never used** |
| Flash on/off `0x0200`/`0x0400` | 05 | — | **Never used** |
| Write multiple coils | 0F | — | Not implemented (optional in the brief; ADR-005) |
| Set control mode | 06/10 | `0x1000` | **Never used** |
| UART parameters | 03 / 06 | `0x2000` | Read by diagnostics only |
| Device address | 03 / 06 | `0x4000` | Read by diagnostics only; readdressing is a manual procedure |
| Software version | 03 | `0x8000` | Read by diagnostics only |

Control modes: 0 Normal, 1 Linkage, 2 Toggle, 3 Edge. The wiki says only Linkage blocks command
control. This software is stricter, as the brief requires: all eight channels must read Normal,
otherwise bench writes are refused. In Linkage mode a DI can switch a relay with no software
involvement, so read-only software does not guarantee a quiet relay.

## Analog Input 8CH, SKU 25821 (V2 table)

| Operation | FC | Address / count | Used |
|---|---:|---|---|
| Read channel values | 04 | `0x0000`, 8 (unsigned) | Every poll |
| Read data types | 03 | `0x1000`, 8 | Every poll; compared with the configured mode |
| Set data type | 06/10 | `0x1000` | **Never used** |
| UART / address / version | 03 | `0x2000` / `0x4000` / `0x8000` | Diagnostics only |

| Type | Standard (A) | B variant | Raw unit |
|---|---|---|---|
| 0 | 0–5 V → 0–5000 | 0–10 V → 0–10000 | mV |
| 1 | 1–5 V → 1000–5000 | 2–10 V → 2000–10000 | mV |
| 2 | 0–20 mA → 0–20000 | same | µA |
| 3 | 4–20 mA → 4000–20000 | same | µA |
| 4 | raw code 0–4096 | same | code |

Both variants are 12-bit. Current inputs need the channel jumper fitted, voltage inputs need it
removed; a wrong jumper gives wrong data with no error. The software therefore treats the jumper as
physical evidence to record in CP-01, not something it can detect.

## Documentation inconsistencies found (do not resolve by assumption)

| Item | What the vendor text says | How the software handles it |
|---|---|---|
| Raw code endpoint | Range stated as 0–4096, conversion divides by 4095 | Raw code is stored unscaled; no conversion is applied |
| Parity code | Request table: `0x01` even; response table: `0x01` odd (both pages) | UART settings are never written; record actual behaviour in CP-01 |
| Version example | Table: `0x0064` = V1.00; relay frame example returns `0x00C8` = V2.00 | Version reported raw and labelled unverified |
| Address/baud examples | Several frames are sent to address `0x00` (broadcast) | Address 0 is refused before anything is sent |
| FC02 example | The brief cites an all-low DI example `01 01 01 00 51 88`, which is an FC01 frame. The current relay page shows `01 02 01 01 60 48` (DI1 set) | An FC01 reply to an FC02 request is rejected as wrong function (test AT-MB-03) |
| Fault current | Neither page says what an open loop or over-range current reads | Out-of-range raw values keep INVALID quality and are never clamped; real behaviour must be measured in CP-02 |

## Library behaviour that the adapter compensates for

Checked against `pymodbus==3.11.3` with the real `ModbusSerialClient` and RTU framer over an
in-memory serial port (`tests/fakes/protocol_aquafake.py`):

- A register reply with fewer registers than requested, or none, is accepted by the library. The
  adapter requires exactly the requested count.
- A reply carrying a different read function code (for example an FC01 reply to an FC02 request)
  is returned as a successful response. The adapter checks the function code.
- A reply from another unit address, a bad CRC or a truncated frame produces "no response". The
  adapter reports `NO_RESPONSE`.
- The library's retry loop also retries writes. The adapter sets library retries to 0, retries
  reads itself a bounded number of times, and never retries writes.
- The FC05 echo is decoded as "any non-zero value is ON". The adapter does not treat the echo as
  proof; every bench write is followed by an FC01 readback.

Request vectors match the brief byte for byte (`tests/hardware/test_modbus.py::test_at_mb_01_*`).
