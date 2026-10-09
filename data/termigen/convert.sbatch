#!/bin/bash
#SBATCH --job-name=termigen-convert
#SBATCH --account=y500bb
#SBATCH --partition=preempt_cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=6G
#SBATCH --time=01:00:00
#SBATCH --no-requeue
#SBATCH --output=/hnvme/workspace/y500bb10-termigen-audit/conversion.log
#SBATCH --open-mode=append
set -euo pipefail
umask 0022
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${TMPDIR:?Slurm node-local TMPDIR required}"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export ARROW_NUM_THREADS=1
export PYTHONPATH=/home/hpc/y500bb/y500bb10/repos/OT-next-data
cd /home/hpc/y500bb/y500bb10/repos/OT-next-data
printf 'Job=%s node=%s TMPDIR=%s\n' "$SLURM_JOB_ID" "$(hostname)" "$(realpath "$TMPDIR")"
lfs quota -u y500bb10 /hnvme
trap 'lfs quota -u y500bb10 /hnvme' EXIT
/home/hpc/y500bb/y500bb10/.venvs/ot-next-data-prep/bin/python -B -u \
  data/termigen/convert_to_parquet.py \
  --source /hnvme/workspace/y500bb10-harness_optim_tmax_paper/datasets/terminal-bench-env/environments_harbor \
  --output /hnvme/workspace/y500bb10-termigen-audit/tasks.parquet \
  --verify
