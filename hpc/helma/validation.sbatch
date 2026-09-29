#!/bin/bash
#SBATCH --job-name=task-validation
set -euo pipefail
: "${PILOT_ROOT:?Source env.sh before submitting}"
: "${TMPDIR:?Helma must provide node-local TMPDIR}"
# Slurm copies this script into its spool; the submitter exports the repository
# path explicitly, so locating the worker does not depend on the spool path.
source "$VALIDATION_REPO/hpc/helma/proxy.sh"
exec "$PILOT_ROOT/envs/prep/bin/python" -u "$VALIDATION_REPO/hpc/helma/validation_worker.py" "$1"
