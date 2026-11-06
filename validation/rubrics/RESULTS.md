# What we want to do 

Figure out which defects actually matter for RL training performance.  

Why not just filter out all tasks flagged with LLM as a judge rubric: 

1.) INSTR-ENV-MISMATCH: should differentiate between an environment mismatch that the agent can easily fix and one that requires the agent to invent missing data or services. For example, if the instruction says a service is running but it is not, and the agent can easily start it, this just makes the task harder rather than invalid. Example task: https://hub.harborframework.com/tasks/tmax/task_001197_680abe08, which would get filtered with river rubric without this rubric adjustment.

2.) VERIFIER-TOO-WEAK: should differentiate between a verifier that accepts a solution that completes only part of the work the instruction asks for (but actual work) and a verifier that accepts shortcuts. For example portfolio-optimization (https://hub.harborframework.com/tasks/terminal-bench/portfolio-optimization) asks about accelerating financial portfolio calculations using C, verifier checks correctness and speed but not that implementation is in C (a model can still learn from correctly implementing a fast and correct implementation say in numpy not C). So we split the category into "VERIFIER-ACCEPTS-INCOMPLETE" and "VERIFIER-ACCEPTS-SHORTCUTS". "VERIFIER-ACCEPTS-INCOMPLETE" repairing the verifier to test all steps would make the task harder (as if we would combine two tasks in one long horizon task). These are potentials for hardening tasks when we repair them. 

3.) INSTR-VERIFIER-MISMATCH: For example, the Django task django__django-14376 https://code.djangoproject.com/ticket/32732 asks to replace deprecated MySQL parameter names (db → database, passwd → password) and points to one file, but the verifier also expects corresponding changes in another file. Since the instruction does not explicitly restrict changes to the mentioned file, we may not want to filter out the task, as figuring out which other files need updating should be part of the work. Q: should we add "Do not flag tasks where the instruction points to specific files, but the verifier expects the same requested change in additional files or code paths, unless the instruction explicitly restricts changes to those files." to the criteria description to not filter out these tasks. 

4.) INSTR-AMBIGUOUS: For example, the Django task [django__django-14534](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/resolve/c496a719605039fb29df2bb0906433946d095820/tasks/39352eb2be2ee55a/task.tar) asks for a widget’s ID but does not explicitly say what should happen when no ID exists: raise `KeyError` or return `None`. The verifier requires `None`. We may not want to filter out such tasks: an agent could choose the accepted behavior and still learn from the task and also the alternative is not necessarily undesirable behaviour. Unclear if filtering these will improve RL performance 

5.) "TASK-TRIVIAL" very subjective criteria and changes per model, for the judge which is strong something would be trivial but for the model we want to train might still teach it something. 

6.) "ANSWER-LEAK" unclear if a model would exploit the shortcut, would need to see from RL runs. Also can be subjective e.g. many SWE tasks get filtered out where instruction has a suggestion of the fix. The ones where it is a direct leak e.g. from git history or exact/partial reference solution already in agent environment we can filter out by hash matching (static rule) which is cheaper and more determinsitc/interpretable. 


# What we need to figure out first to answer this 

1.) Task ambiguity: Is there objectively a defect, or does identifying one require a subjective judgment?

To compare judges performance, choose ground truth tasks that are not subjective and defect is mostly objectively true, moving from benchmark tasks that are alrady very good and defects are more minor and perhapse subjetcive to tasks filtered from training RL envs where defect is more clear. 

2.) Rubric quality: Is the criterion sufficiently clear that two competent evaluators should interpret it the same way?

After this experiment did the following adjustments to the rubric (might want to rerun if they show an imporvement): 

- For verifier too weak asking for a demonstration to resolve hypothetical examples given by the judge
- For verifier too weak sometimes judge does not distinguish between a test being present or executed versus affecting the final score, added "Check that relevant tests affect the final score, not just that they are present or executed."
- Added “ rejects build artifacts created by the instruction’s own compilation commands” as an example to INSTR-VERIFIER-MISMATCH based on one task where verifier checks that only one script in folder but that scripts creates binaries so valid solution gets rejected, which should be flagged but judges disagreed 
- In “INSTR-VERIFIER-MISMATCH" Changed “The verifier checks something the instruction does not imply, or contradicts it to” The verifier checks an arbitrary requirement or contradicts the instruction to better distinguish cases that should be classified as ambigious (where agent may by chance figure out solution that scores 1 and can learn from) to tasks where its impossibel (e..g match exact warning message) And added to INSTR-AMBIGUOUS: “For example, the expected behavior for an edge case is unclear, and the verifier accepts only one of several reasonable behaviors” so that case from above gets classified in here 


