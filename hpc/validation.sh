#!/bin/bash
set -euo pipefail
# Keep the submitted runtime workspace when loading credentials/config below.
validation_submitted_workspace=${OT_WORKSPACE:?Source env.sh before submitting}
validation_submitted_cluster=${OT_CLUSTER:-helma}
validation_submitted_bundle=${OT_RUNTIME_BUNDLE:-$OT_WORKSPACE/runtime/prep.json}
# Load credentials on the compute node, including assignments without "export".
# Keep this file outside the repository and never include its contents in logs.
validation_secrets_files=("$HOME/.env" "$PWD/.env")
if [[ -n "${OT_SECRETS_FILE:-}" ]]; then
    validation_secrets_files=("$OT_SECRETS_FILE")
    if [[ ! -r "$OT_SECRETS_FILE" ]]; then
        echo "OT_SECRETS_FILE is not readable" >&2
        exit 1
    fi
fi
validation_existing_exports=$(export -p)
for validation_secrets_file in "${validation_secrets_files[@]}"; do
    if [[ -r "$validation_secrets_file" ]]; then
        set -a
        source "$validation_secrets_file" >/dev/null 2>&1
        set +a
    fi
done
eval "$validation_existing_exports"
unset validation_existing_exports
export OT_WORKSPACE="$validation_submitted_workspace"
export OT_CLUSTER="$validation_submitted_cluster"
export OT_RUNTIME_BUNDLE="$validation_submitted_bundle"
: "${TMPDIR:?Set TMPDIR to node-local scratch for this allocation}"
# Slurm copies this script into its spool; the submitter exports the repository
# path explicitly, so locating the worker does not depend on the spool path.
export VALIDATION_SOURCE_REPO="$VALIDATION_REPO"
eval "$(/usr/bin/python3 "$VALIDATION_REPO/config/runtime.py" shell --cluster "${OT_CLUSTER:-helma}")"
if [[ -n "$OT_PYTHON_MODULE" ]]; then module load "$OT_PYTHON_MODULE"; fi
if [[ -n "$OT_APPTAINER_MODULE" ]]; then module load "$OT_APPTAINER_MODULE"; fi
# The CUDA module exists only where GPUs do (e.g. not on ZIH Barnard); CPU jobs do not need it.
if [[ -n "$OT_CUDA_MODULE" && -n "${SLURM_JOB_GPUS:-${SLURM_GPUS_ON_NODE:-}}" ]]; then module load "$OT_CUDA_MODULE"; fi
validation_local_root="$TMPDIR/validation-runtime"
/usr/bin/python3 "$VALIDATION_REPO/hpc/local_runtime.py" stage \
    "${OT_RUNTIME_BUNDLE:-$OT_WORKSPACE/runtime/prep.json}" "$VALIDATION_REPO" "$validation_local_root"
source "$validation_local_root/env.sh"
"$HARBOR_BRIDGE_VENV/bin/python" "$VALIDATION_REPO/config/runtime.py" check
if [[ "$OT_CLUSTER" == helma ]]; then source "$VALIDATION_REPO/hpc/helma/proxy.sh"; fi
cd "$VALIDATION_REPO"
exec "$HARBOR_BRIDGE_VENV/bin/python" -u "$VALIDATION_REPO/hpc/validation_worker.py" "$1"
