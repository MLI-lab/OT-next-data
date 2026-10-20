# [SETA-Env](https://huggingface.co/datasets/camel-ai/SETA-Env)

## Dataset stats

4,567 native tasks; 1,414 excluded during upstream conversion, leaving **3,153 audited tasks: 3,107 kept, all 3,107 patched; 46 archived**. Original unique image count: **Not recorded**. Current: **11 cached Apptainer environments** covering all kept tasks; per-task setup still installs task-specific dependencies.

## Dataset characteristics

<details><summary>Domains</summary>

**Data Science and Machine Learning:** data cleaning, analysis, scikit-learn models and reports. **Debugging:** Python/pandas repairs. **System Administration:** shell and system configuration. **Data Processing:** file inventories, policy checks and structured outputs.

</details>
<details><summary>Capabilities</summary>

**Specification Adherence:** exact paths, schemas, metrics and constraints. **Exploration:** inspect data, policies and broken scripts. **Error Recovery:** reproduce failures and verify repairs. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

</details>

Intended benchmark transfer: **Terminal-Bench 2.0, OpenThoughts-TBLite 2.0, DS-1000 mini-200 and BixBench CLI corrections @ Pi**, through terminal workflows, Python data analysis and executable verification.

## Per-stage breakdown

- **Build and setup: 3,107 kept tasks passed.** Repairs to kept tasks: **215 environments, 72 setups, 3,021 verifiers and 35 configurations**, including Docker-to-Apptainer adaptation, dependency pins and resource/time limits. **24 archived:** 19 Rocky Linux ownership mismatches, 2 read-only NVIDIA-tool conflicts and 3 slow R setups.
- **No-op baseline (reward 0): 3,107 kept tasks passed.** **32 repairs:** 31 setup-timeout increases and 1 missing-backup-script check; **2 archived** for slow R setup.
- **Reference solution (reward 1): 3,107 kept tasks passed.** **6 repairs** to memory, filename matching, PHP requests, reference startup/logging and setup timeout; **1 archived** for slow desktop setup.
- **Rule-based checks:** **25 kept instructions clarified**, plus timeout/anti-cheating suffixes on **3,107**; historical dependency-pin repairs kept. **43 historical `check-test-file-references.sh` flags reviewed: 19 archived** for undisclosed deliverable paths; **24 kept** because paths were discoverable or tokens were shell syntax. Reviewed `nproc` commands were kept after allocation probes; absolute-path checks were excluded from the original full run and used only in later subsets.

Repair counts overlap. Results combine historical runs with a fresh rerun of the **3 restored tasks whose verifier files changed**; all three passed build, reference 1 and no-op 0.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; **32 CPU cores/job**, up to 16 concurrent runtime tasks.

| Check | Time per task | Tasks measured |
| --- | ---: | ---: |
| Rule-based checks | **0.70 s median** | 3,130 |
| Build and setup | **28.40 s median** | 3,130 |
| Reference solution | **35.60 s median** | 3,130 |
| No-op baseline | **38.40 s median** | 3,130 |

Original 3,130-task run, before later repairs and retries. Only medians were recorded; these are not means.
