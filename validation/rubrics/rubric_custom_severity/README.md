# Custom severity rubric

Select this rubric with `--review-rubric custom_severity`. It uses the existing stage-5 runner, task environments, model selection, Slurm submission, checkpoints and JSON reports. A separate run script is not needed.

The existing `custom`, `terminal-bench`, `harbor` and `river` rubrics remain runnable through the same scripts with their existing output formats. Select them with `--review-rubric`; severity output applies only to `custom_severity`.

Definitions and examples are in [rubric.toml](rubric.toml). The review prompt is [custom_severity.txt](../prompt_templates/modified/custom_severity.txt). The runner stages both files, and the judge writes `/app/verdicts.json` in the agent environment.

Use these options with the normal contract preparation and execution workflow:

```bash
python validation/run.py /path/to/tasks --stages 5 \
  --review-rubric custom_severity --review-format severity \
  --review-agent codex --review-model gpt-6.1-sol \
  --review-reasoning-effort low --review-evidence image \
  --out /path/to/results --prepare-contract /path/to/severity-contract.json
```

`--review-format severity` is optional for this rubric and is resolved automatically. `--review-criteria` can restrict the review to exact rubric IDs. Image-backed CLI/local judges, explicit file-only reviews, and Terminus-2 task-environment reviews use the same severity validator and repair logic. File-only reviews do not provide execution evidence from the actual task environment.

Each result records the task interpretation, verifier mode, and one entry per selected rubric with severity, subcategory, finding, evidence and any assessment limitation. Instruction/verifier alignment requires exactly one subcategory for `minor` or `major`, and `null` otherwise. Anti-cheating robustness allows only `none`, `major` or `not_assessed`; its `major` findings require demonstrated execution evidence as specified in the rubric. The schema checks evidence fields, but does not independently establish that the reported evidence is correct.

The full `results` object is retained in stage reports and compact `reviews.json` exports. The runner also derives compatibility checks for existing consumers:

| Severity | Compatibility outcome | Task status when there are no execution/output errors |
| --- | --- | --- |
| `none` | `pass` | `passed` if every selected rubric is `none` |
| `minor`, `major` | `fail` | `findings` |
| `not_assessed` | `incomplete` | `findings`, requiring inspection rather than a confirmed defect |

The compatibility `outcome` belongs to each rubric's entry in `checks`; the overall task `status` summarizes its reviews. `findings` means the task needs attention because a defect was identified or an assessment could not be completed. It does not always indicate a confirmed defect. `incomplete` is a per-rubric outcome, not an overall task status. Execution failures or missing/invalid output that remains unresolved after repairs produce an `error` status.

These compatibility labels support existing pipeline consumers without replacing the original severity judgments. Read `results` for the actual severity, evidence, subcategory and assessment limitations; agreement analysis also uses those severity judgments directly.

`not_assessed` is a valid recorded assessment and does not trigger a repair. Missing or invalid entries are retried without overwriting accepted results. Use `--review-runs N` for independent repetitions; repair rounds are part of their original review. Matching checkpoints can resume completed and partial reviews.

The shared agreement analysis compares actual severity labels and subcategories, preserving the distinction between `minor` and `major`. It also reports severity counts and per-rubric subcategory agreement:

```bash
python -m validation.rubrics.analysis /path/to/stage-5-report.json \
  --out /path/to/agreement.log --json-out /path/to/agreement.json
```
