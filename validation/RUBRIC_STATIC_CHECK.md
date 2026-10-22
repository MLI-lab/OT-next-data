# Model-free Static Task Checker

`run_static_rubric.py` is a model-free first-pass checker. It is based on features that does not rely on LLM judges or human reviews, or execute agent trajectories to invetigate. It combines TOML parsing, filesystem/Dockerfile inspection, AST inspection, regular expressions, and repository shell checks.

Each check returns `pass`, `fail`, `not_evidenced`, `not_applicable`, or `error`. `static_failures` contains only failure-class criteria; `static_warnings` contains only warning-class criteria. The raw per-criterion result remains in `criteria`.

The original checklist is derived from Terminal-Bench.

## Checklist

The table is ordered with failure-class criteria first and warning-class criteria last. `Full` means the pass establishes the checked static predicate. `Partial` means it is only a proxy or covers one failure mode; it does not prove the complete natural-language rubric.

### Failure criteria

| Criterion | Class | Check and failure condition | Passing evidence |
|---|---|---|---|
| `task_toml_schema` | failure | Parse `task.toml` with `tomllib`; fail if parsing fails. | Partial |
| `no_extraneous_files` | failure | Scan outside `tests/` for obvious `debug*`, `unused*`, `old_*`, `.bak`, `.tmp` files. | Partial |
| `resource_configuration` | failure | Run `check-resource-sizes.sh`; fail on a repository-check failure. | Partial |
| `environment_hygiene` | failure | Inspect Docker `COPY`/`ADD`; fail when a local source is absent. | Partial |
| `verifier_execution_isolation` | failure | Run `check-test-file-references.sh`; fail on invalid task-file references. | Partial |
| `functional_verification` | failure | Inspect `tests/` for executable files, assertions, and behavior operations; fail for no executable verifier or source/keyword-only testing. | Partial |
| `python_missing_imports` | failure | AST-scan selected aliases (`np`, `pd`, `plt`, `torch`, `tf`, `yaml`, `requests`, `px`) in `tests/` and `solution/`; fail only for an unbound alias used as an attribute. Imports and local bindings are excluded. | Partial |
| `artifact_efficiency` | failure | Fail when a non-`.git` file is over 100 MB. | Partial |
| `task_security` | failure | Fail on suspicious text combinations such as curl/wget near credentials, `docker.sock`, or dangerous root deletion. | Partial |
| `outcome_verified` | failure | Fail when verifier files contain no behavior-execution pattern. | Partial |
| `solution_quality` | failure | Fail on an obvious constant-only solution without computation or I/O. | Partial |
| `do_not_modify_enforced` | failure | Fail when instructions name a concrete protected path but verifier text has no corresponding reference. | Partial |
| `binary_reward` | failure | Fail on non-0/1 reward literals or explicit partial-credit language. | Partial |

### Warning criteria

| Criterion | Class | Check and warning condition | Passing evidence |
|---|---|---|---|
| `ctrf_reporting` | warning | Warn when no CTRF-like file or path reference is present. | Partial |
| `typos` | warning | Warn when the task folder is not kebab-case or Python filenames contain spaces/uppercase. | Full |
| `instruction_concision` | warning | Warn when `check-task-absolute-path.sh` finds relative paths. This checks portability, not prose concision. | Partial |
| `deterministic_reproducible` | warning | Warn when `check-pip-pinning.sh` finds unpinned pip/uv installs. This checks dependency pinning, not services, hardware, or randomness. | Partial |

The following Terminal-Bench profile checks are intentionally omitted from the Results warning-rate calculation because they are not meaningful cross-dataset quality signals: `task_name`, `task_readme`, and `separate_verifier_configured`. They may still be retained as compatibility metadata by the runner when requested.

## Execution

The following commands use `ot-next-data/` as the working directory:

```sh
cd OT-next-data
python3 validation/checks/run_static_rubric.py \
  --task PATH/TO/task-directory
```

```sh
cd OT-next-data
PYTHONPATH=/tmp/ot_next_pyarrow python3 \
  validation/checks/check_validated_parquet.py \
  --limit 0 --archive-all --workers 16
```

The parquet adapter writes one JSONL record per bundle to `validated_task/<dataset>_static_check.json`.