# Running a different dataset

Package Harbor task directories in a gzip-compressed tar archive with paths
`tasks/<task-id>/task.toml`, `tasks/<task-id>/instruction.md`, and the task's
remaining files. Store the archive and its JSON configuration in your workspace.
For example, `new-data.json`:

```json
{
  "dataset": "new-data",
  "archive": "tasks/new-data.tar.gz",
  "sha256": "REPLACE_WITH_THE_ARCHIVE_SHA256",
  "groups": {
    "coding": ["task-one", "task-two"],
    "reasoning": ["task-three"]
  },
  "artifacts": []
}
```

Archive paths are relative to the configuration file; absolute paths also work.
Use `sha256sum tasks/new-data.tar.gz` to get the checksum. Preparation verifies
it before creating the run. Each task ID must appear in exactly one group.
Choose group names other than `total` (reserved for the summary), or use one
`all` group. List tasks in your desired order:
`smoke`, `diag`, and `sweep` take the first 1, 5, and 25 tasks per group (or all
available tasks if fewer); `full` takes every listed task. There is no 250-task
requirement. Optional `task_repo` and `task_revision` record the source version;
otherwise the archive checksum identifies the task version. `artifacts` lists
absolute paths inside each task container to save after an attempt.

```bash
python verify_pipeline.py model --dataset-config /path/to/new-data.json \
    --model coder-30b --stage smoke --attempts 8 --time 00:45:00
```

Add `--dry-run` to inspect the submission. Direct Slurm submissions accept the
configuration path as a third argument after the model and stage. Task groups
are saved with each run and used by pass@k reporting and plots. New datasets
still need valid Harbor task environments and scoring scripts; this option does
not convert raw data into tasks. The same Slurm/Apptainer runtime is required.
