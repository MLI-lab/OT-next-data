# NL2Bash pass@k filtering notes

Inspected on 2026-10-21 using the Qwen38-27B pass@2 pilot and completed full-run
shards submitted on 2026-10-20.

Keeping only tasks solved at least once can remove tasks with broken or overly
restrictive verifiers as a useful side effect. The examples below each scored
0/2 in the inspected run, despite traces showing solutions that satisfy the
written request. They would be excluded by a filter requiring at least one
passing attempt in that run. Final dataset removal has not been verified.

This is an empirical solvability filter, not a verifier-correctness test. It
also removes valid tasks the sampled agent cannot solve, and a defective
verifier can survive if an attempt happens to match its expected output.
Reconcile all runs/models used for selection before declaring a task excluded.

## Reviewed examples

| Task | Agent behavior | Why the zero score is a grading defect |
| --- | --- | --- |
| `task_3157` (pilot) | Ran `mkdir -p alpha beta/gamma`, verified both directories, and captured the successful command's empty output. | The verifier demanded `mkdir: created directory ...` messages from `mkdir -v`. The instructions did not require verbose output and explicitly allowed an empty capture for silent commands. |
| `task_0` (pilot) | Used `find` with case-insensitive matching, regular-file filtering and pruning of `./ignored_directory`; captured the two correct paths on separate lines. | The expected output used NUL separators, which the instructions did not require. The line-based verifier rejected the correct newline-separated paths. |
| `task_3739` (full shard `0001`) | Ran `find . -maxdepth 3 -type f -iname '*.log'` and captured the three matching paths. | The stored expected output was three copies of `printf: %p: invalid conversion specification`, rather than the requested file paths. The grader therefore rejected successful execution for not reproducing errors. |

## Evidence

Run root: `/hnvme/workspace/y500bb12-optiagent/nl2bash-qwen38-pass2-20261020/`.

- Pilot job `1085474_0`: `pilot/array/shards/0000/evidence.tar.gz`.
- Full job `1085473_1`: `full/array/shards/0001/evidence.tar.gz`.
- Each shard's `report/summary.json` records completion and reward metrics.
- Within each archive, compare `tasks/<task>/instruction.md`,
  `tasks/<task>/tests/verifier.py`, and `tasks/<task>/tests/verifier_data.json`
  with `results/stage_6_agent_trials/*/jobs/job/<task>__*/attempts/000/agent/trajectory.json`
  and the corresponding `verifier/test-stdout.txt` and `verifier/reward.txt`.

The pilot's 8 attempts and the first three full shards' 138 attempts completed
without reported model-server, environment or verifier exceptions. The full
shards solved 18/69 tasks at pass@2. Successful execution does not establish
grading correctness, as the examples above demonstrate. These are stage 6/7
observations, not a certification of all datasource validation checks.
