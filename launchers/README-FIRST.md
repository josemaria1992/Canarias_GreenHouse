# AquaCrop greenhouse platform: release folder

This release is software and simulation only. It has not been tested on the CM4, the I/O modules, the
drive, the probes or a real OneDrive account. Physical AUTO does not exist in this release, and no
setting enables it. What is proven and what is still open is in `docs/ACCEPTANCE.md` and
`docs/RELEASE_READINESS.md`.

## 1. Check the download

`MANIFEST.sha256` lists the SHA-256 of every file in this folder. `RELEASE.json` gives the version,
the source commit and the build time. The ZIP's own SHA-256 is published next to it
(`aquacrop-<version>.zip.sha256`).

- Windows (PowerShell): `Get-FileHash .\aquacrop-<version>.zip -Algorithm SHA256`
- Linux: `sha256sum -c MANIFEST.sha256` (run inside this folder)

## 2. Demo on a Windows PC (the full installation, simulated)

1. Install Python 3.12 from python.org. Keep the "py launcher" option ticked.
2. Double-click `AquaCrop-Demo.cmd`.
   - The first run prepares a private environment under `%LOCALAPPDATA%\AquaCrop`. It uses the bundled
     `wheelhouse-win_amd64-py312` folder, so no internet is needed.
   - It then asks you to choose the administrator password. There is no default password.
3. The browser opens at <http://127.0.0.1:8000/>. Sign in.
   - The history and the accounts are kept in `%LOCALAPPDATA%\AquaCrop\demo`.
   - The log is in the `logs` folder there.
4. To stop, press Ctrl+C in the window, or close it.

The walkthrough is in `docs/operator/walkthrough.md`. On macOS or Linux, use `./aquacrop-demo.sh` instead.

From a terminal you can do the same steps by hand:

```text
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse-win_amd64-py312 --require-hashes -r requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps wheels\aquacrop_greenhouse-<version>-py3-none-any.whl
.venv\Scripts\python.exe -m aquacrop launch
```

## 3. Controller (CM4)

Follow `docs/installation/cm4.md` in order: flash the eMMC, verify the serial ports, then run the
guided installer from this folder.

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
