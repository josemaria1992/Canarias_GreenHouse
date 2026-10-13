# AquaCrop greenhouse platform: read this first

This release is software and simulation only. It has not been tested on the CM4, the I/O modules, the
drive, the probes or a real OneDrive account. Physical AUTO does not exist in this release, and no
setting enables it. What is proven and what is still open is in `docs/ACCEPTANCE.md` and
`docs/RELEASE_READINESS.md`; the test results of this build are in `VERIFICATION.md` when present.

The normal demo is **installation-80**: the full simulated installation with its six modules and 80 I/O
points (16 AI + 32 DI + 32 DO). The older 48- and 24-point configurations are kept only as legacy test
fixtures.

## 1. Check the download

`aquacrop-<version>.zip.sha256`, published next to the ZIP, holds the ZIP's SHA-256. Inside the folder,
`MANIFEST.sha256` lists every file and `RELEASE.json` gives the version, source commit and build time.

- Windows (PowerShell): `Get-FileHash .\aquacrop-<version>.zip -Algorithm SHA256` and compare.
- Linux: `sha256sum -c MANIFEST.sha256` inside the extracted folder.

## 2. Before the first start (Windows): 64-bit Python 3.12 with the py launcher

The bundled offline packages are for **64-bit Python 3.12**. Install it once from python.org; a Python
that came with another program (for example a coding assistant) or from the Microsoft Store is not
what the launcher looks for.

1. Open <https://www.python.org/downloads/windows/>, choose the latest **Python 3.12** release and its
   **Windows installer (64-bit)**.
2. Run it. Leave the option at the bottom of the first page that installs the **py launcher** ticked,
   then choose **Install Now**. Adding python.exe to PATH is not needed.
3. Check: open Command Prompt and type `py --list`. A line with `3.12` must appear.

