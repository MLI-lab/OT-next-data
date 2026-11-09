#!/bin/bash
#SBATCH --partition=preempt_cpu
#SBATCH --cpus-per-task=48
#SBATCH --mem=12G
#SBATCH --time=00:20:00
#SBATCH --job-name=sympy13878-proof
#SBATCH --output=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/sympy13878/slurm-%j.log
set -euo pipefail
out=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/sympy13878
bundle=/hnvme/workspace/y500bb12-rule-based-checks/judge-images-20261105/images/bundles-v1/build_c65c58808f578fcf-c65c58808f57.sif.tar
work=$(mktemp -d "${TMPDIR:-/tmp}/sympy13878-proof.XXXXXX")
cd "$work"
echo 'cc64582c0fdec1c4a45f21f94e4484bbc1edd139a691fbc89a0e972611a330cf  '"$bundle" | sha256sum -c - > "$out/image-verification.txt"
tar -xf "$bundle"
sif="$work/build_c65c58808f578fcf-c65c58808f57.sif"
echo '6d33c2cdac38fb85c7cf9a3feaeeec7a265deb173cfab8ed9472579f89faa8e6  '"$sif" | sha256sum -c - >> "$out/image-verification.txt"
for mode in equivalent gold; do
 mkdir -p "$out/$mode/logs/verifier"
 unshare -r apptainer exec --containall --cleanenv --no-home --writable-tmpfs --pwd /testbed --bind "$out:/audit" --bind "$out/tests:/tests:ro" --bind "$out/$mode/logs:/logs" --bind /etc/resolv.conf:/etc/resolv.conf:ro "$sif" /bin/bash /audit/inside.sh "$mode"
done
