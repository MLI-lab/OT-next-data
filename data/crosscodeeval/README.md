# CrossCodeEval

The full patcher automatically writes `<output>.archive.parquet` and
`<output>.manifest.json`. Dropped tasks retain their original payloads and explicit
drop labels; retained changes receive labels for the files actually changed.
`--pin-only` carries this comparison forward when its input has a new-format
manifest and the recorded original Parquet is still available.
See [patch reporting](../INVENTORY.md#reporting-patches-in-the-datasource-pr).

Cross-file code completion: the agent sees a file cut at a cursor and must write
the next statement, which depends on code in *other* files of the repository.
Upstream benchmark: amazon-science/cceval, pinned at `40c68d2b`. TaskTrove
packaged it without that cross-file context and with a verifier that paid full
reward for a prefix match, which is what `patch.py` repairs.

## What the patch changes inside a task

| File | Change |
| --- | --- |
| `setup_files/context/*` | **Added.** The benchmark's own retrieved excerpts, minus empty ones, ones from the target file and ones containing the answer. Needs a Harbor that uploads `setup_files/` before the agent runs. |
| `tests/cceval_verifier.py` | **Added.** The benchmark's scorer, python3 standard library only. Reward = exact match of the first statement after comment removal and per-line stripping; also reports edit similarity and identifier precision/recall/F1. |
| `tests/test.sh` | **Replaced** to call that verifier. |
| `tests/prompt.txt` | **Added** for python tasks (the code before the cursor, needed for python truncation). |
| `instruction.md` | The stale sentence about the old scoring and its partial credit is **removed**; a note that context files exist is added. |
| `environment/Dockerfile` | The pip install is **pinned** to the versions under which the tasks were validated: `pytest==9.1.1`, `pytest-timeout==2.4.0`. |
| `solution/solve.sh` | **Added** — the oracle that writes the reference, so a run can prove the sandbox scores a correct answer. |

Tasks are **dropped** when no retrieval variant makes every identifier of the
graded reference knowable, or when the graded reference has no identifier at all.
1,053 of 7,763 were dropped this way. Task IDs keep their original numbers, with
gaps where tasks were dropped.

## Where the tests are

Each task carries its own verifier (`tests/cceval_verifier.py`) — that is what
grades an agent in production. The repo-level tests are elsewhere by design:

- `tests/test_crosscodeeval_patch.py` — pytest cases on the patch rules
  (truncation, comment handling per language, the solvability verdicts, the
  report contents).
- `tests/test_dataset_reward_plugins.py` — grades the reference and wrong answers with
  the task verifier, on one built task.
- `review/dropped.jsonl` and `review/sample.md`, written beside the output on
  every patcher run — the filter's own audit: every dropped task with its reason,
  and a sample of 40 for hand review, from the same pass that does the filtering.

## Files here

- `patch.py` — the whole pipeline for this dataset in one module: verifier,
  context selection, solvability rules and their audit (`review/`), oracle. One
  file on purpose: a filter that disagrees with the grader silently keeps
  unsolvable tasks, and a separate audit script can drift from the filter.
- `patch.py rewards TASK` — how an answer is graded locally and which wrong answers to try.
  Used by `tests/test_dataset_reward_plugins.py`.

Reproducibility is checked against the published dataset itself, by a hash over
each task's files; no hash file is stored here:
`python validation/checks/dataset_checks.py reproduce --parquet NEW --reference PUBLISHED`.
The tasks of [TaskTrove PR #3](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/3)
(csharp-v5, java-v4, python-v3, typescript-v3, patched from TaskTrove revision
96567362) predate the pip pin of 2026-09-29, so the current patcher's output
differs from them in every task's Dockerfile.

## Validation status and plan

Validation runs through the stages in [`validation/`](../../validation/README.md); results
are proposed as pull requests to [FWeindel/validated-tasks](https://huggingface.co/datasets/FWeindel/validated-tasks).

Done or running:

- **Stages 1, 3, 4, 5** (static checks, build, oracle, no-answer) on all 6,710 tasks with
  the pip pin: job 917298, waiting for the Helma maintenance of 2026-09-30 to end. The
  checks `separate-verifier` and `test-sh-sanity` are excluded with recorded reasons
  (shared verifier by design). The run before it (916252) passed every task it reached
  and stopped on a node problem that is fixed since.
- **Teacher runs on the earlier 1,000-task selection** (2026-09-17, before the pin):
  Qwen3-Coder-30B-A3B-Instruct 16 attempts per task, pass@1 7.0%, pass@4 14.3%, pass@16
  22.3%; Qwen3.5-122B-A10B one attempt, pass@1 20.0%. Only the per-task reward summaries
  survive (`~/crosscodeeval-pilot-archives/hnvme-run-summaries-2026-09-22/`); the
  trajectories were on node-local and /hnvme storage and are gone.

To do, in order:

1. Merge the stage 1/3/4/5 pull request once the run is complete.
2. **Stage 6 and 7**, agent trials with run record, pass@k and trace metrics, on the kept
   tasks: Qwen3-Coder-30B-A3B-Instruct (8 attempts) and Qwen3.5-122B-A10B (1 attempt) again,
   and the SFT'd Snowball model once it is added to `config/models.py`. Keep the
   trajectories this time and publish them to Hugging Face next to the tasks.
3. **Stage 8**, LLM trajectory analysis, on a sample of those trials.
4. **Stage 2**, LLM rubric review, on a sample, to see what the reviewer objects to.