3.) Judge quality: Can the LLM correctly apply that criterion to the evidence?

We figure out by looking at same judge agreement. The better the judge (Sonnet 5.5.) the more agreement (Qwen 3.8 27B) has higher disagreement. 


# Questions to answer
The questions we want to answer: 

- Is the rubric applied consistently by a capable model? (i.e. is our rubric good, not too subjective)
- Are the consistent judgments also correct?
- Is performance limited by model capability?

**Estimated API cost:** One Sonnet 5.5 labeling review costs approximately **$0.06 per task**, and one classification review **$0.054 per task**.

Main conclusions: 

Conclusion: 
Answer leak and subjective should be combined if we want to filter by this, e.g. if an instruction gives suggested implementation or solution is in git history is it answer leak or trivial. 
Split verifier too weak into verifiers that allow a shortcut and verifiers that only check some of the requirements with the reasoning that for partial real coverage agent stil has to do some actual work which may be useful for RL training (as if the actual task is putting together two requirements and this one only one is checked) 
And for verifier too weak asking for a demonstration to resolve hypothetical examples given by the judge. 

## On confidently clean tasks

We selected [66 tasks](https://huggingface.co/datasets/FWeindel/task-defect-ground-truth/blob/main/curated/2026-11-06-swe66-review/TASKS.md) from the SWE-bench Verified audit ([original audit rubric](https://cdn.openai.com/introducing-swe-bench-verified/swe-b-annotation-instructions.pdf)) where all three reviewers agreed that the instructions were clear and the tests were fair, each with confidence 5/5. None reported other major issues (use to assume all our rubric citeria are clean). 

**Ground-truth update:** Four tasks are excluded from HF after verifying not clean and two remain PASS with a subjective flag. The results below still describe the original 66-tasks.

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

<details>
<summary>Which categories disagree?</summary>

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

</details>

### Are the consistent judgments also correct?

| Judge | Format | Mean accuracy over 3 reviews | Majority-vote accuracy | Lowest review-index accuracy |
|---|---|---:|---:|---:|
| Sonnet 5.5 | Labeling | 144/198 (72.7%) | 48/66 (72.7%) | 45/66 (68.2%) |
| Sonnet 5.5 | Labeling excluding answer leakage and triviality | 180/198 (90.9%) | 60/66 (90.9%) | 59/66 (89.4%) |
| Sonnet 5.5 | Classification | 157/198 (79.3%) | 51/66 (77.3%) | 52/66 (78.8%) |
| Sonnet 5.5 | Classification counting answer leakage and triviality as clean | 181/198 (91.4%) | 60/66 (90.9%) | 60/66 (90.9%) |

### Is performance limited by model capability?

#### Judge agreement

These figures measure agreement across each judge's own three reviews, not agreement between the two judges.

| Judge | Format | Unanimous agreement | Mean pairwise agreement |
|---|---|---:|---:|
| Sonnet 5.5 | Labeling | 54/66 (81.8%) | 174/198 (87.9%) |
| Qwen3.8-27B | Labeling | 45/66 (68.2%) | 156/198 (78.8%) |
| Sonnet 5.5 | Classification | 62/66 (93.9%) | 190/198 (96.0%) |
| Qwen3.8-27B | Classification | 43/66 (65.2%) | 152/198 (76.8%) |

<details>
<summary>Qwen3.8-27B: which categories disagree?</summary>

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

</details>

#### Accuracy

For this comparison, we exclude environment mismatch, answer leakage and triviality, because Qwen was run offline some steups required online though and answer leakage actually present in some etc. (ask me why for more details). Labeling requires PASS on the three remaining criteria: weak verifier, instruction–verifier mismatch and ambiguity. Classification counts `CLEAN` and the three excluded categories as clean.

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

Main reason for false negatives is that the judges often treat substantial testing of the main behavior as sufficient, even when a stated requirement remains unchecked. I think is fine if the core functionality is genuinely tested, so the remaining gap is minor and we keep them
Main reason for false positives is that judge says a shortcut is possible but does not demonstrate it i.e. hypothetical examples.

## On INSTR-VERIFIER-MISMATCH

Labeling results only, on 180 category-labeled tasks: 118 FAIL and 62 PASS. Both judges have three reviews per task. Only this criterion’s PASS/FAIL decision counts; positive means a defect.

The 62 PASS tasks use the updated reference set: four excluded tasks are removed, and the two subjective cases remain PASS.

**Agreement across three reviews:**

| Judge | Unanimous agreement | Mean pairwise agreement |
|---|---:|---:|
| Sonnet 5.5 | 167/180 (92.8%) | 514/540 (95.2%) |
| Qwen3.8-27B | 130/180 (72.2%) | 440/540 (81.5%) |

**Majority vote across three reviews:**

| Judge | Accuracy | TP | FN | FP | TN |
|---|---:|---:|---:|---:|---:|
| Sonnet 5.5 | 163/180 (90.6%) | 102 | 16 | 1 | 61 |
| Qwen3.8-27B | 128/180 (71.1%) | 68 | 50 | 2 | 60 |

Accuracy is `(TP + TN) / 180`. [Results and individual reviews](category_results/INSTR-VERIFIER-MISMATCH.json).

Main reason for false negative is that the judges often confirm that the intended solution passes, but do not sufficiently check whether the verifier rejects other solutions allowed by the instructions.

<details>
<summary>Sonnet: why do the three mismatch reviews disagree on 13 tasks?</summary>

PASS means no mismatch; FAIL means mismatch. Results below are relative to the current reference labels, not independent proof that a review is wrong. In labeling, each criterion is judged separately: “only ambiguity” means INSTR-AMBIGUOUS = FAIL and INSTR-VERIFIER-MISMATCH = PASS.

**Ambiguity instead of mismatch: 5 tasks**

At least one review assigns mismatch PASS but ambiguity FAIL. This does not always explain the entire disagreement: for `django__django-14534`, another review passes both criteria.

| Task | Reference | Reviews 1 / 2 / 3 | Majority result | Why the reviews differ |
|---|---|---|---|---|
| `adaptive-rejection-sampler` | FAIL | FAIL / PASS / FAIL | TP; one review misses it | All three flag the unspecified function signature as ambiguity. Two also flag mismatch because the verifier requires particular argument names and ordering; one assigns mismatch PASS. |
| `django__django-12747` | FAIL | FAIL / PASS / FAIL | TP; one review misses it | The instruction permits empty dictionaries or zero-valued entries, but tests accept only the empty form. All flag ambiguity; the PASS review assigns the problem only to that category. |
| `instance_qutebrowser__qutebrowser-e34dfc68647d087ca3175d9ad3f023c30d8c9746-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d` | FAIL | FAIL / PASS / PASS | FN; majority misses it | All flag unclear URL-validation rules as ambiguity. Only FAIL also argues that specific URLs accepted by the tests conflict with the instruction. |
| `django__django-13199` | FAIL | FAIL / FAIL / PASS | TP; one review misses it | FAIL flags an unspecified API and session-middleware behavior. PASS considers the tests consistent with the request and flags the unspecified interface only as ambiguity. |
| `django__django-14534` | PASS (subjective) | PASS / FAIL / PASS | TN; one review flags it | PASS considers None for a missing ID reasonably implied. FAIL emphasizes that the suggested code raises KeyError instead. Two reviews flag ambiguity, including one that passes mismatch. |

**Other reasons for disagreement: 8 tasks**

No review assigns mismatch PASS while flagging ambiguity. Reviews instead differ over what the instruction implies, which checks matter, or whether the verifier’s expected result is correct. This is not necessarily a switch to another defect category.

| Task | Reference | Reviews 1 / 2 / 3 | Majority result | Why the reviews differ |
|---|---|---|---|---|
| `polyglot-rust-c` | FAIL | FAIL / PASS / PASS | FN; majority misses it | 1. The task asks for one source file, `main.rs`.<br>2. Compiling it using the supplied commands creates executable files alongside it.<br>3. The verifier requires the directory to contain only `main.rs`, so those compilation outputs cause failure—even when the program works correctly.<br>PASS reviews interpret “single file” as forbidding these extra files; FAIL identifies the conflict. The upstream annotation confirms that compilation outputs are allowed. |
| `polyglot-c-py` | FAIL | FAIL / PASS / PASS | FN; majority misses it | PASS treats a source-only directory as implied by “single file.” FAIL notes that the instructed build creates cmain in that directory, but the verifier rejects it unless deleted. |
| `dna-insert` | FAIL | FAIL / PASS / PASS | FN; majority misses it | FAIL says repeated DNA bases permit equivalent primer designs that the verifier rejects. PASS says the sequences determine the insertion location and valid designs are accepted. |
| `sam-cell-seg` | FAIL | PASS / PASS / FAIL | FN; majority misses it | PASS says the output checks match the instruction. FAIL says the input uses unbracketed coordinates but the verifier requires bracketed lists, despite asking for matching format. |
| `task_009230_96ffbd11` | FAIL | PASS / PASS / FAIL | FN; majority misses it | PASS says expected numbers match the formula within tolerance. FAIL calculates a discrepancy larger than that tolerance. This is a factual disagreement. |
| `task_002319_20a7cc18` | FAIL | PASS / FAIL / FAIL | TP; one review misses it | PASS says the expected values match. FAIL says the instructed calculation produces peak index 50000, while the verifier requires 10000. |
| `task_006064_10c605da` | FAIL | FAIL / FAIL / PASS | TP; one review misses it | FAIL identifies a source-code check requiring is_alphabetic, rejecting alternative implementations. PASS discusses outputs but does not address that restriction. |
| `django__django-11299` | PASS | FAIL / PASS / PASS | TN; one review flags it | FAIL considers requiring the internal SimpleCol implementation an extra demand. PASS considers it implied by the issue’s discussion of Col versus SimpleCol. |

The majority gives **5 TP, 6 FN, 0 FP and 2 TN** on these 13 tasks. These explanations summarize the saved reviews; their individual factual claims have not all been independently verified. [Individual reviews and reference annotations](category_results/INSTR-VERIFIER-MISMATCH.json).

Under the revised wording, an arbitrary required function signature is a mismatch if it cannot reasonably be inferred from the instructions or repository context. Rejecting an output explicitly permitted by the instructions is also a mismatch. The historical reviews used the earlier rubric.

</details>

## On INSTR-AMBIGUOUS

Labeling results only, on 76 category-labeled tasks: 14 FAIL and 62 PASS. Both judges have three reviews per task. Only this criterion’s PASS/FAIL decision counts; positive means a defect.

The 62 PASS tasks use the updated reference set: four excluded tasks are removed, and the two subjective cases remain PASS.

**Agreement across three reviews:**

| Judge | Unanimous agreement | Mean pairwise agreement |
|---|---:|---:|
| Sonnet 5.5 | 74/76 (97.4%) | 224/228 (98.2%) |
| Qwen3.8-27B | 71/76 (93.4%) | 218/228 (95.6%) |

**Majority vote across three reviews:**

| Judge | Accuracy | TP | FN | FP | TN |
|---|---:|---:|---:|---:|---:|
| Sonnet 5.5 | 69/76 (90.8%) | 9 | 5 | 2 | 60 |
| Qwen3.8-27B | 67/76 (88.2%) | 5 | 9 | 0 | 62 |

Most ambiguity false negatives occur because the judge considers one interpretation reasonably implied by conventions or context. And most false positives are subjective tasks i.e. does an impliict behaviour need an explicit instruction.

## On INSTR-ENV-MISMATCH

Labeling results only, on 72 category-labeled tasks: 59 FAIL and 13 PASS. Both judges have three reviews per task. Only this criterion’s PASS/FAIL decision counts; positive means a defect.

**Agreement across three reviews:**

| Judge | Unanimous agreement | Mean pairwise agreement |
|---|---:|---:|
| Sonnet 5.5 | 61/72 (84.7%) | 194/216 (89.8%) |
| Qwen3.8-27B | 65/72 (90.3%) | 202/216 (93.5%) |

**Majority vote across three reviews:**

| Judge | Accuracy | TP | FN | FP | TN |
|---|---:|---:|---:|---:|---:|
| Sonnet 5.5 | 59/72 (81.9%) | 51 | 8 | 5 | 8 |
| Qwen3.8-27B | 70/72 (97.2%) | 58 | 1 | 1 | 12 |

Main mistake for FP (7/8) was that sonnet assumed grading resources there and did not check them. Man mistake for FP mainly fixable setup problems that the judges treated as missing resources (not a big deal as okay also if we filter them out).
