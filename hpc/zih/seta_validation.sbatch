#!/bin/bash
#SBATCH --job-name=seta-validation
#SBATCH --partition=barnard
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=24:00:00
#SBATCH --signal=B:USR1@300
set -euo pipefail
run=$(realpath "${1:?Pass the prepared run directory}")
export SETA_ROOT=${SETA_ROOT:-/data/horse/ws/frwe188h-trp-shared/seta}
export PILOT_ROOT=$(realpath "$SETA_ROOT")
for path in "$PILOT_ROOT" "$run"; do
    case "$path" in
        /data/horse/*|/data/ws/*|/data/cat/*) ;;
        *) echo 'SETA data and results must be in a data workspace.' >&2; exit 2 ;;
    esac
done
source "$run/code/hpc/zih/storage.sh"
zih_storage_init "$PILOT_ROOT" "$PILOT_ROOT/venv"
trap zih_storage_cleanup EXIT
export LITELLM_LOCAL_MODEL_COST_MAP=True
unset GPTZERO_API_KEY
cd "$run/code"
# Preserve scratch on failures/time limits for diagnosis and partial evidence.
python hpc/zih/seta_validation.py "$run" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true; wait "$child" || true; exit 143' USR1 TERM INT
wait "$child"
