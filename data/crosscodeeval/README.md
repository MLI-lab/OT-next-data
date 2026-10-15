# CrossCodeEval

## Dataset stats

- **Tasks:** 7,763 original → 6,710 retained, all 6,710 patched; 1,053 archived.
- **Images:** 3 original → 3 current definitions; 3 [cached Apptainer bundles](https://huggingface.co/datasets/FWeindel/validated-tasks/tree/main/crosscodeeval_v1/cached_images). No consolidation.

## Dataset characteristics

Complete the next statement in a real C#, Java, Python or TypeScript repository using excerpts from related files.

<details><summary>Domains</summary>

**Software Engineering:** cross-file code completion, repository APIs and library usage.

</details>
<details><summary>Capabilities</summary>

**Exploration:** find required names and calling conventions in supplied context. **Specification Adherence:** write the requested continuation in the required output file.

</details>

**Intended benchmark transfer:** Python subset: HumanEval+, MBPP+ and DS-1000 mini-200. Java/Python/TypeScript: repository exploration for SWE-bench Verified random-100. Labels follow [CLI-Universe](https://arxiv.org/abs/2606.22883v1).

## Per-stage breakdown

Counts cover retained tasks; repair categories overlap.

- **Rule-based checks:** 6,710 retain the timeout/anti-cheating instruction (`instruction-suffix`) and context guidance; 0 archived by this validation stage. Patcher rules separately archived 1,043 tasks with required identifiers absent from available context and 10 whose scored answer contained no identifiers. All 6,710 retain restored cross-file excerpts with answer leakage removed.
- **Build and setup:** 6,710 received pinned verifier dependencies; 6,710 passed, 0 archived. Docker definitions were converted to Apptainer for the Marin Harbor bridge and repository runtime patches.
- **Reference solution (reward 1):** reference solutions added to all 6,710; all passed, 0 archived.
- **No-op baseline (reward 0):** scoring repaired in all 6,710 to reject incomplete prefix matches and normalize comments/whitespace; all passed, 0 archived.

Results are from the [historical validation](https://huggingface.co/datasets/FWeindel/validated-tasks/blob/b8a56dc21aa9f78340048a367e70852c59d01ff8/runs/2026-10-03-c536bae24db8.json); retained task payloads are unchanged.

## Runtime and validation time

- **Versions:** Apptainer **1.5.4**, Python **3.12.14**, Harbor **0.8.1**.
- **Implementation:** [Marin Harbor `7faf878c14b6`](https://github.com/marin-community/harbor/tree/7faf878c14b6d72737579ec936ebf5f74ba3194d) + [our runtime patches](https://github.com/MLI-lab/OT-next-data/tree/main/harbor_patches).
- **Resources:** Helma; **48 CPU cores, 128 GiB RAM per job**, 16 concurrent tasks, no GPUs.

| Check | Mean per task | Median per task | Tasks measured |
| --- | ---: | ---: | ---: |
| Rule-based checks | **3.36 s** | **0.54 s** | 6,710 |
| Build and setup | **16.50 s** | **13.36 s** | 6,710 |
| Reference solution | **14.39 s** | **11.20 s** | 6,710 |
| No-op baseline | **15.35 s** | **11.50 s** | 6,710 |

Means and medians cover check execution, environment start/inspection/stop, and reference/no-op trials respectively. Image builds are excluded.
