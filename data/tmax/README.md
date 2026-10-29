# [TMAX](https://huggingface.co/datasets/allenai/TMax-15K)

## Dataset stats

- **Original tasks:** 14,600
- **Kept:** 14,564
- **Changed:** 14,564
- **Archived:** 36
- **Images:** 14,494 images before, 623 images now.

## Dataset characteristics

[TMax: A simple recipe for terminal agents](https://arxiv.org/abs/2606.23321) provides terminal tasks involving software repair, services, file processing and executable verification. This version uses the pinned TMAX snapshot distributed through [Prime Intellect's prime-tasks](https://github.com/PrimeIntellect-ai/prime-tasks/tree/27de1c1b1ed6279b607e105ff9cb598e177ad938/datasets/tmax).

<details><summary>Domains</summary>

**Software Engineering and Debugging:** write scripts and services, repair programs, diagnose build failures and check executable output. **System Administration:** configure service bindings, processes, quotas, permissions and audit logs. **Data Processing:** transform JSON, XML, CSV, logs and binary records. **Security:** repair audit pipelines and redact sensitive binary content. **Scientific Computing:** extract numerical data and produce results checked against expected values.

</details>
<details><summary>Capabilities</summary>

**Exploration:** inspect source, logs, binaries and directory layouts. **Error Recovery:** diagnose crashes, build errors and incorrect outputs. **Specification Adherence:** satisfy exact output, path, permission and formatting requirements. **Constraint Satisfaction:** change behavior while preserving required data or structure. **Reverse Engineering:** infer procedures and inspect binary artifacts. These are intended capabilities inferred from task examples, not measured agent behavior. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

Intended benchmark transfer: **Terminal-Bench 2.0 and OpenThoughts-TBLite 2.0**, through terminal-based repair, service configuration and file processing; **SWE-bench Verified**, through source inspection and test-driven debugging.

## Per-stage breakdown

- **1. Build and setup: 14,564 changed, 2 archived.** Changed all kept tasks to build with Apptainer: 13,943 tasks share two dependency images and keep their task-specific files and setup separate; startup times were measured to make sure the shared images do not slow startup. The other 621 tasks keep their own images. Changed 3 tasks so the services their tests need start reliably. Archived 1 task that needs Docker inside the container and 1 task whose setup cannot remove executable permissions.
- **2. Reference solution (reward 1): not applicable.** TMax has no reference solutions.
- **3. No-op baseline (reward 0): 12 changed, 34 archived.** Changed 12 tasks whose tests failed for reasons unrelated to the agent's work, such as missing test inputs or services the tests start themselves. Archived 22 tasks whose verifier did not finish on the unchanged task: in 11, the planted bug (endless loop, deadlock, cycle without a visited check, diverging integrator) runs inside the tests without a time limit, so the no-op hangs until the 120 s verifier timeout; in 7, the tests run a slow reference program or dependency build before checking the agent's work, so a correct solution may also exceed the timeout; 4 crash or hang for other reasons (a background database holding the test output, unsafe code run inside the test process, unbounded service launches). Archived 6 tasks whose unchanged state already scores 1, 4 tasks whose tests contain no test functions, 1 task whose reference program is empty and 1 task whose tests kill their own test process. Manually approved 132 no-op failures where the tests' setup fails because the agent's output or service is missing, which is the expected result when the agent does nothing.
- **4. Rule-based checks: 14,564 changed, 0 archived.** Changed all 14,564 kept tasks so their instruction ends with the time limit and the request not to cheat. Flagged 1,095 tasks for relative paths. Completed path discovery for all 1,095 flagged tasks. Automatic repair cleared warnings for 349 tasks. 746 tasks still have warnings. Inspected 50: all sampled warnings were harmless (commands run from a directory the instruction names, example archive or path-traversal names, package patterns). Accepted the remaining path warnings after this review.

Git-history cleanup, timestamp normalization and repository solution-leak checking have not been completed.

## Per-task resource requirements

| CPUs | Memory | Agent timeout | Verifier timeout | Tasks |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2 GiB | 600 s | 120 s | 14,564 |

## Runtime and validation time

- **Versions:** Apptainer **1.5.4** (CLAIX package `1.5.4-1.el8`), Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) with [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** stages 1 and 3 ran on CLAIX shards of **48 CPU cores, 110–128 GiB RAM** with trial concurrency 8; stage 4 requested **64 CPU cores, 192 GiB RAM** with static-check concurrency 32; image building uses **4 CPU cores and 8 GiB RAM per image**. Includes the full validation run and reruns of repaired tasks.

| Stage | Measured tasks | Total stage time (summed shard wall time) | Mean per task | Median per task |
| --- | --- | --- | --- | --- |
| 1. Build and setup | 14,564 (all kept) | 89.3 h | 12.9 s | 10.3 s |
| 3. No-op baseline | 14,564 (all kept) | 36.7 h | 57.4 s | 23.3 s |
| 4. Rule-based checks | 14,564 (all kept) | 211.7 s | 0.4 s | 0.4 s |

See the [release metadata](metadata/release-review-20261027.json) and [historical validation report](metadata/validation-run-20261022.json) and [timing summary](metadata/timing-20261022.json) for evidence.
