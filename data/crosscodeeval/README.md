# CrossCodeEval

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
| `solution/solve.sh` | **Added** — the oracle that writes the reference, so a run can prove the sandbox scores a correct answer. |

Tasks are **dropped** when no retrieval variant makes every identifier of the
graded reference knowable, or when the graded reference has no identifier at all.
1,053 of 7,763 were dropped this way. Task IDs keep their original numbers, with
gaps where tasks were dropped.

## Where the tests are

Each task carries its own verifier (`tests/cceval_verifier.py`) — that is what
grades an agent in production. The repo-level tests are elsewhere by design:

- `tests/test_crosscodeeval_patch.py` — 34 pytest cases on the patch rules
  (truncation, comment handling per language, the solvability verdicts, the
  report contents).
- `verify/check_reward.py` — grades gold and nonsense with those per-task
  verifiers, over the whole dataset.
- `patch.py --review reviews/` — the filter's own audit: per-task verdicts and a
  sample of dropped tasks, written by the same pass that does the filtering.

## Files here

- `patch.py` — the whole pipeline for this dataset in one module: verifier,
  context selection, solvability rules and their audit (`--review`), oracle. One
  file on purpose: a filter that disagrees with the grader silently keeps
  unsolvable tasks, and a separate audit script can drift from the filter.
- `rewards.py` — how an answer is graded locally and which nonsense answers the
  reward checks should try. This is the plug-in `verify/check_reward*.py` load.
- `published_tasktrove_pr3.task_hashes.json` — a hash of every task's files as
  published in [TaskTrove PR #3](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/3)
  (csharp-v5, java-v4, python-v3, typescript-v3, patched from TaskTrove revision
  96567362), so `verify/check_reproducible.py` can prove the patcher still
  produces exactly those tasks. A later publication gets its own file.
- `strict_subset_1000.json` — which of the 1,000 evaluated tasks survive the
  strict reading of the "parts" rule (750 do), with the name that fails for each
  of the others. Reporting only; nothing is filtered by it.
