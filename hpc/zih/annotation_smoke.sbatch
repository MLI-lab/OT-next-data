#!/bin/bash
#SBATCH --job-name=annotation-smoke
#SBATCH --partition=barnard
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:45:00
set -euo pipefail
umask 077
repo=${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}
run=$(realpath "${1:?Pass a fresh workspace run directory}")
case "$run" in
    /data/horse/*|/data/ws/*|/data/cat/*) ;;
    *) echo 'Use an available ZIH data workspace.' >&2; exit 2 ;;
esac
mkdir -p "$run/scratch" "$run/cache" "$run/images"
export TMPDIR="$run/scratch" APPTAINER_TMPDIR="$run/scratch"
export APPTAINER_CACHEDIR="$run/cache/apptainer" HARBOR_SIF_CACHE="$run/images"
export XDG_CACHE_HOME="$run/cache/xdg" HF_HOME="$run/cache/huggingface"
export UV_CACHE_DIR="$run/cache/uv" PIP_CACHE_DIR="$run/cache/pip"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LITELLM_LOCAL_MODEL_COST_MAP=True
export PYTHONPATH="$repo/external/harbor-validation/src:$repo"
export BRIDGE_USE_FAKEROOT=1 BRIDGE_INSTANCE_REUSE=0 BRIDGE_WORKERS_DEAD_TIMEOUT=60
export PILOT_NET_ISOLATION=0 PILOT_TRIAL_SRUN=0
export APPTAINER_NO_MOUNT=hostfs,bind-paths,cwd
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
# Only credential variables are inherited by the installed Claude agent.
set -a
source "${ANNOTATION_SECRETS:-/home/frwe188h/secrets.env}" >/dev/null 2>&1
set +a
cd "$repo"
exec /data/horse/ws/frwe188h-trp-shared/crosscodeeval/venv/bin/python \
    hpc/zih/annotation_smoke.py \
    /data/horse/ws/frwe188h-trp-shared/crosscodeeval/parquets-pinned/python-v3.parquet \
    "$run" --model "${ANNOTATION_MODEL:-claude-fable-5-1}"
