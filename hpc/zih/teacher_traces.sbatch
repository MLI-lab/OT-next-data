#!/bin/bash
# ZIH (TU Dresden) launcher — SKELETON, never run yet.
#
# Copy the structure from ../helma/teacher_traces.sbatch, which is the working one, and
# replace the cluster-specific parts marked TODO below. Everything else (the
# pilot layer in ../../teacher_traces, ../../harbor_patches) is cluster-agnostic.
#
# Submit as:  sbatch hpc/zih/teacher_traces.sbatch <weak|strong> <smoke|diag|sweep|full>
#SBATCH --job-name=cceval-pilot
#SBATCH --partition=TODO          # GPU partition name on ZIH
#SBATCH --nodes=1
#SBATCH --time=12:00:00
#SBATCH --signal=B:USR1@900
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${PILOT_ROOT:?Workspace with models, images, tasks and runs}"
: "${TMPDIR:?Node-local scratch required}"
: "${OTAGENT_ROOT:?Set by setup.sh in env.sh}"

echo 'hpc/zih/teacher_traces.sbatch is a skeleton; fill in the TODOs before using it.' >&2
exit 2

# --- What has to be decided per cluster -------------------------------------
# TODO partition, GPU type and --gres line; Helma uses one H200 (weak) or four
#      (strong) and allows at most 32 cores per GPU.
# TODO container runtime: Helma uses apptainer with a prebuilt runtime.sif
#      (../helma/runtime.def). If ZIH provides a different apptainer version or
#      a module system, load it here.
# TODO scratch paths: Helma stages tasks and trials on node-local $TMPDIR and
#      archives them to $PILOT_ROOT because of a file-count quota.
# TODO quota guard: Helma refuses to start when the shared file quota is nearly
#      exhausted (lfs quota). Replace with the ZIH equivalent or drop it.
# TODO GPUs per node: config/clusters.py derives the node count for a model from it
#      (Helma has 4 H200 per node, so a 756 GB model takes two nodes).
# TODO whether user network namespaces are allowed. Helma forbids them
#      (user.max_net_namespaces=0), so harbor_patches/bridge_worker.py probes
#      and only adds --net --network none where the node permits it. That probe
#      is portable and needs no change.
#
# Ports, PID namespace and per-trial Slurm steps are NOT cluster-specific: keep
# the job-derived ports (bridge 40000+, Ray 20000+, vLLM 30000+), keep
# `apptainer exec --pid`, and keep `srun --exact --gres=none` per trial.
