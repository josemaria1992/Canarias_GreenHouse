#!/bin/sh
# AquaCrop desktop demo (macOS, Linux). Run it from a terminal, with options for `aquacrop launch`:
#   ./aquacrop-demo.sh            ./aquacrop-demo.sh --check-only            ./aquacrop-demo.sh --port 8010
# First run: prepares a private Python environment under ~/.local/share/aquacrop (macOS: ~/Library/
# Application Support/AquaCrop), offline when the release carries a matching wheelhouse-* folder, then asks
# for the first administrator password. Served on this computer only (127.0.0.1). Stop with Ctrl+C.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
case "$(uname -s)" in
  Darwin) LOCAL="$HOME/Library/Application Support/AquaCrop" ;;
  *) LOCAL="${XDG_DATA_HOME:-$HOME/.local/share}/aquacrop" ;;
esac
fail() { echo "$1" >&2; echo "AquaCrop did not start." >&2; exit 1; }

PY=""
for v in python3.12 python3.13 python3.11 python3; do
  if command -v "$v" >/dev/null 2>&1 && "$v" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13)) else 1)' 2>/dev/null; then
    PY=$v; break
  fi
done
[ -n "$PY" ] || fail "AquaCrop needs Python 3.11, 3.12 or 3.13 with venv (Debian/Ubuntu: apt install python3-venv)."

WHEEL=$(ls "$HERE"/wheels/aquacrop_greenhouse-*.whl 2>/dev/null | head -n 1 || true)
EDITABLE=""
if [ -n "$WHEEL" ]; then
  TAG=$(basename "$WHEEL" .whl)
elif [ -f "$HERE/../pyproject.toml" ]; then
  WHEEL=$(cd "$HERE/.." && pwd); TAG=source; EDITABLE="-e"
else
  fail "Cannot find the AquaCrop wheel in $HERE/wheels or a source checkout. Unpack the whole release ZIP."
fi
LOCK="$HERE/requirements.lock"; [ -f "$LOCK" ] || LOCK="$HERE/../requirements.lock"
[ -f "$LOCK" ] || fail "requirements.lock is missing next to this script. Unpack the whole release ZIP."
# The environment is keyed by the wheel's SHA-256 (12 hex digits in the folder name, all 64 in a marker
# written only after a complete install), so a new build never reuses an older environment.
WSHA=""
if [ -z "$EDITABLE" ]; then
  WSHA=$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$WHEEL") \
    || fail "Cannot read the AquaCrop wheel $WHEEL. Unpack the whole release again."
  VENV="$LOCAL/venv-$TAG-$(printf %s "$WSHA" | cut -c1-12)"
else
  VENV="$LOCAL/venv-$TAG"
fi
MARKER="$VENV/aquacrop-wheel.sha256"
if [ -n "$WSHA" ] && [ -x "$VENV/bin/python" ] && [ "$(cat "$MARKER" 2>/dev/null)" != "$WSHA" ]; then
  echo "The environment $VENV is incomplete; preparing it again."   # no marker: the install never finished
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  echo "Preparing AquaCrop in $VENV (first run only) ..."
  mkdir -p "$LOCAL"
  "$PY" -m venv "$VENV" || fail "could not create the environment"
  # release wheelhouses are named wheelhouse-<platform>-py<XY>, e.g. wheelhouse-aarch64-py311
  MACH=$("$VENV/bin/python" -c 'import platform; print(platform.machine().lower())')
  PYV=$("$VENV/bin/python" -c 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}")')
  WH=$(ls -d "$HERE"/wheelhouse-*"$MACH"*-"$PYV" 2>/dev/null | head -n 1 || true)
  if [ -n "$WH" ]; then echo "Installing from $WH (no internet needed)."; else echo "Installing the locked packages from the internet."; fi
  if ! "$VENV/bin/python" -m pip install --disable-pip-version-check -q ${WH:+--no-index --find-links "$WH"} --require-hashes -r "$LOCK" \
     || ! "$VENV/bin/python" -m pip install --disable-pip-version-check -q --no-deps $EDITABLE "$WHEEL"; then
    rm -rf "$VENV"; fail "Installation failed; the incomplete environment was removed."
  fi
  [ -z "$WSHA" ] || printf '%s\n' "$WSHA" > "$MARKER"
fi
export AQUACROP_WHEEL_SHA256="$WSHA"
# a release wheel is always checked against RELEASE.json (missing counts as a failed check); a source checkout is not a release
if [ -n "$WSHA" ]; then export AQUACROP_RELEASE_INFO="$HERE/RELEASE.json"; else export AQUACROP_RELEASE_INFO=""; fi
exec "$VENV/bin/python" -m aquacrop launch "$@"
