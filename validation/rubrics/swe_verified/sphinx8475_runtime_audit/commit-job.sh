#!/bin/bash
#SBATCH --partition=preempt_cpu
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:15:00
#SBATCH --job-name=sphinx8475-commit
#SBATCH --output=/hnvme/workspace/y500bb12-rule-based-checks/sphinx8475-audit-20261107/commit-%j.log
set -euo pipefail
work=$(mktemp -d "${TMPDIR:-/tmp}/s8475c.XXXXXX"); cd "$work"
tar -xf /hnvme/workspace/y500bb12-rule-based-checks/judge-images-20261105/images/bundles-v1/build_0d7ecd6e59824346-0d7ecd6e5982.sif.tar
sif=$(ls "$work"/*.sif)
unshare -r apptainer exec --containall --cleanenv --no-home --writable-tmpfs --pwd /testbed "$sif" /bin/bash -c 'git log --oneline -3; git show --stat HEAD | head -20; git show HEAD -- tox.ini'
