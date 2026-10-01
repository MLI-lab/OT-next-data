#!/bin/bash
#SBATCH --job-name=cce-validation
#SBATCH --partition=barnard
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=08:00:00
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
repo=${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}
export PILOT_ROOT=${CCE_ROOT:-/data/horse/ws/frwe188h-trp-shared/crosscodeeval}
case "$PILOT_ROOT" in
  /data/horse/*|/data/ws/*|/data/cat/*) ;;
  *) echo 'CCE_ROOT must be in an available data workspace.' >&2; exit 2 ;;
esac
source "$repo/hpc/zih/storage.sh"
zih_storage_init "$PILOT_ROOT" "$PILOT_ROOT/venv"
export UV_CACHE_DIR="$PILOT_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$PILOT_ROOT/python"
export PIP_CACHE_DIR="$PILOT_ROOT/cache/pip"
export HF_HOME="$PILOT_ROOT/cache/huggingface"
export XDG_CACHE_HOME="$PILOT_ROOT/cache/xdg"
export PYTHONUNBUFFERED=1
cd "$repo"
trap zih_storage_cleanup EXIT
"$ZIH_PYTHON" hpc/zih/crosscodeeval_validation.py
