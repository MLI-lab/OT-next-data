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

## Results

Failure and warning rates are calculated independently. A record may have both a failure and a warning; criterion breakdown counts are non-exclusive.

| Dataset | Records | Records with ≥1 failure | Failure rate | Records with ≥1 warning | Warning rate |
|---|---:|---:|---:|---:|---:|
| CalibForge v1 | 5,431 | 133 | 2.45% | 1,301 | 23.96% |
| CalibForge v2 (validated) | 84 | 5 | 5.95% | 22 | 26.19% |
| ScaleSWE v1 | 17,202 | 175 | 1.02% | 621 | 3.61% |
| ScaleSWE v2 (validated) | 292 | 1 | 0.34% | 5 | 1.71% |
| Seta v1 | 3,153 | 3,137 | 99.49% | 483 | 15.32% |

Failure breakdown:

| Dataset | Failure criteria |
|---|---|
| CalibForge v1 | `do_not_modify_enforced` 49; `no_extraneous_files` 31; `verifier_execution_isolation` 21; `task_security` 20; `python_missing_imports` 10; `artifact_efficiency` 2; `functional_verification` 1 |
| CalibForge v2 (validated) | `task_security` 3; `verifier_execution_isolation` 1; `do_not_modify_enforced` 1 |
| ScaleSWE v1 | `task_security` 111; `python_missing_imports` 35; `do_not_modify_enforced` 30 |
| ScaleSWE v2 (validated) | `do_not_modify_enforced` 1 |
| Seta v1 | `task_security` 3,136; `verifier_execution_isolation` 135; `do_not_modify_enforced` 24; `python_missing_imports` 10; `solution_quality` 2; `artifact_efficiency` 2 |

For this table, warnings include only `ctrf_reporting`, `instruction_concision`, and `deterministic_reproducible`. `task_name`, `task_readme`, `separate_verifier_configured`, and `typos` are excluded.

Warning breakdown:

| Dataset | Warning criteria |
|---|---|
| CalibForge v1 | `deterministic_reproducible` 900; `instruction_concision` 420; `ctrf_reporting` 0 |
| CalibForge v2 (validated) | `deterministic_reproducible` 18; `instruction_concision` 5; `ctrf_reporting` 0 |
| ScaleSWE v1 | `instruction_concision` 621; `deterministic_reproducible` 0; `ctrf_reporting` 0 |
| ScaleSWE v2 (validated) | `instruction_concision` 5; `deterministic_reproducible` 0; `ctrf_reporting` 0 |
| Seta v1 | `instruction_concision` 482; `deterministic_reproducible` 4; `ctrf_reporting` 0 |

`typos` is removed since the characteristic of current implementation is heavily tied to Terminal Bench.

## Rubric Oracle Comparison in Terminal Bench

The Terminal-Bench PR experiment used two related but different labels:

1. **PR/task outcome label:** whether the task-producing PR was merged. This feature proxies the historical pass of the task.
2. **Criteria oracle:** criterion rows extracted from GitHub Actions bot comments and human-review conversation. A historical evaluation is pass when all available criterions pass and fail when at least one row fails.

The local static-runner result is not the same label as either one. It is a model-free package screen. In particular, the runner can abstain or pass a screen even when a historical oracle evaluation failed, because many semantic criteria are not statically decidable.

### Sample identity

The `Terminal-Bench PR tasks` row above contains **66 unique local task packages**. All 66 had cached oracle data, but the conversation contains **82 historical rubric evaluations** because some tasks were evaluated multiple times across revisions/comments. Those 82 evaluations generated **2,530 criterion-level comparisons**. Therefore, the 66-task table and the 82-evaluation oracle metrics are related but are not a one-row-per-observation dataset and must not be conflated.

### Static result versus oracle

Across the 82 historical evaluations, the oracle label was 57 pass and 25 fail. The strict static interpretation could not make a complete whole-task decision for any evaluation because the runner does not statically decide every criterion. Under the optimistic screening interpretation—no detected static failure means screen-pass—the matrix was:

| Interpretation | Pass/pass | Pass/fail | Fail/pass | Fail/fail | Pass precision | Pass recall | Pass F1 | Fail recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No static failure = screen-pass | 57 | 25 | 0 | 0 | 0.695 | 1.000 | 0.820 | 0.000 |

Thus the static runner did not provide a useful whole-task failure filter on this historical PR sample: it allowed all 25 oracle-fail evaluations through. This is expected from a model-free partial checker, not evidence that the merge label or oracle was skipped.

### Criterion-level agreement

There were 2,530 criterion-level comparisons and 1,150 exact status matches. The useful positive agreement is concentrated in criteria with explicit local predicates. Representative pass-F1 results are:

| Criterion | Oracle evaluations | Pass F1 | Interpretation |
|---|---:|---:|---|
| `deterministic_reproducible` | 82 | 1.000 | Static pinning check agrees with all observed oracle passes; no oracle failures were present. |
| `functional_verification` | 82 | 1.000 | Static positive evidence matches observed passes; it does not establish failures. |
| `outcome_verified` | 82 | 1.000 | Strong agreement on observed passes; no oracle failures were present. |
| `task_security` | 82 | 1.000 | No oracle failures were present; the pattern scan remains partial semantic evidence. |
| `environment_hygiene` | 82 | 0.988 | A few static false positives; no captured oracle failures. |
| `instruction_concision` | 82 | 0.988 | Relative-path proxy agrees with most observed passes; it is now a warning. |
| `no_extraneous_files` | 82 | 0.969 | Filename-based scan has false positives; no captured oracle failures. |
| `solution_quality` | 82 | 0.994 | Narrow hardcoding scan agrees with observed passes; semantic quality remains unproved. |
| `verifier_execution_isolation` | 16 | 0.897 | Useful path proxy, but weaker positive agreement. |


## Trajectory comparison for v2

Trajectory success means the task verifier returned a positive reward; it does not mean the pass of all static checks. All five CalibForge v2 hard-failure tasks had successful trajectories with `reward=1.0` and `verifier_reward=1.0`. Imperfect verification coverage or mild failure can happen within this.

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