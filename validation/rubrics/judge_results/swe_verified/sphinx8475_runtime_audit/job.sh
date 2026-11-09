#!/bin/bash
#SBATCH --partition=preempt_cpu
#SBATCH --cpus-per-task=16
#SBATCH --mem=12G
#SBATCH --time=00:40:00
#SBATCH --job-name=sphinx8475-proof
#SBATCH --output=/hnvme/workspace/y500bb12-rule-based-checks/sphinx8475-audit-20261107/slurm-%j.log
set -euo pipefail
out=/hnvme/workspace/y500bb12-rule-based-checks/sphinx8475-audit-20261107
bundle=/hnvme/workspace/y500bb12-rule-based-checks/judge-images-20261105/images/bundles-v1/build_0d7ecd6e59824346-0d7ecd6e5982.sif.tar
work=$(mktemp -d "${TMPDIR:-/tmp}/sphinx8475-proof.XXXXXX")
cd "$work"
echo '3c5f540724a5c659c2978ad899c39ef2d45d52f79fbd979dcbd2e59328756cf1  '"$bundle" | sha256sum -c - > "$out/image-verification.txt"
tar -xf "$bundle"
sif=$(ls "$work"/*.sif)
for mode in baseline gold gold-rA; do
 mkdir -p "$out/$mode/logs/verifier"
 unshare -r apptainer exec --containall --cleanenv --no-home --writable-tmpfs --pwd /testbed --bind "$out:/audit" --bind "$out/pkg/task:/task:ro" --bind "$out/pkg/task/tests:/tests:ro" --bind "$out/$mode/logs:/logs" --bind /etc/resolv.conf:/etc/resolv.conf:ro "$sif" /bin/bash /audit/inside.sh "$mode"
done
