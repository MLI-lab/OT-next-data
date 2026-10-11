#!/bin/bash
#SBATCH --job-name=task-validation
# The new shared setup has not yet been smoke-tested on ZIH.
# Resources and the frozen request are supplied by validation/run.py.
set -euo pipefail
export OT_CLUSTER=zih
# Some ZIH partitions do not provide a per-job TMPDIR. Require local, executable storage.
validation_tmp_base=${TMPDIR:-/tmp}
case "$(findmnt -T "$validation_tmp_base" -n -o FSTYPE)" in
    nfs*|lustre|gpfs|beegfs) echo 'TMPDIR must be node-local, not shared storage' >&2; exit 1 ;;
esac
case ",$(findmnt -T "$validation_tmp_base" -n -o OPTIONS)," in
    *,noexec,*|*,ro,*) echo 'TMPDIR must be writable and executable' >&2; exit 1 ;;
esac
validation_job_tmp=$(mktemp -d "$validation_tmp_base/ot-validation-${SLURM_JOB_ID:?}-XXXXXX")
export TMPDIR="$validation_job_tmp"
trap 'rm -rf -- "$validation_job_tmp"' EXIT
bash "${VALIDATION_REPO:?}/hpc/validation.sh" "$@"
