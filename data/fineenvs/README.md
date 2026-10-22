# [FineEnvs / SmolDataEnvs](https://huggingface.co/datasets/FineEnvs/SmolDataEnvs-harbor-train)

## Dataset stats

- **Original tasks:** 5,000
- **Kept:** 5,000
- **Changed:** 5,000
- **Archived:** 0
- **Images:** 1 image before, 1 image now.

## Dataset characteristics

[SmolDataEnvs](https://huggingface.co/datasets/FineEnvs/SmolDataEnvs-harbor-train) (source revision [`4719635555de`](https://huggingface.co/datasets/FineEnvs/SmolDataEnvs-harbor-train/tree/4719635555de1666f374d847baebb500d368e493)) asks agents to inspect supplied datasets, compute an answer with Python data-analysis tools, and write it to `/workdir/answer.txt`.

<details><summary>Domains</summary>

**Data Science:** statistics, correlations, category frequencies, proportions and model evaluation. **Data Processing:** column conversion, missing values, filtering and aggregation of heterogeneous tabular data.

</details>
<details><summary>Capabilities</summary>

**Exploration:** inspect input files, shapes and column types before computing an answer. **Specification Adherence:** produce a single value, short label, yes/no answer or comma-separated list. The grader checks the final answer through exact, numeric, list and mathematical equivalence matching; it does not evaluate the analysis trace. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

**Intended benchmark transfer:** DS-1000 mini-200, through dataset inspection and use of pandas, NumPy and related analysis libraries. This is an expected transfer, not a measured training gain.

## Per-stage breakdown

Counts can overlap because a task can require changes at several stages.

- **1. Build and setup: 5,000 changed, 0 archived.** Changed 5,000 tasks to download inputs ahead of time and package them under `setup_files/inputs`, avoiding Hugging Face rate limits during parallel setup. Upstream used the mutable `:base` image tag; we pinned the same image to an exact version.
- **2. Reference solution (reward 1): 5,000 changed, 0 archived.** Added `solution/solve.sh` to 5,000 tasks to run our reference solution checker; all passed.
- **3. No-op baseline (reward 0): 5,000 changed, 0 archived.** Changed 5,000 tasks to normalize the JSON score key from `correctness` to `reward`; all recorded reward 0. The checker flagged missing grader execution for all tasks. Inspected a random sample of 50: all were valid no-ops, with the shell verifier writing zero and exiting before the Python grader started. Their reference trials confirmed that the grader could run successfully.
- **4. Rule-based checks: 4,576 changed, 0 archived.** Flagged 4,745 tasks for relative paths. The automatic resolver cleared warnings for 4,575 tasks; 170 still have warnings. Inspected a random sample of 50, and all warnings were harmless.

Git-history and timestamp checks were not applicable because no Git repositories were found at the scanned roots.

## Per-task resource requirements

| Kept tasks | CPUs per task | Memory per task | Agent timeout |
| ---: | ---: | ---: | ---: |
| 4,932 | 1 | 1 GiB | 600 s |
| 3 | 1 | 1 GiB | 900 s |
| 6 | 2 | 4 GiB | 600 s |
| 59 | 2 | 4 GiB | 900 s |

All tasks have a 600 s setup budget and a 120 s verifier timeout. Source resource limits were preserved.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4-1.el9**, Python **3.12.14**, Harbor **0.8.1**, math-verify **0.9.0**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma `cpu` and `preempt_cpu`; **48 CPU cores, 90 GiB RAM per job**, **up to 4 concurrent tasks**, no GPUs. All jobs required prebuilt images.

| Stage | Measured kept tasks | Total stage time (summed shard wall time) | Mean per task | Median per task |
| --- | ---: | ---: | ---: | ---: |
| 1. Build and setup | 5,000 | **4 h 53 min 01.0 s** | **13.26 s** | **10.62 s** |
| 2. Reference solution | 5,000 | **5 h 25 min 15.9 s** | **15.30 s** | **13.80 s** |
| 3. No-op baseline | 5,000 | **4 h 46 min 51.4 s** | **13.44 s** | **11.70 s** |
| 4. Rule-based checks | 5,000 | **3 h 41 min 11.0 s** | **0.50 s** | **0.46 s** |
