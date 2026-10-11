# AquaCrop Technologies AB — Greenhouse Control v0.1 specification

**Project:** Instalacion Invernadero hidroponia. **Baseline:** 2026-10-11.
**Scope:** Desktop development now, CM4/Modbus bring-up later. **Status:** prototype.

## Confirmed project constraints

Greenhouse approximately 90 x 45 m, 13 hydroponic channels, two irrigation sectors. Three-pump concept, nutrient tanks A/B/acid and nine solenoid valves are in the earlier project history, not a finalized channel-to-relay mapping. No equipment sequence, interlock list, or pump/VFD Modbus map has been confirmed.

## User decisions

- One Raspberry Pi CM4 hosts local control, storage and browser dashboard.
- Operator can read/write from another computer on the LAN and from home over an authenticated private network.
- MANUAL and AUTO modes. **AUTO on v0.1 is virtual only.**
- All eight analog samples and 16 digital states retained with timestamps every poll, even unchanged.
- Initial alarming: analog out-of-configured raw range, virtual tank overflow, virtual no-flow during watering, lost Modbus communications.
- No physical I/O mapping assumed. Real outputs are locked until isolated bench commissioning.

## Hardware inventory used

| Supplier | SKU | Item | Relevant interface |
|---|---|---|---|
| Waveshare | 22555 | CM4-ETH-RS485-BOX-B-EU | Dual Ethernet, 4 isolated RS485, 7–36 V supply |
| Waveshare | 26517 | Modbus RTU Relay (D) | 8 relay DO + 8 DI |
| Waveshare | 25821 | Modbus RTU Analog Input 8CH | 8 AI, configurable voltage/current |
| Waveshare | 34620 | USB TO RS485 (C) | Isolated USB-RS485 bench adapter |
| Waveshare | 18806 | CM4004032 | CM4, 4 GB RAM, 32 GB eMMC, no Wi-Fi |
| RS | 7755344 | RS PRO IP66 steel wall box | Cabinet (no software interface) |
| Electrokit | 41037459 | Mean Well NDR-240-24 | 24 V / 10 A supply |
| Elgiganten | 973355 | FNIRSI SG-003A | External signal generator (NOT Modbus output) |

The software currently addresses 8 AI + 8 DI + 8 relay DO = 24 points. No purchased analog output module is confirmed. Eight relays are less than nine valves, even before pump control.

## Architecture

Single process FastAPI/uvicorn + controller + Modbus/simulation driver. One poll task (no multiple workers). One SQLite DB with WAL. Browser UI served by Pi. Tailscale or similarly managed private VPN for home access. Password authentication required for all endpoints. Read-only by default for real devices. The virtual AUTO irrigation cycle alternates sector 1/sector 2 with configurable irrigation/pause times; **does not change physical or simulation relay channels** because mapping is not specified.

### Database

`sample`: UTC timestamp, analog values (8 raw), analog data types (8), digital inputs (8), relay status (8), mode, virtual sector, phase, quality. One full snapshot per poll. On communication failure the sample explicitly stores null signal arrays and `ERROR`; last known values are not falsely presented as current.

`events`: UTC timestamp, type, actor, human-readable change. Includes relay writes, input/value simulation changes, mode transitions, auto phases, configured limits, alarm raises/clears, software starts/stops.

`settings`: persisted alarm limits and simulated cycle timings.

No data is automatically deleted. Periodic backup and capacity alert are **future work**. CSV API currently exports the last 20,000 samples; use SQLite backup for full archive.

### Control states

- Startup: MANUAL/IDLE, no relay write.
- MANUAL (simulation): operator may toggle relay channels if connection good and no active alarms.
- AUTO (simulation only): virtual `IRRIGATING` (one sector) -> `PAUSE` -> alternate sector; timing is demonstration-only, not crop/plant recipe.
- Active alarm or disconnected bus during AUTO: transition to MANUAL/STOPPED_ALARM; no restart without operator command.
- REAL MODBUS: poll three supported read functions; manual relay writes denied until explicit bench unlock, automatic operation denied always in v0.1.
- System shutdown: no relay commands are sent. Physical fail-safe behavior needs separate hardware design; process shutdown does NOT guarantee de-energization.

### Analog input interpretation

Reading each channel's configured type from the module at holding register `0x1000..0x1007` is essential. Type 0/1 -> mV, 2/3 -> µA, 4 -> ADC code. The 4–20mA **simulation default only** uses 12000 µA and alarms outside 4000..20000 µA. These are raw module limits, **not engineering-unit alarms** and not sufficient for broken-wire or clinical/agronomic limit detection. Real mode starts with limits unspecified; configure channel-by-channel after jumper settings are physically checked.

## Non-goals for v0.1

No VFD control, pump contactor control, acid dosing, chemical safety, plant recipes, scheduling, role-based authorization, MQTT, cloud storage, alarm notifications, redundant control, UPS handling or field-qualified interlocks. No actual valve or tank input mapping. No automatic relay sequencing. No security penetration testing. No completed physical commissioning.

## Acceptance test checklist

1. Start simulation on a normal computer. Login and view 8 AI + 8 DI + 8 DO.
2. Inject raw analog values on AI1..AI8; verify exact readback and history.
3. Simulate DI1..DI8; verify exact readback and history.
4. Toggle DO1..DO8 in MANUAL; verify feedback and event entries.
5. Enter AUTO; confirm two virtual sectors alternate and **relays are not switched**.
6. Raise overflow/no-flow/analog-range faults; confirm alarm history and AUTO stops.
7. Test improper mode write, invalid channel, wrong credentials, and Modbus failure handling.
8. Download CSV; compare stored values and timestamps.
9. Deploy on CM4 with hardware read-only; identify serial device and node IDs; compare values to manufacturer utility.
10. Execute physically isolated dummy-load relay tests before any live actuator wiring; only then produce production I/O map and interlocks.
