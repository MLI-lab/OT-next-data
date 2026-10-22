# Scale SWE

## Dataset stats

- **[17,202 original tasks](https://huggingface.co/datasets/PrimeIntellect/Scale-SWE-Verified/tree/8935f8e55244fd56080cdb8dcd0819a57e8a003c); 16,786 kept, all changed; 416 archived.**
- **17,202 original Docker images → 16,786 kept images.** No consolidation; the decrease follows task exclusions. Exact tags remain in each Dockerfile, under [aweaiteam/scaleswe](https://hub.docker.com/r/aweaiteam/scaleswe/tags).

- All kept tasks use the original Docker Hub image tags; **48 optional cached Apptainer conversions** are also available on HF. Duplicating the remaining upstream images on HF is unnecessary.

## Dataset characteristics

Real repository pull requests with issue instructions, executable regression tests and reference fixes.

<details><summary>Domains</summary>

Software Engineering; Debugging; Web & API Tooling; Scientific Computing.

</details>

<details><summary>Capabilities</summary>

Exploration; Error Recovery; Specification Adherence; Constraint Satisfaction. Trace repository behavior, modify code and run regression tests.

</details>

**Likely transfer targets:** SWE-bench Verified; repository debugging in Terminal-Bench 2.0 and TBLite 2.0.

## Per-stage breakdown

Counts below describe kept repairs; repair groups overlap. Results combine historical validation and repair reruns; the later full reproduction failed during startup.

- **Build and setup:** 16,786 passed; 0 dataset repairs, 0 archived. Original per-task images ran through the shared Apptainer/Marin Harbor bridge.
- **No-op baseline (reward 0):** 16,786 passed; 37 repaired, 54 archived. Repairs: 31 checkout imports, 5 deferred missing-feature imports, 1 fixture/worker-timeout repair. Archives: 37 passing without a fix, 15 test-only pull requests, 1 timeout and 1 timing-sensitive result.
- **Reference solution (reward 1):** 16,786 passed; 419 repaired, 362 archived. Repairs: 367 missing test fixtures, 26 pytest file-handle conflicts, 6 worker limits, 5 import paths, 5 parameterized-test matching, 5 always-skipped required tests, 4 worker cleanup and 1 temporary-file cleanup. Archives: 149 failing reference tests, 122 unavailable services, 60 missing/incompatible dependencies, 18 stale HTTP recordings, 7 privileged ports, 4 import-path conflicts and 2 missing test definitions/helpers.
- **Rule-based checks:** 16,786 changed, 0 archived. Restored the timeout/anti-cheating sentence (`check-instruction-suffix.sh`) and 750 recorded instruction-path corrections supported by setup/reference observations. The historical `check-task-absolute-path.sh` treated unresolved relative paths as advisory; these corrections do not imply 750 checker failures.

## Runtime and validation time

- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; cached image building uses **4 CPU cores and 8 GiB RAM per image**.

| Check | Mean per task | Median per task | Tasks measured |
| --- | ---: | ---: | ---: |
| Rule-based checks | **0.30 s** | **0.29 s** | 16,786 |
| Build and setup | **10.04 s** | **9.38 s** | 16,786 |
| Reference solution | **16.53 s** | **10.70 s** | 16,786 |
| No-op baseline | **15.71 s** | **9.90 s** | 16,786 |

Latest recorded checks for the **16,786 kept tasks**. Build/setup measures environment start, inspection and stop; image builds are excluded. Reference/no-op timings cover the recorded trials.
