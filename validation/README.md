# Task validation

For the full preparation and validation pipeline, see the [main README](../README.md#validation-pipeline). This page covers execution; individual checks are documented in the [rules reference](rules/README.md).

## Quickstart

### 1. Prepare a contract

A contract records the tasks, stages, settings and code used for a run. Generate it with the command below; do not write or edit it manually.

Before submitting validation, prepare missing images in a separate image-only job and verify all required entries in the target cluster’s cache. A cache on another cluster is insufficient. Keep `OT_REQUIRE_PREBUILT_IMAGES=1` set so a cache miss cannot trigger a build during validation.

```bash
export OT_REQUIRE_PREBUILT_IMAGES=1

python validation/run.py /path/to/tasks \
  --stages 0,1,2,3 \
  --resolve-path-root / \
  --submit helma --partition cpu --cpus 48 \
  --concurrency 4 --time 02:00:00 \
  --out "$OT_WORKSPACE/validation" \
  --prepare-contract "$OT_WORKSPACE/contracts/validation.json"
```

This prepares the contract without submitting validation. Add `--limit 10` for a small pilot; omit it for the full dataset.

### 2. Run the contract

```bash
python validation/run.py \
  --contract "$OT_WORKSPACE/contracts/validation.json"
```

The example runs the full task-filtering pipeline, stages 0 through 3.

To run a single stage, prepare a separate contract with, for example, `--stages 0`. For several stages, use a comma-separated list. Model stages also need their model settings; stages that analyse saved trajectories need `--trials`.

`--stages all` includes the experimental stages 9 and 10, so use an explicit stage list for normal validation.

Prepare a new contract when tasks, settings or code change. Run `python validation/run.py --help` for all options.

### 3. Publishing results on Hugging Face

See [publishing](publishing/README.md) for how READMEs are generated.

## Execution-stage mapping

`validation/run.py --stages` uses the IDs in [stages/layout.py](stages/layout.py) (layout `environment-first-v4`).
They group checks by the execution environment they need: one container start per task serves the checks of
several pipeline steps. They differ from the step numbers in the [pipeline overview](../README.md#validation-pipeline).

| CLI stage | Execution | Pipeline steps covered |
| --- | --- | --- |
| Preparation outside the runner | `data/<datasource>/patch.py`, image and runtime preparation | 1 |
| `0` | Static checks (task files only) | 1, and the file checks of 2 and 3 |
| `1` | Build, setup, declared environment checks, repository history/timestamps and absolute-path recovery evidence | 2 and 3 |
| `2` | Reference solution, reference determinism and protected-file mutation checks | 2 |
| `3` | No-op, no-op determinism, verifier paths, verifier tampering and reference-solution leaks | 2 and 3 |
| `5` | LLM task-quality review | 4 |
| `6` | Target RL model pass@k with resource calibration | 5 |
| `7` | Strong teacher pass@k with resource recalibration | 6 |
| `8`, `9` | Trace metrics and LLM trajectory review | 7 |
| `10`, `11` | Adversarial trials and hacker/fixer loop (optional, untested) | 8 |

There is no CLI stage `4`. Do not pass the overview's step numbers directly to `--stages`. Records of the
previous layout (`environment-first-v3`) stay valid for stages 0-5, which mean the same in both layouts.

### Stages 6 and 7: pass@k with resource calibration

`--stages 6` runs the target model (`--target-model`, default `Qwen/Qwen3.5-35B-A3B`) and `--stages 7` the
teacher (`--teacher-model`, default `zai-org/GLM-5.3-Flash`) on the tasks the target model left at 0/k. Each
stage serves its own model, so run them as separate submissions, stage 7 after stage 6. Both use
mini-swe-agent and take the tasks without a failed stage 0-3 check, read from `--judge-results` as for
stage 5. `--pass-k` (default 4) sets k.

Resource calibration follows MiMo's sandbox provisioning pass ([code](stages/calibration.py)). A task starts
at max(its `task.toml` values, 2 vCPU / 8 GiB / 20 GiB). After a batch of k attempts, an out-of-memory kill
doubles memory, a full disk doubles disk and a confirmed CPU shortage doubles the vCPUs; the whole batch then
reruns. An infrastructure failure reruns only that attempt. A timeout alone is not a CPU shortage: it needs
busy CPUs or CPU pressure as well (Slurm limits CPUs with a cpuset, so the CFS throttling MiMo used stays
near 0 here). Limits and thresholds are `--calibration-*` options (defaults: at most 4 vCPU, 16384 MB memory,
40960 MB disk and 3 rounds). A task still failing at the limits is `inconclusive` and does not go to the
teacher. The teacher starts from the target model's allocation and calibrates again. pass@k uses only the
final batch. The calibration state is saved after every Harbor job, so rerunning a stage resumes it.

Results in `results.parquet`: `passk_target_outcome` (`solved`, `unsolved` or `inconclusive`),
`passk_target_successes`, `passk_target_valid_attempts` and `passk_target_attempt_results` (every attempt
as JSON), the same for `passk_teacher_*`, and `resources` (upstream values, both models' allocations and
histories, and the final requirement: memory `ceil(peak GiB) + 1`, disk `max(10, ceil(peak GiB) + 2)`, vCPU
as calibrated, never below `task.toml`).

## Execution requirements

Use the default `training` profile with all its selected checks and no additional exclusions. Preserve the profile's intentional exclusions of upstream benchmark conventions. AI-detection checks (`check_ai_detection.py`) are intentionally excluded by user policy, not an incomplete prerequisite.

Enable absolute-path resolution with `--resolve-path-root /` (or appropriate absolute container search roots, repeated as needed) and collect path-recovery evidence in CLI stage 1. Keep reference-solution leak checking enabled (`--reference-leaks`, enabled by default) when using reference solutions; the current runner records this check in CLI stage 3.

Static findings do not stop runtime checks. Unavailable container preparation blocks dependent reference and no-op runs; missing prerequisites and measurements remain incomplete. Required failures must not be downgraded to warnings. When reusing results, verify the run used these settings and the relevant task revision.

See the [rules reference](rules/README.md) for check behavior, automatic repairs, exclusions and result fields, and [rubrics](rubrics/README.md) for LLM review configuration. Model stages need their model settings; trajectory analysis needs saved trials via `--trials`.
