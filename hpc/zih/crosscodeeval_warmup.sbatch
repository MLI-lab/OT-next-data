#!/bin/bash
#SBATCH --job-name=cce-storage
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --licenses=horse
set -euo pipefail
repo=${OT_DATA_REPO:-$PWD}
source "$repo/hpc/zih/storage.sh"
zih_storage_init "${CCE_ROOT:-/data/horse/ws/frwe188h-trp-shared/crosscodeeval}" "${CCE_VENV:-/data/horse/ws/frwe188h-trp-shared/crosscodeeval/venv}"
trap zih_storage_cleanup EXIT
# A project can select an approved SSD/NVMe mount explicitly; default uses the
# same verified SSD/RAM choice as static checks. Never silently use root /tmp.
local_root=${CCE_WARMUP_LOCAL_ROOT:-$ZIH_STATIC_TMPDIR/containers}
export APPTAINER_CACHEDIR="$ZIH_CACHE_DIR/apptainer"
"$ZIH_PYTHON" "$repo/data/crosscodeeval/warmup.py" \
    --tasks "${CCE_WARMUP_TASKS:-$ZIH_DATA_ROOT/recovery/137304-stage1/tasks}" \
    --cache "$ZIH_IMAGE_DIR" --out "$ZIH_WARMUP_DIR/$ZIH_CLUSTER-$SLURM_JOB_ID" \
    --local-root "$local_root" "$@"
