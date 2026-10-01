#!/bin/bash
#SBATCH --job-name=calibforge-pilot
#SBATCH --partition=barnard
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --signal=B:USR1@300
set -euo pipefail
run=$(realpath "${1:?Pass the prepared run directory}")
export PILOT_ROOT=$(realpath "${2:?Pass the data workspace}")
for path in "$PILOT_ROOT" "$run"; do
    case "$path" in
        /data/horse/*|/data/ws/*|/data/cat/*) ;;
        *) echo 'Data and results must be in a data workspace.' >&2; exit 2 ;;
    esac
done
source "$run/code/hpc/zih/storage.sh"
zih_storage_init "$PILOT_ROOT" /data/horse/ws/frwe188h-trp-shared/seta/venv
trap zih_storage_cleanup EXIT
export LITELLM_LOCAL_MODEL_COST_MAP=True
unset GPTZERO_API_KEY
cd "$run/code"
python hpc/zih/calibforge_validation.py "$run" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true; wait "$child" || true; exit 143' USR1 TERM INT
wait "$child"
