#!/bin/bash
# The checks that need a real sandbox: verify/check_reward_harbor.py (does the
# image build and does the task's own test.sh score correctly inside it?) and
# verify/check_isolation.py (can two trial containers see each other?).
#
# They need a bridge server and a bridge worker, which only exist inside a job,
# so this is the same environment teacher_traces.sbatch sets up - without the model.
# Helma's GPU partition refuses a job with no GPU, so one is requested and left
# idle; a cluster with a CPU partition should use it instead.
#
#   sbatch --gres=gpu:h200:1 --time=00:30:00 hpc/helma/checks.sbatch <dir with tasks/> [limit]
#SBATCH --job-name=cceval-checks
#SBATCH --partition=h200
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${PILOT_ROOT:?Workspace}"
: "${TMPDIR:?Node-local scratch required}"
here=${OT_NEXT_DATA:?Set OT_NEXT_DATA to the OT_next_data checkout (source env.sh)}
tasks=${1:?directory containing tasks/}
limit=${2:-3}
base=$PILOT_ROOT
out=$base/runs/checks-$SLURM_JOB_ID
mkdir -p "$out" "$TMPDIR/apptainer" "$TMPDIR/bridge"

export HARBOR_BRIDGE_VENV="$base/envs/prep"
export PATH="$HARBOR_BRIDGE_VENV/bin:$PATH"
export PYTHONPATH="$here"
export HARBOR_SIF_CACHE="$base/images" APPTAINER_CACHEDIR="$base/cache/apptainer"
export APPTAINER_TMPDIR="$TMPDIR/apptainer"
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export BRIDGE_USE_FAKEROOT=1 BRIDGE_INSTANCE_REUSE=0 BRIDGE_WORKERS_DEAD_TIMEOUT=60
export PILOT_TRIAL_CPUS=${PILOT_TRIAL_CPUS:-1} PILOT_TRIAL_MEM=${PILOT_TRIAL_MEM:-4G}
bridge_port=$((40000 + SLURM_JOB_ID % 10000))
export APPTAINER_BRIDGE_URL="http://127.0.0.1:$bridge_port"

pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT
cd "$TMPDIR"

python -u -m harbor.environments.apptainer.server --host 127.0.0.1 --port "$bridge_port" > "$out/bridge.log" 2>&1 &
pids+=("$!")
# The worker stays in the batch step: it starts one Slurm step per trial, and a
# step inside a step loses its tmux anchor (872598).
python -u "$here/harbor_patches/bridge_worker.py" --bridge-url "$APPTAINER_BRIDGE_URL" \
    --sif-cache "$HARBOR_SIF_CACHE" --staging-base "$TMPDIR/bridge" --num-workers 4 > "$out/worker.log" 2>&1 &
pids+=("$!")

ready=false
for ((i = 0; i < 60; i++)); do
    for pid in "${pids[@]}"; do kill -0 "$pid"; done
    if curl -fsS "$APPTAINER_BRIDGE_URL/status" 2>/dev/null | python -c 'import json,sys; sys.exit(not json.load(sys.stdin)["workers_alive"])' 2>/dev/null; then ready=true; break; fi
    sleep 2
done
"$ready" || { echo 'Bridge readiness failed; see bridge.log/worker.log' >&2; exit 1; }
echo "bridge ready on $APPTAINER_BRIDGE_URL" | tee "$out/phases.log"

rc=0
echo "== check_reward_harbor.py (first $limit tasks)" | tee -a "$out/phases.log"
python "$here/verify/check_reward_harbor.py" "$tasks" "$out/sandbox" --limit "$limit" --extras \
    2>&1 | tee "$out/check_reward_harbor.log" || rc=1
echo "== check_isolation.py" | tee -a "$out/phases.log"
python "$here/verify/check_isolation.py" "$(ls -d "$tasks"/tasks/* | head -1)" "$out/isolation" \
    2>&1 | tee "$out/check_isolation.log" || rc=1
echo "exit $rc" | tee -a "$out/phases.log"
exit "$rc"
