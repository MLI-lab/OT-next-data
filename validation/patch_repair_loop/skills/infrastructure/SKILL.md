# Infrastructure failures

Use this file for confirmed repairs to shared Slurm submission, image caching,
Apptainer bridge, cluster networking, or validation runtime code. An
infrastructure failure is retried after repair and never justifies discarding
a task. Record the failure signature, root cause, general infrastructure rule,
and before/after evidence below.

## Confirmed repairs

### FACET-Terminal/FACET-Terminal-Tasks-6k — wave 0, repair 1

- **Failure signature:** worker.log: '[build] Dockerfile build failed for <sif> (… while running %post section: exit status N); importing docker://ubuntu:22.04 and deferring N RUN step(s) to a baked overlay', followed by libutempter.so.0 or dpkg errors under 'unshare -r'.
- **Cause:** No shared-runtime defect was repaired in this iteration; runtime hashes are identical before and after. The errors users see come from the deferred-overlay fallback and are secondary to a failed fakeroot %post build.
- **General rule:** When environment start fails with libutempter or 'unshare -r … --overlay' errors, first look in worker.log for the 'Dockerfile build failed … deferring' line and diagnose the %post failure. Treat the fallback error as a symptom, and do not discard the task.
- **Match predicate:** A stage 3 bridge error whose task has a 'Dockerfile build failed … deferring N RUN step(s)' line in worker.log.
- **Limits and counterexamples:** This is a diagnostic rule only; no infrastructure repair was made or confirmed. The 300-character cut on build stderr hides the actual %post error. The task_004102 fakeroot/dpkg failure is unresolved and may need an infrastructure fix. The handling of /tmp during %post was worked around on the dataset side, not fixed in the builder.
- **Full-source effect:** 2674 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/facet-repair/loop/wave-10/iteration-00`.

### ucsb-mlsec/terminal-bench-env — wave 0, repair 1

- **Failure signature:** worker.log: 'Job j-… failed: [Errno 17] File exists: <bridge>/apt_env-…/workspace/<file>', within about 0.1 s and before any 'SIF resolution' line. The task's Dockerfile has a single-file COPY to /workspace/<same relative path as in the build context> (for example COPY classifier.py /workspace/classifier.py).
- **Cause:** Unconfirmed, from code reading only: in Harbor's apptainer worker.py, the single-file branch of _apply_dockerfile_copies calls os.makedirs(dest_abs, exist_ok=True) on the destination file path, which already holds the staged context file. No shared-runtime change was made in this iteration, so the cause has not been tested.
- **General rule:** Treat this as a shared-runtime defect: do not discard the task and do not rewrite the Dockerfile. It must be fixed in the human-reviewed bridge code and the affected tasks retried. Until then report them as stage 3 errors.
- **Match predicate:** A stage 3 or stage 5 bridge error '[Errno 17] File exists' on a path under the bridge workspace, for a task whose Dockerfile COPYs a single file to /workspace/<path> where <path> is also a file in the build context.
- **Limits and counterexamples:** No infrastructure repair was made or confirmed; the four pilot tasks fail identically before and after (adversarial_attack_fgsm_medium, directx_dxil_shader_reflection_hard, pyrender_offscreen_egl_init_medium, snowpipe_continuous_loading_medium). The full-source counts (1210 crash, 844 silent wrong-layout) come from the proposal's scan and were not run. The silent subclass is derived from the code and was never observed. The proposed fix (create the parent directory, copy to the destination path, skip when source and destination are the same file) is untested.
- **Full-source effect:** 216 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/termigen/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/termigen/loop/wave-10/iteration-00`.

### open-thoughts/TaskTrove/pymethods2test — wave 0, repair 1

