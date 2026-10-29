# PyMethods2Test v1

Original: **4,990** tasks. Kept: **4,990**. Changed: **4,990**. Archived: **0**.
Docker images: 1 before, 3 now.

## Dataset characteristics

Source: `open-thoughts/TaskTrove`, subset
`DCAgent__exp_rpt_pymethods2test-large-v2`, revision
`946884702046be1a7dcea2638186ad6b0d2ea103`. Tasks pair Python programming
instructions with supplied reference solutions and pytest suites.

## Per-stage breakdown

**1. Build and setup: 4,990 changed, 0 archived.**
Changed 4,990 tasks to pin pytest, which was installed unpinned, and to use the
supported shared verifier environment setting. Changed 19 tasks to install
NumPy, which their references import but their images never installed.

**2. Reference solution (reward 1): 4,990 changed, 0 archived.**
Changed 4,990 tasks to add an entry point that runs the supplied reference.
Changed 38 references to add a missing standard-library import, such as
`from collections import deque`; function bodies and tests are unchanged.
All 4,990 references scored 1.

**3. No-op baseline (reward 0): 0 changed, 0 archived.**
All 4,990 tasks scored 0 without a solution. Manually approved 176 false
positives from the no-op checker, which could not confirm verifier execution;
the corresponding reference solutions passed. In each, the tests stopped only
because the missing solution module could not be imported.

**4. Rule-based checks: 4,990 changed, 0 archived.**
Changed all 4,990 tasks so their instruction ends with the time limit and the
request not to cheat. Flagged 88 tasks for relative paths. Automatic repair cleared warnings for 15
tasks. 73 tasks still have warnings. Inspected 50: all were fine, naming
`solution.py` in the `/app` working directory where the verifier reads it. All other
rule-based checks passed. Git-history and timestamp cleanup do not apply: the
tasks contain no repository.

The reference-solution leak check ran on 177 tasks, all passing; it did not run
on the remaining 4,813.

## Per-task resource requirements

| CPU limit | Memory limit | Tasks |
| --- | --- | ---: |
| Unspecified | Unspecified | 4,990 |

All tasks use a 900 s agent timeout and a 720 s verifier timeout.

## Runtime and validation time

Validation used Harbor 0.8.1 with the Apptainer backend and host Python 3.12.14.
Tasks run Python 3.10 with pytest 9.1.1, or 9.0.2 for the 55 repaired tasks.
CLAIX jobs used 16 CPUs, 64 GiB and four concurrent tasks; one repaired task ran
on Helma with 8 CPUs, 15 GiB and one task at a time. Includes the full
validation run and reruns of repaired tasks.

| Stage | Measured tasks | Summed task time | Mean per task | Median per task |
| --- | --- | --- | --- | --- |
| Build and setup (container start and setup) | 4,990 kept | 133.6 h | 96.4 s | 6.2 s |
| Reference solution | 4,990 kept | 148.5 h | 107.2 s | 23.5 s |
| No-op baseline | 4,990 kept | 121.6 h | 87.7 s | 23.1 s |
| Rule-based checks | 4,990 kept | 0.4 h | 0.3 s | 0.3 s |
