#!/bin/bash
#SBATCH --job-name=instruction-loop
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=32G
#SBATCH --time=03:00:00
# Submit from the repository root after sourcing the workspace env.sh; all
# arguments are passed to patch.py instruction-loop. Helma's cpu partition needs
# full 48-core NUMA domains. Claude uses the subscription OAuth token in .env.
set -euo pipefail
: "${OT_WORKSPACE:?Source env.sh before submitting}"
: "${TMPDIR:?Helma must provide node-local TMPDIR}"
repo=$SLURM_SUBMIT_DIR
instruction_workspace=$OT_WORKSPACE
instruction_bundle=${OT_RUNTIME_BUNDLE:-$OT_WORKSPACE/runtime/prep.json}
instruction_secrets=${OT_SECRETS_FILE:-$HOME/.env}
if [[ -r "$instruction_secrets" ]]; then
    set -a
    source "$instruction_secrets" >/dev/null 2>&1
    set +a
fi
export OT_WORKSPACE="$instruction_workspace" OT_RUNTIME_BUNDLE="$instruction_bundle"
# This launcher deliberately uses subscription authentication only.
: "${CLAUDE_CODE_OAUTH_TOKEN:?Claude subscription OAuth token is required}"
export CLAUDE_CODE_OAUTH_TOKEN
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY AWS_BEARER_TOKEN_BEDROCK
eval "$(/usr/bin/python3 "$repo/config/runtime.py" shell --cluster helma)"
if [[ -n "$OT_PYTHON_MODULE" ]]; then module load "$OT_PYTHON_MODULE"; fi
if [[ -n "$OT_APPTAINER_MODULE" ]]; then module load "$OT_APPTAINER_MODULE"; fi
if [[ -n "$OT_CUDA_MODULE" ]]; then module load "$OT_CUDA_MODULE"; fi
instruction_runtime="$TMPDIR/instruction-runtime"
/usr/bin/python3 "$repo/hpc/local_runtime.py" stage "$instruction_bundle" "$repo" "$instruction_runtime"
source "$instruction_runtime/env.sh"
mkdir -p "$VALIDATION_REPO/data/tasktrove_bugsinpy"
cp "$repo/data/tasktrove_bugsinpy/patch.py" "$VALIDATION_REPO/data/tasktrove_bugsinpy/patch.py"
source "$VALIDATION_REPO/hpc/helma/proxy.sh"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
cd "$VALIDATION_REPO"
exec "$HARBOR_BRIDGE_VENV/bin/python" -u "$VALIDATION_REPO/data/tasktrove_bugsinpy/patch.py" instruction-loop \
    --work-dir "$TMPDIR/instruction-loop" "$@"
