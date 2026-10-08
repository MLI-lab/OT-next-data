# SETA patch

[`patch.py`](patch.py) repairs the 3,153 SETA tasks from
[TaskTrove PR #4](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/4),
revision `9262d5628e13ec20ac75b7d897f94b93f6be0594`,
`camel-ai__SETA-Env/tasks.parquet`.
The current patch, `seta-stage1-v55`, keeps **3,134 tasks**: **3,052 patched** and
**82 unchanged**. It excludes **19** before validation.

[Hugging Face PR #4](https://huggingface.co/datasets/FWeindel/validated-tasks/discussions/4) contains 3,083 kept tasks, 70 archived tasks,
the automatically generated datasource README, validation evidence and 11 cached images.
The PR is open and unmerged.

```bash
python data/seta/patch.py --input upstream/tasks.parquet --output NEW_DIR/tasks.parquet
```

Use a new output directory. The patcher writes:

| File | Contents |
| --- | --- |
| `tasks.parquet` | Patched and unchanged tasks |
| `tasks.report.json` | Changes, exclusions and before/after hashes for each patched task |
| `tasks.archive.parquet` | Original payloads of the 19 excluded tasks |
| `tasks.manifest.json` | Comparison with upstream, including file changes and exclusion reasons |

These are patch outputs, not a validated release. Validation and publishing decide
which additional tasks to archive; see [patch reporting](../INVENTORY.md#reporting-patches-in-the-datasource-pr).

## What changes

Counts overlap: one task can receive several changes.

| Change | Tasks |
| --- | ---: |
| Fail on HTTP download errors, retry downloads and report installer failures | 3,011 |
| Preinstall pinned pytest in the shared NL2Bash image | 214 |
| Pin verifier packages installed through `uvx` | 146 |
| Pin unversioned pytest packages in task setup | 64 |
| Run the verifier with pinned `uvx` pytest and reject startup errors | 28 |
| State required output paths and command-line arguments in the instructions | 23 |
| Pin reference-solution packages | 9 |
| Increase memory from 2 GiB to 8 GiB for two references that ran out of memory | 2 |
| Isolate PHP status requests and repair the log-pipeline reference | 2 |
| Repair setup or an internal timeout/wait that caused an oracle failure | 8 |
| Repair individual verifiers or reference solutions | 8 |
| Align setup pytest with the verifier's version | 5 |
| Pin the verifier's `uv` installer | 4 |
| Pin a verifier's direct pip install | 1 |

v51 isolates FastCGI request variables in the PHP task's monitor and verifier.
For `unix_linux_se__synth__16455`, the reference protects collector startup from
early monitor signals and generates the same 5,000 records in one Python process.
Monitoring, random payloads, assertions and time limits are unchanged.

v53 raises `stack_overflow__synth__54789050` from 2 GiB to **6 GiB** for Julia
package precompilation. It passed static checks and three fresh setups, with
reference reward **1** and no-op reward **0**. The setup failure is cleared.
[Memory measurements and validation](/hnvme/workspace/y500bb12-seta-validation/experiments/julia-memory-20261007/verification.json).

v52 keeps all replacement code in `patch.py`, doubles setup budgets for 39
observed timeouts (600 → 1,200 seconds; 900 → 1,800 seconds), and makes a missing
backup script fail ordinary tests instead of fixture setup. Agent and verifier
limits are unchanged. The ten-task pilot passed stages 3, 4 and 5; stage 1
retained one existing control-task finding. The remaining 34-task timeout recheck has finished; see the combined results below. [v52 evidence](/hnvme/workspace/y500bb12-seta-validation/experiments/setup-verifier-v52-20261007/verification.json).

v50 raises the electric-production and Netflix tasks to 8 GiB, matching the
successful reference diagnostics. All five recent repairs passed the v51 pilot below.

v49 fixes `ask_ubuntu__synth__87`: JSON and CSV reports now match complete
filenames, so `unsafe_script.sh` cannot overwrite the permissions for
`safe_script.sh`. No assertions or expected permissions change.

The individual repairs test behavior on fresh inputs, check file contents and
preservation, or remove assumptions about how a valid solution is implemented.
All patch logic and replacement file contents live in `patch.py`. Repairing a
task does not itself establish that it passes.

## Excluded before validation

- **19 Rocky Linux tasks:** the cluster's Apptainer builder cannot preserve the
  `root:ssh_keys` ownership required by OpenSSH. Emulated fakeroot changes that
  ownership, so these tasks are excluded on this backend. A builder with suitable
  UID/GID mappings could support them.
The four previous `nproc` exclusions are restored in v54:
`ask_ubuntu__evolve__1060__d1`, `ask_ubuntu__synth__1060`,
`ask_ubuntu__synth__658`, and `ask_ubuntu__synth__826`.
Helma probe **948213** verified that their commands report the allocated 1 or 2
CPUs; the compilation commands selected matching `make -j1`/`-j2` values.
The training static checker permits only the reviewed solution and Dockerfile
contents. The nproc restoration changed no task commands or resource settings.
[CPU probe evidence](/hnvme/workspace/y500bb12-seta-validation/experiments/nproc-review-20261007/probe-results.json).
In v55, both SSH verifiers connect directly to their own server through a pipe,
without shared port 2222 or process-name cleanup. Only their own process group
is stopped. Under fakeroot, the server uses a nested user namespace with a real
non-root identity so OpenSSH can complete the handshake. A broken connection
cannot pass: the original binary must display the banner and the modified one
must complete authentication negotiation without it.

Helma job **948484** ran a concurrent ten-task pilot: all ten passed stages
1, 3, 4 and 5. Both SSH references scored **1** and both no-ops scored **0**.
All **827 repository tests** passed. Earlier failed attempts remain in their
original evidence archives.

## Validation results

The full v48 run (Helma job **934523**, 2026-10-04) checked all 3,130 tasks on an
H200 node with Apptainer fakeroot and up to 16 concurrent trials.

| Stage | Passed | Not passed |
| --- | ---: | ---: |
| 1 — Static checks | 3,087 | 43 |
| 3 — Build, start and setup | 3,120 | 10 |
| 4 — Reference solution scores 1 | 3,114 | 16 |
| 5 — No-op scores 0 | 2,695 | 435 |

**2,657 passed all four stages; 473 had at least one finding.** These counts include
execution errors, so a failed stage does not necessarily mean the verifier returned
the wrong reward. Original reports are under
`~/seta-validation-artifacts/full-v48-20261004/results/submissions/77dd88ef3465/report/`.

All **43 static failures** came from `check-test-file-references.sh`: it flagged
file references in tests that the task did not clearly provide. They comprise
27 Ask Ubuntu, 8 Unix/Linux, 5 NL2Bash and 3 Stack Overflow tasks.
Before the v49 repair on 2026-10-06, [regenerating v48](/hnvme/workspace/y500bb12-seta-validation/experiments/build-recheck-v48-20261006/current-patcher-comparison.json) produced byte-identical
payloads for all 3,130 retained tasks, including these 43. No later patcher change
had repaired their contents at that point.

The **10 build/start failures** were seven setup timeouts, two attempts to overwrite
the read-only NVIDIA tool, and one Julia setup killed at its memory limit.
The recheck below repeats them in fresh environments; a task needs three
consecutive passes to clear its build finding. Other stage findings still apply.

The **16 reference-stage failures** include those ten setup failures, one more setup
timeout (`unix_linux_se__synth__14746`), and five other failures: a filename-matching
bug (`ask_ubuntu__synth__87`), unavailable PHP-FPM pools
(`stack_overflow__synth__45762059`), a 60-second test subprocess timeout
(`unix_linux_se__synth__16455`), and two killed Python reference processes (the
electric-production and Netflix Kaggle tasks), leaving required outputs missing.
The Kaggle verifier runs finished in about five seconds; they were not verifier
timeouts. Both tasks exceeded their 2 GiB memory limit: Slurm recorded OOM kills
in steps `934523.4665` (electric-production) and `934523.4732` (Netflix); see
[the matching task and Slurm evidence](/hnvme/workspace/y500bb12-seta-validation/experiments/stage4-review-v48-20261006/oom-evidence.json). These historical observations
are not evidence that every failure is permanent.

### Current combined results (2026-10-07)

After the timeout rechecks, nproc restoration and v55 SSH repair, **3,083 of 3,134 tasks pass all
four stages; 51 have findings**. Another 19 are excluded by the patch before validation.

| Stage | Passed | Failed | Skipped because stage 3 failed |
| --- | ---: | ---: | ---: |
| 1: Static | 3,091 | 43 | 0 |
| 3: Build/setup | 3,129 | 5 | 0 |
| 4: Reference | 3,128 | 3 | 3 |
| 5: No-op | 3,126 | 5 | 3 |

The Julia failure and `14746` reference timeout are cleared. The timeout batch
finished in 2 h 37 min: 28 of 34 passed no-op verification, three timed out during
fresh setup and three were skipped after unsuccessful build retries. Two
pre-existing static findings remain in that batch.
[Combined report](/hnvme/workspace/y500bb12-seta-validation/experiments/ssh-isolation-20261007/effective-report/summary.json).
Original failed attempts remain preserved as historical evidence.

The six remaining slow setups were timed separately in job **948136**, using
**1 CPU / 2 GiB per task**. Keep them marked for archiving because setup is too
slow; the diagnostic's extended deadline does not replace the validation results.

| Task | Total setup time | Finding |
| --- | ---: | --- |
| `ask_ubuntu__synth__339` | 20m06s | Desktop package installation took 19m54s |
| Kaggle `hrhuynguyen_lung-cancer…__b1` | 25m39s | R package installation took 25m09s |
| Kaggle `rtatman_regression-challenge-day-3__b1__d1` | 38m57s | R installation took 38m26s; `glmnet` compilation was killed at the memory limit, although setup returned success |
| Kaggle `ashydv_car-price…__b1` | >40m | Cancelled while compiling R packages; completion time unknown |
| Kaggle `matinmahmoudi_drug-classification…__b1__d1` | >40m | Cancelled while compiling R packages; completion time unknown |
| Kaggle `rtatman_regression-challenge-day-3__b1` | >40m | Cancelled while compiling R packages; completion time unknown |

[Exact task IDs, timings and memory evidence](/hnvme/workspace/y500bb12-seta-validation/experiments/setup-diagnosis-20261007/final-measurements.json).
The combined JSON includes these archive explanations. No setup timeout was
increased in `patch.py` after this diagnostic. Counts above are unchanged.

### v51 pilot (2026-10-07)

Helma job **946973** checked the five recently repaired tasks and five previously
passing controls. **All 10 passed stages 1, 3, 4 and 5**: references scored **1**,
no-op agents scored **0**. All **711 repository tests** passed.

The pipeline's nine reference tests, including four full 5,000-record runs,
finished in **1.71 seconds**. Startup protection alone took **223.47 seconds**;
generating records in one process also meets the instruction's timing requirement.

[Validation evidence](/hnvme/workspace/y500bb12-seta-validation/experiments/service-fixes-v51-20261007/verification.json) · [Combined results](/hnvme/workspace/y500bb12-seta-validation/experiments/service-fixes-v51-20261007/effective-report/summary.json).
The combined report now has **3,120 passed / 10 failed** in stage 4 and **2,662 tasks
passing all four stages**. It combines the pilot with prior results; the full
3,130-task dataset was not rerun. Original reports remain unchanged.

At that point, the **9 build failures** were six setup timeouts, two read-only NVIDIA
file errors and one killed Julia setup. All **10 stage-4 failures** stopped
during setup: those same nine tasks plus `unix_linux_se__synth__14746` (timeout).
Every remaining stage-3/4 finding has a `failure_label` and `failure_explanation`
in the combined JSON; the PR serializer preserves both. The remaining stage-5 findings are listed in the no-op recheck below. Automatic publication requires contract-bound outcomes; this
combined report is review evidence from multiple runs.

### No-op infrastructure recheck (2026-10-07)

All **391 infrastructure failures passed on retry** in jobs **947001/947002**:
no-op rewards were **0**, with no fakeroot errors. The current runtime tracks and
cleans up both message queues and semaphores owned by each stopped task; the
original run used older queue-only cleanup. No dataset changes were needed for
these retries. The precise cause of the original missing-`LD_PRELOAD` errors
remains unconfirmed.

Stage 5 now has **3,087 passed / 43 failed**: 38 setup timeouts, three known setup
failures and two verifier issues. Across all four stages, **3,045 tasks pass and
85 have findings**. The stage-1 and stage-5 counts of 43 refer to different sets.
[Combined results](/hnvme/workspace/y500bb12-seta-validation/experiments/nop-recheck-v51-20261007/effective-report/summary.json)
preserve the original failures and link each retry. This is combined evidence,
not a full dataset rerun. [Runtime investigation](/hnvme/workspace/y500bb12-seta-validation/experiments/nop-recheck-v51-20261007/fakeroot-investigation.json).

### Reference failures reviewed (2026-10-06)

The v49 permissions fix for `ask_ubuntu__synth__87` passed stages 1, 3, 4 and 5
in Helma job **946889**: reference reward **1**, no-op reward **0**.
[Validation report](/hnvme/workspace/y500bb12-seta-validation/experiments/stage4-review-v48-20261006/permissions/submissions/144bb5a81bfa/report/summary.json).

The recovered build task, `kaggle_notebook__evolve__melikedilekci_student-mental-health__b1`,
also scored **1** in its reference rerun (job **946885**, unchanged v48 task).
Its original stage-5 setup failure remains pending a no-op rerun.
After those reruns, stage-4 counts were **3,116 passed, 14 failed**.
[Combined results](/hnvme/workspace/y500bb12-seta-validation/experiments/stage4-review-v48-20261006/effective-report/summary.json)
retain the original reports and identify each replacement's source.

[Historical diagnosis and diagnostic runs](/hnvme/workspace/y500bb12-seta-validation/experiments/stage4-review-v48-20261006/investigation-summary.json).

### Stage-3 recheck (2026-10-06)

Rechecked the ten startup/setup failures with unchanged v48 task files and task
resource limits on Helma H200 nodes. **1 recovered** with three consecutive
passes; **9 remain failed** under the retry policy. Effective stage-3 counts:
**3,121 passed, 9 failed** out of 3,130. Recovered tasks are no longer
excluded for stage 3; existing findings in stages 1, 4 and 5 still apply.

[Recheck evidence](/hnvme/workspace/y500bb12-seta-validation/experiments/build-recheck-v48-20261006/recheck-summary.json) · [Updated combined results](/hnvme/workspace/y500bb12-seta-validation/experiments/build-recheck-v48-20261006/effective-report/summary.json).
The combined report records evidence from multiple runs; the original frozen run is preserved.

Cleared build finding: `kaggle_notebook__evolve__melikedilekci_student-mental-health__b1`.

Pass counts below include only the recheck attempts. A failed run triggers three
additional attempts in the pipeline.

| Task | Passes / attempts | Failure label | What happened |
| --- | ---: | --- | --- |
| `ask_ubuntu__evolve__562__d1` | 0/4 | Read-only NVIDIA tool | Setup tries to replace /usr/bin/nvidia-smi, but Helma mounts that file read-only on GPU nodes. |
| `ask_ubuntu__synth__135` | 0/4 | Setup time limit | Startup and setup did not finish within the task’s 600-second limit. |
| `ask_ubuntu__synth__339` | 0/4 | Setup time limit | Startup and setup did not finish within the task’s 600-second limit. |
| `ask_ubuntu__synth__562` | 0/4 | Read-only NVIDIA tool | Setup tries to replace /usr/bin/nvidia-smi, but Helma mounts that file read-only on GPU nodes. |
| `kaggle_notebook__evolve__hrhuynguyen_lung-cancer-detection-using-random-forest__b1` | 0/4 | Setup time limit | Startup and setup did not finish within the task’s 600-second limit. |
| `kaggle_notebook__evolve__matinmahmoudi_drug-classification-quick-start-for-beginners__b1__d1` | 0/4 | Setup time limit | Startup and setup did not finish within the task’s 600-second limit. |
| `kaggle_notebook__evolve__rtatman_regression-challenge-day-3__b1` | 1/4 | Setup time limit | Startup and setup did not finish within the task’s 600-second limit. |
| `kaggle_notebook__evolve__rtatman_regression-challenge-day-3__b1__d1` | 2/5 | Setup time limit | Startup and setup did not finish within the task’s 600-second limit. |
| `stack_overflow__synth__54789050` | 0/4 | Julia setup killed | The original 2 GiB setup was killed. Cleared at 6 GiB by the v53 pilot. |

## Checking the shared grader

SETA grades the files a task leaves behind. This command checks the shared grader
against correct files and six incorrect variants:

```bash
python data/seta/patch.py audit --pilot pilot.parquet --image task.sif --out NEW_DIR
```

## Earlier evidence

Earlier v12–v46 results describe older task versions and runtime bugs, including
a bridge cleanup bug that stopped active containers. They do not replace the v48
results above. The patch history is in
`~/seta-validation-artifacts/README-history-through-v48.md`.
Historical repair evidence, including three successful reference runs for
`unix_linux_se__synth__502065` with its original budgets, is preserved in
[the evidence archive](/hnvme/workspace/y500bb12-seta-validation/experiments/setup-verifier-v52-20261007/historical-repair-evidence.tar.gz).
The [detailed older notes](/hnvme/workspace/y500bb12-seta-validation/experiments/build-recheck-v48-20261006/before-merge-pr-readme-evidence.md)
are preserved with the recheck evidence.

## Verifier preparation boundary (v57/v58)

Every retained task now has `tests/setup.sh`. The patcher uses Bash syntax checks
without execution to separate complete installer statements from `tests/test.sh`.
APT/pip/uv installation and uv project initialization move into setup. `uvx` and
`uv run` resolve their original Python/package options there using `pytest
--version`; grading uses `UV_OFFLINE=1`. Existing task execution, compilation of
agent-modified code, pytest arguments and reward handling remain in `test.sh`.
Mixed installation/grading statements fail patching unless explicitly handled.

The pinned source produces 3,134 retained tasks and the same 19 exclusions.
The v57 comparison against v56 kept all instructions, solutions, task configurations
and Python verifier files byte-identical. V58 additionally removes the redundant
`pip install Pillow` fallback from `ask_ubuntu__evolve__945__b1`; Pillow was already
pinned in that verifier's uvx dependencies and is prepared by setup. Its grading
assertions are unchanged. Shell preparation and dependency timing nevertheless
changed, so this comparison alone does not certify old oracle/no-op or teacher
results for the new execution profile. Preserve old results with provenance;
revalidate changed environments and verifier wrappers before publishing or
resuming teacher generation.

Preparation review uses nested 10, 50, 200 and full-task cohorts, five fresh
containers per task and cached images. Stage 3 times task preparation and explicit
verifier setup separately. Move reusable slow installation into images, rebuild
changed image content, and repeat the affected gate before expanding. Only after
the preparation gate passes should new stage 4/5 results certify the new payloads.
Evidence and cohort IDs:
`/hnvme/workspace/y500bb12-seta-validation/experiments/setup-review-20261008/`.

The first five-run pilot passed 6/10 tasks (job 954826). V59 moves measured
dependency installation for package inventory and VPN tasks, and verifier
preparation for the report task, into their images. V60 fixes the video image:
Ubuntu's fontconfig post-install script assigns `root:staff` only when creating
`/usr/local/share/fonts`; pre-creating that directory as root with mode 0755
avoids the unmapped group in single-ID builds. Package configuration remains
enabled and the shared builder still requires a clean dpkg audit. The original
package post-install script and failed build log are preserved with the pilot.
These repairs require a new successful pilot before expanding the cohort.
The second pilot passed 7/10 (job 954946), including all five video preparations.
The other three images exposed a shared builder bug: missing CA bind targets
aborted the normal definition build, forcing an overlay fallback unable to update
the base image's unmapped group-owned files. The shared image adapter now creates
empty mount targets before `%post`. V61 also explicitly enters the declared
working directory when baking verifier preparation. No task limits were raised.

The 10-task gate passed on job 955102: all 50 fresh preparations succeeded,
with maximum task preparation 45.13 seconds. Earlier job 955050 exposed four
initial container-start timeouts on the same node, followed by fast successful
starts; that transient startup cause remains unproven. The shared runner also
now uses `--cleanenv` for instance execs, preventing host `UV_CACHE_DIR` from
overriding baked dependencies. The next gate is the nested 50-task cohort.
