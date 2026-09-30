#!/bin/bash
#SBATCH --job-name=ib-package
#SBATCH --partition=barnard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=slurm-%x-%j.out
# Run only after reviewing verifier evidence and regenerating the warning tables.
set -euo pipefail
source "${OT_DATA_REPO:-${SLURM_SUBMIT_DIR:?}}/hpc/zih/inferredbugs_common.sh"
IB_INPUT=${IB_INPUT:-$IB_ROOT/upstream/tasks.parquet}
IB_OUTPUT=${IB_OUTPUT:-$IB_ROOT/tasks.patched.parquet}
require_data_path "$IB_INPUT"
require_data_path "$IB_OUTPUT"
[[ -f "$IB_INPUT" && ! -e "$IB_OUTPUT" ]] || {
  echo 'Input must exist and output must be new.' >&2; exit 2;
}
"$IB_PYTHON" -u data/inferredbugs/patch_tasktrove_inferredbugs_v3.py \
  --input "$IB_INPUT" --output "$IB_OUTPUT" --cache-dir "$IB_ROOT/package-cache" \
  --inferredbugs-root "$CHECK_SOURCE" --trust-recipes --no-verify-package --repair-backend none
