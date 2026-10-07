# Dataset inventory and preparation

[Sources and status](#existing-datasets--environment-sources-to-audit) · [Dataset commands](#dataset-code) · [Patch reporting](#reporting-patches-in-the-datasource-pr)

## Existing datasets / environment sources to audit

Only sources with publicly released task, environment, or trajectory data are listed.

Availability:
- **A** = executable RL environment already released (task + environment + verifier/reward)
- **B** = task/environment artifacts are released or recoverable, but conversion/reconstruction work is needed
- **C** = trajectory source; directly useful for SFT and potentially useful for reconstructing RL environments

## Release TODO: publish reusable environment images

- [ ] Publish validated, pristine cached images alongside the task dataset on
  Hugging Face so downstream users can download them instead of rebuilding.
  Keep large image binaries separate from task Parquet rows; attach a versioned
  manifest mapping task/environment content hashes to pinned repository revisions,
  artifact paths, SHA-256 checksums, architecture, format, and build provenance.
- [ ] Include complete bundles: Apptainer SIF plus any required deferred-build
  metadata and overlay. Preserve original Dockerfiles for other runtimes; SIFs
  are not Docker/OCI images. Deduplicate environments shared by multiple tasks.
- [ ] Add manifest references to dataset/task metadata without breaking the
  current Parquet schema, and implement download, checksum verification, and
  local cache staging. Never publish containers modified by an agent, reference
  solution, or verifier; export only pristine build artifacts.
- [ ] Verify a fresh downstream run can download the published artifacts and
  pass validation without rebuilding. Record runtime compatibility and review
  redistribution permissions for the images before release.

Status: upload/download support implemented; no images uploaded by this change.
The publisher discovers pristine bundles in `$OT_WORKSPACE/images` (or
`--image-cache`), writes content-addressed artifacts and a versioned manifest,
and pins their HF commit in Parquet schema metadata without adding columns.
Materialization and cluster staging restore selected bundles automatically.
Legacy caches without build provenance are omitted. A live fresh-download
Apptainer validation and redistribution review remain release requirements.
See [image publication](../validation/docs/image-publication.md),
[`hpc/image_cache.py`](../hpc/image_cache.py), and
[`validation/publishing/image_release.py`](../validation/publishing/image_release.py).


Additional user/agent summaries of relevant papers and datasets can be found at [agentic-data-papers](https://franziweindel.github.io/agentic-data-papers/).

Notes:
- **Harbor: Yes** = native Harbor format or explicit Harbor support.
- **Harbor: Conversion** = an adapter or conversion is recorded; this alone does not establish that a ready-to-download Harbor taskset exists.
- **Harbor: No** = another execution framework is used.
- Converter links apply to that source or supported task template. **OT** = OpenThoughts-Agent generator; **Marin** = TaskTrove normalization/repair ([pipeline](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/pipeline.py), [converter registry](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/registry.py)).
- **pp** = absolute percentage-point improvement.
- **Status** = recorded run coverage and outcomes, checked against available reports and archived summaries on **2026-10-06**. Completion does not mean all tasks passed; historical repair-queue outcomes are dated 2026-10-04.
- **—** = no clean matched base → trained result attributable to the exact released source was verified.

## Pilot stage coverage

Stage numbers follow the [current validation workflow](../validation/README.md#workflow):
**1** static checks, **2** task review, **3** build/runtime, **4** reference solution,
**5** no-op, **6** teacher trials, **7** trace metrics, **8** trajectory review,
**9** adversarial trials, **10** hacker/fixer loop.

- **15 source pilots:** 20 tasks each, stages 1/3/4/5 requested. Stage 4 was skipped
  for all tasks in MiMo, CalibForge, TMAX, TermiGen, FineEnvs, pymethods2test and
  BugsInPy because no oracle was available. Dataset rows below link the reports or archived results
  containing the per-stage pass counts. MiMo covers code, DevOps-Gym build tasks,
  and Repo2RLEnv the FineEnvs `repo2rlenv-pr-runtime` derivative.
- **Later repair pilots:** the 2026-10-04 snapshot records 12 sources with
  submitted pilots, but none completed the 10 → 50 → 200
  sequence. CalibForge and BugsInPy used only stages 1/3/5. BugsInPy's conversion
  was subsequently retired despite its reduced-policy pass. Repair passes are
  separate from the original 20-task results and do not establish full-source acceptance.
- **BugsInPy replacement (2026-10-07):** native source-backed project cohorts now have stages 1/3/4/5 pass evidence, with documented exclusions and infrastructure retries. The historical TaskTrove conversion above remains retired. The five-task Opus 5.5 instruction pilot completed; all five were accepted by the loop, with independent cleanup recommendations before scaling.
- **SETA:** full stages 1/3/4/5 results now replace the old “pilot running” status.
- **CrossCodeEval:** evidence exists for stages 1–9, with different scopes and task
  versions. Stages 1/3/4/5 cover the full 6,710-task set; stage 2 is a small review
  pilot; stages 6/7 have current teacher/trace reports. Historical one-task
  [normal trajectory review](/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/reports/analysis-normal/summary.json) and
  [adversarial trajectory review](/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/reports/analysis-adversarial/summary.json) are **current stage 8**
  (called stage 7 in those reports). The
  [adversarial trial](/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/reports/agent-demo/summary.json) is **current stage 9**
  (called stage 8 in that report); its embedded old analysis is superseded by
  the two separate review reports. These demos do not validate stage 8/9 on the
  current full dataset. No completed stage-10 run was located.
- **InferredBugs (2026-10-07):** full stage 4 and stage 5 retries are running,
  6,398 tasks per stage across four CPU shards each. Docker Hub pull-rate limits have caused image-preparation
  failures; completed executions are not yet confirmed
  reward passes. Historical stage 1/3 evidence covers 6,398 tasks, with stage 3
  combining the original run and 42 retries using the retired runtime workaround.
  The new runs include the permanent build-time tmux fix. See the
  [retry job index](/hnvme/workspace/y500bb12-inferredbugs/stages45-retry-20261007/job-index.json)
  and [partial results](/hnvme/workspace/y500bb12-inferredbugs/full-repatched-20261004/partial-results.json).
- For sources other than CrossCodeEval, no completed stages 2 or 6–10 were
  located in the inspected records. “Not started” below means no recorded pilot.

Absolute result/archive links require access to the cluster filesystem. Archived
20-task runs retain their `results/submissions/<id>/report/summary.json` inside
the linked archive; they have not been restored just to update this inventory.

## Already in Harbor — pilot and full-run status

**18 sources.** Follow the [validation workflow](../validation/README.md#workflow)
for each. **Priority** is the processing order across both tables.

| Priority | Dataset / source | Status | Availability | Scale / artifact | Dataset / project | Paper | Harbor? | Original use | Published base → trained lift | Main expected eval target |
|---:|---|---|---|---|---|---|---|---|---|---|
| 1 | **Scale-SWE-Verified** | **Full stages 1, 3, 4, and 5 validation running**, 17,202 tasks (array **948254**). [Run artifacts](/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-full-validation-20261007-v2); [patched Parquet](/hnvme/workspace/y500bb12-optiagent/runs/scaleswe-972-path-resolution-20261007/patched-automatic-v2/tasks.parquet). 717 batches, up to 16 concurrent, 24-hour limit per batch. | **A** | 17,202 validated Python issue-resolution tasks | [Prime env/taskset](https://github.com/PrimeIntellect-ai/research-environments/tree/main/environments/swe/scaleswe_v1) / [HF source dataset](https://huggingface.co/datasets/PrimeIntellect/Scale-SWE-Verified) | — | **Yes — Prime first-party Harbor-backed taskset (`scaleswe_v1`)** | **RL-ready** | **Model trained:** —<br>**Benchmark:** SWE-bench-family training target<br>**Score:** no clean matched base → trained result isolating this exact verified fork is recorded here<br>**Lift:** **—** | **SWE-bench Verified**, **DeepSWE 1.1** |
| 2 | **Multi-SWE-RL / Multi-SWE-RL-Verified** | **20-task pilot complete, with findings** (job 923740). Stages **1, 3, 4, 5**. [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/multiswe-20-pilot.tar.zst).<br>Later repair pilot: 0/10; needs review. | **A** | 4,723 upstream tasks; 2,232 robust verified tasks in Prime fork | [Prime env/taskset](https://github.com/PrimeIntellect-ai/research-environments/tree/main/environments/swe/multiswe_v1) / [HF source dataset](https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified) | [arXiv:2504.02605](https://arxiv.org/abs/2504.02605) | **Yes — Prime first-party Harbor-backed taskset (`multiswe_v1`)** | **RL-ready dataset release** | **Model trained:** —<br>**Benchmark:** —<br>**Score:** no clean matched RL result attributable specifically to the Prime verified subset is recorded here<br>**Lift:** **—** | **SWE-bench Verified**, **DeepSWE** |
| 3 | **SWE-Lego Real Data / Verified** | **20-task pilot complete, with findings** (job 923669). Stages **1, 3, 4, 5**. [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/swelego-20-pilot.tar.zst).<br>Later repair pilot: 56/60; blocked in 50-new-task wave. | **A/C** | 32k tasks / 18k validated trajectories upstream; 4,323 independently verified real-data tasks in Prime release | [Prime env/taskset](https://github.com/PrimeIntellect-ai/research-environments/tree/main/environments/swe/swelego_v1) / [HF source dataset](https://huggingface.co/datasets/PrimeIntellect/SWE-Lego-Real-Data-Verified) / [upstream GitHub](https://github.com/SWE-Lego/SWE-Lego) | [arXiv:2601.01426](https://arxiv.org/abs/2601.01426) | **Yes — Prime first-party Harbor-backed taskset (`swelego_v1`)** | **SFT** | **Model trained:** SWE-Lego-Qwen3-8B<br>**Benchmark:** SWE-bench Verified<br>**Score:** trained model = 42.2%; matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence**<br><br>**Model trained:** SWE-Lego-Qwen3-32B<br>**Benchmark:** SWE-bench Verified<br>**Score:** trained model = 52.6%; matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence** | **SWE-bench Verified**, **DeepSWE** |
| 4 | **MiMo-V2.6-RL-oss** | **20-task pilot complete, with findings** (job 923506). Stages **1, 3, 5**; **4 skipped** (no oracle). [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/mimo-20-pilot.tar.zst).<br>Later repair pilot: 0/10; blocked. | **A** | 7,780 released agentic RL tasks across Code, Cyber, General, Visual/WebDev and Music; Docker images + training code released | [HF](https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss) / [GitHub training code](https://github.com/XiaomiMiMo/verl) | [Technical report](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-RL/blob/main/MiMo_V2_6_technical_report.pdf) | **No upstream Harbor; Harbor conversions exist for Code/Cyber** | **RL (domain GRPO)** | **Model trained:** MiMo-V2.6-Distill-Qwen-9B<br>**Benchmark:** SWE-bench Verified<br>**Score:** 61.1 → 66.2<br>**Lift:** **+5.1 pp**<br><br>**Model trained:** MiMo-V2.6-Distill-Qwen-9B<br>**Benchmark:** SWE-bench Pro<br>**Score:** 44.6 → 47.6<br>**Lift:** **+3.0 pp**<br><br>**Model trained:** MiMo-V2.6-Distill-Qwen-9B<br>**Benchmark:** Terminal-Bench 2.1<br>**Score:** 37.1 → 52.8<br>**Lift:** **+15.7 pp** | **SWE-bench Verified**, **Terminal-Bench 2.x**, DeepSWE / general agent transfer |
| 5 | **SETA-Env** | **Full v48 run plus targeted reruns through v55: stages 1, 3, 4, 5**, 3,134 tasks. Combined after infrastructure retries, timeout rechecks and four nproc restorations: **3,083 pass all four**; 51 with findings; 19 earlier patch exclusions. **Stages 6, 7:** pass@16 submitted for Qwen3-30B Instruct and Thinking using the 3,083 retained tasks in PR #4 (revision `7c2f111ade1f3a79609023c8a23cd58372d935f9`). Authenticated pilots 949577/949580 are running; full runs 949579/949581 wait for successful pilots. Thinking uses two submission waves to respect the cluster queue limit; job 949582 submits the second wave. [Job status](/hnvme/workspace/y500bb12-seta-validation/experiments/teachers-20261007/jobs.json). [HF PR #4](https://huggingface.co/datasets/FWeindel/validated-tasks/discussions/4); [Results](/hnvme/workspace/y500bb12-seta-validation/experiments/ssh-isolation-20261007/effective-report/summary.json); [stage counts and findings](seta/README.md#validation-results). | **A** | 4,567 executable terminal environments | [HF](https://huggingface.co/datasets/camel-ai/SETA-Env) | [arXiv:2607.10891](https://arxiv.org/abs/2607.10891) | **Yes / Harbor-style** | **RL (GRPO)** | **Model trained:** DeepSeek-V4-Flash<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** pass@1 40 → 43; pass@5 54 → 58<br>**Lift:** **+3 pp pass@1; +4 pp pass@5**<br><br>**Model trained:** Qwen3-8B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** trained model = 12%; matched baseline not recorded in this table<br>**Lift:** **not available from the currently recorded evidence** | **Terminal-Bench 2.0**, **TBLite 2.0** |
| 6 | **CalibForge** | **20-task pilot complete, with findings** (job 923446). Stages **1, 3, 5**; **4 skipped** (no oracle). [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/calibforge-20-pilot.tar.zst).<br>Later repair pilot: 9/10 for stages 1, 3, 5; stage 4 not evaluated. | **A** | 5,431 executable terminal tasks | [HF](https://huggingface.co/datasets/AweAI-Team/CalibForge) | [arXiv:2608.06352](https://arxiv.org/abs/2608.06352) | Harbor-style | Post-training from task trajectories | **Model trained:** Qwen3-30B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** baseline → trained scores imply the reported absolute change below<br>**Lift:** **+24.71 pp**<br><br>**Model trained:** Qwen3-30B<br>**Benchmark:** SWE-bench Pro<br>**Score:** baseline → trained scores imply the reported absolute change below<br>**Lift:** **+27.68 pp**<br><br>**Model trained:** Qwen3-30B<br>**Benchmark:** Doc2Repo<br>**Score:** baseline → trained scores imply the reported absolute change below<br>**Lift:** **+30.04 pp**<br><br>**Model trained:** Qwen3.5-35B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** 39.10 → 47.57<br>**Lift:** **+8.47 pp** | **TB2**, **TBLite**, SWE transfer |
| 8 | **TMAX / TMax-15K-Harbor** | **20-task pilot complete, with findings** (job 923246). Stages **1, 3, 5**; **4 skipped** (no oracle). [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/tmax-20-pilot.tar.zst).<br>Later repair pilot: 0/10; quota blocker. | **A** | ~15k self-contained terminal tasks | [GitHub](https://github.com/hamishivi/tmax) | [arXiv:2606.23321](https://arxiv.org/abs/2606.23321) | **Yes — native / Harbor release** | **SFT + RL** | **Model trained:** multiple TMAX checkpoints<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** exact matched before → after values not recorded in this README yet<br>**Lift:** **not yet extracted** | **TB2**, **TBLite** |
| 10 | **TermiGen** | **20-task pilot complete, with findings** (job 923452). Stages **1, 3, 5**; **4 skipped** (no oracle). [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/termigen-20-pilot.tar.zst).<br>Later repair pilot: 0/10; needs review. | **A** | 3,500+ verified Docker environments | [GitHub](https://github.com/ucsb-mlsec/terminal-bench-env) | [arXiv:2602.07274](https://arxiv.org/abs/2602.07274) | **Yes — Harbor 2.0** | **SFT / trajectory training** | **Model trained:** TerminalGen-Qwen2.5-Coder-32B<br>**Benchmark:** TerminalBench<br>**Score:** trained model = 31.3%; matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence**<br><br>**Model trained:** multiple backbones<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** exact matched scores not recorded here<br>**Lift:** paper reports gains up to ~10 pp | **TB2**, **TBLite** |
| 13 | **FineEnvs / SmolDataEnvs-harbor-train** | **20-task pilot complete, with findings** (job 923628). Stages **1, 3, 5**; **4 skipped** (no oracle). [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/fineenvs-20-pilot.tar.zst).<br>Later repair pilot: 0/10; blocked. | **A** | 5,000 verified data-analysis RL tasks over real datasets; deterministic grading; held-out eval/test splits and verified SFT trajectories also released | [HF](https://huggingface.co/datasets/FineEnvs/SmolDataEnvs-harbor-train) | — | **Yes — native Harbor / OpenEnv** | **RL environment + SFT trajectories** | **Model trained:** small-model SmolDataEnvs experiments<br>**Benchmark:** held-out SmolDataEnvs evaluation<br>**Score:** exact matched before → after values not yet extracted into this README<br>**Lift:** **not yet extracted** | **DS-1000**, **BixBench CLI**, data-analysis / scientific-agent transfer |
| 14 | **FACET-Terminal-Tasks-6k** | **20-task pilot complete, with findings** (job 923624). Stages **1, 3, 4, 5**. [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/facet-20-pilot.tar.zst).<br>Later repair pilot: 6/10; needs review. | **A** | 6,078 validated terminal tasks | [Project](https://stokou.github.io/FACET-Terminal/) | [arXiv:2608.18580](https://arxiv.org/abs/2608.18580) | **Yes — Harbor tasks** | **SFT** | **Model trained:** Qwen3.5-4B<br>**Benchmark:** Terminal-Bench 2.1<br>**Score:** 17.60 → 24.72<br>**Lift:** **+7.12 pp**<br><br>**Model trained:** Qwen3.5-9B<br>**Benchmark:** Terminal-Bench 2.1<br>**Score:** 27.34 → 35.58<br>**Lift:** **+8.24 pp**<br><br>**Model trained:** Qwen3.5-27B<br>**Benchmark:** Terminal-Bench 2.1<br>**Score:** 40.82 → 47.57<br>**Lift:** **+6.75 pp** | **TB2**, **TBLite** |
| 15 | **Terminal-Lego-15k** | **20-task pilot complete, with findings** (job 923441). Stages **1, 3, 4, 5**. [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/terminallego-20-pilot.tar.zst).<br>Later repair queue stopped before a new pilot. | **A** | ~15k StackOverflow-grounded terminal tasks | [HF](https://huggingface.co/datasets/PrimeIntellect/Terminal-Lego-15k) | — | **Yes / TB-Harbor style** | Trajectory generation / terminal post-training | **Model trained:** —<br>**Benchmark:** —<br>**Score:** no clean matched base → trained result attributed to this exact release is recorded here<br>**Lift:** **—** | **TB2**, **TBLite** |
| 22 | **Repo2RLEnv-derived datasets** | **20-task pilot complete, with findings** (job 923463). Stages **1, 3, 4, 5**. [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/repo2rlenv-20-pilot.tar.zst).<br>Later repair queue stopped before a new pilot. | **A** | Multiple repo/PR/commit/CVE-derived RL tasksets | [GitHub](https://github.com/huggingface/Repo2RLEnv) | — | **Yes — outputs Harbor spec** | **RL environment generation** | **Model trained:** varies by downstream recipe<br>**Benchmark:** varies<br>**Score:** no single aggregate matched comparison applies to the framework<br>**Lift:** **—** | **SWE-bench Verified**, TB2-adjacent |
| 23 | **DevOps-Gym** | **20-task pilot complete, with findings** (job 923482). Stages **1, 3, 4, 5**. [archived results](/home/vault/y500bb/y500bb12/hnvme/y500bb12-optiagent/devopsgym-20-pilot.tar.zst).<br>Later repair queue stopped before a new pilot. | **A** | 700+ real-world DevOps tasks across 30+ Java/Go projects | [GitHub](https://github.com/ucsb-mlsec/DevOps-Gym) | [arXiv:2601.20882](https://arxiv.org/abs/2601.20882) | **Yes — Terminal-Bench / Harbor adapter**<br>[Harbor adapter](/data/horse/ws/frwe188h-trp-shared/harbor-marin/adapters/devopsgym/run_adapter.py) | Benchmark / executable training source | **Model trained:** —<br>**Benchmark:** —<br>**Score:** original work is primarily an environment/benchmark contribution; no matched training result recorded here<br>**Lift:** **—** | **TB2**, SWE-bench-adjacent |
| 37 | **NL2Bash TaskTrove source** | **20-task pilot complete, with findings** (job 923423). Stages **1, 3, 4, 5**. [results](/hnvme/workspace/y500bb12-optiagent/tasktrove-20-pilots/nl2bash/results/submissions/bfb8d8a91448/report/summary.json).<br>Later repair pilot: 3/10; needs review. | **A/B** | Underlying source used by OpenThoughts-Agent-v1-RL | [TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove) | — | **TaskTrove/Harbor conversion**<br>[OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/nl2bash/generate.py); [Marin normalizer](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/nl2bash.py) | **RL in OpenThoughts-Agent; SFT-capable** | **Model trained:** OpenThoughts-Agent model trained on the NL2Bash-derived RL subset<br>**Benchmark:** not yet normalized into this README<br>**Score:** exact matched before → after values not recorded here<br>**Lift:** **not yet extracted** | **TB2**, **TBLite** |
| 38 | **pymethods2test-large TaskTrove source** | **20-task pilot complete, with findings** (job 923424). Stages **1, 3, 5**; **4 skipped** (no oracle). [results](/hnvme/workspace/y500bb12-optiagent/tasktrove-20-pilots/pymethods2test/results/submissions/bf837d15a069/report/summary.json).<br>Later repair pilot: 0/10; needs review. | **A/B** | Underlying source used by OpenThoughts-Agent RL-5K | [TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove) | — | **TaskTrove/Harbor conversion**<br>[Marin normalizer](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/python_unit_tests.py) (Python pymethods2test templates; not Java methods2test) | **RL in OpenThoughts-Agent** | **Model trained:** OpenThoughts-Agent model trained on the pymethods2test-large-derived 5K subset<br>**Benchmark:** not yet normalized into this README<br>**Score:** exact matched before → after values not recorded here<br>**Lift:** **not yet extracted** | SWE / terminal-code |
| 39 | **BugsInPy / source-backed replacement** | **Source-backed replacement: stages 1, 3, 4 and 5 passed for retained validated project cohorts**, including documented infrastructure retries and exclusions ([project evidence](tasktrove_bugsinpy/README.md)). **Instruction review/rewrite pilot complete:** five-task CPU job **949473** finished in 12 minutes, with 5/5 accepted by the loop; independent review recommends prompt cleanup before scaling ([review](/home/vault/y500bb/y500bb12/OT_next_data/runs/bugsinpy/instruction-loop/opus55-medium-pilot5-20261007-144636/review/independent-review.json)). Claude Opus 5.5, medium effort ([run manifest](/home/vault/y500bb/y500bb12/OT_next_data/runs/bugsinpy/instruction-loop/opus55-medium-pilot5-20261007-144636/run.json)). Full-set instruction rewriting has not started.<br>Historical TaskTrove conversion: 20-task pilot (job 923425), stages 1/3/5 only; later retired after manual quality review. | **A/B** | Real Python bug-repair tasks | [GitHub](https://github.com/soarsmu/BugsInPy) / [TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove) | — | **Native BugsInPy replacement:** [`patch.py`](tasktrove_bugsinpy/patch.py) converts original repositories, tests and reference fixes to Harbor.<br>[Marin excludes its TaskTrove variant](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/source_verdicts.json) | SWE task source | **Model trained:** —<br>**Benchmark:** —<br>**Score:** no matched training result recorded here<br>**Lift:** **—** | SWE-bench-adjacent |
| 40 | **InferredBugs / TaskTrove conversion** | **Full stages 4/5 running (2026-10-07): 6,398 tasks per stage, four CPU shards each.** Image-preparation failures include Docker Hub pull-rate limits; final rewards pending. Historical stages 1/3 have 6,398-task passing evidence with mixed provenance; new image definitions are not yet fully validated. [Retry jobs](/hnvme/workspace/y500bb12-inferredbugs/stages45-retry-20261007/job-index.json); [partial results](/hnvme/workspace/y500bb12-inferredbugs/full-repatched-20261004/partial-results.json). | **A/B** | ~10k packaged bug-repair tasks in TaskTrove ecosystem | [TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove) | — | **TaskTrove/Harbor conversion**<br>[OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/inferredbugs/generate_with_verifier.py)<br>[Marin excludes its TaskTrove variant](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/source_verdicts.json) | SWE task source | **Model trained:** —<br>**Benchmark:** —<br>**Score:** no matched training result recorded here<br>**Lift:** **—** | SWE-bench-adjacent |
| — | **CrossCodeEval / TaskTrove conversion** | **Full run: 6,710/6,710 pass stages 1, 3, 4, 5** (job 923204; [results](/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/full-6710-dual-nop-20261002/submissions/02f9a91f9779/report/summary.json)).<br>**Stage 2:** 5-task review complete, with findings ([results](../validation/results/crosscodeeval/claude-login-5-v1/submissions/84b49e17e33b/report/summary.json)).<br>**Cached images:** all 6,710 tasks covered by three images in [HF PR #5](https://huggingface.co/datasets/FWeindel/validated-tasks/discussions/5), awaiting merge.<br>**Stages 6, 7:** pass@16 recovery running. Qwen3-30B Instruct: **107,360/107,360 scored attempts**, complete; Thinking: **103,364/107,360**, with 3,996 attempts in two running recovery jobs (October 7). Includes 194 recovered verifier scores of 0 after agent timeouts and one context-limit agent failure counted as 0 with its error retained. [Recovery summary](/hnvme/workspace/y500bb12-crosscodeeval-pilot/validation-smoke/pass16-retry-20261007/recovery-results.json).<br>**Stages 8, 9:** historical one-task demos; [evidence and numbering](#pilot-stage-coverage). | **A/B** | 6,710 cross-file code-completion tasks (C# 1,353, Java 1,895, Python 432, TypeScript 3,030) after the [patch](crosscodeeval/README.md) dropped 1,053 of 7,763 | [GitHub](https://github.com/amazon-science/cceval) / [TaskTrove](https://huggingface.co/datasets/open-thoughts/TaskTrove) | [arXiv:2310.11248](https://arxiv.org/abs/2310.11248) | **TaskTrove/Harbor conversion**<br>[local patcher](crosscodeeval/patch.py); [status](crosscodeeval/STATUS.md) | Benchmark / code-completion task source | **Model trained:** —<br>**Benchmark:** —<br>**Score:** no matched training result recorded here<br>**Lift:** **—** | Cross-file code completion; SWE-bench-adjacent |

## Needs Harbor conversion or reconstruction first

**22 sources.** Convert a ten-task sample first, then follow the validation workflow.
| Priority | Dataset / source | Status | Availability | Scale / artifact | Dataset / project | Paper | Harbor? | Original use | Published base → trained lift | Main expected eval target |
|---:|---|---|---|---|---|---|---|---|---|---|
| 7 | **Endless Terminals** | Not started | **A** | 3,255 generated executable tasks | [HF](https://huggingface.co/datasets/obiwan96/endless-terminals) | [arXiv:2601.16443](https://arxiv.org/abs/2601.16443) | Conversion / terminal-task format | **RL (PPO)** | **Model trained:** Llama-3.2-3B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** 0.0 → 2.2<br>**Lift:** **+2.2 pp**<br><br>**Model trained:** Qwen2.5-7B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** 2.2 → 3.4<br>**Lift:** **+1.2 pp**<br><br>**Model trained:** Qwen3-8B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** 1.1 → 6.7<br>**Lift:** **+5.6 pp** | **TB2**, **TBLite** |
| 9 | **Recursive Synthetic Terminal Tasks (RST)** | Not started | **A** | 37,484 validated terminal tasks | [HF](https://huggingface.co/datasets/Zhongzhi1228/Recursive-Task-Synthesis) | [arXiv:2608.05466](https://arxiv.org/abs/2608.05466) | Conversion / runnable task format | **SFT + PPO RL** | **Model trained:** Qwen3.5-27B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** trained model = 49.44%; matched baseline score not recorded here<br>**Lift:** paper reports **+20% relative**, not an absolute pp value<br><br>**Model trained:** multiple SFT checkpoints<br>**Benchmark:** TB2 / TB-Hard / Long-Horizon TB<br>**Score:** exact matched scores not recorded here<br>**Lift:** paper reports gains up to ~10 pp | **TB2**, **TBLite** |
| 11 | **TerminalTraj** | Not started | **B/C** | 32k Docker images, 50,733 verified trajectories | [HF](https://huggingface.co/datasets/m-a-p/TerminalTraj) / [GitHub](https://github.com/Wusiwei0410/TerminalTraj) | [arXiv:2602.01244](https://arxiv.org/abs/2602.01244) | No native Harbor | **SFT** | **Model trained:** TerminalTraj-32B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** trained model = 22.0%; matched baseline not recorded here<br>**Lift:** **not available for this checkpoint from the currently recorded evidence**<br><br>**Model trained:** multiple TerminalTraj backbones<br>**Benchmark:** Terminal-Bench 1 / Terminal-Bench 2.0<br>**Score:** exact matched scores not recorded here<br>**Lift:** paper reports up to **+20 pp TB1** and **+10 pp TB2** | **TB2**, **TBLite** |
| 12 | **CLI-Gym / CLI-Universe** | Not started | **A** | 1,655 executable repo-derived CLI tasks; CLI-Universe also releases 6k distilled trajectories | [GitHub](https://github.com/LiberCoders/CLI-Gym) | [arXiv:2606.22883](https://arxiv.org/abs/2606.22883) | No native Harbor | **SFT** | **Model trained:** Qwen3-32B fine-tuned on CLI-Universe-6K<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** trained model = 33.4%; matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence** | **TB2**, **TBLite** |
| 16 | **Nemotron-Terminal Corpus* (Snowball finetuned on this) | Not started | **C / partially reconstructable** | ~366k execution trajectories | [HF](https://huggingface.co/datasets/nvidia/Nemotron-Terminal-Corpus) | [arXiv:2602.21193](https://arxiv.org/abs/2602.21193) | Conversion / reconstruction: [OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/nemotron_terminal_corpus/generate.py) | **SFT** | **Model trained:** Nemotron-Terminal-8B<br>**Benchmark:** Terminal-Bench 2.0<br>**Score:** trained model ≈ 13%; exact matched baseline not recorded in this README<br>**Lift:** **not available from the currently recorded evidence** | **TB2**, **TBLite** |
| 17 | **SWE-Gym** | Not started | **A** | ~2.4k real SWE tasks | [HF](https://huggingface.co/datasets/SWE-Gym/SWE-Gym) / [GitHub](https://github.com/SWE-Gym/SWE-Gym) | [arXiv:2412.21139](https://arxiv.org/abs/2412.21139) | Conversion: [Harbor adapter](/data/horse/ws/frwe188h-trp-shared/harbor-marin/adapters/swegym/run_adapter.py); [OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/swegym/generate_patched.py)<br>[Marin excludes its TaskTrove variant](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/source_verdicts.json) | **SFT + RL** | **Model trained:** OpenHands-7B with online RL on SWE-Gym<br>**Benchmark:** SWE-bench Verified<br>**Score:** 11.0 → 14.6<br>**Lift:** **+3.6 pp**<br><br>**Model trained:** OpenHands LM-32B<br>**Benchmark:** SWE-bench Verified<br>**Score:** trained model = 37%; matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence** | **SWE-bench Verified**, **DeepSWE** |
| 18 | **SWE-smith** | Not started | **A** | ~50k scalable synthetic bug-fixing environments over real repos | [HF](https://huggingface.co/datasets/SWE-bench/SWE-smith) / [GitHub](https://github.com/SWE-bench/SWE-smith) | [arXiv:2504.21798](https://arxiv.org/abs/2504.21798) | Conversion: [Harbor adapter](/data/horse/ws/frwe188h-trp-shared/harbor-marin/adapters/swesmith/src/swesmith_adapter/main.py); [OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/swesmith/generate.py)<br>[Marin normalizer](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/swe_trusted_paths.py) (TaskTrove SWE-smith) | **SFT; RL in downstream work** | **Model trained:** SWE-agent-LM-32B<br>**Benchmark:** SWE-bench Verified<br>**Score:** trained model ≈ 40%; exact matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence** | **SWE-bench Verified**, **DeepSWE** |
| 19 | **OpenSWE / daVinci-Env** | Not started | **A** | ~45k executable SWE environments across 12.8k+ repos | [HF](https://huggingface.co/datasets/GAIR/OpenSWE) / [GitHub](https://github.com/GAIR-NLP/OpenSWE) | — | Conversion: [OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/openswe/generate.py)<br>[Marin excludes its TaskTrove variant](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/source_verdicts.json) | **SFT** | **Model trained:** SWE-Agent-32B using OpenSWE instead of SWE-rebench data<br>**Benchmark:** SWE-bench Verified<br>**Score:** 50.2 → 62.4<br>**Lift:** **+12.2 pp**<br><br>**Model trained:** CodeAct-32B using OpenSWE instead of SWE-rebench data<br>**Benchmark:** SWE-bench Verified<br>**Score:** 51.4 → 59.8<br>**Lift:** **+8.4 pp** | **SWE-bench Verified**, **DeepSWE** |
| 20 | **SWE-rebench V2** | Not started | **A/B** | ~32k issue/PR tasks across ~20 languages | [HF](https://huggingface.co/datasets/PrimeIntellect/SWE-rebench-V2) | — | Conversion: [OT V2 converter](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/patchers/patch_swe_rebench_v2_tasks.py) (nebius upstream; check Prime fork compatibility)<br>[Marin normalizer](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/swe_patched.py) (TaskTrove V2) | Environment/data generation; SFT downstream | **Model trained:** —<br>**Benchmark:** —<br>**Score:** no clean matched base → trained result is recorded here<br>**Lift:** **—** | **SWE-bench Verified**, **DeepSWE** |
| 21 | **R2E-Gym / AgentGym** | Not started | **A** | ~8.7k procedurally curated executable SWE tasks | [HF](https://huggingface.co/datasets/R2E-Gym/R2E-Gym-V1) / [GitHub](https://github.com/R2E-Gym/R2E-Gym) | [arXiv:2504.07164](https://arxiv.org/abs/2504.07164) | Conversion: [OT generator](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/r2egym/generate.py)<br>[Marin excludes its TaskTrove variant](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/source_verdicts.json) | **SFT / agent training; RL in follow-ups** | **Model trained:** R2E-Gym-32B<br>**Benchmark:** SWE-bench Verified<br>**Score:** trained model = 34.4% pass@1; matched baseline not recorded here<br>**Lift:** **not available from the currently recorded evidence** | **SWE-bench Verified**, **DeepSWE** |
| 24 | **MindForge** | Not started | **A/C** | 562 cleanroom executable environments + trajectories | [HF](https://huggingface.co/datasets/centre-for-swe/MindForge-27B-Training-Trajectories) | [arXiv:2607.27146](https://arxiv.org/abs/2607.27146) | No native Harbor | **SFT** | **Model trained:** Qwen3.6-27B<br>**Benchmark:** ProgramBench<br>**Score:** 37.98 → 49.51<br>**Lift:** **+11.53 pp**<br><br>**Model trained:** Qwen3.6-27B<br>**Benchmark:** DeepSWE<br>**Score:** exact before → after scores not recorded here<br>**Lift:** **+14.16 pp**<br><br>**Model trained:** Qwen3.6-27B<br>**Benchmark:** SWE-bench Verified<br>**Score:** exact before → after scores not recorded here<br>**Lift:** **+5.04 pp** | **DeepSWE**, SWE-bench, long-horizon coding |
| 25 | **AgentWorldModel-1K** | Not started | **A** | 1,000 executable environments / ~10k tasks | [HF](https://huggingface.co/datasets/Snowflake/AgentWorldModel-1K) / [GitHub](https://github.com/Snowflake-Labs/agent-world-model) | [arXiv:2602.10090](https://arxiv.org/abs/2602.10090) | **No — OpenEnv/MCP** | **RL** | **Model trained:** Arctic-AWM models<br>**Benchmark:** adapted BFCL / τ²-bench / MCP-Universe<br>**Score:** exact matched before → after scores not recorded in this README<br>**Lift:** **not yet extracted** | **BFCL-Parity**, **τ³/τ-family**, tool use |
| 26 | **EnvScaler** | Not started | **A** | 191 environments, ~7k scenarios; 2,550 RL scenarios | [HF](https://huggingface.co/datasets/XXHStudyHard/EnvScaler-RL-Scenario) / [GitHub](https://github.com/RUC-NLPIR/EnvScaler) | [arXiv:2601.05808](https://arxiv.org/abs/2601.05808) | No | **SFT + RL** | **Model trained:** EnvScaler-trained models<br>**Benchmark:** BFCL / TauBench / ACEBench<br>**Score:** exact matched before → after scores not recorded in this README<br>**Lift:** **not yet extracted** | **BFCL**, **τ³/τ-family** |
| 27 | **NVIDIA Nemotron-RL-agent-calendar_scheduling** | Not started | **A** | ~4k released stateful calendar-scheduling RL environment examples | [HF](https://huggingface.co/datasets/nvidia/Nemotron-RL-agent-calendar_scheduling) | — | Conversion: [OT calendar converter](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/nemotron_gym/converters/agent_calendar.py)<br>[Marin normalizer](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/agent_calendar.py) (TaskTrove final-state calendar tasks) | **RL** | **Model trained:** Nemotron RLVR models<br>**Benchmark:** calendar / agentic tool-use evaluations<br>**Score:** exact source-specific matched before → after result not isolated in this README<br>**Lift:** **not yet extracted** | **τ³ / τ-family**, general tool use, scheduling agents |
| 28 | **NVIDIA Nemotron-RL-instruction_following** | Not started | **A** | ~750 released verifiable instruction-following RL environment examples | [HF](https://huggingface.co/datasets/nvidia/Nemotron-RL-instruction_following) | — | Conversion: [OT instruction-following converter](https://github.com/open-thoughts/OpenThoughts-Agent/blob/3bd1917e62c9d03d73063b433f5c442c279c0563/data/nemotron_gym/converters/instruction_following.py)<br>[Marin normalizer](https://github.com/marin-community/marin/blob/16ed2b63cdf810fc444930138ac3a35f53260e71/experiments/post_training/tasktrove/converters/nemotron_ifeval.py) (TaskTrove IFEval template) | **RL** | **Model trained:** Nemotron RLVR models<br>**Benchmark:** instruction-following evaluations<br>**Score:** exact source-specific matched before → after result not isolated in this README<br>**Lift:** **not yet extracted** | **IFBench**, general instruction following; secondary agent robustness |
| 29 | **MLE-Dojo** | Not started | **A** | 200+ executable Kaggle challenge environments | [GitHub](https://github.com/MLSysOps/MLE-Dojo) | [arXiv:2505.07782](https://arxiv.org/abs/2505.07782) | No | **SFT + RL capable** | **Model trained:** —<br>**Benchmark:** —<br>**Score:** original work focuses on executable ML environments/framework evaluation rather than one canonical matched training comparison<br>**Lift:** **—** | **DS-1000**, **TB-Science**, ML engineering |
| 30 | **MLE-bench** | Not started | **A** | 75 Kaggle ML-engineering competitions | [GitHub](https://github.com/openai/mle-bench) | [arXiv:2410.07095](https://arxiv.org/abs/2410.07095) | No | Benchmark / environments | **Model trained:** —<br>**Benchmark:** —<br>**Score:** benchmark paper; no canonical base → trained post-training comparison<br>**Lift:** **—** | **DS-1000**, **TB-Science**, **BixBench CLI** |
| 31 | **KernelBench** | Not started | **A** | Public GPU kernel optimization tasks | [HF](https://huggingface.co/datasets/ScalingIntelligence/KernelBench) | [arXiv:2502.10517](https://arxiv.org/abs/2502.10517) | No | Benchmark / optimization; training in follow-ups | **Model trained:** — in the original benchmark paper<br>**Benchmark:** KernelBench<br>**Score:** post-training gains belong to downstream papers rather than the original benchmark release<br>**Lift:** **— for original release** | **TB-Science** / discovery |
| 32 | **AlgoTune** | Not started | **A/B** | 154 executable numerical-program optimization tasks | [GitHub](https://github.com/oripress/AlgoTune) | [arXiv:2507.15887](https://arxiv.org/abs/2507.15887) | Conversion: [Harbor adapter](/data/horse/ws/frwe188h-trp-shared/harbor-marin/adapters/algotune/src/algotune/main.py) | Optimization / discovery | **Model trained:** —<br>**Benchmark:** AlgoTune objective suite<br>**Score:** original work evaluates optimization/search rather than a matched policy-model post-training comparison<br>**Lift:** **—** | **TB-Science** / discovery |
| 33 | **TerminalWorld (recorded-session benchmark)** | Not started | **A/B** | 1,530 validated real-world terminal tasks | [HF](https://huggingface.co/datasets/EuniAI/TerminalWorld) | [arXiv:2605.22535](https://arxiv.org/abs/2605.22535) | Not native | Benchmark / task reconstruction | **Model trained:** —<br>**Benchmark:** TerminalWorld<br>**Score:** benchmark/reconstruction dataset; no training lift recorded<br>**Lift:** **—** | Terminal generalization |
| 36 | **Terminal Wrench** | Not started | **A — special-purpose** | 331 reward-hackable environments + 3,632 exploit trajectories + legitimate baselines | [GitHub](https://github.com/few-sh/terminal-wrench) | [arXiv:2604.17596](https://arxiv.org/abs/2604.17596) | Terminal-benchmark-style | Auditing / adversarial trajectories | **Model trained:** —<br>**Benchmark:** verifier/reward-hacking robustness<br>**Score:** special-purpose audit dataset rather than a standard capability-training comparison<br>**Lift:** **—** | **Verifier robustness / reward hacking** |
| 41 | **RST trajectory corpus** | Not started | **C** | ~327k trajectories corresponding to RST executable tasks | [HF trajectory release](https://huggingface.co/datasets/Zhongzhi1228/Recursive-Task-Synthesis-Trajectories) | [arXiv:2608.05466](https://arxiv.org/abs/2608.05466) | N/A; underlying tasks runnable | **SFT** | **Model trained:** same models as the RST executable-task row<br>**Benchmark:** same RST evaluations<br>**Score:** trajectory corpus corresponds to the same underlying task family<br>**Lift:** **do not count separately from RST** | **TB2**, **TBLite** |

## Dataset code

Each source has one executable `data/<source>/patch.py`. Keep source-specific
repairs, preparation, and conversion in that module. Small results, example
tasks, configuration, and source documentation can stay beside it. Large inputs
and outputs belong under `$OT_WORKSPACE/datasets/<source>/`.

Run from the repository root with `python -m data.<source>.patch`, or invoke
`python data/<source>/patch.py` directly. Existing patch arguments are preserved.
Use `--help` for patch arguments and `COMMAND --help` for an additional command.

| Source | Additional commands |
| --- | --- |
| CrossCodeEval | `prepare`, `warmup`, `rewards` |
| SETA | `download` (existing `audit` also remains) |
| FACET | `build-source`, `prepare-pilot` |
| MiMo, DevOps-Gym | `build-full`, `prepare-pilot` |
| CalibForge, TMAX, TermiGen | `prepare-pilot` |
| BugsInPy | `instruction-loop` (wrapper around the shared instruction repair utility) |

For example:

```bash
python -m data.facet.patch build-source release.zip "$OT_WORKSPACE/datasets/facet/tasks.parquet"
python -m data.crosscodeeval.patch prepare --help
python -m data.seta.patch download --root "$OT_WORKSPACE/datasets/seta"
```

InferredBugs now uses `data/inferredbugs/patch.py`; the old versioned script name
has been retired. The optional BugsInPy Slurm launcher is
`hpc/helma/bugsinpy_instruction_loop.sbatch`.

### Shared utilities

`data/utils/` contains code used across sources. See [the reusable repair guide](Readme.md) for workflows, defaults and limitations:

- `instruction_loop.py`: review and rewrite task instructions with shared prompts; BugsInPy supplies its placeholder marker through a thin wrapper.

- `resolve_absolute_paths.py`: observe paths inside task environments and apply
  explicitly reviewed replacements. Used by validation as well as patchers;
  see [path resolution](utils/resolve_absolute_paths.md).
- `patch_reporting.py`: write archive Parquets and change manifests; see
  [patch reporting](#reporting-patches-in-the-datasource-pr).
- `full_source/`: pinned Prime conversion, grader templates, Parquet packaging,
  and immutable-source copying; see [full-source inputs](utils/full_source/README.md).
- `pack_git_harbor_source.py`: package a pinned Git task tree.
- `prepare_hf_tree_pilot.py`, `prepare_tasktrove_pilots.py`: shared source sampling.
- `maven_proxy.py`: Maven settings for cluster proxies.
- `task_archive.py`: read task archives without extracting them to disk.
- `cli.py`: dispatch dataset subcommands while preserving patch arguments.

Invoke utility commands as modules, for example
`python -m data.utils.pack_git_harbor_source --help`.

The old `submit_tasktrove_pilots.py` and `submit_hf_tree_pilot.py` wrappers are
removed: use `validation/run.py` to select tasks, create a contract, and submit
it. This avoids source-specific Slurm settings and fixed 20-task restrictions.
The old `refresh_pilot_status.py` was tied to historical workspace paths and
could overwrite newer inventory findings; it is removed too. The result links in the source rows retain the supporting evidence. Record new results
from the shared validation reports, following the
[validation workflow](../validation/README.md).

## Reporting patches in the datasource PR

A patcher produces three artifacts:

- `tasks.parquet`: the final patched tasks to validate.
- `archive.parquet`: the **original payloads** of deliberately dropped tasks,
  with `archive_category` and `archive_reason`.
- `patch-manifest.json`: the source dataset/link/revision, per-task comparison,
  optional change labels, and patch version information.

The manifest is generated by code, not written by hand. Detailed explanations
can stay in the patcher's Git README. The datasource PR contains statistics,
labels and the upstream link. Its run JSON contains the per-task evidence.

Users run only the patch script. `write_patch_report` is an internal function
called by that script, not a second command or a report users must fill in.
The patcher collects labels and drop explanations while applying its rules.
Here, “category” means the short **drop label**, such as
`environment-task-mismatch`; “reason” is the accompanying plain-language detail.

By default the function puts both generated files beside the patched Parquet:
`tasks.parquet`, `tasks.archive.parquet`, and `tasks.manifest.json`. Choose the
Parquet's output directory once in the patcher command. This keeps separate
runs together without mixing generated datasets into the patch source directory.
If the Parquet is written beside the patch script, its other outputs go there too.

### Adapting a patch script

Inside the patcher, keep writing the patched Parquet as before. Track intentional
drops by task ID and optionally track change labels as each rule runs. After
finishing the patched Parquet, the patcher itself calls this function:

```python
from data.utils.patch_reporting import write_patch_report

write_patch_report(
    original="original.parquet",
    patched="tasks.parquet",
    source={
        "dataset": "org/dataset",
        "url": "https://huggingface.co/datasets/org/dataset/blob/COMMIT/tasks.parquet",
        "revision": "COMMIT",
    },
    dropped={
        "task-003": {
            "category": "environment-task-mismatch",
            "reason": "The supplied environment cannot support the task's required service.",
        },
    },
    change_labels={
        "task-001": ["instruction-clarified", "verifier-fixed"],
        "task-002": ["dependencies-pinned"],
    },
    patches=[{"version": "v2", "readme": "https://github.com/ORG/REPO/blob/COMMIT/data/DATASET/README.md"}],
)
```

Run/import with the repository root on `PYTHONPATH`. Alternatively, put labels
in an optional `patch_labels: list<string>` column of the patched Parquet;
`change_labels` entries override that column. Use `[]` for unlabeled tasks.
No column per individual label is needed.

The helper computes changed tasks and files from the actual original/final
payloads, including file modes and ignoring tar timestamps/order. Labels are
optional for changed tasks; every drop requires a category and explanation.
Counts are unique tasks, not edits. A task with multiple labels counts once
under each label, and once in the total changed count. Instruction changes are
reported as such; they are not assumed to be complete rewrites.

Pass the original pinned source and the final result after all patches, so the
comparison is cumulative. The source URL/revision are supplied by the patcher;
the helper cannot establish that a local file came from a claimed remote revision.
Task IDs must be preserved: this helper rejects additions/renames rather than
guessing correspondence. Use new artifact paths on each run.

SETA, CrossCodeEval and InferredBugs call this helper automatically, emitting
`<output>.archive.parquet` and `<output>.manifest.json` alongside existing reports.
InferredBugs' pipeline `package` command uses the same reporting function.
CrossCodeEval `--pin-only` propagates the cumulative comparison when its input
has a new-format manifest and the recorded original input is still available.
Legacy pin-only inputs without a manifest still produce only the patched Parquet;
run the full patcher first to establish the source comparison.

The BugsInPy converter writes `tasks.archive.parquet` and `tasks.manifest.json`
per project, keeping its existing detailed `manifest.json` and legacy archive.
It marks its comparison as a conversion: `action: changed` with
`converted-to-harbor` means native BugsInPy records became Harbor tasks. Its
archived payloads are generated Harbor packages, not pre-existing source packages.
Conversion errors and unresolved InferredBugs decisions make a manifest incomplete
and prevent publication of a misleading complete-source comparison.

These patchers assign change labels based on files actually changed. Inside a
patcher you can additionally pass `change_labels={task_id: [label, ...]}` and
`change_reasons={task_id: "explanation"}`. A kept, changed task receives
`action: "changed"`; adding labels never moves it into the archive.

### Passing the artifacts to validation/publishing

Validate **only `tasks.parquet`**. At contract preparation, pass:

```text
--publish-conversion-archive FOLDER=/absolute/path/archive.parquet
--publish-patch-manifest FOLDER=/absolute/path/patch-manifest.json
```

For standalone `validation/publishing/publish.py`, the corresponding arguments are
`--conversion-archive FOLDER=PATH` and `--patch-manifest FOLDER=PATH`.
`FOLDER` must match the existing `--publish-folder` / `--folder` mapping.
Files must remain accessible on the publishing worker.

Publishing combines patch drops and validation exclusions into the final
`archive.parquet`. Patch drops have stage 0 and `patch-drop:<category>` labels;
they are not rerun through validation. Rows also retain `archive_labels`,
`validation_labels`, `patch_labels`, `patch_changed`, and `source_task_id`.

The PR reports total retained/archived tasks, archive labels, changed/unchanged/
dropped counts against upstream, and optional change-label counts. Patch-changed
tasks can subsequently be archived by validation; these are overlapping views,
not counts to add together. The manifest must cover the complete selected source
and match every published task's payload; attaching a full manifest to a pilot
is rejected.

The PR's `runs/<run>.json` also keeps each task's validation findings. When a
stage report supplies `failure_label`, `failure_explanation`, `build_stability`,
`build_attempts` or `recheck_evidence`, publishing includes them in `task_findings`.
These fields are optional; no extra flag is needed.

Generated datasource READMEs describe the source, tasks, domains, capabilities
and likely benchmark transfer. Validation results, patch statistics, archive
labels and runtime limitations belong in the PR and its run JSON.
Existing cards retain the current preservation policy: use `--readme-force`
(`--publish-readme-force` for automatic publishing) with README generation to
refresh them. PR statistics are generated on every publication regardless.
