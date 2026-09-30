#!/bin/bash
#SBATCH --job-name=ib-verifier
#SBATCH --partition=barnard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=12:00:00
#SBATCH --output=slurm-%x-%A_%a.out
# No array by default: use a pilot first; see README.md for the full array.
set -euo pipefail
source "${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}/hpc/zih/inferredbugs_common.sh"
: "${CHECK_RUN:?Set CHECK_RUN to a shared output directory containing ids.txt}"
require_data_path "$CHECK_RUN"
[[ "$CHECK_RUN" = /* && -s "$CHECK_RUN/ids.txt" ]] || {
  echo 'CHECK_RUN must be absolute and contain a nonempty ids.txt.' >&2; exit 2;
}
[[ -d "$CHECK_SOURCE/inferredbugs" && -d "$CHECK_IMAGES" ]] || {
  echo 'Run inferredbugs_warmup.sbatch first.' >&2; exit 2;
}
"$IB_PYTHON" -c 'import pyarrow'
command -v apptainer >/dev/null
workers=${CHECK_WORKERS:-8}
shard=${SLURM_ARRAY_TASK_ID:-0}
shards=${CHECK_SHARDS:-1}
[[ "$workers" =~ ^[1-9][0-9]*$ && "$shards" =~ ^[1-9][0-9]*$ ]] || exit 2
(( shard < shards && workers <= SLURM_CPUS_PER_TASK )) || exit 2
if [[ -n "${SLURM_ARRAY_TASK_ID:-}" ]]; then
  [[ "${SLURM_ARRAY_TASK_MIN:?}" == 0 && "${SLURM_ARRAY_TASK_STEP:?}" == 1 ]] || exit 2
  (( SLURM_ARRAY_TASK_MAX + 1 == shards )) || {
    echo 'CHECK_SHARDS must match a contiguous zero-based array.' >&2; exit 2;
  }
else
  (( shards == 1 )) || { echo 'CHECK_SHARDS > 1 requires an array.' >&2; exit 2; }
fi
# Protect against overlapping submissions for the same shard.
exec 8>"$CHECK_RUN/shard-$shard.lock"
flock -n 8 || { echo 'This shard is already running.' >&2; exit 2; }
extra=()
if [[ -n "${CHECK_PARQUET:-}" ]]; then
  require_data_path "$CHECK_PARQUET"
  extra+=(--parquet "$CHECK_PARQUET")
fi
"$IB_PYTHON" -u hpc/helma/inferredbugs_verifier_check.py \
  --inferredbugs-root "$CHECK_SOURCE" --images "$CHECK_IMAGES" \
  --scratch "$scratch/tasks" --output "$CHECK_RUN" --ids-file "$CHECK_RUN/ids.txt" \
  --proxy-helper "$repo/hpc/helma/configure_maven_proxy.py" \
  --workers "$workers" --variants buggy,full --shard "$shard" --shards "$shards" \
  --step-timeout "${CHECK_STEP_TIMEOUT:-2400}" --timeout "${CHECK_TIMEOUT:-7200}" "${extra[@]}"
# Deliberately omit --summary-file: verifier_keys.py needs result.json from
# EVERY verification's logs.tar.gz, including successful ones. Per-task caches
# stay isolated; Maven/NuGet project artifacts must not leak between variants.
