The original SETA-Env release contains 4,567 tasks. The conversion in [TaskTrove PR #4](https://huggingface.co/datasets/open-thoughts/TaskTrove/discussions/4) excludes 1,414 tasks whose environments relied on overly specialized Dockerfiles or could not be converted reliably from Docker to Apptainer. All 3,153 retained tasks were audited on an Apptainer cluster: a no-op scored 0, the packaged gold solution scored 1, and every verifier emitted its score through reward.txt.

We then tried to reproduce the results with validation pipeline here: 

## Stage 3: Verifying build

- **19 excluded:** Rocky Linux builds required file ownership our Apptainer builder couldn’t preserve. Fakeroot completed installation but produced incorrect ownership.
- **5 additional exclusions:** 2 read-only NVIDIA-tool conflicts and 3 very slow R-package setups.
- **Total: 24 excluded; 3,129 passed stage 3.**

## Stage 4: Reference solution reward 1

- **6 failures fixed:** 2 memory increases, 1 fix to how the verifier matched filenames, 1 fix to how the PHP monitor and verifier sent requests, 1 reference script fixed to start reliably and generate logs faster, and 1 setup-timeout increase. All six then scored 1.
- **1 task excluded:** installing desktop packages during task setup took too long, so the reference solution could not run successfully.
- **Total for stage 4: 7 tasks changed: 6 patched and 1 excluded.**

## Stage 5: No-op reward 0

- **32 additional failures fixed:** 31 setup-timeout increases and 1 verifier fix so a missing backup script fails the tests normally instead of stopping them from running. All 32 then scored 0.
- **2 additional tasks excluded:** R-package installation during task setup too slow.
- **Total for stage 5: 34 tasks changed: 32 patched and 2 excluded.**

**Stages 3/4/5 dataset:** [`camel-ai__SETA-Env_filtered_stages_3_4_5`](https://huggingface.co/datasets/FWeindel/validated-tasks/discussions/13). Strict baseline with static-check-only repairs removed; 3,126 candidate tasks, awaiting fresh validation.

## Stage 1: Static checks

- **217 tasks patched:** we pinned Python package versions in installation commands or made setup use the same pytest version as the verifier.
- **27 tasks patched after `check-test-file-references.sh` flagged them:** in 23, we updated the instructions to name the files, output paths or command-line arguments that the tests expected. In 7, we changed tests or reference solutions to better match the instructions and check actual behavior. Three tasks received both kinds of repair.
- **43 tasks excluded:** they also failed `check-test-file-references.sh`. We left these findings unrepaired for now because resolving them reliably required further review and repair work.
- **Total: 284 distinct tasks affected, 244 patched and 43 excluded, with 3 tasks in both groups.** This left 3,083 tasks passing stages 1, 3, 4 and 5.

Separately, all 3,134 tasks received the standard timeout and anti-cheating instruction sentence.

## Stage 2: LLM as a judge on tasks

We sampled **1,000 of the 3,083 stage-1 survivors with seed 42** for review with **Claude Opus 5.5**, using our patched Terminal-Bench rubric and container inspection. The resulting version will be named `camel-ai__SETA-Env_filtered_stages_1_2`. Tasks that fail an active rubric criterion will be excluded; review errors will be retried.

The sample is reproducible with [`prepare_quality_subset.py`](prepare_quality_subset.py), which samples from sorted task IDs and saves the selected IDs and source hashes. Matching stage-3/4/5 baseline tasks are saved for comparison. Review results will be added when the run finishes.

[Inspect the first 199 completed reviews](stage2-review-199/README.md): pass/fail counts per criterion, task instructions, judge explanations and recorded costs.
