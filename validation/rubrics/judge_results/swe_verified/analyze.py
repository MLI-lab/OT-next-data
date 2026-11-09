"""Reproduce agreement summaries using only Python's standard library."""
import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT / 'annotations.csv'
rows = list(csv.DictReader(source.open()))
groups = defaultdict(list)
for row in rows:
    groups[row['instance_id']].append(row)
assert len(rows) == 5097 and len(groups) == 1699
assert all(len(rs) == len({r['user_id'] for r in rs}) == 3 for rs in groups.values())

def write_csv(name, records):
    with (ROOT / name).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)

records = []
summaries = []
patterns = []
for criterion in ('underspecified', 'false_negative'):
    current = []
    for task, rs in sorted(groups.items()):
        values = [int(float(r[criterion])) if r[criterion] else None for r in rs]
        assert all(v is None or v in range(4) for v in values)
        complete = None not in values
        confidence = min(int(r['annotator_confidence']) for r in rs)
        severe = sum(v >= 2 for v in values if v is not None)
        record = dict(instance_id=task, criterion=criterion,
                      labels=';'.join('missing' if v is None else str(v) for v in sorted(values, key=lambda v: -1 if v is None else v)),
                      complete=complete, severe_votes=severe,
                      exact_unanimous=complete and len(set(values)) == 1,
                      binary_unanimous=complete and severe in (0, 3),
                      min_confidence=confidence,
                      max_label=max((v for v in values if v is not None), default=''))
        records.append(record)
        current.append(record)
    for floor in (1, 4, 5):
        eligible = [r for r in current if r['complete'] and r['min_confidence'] >= floor]
        summaries.append(dict(criterion=criterion, min_confidence=floor, complete_tasks=len(eligible),
            exact_unanimous=sum(r['exact_unanimous'] for r in eligible),
            binary_unanimous=sum(r['binary_unanimous'] for r in eligible),
            all_minor=sum(r['severe_votes'] == 0 for r in eligible),
            all_severe=sum(r['severe_votes'] == 3 for r in eligible),
            split_minor_severe=sum(r['severe_votes'] in (1, 2) for r in eligible)))
    counts = Counter(r['labels'] for r in current)
    patterns.extend(dict(criterion=criterion, labels=k, tasks=v) for k, v in sorted(counts.items()))
write_csv('task_agreement.csv', records)
write_csv('agreement_summary.csv', summaries)
write_csv('label_patterns.csv', patterns)

lines = ['# SWE-bench Verified audit (2024)', '',
    'OpenAI’s audit involved 93 software developers reviewing 1,699 SWE-bench test tasks, with three annotators per task. Issue clarity and test fairness use severity labels 0 to 3: 0/1 are minor and 2/3 severe. OpenAI filtered using the highest severity across reviewers, rather than majority vote. See the [original article and exact annotation criteria](https://openai.com/index/introducing-swe-bench-verified/).', '',
    '## Relationship to our categories', '',
    '- `underspecified` measures issue clarity. Labels 2/3 concern ambiguity or missing information, broadly related to `INSTR-AMBIGUOUS`. Label 3 alone does not establish a conflict with the verifier.',
    '- `false_negative` measures whether tests reject valid solutions. Labels 2/3 are candidates for `INSTR-VERIFIER-MISMATCH`; check the notes and task before assigning our label. These are not direct labels for `VERIFIER-TOO-WEAK`.', '',
    '## Agreement', '',
    'Exact agreement means all three reviewers assigned the same 0 to 3 label. Binary agreement means all three assigned minor (0/1) or all three assigned severe (2/3). Confidence is a separate, annotation-wide 1 to 5 score, not a criterion-specific agreement measure. A confidence threshold applies to all three reviewers.', '',
    '| Criterion | Minimum confidence | Complete tasks | Exact agreement | Binary agreement | All minor | All severe | Split minor/severe |',
    '|---|---:|---:|---:|---:|---:|---:|---:|']
for s in summaries:
    n = s['complete_tasks']
    def pct(key):
        return f"{s[key]} ({s[key] / n:.1%})" if n else '0'
    lines.append(f"| {s['criterion']} | {s['min_confidence']} | {n} | {pct('exact_unanimous')} | {pct('binary_unanimous')} | {s['all_minor']} | {s['all_severe']} | {s['split_minor_severe']} |")
lines += ['', 'Percentages use the complete-task count in that row. Missing labels are excluded, never treated as 0.', '']
for c in ('underspecified', 'false_negative'):
    subset = [r for r in records if r['criterion'] == c]
    missing = sum(not r['complete'] for r in subset)
    votes = Counter(r['severe_votes'] for r in subset if r['complete'])
    lines.append(f"- `{c}`: {missing} tasks lack at least one label. Among complete tasks, severe votes 0/1/2/3: {votes[0]} / {votes[1]} / {votes[2]} / {votes[3]}.")
by_task = defaultdict(list)
for r in records:
    by_task[r['instance_id']].append(r)
lines += ['', '## Joint selections', '', '| Selection, required on both criteria | Tasks |', '|---|---:|']
for name, predicate in [
    ('All three minor', lambda r: r['complete'] and r['severe_votes'] == 0),
    ('All three label 0', lambda r: r['complete'] and r['labels'] == '0;0;0'),
    ('All three minor, all confidence 5', lambda r: r['complete'] and r['severe_votes'] == 0 and r['min_confidence'] == 5),
    ('All three label 0, all confidence 5', lambda r: r['complete'] and r['labels'] == '0;0;0' and r['min_confidence'] == 5),
    ('Unanimous binary labels (either direction)', lambda r: r['binary_unanimous']),
]:
    lines.append(f'| {name} | {sum(all(predicate(r) for r in rs) for rs in by_task.values())} |')
lines += ['', 'These selections only concern the two criteria above; they do not exclude `other_major_issues` or imply membership in the final 500-task Verified subset.', '',
    '## Files and reproduction', '',
    '- [annotations.csv](annotations.csv): unchanged public CSV, 5,097 rows, including reviewer notes and confidence.',
    '- [task_agreement.csv](task_agreement.csv): task IDs, sorted label triples, severe-vote counts and minimum confidence, one row per task and criterion.',
    '- [agreement_summary.csv](agreement_summary.csv): the agreement table above.',
    '- [label_patterns.csv](label_patterns.csv): counts for every observed label triple, including missing labels.', '',
    'Source: [OpenAI annotation archive](https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip), member `samples_with_3_annotations_public.csv`. Copied from the retained local audit CSV and verified byte for byte against the official archive on 2026-11-06. No annotations were changed.', '',
    f'CSV SHA-256: `{hashlib.sha256(source.read_bytes()).hexdigest()}`.', '',
    'Regenerate the summaries and this README with:', '', '```bash', 'python validation/rubrics/judge_results/swe_verified/analyze.py', '```', '']
(ROOT / 'README.md').write_text('\n'.join(lines))
print('\n'.join(lines))
