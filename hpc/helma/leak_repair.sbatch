#!/bin/bash
#SBATCH --job-name=leak-repair
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=48
#SBATCH --time=08:00:00
#SBATCH --signal=B:USR1@300
# Leak-repair sessions (validation/leak_repair) in one allocation: see hpc/leak_repair_job.py.
# Submit from the repository root after sourcing env.sh, with OT_RUNTIME_BUNDLE set to a bundle of the current prep
# environment and OT_SECRETS_FILE holding the Claude subscription OAuth token. Arguments go to hpc/leak_repair_job.py:
#   --images CACHE [--concurrency N] -- <launch.py repair arguments>
# Helma's cpu partition needs full 48-core NUMA domains; job /tmp is memory-backed (the runtime alone is ~30 GB),
# so give CPU jobs --mem=128G (stage 4 does; GPU jobs get memory per GPU). A local repair model (stage 4 with
# OT_LEAK_REPAIR_LOCAL=1) needs no Claude token. With --leak-repair-api-base the server runs in a separate GPU
# allocation; this job stays on CPUs. Put its optional OT_LEAK_REPAIR_API_KEY in OT_SECRETS_FILE.
set -euo pipefail
: "${OT_WORKSPACE:?Source env.sh before submitting}"
: "${TMPDIR:?Helma must provide node-local TMPDIR}"
repo=$SLURM_SUBMIT_DIR
bundle=${OT_RUNTIME_BUNDLE:?Set OT_RUNTIME_BUNDLE to a bundle of the current prep environment}
secrets=${OT_SECRETS_FILE:-$HOME/.env-judge-cli}
if [[ -r "$secrets" ]]; then
    set -a
    source "$secrets" >/dev/null 2>&1
    set +a
fi
# Claude: subscription authentication only.
if [[ "${OT_LEAK_REPAIR_LOCAL:-0}" != 1 ]]; then
    : "${CLAUDE_CODE_OAUTH_TOKEN:?Claude subscription OAuth token is required}"
    export CLAUDE_CODE_OAUTH_TOKEN
fi
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL OPENAI_API_KEY
unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY AWS_BEARER_TOKEN_BEDROCK
eval "$(/usr/bin/python3 "$repo/config/runtime.py" shell --cluster helma)"
if [[ -n "$OT_PYTHON_MODULE" ]]; then module load "$OT_PYTHON_MODULE"; fi
if [[ -n "$OT_APPTAINER_MODULE" ]]; then module load "$OT_APPTAINER_MODULE"; fi
# A local repair model runs on GPUs (vLLM); load CUDA only where the job has them.
if [[ -n "${OT_CUDA_MODULE:-}" && -n "${SLURM_JOB_GPUS:-${SLURM_GPUS_ON_NODE:-}}" ]]; then module load "$OT_CUDA_MODULE"; fi
runtime="$TMPDIR/leak-repair-runtime"
/usr/bin/python3 "$repo/hpc/local_runtime.py" stage "$bundle" "$repo" "$runtime"
source "$runtime/env.sh"
source "$VALIDATION_REPO/hpc/helma/proxy.sh"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OT_CLUSTER=helma
cd "$VALIDATION_REPO"
exec "$HARBOR_BRIDGE_VENV/bin/python" -u "$VALIDATION_REPO/hpc/leak_repair_job.py" "$@"
