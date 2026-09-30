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
mkdir -p "$PILOT_ROOT/scratch"
scratch=$(mktemp -d "$PILOT_ROOT/scratch/cce-resume-${SLURM_JOB_ID}-XXXXXX")
export TMPDIR=$scratch
export UV_CACHE_DIR="$PILOT_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$PILOT_ROOT/python"
export PIP_CACHE_DIR="$PILOT_ROOT/cache/pip"
export HF_HOME="$PILOT_ROOT/cache/huggingface"
export XDG_CACHE_HOME="$PILOT_ROOT/cache/xdg"
export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
export PATH="$PILOT_ROOT/venv/bin:$PATH"
cd "$repo"
# Keep scratch on abnormal termination; never erase the sole remaining evidence.
finish() {
    status=$?
    trap - EXIT
    if [ "$status" -eq 0 ]; then rm -rf -- "$scratch";
    else echo "Retained scratch after failure: $scratch"; fi
    exit "$status"
}
trap finish EXIT
"$PILOT_ROOT/venv/bin/python" hpc/zih/crosscodeeval_resume.py
