"""Rebuild EMBEDDED_WARNINGS from the packaged verifier's own runs (python with pyarrow).

  python verifier_keys.py --buggy 'RUN/**/buggy/result.json' --fix 'RUN/**/full/result.json'

Inputs are the verifier's result.json files (the harness keeps them in logs.tar.gz per
verification, Harbor trials under the trial's verifier logs): runs on the buggy file, possibly
several per task, and runs of the historical fix (solution/solve.sh). Per task the table then holds
  originals         the task's warning as this verifier reports it on the buggy file: the warning
                    with the audited hash, or else the audited type, line and message
  allowed           hashes of the buggy file's other warnings in the target file, and of the fix's
  allowed_elsewhere hashes of the fix's warnings in the other files it changed
and the run's analyzer and snapshot stay as audited. Tasks are recorded for discarding
(recovery-outcomes.json) when a buggy run does not report the warning (verifier_accepts_buggy_file),
when the fix still has it (reference_retains_warning), or when a run failed to build or analyze
(build_or_analysis_failure). reviewed_pairs_table.py and recovery_tables.py stay as they are.
"""
import argparse
import base64
import collections
import glob
import importlib.util
import json
import lzma
from pathlib import Path
import re
import sys
import tarfile

here = Path(__file__).resolve().parent
path = here.parent / 'patch_tasktrove_inferredbugs_v3.py'
spec = importlib.util.spec_from_file_location('inferredbugs_patcher', path)
patcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patcher
spec.loader.exec_module(patcher)
FIELDS = ('hash', 'bug_type', 'procedure', 'line', 'qualifier')


def results(pattern):
    """result.json files, also inside the harness's logs.tar.gz archives."""
    found = collections.defaultdict(list)
    for name in glob.glob(pattern, recursive=True):
        if name.endswith('.tar.gz'):
            with tarfile.open(name) as tar:
                member = next((m for m in tar.getmembers() if m.name.endswith('verifier/result.json')), None)
                if member is None:
                    continue
                data = json.load(tar.extractfile(member))
        else:
            data = json.loads(Path(name).read_text())
        found[data['task_id']].append(data)
    return found


def same_warning(audited, warning, target):
    normalize = lambda s: re.sub(r'\s+', ' ', s or '').strip()
    return (warning.get('hash') == audited['hash'] or
            (warning.get('bug_type') == audited['bug_type'] and warning.get('line') == audited['line']
             and normalize(warning.get('qualifier')) == normalize(audited['qualifier'])
             and patcher.warning_audit_file(target, warning.get('file', ''))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--buggy', required=True, help='glob of result.json (or logs.tar.gz) of runs on the buggy file')
    ap.add_argument('--fix', required=True, help='glob of result.json (or logs.tar.gz) of runs of the historical fix')
    ap.add_argument('--write', action='store_true', help='write the table into the patch script and the discards into recovery-outcomes.json')
    a = ap.parse_args()
    table = patcher.embedded_warnings()
    recipes = {r['task_id']: r for r in patcher.embedded_recipes()}
    buggy, fix = results(a.buggy), results(a.fix)
    rebuilt, drop, counts = {}, {}, collections.Counter()
    for task, row in table.items():
        target = recipes[task]['target_file']
        in_target = lambda w: patcher.warning_audit_file(target, w.get('file', ''))
        runs_b, runs_f = buggy.get(task, []), fix.get(task, [])
        if not runs_b or not runs_f:
            counts['not run'] += 1
            continue
        if any(r.get('status') not in ('original_warning_remains', 'new_warnings', 'passed') for r in runs_b + runs_f):
            drop[task] = 'build_or_analysis_failure'
            continue
        originals, target_keys = [], set()
        for r in runs_b:
            found = [w for w in r['warnings'] if in_target(w) and any(same_warning(o, w, target) for o in row['originals'])]
            if not found:
                drop[task] = 'verifier_accepts_buggy_file'
                break
            originals += [{k: w.get(k) for k in FIELDS} for w in found if w['hash'] not in {o['hash'] for o in originals}]
            target_keys |= {w['hash'] for w in r['warnings'] if in_target(w)}
        if task in drop:
            continue
        original_hashes = {o['hash'] for o in originals}
        elsewhere = set()
        for r in runs_f:
            if any(in_target(w) and w['hash'] in original_hashes for w in r['warnings']):
                drop[task] = 'reference_retains_warning'
                break
            target_keys |= {w['hash'] for w in r['warnings'] if in_target(w)}
            elsewhere |= {w['hash'] for w in r['warnings'] if not in_target(w)}
        if task in drop:
            continue
        rebuilt[task] = dict(row, originals=originals, allowed=sorted(target_keys - original_hashes), allowed_elsewhere=sorted(elsewhere))
        counts['rebuilt'] += 1
    counts.update(collections.Counter(drop.values()))
    print(dict(counts))
    if not a.write:
        return
    for task, reason in drop.items():
        table.pop(task, None)
    table.update(rebuilt)
    data = base64.b85encode(lzma.compress(json.dumps(table, sort_keys=True, separators=(',', ':')).encode(), preset=9 | lzma.PRESET_EXTREME)).decode()
    lines = ["EMBEDDED_WARNINGS = '''"] + [data[i:i + 100] for i in range(0, len(data), 100)] + ["'''"]
    text = path.read_text()
    text, n = re.subn(r'(# BEGIN EMBEDDED_WARNINGS\n).*?(\n# END EMBEDDED_WARNINGS)', lambda m: m.group(1) + '\n'.join(lines) + m.group(2), text, flags=re.S)
    assert n == 1
    path.write_text(text)
    outcomes = json.loads((here / 'recovery-outcomes.json').read_text())
    for task, reason in drop.items():
        outcomes['recovered'].pop(task, None)
        outcomes['discarded'].setdefault(reason, [])
        if task not in outcomes['discarded'][reason]:
            outcomes['discarded'][reason].append(task)
    outcomes['discarded'] = {k: sorted(v) for k, v in outcomes['discarded'].items()}
    (here / 'recovery-outcomes.json').write_text(json.dumps(outcomes, indent=1) + '\n')
    print('written:', len(table), 'tasks in the table;', len(drop), 'to discard (run recovery_tables.py, then provenance.py)')


if __name__ == '__main__':
    main()
