#!/bin/bash
# Build a serving runtime image for one vLLM version.
#
#   PILOT_ROOT=... TMPDIR=... hpc/helma/build_runtime.sh [vllm tag]
#
# Produces $PILOT_ROOT/images/runtime-<tag>.sif. The tag defaults to
# config/models.py's VLLM_VERSION, which is the one version everything runs on.
set -euo pipefail
: "${PILOT_ROOT:?}"
: "${TMPDIR:?Private frontend build directory}"
here_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tag=${1:-$(python3 -c "import sys; sys.path.insert(0, '$here_repo/config'); import models; print(models.VLLM_VERSION)")}
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
