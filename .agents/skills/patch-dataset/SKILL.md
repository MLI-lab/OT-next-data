---
name: patch-dataset
description: Repair an agent-task dataset (context, verifier, oracle, solvability filter) and produce parquets ready for review.
---

# Patching a task dataset

## Rules that hold for every dataset here

1. **One patcher per dataset, one file.** The verifier, the solvability rules,
   the context selection and the audit of all three stay in the same module: a
   filter that disagrees with the grader silently keeps unsolvable tasks, and a
   separate audit script drifts from the filter it audits.
   Running the patcher on the pinned upstream must reproduce the published
   dataset exactly - record it with `verify/check_reproducible.py --digests`.
2. **The filter asks exactly what the grader asks.** Identifiers are extracted
   from the *graded* text (after truncation and comment removal), by calling
   the verifier's own functions — never by a second regex.
3. **Never modify the upstream checkout.** Patches live here; upstream stays
   diffable.
4. **Report, don't silently drop.** Every removed task ID lands in
   `<output>.report.json` with the reason.

## Run it

```bash
source env.sh
python data/crosscodeeval/patch.py --input <src>.parquet --output <dst>.parquet \
       --archive $CCEVAL_ARCHIVE
```

Per task the patcher: matches it to the pinned upstream record; picks the first
retrieval variant (BM25 → UniXcoder → OpenAI, before-cursor only) that makes
every reference identifier knowable; writes those excerpts to
`setup_files/context/` minus excerpts from the target file or containing the
answer; installs the benchmark scorer as `tests/cceval_verifier.py`; strips the
stale grading promise from `instruction.md`; adds `solution/solve.sh`.

Tasks are dropped when no retrieval makes every name knowable, or when the
graded reference contains no identifier at all.

## After patching, always

```bash
pytest tests -q                                   # 43 passed
python data/crosscodeeval/patch.py ... --review reviews/   # verdicts + dropped sample
```
then the discrimination check from `.agents/skills/verify-dataset/SKILL.md`.
Patched parquets that have not passed both are not ready for a run.

## Adding a new dataset

A dataset folder holds `patch.py` (the whole pipeline, filter and audit
included), `rewards.py` (how an answer is graded locally and which nonsense
answers to try), `published_digests.json` once published, and a short `README.md`
saying what the patch changes inside a task. Everything in `verify/` is dataset-agnostic and finds the
plug-in from the task name prefix. Keep task IDs stable across versions
(gaps where tasks were dropped) so results stay comparable to earlier runs.

## Publishing

Repaired sets go under the next version label in the dataset repo
(`…-python-v2` → `…-python-v3`); the version lives in the directory name only.
Upload with `hf upload <repo> <dir> . --repo-type dataset --create-pr`. Never
add assistant attribution to a commit, PR title or description.
