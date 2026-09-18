---
name: verify-dataset
description: Prove a patched dataset before spending GPU hours - verifier discrimination, solvability, oracle parity, isolation.
---

# Verifying a dataset

A reward number is worthless until these four hold. Run them in order; each is
cheap compared to a 1,000-task run.

## 1. Tests

```bash
source env.sh && pytest tests -q          # expect: 43 passed
```
Check the output for `failed`, not only for `passed` — `grep ' passed'` matches
`1 failed, 42 passed`, which is how a stale test once went unnoticed for a day.

## 2. Reward sanity — every task, not a sample

Gold must score 1 and nonsense must score 0, graded by each task's own verifier.
Two levels, and both matter:

```bash
tar -xzf $PILOT_ROOT/tasks/<selection>.tar.gz -C $DIR
python verify/check_reward.py $DIR                  # this machine, ~1 min for 1,000 tasks
python verify/check_reward_harbor.py $DIR $OUT      # inside a Slurm job: build + sandbox + test.sh
```

The local one catches verifier bugs; the Harbor one catches a broken image, a
missing python in the container, or a reward that never reaches
`/logs/verifier/reward.json`.

Variants graded: gold, re-indented gold (must still be 1 — whitespace must not
matter), empty, garbage, lone `}` / `;` / `{`, `return null;`, and the gold with
one identifier renamed (the near miss that catches a prefix-matching verifier).
Anything other than "gold N/N, every nonsense 0/N" is a defect in the verifier,
not in the tasks.

## 3. Solvability

```bash
python verify/check_solvability.py <patched>.parquet --out reviews/
```
Re-derives every verdict from the shipped parquets and writes a sample for
hand review. Read the sample: the rules are heuristics, and the lenient "parts"
rule (camelCase parts appearing anywhere) passes names the agent cannot
actually infer. The strict variant is in
`data/crosscodeeval/strict_subset_1000.json`; report scores on both.

## 4. Oracle parity and isolation, on the cluster

- Oracle stage of a run (`PILOT_ORACLE_CHECK=1`, the default) executes every
  `solution/solve.sh` through the real harness: expect 1,000/1,000 reward 1.
- `python verify/check_isolation.py` — two containers at once: separate cgroups,
  no shared temp files, an OOM in one contained, loopback not shared.

## Reporting

```bash
python verify/pass_at_k.py <run dirs...> --k 1 4 16
```
Report all tasks *and* the strict subset, and state the timeout rate: timeouts
count as failures, so a run with a broken serving path looks like a weak model.
A trajectory review beats a guess — when a score looks wrong, read ten failed
trajectories per language before touching the verifier.
