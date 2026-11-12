#!/bin/bash
# Stage-4 CPU workers or finalization on ZIH; resources come from stage4.submit.
set -euo pipefail
export OT_CLUSTER=zih
: "${OT_WORKSPACE:?Set OT_WORKSPACE}"
: "${OT_RUNTIME_BUNDLE:?Set the verified host runtime bundle}"
repo=$SLURM_SUBMIT_DIR
if [[ -n "${OT_SECRETS_FILE:-}" ]]; then
    set -a
    source "$OT_SECRETS_FILE" >/dev/null 2>&1
    set +a
fi
if [[ "${OT_LEAK_REPAIR_LOCAL:-0}" != 1 ]]; then
    : "${CLAUDE_CODE_OAUTH_TOKEN:?Claude subscription OAuth token is required}"
fi
export TMPDIR=$(mktemp -d "/tmp/ot-stage4-${SLURM_JOB_ID}-XXXXXX")
eval "$(/usr/bin/python3 "$repo/config/runtime.py" shell --cluster zih)"
if [[ -n "$OT_PYTHON_MODULE" ]]; then module load "$OT_PYTHON_MODULE"; fi
if [[ -n "$OT_APPTAINER_MODULE" ]]; then module load "$OT_APPTAINER_MODULE"; fi
if [[ -n "$OT_CUDA_MODULE" && -n "${SLURM_JOB_GPUS:-${SLURM_GPUS_ON_NODE:-}}" ]]; then module load "$OT_CUDA_MODULE"; fi
/usr/bin/python3 "$repo/hpc/local_runtime.py" stage "$OT_RUNTIME_BUNDLE" "$repo" "$TMPDIR/runtime"
source "$TMPDIR/runtime/env.sh"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
cd "$VALIDATION_REPO"
exec "$HARBOR_BRIDGE_VENV/bin/python" -u "$VALIDATION_REPO/hpc/leak_repair_job.py" "$@"
