"""Apply recovery-outcomes.json to the patch script's generated tables.

The tables of the first audit (DISCARDED_TASKS, REFERENCE_SIMILAR_WARNING, JAVA_INFER,
CSHARP_INFERSHARP) stay as their generators wrote them; this adds the later work on top: recovered
and re-audited tasks get the analyzer that reproduced their warning and the tag (set where the
reference has a similar warning); every task without a reproduced and removed warning is discarded.
Running it again changes nothing.

Discard reasons added here:
  warning_not_reproduced:    buggy and reference compile and analyze, the task's warning never appears.
  reference_not_analyzed:    the warning is reproduced, the reference could not be analyzed.
  build_or_analysis_failure: no analyzed buggy snapshot (build, dependency, capture or analyzer failure).
  verifier_accepts_buggy_file: the packaged verifier, run on the buggy file, did not report the warning
                             (an analysis whose result changes between runs on the same input)."""
import json
import re
from pathlib import Path

here = Path(__file__).resolve().parent
outcomes = json.loads((here / 'recovery-outcomes.json').read_text())
path = here.parent / 'patch_tasktrove_inferredbugs_v3.py'
text = path.read_text()


def table(name):
    body = re.search(rf'^{name} = (.*?)\n(?=# END |[A-Z_]+ = )', text, re.M | re.S).group(1)
    return eval(body)


def rows(ids):
    ids = sorted(ids)
    return ['        ' + ', '.join(repr(x) for x in ids[i:i + 6]) + ',' for i in range(0, len(ids), 6)]


def block(name, value):
    if isinstance(value, dict):
        lines = [f'{name} = {{']
        for key, ids in value.items():
            lines += [f'    {key!r}: [', *rows(ids), '    ],']
        return '\n'.join(lines + ['}'])
    return '\n'.join([f'{name} = frozenset({{', *[r[4:] for r in rows(value)], '})'])


recovered = outcomes['recovered']
versions = {'java': table('JAVA_INFER'), 'csharp': table('CSHARP_INFERSHARP')}
defaults = {'java': '0.17.0', 'csharp': '1.3'}
for lists in versions.values():
    for ids in lists.values():
        ids[:] = [t for t in ids if t not in recovered]
for task, row in recovered.items():
    language = 'java' if row['analyzer'].startswith('Infer ') else 'csharp'
    version = row['analyzer'].split(' ', 1)[1]
    if version != defaults[language]:
        versions[language].setdefault(version, []).append(task)
versions = {language: {v: ids for v, ids in sorted(lists.items()) if ids} for language, lists in versions.items()}

discarded = {reason: [t for t in ids if t not in recovered] for reason, ids in table('DISCARDED_TASKS').items()}
for reason, ids in outcomes['discarded'].items():
    discarded[reason] = sorted(set(discarded.get(reason, [])) | set(ids))
seen = {}
for reason, ids in discarded.items():
    for t in ids:
        assert t not in recovered and seen.setdefault(t, reason) == reason, (t, reason, seen[t])
similar = (set(table('REFERENCE_SIMILAR_WARNING')) - set(recovered) | {t for t, r in recovered.items() if r['reference_similar_warning']}) - set(seen)


def replace(marker, new):
    global text
    text, n = re.subn(rf'(# BEGIN {marker}\n).*?(\n# END {marker})', lambda m: m.group(1) + new + m.group(2), text, flags=re.S)
    assert n == 1, marker


replace('JAVA_INFER', block('JAVA_INFER', versions['java']))
replace('CSHARP_INFERSHARP', block('CSHARP_INFERSHARP', versions['csharp']))
replace('DISCARDED_TASKS', block('DISCARDED_TASKS', discarded) + '\n' + block('REFERENCE_SIMILAR_WARNING', similar))
path.write_text(text)
print('analyzers:', {l: {v: len(i) for v, i in t.items()} for l, t in versions.items()})
print('discarded:', {k: len(v) for k, v in discarded.items()}, 'total', len(seen))
print('tagged reference_similar_warning:', len(similar))
