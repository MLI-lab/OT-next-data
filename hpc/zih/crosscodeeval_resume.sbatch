#!/bin/bash
#SBATCH --job-name=cce-resume
#SBATCH --partition=julia
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --mem=512G
#SBATCH --time=12:00:00
#SBATCH --signal=B:USR1@300
set -euo pipefail
repo=${OT_DATA_REPO:?}
export PILOT_ROOT=${CCE_ROOT:-/data/horse/ws/frwe188h-trp-shared/crosscodeeval}
case "$PILOT_ROOT" in /data/horse/*|/data/ws/*|/data/cat/*) ;; *) exit 2 ;; esac
source "$repo/hpc/zih/storage.sh"
zih_storage_init "$PILOT_ROOT" "$PILOT_ROOT/venv"
scratch=$TMPDIR
export UV_CACHE_DIR="$PILOT_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$PILOT_ROOT/python"
export PIP_CACHE_DIR="$PILOT_ROOT/cache/pip"
export HF_HOME="$PILOT_ROOT/cache/huggingface"
export XDG_CACHE_HOME="$PILOT_ROOT/cache/xdg"
export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
cd "$repo"
# Evidence stays on Horse; only private local static-check scratch is cleaned.
finish() {
    status=$?
    trap - EXIT
    zih_storage_cleanup
    echo "Retained durable evidence: $scratch"
    exit "$status"
}
trap finish EXIT
"$ZIH_PYTHON" hpc/zih/crosscodeeval_resume.py
