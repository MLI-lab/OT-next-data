# Stage 4: oracle

Check that an existing source-backed solution actually ran and that its
verifier returned reward 1. A skipped oracle, crashed grader, or missing
reward is not a pass. Do not invent a new ground-truth solution or weaken
tests. Record confirmed repairs below.

## Confirmed repairs

### FACET-Terminal/FACET-Terminal-Tasks-6k — wave 0, repair 1

- **Failure signature:** Oracle run has no reward because the environment never starts: same tmux/libutempter or 'unshare -r apptainer exec --overlay' bridge error as stage 3, for exactly the tasks that errored at stage 3.
- **Cause:** Downstream of the stage 3 build failure. The solutions and verifiers themselves were not at fault.
- **General rule:** When stage 4 failures coincide exactly with stage 3 build errors, repair the build and rerun the oracle unchanged. Do not edit the solution or tests. Count a pass only when the rerun shows tests executed and reward 1.
- **Match predicate:** The stage 4 finding is an environment-start exception with no verifier output, and the same task errored at stage 3.
- **Limits and counterexamples:** Five tasks reached reward 1 with 14 to 50 tests passed after the stage 3 repair. task_004102 still has no reward (EnvironmentStartTimeoutError after 600 s) and is not a pass. task_003701, the only task whose solve.sh was rewritten, has not been run.
- **Same retained pilot tasks:** stage 4 passed 4 → 9.
- **Full-source effect:** 2674 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-00`.

### open-thoughts/TaskTrove/pymethods2test — wave 0, repair 1

- **Failure signature:** Stage 4 is skipped for every task with 'no solution/solve.sh for oracle validation'. The task package has solution/solution.py but no solve.sh, and the tests begin with `sys.path.insert(0, '/app'); from solution import *`.
- **Cause:** The source ships the reference solution as a plain Python file and not as the solve.sh entry point that Harbor's oracle agent executes, so the oracle never ran. The solutions themselves were not shown to be at fault.
- **General rule:** Add solution/solve.sh (mode 0755) that only copies the existing /solution/solution.py to the deliverable path named in the instruction (/app/solution.py, with /app the Dockerfile WORKDIR). Leave solution.py, tests and instruction byte-identical. Count a pass only when the rerun shows tests executed and reward 1.
- **Match predicate:** The task has solution/solution.py and no solution/solve.sh, the instruction names solution.py in /app as the deliverable, and the tests import `solution` from /app. The patcher raises if solve.sh exists or solution.py is missing.
- **Limits and counterexamples:** Confirmed on 10 pilot tasks only (stage 4 skipped 10 to passed 10, reward 1, 4 to 8 tests passed each). The wrapper was added to all 4990 tasks and 4980 were not run. Nineteen tasks whose solution imports numpy (6 also in tests) are expected to fail because the image installs only pytest: 0457, 0469, 0732, 0907, 2208, 2331, 2354, 2680, 2995, 3005, 3148, 3412, 3525, 3701, 3772, 4306, 4434, 4752, 4921; none was run and no numpy version is evidenced. A source solution that fails its own tests is a task defect to discard with evidence, not a reason to write a new solution. The wrapper does not fix stage 5, which stays failed 10 of 10 on a collection error ('1 error during collection', zero tests executed). The same patch also pinned pytest==9.1.1 in the Dockerfile and test.sh, so the stage 4 run was on the rebuilt pinned image.
- **Same retained pilot tasks:** stage 4 passed 0 → 10.
- **Full-source effect:** 4990 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/pymethods2test/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/pymethods2test/loop/wave-10/iteration-00`.
