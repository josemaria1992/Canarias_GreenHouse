# Desktop installation (Windows, macOS, Linux)

Tested: Python 3.11.15, 3.12.13 and 3.13.16 on Linux x86_64 (full test suite and `pip check`
green on each, 2026-10-11). The CI workflow (`.github/workflows/ci.yml`) adds Windows, macOS and
ARM64 Linux, but **it has not run yet** (no GitHub repository is connected). Treat Windows and macOS
as untested until it does.

## From the source tree (online)

Windows (no PowerShell activation or execution-policy change needed):

```text
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps .
.venv\Scripts\python.exe -m aquacrop demo verify --config demo-48 --seed 42
.venv\Scripts\python.exe -m aquacrop users bootstrap --config demo-48 --username admin --generate
.venv\Scripts\python.exe -m aquacrop serve --config demo-48 --host 127.0.0.1 --port 8000
```

macOS / Linux:

```text
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m aquacrop demo verify --config demo-48 --seed 42
.venv/bin/python -m aquacrop users bootstrap --config demo-48 --username admin --generate
.venv/bin/python -m aquacrop serve --config demo-48 --host 127.0.0.1 --port 8000
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
