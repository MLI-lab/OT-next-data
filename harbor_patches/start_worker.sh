#!/bin/bash
# Start a bridge worker on whichever node this runs on.
#
# Slurm creates the job's $TMPDIR only on the node that runs the batch script, so
# a worker started on another node has nowhere to stage its containers (873461:
# "couldn't chdir to /tmp/<job>.helma"). It therefore makes its own staging
# directory, on node-local /tmp, named after the job so cleanup is unambiguous.
#
#   start_worker.sh <repo> <bridge url> <sif cache> <workers>
set -euo pipefail
repo=$1; bridge_url=$2; sif_cache=$3; workers=$4
staging=${TMPDIR:-/tmp}/bridge-$SLURM_JOB_ID
mkdir -p "$staging"
echo "worker on $(hostname -s): staging $staging, bridge $bridge_url"
# The anchor log explains a container that dies at once; node-local staging is
# wiped with the job, so copy it out when this worker stops.
if [[ -n ${PILOT_RUN_DIR:-} ]]; then
    trap 'mkdir -p "$PILOT_RUN_DIR/anchor-logs"; cp $staging/*/tmux-anchor.log "$PILOT_RUN_DIR/anchor-logs/$(hostname -s).log" 2>/dev/null || true' EXIT
fi
exec python -u "$repo/harbor_patches/bridge_worker.py" --bridge-url "$bridge_url" \
    --sif-cache "$sif_cache" --staging-base "$staging" --num-workers "$workers"
