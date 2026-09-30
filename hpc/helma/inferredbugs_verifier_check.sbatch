#!/bin/bash
#SBATCH --job-name=ib-verifier-check
#SBATCH --partition=preempt
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --gres=gpu:1
#SBATCH --time=06:00:00
# Runs packaged tasks' tests/test.sh with the buggy and the historical fixed file (the cpu
# partition refuses jobs below 48 cores; this one follows the recovery jobs onto preempt).
# CHECK_RUN: output directory holding ids.txt.
set -euo pipefail
repo=/home/hpc/y500bb/y500bb12/OT_next_data
source "$repo/hpc/helma/proxy.sh"
base="$repo/runs/inferredbugs-20260925/full-warning-audit"
run=${CHECK_RUN:?}
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
export INFERREDBUGS_CENTRAL_MIRROR=https://maven-central.storage-download.googleapis.com/maven2
scratch="${TMPDIR:?}/ib-verifier-check"
mkdir -p "$scratch"
# The buggy and fixed files: Helma's inputs.sqlite, or a checkout of microsoft/InferredBugs (warmup.py --source)
if [ -n "${CHECK_SOURCE:-}" ]; then inputs=(--inferredbugs-root "$CHECK_SOURCE"); else
  cp "$repo/runs/inferredbugs-20260927/final-inputs2/inputs.sqlite" "$scratch/inputs.sqlite"; inputs=(--inputs "$scratch/inputs.sqlite"); fi
# As an array job (a full run): one shard per array task, one summary line per verification.
extra=()
if [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then
  extra=(--shards "${CHECK_SHARDS:?}" --shard "$SLURM_ARRAY_TASK_ID" --summary-file "$run/summaries-$SLURM_ARRAY_TASK_ID.jsonl")
fi
# CHECK_LOAD=N: N busy processes beside the check, to see what a loaded node does to the analysis
for i in $(seq 1 "${CHECK_LOAD:-0}"); do ( while :; do :; done ) & done
"$base/venv/bin/python" -u "$repo/hpc/helma/inferredbugs_verifier_check.py" \
  "${inputs[@]}" --images "${CHECK_IMAGES:-$base/images}" --scratch "$scratch/tasks" --output "$run" \
  --ids-file "$run/ids.txt" --proxy-helper "$repo/hpc/helma/configure_maven_proxy.py" \
  --workers "${CHECK_WORKERS:-8}" --variants "${CHECK_VARIANTS:-buggy,gold}" --step-timeout "${CHECK_STEP_TIMEOUT:-2400}" --timeout "${CHECK_TIMEOUT:-7200}" "${extra[@]}"
kill $(jobs -p) 2>/dev/null || true
