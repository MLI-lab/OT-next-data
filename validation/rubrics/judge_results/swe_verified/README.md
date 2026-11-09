# SWE-bench Verified audit (2024)

OpenAI’s audit involved 93 software developers reviewing 1,699 SWE-bench test tasks, with three annotators per task. Issue clarity and test fairness use severity labels 0 to 3: 0/1 are minor and 2/3 severe. OpenAI filtered using the highest severity across reviewers, rather than majority vote. See the [original article and exact annotation criteria](https://openai.com/index/introducing-swe-bench-verified/).

## Relationship to our categories

- `underspecified` measures issue clarity. Labels 2/3 concern ambiguity or missing information, broadly related to `INSTR-AMBIGUOUS`. Label 3 alone does not establish a conflict with the verifier.
- `false_negative` measures whether tests reject valid solutions. Labels 2/3 are candidates for `INSTR-VERIFIER-MISMATCH`; check the notes and task before assigning our label. These are not direct labels for `VERIFIER-TOO-WEAK`.

## Agreement

Exact agreement means all three reviewers assigned the same 0 to 3 label. Binary agreement means all three assigned minor (0/1) or all three assigned severe (2/3). Confidence is a separate, annotation-wide 1 to 5 score, not a criterion-specific agreement measure. A confidence threshold applies to all three reviewers.

| Criterion | Minimum confidence | Complete tasks | Exact agreement | Binary agreement | All minor | All severe | Split minor/severe |
|---|---:|---:|---:|---:|---:|---:|---:|
| underspecified | 1 | 1695 | 525 (31.0%) | 1108 (65.4%) | 1047 | 61 | 587 |
| underspecified | 4 | 1343 | 449 (33.4%) | 916 (68.2%) | 871 | 45 | 427 |
| underspecified | 5 | 337 | 139 (41.2%) | 249 (73.9%) | 242 | 7 | 88 |
| false_negative | 1 | 1694 | 544 (32.1%) | 979 (57.8%) | 658 | 321 | 715 |
| false_negative | 4 | 1345 | 460 (34.2%) | 804 (59.8%) | 548 | 256 | 541 |
| false_negative | 5 | 338 | 137 (40.5%) | 209 (61.8%) | 156 | 53 | 129 |

Percentages use the complete-task count in that row. Missing labels are excluded, never treated as 0.

- `underspecified`: 4 tasks lack at least one label. Among complete tasks, severe votes 0/1/2/3: 1047 / 405 / 182 / 61.
- `false_negative`: 5 tasks lack at least one label. Among complete tasks, severe votes 0/1/2/3: 658 / 393 / 322 / 321.

## Joint selections

| Selection, required on both criteria | Tasks |
|---|---:|
| All three minor | 547 |
| All three label 0 | 198 |
| All three minor, all confidence 5 | 142 |
| All three label 0, all confidence 5 | 66 |
| Unanimous binary labels (either direction) | 712 |

These selections only concern the two criteria above; they do not exclude `other_major_issues` or imply membership in the final 500-task Verified subset.

## Files and reproduction

- [annotations.csv](annotations.csv): unchanged public CSV, 5,097 rows, including reviewer notes and confidence.
- [task_agreement.csv](task_agreement.csv): task IDs, sorted label triples, severe-vote counts and minimum confidence, one row per task and criterion.
- [agreement_summary.csv](agreement_summary.csv): the agreement table above.
- [label_patterns.csv](label_patterns.csv): counts for every observed label triple, including missing labels.

Source: [OpenAI annotation archive](https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip), member `samples_with_3_annotations_public.csv`. Copied from the retained local audit CSV and verified byte for byte against the official archive on 2026-11-06. No annotations were changed.

CSV SHA-256: `129f6a0ebe06decf12f7708607206e2cc1ea67e57b2279d7b334b70feaf777e8`.

Regenerate the summaries and this README with:

```bash
python validation/rubrics/judge_results/swe_verified/analyze.py
```
