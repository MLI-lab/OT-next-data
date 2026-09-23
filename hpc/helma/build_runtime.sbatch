#!/bin/bash
# Build a serving runtime image on a compute node.
#
# Unpacking a container's layers creates tens of thousands of files, which the
# shared filesystem's file-count quota refuses (the v0.29.0 build died on it).
# Node-local scratch has no such limit, and only the finished image - a single
# file - is copied back.
#
#   sbatch --time=02:00:00 --cpus-per-task=48 hpc/helma/build_runtime.sbatch v0.29.0
# (Helma's cpu partition allocates cores in multiples of 48.)
#SBATCH --job-name=build-runtime
#SBATCH --partition=cpu
#SBATCH --nodes=1
set -euo pipefail
: "${PILOT_ROOT:?}"
here=${OT_NEXT_DATA:?Set OT_NEXT_DATA (source env.sh)}
tag=${1:?vLLM tag, e.g. v0.29.0}
export TMPDIR=${TMPDIR:?Node-local scratch required}
export APPTAINER_CACHEDIR="$TMPDIR/apptainer-cache" APPTAINER_TMPDIR="$TMPDIR/apptainer-build"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
out=$TMPDIR/runtime-$tag.sif
apptainer build --fakeroot --mksquashfs-args '-processors 4' \
    --build-arg VLLM_TAG="$tag" --build-arg REQUIREMENTS="$PILOT_ROOT/scripts/requirements.txt" \
    "$out" "$here/hpc/helma/runtime.def"
cp "$out" "$PILOT_ROOT/images/runtime-$tag.sif.tmp"
mv "$PILOT_ROOT/images/runtime-$tag.sif.tmp" "$PILOT_ROOT/images/runtime-$tag.sif"
sha256sum "$PILOT_ROOT/images/runtime-$tag.sif" > "$PILOT_ROOT/images/runtime-$tag.sif.sha256"
echo "built $PILOT_ROOT/images/runtime-$tag.sif"
