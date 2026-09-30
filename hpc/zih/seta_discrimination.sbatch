#!/bin/bash
#SBATCH --job-name=seta-discrimination
#SBATCH --partition=julia
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
set -euo pipefail
base=/data/horse/ws/frwe188h-trp-shared/seta
export TMPDIR=$(mktemp -d "$base/scratch/discrimination-${SLURM_JOB_ID}-XXXXXX")
export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR="$base/cache/apptainer"
export XDG_CACHE_HOME="$base/cache/xdg"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
"$base/venv/bin/python" data/seta/audit.py --pilot "${1:?pilot parquet}" \
    --image "${2:?task image}" --out "${3:?new audit directory}"
