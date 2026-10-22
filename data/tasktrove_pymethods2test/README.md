# PyMethods2Test patch

Source: `open-thoughts/TaskTrove`, subset
`DCAgent__exp_rpt_pymethods2test-large-v2`, revision
`946884702046be1a7dcea2638186ad6b0d2ea103`. The patch retains all 4,990 task IDs.

## Per-stage breakdown

- **Build and setup:** the existing patch pins pytest and migrates the shared
  verifier configuration. Task `pymethods2test-3412` now installs
  `numpy==2.2.6` through `tests/setup.sh`; its supplied reference imports NumPy,
  but the upstream image did not install it. This dependency repair does not
  change the shared Dockerfile. The reference import repair for task `1059`,
  described below, is packaged with the same patch.
- **Reference solution (reward 1):** task `pymethods2test-1059` now imports
  `cmp_to_key` from `functools`. Previously all seven tests failed with
  `NameError`. With the import, all seven pass locally. With NumPy installed,
  all six unchanged tests for `pymethods2test-3412` pass locally. The existing
  `solution/solve.sh` entry point copies each supplied reference to
  `/app/solution.py`.
- **No-op baseline (reward 0):** no tests or validator rules are changed.
  Without a submission, both suites report a missing `solution` module. A
  passing reference run lets the existing validator recognize that collection
  error as the expected no-op failure. Both repaired candidates were accepted
  locally with reference reward 1 and no-op reward 0.
- **Rule-based checks:** these two repairs do not change instructions, test
  cases, expected outputs or reward logic. Full cluster revalidation is pending.

Local diagnosis used Python 3.10.21, pytest 9.0.2 and NumPy 2.2.6. These results
are not a completed CLAIX run. The repository patch retains its existing pytest
9.1.1 pin. Per-task repair explanations are also written to the patch manifest.

## Retry behavior

The inspected CLAIX run allowed three outcome retries after the initial attempt.
Only stages without a usable result are retried; a passing build is not repeated
just because the no-op stage needs another attempt. Fresh trial environments and
setup may still add overhead. This is not five full image builds per task.
