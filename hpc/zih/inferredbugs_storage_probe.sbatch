#!/bin/bash
#SBATCH --job-name=ib-storage-probe
#SBATCH --partition=barnard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=03:00:00
#SBATCH --output=slurm-%x-%j.out
# Where should the check harness keep its per-check working directories (checkout, build,
# per-task dependency cache, logs, container overlay)? This runs the same tasks with the same
# workers on Horse and on the job-local storage storage.sh selects for this cluster/partition
# (storage_paths.sh), in both orders, and records each arm's wall time and the local peak size.
# Local RAM storage counts against the job's memory. A measurement, not a validation run.
#   PROBE_RUN        output directory on data storage; holds ids.txt (the sample)
#   CHECK_PARQUET    the packaged tasks to check (as in inferredbugs_verifier_check.sbatch)
#   PROBE_ORDER      arms in order, default "horse local local horse"
#   PROBE_EXTRA_IDS  optional file of task ids to run buggy,full packaged from this working tree
#                    (not the parquet), on the last arm's storage: a check of script changes
set -euo pipefail
source "${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}/hpc/zih/inferredbugs_common.sh"
: "${PROBE_RUN:?Set PROBE_RUN to a data directory containing ids.txt}"
: "${CHECK_PARQUET:?Set CHECK_PARQUET to the packaged tasks}"
require_data_path "$PROBE_RUN"; require_data_path "$CHECK_PARQUET"
[[ -s "$PROBE_RUN/ids.txt" ]] || { echo 'PROBE_RUN/ids.txt is missing.' >&2; exit 2; }
source "$repo/hpc/zih/storage.sh"
zih_storage_init "$IB_ROOT" "$IB_ROOT/venv"
trap 'zih_storage_cleanup; rm -rf -- "$scratch" "$ZIH_DURABLE_SCRATCH"' EXIT
export APPTAINER_TMPDIR=$scratch
workers=${CHECK_WORKERS:-8}
local_dir=''
case "$ZIH_STATIC_TMPDIR" in /tmp/*|/dev/shm/*) local_dir=$ZIH_STATIC_TMPDIR/ib-tasks ;; esac
{
  echo "job=$SLURM_JOB_ID cluster=$ZIH_CLUSTER partition=$ZIH_PARTITION node=$(hostname) policy=$ZIH_STATIC_POLICY workers=$workers"
  echo "horse=$ZIH_DURABLE_SCRATCH ($(findmnt -T "$ZIH_DURABLE_SCRATCH" -no FSTYPE))"
  echo "local=${local_dir:-none} ($([[ -n "$local_dir" ]] && findmnt -T "$ZIH_STATIC_TMPDIR" -no FSTYPE,SIZE,AVAIL))"
} | tee "$PROBE_RUN/storage-$SLURM_JOB_ID.txt"
arm() {   # NAME SCRATCH IDS VARIANTS [harness options]
  local name=$1 dir=$2 ids=$3 variants=$4 out start sampler=''
  shift 4
  out="$PROBE_RUN/$SLURM_JOB_ID-$name"
  mkdir -p "$out" "$dir"
  cp "$ids" "$out/ids.txt"
  # du is cheap on local storage only; on Horse it would disturb what is measured
  if [[ "$dir" == "$local_dir"* && -n "$local_dir" ]]; then
    ( while sleep 20; do du -sk "$dir" 2>/dev/null | cut -f1; done > "$out/local-scratch-kb.log" ) & sampler=$!
  fi
  start=$(date +%s)
  "$IB_PYTHON" -u hpc/helma/inferredbugs_verifier_check.py \
    --inferredbugs-root "$CHECK_SOURCE" --images "$CHECK_IMAGES" \
    --scratch "$dir" --output "$out" --ids-file "$out/ids.txt" \
    --proxy-helper "$repo/hpc/helma/configure_maven_proxy.py" \
    --workers "$workers" --variants "$variants" \
    --step-timeout "${CHECK_STEP_TIMEOUT:-2400}" --timeout "${CHECK_TIMEOUT:-7200}" "$@" || echo "$name: harness exit $?"
  [[ -z "$sampler" ]] || kill "$sampler" 2>/dev/null || true
  echo "$name $(( $(date +%s) - start )) s" | tee -a "$PROBE_RUN/arms-$SLURM_JOB_ID.txt"
  rm -rf -- "$dir"
}
n=0; last=$ZIH_DURABLE_SCRATCH/ib-tasks
for where in ${PROBE_ORDER:-horse local local horse}; do
  n=$((n + 1))
  case "$where" in
    horse) last=$ZIH_DURABLE_SCRATCH/ib-tasks ;;
    local) [[ -n "$local_dir" ]] || { echo "arm $n: no local storage on this node, skipped"; continue; }; last=$local_dir ;;
    *) echo "unknown arm $where" >&2; exit 2 ;;
  esac
  arm "$n-$where" "$last" "$PROBE_RUN/ids.txt" buggy --parquet "$CHECK_PARQUET"
done
if [[ -n "${PROBE_EXTRA_IDS:-}" ]]; then
  arm extra "$last" "$PROBE_EXTRA_IDS" buggy,full
fi
