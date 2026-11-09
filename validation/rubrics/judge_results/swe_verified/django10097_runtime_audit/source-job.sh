#!/bin/bash
#SBATCH --partition=preempt_cpu
#SBATCH --cpus-per-task=48
#SBATCH --mem=12G
#SBATCH --time=00:40:00
#SBATCH --job-name=django10097-proof
#SBATCH --output=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/django10097/slurm-%j.log
set -euo pipefail
out=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/django10097
bundle=/hnvme/workspace/y500bb12-rule-based-checks/judge-images-20261105/images/bundles-v1/build_2045d9ed467352ef-2045d9ed4673.sif.tar
work=$(mktemp -d "${TMPDIR:-/tmp}/django10097-proof.XXXXXX")
cd "$work"
echo '1b8c9a2bf2375db82d20aa0c6955ce3dc9f86b66d6d5b822885ccb192635878f  '"$bundle" | sha256sum -c - > "$out/image-verification.txt"
tar -xf "$bundle"
sif="$work/build_2045d9ed467352ef-2045d9ed4673.sif"
echo 'dca90872b4794ad2960db484aab37567b859a21ff993017e9da1a71d63c54663  '"$sif" | sha256sum -c - >> "$out/image-verification.txt"
for mode in baseline_source; do
 mkdir -p "$out/$mode/logs/verifier"
 unshare -r apptainer exec --containall --cleanenv --no-home --writable-tmpfs --pwd /testbed --bind "$out:/audit" --bind "$out/tests:/tests:ro" --bind "$out/$mode/logs:/logs" --bind /etc/resolv.conf:/etc/resolv.conf:ro "$sif" /bin/bash /audit/inside-source.sh "$mode"
done
