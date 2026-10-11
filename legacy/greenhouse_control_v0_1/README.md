# AquaCrop Greenhouse Control — v0.1

**Runnable simulation prototype** for AquaCrop Technologies AB, Malmö. Python FastAPI, HTML dashboard, SQLite logger, simulated 8 analog/8 digital inputs/8 relay outputs, password-protected local/remote browser controls, virtual two-sector AUTO, alarms and Waveshare Modbus RTU read-only drivers.

**Important:** simulation-tested only. Real hardware has NOT been connected or commissioned. No physical irrigation output mapping, live automatic motor operation, or safety interlocks exist yet. Read `SPECIFICATION.md` and `HARDWARE_COMMISSIONING.md` before field testing.

## 1. Start it now on your computer

Install **Python 3.11 or newer**. Extract this ZIP. Open a terminal in its root directory. Then:

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export HYDRO_PASSWORD='CHANGE-THIS-TO-YOUR-OWN-UNIQUE-LONG-PASSWORD'
python -m greenhouse
```

**Windows PowerShell**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
$env:HYDRO_PASSWORD="CHANGE-THIS-TO-YOUR-OWN-UNIQUE-LONG-PASSWORD"
python -m greenhouse
```

Open **http://127.0.0.1:8000** in a browser. Username is `operator`. Password is the strong password you set above. The server deliberately refuses to start without a password of at least 12 characters.

In the browser: test every DI/AI/relay channel, switch between MANUAL and virtual AUTO, inject overflow/no-flow faults, review the event log and export CSV. Sensor channels are simply `AI1..AI8` because the project I/O mapping has not been assigned. Default simulated analog is 12 mA and simulated raw-range alarms are 4–20 mA.

Run the automated tests:

```bash
python -m pytest -q
```

If port 8000 is already used, run `python -m greenhouse --port 8001`.

## 2. Architecture and logging

**Web:** FastAPI + a self-contained dashboard served locally, with HTTP Basic auth on every API endpoint. No CDN or cloud requirement. **Driver:** simulation or real Modbus on explicitly configured serial bus. **History:** each 3-second poll stores one complete timestamped snapshot in SQLite, including unchanged values. Every manual write, setting change, mode change and alarm transition logs an event. Disconnected hardware produces an ERROR record with null I/O values, not fabricated readings.

- **Manual simulation:** operate each of eight digital outputs and inject every analog/digital input.
- **Automatic simulation:** alternates *virtual* sectors 1 and 2 based on configurable demonstration timings; deliberately does NOT switch physical or simulated outputs until channel mapping is approved.
- **Alarm demo:** analog raw limits, simulated tank overflow, simulated no flow while virtual irrigating. Any active alarm ends AUTO.
- **Real Modbus:** read 8 analog input registers + types, read 8 DI, read 8 relay coils. By default writes are **locked**. Physical AUTO is always locked in v0.1.

Database: `./data/greenhouse.sqlite3` (SQLite journal companions `-wal`/`-shm` may appear). Samples and events are retained without automatic deletion. UI graph shows recent records and `/api/export.csv` exports up to the 20,000 latest samples. Full database remains on disk; implement controlled rotating backup/retention/storage alerts before 24/7 use.

## 3. Access from another computer or from home

**Recommended:** install Tailscale on both Raspberry Pi and the computers/phones authorized to operate it, restrict tailnet access and use Tailscale Serve to expose the local port to tailnet members, not the public internet. Keep the server bound to 127.0.0.1 when using Serve. See [Tailscale's private web-server sharing guide](https://tailscale.com/docs/share-web-server). Network connectivity must never be a prerequisite for local polling/control.

For a physically trusted test LAN, `python -m greenhouse --host 0.0.0.0` allows browser access via `http://PI_LAN_IP:8000` from other devices. **This is unencrypted HTTP Basic authorization on the LAN**; do not use on untrusted networks, do not port-forward it, and use firewall rules/HTTPS or VPN for actual operation. Basic Auth is not a substitute for network segmentation, TLS and authorization review.

## 4. Later deploy on your CM4

The ordered CM4004032 has 4 GB RAM and **32 GB built-in eMMC, not an SD-booting Lite version**. First install Raspberry Pi OS 64-bit on the eMMC, and verify its network configuration. Then install the app in a dedicated virtual environment as above, using a durable data folder and systemd service; a service-file template is included in `deploy/` and must be edited for your actual username, location and environment file. Start in simulation even on Pi. Follow `HARDWARE_COMMISSIONING.md` for UART/RS485 bring-up and how to switch to **read-only** Modbus, using confirmed device IDs. Only isolated bench tests may enable manual relay writes.

**Known project gap:** only 8 relay outputs bought versus 9 valves previously discussed, not counting pump control. There is no purchased analog output module. FNIRSI SG-003A injects electrical signals during physical testing; it is not a Pi-controllable Modbus analog-output module.

## 5. Open questions that matter before real automation

- Complete mapping and naming AI1–8, DI1–8, DO1–8 to sensor/actuator tags.
- Specify what happens with overflow, no flow, dry run, communications failure, power failure and manual mode changes.
- Confirm whether actuators need normally-open or normally-closed relay contacts, feedback contacts, and separate hardwired safety chains.
- Confirm pump/VFD hardware models, drive control interface and relay expansion purchases.
- Define real irrigation intervals, sequencing, nutrient dosing and reset/restart behavior.
- Commission actual module RS485 addresses/serial settings, AI jumpers and sensor engineering calibration.
- Design backups, alert delivery, security hardening and long-term disk monitoring.

## Source tree

```
greenhouse/config.py      environment + hardware safety guards
greenhouse/driver.py      simulation + Waveshare Modbus drivers
greenhouse/controller.py  mode machine + alarms + IO state
greenhouse/storage.py     SQLite snapshots + event log + CSV
greenhouse/app.py         authenticated API endpoints
greenhouse/__main__.py    CLI web server
static/index.html         single-page dashboard with graph
tests/                    controller and API tests
deploy/                   systemd example
```
