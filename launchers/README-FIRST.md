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

## 2. Nothing to install

The Windows release carries its own Python: the folder `runtime` holds Python 3.14.8 for 64-bit Windows,
the official build of python.org (published by the Python Software Foundation), with AquaCrop already
installed in it. You do **not** install Python, and it does not matter whether the PC has a Python or
not: the launcher uses only its own. Nothing is downloaded.

The illustrated guide, `owner-tour/index.html`, starts with these steps (section *Starting
AquaCrop*), with the text the launcher window prints on a first start and pictures of the browser
screens. The Windows dialogs (Properties, Extract All, the security question) are described in words
below, not photographed: this build was not rehearsed on a Windows desktop with a camera.

## 3. Start the demo

1. Before extracting: right-click the ZIP, **Properties**, tick **Unblock** at the bottom (if it is
   there), **OK**. This avoids a Windows security question on every start.
2. Right-click the ZIP, **Extract All…**, and choose a folder that is **not** synced by OneDrive, for
   example `C:\AquaCrop`. Use the whole extracted folder; do not start anything from inside the ZIP.
3. Open the extracted folder `aquacrop-<version>` and double-click **`AquaCrop-Demo.cmd`**.
   - If Windows asks "Do you want to run this file?" or shows "Windows protected your PC", the file
     came from the internet: choose **Run** (or **More info**, then **Run anyway**). Unblocking the ZIP
     (step 1) avoids it.
   - A black window opens. Its first line is `Using the Python bundled with this release`. It lists
     its checks: release, Python, packages, pages, configuration `installation-80`, data folder, port.
4. **First start only**, in the same window: create the administrator account. There is no default
   password.
   - **Login name**: 2 to 32 characters, letters, digits and `. _ -`; no spaces or accents. Example:
     `jose`. The window suggests a name in brackets, e.g. `[admin]`; press Enter to accept it. If a name cannot be used, the window
     says why and suggests one (for `José` it suggests `jose`).
   - **Password**: at least 12 characters, and it must not contain the login name. Three or more
     unrelated words joined by dashes make a good password. **Nothing appears while you type it**;
     that is normal. Type it twice. If it cannot be used, the window says why and asks again.
5. The browser opens at <http://127.0.0.1:8000/>. Sign in with the login name and password. The blue
   banner says `SIMULATION — DEMO MODEL`.
6. In the browser, open **Getting started** (first item of the menu): four short tasks. Then follow
   the tour: `owner-tour/index.html` (illustrated, offline) or `docs/operator/walkthrough.md`. Record
   results in `owner-tour/results.html`. That page keeps nothing by itself: press **Download
   results** and send the downloaded file. A first-time operator who tries the four Getting started
   tasks records them in the separate part at the bottom of the same page, so their results stay apart
   from yours. `owner-checklist.csv` is the same list for a spreadsheet; report findings with
   `owner-feedback-template.md`.

AquaCrop is served on this PC only (127.0.0.1). It never opens a serial port in this mode.

## 4. Which build is running

- In the browser: **Settings**, card **Versions**. *Release* shows the version, source commit, build
  time and **wheel verified** when the program in `runtime` is exactly the one this folder was built
  with. *Python* shows 3.14.8 and the `runtime` folder. *Config*
  shows `installation-80@…`, *Inventory* `6 modules: 16 AI + 32 DI + 32 DO = 80 points`.
- In the launcher window: the line `AquaCrop <version>, release <version>, source commit …: VERIFIED`.
- If it says **MISMATCH** (the program does not belong to this folder) or **MISSING** (`RELEASE.json`
  is gone), extract the whole ZIP again into a new folder and start from there.

## 5. Where your data is

