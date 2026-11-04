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

`validation/run.py --stages` uses the IDs in [stages/layout.py](stages/layout.py). These group checks by the execution environment they need; they differ from the conceptual stages in the [pipeline overview](../README.md#validation-pipeline).

| CLI stage | Execution | Pipeline stages covered |
| --- | --- | --- |
| Preparation outside the runner | `data/<datasource>/patch.py`, image and runtime preparation | 0: task & environment preparation |
| `0` | Static checks | Static parts of 1–5 |
| `1` | Build, setup, declared environment checks, repository history/timestamps and absolute-path recovery evidence | Runtime parts of 2, 3 and 5 |
| `2` | Reference solution, reference determinism and protected-file mutation checks | 3 and 6 |
| `3` | No-op, no-op determinism, verifier tampering and reference-solution leaks | 4, 5 and 7 |
| `5` | LLM task-quality review | 8 |
| `6` | Agent trials; run separately for the target RL model and stronger model | 9 |
| `7`, `8` | Trace metrics and LLM trajectory review | 10 |
| `9`, `10` | Adversarial trials and hacker/fixer loop (optional, untested) | 11 |

There is no CLI stage `4` in the current layout. Do not pass the overview's stage numbers directly to `--stages`.

## Execution requirements

Use the default `training` profile with all its selected checks and no additional exclusions. Preserve the profile's intentional exclusions of upstream benchmark conventions. AI-detection checks (`check_ai_detection.py`) are intentionally excluded by user policy, not an incomplete prerequisite.

Enable absolute-path resolution with `--resolve-path-root /` (or appropriate absolute container search roots, repeated as needed) and collect path-recovery evidence in CLI stage 1. Keep reference-solution leak checking enabled (`--reference-leaks`, enabled by default) when using reference solutions; the current runner records this check in CLI stage 3.

Static findings do not stop runtime checks. Unavailable container preparation blocks dependent reference and no-op runs; missing prerequisites and measurements remain incomplete. Required failures must not be downgraded to warnings. When reusing results, verify the run used these settings and the relevant task revision.

See the [rules reference](rules/README.md) for check behavior, automatic repairs, exclusions and result fields, and [rubrics](rubrics/README.md) for LLM review configuration. Model stages need their model settings; trajectory analysis needs saved trials via `--trials`.
