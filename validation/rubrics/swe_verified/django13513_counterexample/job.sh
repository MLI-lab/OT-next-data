#!/bin/bash
#SBATCH --partition=cpu
#SBATCH --cpus-per-task=48
#SBATCH --mem=12G
#SBATCH --time=00:20:00
#SBATCH --job-name=django13513-proof
#SBATCH --output=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/django13513/slurm-%j.log
set -euo pipefail
out=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/django13513
bundle=/hnvme/workspace/y500bb12-rule-based-checks/judge-images-20261105/images/bundles-v1/build_e36851c11f016bbe-e36851c11f01.sif.tar
work=$(mktemp -d "${TMPDIR:-/tmp}/django13513-proof.XXXXXX")
cd "$work"
echo '1efc0ba7b31cea8fa33637d2de73fbacc409103660f287bbec5bb6116de20358  '"$bundle" | sha256sum -c - > "$out/image-verification.txt"
tar -xf "$bundle"
sif="$work/build_e36851c11f016bbe-e36851c11f01.sif"
echo '0cc68515d77c453a0debb86323a8ce85d065bf4ef0ce373c077c5bafe698c3fd  '"$sif" | sha256sum -c - >> "$out/image-verification.txt"
for mode in baseline instruction gold; do
 mkdir -p "$out/$mode/logs/verifier"
 unshare -r apptainer exec --containall --cleanenv --no-home --writable-tmpfs --pwd /testbed --bind "$out:/audit" --bind "$out/tests:/tests:ro" --bind "$out/$mode/logs:/logs" --bind /etc/resolv.conf:/etc/resolv.conf:ro "$sif" /bin/bash /audit/inside.sh "$mode"
done
