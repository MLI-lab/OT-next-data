# Stage 5: no-op

Check that the verifier ran real tests and returned reward 0 without the
solution. Distinguish a valid zero from a wrapper that reports zero after
setup or collection failed. Preserve a fresh-container NOP check. Record
confirmed repairs below.

## Confirmed repairs

### FACET-Terminal/FACET-Terminal-Tasks-6k — wave 0, repair 1

- **Failure signature:** NOP run has no reward because the environment never starts (same bridge error as stage 3). Separately, task_004801 is rejected with 'verifier executed no tests' on a pytest summary of '24 errors', reward 0.
- **Cause:** The first class is downstream of the stage 3 build failure. The second is the NOP gate reading an errors-only summary as no tests run; the errors come from an autouse fixture asserting a deliverable exists.
- **General rule:** Repair the build and rerun NOP in a fresh container unchanged; accept only reward 0 with tests actually executed. Make no dataset or verifier change for the errors-only class; that is a validation-code decision for human review.
- **Match predicate:** The stage 5 finding is an environment-start exception and the same task errored at stage 3.
- **Limits and counterexamples:** Five tasks now give a valid zero with executed tests. task_004801 remains failed and task_004102 remains without a reward. The gate is inconsistent on fixture errors: '2 passed, 19 errors' (task_004515) and '29 passed, 21 errors' (task_007840) are accepted while '24 errors' is rejected. No rule was tested for this.
- **Same retained pilot tasks:** stage 5 passed 3 → 8.
- **Full-source effect:** 2674 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-00`.

### ucsb-mlsec/terminal-bench-env — wave 0, repair 1

- **Failure signature:** NOP run has no reward because the environment never starts: the same 'Dockerfile COPY source does not exist' or '[Errno 17] File exists' bridge exception as stage 3, for exactly the tasks that errored at stage 3.
- **Cause:** Downstream of the stage 3 environment-start failure. The verifier and tests were not at fault and were not changed.
- **General rule:** Repair the stage 3 cause and rerun NOP unchanged in a fresh container. Count a pass only when the verifier output shows tests executed and reward 0.
- **Match predicate:** The stage 5 finding is an environment-start exception with no verifier output, and the same task errored at stage 3.
- **Limits and counterexamples:** Confirmed on 1 task: rsync_incremental_backup_strategy_medium now runs 10 tests, all failing on missing deliverables, reward 0 (stage 5 passed 5 → 6). The four Errno 17 tasks still have no reward and are not passes. The NOP zeros for shap (7 failed), yara (12 failed) and perceptual_loss (7 failed) fail on the missing output file before reading copied inputs, so they do not show the /workspace layout is correct. With no oracle (stage 4 skipped 10 of 10), a NOP zero does not show any task is solvable.
- **Same retained pilot tasks:** stage 5 passed 5 → 6.
- **Full-source effect:** 216 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/termigen/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/termigen/loop/wave-10/iteration-00`.

### AweAI-Team/CalibForge — wave 0, repair 1

- **Failure signature:** NOP run has no reward because the environment never starts: the same tag+digest or %post/libutempter bridge error as stage 3, for exactly the six tasks that errored at stage 3.
- **Cause:** Downstream of the stage 3 build failures. The verifiers and tests were not at fault and were not changed.
- **General rule:** Repair the stage 3 cause and rerun NOP unchanged in a fresh container. Count a pass only when the verifier output shows tests executed and reward 0.
- **Match predicate:** The stage 5 finding is an environment-start exception with no verifier output, and the same task errored at stage 3.
- **Limits and counterexamples:** Confirmed on 6 pilot tasks: stage 5 passed 4 → 10, each with pytest assertion failures and reward 0 (1 to 37 tests failed; the security task had 1 failed and 1 passed). With stage 4 skipped 10 of 10 for lack of any solution, a NOP zero does not show a task is solvable. security_20260611_210349_004 passes stage 5 while still an error at stage 3. The before and after runs used different hashes of validation/stages/harbor.py, so the gate code was not held constant.
- **Same retained pilot tasks:** stage 5 passed 4 → 10.
- **Full-source effect:** 5086 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-00`.