| What | Where |
|---|---|
| Accounts, history, recipes, stock acknowledgements | `%LOCALAPPDATA%\AquaCrop\demo\installation-80.sqlite3` |
| Local backups (and any pre-migration safety copy) | `%LOCALAPPDATA%\AquaCrop\demo\backups\` |
| Log file | `%LOCALAPPDATA%\AquaCrop\demo\logs\aquacrop-launch.log` |
| The program and its Python (no data) | the extracted release folder, `runtime` |

Your data is never written into the release folder. When a start fails, the last lines of the window
name the data folder and the log file actually in use. Type `%LOCALAPPDATA%\AquaCrop` in the Explorer
address bar to open the folder. Never delete the `demo` folder: it is your data.

## 6. Stop and start again

- Stop: press **Ctrl+C** in the launcher window, or close the window. It prints "AquaCrop stopped
  cleanly".
- Start again: double-click `AquaCrop-Demo.cmd`. It does not ask for the administrator again. Every
  process starts stopped; nothing restarts by itself.

## 7. Upgrading from an earlier demo (0.3.1 or 0.3.2)

1. Optional but sensible: in the old version, Settings, *Create backup now*.
2. Stop the old version (Ctrl+C in its window). Extract the new ZIP into a **new** folder and
   double-click its `AquaCrop-Demo.cmd`.
3. The new release runs on its own `runtime`; the Python 3.12 that 0.3.1 or 0.3.2 needed is no longer
   used, and you may keep or uninstall it. The old program environments under
   `%LOCALAPPDATA%\AquaCrop\venv-…` hold no data; you can delete them later.
4. The same data folder is used: your accounts, history and recipes are kept. If a release changes
   the database format, AquaCrop first writes a full safety copy to `demo\backups\pre-migrate-…`
   (0.3.1 and 0.3.2 to 0.3.3 do not change it).
5. Check Settings, Versions: the new version and **wheel verified**.

## 8. A separate trial data folder

To try things on a clean slate without touching your normal data, start from a Command Prompt in the
release folder:

```text
AquaCrop-Demo.cmd --state-dir "%LOCALAPPDATA%\AquaCrop\demo-trial"
```

It asks for a first administrator for that folder, and a failed start names that folder and its
log file. Run only one AquaCrop at a time (they share the
browser's sign-in cookie). To return to the normal data, start `AquaCrop-Demo.cmd` without options.

## 9. If something goes wrong

| Message | What to do |
|---|---|
| `This folder has no bundled Python (runtime)` | The folder is incomplete: extract the whole ZIP again into a new folder |
| `"José" cannot be used: it contains a space, an accent…` | Press Enter to take the suggested login name, or type another one without spaces or accents |
| `That password cannot be used: …` | The reason follows (too short, or it contains the login name): type another password |
| `[FAIL] port 8000 on 127.0.0.1 is free` followed by "another program (or AquaCrop already running) uses it" | Another AquaCrop window is still open: close it and start again. Or start with `AquaCrop-Demo.cmd --port 8010` and open <http://127.0.0.1:8010/> |
| `MISMATCH` or `MISSING` in the release check | The program or `RELEASE.json` does not match this folder: extract the whole ZIP into a new folder and start from there |
| `requirements.lock is missing` / `Cannot find the AquaCrop wheel` | You started a single file: extract the whole ZIP |
| Windows asks "Do you want to run this file?" on every start | Unblock the ZIP before extracting (section 3, step 1), extract again into a new folder |
| The browser does not open | Open <http://127.0.0.1:8000/> yourself |
| "Access is denied" (WinError 5) while running the developer test suite inside a OneDrive folder | OneDrive locks files while syncing. Use a folder outside OneDrive. The demo itself keeps its data in `%LOCALAPPDATA%` |

`AquaCrop-Demo.cmd --check-only` runs the checks and starts nothing.

## 10. macOS or Linux

`./aquacrop-demo.sh` does the same (data in `~/.local/share/aquacrop/demo` or
`~/Library/Application Support/AquaCrop/demo`) with a Python 3.11 to 3.14 already on the computer. There
is no bundled Python for these; it installs the locked packages from the internet on the first start.

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
