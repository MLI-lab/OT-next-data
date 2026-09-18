#!/bin/bash
# Build a serving runtime image for one vLLM version.
#
#   PILOT_ROOT=... TMPDIR=... hpc/helma/build_runtime.sh [vllm tag]
#
# Produces $PILOT_ROOT/images/runtime-<tag>.sif and points images/runtime.sif at
# the default one. A model that needs a newer vLLM names its image in
# config/models.py, so several versions can live side by side.
set -euo pipefail
: "${PILOT_ROOT:?}"
: "${TMPDIR:?Private frontend build directory}"
tag=${1:-v0.20.0}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$PILOT_ROOT/images" "$TMPDIR/apptainer-cache" "$TMPDIR/apptainer-build"
export APPTAINER_CACHEDIR="$TMPDIR/apptainer-cache"
export APPTAINER_TMPDIR="$TMPDIR/apptainer-build"
out=$PILOT_ROOT/images/runtime-$tag.sif
apptainer build --fakeroot --mksquashfs-args '-processors 2' \
    --build-arg VLLM_TAG="$tag" --build-arg REQUIREMENTS="$PILOT_ROOT/scripts/requirements.txt" \
    "$out.tmp" "$here/runtime.def"
mv "$out.tmp" "$out"
sha256sum "$out" > "$out.sha256"
echo "built $out"
