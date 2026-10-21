# [MultiSWE](https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified)

## Dataset stats

- **Original tasks:** 2,232
- **Kept:** 1,872
- **Changed:** 1,872
- **Archived:** 314
- **Pending final inclusion or exclusion:** 46
- **Images:** 2,232 original per-task Docker image references, reduced to 339 shared image build contexts before exclusions and **290** for the kept tasks.

The kept set is not yet a fully validated release. Reconciliation of saved outcomes still finds 3 setup failures, 635 reference failures, 622 no-op failures and 24 tasks without complete stage 4 results among kept tasks; these groups overlap. Manual approvals are included in these counts. Historical passes do not certify compliance with the current validation settings.

## Dataset characteristics

Real repository issue-solving tasks with a base checkout, issue instructions, upstream test patch and reference solution. Source: [PrimeIntellect/Multi-SWE-RL-Verified, revision `80de95c`](https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified/tree/80de95c62ac792c99dcfa8e26569bcd7d036bdc3).

<details><summary>Domains</summary>

**Software Engineering; Debugging; Web and API Tooling; Build Systems.** Tasks span projects including Checkstyle, Mockito, SpotBugs, Hugo, etcd, SDL, Svelte, React Router and Nuxt.

</details>

<details><summary>Capabilities</summary>

**Exploration; Error Recovery; Specification Adherence.** Locate relevant code in unfamiliar repositories, reproduce a defect, modify the implementation and verify the repair against project tests. Some tasks require dependency or build-system changes.

</details>

**Intended benchmark transfer:** repository-level issue diagnosis and test-driven repairs in SWE-bench-style evaluations and terminal software-engineering tasks. This is an intended use, not a measured transfer result.

## Per-stage breakdown

- **1. Build and setup: 1,872 changed, 8 archived.** Archived 8 tasks: 4 Nuxt setup OOMs, 1 Checkstyle setup failure and 3 Mockito image-build failures due to Maven dependency downloads.
- **2. Reference solution (reward 1): 62 changed, 269 archived.** Increased memory from 4 to 8 GiB for 58 tasks to prevent reference-solution OOMs. Restored missing Java regression-test inputs in 3 tasks; all 3 now pass reference and no-op validation. Fixed passing-test detection in 1 task. Archived 180 tasks whose reference solution returned 0, 53 whose verifier could not execute successfully because of dependency, environment or test-execution errors, 32 that hit the memory limit with an 8 GiB configuration, and 4 whose verifier exceeded 30 minutes.
- **3. No-op baseline (reward 0): 0 changed, 37 archived.** Archived 29 tasks whose unchanged baseline scored 1 and 8 tasks that hit the memory limit at 8 GiB. Manually approved 422 false positives from the no-op checker, which could not confirm verifier execution; the corresponding reference solutions passed.
- **4. Rule-based checks: 1,869 changed, 0 archived.** Later or unrelated Git history was accessible in 1,869 kept tasks; we removed it to prevent access to potential future fixes. Also cleared reflogs and unreachable objects and normalized tracked-file timestamps.

The strict check for exact solution-file copies in the agent environment passed for all 68 checked tasks selected to keep. The remaining tasks have not completed this check.

The test-output parser repair is awaiting cluster validation.

## Runtime and validation time

- **Runtime:** Apptainer on Helma; pinned grader `multi-swe-bench==1.1.2` with Python **3.11.13** inside task images.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) plus [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Task resources:** typically **4 CPUs and 4 GiB**, with task-specific exceptions. Increased 58 tasks from **4 to 8 GiB** with passing reference solutions. The verifier limit is **1,800 seconds**.
- **Task preparation:** median **13.5 s**, mean **19.5 s**, p95 **53.0 s** across all 2,232 original tasks, including container startup and setup.
- **Validation time:** summed stage wall time across the historical full run's 140 shards: setup **6.95 h**, reference **47.99 h**, no-op **46.88 h**, static checks **43.3 s**. These totals exclude queue waits, image preparation and separately recorded reruns; shards ran concurrently. Static timing predates the reference-solution leak check.
