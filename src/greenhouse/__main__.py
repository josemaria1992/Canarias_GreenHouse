"""v0.1 compatibility launcher.

Maps the old `python -m greenhouse [--host H] [--port P]` + HYDRO_* environment to the new CLI:

* HYDRO_BACKEND=simulation (default) -> `aquacrop serve --config demo-24` (the 24-point fixture that
  matches v0.1's single AI + single relay module), state in the directory of HYDRO_DB.
* HYDRO_PASSWORD is ignored: shared credentials are gone. Create individual accounts with
  `python -m aquacrop users bootstrap`.
* A v0.1 database at HYDRO_DB is never opened by the new server. Import it explicitly with
  `python -m aquacrop migrate-v01` (backup-first, LEGACY_UNKNOWN provenance).
* HYDRO_BACKEND=modbus and HYDRO_BENCH_WRITES are refused: hardware profiles need a validated
  YAML inventory, start read-only, and bench writes need an expiring server-side lease. Physical
  permission is never inherited from shell defaults.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def _is_v01_db(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        return "samples" in names and "settings" in names and "schema_meta" not in names
    except sqlite3.Error:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AquaCrop v0.1 compatibility launcher (deprecated)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=8000, type=int)
    a = ap.parse_args(argv)
    print("NOTE: `python -m greenhouse` is a compatibility launcher; use `python -m aquacrop serve`.",
          file=sys.stderr)
    backend = os.environ.get("HYDRO_BACKEND", "simulation").strip().lower()
    if os.environ.get("HYDRO_BENCH_WRITES", "0") != "0":
        print("REFUSED: HYDRO_BENCH_WRITES is no longer honoured. Bench writes require an authenticated, "
              "expiring server-side lease (see docs/commissioning).", file=sys.stderr)
        return 2
    if backend != "simulation":
        print("REFUSED: HYDRO_BACKEND=modbus is replaced by a validated YAML inventory "
              "(configs/hardware-read-only.example.yaml) and `python -m aquacrop diagnostics read`.",
              file=sys.stderr)
        return 2
    if os.environ.get("HYDRO_PASSWORD"):
        print("NOTE: HYDRO_PASSWORD ignored; use individual accounts (`python -m aquacrop users bootstrap`).",
              file=sys.stderr)
    legacy_db = Path(os.environ.get("HYDRO_DB", "./data/greenhouse.sqlite3"))
    state_dir = legacy_db.parent / "aquacrop"
    if _is_v01_db(legacy_db):
        print(f"NOTE: v0.1 history found at {legacy_db}. It is untouched. To import it (backup-first):\n"
              f"  python -m aquacrop migrate-v01 --legacy-db {legacy_db} --config demo-24 --state-dir {state_dir}",
              file=sys.stderr)
    from aquacrop.cli import main as aq
    return aq(["serve", "--config", "demo-24", "--state-dir", str(state_dir), "--host", a.host,
               "--port", str(a.port)])


if __name__ == "__main__":
    raise SystemExit(main())
