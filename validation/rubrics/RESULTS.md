# Questions to answer
The questions we want to answer:

- Is the rubric applied consistently by a capable model?
- Are the consistent judgments also correct?
- Is performance limited by model capability?

## On confidently clean tasks

We selected [66 tasks](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/blob/main/curated/2026-11-06-swe66-review/TASKS.md) from the SWE-bench Verified audit ([original audit rubric](https://cdn.openai.com/introducing-swe-bench-verified/swe-b-annotation-instructions.pdf)) where all three reviewers agreed that the instructions were clear and the tests were fair, each with confidence 5/5. None reported other major issues and we use this as evidence for the assumption that all our rubric citeria are clean. This should be a clean reference set to first study judge behavior on tasks with strong human agreement.

**Ground-truth update:** Four tasks are excluded from the active Hugging Face selection; two remain PASS with a subjective flag. This leaves 62 tasks, including the two subjective cases ([exclusions and subjective cases](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/blob/main/curated/2026-11-06-swe66-review/README.md)). The results below still describe the original 66-task cohort.

We report labeling and classification separately, with three accepted reviews per task for each format:

- **Labeling:** PASS if every rubric criterion passes; FAIL if any criterion fails.
- **Classification:** PASS if the selected category is `CLEAN`; FAIL otherwise.


### Is the rubric applied consistently by a capable model?

For Sonnet 5.5, we measure:

1. Unanimous agreement: Percentage of tasks where all three reviews give the same PASS/FAIL decision, whether correct or incorrect.

2. Mean pairwise agreement: Compare reviews 1–2, 1–3, and 2–3 for each task, then average across all 198 comparisons.

| Format | Unanimous agreement | Mean pairwise agreement |
|---|---:|---:|
| Labeling | 54/66 (81.8%) | 174/198 (87.9%) |
| Labeling excluding answer leakage and triviality | 62/66 (93.9%) | 190/198 (96.0%) |
| Classification | 62/66 (93.9%) | 190/198 (96.0%) |
| Classification counting answer leakage and triviality as PASS | 65/66 (98.5%) | 196/198 (99.0%) |

All rows use the original 66 tasks. Labeling ignores the two excluded criteria; classification counts their categories as PASS alongside CLEAN. 3/4 tasks with disagreement human also made wrong annotation, 1 judge actually made an error.

**Which categories disagree?**

**Classification: which categories were assigned to the same task?**

Each cell counts tasks where one review selected the row category and another selected the column category. A task counts once per category pair, regardless of whether its votes split 2–1 or 1–2. The diagonal is omitted because this table shows disagreements only.

Abbreviations: Leak = `ANSWER-LEAK`; Weak = `VERIFIER-TOO-WEAK`; Mismatch = `INSTR-VERIFIER-MISMATCH`; Ambiguous = `INSTR-AMBIGUOUS`. Categories with no disagreements are omitted.

| Category | CLEAN | Leak | Weak | Mismatch | Ambiguous |
|---|---:|---:|---:|---:|---:|
| CLEAN | — | 3 | 1 | 0 | 0 |
| Leak | 3 | — | 0 | 0 | 0 |
| Weak | 1 | 0 | — | 0 | 0 |
| Mismatch | 0 | 0 | 0 | — | 1 |
| Ambiguous | 0 | 0 | 0 | 1 | — |

As expected ambigious and mismatch can be confused. Clean is confused with weak which makes sense cause its itself a subjective criteira. Answer leak all flagged has a suggested fix in the instructions, which again is subjective if it counts as leakage or not.

**Labeling: which criteria change between PASS and FAIL?**

Labeling gives each criterion its own PASS/FAIL decision. We show only tasks where the three reviews disagree on that criterion; unanimous decisions and criteria with no disagreements are omitted.

| Criterion | 2 PASS, 1 FAIL | 1 PASS, 2 FAIL | Tasks with disagreement |
|---|---:|---:|---:|
| ANSWER-LEAK | 2 | 3 | 5 |
| TASK-TRIVIAL | 3 | 3 | 6 |
| VERIFIER-TOO-WEAK | 1 | 1 | 2 |
| INSTR-VERIFIER-MISMATCH | 2 | 0 | 2 |
| INSTR-AMBIGUOUS | 0 | 1 | 1 |

- **Answer leak:** Three classification tasks and five labeling tasks disagree; two overlap. The three labeling-only cases are unanimously CLEAN in classification. Reviews differ on whether a suggested fix leaves enough work or gives away the answer.
- **Triviality:** Six labeling tasks disagree over whether a small code change is meaningful work. In classification, three are unanimously CLEAN; the other three receive answer-leak flags, two with CLEAN/ANSWER-LEAK disagreement.
- **Weak verifier:** Two labeling tasks disagree about test coverage. Classification calls one unanimously CLEAN and splits between CLEAN and weak verifier on the other. The disagreements concern whether partial coverage is sufficient and whether tests being present or executed means they affect the final score—a distinction some reviews missed.
- **Instruction–verifier mismatch:** Two labeling tasks disagree about whether tests require unstated behavior. Classification calls one unanimously CLEAN and splits between mismatch and ambiguity on the other.
- **Ambiguity:** One labeling task disagrees about an unspecified edge case—the same task that classification splits between ambiguity and mismatch.

### Are the consistent judgments also correct?

| Judge | Format | Mean accuracy over 3 reviews | Majority-vote accuracy | Lowest review-index accuracy |
|---|---|---:|---:|---:|
| Sonnet 5.5 | Labeling | 144/198 (72.7%) | 48/66 (72.7%) | 45/66 (68.2%) |
| Sonnet 5.5 | Labeling excluding answer leakage and triviality | 180/198 (90.9%) | 60/66 (90.9%) | 59/66 (89.4%) |
| Sonnet 5.5 | Classification | 157/198 (79.3%) | 51/66 (77.3%) | 52/66 (78.8%) |
| Sonnet 5.5 | Classification counting answer leakage and triviality as clean | 181/198 (91.4%) | 60/66 (90.9%) | 60/66 (90.9%) |

Mean accuracy averages accuracy across the three reviews. Majority-vote accuracy uses the decision supported by at least two reviews per task. Lowest review-index accuracy is the lowest of the three accuracy scores.

### Is performance limited by model capability?

We compare both judges on the same tasks, looking first at consistency and then at accuracy.

#### Judge agreement

These figures measure agreement across each judge's own three reviews, not agreement between the two judges.

| Judge | Format | Unanimous agreement | Mean pairwise agreement |
|---|---|---:|---:|
| Sonnet 5.5 | Labeling | 54/66 (81.8%) | 174/198 (87.9%) |
| Qwen3.8-27B | Labeling | 45/66 (68.2%) | 156/198 (78.8%) |
| Sonnet 5.5 | Classification | 62/66 (93.9%) | 190/198 (96.0%) |
| Qwen3.8-27B | Classification | 43/66 (65.2%) | 152/198 (76.8%) |

**Qwen3.8-27B classification: which categories were assigned to the same task?**

Each cell counts tasks receiving both categories across the three reviews, once per category pair. Mirrored entries describe the same tasks. Categories with no disagreements are omitted. Environment means `INSTR-ENV-MISMATCH`; Trivial means `TASK-TRIVIAL`.

| Category | CLEAN | Leak | Trivial | Weak | Mismatch | Environment |
|---|---:|---:|---:|---:|---:|---:|
| CLEAN | — | 4 | 1 | 3 | 11 | 7 |
| Leak | 4 | — | 1 | 0 | 2 | 2 |
| Trivial | 1 | 1 | — | 0 | 1 | 0 |
| Weak | 3 | 0 | 0 | — | 3 | 0 |
| Mismatch | 11 | 2 | 1 | 3 | — | 0 |
| Environment | 7 | 2 | 0 | 0 | 0 | — |

Qwen disagrees on the exact category for 29 tasks: 23 switch between CLEAN and a defect, while six switch only between defect categories. The largest category pairs are CLEAN versus instruction–verifier mismatch (11 tasks) and CLEAN versus environment mismatch (seven tasks).

**Qwen3.8-27B labeling: which criteria change between PASS and FAIL?**

Using the same 66 tasks and three reviews per task, we count disagreements for each criterion. Unanimous decisions are omitted. These are the original six-criterion reviews, before splitting the weak-verifier criterion.

| Criterion | 2 PASS, 1 FAIL | 1 PASS, 2 FAIL | Tasks with disagreement |
|---|---:|---:|---:|
| ANSWER-LEAK | 9 | 5 | 14 |
| TASK-TRIVIAL | 4 | 1 | 5 |
| VERIFIER-TOO-WEAK | 1 | 0 | 1 |
| INSTR-VERIFIER-MISMATCH | 8 | 2 | 10 |
| INSTR-AMBIGUOUS | 2 | 0 | 2 |
| INSTR-ENV-MISMATCH | 2 | 0 | 2 |

There are 34 criterion disagreements across 26 tasks; some tasks disagree on multiple criteria. The overall PASS/FAIL decision changes on 21 tasks. On the other five, another criterion keeps the task at FAIL in every review.

- **Answer leak:** Fourteen tasks disagree: ten concern suggestions or code in the instructions, one a PR link, and three Git history. We confirmed from preserved command outputs that all three Git-history fixes were locally accessible and matched the reference changes; checking out an older commit had left the history accessible. The [linked PR](https://github.com/django/django/pull/14157) contains a different proposed fix, contrary to Qwen's claim, but its discussion links to the final PR. Public access was confirmed; access from the review container was not ([verification evidence](swe_verified/qwen_answer_leak_audit.json)).
- **Triviality:** Five tasks disagree over whether applying a small or supplied fix is meaningful work. Some reviews treat the supplied fix as answer leakage only, while others also mark the task trivial.
- **Weak verifier:** One task disagrees because a FAIL review claims JSON-encoded test lists are processed character by character, making every solution pass. The pinned loader actually decodes these strings into lists, so this particular justification is incorrect.
- **Instruction–verifier mismatch:** Ten tasks disagree. Six include the same incorrect claim about JSON-encoded test lists, this time arguing that correct solutions must fail. Three others concern log parsing or missing output markers, and one concerns exact output formatting. Those additional claims need separate verification.
- **Ambiguity:** One task asks to replace old option names with new ones, but tests also require the old names to keep working. Another asks for a readable object representation, but tests require a particular parameter order. Reviews differ over whether these details are missing from the instructions, extra demands from the tests, or implied by existing code conventions.
- **Environment mismatch:** Both tasks use `test.sh` to download grading dependencies. Preserved command outputs confirm DNS/download failures, and both runs explicitly used `network_mode: simulated`. FAIL reviews treat the observed restriction as a task defect; PASS reviews attribute it to the review environment. The failures are real in the review runtime so shoudl exclude in accuracy.

[Individual reviews](swe_verified/qwen_labeling_disagreements.json) retain the explanations. The [grading-code check](swe_verified/qwen_grading_claim_audit.json) confirms JSON decoding for the seven affected task/criterion cases; it does not validate the other claims or rerun the full verifiers.

#### Accuracy

For this comparison, we exclude environment mismatch, answer leakage and triviality. Labeling requires PASS on the three remaining criteria: weak verifier, instruction–verifier mismatch and ambiguity. Classification counts `CLEAN` and the three excluded categories as clean.

| Judge | Format | Mean accuracy over 3 reviews | Majority-vote accuracy | Lowest review-index accuracy |
|---|---|---:|---:|---:|
| Sonnet 5.5 | Labeling | 180/198 (90.9%) | 60/66 (90.9%) | 59/66 (89.4%) |
| Qwen3.8-27B | Labeling | 174/198 (87.9%) | 60/66 (90.9%) | 56/66 (84.8%) |
| Sonnet 5.5 | Classification | 181/198 (91.4%) | 60/66 (90.9%) | 60/66 (90.9%) |
| Qwen3.8-27B | Classification | 162/198 (81.8%) | 57/66 (86.4%) | 53/66 (80.3%) |

Qwen’s accuracy varies more between prompting formats, consistent with its lower self-consistency. Sonnet’s accuracy is nearly unchanged.

#### Why does Sonnet reject six human-approved tasks?

Sonnet rejects the same six tasks in both prompting formats. We group them by our current assessment into likely human annotator error (4/6), Sonnet error (0/6) and subjective (2/6).

##### Likely human annotation errors

<details>
<summary>django-12039 — Weak verifier: PostgreSQL checks are skipped</summary>

**Sonnet’s concern:** The issue asks for two whitespace fixes. The verifier uses SQLite, so the PostgreSQL-only tests for the second fix are skipped. Fixing only the first could earn full credit.

**Why humans accepted it:** All three describe the added tests as covering both fixes, including PostgreSQL opclasses. Their notes discuss what those tests check, but do not address whether they run in the grading environment.

**Confirmed:** Fixing only the first issue earned full credit (`reward = 1`) with the original verifier. The PostgreSQL tests were skipped, and a direct check confirmed that the trailing-space bug remained. Tests and scoring were unchanged ([counterexample evidence](swe_verified/django12039_counterexample/summary.json), [verifier log](swe_verified/django12039_counterexample/verifier.log)).

</details>

<details>
<summary>django-13513 — Instruction–verifier mismatch: a different exception bug</summary>

**Sonnet’s concern:** The instruction asks Django to respect `__suppress_context__`. The base code shown in the patch already does this. The sole fail-to-pass test instead checks a context exception with no traceback, which is a different bug.

**Why humans accepted it:** Their notes appear to mix up the two bugs. Two reviewers say the new test checks the requested behavior, but it actually checks the missing-traceback bug. The third describes that missing-traceback bug as if it were the task’s instruction.

**Confirmed:** The starting code and the instruction’s exact suggested fix both pass the suppressed-context test but fail the required missing-traceback test (`reward = 0`). The reference fix passes both and earns `reward = 1`. Following the suggested fix does not automatically solve the other bug ([runtime evidence](swe_verified/django13513_counterexample/summary.json)).

</details>

<details>
<summary>scikit-learn-14983 — Instruction–verifier mismatch: parameter order in repr</summary>

**Sonnet’s concern:** The issue’s expected output puts `n_splits` before `n_repeats`; the test requires the reverse order using exact string equality. An implementation following the example’s order would fail that assertion.

**Why humans accepted it:** Their notes focus on showing the correct class and parameter values. None addresses the ordering difference. The required order follows the existing representation helper’s alphabetical convention, which could explain acceptance, but this rationale is not stated in their notes.

**Assessment:** Likely human annotation error. The test requires a different parameter order from the instruction’s explicit expected output, so an implementation following that order would be rejected. The human explanations do not address this conflict.

</details>

<details>
<summary>sympy-13878 — Instruction–verifier mismatch: equivalent formulas versus exact expressions</summary>

**Sonnet’s concern:** The instruction asks for correct precomputed cumulative distribution functions and suggests numerical checks. Several tests also require exact symbolic expressions, potentially rejecting mathematically equivalent formulas.

**Why humans accepted it:** The notes emphasize coverage of the requested distributions and mathematical correctness. Reviewer 29 mentions both formula comparisons and differentiation checks; none discusses whether an equivalent expression could fail structural equality.

**Confirmed:** Starting from the reference fix, we changed only the Arcsin CDF’s zero branch from `x < a` to `x <= a`. Both return zero at `x = a`, so the function is mathematically identical. The reference earned `reward = 1`; the equivalent version earned `reward = 0` because `test_arcsin` requires the exact symbolic form. This supports Sonnet’s mismatch flag ([counterexample evidence](swe_verified/sympy13878_counterexample/summary.json)).

</details>

##### Subjective

<details>
<summary>django-14376 — Instruction–verifier mismatch: updating MySQL option names</summary>

The instruction asks to replace deprecated MySQL option names and explicitly points to `django/db/backends/mysql/base.py`, but does not limit the change to that file. The verifier also requires `mysql/client.py` (`dbshell`) to support the new names while preserving compatibility with the old ones.

Sonnet treats this as an unstated requirement; the human reviewers consider it reasonably implied by the request. Both readings are defensible: the instruction points to the connection code, while updating both code paths keeps configuration handling consistent. We therefore treat this case as debatable, not an established error by either side.

</details>

<details>
<summary>django-14534 — Ambiguity / mismatch: what happens when no widget ID exists?</summary>

**Sonnet’s concern:** The instruction suggests returning `self.data['attrs']['id']`. A changed test also requires `None` when `auto_id=False` leaves no ID; the suggested code would raise `KeyError`.

**Why humans accepted it:** Two reviewers focus on the custom-ID test. Reviewer 14 explicitly discusses and accepts the `auto_id=False → None` requirement as part of correct ID handling.

**Assessment:** Debatable. Returning `None` when IDs are disabled seems reasonably implied, so we lean toward the human judgment. However, the instruction’s suggested code misses this case, which gives Sonnet a reasonable basis for flagging it.

</details>


#### Additional labeling disagreement: django-10097

<details>
<summary>django-10097 — URL tests run, but their failures are not graded</summary>

**Runtime finding:** With no source fix, six URL-validator tests fail, yet all 438 listed fail-to-pass checks pass. Applying the reference fix makes those URL tests pass without changing the grading results. This confirms that the relevant test failures are omitted from scoring, supporting the concern raised by Sonnet’s FAIL review.

**Limit:** Both versions earn `reward = 0` because the same five unrelated admin tests raise template lookup errors. Importing Django from the source checkout does not resolve those errors, even though the template files exist. We have therefore confirmed the grading gap, but have **not** demonstrated an incorrect solution earning full credit or established the cause of the unrelated failures. The human notes discuss the URL tests’ behavior, not their inclusion in scoring. This finding alone is not a new human-annotation-error label.

[Runtime evidence](swe_verified/django10097_runtime_audit/summary.json). This task is outside the six majority-rejected cases: its labeling majority is PASS. No Hugging Face label was changed by this check.

</details>

## On VERIFIER-TOO-WEAK label

Labeling results only, on the 34 category-labeled tasks: 18 FAIL (weak verifier) and 16 PASS.

The [category definition](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/blob/main/curated/2026-11-04-four-categories/VERIFIER-TOO-WEAK.md) distinguishes plausible incorrect or incomplete solutions from interference with grading. Reviewed hacks outside this criterion are labeled PASS, even though the task is not clean overall.

Positive means a weak-verifier defect: **TP** correctly flags a FAIL task; **FN** misses it; **FP** flags a PASS task; **TN** correctly passes it. Only the `VERIFIER-TOO-WEAK` judgment counts here, not failures on other criteria.

**Agreement across three reviews:**

| Judge | Unanimous agreement | Mean pairwise agreement |
|---|---:|---:|
| Sonnet 5.5 | 28/34 (82.4%) | 90/102 (88.2%) |
| Qwen3.8-27B | 23/34 (67.6%) | 80/102 (78.4%) |

Agreement concerns only this criterion’s PASS/FAIL decision, regardless of correctness.

<details>
<summary>Sonnet: why do the three reviews disagree on six tasks?</summary>

These six disagreement tasks are not the six false negatives. Their majority decisions give **3 TP, 2 FN, 0 FP and 1 TN** against the current category labels.

| Task | PASS / FAIL votes | Majority result | Why the reviews differ |
|---|---|---|---|
| freecad-impeller | 2 / 1 | TN | PASS reviews consider the geometric comparisons sufficient. FAIL suspects that twist and blade placement can be wrong while the coarse geometry checks still pass. This is a different concern from the reference-based baked-geometry exploit used in the source review. |
| `task_000254_21ede93b` | 1 / 2 | TP | PASS focuses on live file processing and exact outputs. FAIL focuses on unchecked requirements: C, inotify, popen, recursion and atomic writes. |
| `instance_gravitational__teleport-32bcd71591c234f0d8b091ec01f1f5cbfdc0f13c-vee9b09fb20c43af7e520f57e9239bbcf46b7113d` | 2 / 1 | FN | All acknowledge that the CLI nil guard is untested. PASS calls this a minor gap; FAIL treats it as an unchecked stated requirement. |
| `instance_navidrome__navidrome-5e549255201e622c911621a7b770477b1f5a89be` | 1 / 2 | TP | PASS trusts the real database persistence suite. FAIL says required filtering/controller changes are not exercised by that suite. |
| git-multibranch | 1 / 2 | TP | PASS trusts the clone/push/HTTPS checks. FAIL points to the unverified deployment mechanism and retries that allow deployment to exceed the required three seconds. |
| portfolio-optimization | 2 / 1 | FN | PASS trusts numeric results, speed and the existence of a compiled library. FAIL notices that the Python wrapper is not required to call that library, so NumPy could do the work instead of C. |

These summarize the judges’ reasoning; a majority agreeing with the ground-truth label does not establish that its particular explanation is correct. **FreeCAD follow-up:** An independently constructed untwisted candidate received reward 0. A 50° candidate also failed other geometry checks, so this is inconclusive about twist alone: no passing counterexample was demonstrated ([probe evidence](weak_verifier/freecad_probe/summary.json)). [Individual reviews](weak_verifier/sonnet_disagreements.json).

</details>

**Majority vote across three reviews:**

| Judge | Accuracy | TP | FN | FP | TN |
|---|---:|---:|---:|---:|---:|
| Sonnet 5.5 | 22/34 (64.7%) | 12 | 6 | 6 | 10 |
| Qwen3.8-27B | 25/34 (73.5%) | 13 | 5 | 4 | 12 |

Accuracy is `(TP + TN) / 34`, using the majority decision for this criterion only.

<details>
<summary>Counts across all three reviews (102 judgments per judge)</summary>

| Judge | TP | FN | FP | TN |
|---|---:|---:|---:|---:|
| Sonnet 5.5 | 35 | 19 | 19 | 29 |
| Qwen3.8-27B | 41 | 13 | 15 | 33 |


</details>

## On INSTR-VERIFIER-MISMATCH

## On INSTR-ENV-MISMATCH

## On INSTR-AMBIGUOUS
