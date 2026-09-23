# Cluster layers

One folder per machine. Everything else in this repo is cluster-agnostic: the
launcher here only stages data, serves the model and calls
`../teacher_traces/*` and `../harbor_patches/*`.

| Folder | State |
| --- | --- |
| `helma/` | Working. NHR@FAU, H200 partition, apptainer, node-local `$TMPDIR`, Lustre file-count quota. All results so far were produced here. |
| `zih/` | Skeleton only — `teacher_traces.sbatch` lists what has to be decided per cluster and exits 2. |

Porting checklist: partition and `--gres`, container runtime, scratch paths,
quota guard, and whether user network namespaces are allowed. Keep the
job-derived ports, `apptainer exec --pid` and the per-trial
`srun --exact --gres=none` — those are not cluster-specific, they are what keeps
two jobs on one node from killing each other.
