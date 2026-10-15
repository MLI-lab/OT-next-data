# SWE-Lego

## Dataset stats

- **[4,323 original tasks](https://huggingface.co/datasets/PrimeIntellect/SWE-Lego-Real-Data-Verified/tree/8b0d2bed6f04ef571ca04abebf737ea23cbceaf3); 4,277 retained, all changed; 46 archived.**
- **4,323 original build contexts → 101 shared contexts and cached Apptainer image bundles.**

## Dataset characteristics

Real repository issues with source checkouts, regression tests and reference patches.

<details><summary>Domains</summary>

Software Engineering; Debugging; Scientific Computing; Web & API Tooling.

</details>

<details><summary>Capabilities</summary>

Exploration; Error Recovery; Specification Adherence; Constraint Satisfaction. Locate faulty code, implement fixes and verify regression behavior.

</details>

**Likely transfer targets:** SWE-bench Verified; repository debugging in Terminal-Bench 2.0 and TBLite 2.0.

## Per-stage breakdown

Counts below describe retained repairs; repair groups overlap. Results are historical, with the validated instructions restored.

- **Rule-based checks:** 4,277 changed, 0 archived. Restored the timeout/anti-cheating sentence required by `check-instruction-suffix.sh`; no additional recorded automatic path or pip-pin repairs. Existing `/testbed` and setup instructions remain.
- **Build and setup:** 4,277 changed, 1 archived. Shared images, pinned dependencies and Apptainer/Marin Harbor bridge compatibility; 736 retained tasks also needed missing build dependencies. Archived 1 unavailable repository.
- **Reference solution (reward 1):** 4,277 passed; 55 repaired, 42 archived. Repairs: 19 missing history/metadata/dependencies, 13 worker limits, 6 memory limits, 6 legacy SymPy helpers, 5 mocked/recorded HTTP settings, 3 astronomy time tables, and 1 each for a fixed test date, CPU affinity and Python import paths. Archives: 23 proxy-sensitive tests, 8 unreliable services, 6 dependency mismatches, 2 version-sensitive expectations, and 1 each for system headers, expired licence and excessive memory.
- **No-op baseline (reward 0):** 4,277 passed; 3 repaired, 3 archived. Repairs: defer a missing-feature import, test the buggy Python backend, freeze a date-sensitive test. Archived 2 tasks passing without a fix and 1 verifier usage error.

## Runtime and validation time

- **Versions:** Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; **32 CPU cores/job**, 32 concurrent tasks. Image builds: **4 CPU cores**, 98 images at **12 GiB**, 3 at **8 GiB**.

| Check | Time per task | Tasks measured |
| --- | ---: | ---: |
| Rule-based checks | **0.80 s median** | 4,281 |
| Build and setup | **70.10 s median** | 4,281 |
| Reference solution | **56.50 s median** | 4,280 |
| No-op baseline | **52.60 s median** | 4,280 |

Recorded full run, including subsequently archived tasks. Only medians were recorded; these are not means.
