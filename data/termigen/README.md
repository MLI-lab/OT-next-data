# TermiGen

## Dataset stats

- **Tasks:** 3,566 original → 3,336 retained, all 3,336 patched; 230 archived.
- **Image definitions:** 3,566 original → 3,336 retained task Dockerfiles (3,326 distinct); 3,336 runtime build contexts. No consolidation. Cached artifacts: none published yet.

## Dataset characteristics

[TermiGen](https://github.com/ucsb-mlsec/terminal-bench-env/tree/03bddac74ada2fa344df0e93b4a3ace2034294d1) supplies terminal tasks with prepared files and executable verifiers.

<details><summary>Domains</summary>

Software Engineering, Debugging, System Administration, Security, Data Processing, Scientific Computing and Machine Learning: repair code, diagnose services, inspect artifacts and configure computational workflows.

</details>
<details><summary>Capabilities</summary>

Exploration, Error Recovery and Specification Adherence; specialized tasks also involve Reverse Engineering and Adversarial Reasoning. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

**Intended benchmark transfer:** terminal problem solving for Terminal-Bench; debugging and environment diagnosis for repository-based software engineering.

## Per-stage breakdown

Counts cover retained tasks; repair categories overlap. Archives are counted at their first failing stage.

- **Rule-based checks:** 3,336 timeout/anti-cheating instruction repairs (`check-instruction-suffix.sh`); 141 archived: 135 relative instruction paths (`check-task-absolute-path.sh`), 3 host-directory binds (`check-compose-host-binds.sh`), 3 host CPU-count calls (`check-nproc.sh`). These preserve recorded exclusions; file-reference and pip-pin findings were advisory.
- **Build and setup:** 2,060 repaired for Apptainer and the Marin Harbor bridge with repository patches: 1,911 COPY destinations/renames, 149 files hidden by temporary mounts, 28 environment-variable expansions, 2 initial-file repairs, 1 missing build helper, 1 wildcard COPY. All 3,336 retained tasks passed. Another 54 archived: 12 missing/changed runtime files, 12 dependency failures, 9 incomplete checks, 7 startup failures, 6 absent COPY inputs, 3 image-preparation failures, 2 COPY collisions, 2 unsupported sidecars, 1 unavailable build command.
- **Reference solution (reward 1):** skipped; no reference solutions.
- **No-op baseline (reward 0):** 3,336 passed; 35 archived: 2 rewarded no work, 28 ran no tests, 5 timed out. No verifier repairs.

Uses [historical checks and reruns](https://huggingface.co/datasets/FWeindel/validated-tasks/blob/44b082b9ad564790321d6a569c8531ed14a3393f/runs/termigen-phases-1-3-5-6-7-preview-20261014.json). Teacher results did not determine retention.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.7 / 3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; **48 CPU cores, 32 GiB RAM per job**, 2 concurrent tasks.

| Check | Mean per task | Median per task | Tasks measured |
| --- | ---: | ---: | ---: |
| Rule-based checks | **0.40 s** | **0.35 s** | 3,336 |
| Build and setup | **60.62 s** | **71.32 s** | 3,336 |

Retained tasks only. Rule-based time sums the recorded checks; build/setup measures environment start, inspection and stop, excluding image builds and verification.