- **Failure signature:** Stage 1 reports check_ai_detection.py skipped on 10 of 10 tasks with 'no GPTZERO_API_KEY configured', leaving the stage as missing_or_skipped_check after all 14 required static checks pass. There are no bridge, build or Slurm errors; stage 3 passes 10 of 10 before and after.
- **Cause:** A credential for an optional check is not configured in the validation environment. No shared-runtime defect was found and no infrastructure file was changed (action retry_only, changed_files empty).
- **General rule:** Treat a check skipped for a missing credential as unverified, not as a pass and not as a runtime defect. Do not edit shared code or the dataset for it and do not discard tasks. Either supply the key and rerun, or have a human record the check as explicitly unverified under the frozen static profile.
- **Match predicate:** A stage 1 finding whose only non-passed check is check_ai_detection.py with status skipped and detail 'no GPTZERO_API_KEY configured', with no stage 3 environment-start or bridge error for the same task.
- **Limits and counterexamples:** No infrastructure repair was made or confirmed, and a plain retry will not change the result without the key. Whether the frozen profile requires this check is undecided and needs a human. The proposal's observation that validation/stages/harbor.py:135 expects '=' borders on the 'N errors during collection' line while pytest prints '!' was not tested or changed; the no-op rejection came from the generic errors-only rule and is correct for these logs.
- **Full-source effect:** 4990 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/pymethods2test/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/pymethods2test/loop/wave-10/iteration-00`.

### AweAI-Team/CalibForge — wave 0, repair 1

- **Failure signature:** Stage 3 reports status error with an empty error string and a start time of 300.0 s for one task, while worker.log later shows the same environment's tmux owner ready and the SIF built without a deferred overlay. Separately, check_ai_detection.py is skipped 10 of 10 with 'no GPTZERO_API_KEY configured'.
- **Cause:** The first build of a new image on the calibforge base took longer than the 300 s stage 3 start limit while eight builds ran concurrently; this is inferred from timings, not tested. The skip is a missing credential. No shared-runtime file was changed by this repair.
- **General rule:** Treat an empty-error 300 s start timeout as a retry once the image is cached, not as a task defect or a discard. Treat a check skipped for a missing credential as unverified, not as a pass. When comparing before and after, check that the contract hashes of shared runtime files match.
- **Match predicate:** A stage 3 item with status error, empty error text and start time at the 300 s limit, whose environment shows 'tmux owner ready' later in worker.log; or a stage 1 finding whose only non-passed check is check_ai_detection.py skipped for a missing GPTZERO_API_KEY.
- **Limits and counterexamples:** No infrastructure repair was made or confirmed, and the rerun has not been done, so the timeout cause is unproven. The tag+digest and missing-cd defects were worked around in the dataset and remain in the bridge and builder. The hashes of hpc/helma/validation.sbatch and validation/stages/harbor.py changed between the before and after runs for reasons outside this patch.
- **Full-source effect:** 5086 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-01/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-00`.

### AweAI-Team/CalibForge — wave 0, repair 2

- **Failure signature:** Empty-error 300 s environment-start timeout at stage 3 for one task on a first, uncached image build. Separately, check_ai_detection.py is skipped 10 of 10 with 'no GPTZERO_API_KEY configured'.
- **Cause:** Cold image build time against a fixed 300 s start limit under concurrent builds; inferred from timings. The skip is a missing credential for an optional check. No shared-runtime file was changed: the contract hashes of all stage and bridge files are identical between the two runs.
- **General rule:** Treat an empty-error start timeout whose build later completes as retry_only and rerun against the warm image cache, checking that runtime contract hashes match between runs. Record the optional GPTZero skip as skipped; it is non-failing and never a pass.
- **Match predicate:** A stage 3 error with empty error text and start time at the 300 s limit where worker.log shows the SIF built afterwards; or a stage 1 entry for check_ai_detection.py with status skipped and optional true.
- **Limits and counterexamples:** No infrastructure repair was made. The retry passed only because the image was cached; the start limit, build concurrency and base-image pre-warming are unchanged, so cold builds in the full source can time out the same way. The tag+digest and missing-cd defects remain in the bridge and builder and are worked around in the dataset.
- **Full-source effect:** 0 changed, 0 removed, 0 added task packages.
- **Evidence:** `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-02/before-after.json`; repair record: `/hnvme/workspace/y500bb12-optiagent/repair-queue/calibforge/loop/wave-10/iteration-01`.
