#!/bin/bash
#SBATCH --job-name=task-validation
# Resources and the frozen request are supplied by validation/run.py.
set -euo pipefail
export OT_CLUSTER=helma
exec bash "${VALIDATION_REPO:?}/hpc/validation.sh" "$@"
