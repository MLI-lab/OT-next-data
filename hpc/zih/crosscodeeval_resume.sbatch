#!/bin/bash
#SBATCH --job-name=cce-resume
#SBATCH --partition=julia
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --mem=512G
#SBATCH --time=48:00:00
#SBATCH --signal=B:USR1@300
set -euo pipefail
repo=${OT_DATA_REPO:?}
export PILOT_ROOT=${CCE_ROOT:-/data/horse/ws/frwe188h-trp-shared/crosscodeeval}
case "$PILOT_ROOT" in /data/horse/*|/data/ws/*|/data/cat/*) ;; *) exit 2 ;; esac
source "$repo/hpc/zih/storage.sh"
zih_storage_init "$PILOT_ROOT" "$PILOT_ROOT/venv"
scratch=$TMPDIR
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
"$ZIH_PYTHON" hpc/zih/crosscodeeval_resume.py &
child=$!
# Let the worker preserve partial evidence before Slurm's hard time limit.
trap 'kill -TERM "$child" 2>/dev/null || true; wait "$child" || true; exit 143' USR1 TERM INT
wait "$child"
