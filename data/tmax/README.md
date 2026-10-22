# [TMAX](https://huggingface.co/datasets/allenai/TMax-15K)

## Dataset stats

- **Original tasks:** 14,600
- **Kept:** 14,564
- **Changed:** 14,564
- **Archived:** 36
- **Images:** 14,494 original build contexts; 623 for the tasks kept: two shared dependency images for 13,943 tasks and 621 task-specific build contexts (can be dropped if number of images becomes a problem).

These are the current prepared-dataset counts. Full stage 4 and final result reconciliation are still pending; this dataset has not yet been published to Hugging Face.

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

- **Build and setup: 14,564 changed, 2 archived.** Adapted all kept tasks for Apptainer builds. 13,943 tasks use two shared dependency images, with task-specific fixtures and setup kept separate. Setup times were measured to ensure shared images did not make startup too slow. The other 621 tasks use individual images. Repaired service startup in 3 tasks. Archived: 1 nested-Docker dependency and 1 setup-permission failure.
- **No-op baseline (reward 0): 12 changed, 34 archived.** Repaired task/verifier data in 12 tasks. Archived: 22 incomplete no-op cases, 6 tasks incorrectly awarding reward 1 to a no-op, 4 verifiers with no tests, 1 verifier whose reference script was empty, so it expected empty output instead of the requested log aggregation and 1 verifier that ran `killall python3`, killing its own pytest process. Reviewed 125 checker-flagged cases individually and accepted them as valid no-op zeros: verifier checks rejected missing outputs or services, or an unfinished deployment, as expected when the agent does nothing.
- **Reference solution (reward 1): 0 changed, 0 archived; not applicable.** 
- **Rule-based checks: changed and archived counts pending.** Full stage 4 is in progress with automatic instruction-path resolution enabled. The latest 11 verifier repairs already passed stage 4; full-dataset results have not yet been reconciled. Instructions receive the timeout/anti-cheating suffix. Final path-edit counts will be reported after the full run completes.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4** (CLAIX package `1.5.4-1.el8`), Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) with [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** full stage 4 on CLAIX requests **64 CPU cores, 192 GiB RAM**, trial concurrency 12 and static-check concurrency 32; image building uses **4 CPU cores and 8 GiB RAM per image**.

| Check | Median per task | Tasks measured |
| --- | ---: | ---: |
| Build and setup | Pending reconciliation | Pending reconciliation |
| No-op baseline | Pending reconciliation | Pending reconciliation |
| Rule-based checks | Pending completion | Pending completion |

Timing summaries will use the selected compatible results for the kept tasks. Full stage 4 and final per-task timing reconciliation are not yet complete.
