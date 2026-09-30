"""Summarize a verifier check run: outcome per variant, and for the buggy file whether the verifier
saw the task's warning and only warnings the audit knows (python with pyarrow: loads the patch script).

  python inferredbugs_verifier_check_summary.py RUN_DIR
"""
import collections
import importlib.util
import json
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('inferredbugs_patcher', repo / 'data/inferredbugs/patch_tasktrove_inferredbugs_v3.py')
patcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patcher
spec.loader.exec_module(patcher)
table = patcher.embedded_warnings()
run = Path(sys.argv[1])
rows = [json.loads(p.read_text()) for p in sorted(run.glob('*/*/summary.json'))]
rows += [json.loads(line) for p in sorted(run.glob('summaries-*.jsonl')) for line in p.read_text().splitlines()]
rows = list({(r['task_id'], r['variant']): r for r in rows}.values())
rows = [r for r in rows if r['task_id'] in table]
counts = collections.Counter((r['variant'], r['analyzer'], r['reward'], r['status']) for r in rows)
print(len({r['task_id'] for r in rows}), 'tasks')
for key, n in sorted(counts.items(), key=str):
    print('%4d  %s' % (n, '  '.join(map(str, key))))
print()
buggy = [r for r in rows if r['variant'] == 'buggy']
print('buggy file rewarded:', [(r['task_id'], r['analyzer']) for r in buggy if r['reward'] != '0'])
print('buggy file rejected for another reason than the warning:', [(r['task_id'], r['status']) for r in buggy if r['reward'] == '0' and r['status'] != 'original_warning_remains'])
same = 0
analyzed = [r for r in buggy if r['warning_hashes'] is not None]
for r in analyzed:
    row = table[r['task_id']]
    original = {w['hash'] for w in row['originals']}
    seen = set(r['warning_hashes'])
    if original <= seen and seen <= original | set(row['allowed']):
        same += 1
    else:
        print('differs from the audit:', r['task_id'], r['analyzer'], 'original missing', len(original - seen), 'unknown to the audit', len(seen - original - set(row['allowed'])))
print('buggy file: the task\'s warning and only audited warnings in', same, 'of', len(analyzed), 'analyzed tasks')
agent = [r for r in rows if r['variant'] == 'agent']
if agent:
    shown = [r for r in agent if r['test_exit'] == '0' and r['warning_hashes'] is not None
             and {w['hash'] for w in table[r['task_id']]['originals']} <= set(r['warning_hashes'])]
    print('analyze.sh in /app printed the task\'s warning in', len(shown), 'of', len(agent), 'tasks;',
          'others:', [(r['task_id'], r['status'], r['test_exit']) for r in agent if r not in shown])
full = [r for r in rows if r['variant'] == 'full']
if full:
    print('complete historical fix:', dict(collections.Counter(r['status'] for r in full)),
          '; files changed per task (median):', sorted(len(r['changed_files'] or {}) for r in full)[len(full) // 2])
    print('  not passed:', [(r['task_id'], r['status']) for r in full if r['reward'] != '1'][:40])
gold = collections.Counter(r['status'] for r in rows if r['variant'] == 'gold')
print('historical fixed file alone:', dict(gold))
seconds = sorted(r['seconds'] for r in rows)
if seconds:
    print('seconds per verification: median', seconds[len(seconds) // 2], 'max', seconds[-1])
