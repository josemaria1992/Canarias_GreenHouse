# Desktop installation (Windows, macOS, Linux)

Tested: Python 3.11 to 3.14 on Linux x86_64 (full suite locally), and on every pull request by GitHub
CI on Ubuntu, Windows and macOS (3.11 to 3.14) and Ubuntu ARM64. The release ZIP is built and its
Windows launcher is run offline on a hosted Windows runner with every Python folder removed from PATH
and `py` hidden (CI job `release-windows`: a cold start from a folder whose name has a space, brackets
and an accent, the first administrator, sign-in, a clean stop, a restart that keeps the data, and an
upgrade over the 0.3.1 and 0.3.2 demos). No test has run on the owner's own PC. The owner's
step-by-step start is `README-FIRST.md` at the top of the release folder.

## The quick way: the release ZIP (Windows demo)

1. Unpack `aquacrop-<version>.zip` to a folder outside OneDrive and double-click `AquaCrop-Demo.cmd`.
   Nothing needs installing first: the folder `runtime` holds CPython 3.14.8 for 64-bit Windows
   (python.org's build, from the Python Software Foundation's NuGet package, SHA-256 pinned in
   `scripts/build_release.py`) with AquaCrop and the locked packages already installed. A Python on the
   PC is never used by the release; nothing is downloaded.
2. The launcher checks the release (`RELEASE.json` against the installed program), the packages, the
   pages, the configuration, the data folder and the port, and on the first start asks for the first
   administrator. The rules and an example are shown first; an answer that breaks them is explained
   and asked again. There is no default password.
3. The browser opens at <http://127.0.0.1:8000/> with the full installation simulated
   (`installation-80`: six modules, 80 points). The simulation is served on this PC only.
4. Stop with Ctrl+C or by closing the window. History, accounts and logs stay in
   `%LOCALAPPDATA%\AquaCrop\demo` and are there again at the next start.

Options are passed through, for example `AquaCrop-Demo.cmd --check-only` (the prerequisite checks
only) or `AquaCrop-Demo.cmd --port 8010`. On macOS and Linux run `./aquacrop-demo.sh` from the same
folder; the state lives in `~/Library/Application Support/AquaCrop/demo` or
`~/.local/share/aquacrop/demo`. From any installed copy the same launcher is
`python -m aquacrop launch` (`--config installation-48`, `demo-48` or `demo-24` start the legacy test
fixtures). It only starts SIMULATED
configurations; a controller is installed with `installation/cm4.md`.

## From the source tree (online)

Windows (no PowerShell activation or execution-policy change needed):

```text
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps .
.venv\Scripts\python.exe -m aquacrop demo verify --config installation-80 --seed 42
.venv\Scripts\python.exe -m aquacrop users bootstrap --config installation-80 --username admin --generate
.venv\Scripts\python.exe -m aquacrop serve --config installation-80 --host 127.0.0.1 --port 8000
```

macOS / Linux:

```text
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m aquacrop demo verify --config installation-80 --seed 42
.venv/bin/python -m aquacrop users bootstrap --config installation-80 --username admin --generate
.venv/bin/python -m aquacrop serve --config installation-80 --host 127.0.0.1 --port 8000
```

`--generate` prints a one-time password that must be changed at first sign-in. To choose your
own, use `--password-stdin` instead (at least 12 characters). Open <http://127.0.0.1:8000/>.

Developers: install `requirements-dev.lock` instead, then `pip install --no-deps -e .` and run
`python -m pytest -q`.

## Offline installation

Offline *runtime* works out of the box: the UI and charting code are packaged; nothing is fetched
from a CDN. Offline *first installation* needs a wheelhouse built on a connected machine for the
exact target platform and Python version:

```text
scripts/build_wheelhouse.sh 3.11 x86_64 wheelhouse-linux-py311        # or aarch64, win_amd64, macosx_11_0_arm64
# copy the folder to the target, then:
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse-linux-py311 "aquacrop-greenhouse[hardware]"
```

Each wheelhouse has `MANIFEST.sha256` and `TARGET.txt`; wheels are platform specific.

## Where state lives

`--state-dir` (default `var/` in the current directory) holds the database, the session secret
(`secret.key`, mode 0600), owner locks and backups. Keep it private. A simulation and a hardware
installation must not share a database; the server refuses to start on a database written by a
different profile.

## v0.1 users

`python -m greenhouse` still works and starts the 24-point simulation. It refuses
`HYDRO_BACKEND=modbus` and `HYDRO_BENCH_WRITES`, and ignores `HYDRO_PASSWORD` (use
`users bootstrap`). If it finds a v0.1 database it prints the import command:

```text
python -m aquacrop migrate-v01 --config demo-24 --legacy-db greenhouse.sqlite3
```

The import takes a backup first, labels every legacy sample `LEGACY_UNKNOWN`, and records the old
thresholds without applying them.
