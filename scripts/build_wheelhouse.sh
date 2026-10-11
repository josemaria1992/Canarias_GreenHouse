#!/bin/sh
# Build an offline wheelhouse for one target platform from the hash-locked requirements.
# Usage: scripts/build_wheelhouse.sh <python-version> <arch-or-platform> <out-dir>
#   e.g. scripts/build_wheelhouse.sh 3.11 aarch64 wheelhouse-cm4-py311     (Raspberry Pi OS Bookworm 64-bit)
#        scripts/build_wheelhouse.sh 3.13 win_amd64 wheelhouse-win-py313
# For linux arches the glibc tags manylinux_2_17 .. manylinux_2_28 are accepted (Bookworm has glibc 2.36).
# Wheels differ per platform, so each wheelhouse gets its own manifest (MANIFEST.sha256).
set -eu
PYV="$1"; PLAT="$2"; OUT="$3"
case "$PLAT" in
  aarch64|x86_64) PLATS="--platform manylinux_2_17_$PLAT --platform manylinux2014_$PLAT --platform manylinux_2_28_$PLAT" ;;
  *) PLATS="--platform $PLAT" ;;
esac
# shellcheck disable=SC2086
python3 -m pip download --require-hashes -r requirements.lock --only-binary=:all: \
  --python-version "$PYV" $PLATS --platform any -d "$OUT"
python3 -m build --wheel -o "$OUT" .
( cd "$OUT" && sha256sum *.whl > MANIFEST.sha256 && echo "python $PYV platform $PLAT" > TARGET.txt )
echo "wheelhouse: $OUT"
echo "install offline: python3 -m venv venv && venv/bin/python -m pip install --no-index --find-links $OUT aquacrop-greenhouse[hardware]"
