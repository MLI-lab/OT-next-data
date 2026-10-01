#!/bin/bash
#SBATCH --job-name=ot-storage-probe
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:20:00
#SBATCH --licenses=horse
set -euo pipefail
root=${1:?bundle directory}
storage=${2:?scratch mount}
export PYTHONDONTWRITEBYTECODE=1
# System Python orchestrates probes; each tested interpreter is explicit.
/usr/bin/python3 "$root/storage_probe.py" run "$root" --storage "$storage"
