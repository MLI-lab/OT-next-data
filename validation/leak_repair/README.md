# Leak repair loop

An agent writes one repair function for the tasks that a check flagged. Currently the following checks are
implemented: `agent_editable_test`, `verifier_tamper`, `repository_history`, `reference_leaks`, `no-cheat-dir` and
`dockerfile-references`.

To add a check, write `prompts/checks/<check>.txt` which should specify what the check flags, what the evidence contains, and what counts as a true positive, a false positive and not fixable. Then add the check's name to `CHECKS` in `tool.py`.

## In the pipeline

The stored repair functions (`data/<dataset>/leak_repairs/<check>.py`) take effect right after the stage in which
their check runs: stage 0 for `no-cheat-dir`, `dockerfile-references` and `agent_editable_test`, stage 1 for
`repository_history`, stage 3 for `reference_leaks` and `verifier_tamper`. The runner replaces each repaired task in a
working copy of the run's tasks, so the following stages check the repaired version. The contract stays bound to the
original tasks; the stage report records every repair (original hash, repaired hash, checks, evidence), and the later
stages accept exactly these versions. In `results.parquet`, a task's row moves to the repaired version after the stage,
so its earlier results are kept. A fixed task whose repaired content the session validated, and a false positive, get
`pass` with `repaired: true`; a not fixable or unrecognised task keeps its status with `repaired: false`. A repair
that changes the task's image is applied only when that image is prepared in the allocation's cache.

The shard merge and publishing replay the recorded repairs on the original tasks, so the published task is the
version the later stages checked; publishing refuses if a stored function no longer gives the recorded result.

Stage 4 also writes `OUT/repaired-tasks.parquet` when it finishes, including when no task files changed.
This contains the full finalized cohort with `path`, `task_binary` (the task archive), and `content_sha256`.
Use it as the task input alongside `OUT/results.parquet` for downstream work. The original task input is
preserved. Changed archives are reconstructed from recorded repairs, and every exported content hash must
match the corresponding final result. A missing task or a repair that no longer reproduces its recorded
version stops finalization; it cannot silently export the original bytes under a repaired result.
`stage-4.json` records the export path and verification under `task_export`. This local export does not
publish a new remote dataset revision.

To write or extend the functions, run `repair` after a validation run and before the judge, inside an allocation with
the Apptainer bridge. It applies the stored functions first and starts a session only for checks with tasks they do
not settle (no function yet, not recognised, or not validated):

```bash
python -m validation.leak_repair.launch repair RUN/results.parquet --tasks RUN_TASKS --dataset-dir data/<dataset> \
    --dataset NAME --out OUT --concurrency N [--cache RUNTIME_CACHE] [--leak-repair-model claude-sonnet-5-5] \
    [--timeout-minutes 300]
```

The checks are handled one after another, in the order the pipeline runs them (`CHECKS` in `tool.py`); each session gets the tasks as the
earlier checks' repairs left them, and the functions are applied in the same order. `--concurrency` is the same as
for the validation run: how many tasks the tool and the validation service check in parallel. It stores each
session's function as `data/<dataset>/leak_repairs/<check>.py` and the fixed tasks it validated in `<check>.json`;
the next run applies them.

## A single session

The input of a session is a group: all tasks of one dataset that one check flagged, in one folder (`repair` builds
it from `results.parquet`):

```
GROUP/
  group.json              {"dataset": ..., "check": ..., "tasks": [...]}
  tasks/<task>/           each flagged task in Harbor format
  evidence/<task>.json    why the check flagged that task
```

```bash
python -m validation.leak_repair.launch runtimes GROUP --cache RUNTIME_CACHE   # writes GROUP/runtimes/<id>.json
python -m validation.leak_repair.launch stage GROUP --out OUT                  # builds the sandbox in OUT/task
python -m validation.leak_repair.launch session OUT                            # inside an allocation with the bridge
```

The result is `OUT/work`: the agent's `leak_repairs.py` and its verdicts in `classification.jsonl`.

## How the repair agent runs

The session runs like the stage-5 judge: Claude Code without web tools, in a Harbor task inside an allocation with
the Apptainer bridge, for the judge session length. The sandbox image is the same for every group (Ubuntu 24.04 with
Python, Harbor and Claude Code); prepare it once in an image-only job on the target cluster. Inside the sandbox:

| Path | Content |
| --- | --- |
| `/app/group` | a copy of the group, plus `regression_checks.txt` |
| `/app/code` | a copy of the code the checks need (`validation`, `config`, `data`, `harbor_patches`) |
| `/app/work` | the agent's output; collected into `OUT/work` at the end |
| `/app/check-repairs` | the tool (`tool.py`) |

The originals are never changed. The tool runs the agent's function and the fast checks inside the sandbox. Stages
1-3 (the container checks) run on every fixed task, whatever check is repaired, in a validation service next to the
agent in the same allocation; the tool sends it the fixed tasks over the network. A fixed task must still pass every
check it passed before (the originals are validated once as the baseline). If a repair changes a task's image (`environment/`), the service builds that image
first.

Not tested yet: a real session and container checks in an allocation. The no-cheat-dir evidence (the files that
refer to `cheat/`) is not yet taken from the repair record when `repair` builds a group.
