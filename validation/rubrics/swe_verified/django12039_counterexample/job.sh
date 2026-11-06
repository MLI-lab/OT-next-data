#!/bin/bash
#SBATCH --partition=cpu
#SBATCH --cpus-per-task=48
#SBATCH --mem=12G
#SBATCH --time=00:25:00
#SBATCH --job-name=django12039-proof
#SBATCH --output=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/django12039/slurm-%j.log
set -euo pipefail
out=/hnvme/workspace/y500bb12-rule-based-checks/sonnet-six-audit-20261106/django12039
bundle=/hnvme/workspace/y500bb12-rule-based-checks/judge-images-20261105/images/bundles-v1/build_27673990494e9e72-27673990494e.sif.tar
work=$(mktemp -d "${TMPDIR:-/tmp}/django12039-proof.XXXXXX")
cd "$work"
echo 'd857c9a71d4218bbdd61b5924b9618a986636225c8077b72a66eb94c75adf626  '"$bundle" | sha256sum -c - > "$out/image-verification.txt"
tar -xf "$bundle"
sif=$(find "$work" -name '*.sif' -print -quit)
echo 'fe837927b076a9de36679accef187ae4d1499e49f12bf4e1d6834c273b26f111  '"$sif" | sha256sum -c - >> "$out/image-verification.txt"
mkdir -p "$out/logs/verifier"
unshare -r apptainer exec --containall --cleanenv --no-home --writable-tmpfs --pwd /testbed --bind "$out:/audit" --bind "$out/tests:/tests:ro" --bind "$out/logs:/logs" --bind /etc/resolv.conf:/etc/resolv.conf:ro "$sif" /bin/bash /audit/inside.sh
