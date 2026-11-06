# LLM-as-a-Judge Filtering for RL Environments: Preliminary Results

**Main research question:** Which defect criteria are worth filtering to improve downstream RL performance?

Before running RL filtering ablations, we need to know whether the labels are reliable. Otherwise, a criterion might appear unhelpful simply because the rubric, prompting strategy, or judge assigns incorrect labels. These preliminary results aim to answer/make labels relaible. 

## RQ0: Can we build a reliable, minimally subjective ground-truth dataset?

We need ground truth labels that are not subjective but have an obvious defect to assess rubric quality, prompting strategy, and judge accuracy (RQ1-RQ3). Otherwise, disagreements may reflect genuinely debatable task requirements rather than a bad rubric or judge.

Existing benchmark audits give human-labeled clean and defective tasks, but our initial evaluations were often uninformative (sometimes near chance). One possible reason is that many audited benchmark tasks are already high-quality and have subtle, task-specific defects that even humans may disagree on. We do not want to specify every such edge cases into the rubric (too much work and too task specifc).

We therefore curated the [task-defect ground-truth dataset](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth) from the following sources:

- **Clean (PASS):** Initially **66 SWE-bench Verified tasks** from the [OpenAI audit](https://openai.com/index/introducing-swe-bench-verified/), with unanimous agreement among three human reviewers and **5/5 confidence** on instruction clarity and test fairness. Follow-up inspection identified **four previously missed defects**, so we excluded those tasks. **Two additional cases remain PASS but are flagged as subjective**. The resulting set has **62 PASS tasks** (60 without the subjective flag).
- **Defective (FAIL):** Documented, reasonably objective defects from published benchmark audits and papers on auditing/curating RL environments, including [*Hardening Agent Benchmarks with Adversarial Hacker-Fixer Loops*](https://arxiv.org/abs/2606.08960), [*Learning Generalizable Behaviors for Terminal Agents*](https://arxiv.org/abs/2608.22631), [*Automated Benchmark Auditing*](https://arxiv.org/abs/2605.26079), [Terminal-Bench reviews](https://epoch.ai/benchmarks/terminal-bench-4/review), and TMax v1.1. We manually reviewed most cases. 
- **Additional PASS cases:** Hand-reviewed boundary examples where the judge should *not* report a defect: e.g., an agent can fix a missing service without inventing unavailable resources (`INSTR-ENV-MISMATCH`), or a weak-verifier issue where the only way to exploit it is by tampering with the verifier, rather than submitting a plausible but incorrect solution (`VERIFIER-TOO-WEAK`).

We excluded cases that could not be converted to Apptainer. 

## Setup

- **Judges:** Sonnet 5.5 and Qwen3.8-27B, each run **three times per task**.
- **Labeling:** Evaluate each rubric criterion independently as PASS/FAIL.
- **Classification:** Select one defect category or `CLEAN`.
- **Metrics:** Within-judge agreement across runs and accuracy against gt labels (plus TP/FN/FP/TN). 
- All reported judge results use the **selected River rubric saved with the runs**. Some refinements below were already included; later additions are marked as not yet evaluated.

## RQ1 — Can we build a rubric that produces consistent, minimally subjective judgments?

Using the River rubric, we measure how often all six Sonnet reviews agree: three labeling and three classification runs per task.

| Criterion / decision | Agreement |
|---|---:|
| CLEAN (all criteria) | 48/60 (**80.0%**) |
| CLEAN (excluding answer leakage and triviality) | 58/60 (**96.7%**) |
| VERIFIER-TOO-WEAK | 68/76 (**89.5%**) |
| INSTR-VERIFIER-MISMATCH | 156/177 (**88.1%**) |
| INSTR-AMBIGUOUS | 65/73 (**89.0%**) |
| INSTR-ENV-MISMATCH | 104/119 (**87.4%**) |

Looking at the disagreements showed confusion between answer leakage and triviality, triviality and CLEAN, and ambiguity and mismatch. A suggested implementation can be seen as either leaking the answer or making the task trivial, so we suggest considering these together for filtering. Whether a task is trivial is itself subjective and depends on the model being trained. We clarified the ambiguity/mismatch boundary in the rubric below, but have not yet tested whether this reduces disagreement.

### Changes made to the rubric (including changes already used in these experiments)

#### VERIFIER-TOO-WEAK: separate incomplete work from shortcuts

We split this criterion into two categories <span style="color: #d1242f;">(not yet evaluated)</span>:

- **VERIFIER-ACCEPTS-INCOMPLETE:** A solution completes only part of the requested work, but the verifier accepts it.
- **VERIFIER-ACCEPTS-SHORTCUTS:** A fabricated output or hardcoded answer passes without solving the task. 

Tasks with incomplete verification may still be useful for training because the agent must perform meaningful work. For example, if a task combines two smaller tasks but the verifier checks only the first, the agent can still learn from solving that first task. Extending the verifier to check both would effectively **harden the task**, much like combining several smaller tasks into a harder one. For example, `portfolio-optimization` checks numerical correctness and speed but does not check that the implementation uses C. A fast, correct NumPy implementation could still involve useful work, even though it misses a stated requirement.

For both new criteria, we added **“Assign FAIL only if you demonstrate”** that the incomplete solution or shortcut passes **“without tampering with the tests or scoring”** <span style="color: #d1242f;">(not yet evaluated)</span>. In some disagreements, the judge assigned FAIL based on a hypothetical example. We then tried implementing it with GPT-6 Astra: it either did not work or required substantial work comparable to solving the task, suggesting that an agent would be unlikely to exploit it.

For `VERIFIER-ACCEPTS-INCOMPLETE`, we also added: “Check that relevant tests affect the final score, not just that they are present or executed” <span style="color: #d1242f;">(not yet evaluated)</span>, because judges sometimes confused test presence or execution with actual grading coverage.

#### INSTR-VERIFIER-MISMATCH: clarify arbitrary or contradictory requirements

We changed “checks something the instruction does not imply” to **“checks an arbitrary requirement”** <span style="color: #d1242f;">(not yet evaluated)</span> to distinguish mismatch from ambiguity: choosing between reasonable edge-case behaviors (e.g., returning `None` or raising an exception) is ambiguity; requiring an exact, unspecified error message is mismatch.

We removed “A solution that faithfully follows the instruction still fails” <span style="color: #d1242f;">(not yet evaluated)</span> because this can also describe ambiguity: a solution may follow a reasonable interpretation of the instruction and still fail.

We added “rejects build artifacts created by the instruction’s own compilation commands” <span style="color: #d1242f;">(not yet evaluated)</span> based on disagreements over `polyglot-rust-c`. The task asks for a single source file, `main.rs`, but the supplied compilation commands create executable files alongside it. The verifier rejects those additional files. Following the supplied compilation commands creates additional files, so the verifier rejects the resulting directory unless the agent deletes those build artifacts.

We also added these examples: “requires specific output keys not stated in the instruction, or requires exact warning/error messages or other prose wording that the instruction does not specify” <span style="color: #1a7f37;">(already included in the experiments)</span>. 

The following file-scope clarification is **proposed, but not yet implemented** in either selected rubric:

> Do not flag tasks where the instruction points to specific files, but the verifier expects the same requested change in additional files or code paths, unless the instruction explicitly restricts changes to those files.

For example, `django-14376` asks to replace deprecated MySQL parameter names (`db` → `database`, `passwd` → `password`) and points to the database connection file. The verifier also expects the corresponding update in the file used to launch the MySQL command-line client. The judge flagged this as an unstated requirement because the instruction did not name that second file. However, the instruction does not restrict changes to the named file: finding and updating other code that uses the same parameters may reasonably be part of completing the requested change. We may therefore want to keep this task rather than flag it as a mismatch. But unsure if the proposed text makes this clear without being too specifc to this example. 

#### INSTR-AMBIGUOUS: clarify unspecified edge-case behavior

We added: “For example, the expected behavior for an edge case is unclear, and the verifier accepts only one of several reasonable behaviors” <span style="color: #d1242f;">(not yet evaluated)</span>. This makes cases such as `django-14534` (returning `None` versus raising `KeyError` when no widget ID exists) should be ambiguity not mismatch. 

#### INSTR-ENV-MISMATCH: distinguish fixable setup from missing resources

We added an exception for fixable setup issues: “Do not flag if the agent can resolve the issue by installing, configuring, or starting available software or services” <span style="color: #1a7f37;">(already included in the experiments as “available components”)</span>. We clarified what should still be flagged: “Flag if resolving it requires inventing missing data or implementing a service or program that the instructions assume is already provided” <span style="color: #1a7f37;">(already included in the experiments)</span>.

We also added: “Check resources needed for grading as well as solving” <span style="color: #d1242f;">(not yet evaluated)</span>. Some disagreements was because somethimes the judge assumed grading resources were available, other times it explcitly checked. 

## RQ2 — Does the prompting strategy matter (labeling vs. classification)?

CLEAN shows how often the judge correctly passes the 60 clean tasks; each defect column shows how often it identifies that specific defect. All rates average three reviews per task.

| Judge / strategy | CLEAN | Weak verifier | Verifier mismatch | Ambiguity | Environment mismatch |
|---|---:|---:|---:|---:|---:|
| Sonnet 5.5 / Labeling | 178/180 (98.9%) | 32/48 (66.7%) | 305/351 (86.9%) | 26/39 (66.7%) | 149/177 (84.2%) |
| Sonnet 5.5 / Classification | 180/180 (100.0%) | 25/48 (52.1%) | 282/351 (80.3%) | 16/39 (41.0%) | 149/177 (84.2%) |
| Qwen3.8-27B / Labeling | 167/180 (92.8%) | 38/48 (79.2%) | 201/351 (57.3%) | 13/39 (33.3%) | 172/177 (97.2%) |
| Qwen3.8-27B / Classification | 158/180 (87.8%) | 22/48 (45.8%) | 180/351 (51.3%) | 10/39 (25.6%) | 168/177 (94.9%) |

*CLEAN excludes answer leakage and triviality because some reference-PASS tasks contained Git-history leakage, already covered by a rule-based check. It also excludes environment mismatch because we blocked network access for the judge so it could not look up labels, but this broke setup scripts needing internet. Defect columns use the same cases as RQ1, excluding the two known multi-defect tasks. Environment-mismatch results can reflect our runtime restrictions.*

**Answer:** Labeling detects the reference defects more often, but the comparison is confounded by labeling allowing multiple categories while classification selects only one.

## RQ3 — Does the choice of judge matter? Can a cheaper judge be reliable?

Here we hold the prompting strategy fixed at **labeling** and compare Sonnet with Qwen on the same tasks.

**Known defects correctly identified, using the majority of three reviews:**

| Judge / format | Weak verifier | Verifier mismatch | Ambiguity | Environment mismatch |
|---|---:|---:|---:|---:|
| Sonnet 5.5 / Labeling | 11/16 (68.8%) | 101/117 (86.3%) | 9/13 (69.2%) | 51/59 (86.4%) |
| Qwen3.8-27B / Labeling | 12/16 (75.0%) | 67/117 (57.3%) | 5/13 (38.5%) | 58/59 (98.3%) |

**Clean tasks correctly passed for each criterion, using the same majority rule:**

| Judge / format | Weak verifier | Verifier mismatch | Ambiguity | Environment mismatch |
|---|---:|---:|---:|---:|
| Sonnet 5.5 / Labeling | 60/60 (100.0%) | 60/60 (100.0%) | 60/60 (100.0%) | 60/60 (100.0%) |
| Qwen3.8-27B / Labeling | 59/60 (98.3%) | 59/60 (98.3%) | 60/60 (100.0%) | 60/60 (100.0%) |

**Agreement across three runs, on each defect set plus the same 60 clean tasks:**

| Judge / format | Weak verifier | Verifier mismatch | Ambiguity | Environment mismatch |
|---|---:|---:|---:|---:|
| Sonnet 5.5 / Labeling | 70/76 (92.1%) | 166/177 (93.8%) | 72/73 (98.6%) | 111/119 (93.3%) |
| Qwen3.8-27B / Labeling | 71/76 (93.4%) | 128/177 (72.3%) | 69/73 (94.5%) | 113/119 (95.0%) |

**Answer:** Qwen is less likely to flag mismatch or ambiguity because it often assumes missing details can be inferred from the codebase or the intended fix, but it catches more weak-verifier and environment issues. Both judges usually pass clean tasks correctly.

## Estimated costs

The histogram includes **1,782 reviews per model**: 297 matched tasks × 3 runs × 2 prompting strategies. Three of the original 300 common tasks were excluded from both models because Sonnet usage logs were incomplete.

<img src="cost_estimates/token_distribution.png" alt="Distribution of total tokens per review for Sonnet and Qwen" width="700">

Total tokens include input, cached input and output across all calls in a review. The horizontal axis is logarithmic; dashed lines mark medians.

**API prices in USD per million tokens:**

| Model | Uncached input | Cached input read | Cache write | Output |
|---|---:|---:|---:|---:|
| Sonnet 5.5 | $2.00 | $0.10 | $2.50 (5 min) / $4.00 (1 hour) | $10.00 |
| Qwen3.8-27B | $0.50 | $0.10 (implicit caching) | No separate charge for implicit caching | $3.00 |

Rates checked November 6, 2026: [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing) and [Alibaba Cloud, Singapore/international](https://www.alibabacloud.com/help/en/model-studio/qwen3-8-27b). Sonnet’s logs use one-hour cache writes. Twelve reviews also contain Sonnet 5 usage, charged at its $0.20/M cache-read rate; its other rates are the same.

**Estimated cost per review, including caching:**

| Judge | Labeling | Classification |
|---|---:|---:|
| Sonnet 5.5 | **$0.0625** | **$0.0562** |
| Qwen3.8-27B | **$0.2495** | **$0.1909** |

Unexpectedly, Qwen is more expensive per review because it uses far more tokens: an average of 1,055,710 input and output tokens, compared with 101,770 for Sonnet, across both prompting strategies.

## Why the criteria themselves need RL ablations

Even an accurately detected defect may not justify filtering a task:

| Criterion | Example / open question |
|---|---|
| Environment mismatch | If an agent can simply start a missing service, doesn't it make the task just harded? ([TMax example](https://hub.harborframework.com/tasks/tmax/task_001197_680abe08)) |
| Weak verifier | If the task rewards correct and fast NumPy code but not the requested C implementation, might it still teach useful behavior? ([Portfolio optimization](https://hub.harborframework.com/tasks/terminal-bench/portfolio-optimization)) |
| Instruction–verifier mismatch | Could discovering additional related code paths be part of solving the task, rather than a defect? (`django-14376`) |
| Ambiguity | Does requiring a reasonable implicit edge-case behavior actually harm learning? (`django-14534`) |
| Triviality | A task trivial for a strong judge may still challenge the model being trained. |
| Answer leak | Suggested fixes may still require meaningful work, unlike an accessible exact reference solution which we catch with rules. |


