#!/bin/bash
#SBATCH --job-name=task-validation
# Resources and the frozen request are supplied by validation/run.py.
set -euo pipefail
export OT_CLUSTER=lrz
# Require node-local, executable storage on any allocated LRZ CPU node.
validation_tmp_base=/tmp
if [ -d /raid/enroot/tmp ] && [ -w /raid/enroot/tmp ]; then validation_tmp_base=/raid/enroot/tmp; fi
test -d "$validation_tmp_base"
df -h "$validation_tmp_base"
case "$(findmnt -T "$validation_tmp_base" -n -o FSTYPE)" in
    nfs*|lustre|gpfs|beegfs) echo 'TMPDIR must be node-local, not shared storage' >&2; exit 1 ;;
esac
case ",$(findmnt -T "$validation_tmp_base" -n -o OPTIONS)," in
    *,noexec,*|*,ro,*) echo 'TMPDIR must be writable and executable' >&2; exit 1 ;;
esac
validation_job_tmp=$(mktemp -d "$validation_tmp_base/ot-validation-${SLURM_JOB_ID:?}-XXXXXX")
export TMPDIR="$validation_job_tmp"
# Slurm's B:USR1 warning targets this batch shell, not its worker.
# Forward it and wait for the worker to save evidence before removing scratch.
validation_worker_pid=
validation_forward_signal() {
    if [[ -n "$validation_worker_pid" ]]; then
        kill -s "$1" "$validation_worker_pid" 2>/dev/null || true
    fi
}
trap 'validation_forward_signal USR1' USR1
trap 'validation_forward_signal TERM' TERM
trap 'validation_forward_signal INT' INT
bash "${VALIDATION_REPO:?}/hpc/validation.sh" "$@" &
validation_worker_pid=$!
validation_worker_status=0
while true; do
    wait "$validation_worker_pid" && validation_worker_status=0 || validation_worker_status=$?
    # A trapped signal interrupts wait while the worker is still saving results.
    kill -0 "$validation_worker_pid" 2>/dev/null || break
done
# The worker has saved its evidence to shared storage. LRZ does not clean
# /raid between jobs, so remove this job's scratch explicitly.
rm -rf --one-file-system "$validation_job_tmp"
exit "$validation_worker_status"
