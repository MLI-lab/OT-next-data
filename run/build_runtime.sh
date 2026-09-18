#!/bin/bash
set -euo pipefail
: "${PILOT_ROOT:?}"
: "${TMPDIR:?Private frontend build directory}"
mkdir -p "$PILOT_ROOT/images" "$TMPDIR/apptainer-cache" "$TMPDIR/apptainer-build"
export APPTAINER_CACHEDIR="$TMPDIR/apptainer-cache"
export APPTAINER_TMPDIR="$TMPDIR/apptainer-build"
apptainer build --fakeroot --mksquashfs-args '-processors 2' "$PILOT_ROOT/images/runtime.sif.tmp" "$PILOT_ROOT/scripts/runtime.def"
mv "$PILOT_ROOT/images/runtime.sif.tmp" "$PILOT_ROOT/images/runtime.sif"
sha256sum "$PILOT_ROOT/images/runtime.sif" > "$PILOT_ROOT/images/runtime.sif.sha256"
touch "$PILOT_ROOT/envs/runtime-ready"
