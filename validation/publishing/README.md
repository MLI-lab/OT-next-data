# Publishing dataset releases

[readme.py](readme.py) builds all three release cards from `results.parquet` and saved run metadata:

| View | Dataset stats and results |
| --- | --- |
| v1 | Upstream and kept tasks, original and shared images, declared internet settings of kept tasks, outcomes per rule. |
| v2 | Validated v1 tasks, student-solved tasks, model and k, counts from 0/k to k/k. |
| v3 | Existing v2 tasks, newly solved tasks, combined count, stronger-model results. |

All views include recorded timing and allocated resources. Unknown measurements say “not recorded”. Rule times are summed task times, separate from overall wall time and image preparation time. Do not infer these from concurrent stage durations.

## Generate locally

```bash
python -m validation.publishing.readme DATASET/results.parquet \
  --dataset DATASOURCE --view v1 --run run.json \
  --annotation annotation.json --out DATASET
```

Use `--view v2 --evaluation-key COLUMN` for the student evaluation. For v3, also supply `--student-key STUDENT_COLUMN`. Keys are the exact `passk_...` columns in the results table. V3 includes only newly solved tasks whose complete student evaluation had no success. Missing student attempts stay unresolved.

Each command writes `vN/README.md`, `vN/selection.parquet` and its input metadata. Selections point to existing task revisions; tasks are not copied. The results index must contain published `task_file` pointers before generating selections. The publisher reads `allow_internet` from each task’s `task.toml` and saves it in the results index. V1 reports true, false, unspecified and unrecorded counts separately; these describe configuration, not verified network isolation. Only latest revisions are selected, so changing task content requires new validation evidence.

`run.json` supplies measured metadata and the required checks, for example:

```json
{
  "required_rules": ["build", "reference", "nop", "nproc"],
  "upstream_tasks": 100,
  "original_image_count": 90,
  "current_image_count": 3,
  "wall_seconds": 1200,
  "image_build_seconds": 600,
  "resources": {"cluster": "helma", "cpus": 48, "gpus": 0, "memory_gib": 128, "concurrency": 12},
  "rule_timings": {"nproc": {"tasks": 100, "total_seconds": 5, "mean_seconds": 0.05, "median_seconds": 0.04}},
  "evaluations": {"passk_EXACT_COLUMN": {"model": "full/model-id", "k": 4, "thinking": false, "network": "offline"}}
}
```

The numbers and checks above are illustrative. Supply every required check from the actual run, including dynamic checks. Record effective evaluation settings, including token limits and sampling, under each evaluation. Missing measurements can be omitted. Timing collection remains separate: existing reports do not always measure individual rules or historical image builds.

## Datasource classification

Pass the saved `annotation.json` from [the automatic classifier](annotation.py), either its full record or its `annotation` object. Its [prompt](../../data/annotate_dataset/prompt_template.txt), [domain taxonomy](../../data/annotate_dataset/domain_taxonomy.txt) and [capability taxonomy](../../data/annotate_dataset/capability_taxonomy.txt) define the datasource information. The templates preserve its sources, domains, capabilities and expected benchmark transfer. Rendering does not call a model again.

## Publish with task revisions

The existing [publisher](publish.py) accepts `--release-spec releases.json` with the default revision layout. Add it to your normal publishing command; use `--dry-run` to inspect the files first.

```json
{
  "DATASOURCE": {
    "view": "v1",
    "run": "/absolute/path/run.json",
    "annotation": "/absolute/path/annotation.json"
  }
}
```

For v2 or v3 add `evaluation_key`, and for v3 `student_key`. Each folder gets a versioned card and selection, and its main README displays that release. Classification can come from the supplied file, `--readme` generation, or the existing published `annotation.json`. No payload history is deleted. The older `publish_pass_counts.py` remains for existing consumers; new release cards use this workflow.
