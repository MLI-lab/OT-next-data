# [MultiSWE](https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified)

## Dataset stats

- **Original tasks:** 2,232
- **Kept:** 1,879
- **Changed:** 1,879
- **Archived:** 353

Validation is in progress. These counts are provisional until reruns and remaining repairs are complete; the final kept set must pass all four stages, including explicitly approved individual reviews.

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

- **1. Build and setup: 1,879 changed, 8 archived.** Pinned the grader runtime and base images, installed dependencies in shared images, adapted setup for Apptainer, and made Gradle caches and build directories readable during workspace copying. Archived four Nuxt tasks whose setup exceeded 4 GiB and one Checkstyle task whose setup did not reliably succeed. Three Mockito tasks were temporarily excluded because the required image could not be built. Flagged 353 tasks as `slow-setup` (mean preparation time: **52.0 seconds**); none were archived for this flag alone.
- **2. Reference solution (reward 1): 1 changed, 319 archived.** Raised Hugo-12703's memory limit to 8 GiB. Archived 173 tasks whose reference solution scored 0, 53 with dependency, environment or test-execution errors, 92 whose reference solution ran out of memory (OOM), and one whose verifier exceeded 30 minutes. Manually approved one reference result after confirming that the log parser missed a passing test. The OOM tasks are candidates for repair by profiling peak memory and test-worker concurrency, adjusting memory requirements in `task.toml`, and revalidating. That repair remains incomplete. Seven additional React Router tasks remain pending: their reported verifier timeouts coincide with Slurm-confirmed OOMs, including retries; they have not been added to the archive.
- **3. No-op baseline (reward 0): 0 changed, 26 archived.** Archived 26 tasks whose unchanged baseline scored 1. Manually reviewed and accepted 418 no-op results as false positives from the checker's verifier-execution checks; all had passing paired reference solutions. These approvals apply only to the inspected results and preserve the original scores and error evidence. Async-1078 ran tests but its log parser failed, so its approved negative baseline retains a missing numeric score rather than a fabricated reward of 0.
- **4. Rule-based checks: change count pending, 0 archived.** Relative-path normalization is counted under this stage; the number of affected tasks is not yet consolidated.

## Runtime and validation time

- **Runtime:** Apptainer on Helma; pinned grader `multi-swe-bench==1.1.2` with Python **3.11.13** inside task images.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) plus [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Task resources:** typically **4 CPUs and 4 GiB**; task-specific exceptions remain. Hugo-12703 uses **8 GiB**. The verifier limit is **1,800 seconds**.
- **Preparation batches:** The October 19 follow-up jobs have finished. Three batches stopped near their **3-hour allocation limits**, leaving stage 4 incomplete for 48 tasks before the latest archive decisions. Allocation walltime is separate from each task's resource and timeout settings.
- **Timing coverage:** full-run timing aggregates are not yet consolidated. Do not interpret queue time, diagnostic guard limits or individual examples as dataset-wide medians.