(Reference: "Using Python on Windows", <https://docs.python.org/3.12/using/windows.html>.)

## 3. Start the demo

1. Extract the ZIP (right-click, *Extract All…*) to a folder that is **not** synced by OneDrive, for
   example `C:\AquaCrop\aquacrop-<version>`. Use the whole folder; do not start it from inside the ZIP.
2. Double-click `AquaCrop-Demo.cmd`.
   - The first start prepares a private Python environment under `%LOCALAPPDATA%\AquaCrop` from the
     bundled `wheelhouse-win_amd64-py312` folder. No internet is needed. This takes a minute or two.
   - It lists its checks: release, Python, packages, pages, configuration `installation-80`, data
     folder, port.
   - **First start only**: "create the administrator account (there is no default password)". Type a
     user name (Enter keeps `admin`), then a password of at least 12 characters, twice. The password
     is not shown while you type.
3. The browser opens at <http://127.0.0.1:8000/>. Sign in. The blue banner says
   `SIMULATION — DEMO MODEL`.
4. Follow the tour: `owner-tour/index.html` (illustrated, offline) or `docs/operator/walkthrough.md`.
   Record results in `owner-checklist.csv`; report findings with `owner-feedback-template.md`.

AquaCrop is served on this PC only (127.0.0.1). It never opens a serial port in this mode.

## 4. Which build is running

- In the browser: **Settings**, card **Versions**. *Release* shows the version, source commit, build
  time and **wheel verified** when the installed program is exactly the one in this folder. *Config*
  shows `installation-80@…`, *Inventory* `6 modules: 16 AI + 32 DI + 32 DO = 80 points`.
- In the launcher window: the line `AquaCrop <version>, release <version>, source commit …: VERIFIED`.
- If it says **MISMATCH** (the environment does not belong to this folder) or **MISSING** (`RELEASE.json`
  is gone), extract the whole ZIP again into a new folder and start from there.

## 5. Where your data is

| What | Where |
|---|---|
| Accounts, history, recipes, stock acknowledgements | `%LOCALAPPDATA%\AquaCrop\demo\installation-80.sqlite3` |
| Local backups (and any pre-migration safety copy) | `%LOCALAPPDATA%\AquaCrop\demo\backups\` |
| Log file | `%LOCALAPPDATA%\AquaCrop\demo\logs\aquacrop-launch.log` |
| Program environments (no data) | `%LOCALAPPDATA%\AquaCrop\venv-aquacrop_greenhouse-<version>-…-<12 hex digits>` |

Nothing is written into the extracted release folder. Type `%LOCALAPPDATA%\AquaCrop` in the Explorer
address bar to open the folder. Never delete the `demo` folder: it is your data.

## 6. Stop and start again

- Stop: press **Ctrl+C** in the launcher window, or close the window. It prints "AquaCrop stopped
  cleanly".
- Start again: double-click `AquaCrop-Demo.cmd`. It does not ask for the administrator again. Every
  process starts STOPPED; nothing restarts by itself.

## 7. Upgrading from an earlier demo (for example a provisional 0.3.1)

1. Optional but sensible: in the old version, Settings, *Create backup now*.
2. Stop the old version. Extract the new ZIP into a **new** folder and double-click its
   `AquaCrop-Demo.cmd`.
3. The launcher names each environment after the exact program file (its SHA-256), so it always
   prepares a new environment for a new build; the old environment is left as it is (it holds no
   data; you can delete old `venv-…` folders later).
4. The same data folder is used: your accounts, history and recipes are kept. If a release changes
   the database format, AquaCrop first writes a full safety copy to `demo\backups\pre-migrate-…`
   (0.3.1 to 0.3.2 does not change it).
5. Check Settings, Versions: the new version and **wheel verified**.

## 8. A separate trial data folder

To try things on a clean slate without touching your normal data, start from a Command Prompt in the
release folder:

```text
AquaCrop-Demo.cmd --state-dir "%LOCALAPPDATA%\AquaCrop\demo-trial"
```

It asks for a first administrator for that folder. Run only one AquaCrop at a time (they share the
browser's sign-in cookie). To return to the normal data, start `AquaCrop-Demo.cmd` without options.

## 9. If something goes wrong

| Message | What to do |
|---|---|
| `AquaCrop needs Python 3.11, 3.12 or 3.13 (64-bit), and none was found` | Install Python 3.12 (64-bit) as in section 2, then start again |
| `[FAIL] port 8000 on 127.0.0.1 is free` followed by "another program (or AquaCrop already running) uses it" | Another AquaCrop window is still open: close it and start again. Or start with `AquaCrop-Demo.cmd --port 8010` and open <http://127.0.0.1:8010/> |
| `MISMATCH` or `MISSING` in the release check | The program or `RELEASE.json` does not match this folder: extract the whole ZIP into a new folder and start from there |
| `Installation failed; the incomplete environment was removed` | Extract the ZIP again (a file is missing or blocked, for example by antivirus) and start again |
| `requirements.lock is missing` / `Cannot find the AquaCrop wheel` | You started a single file: extract the whole ZIP |
| The browser does not open | Open <http://127.0.0.1:8000/> yourself |
| "Access is denied" (WinError 5) while running the developer test suite inside a OneDrive folder | OneDrive locks files while syncing. Use a folder outside OneDrive. The demo itself keeps its data in `%LOCALAPPDATA%` |

`AquaCrop-Demo.cmd --check-only` runs the checks and starts nothing.

## 10. macOS or Linux

`./aquacrop-demo.sh` does the same (data in `~/.local/share/aquacrop/demo` or
`~/Library/Application Support/AquaCrop/demo`). There is no offline package set for these; it installs
the locked packages from the internet on the first start.

## 11. Controller (CM4)

Follow `docs/installation/cm4.md` in order: flash the eMMC, verify the serial ports, then run the
guided installer from this folder. The bundled `wheelhouse-aarch64-py311` matches Raspberry Pi OS
Bookworm (64-bit, Python 3.11); confirm the actual OS and Python on the CM4 at commissioning.

```text
sudo python3 deploy/aquacrop-cm4.py check   --release .
sudo python3 deploy/aquacrop-cm4.py install --release . --config /etc/aquacrop/hardware-read-only.yaml
```

The controller starts read-only: `REAL — READ ONLY`.

- Remote access through your OpenVPN: `docs/security/remote-access.md`.
- Backups to the SD card and OneDrive: `docs/operations/offsite-backups.md`. They are off until they
  are set up on site.
- Upgrade and rollback: `docs/operations/service.md`.
- Hardware arrival steps: `docs/commissioning/procedures.md`.
