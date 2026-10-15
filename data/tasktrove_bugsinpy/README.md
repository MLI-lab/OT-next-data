# BugsInPy

## Dataset stats

- **Tasks:** 501 native bugs = **489 retained (all 489 converted/patched)** + **11 archived** + **1 not converted** (Keras 12 has identical buggy/fixed commits). Includes 462 retained instruction rewrites.
- **Images:** native source supplied none; conversion introduces **11 shared image definitions**. **11 verified cached Apptainer bundles** cover all 489 retained tasks, pinned by checksum and immutable image commit in `tasks.parquet`.

## Dataset characteristics

Real Python repositories at buggy commits, upstream regression tests and reference fixes from [BugsInPy](https://github.com/soarsmu/BugsInPy/tree/11c5f1eea954a42132cfd06bf257766a7963e0fd), replacing TaskTrove's synthetic code/tests.

<details><summary>Domains</summary>

**Debugging; Software Engineering.** Repair crashes, incorrect results and missing behavior in projects including pandas, Black, Luigi, spaCy, Tornado and youtube-dl.

</details>

<details><summary>Capabilities</summary>

**Exploration; Error Recovery.** Find relevant code in unfamiliar repositories, reproduce failures and iterate against regression tests. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

**Intended benchmark transfer:** SWE-bench Verified random-100; DeepSWE 1.1, through repository-level issue diagnosis and test-driven repairs.

## Per-stage breakdown

Counts overlap. Build, reference and no-op checks passed before instruction review; rule-based checks reran afterwards.

- **Rule-based checks:** 489 retain the timeout/anti-cheating suffix required by `check-instruction-suffix.sh` (427 carried the historical `anti-cheat-instruction` change label). **0 static archives**; 7 recorded `warning:relative-instruction-path` findings remained warnings. The contract excluded 13 checks. Separate instruction review preserved **462 rewrites** and archived **6** for tests requiring specific implementations (5) or testing a different issue (1), recorded as `flagged_by_instruction_loop`, not static-check failures.
- **Build and setup:** **489 converted** to shared Apptainer-compatible environments with pinned dependencies; **247** include compiler/system libraries. Shared Marin/Harbor bridge fixes addressed container startup, uploads and certificate mounts. **1 archived** (`environment_task_mismatch`): YouTube-DL 40 requires older-Python behavior absent from its recorded environment.
- **Reference solution (reward 1):** **489 passed**. Clean verifier dependency installs for **489**; source-timestamp fixes for **66**, plus **169 pandas** native rebuilds. **1 archived** (`flagged_by_instruction_loop`): HTTPie 4 depends on live httpbin.org availability.
- **No-op baseline (reward 0):** **489 passed**. **6 verifier repairs:** Ansible CPU regression (1), missing-function assertions moved inside tests (4), offline YouTube-DL chapter checks (1). **3 archived** (`buggy-version-passes-verifier`): Keras 15 and Scrapy 7/11 passed without a fix.

All 11 archives came from conversion or instruction review; the bullets group them by failure cause.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Final rule-based check run: Helma, **48 CPU cores, 128 GiB**, 24 concurrent tasks.

| Check | Time per task | Tasks measured |
| --- | ---: | ---: |
| Rule-based checks | **0.30 s median** | 489 |

The rule-based report recorded a median, not a mean.
