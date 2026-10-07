#!/bin/bash
# Cross-container isolation check, submitted by validation/checks/dataset_checks.py.
#SBATCH --job-name=task-isolation
#SBATCH --partition=cpu
#SBATCH --cpus-per-task=48
#SBATCH --mem=64G
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${OT_WORKSPACE:?Workspace}"
: "${TMPDIR:?Node-local scratch required}"
here=${OT_NEXT_DATA:?Set OT_NEXT_DATA to the OT_next_data checkout (source env.sh)}
tasks=${1:?directory containing tasks/}
base=$OT_WORKSPACE
out=$base/runs/checks-$SLURM_JOB_ID
mkdir -p "$out" "$TMPDIR/apptainer" "$TMPDIR/bridge"

/usr/bin/python3 "$here/hpc/local_runtime.py" stage \
    "${OT_RUNTIME_BUNDLE:-$base/runtime/prep.json}" "$here" "$TMPDIR/check-runtime"
source "$TMPDIR/check-runtime/env.sh"
here=$OT_NEXT_DATA
# Keep task and image reads local for the lifetime of the bridge.
python - "$tasks" "$TMPDIR/check-tasks" "$base/images" "$TMPDIR/check-images" <<'PY'
import shutil, sys
from pathlib import Path
from hpc.local_assets import stage_images
source, local, images, staged = map(Path, sys.argv[1:])
task = sorted((source / 'tasks').iterdir())[0]
shutil.copytree(task, local / 'tasks' / task.name)
stage_images([local / 'tasks' / task.name], images, staged)
PY
tasks="$TMPDIR/check-tasks"
export HARBOR_SIF_CACHE="$TMPDIR/check-images"
python "$here/hpc/local_assets.py" certificates "$TMPDIR/check-certificates" >/dev/null
export SSL_CERT_FILE="$TMPDIR/check-certificates/ssl_cert_file.pem"
export REQUESTS_CA_BUNDLE="$TMPDIR/check-certificates/requests_ca_bundle.pem"
export APPTAINER_TMPDIR="$TMPDIR/apptainer"
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export BRIDGE_USE_FAKEROOT=1 BRIDGE_INSTANCE_REUSE=0 BRIDGE_WORKERS_DEAD_TIMEOUT=60
export OT_TRIAL_CPUS=${OT_TRIAL_CPUS:-1} OT_TRIAL_MEM=${OT_TRIAL_MEM:-4G}
bridge_port=$((40000 + SLURM_JOB_ID % 10000))
export APPTAINER_BRIDGE_URL="http://127.0.0.1:$bridge_port"

pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT
cd "$TMPDIR"

python -u "$here/harbor_patches/bridge_server.py" --host 127.0.0.1 --port "$bridge_port" 2>&1 &
pids+=("$!")
# The worker stays in the batch step: it starts one Slurm step per trial, and a
# step inside a step loses its tmux anchor (872598).
python -u "$here/harbor_patches/bridge_worker.py" --bridge-url "$APPTAINER_BRIDGE_URL" \
    --sif-cache "$HARBOR_SIF_CACHE" --staging-base "$TMPDIR/bridge" --num-workers 4 2>&1 &
pids+=("$!")

ready=false
for ((i = 0; i < 60; i++)); do
    for pid in "${pids[@]}"; do kill -0 "$pid"; done
    if curl -fsS "$APPTAINER_BRIDGE_URL/status" 2>/dev/null | python -c 'import json,sys; sys.exit(not json.load(sys.stdin)["workers_alive"])' 2>/dev/null; then ready=true; break; fi
    sleep 2
done
"$ready" || { echo 'Bridge readiness failed; see the batch log above' >&2; exit 1; }
echo "bridge ready on $APPTAINER_BRIDGE_URL" | tee "$out/phases.log"

rc=0
echo "== check_isolation.py" | tee -a "$out/phases.log"
python "$here/validation/checks/check_isolation.py" "$(ls -d "$tasks"/tasks/* | head -1)" "$out/isolation" \
    2>&1 | tee "$out/check_isolation.log" || rc=1
echo "exit $rc" | tee -a "$out/phases.log"
exit "$rc"
