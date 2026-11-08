"""Data files for the pipeline website (docs/): one pair of JSON files per datasource with a results.parquet.

Usage: python -m validation.publishing.site_data --dataset NAME=RESULTS.parquet [--dataset ...] --out docs/data

<name>.json holds what the diagram and the tables need: every task's status per check (one letter), the judge
verdicts per criterion and judge, and pass@k of both models. <name>-details.json holds the explanations shown when a
red cell is opened: check details of fails and warnings, judge explanations of failed criteria, and the pass@k
attempts. index.json lists the datasources. Step membership of each check follows the main README.
"""
import argparse
import json
from pathlib import Path

STEPS = [
    {'id': 1, 'name': 'Task standardization & runtime preparation', 'short': 'Standardization',
     'groups': [('Configuration and compatibility', ['task-toml-schema', 'resource-sizes', 'task-timeout',
                                                     'dockerfile-platform', 'compose-host-binds', 'instruction-suffix'])]},
    {'id': 2, 'name': 'Environment & verifier validation', 'short': 'Environment & verifier',
     'groups': [('Environment setup and dependencies', ['build', 'dockerfile-sanity', 'pip-pinning', 'nproc',
                                                        'python_missing_imports', 'artifact_efficiency',
                                                        'declared_workdir', 'declared_internet_access']),
                ('Instruction–verifier consistency', ['task-absolute-path', 'test-file-references', 'do_not_modify',
                                                      'no_behavioural_tests', 'verifier_paths', 'script_execution_coverage']),
                ('Reference and no-op execution', ['reference', 'reference_determinism', 'nop', 'nop_determinism'])]},
    {'id': 3, 'name': 'Leakage & tampering checks', 'short': 'Leakage & tampering',
     'groups': [('Solution and test leakage', ['no-cheat-dir', 'dockerfile-references', 'repository_history',
                                               'repository_timestamps', 'reference_leaks']),
                ('Verifier tampering', ['agent_editable_test', 'verifier_tamper'])]},
]
CODE = {'pass': 'p', 'fail': 'f', 'warning': 'w', 'not_applicable': 'a', 'not_requested': 'r', 'blocked': 'b'}
MODEL_NAMES = {'Qwen/Qwen3.8-27B': 'Qwen3.8-27B', 'zai-org/GLM-5.3-Flash': 'GLM-5.3-Flash'}


def inconclusive_cause(attempts):
    """Why no resource-valid batch: the limit the final attempts hit (validation.stages.calibration flags)."""
    flags = [a.get('calibration') or {} for a in attempts.get('final_attempts', [])]
    for flag, cause in (('oom', 'oom'), ('disk_full', 'disk'), ('verifier_timeout', 'verifier_timeout')):
        if any(f.get(flag) for f in flags):
            return cause
    if any(f.get('kind') == 'infrastructure' for f in flags) or 'infrastructure' in (attempts.get('reason') or ''):
        return 'infrastructure'
    return 'other'


def code(status):
    # Any other status is a check without a result (execution error, timeout, OOM, ...): 'm' for missing.
    return CODE.get(status, 'm') if status else 'r'


def build(name, path):
    from validation import run as results
    rows = results.read(path)
    checks = [c for step in STEPS for _, group in step['groups'] for c in group]
    present = [c for c in checks if any(c in r for r in rows)]
    judges, criteria = [], []
    for r in rows:
        for key, value in r.items():
            if results.is_judge_cell(value):
                criteria.append(key) if key not in criteria else None
                for judge in value['verdicts']:
                    judges.append(judge) if judge not in judges else None
    tasks, details = [], {}
    for r in sorted(rows, key=lambda r: r['task_id']):
        task = {'id': r['task_id'], 'c': ''.join(code(r.get(c)) for c in present)}
        notes = results.details(r)
        texts = {}
        for c in present:
            if r.get(c) in ('fail', 'warning') or code(r.get(c)) == 'm':
                record = notes.get(c) or {}
                text = record.get('details') if isinstance(record.get('details'), str) else results.summary(record) if record else ''
                texts[c] = {'status': r.get(c), 'text': (text or '')[:4000]}
        judged = {}
        for criterion in criteria:
            cell = r.get(criterion)
            if not results.is_judge_cell(cell):
                continue
            judged[criterion] = {judge: ''.join({'pass': 'p', 'fail': 'f'}.get(v, 'm') for v in cell['verdicts'].get(judge, []))
                                 for judge in judges}
            for judge, verdicts in cell['verdicts'].items():
                for i, verdict in enumerate(verdicts):
                    if verdict != 'pass':
                        texts.setdefault('judge', {}).setdefault(criterion, {}).setdefault(judge, []).append(
                            {'review': i + 1, 'verdict': verdict, 'text': cell['details'][judge][i][:4000]})
        if judged:
            task['j'] = judged
        passk = {}
        for role in ('target', 'teacher'):
            outcome = r.get(f'passk_{role}_outcome')
            if outcome and outcome != 'not_run':
                passk[role] = {'o': outcome, 's': r.get(f'passk_{role}_successes'), 'v': r.get(f'passk_{role}_valid_attempts')}
                try:
                    attempts = json.loads(r.get(f'passk_{role}_attempt_results') or '{}')
                except ValueError:
                    attempts = {}
                if outcome == 'inconclusive':
                    passk[role]['c'] = inconclusive_cause(attempts)
                texts.setdefault('passk', {})[role] = {
                    'model': attempts.get('model'), 'reason': attempts.get('reason'), 'rounds': attempts.get('rounds', []),
                    'attempts': [{'reward': a.get('reward'), 'error': a.get('exception_type'), 'allocation': a.get('allocation'),
                                  'memory_mb': ((a.get('resources') or {}).get('agent') or {}).get('memory_peak_mb'),
                                  'disk_mb': ((a.get('resources') or {}).get('agent') or {}).get('disk_peak_mb'),
                                  'agent_sec': (a.get('phases') or {}).get('agent_execution'),
                                  'verifier_sec': (a.get('phases') or {}).get('verifier')}
                                 for a in attempts.get('final_attempts', [])]}
        if passk:
            task['k'] = passk
            try:
                resources = json.loads(r.get('resources') or '{}')
            except ValueError:
                resources = {}
            if resources.get('requirements'):
                task['r'] = {k: resources['requirements'].get(k) for k in ('cpus', 'memory_mb', 'storage_mb')}
                texts.setdefault('passk', {})['resources'] = resources
        tasks.append(task)
        if texts:
            details[r['task_id']] = texts
    steps = [{**{k: v for k, v in s.items() if k != 'groups'},
              'groups': [{'name': g, 'checks': [c for c in cs if c in present]} for g, cs in s['groups']]} for s in STEPS]
    data = {'name': name, 'checks': present, 'steps': steps, 'criteria': criteria,
            'judges': [{'id': j, 'name': MODEL_NAMES.get(j, j.split('/')[-1])} for j in judges], 'tasks': tasks}
    return data, details


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', action='append', required=True, help='NAME=path/to/results.parquet')
    ap.add_argument('--out', type=Path, default=Path('docs/data'))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    index = []
    for spec in a.dataset:
        name, path = spec.split('=', 1)
        data, details = build(name, path)
        (a.out / f'{name}.json').write_text(json.dumps(data, separators=(',', ':')))
        (a.out / f'{name}-details.json').write_text(json.dumps(details, separators=(',', ':')))
        index.append({'name': name, 'tasks': len(data['tasks'])})
        print(name, len(data['tasks']), 'tasks', (a.out / f'{name}.json').stat().st_size // 1024, 'KB +',
              (a.out / f'{name}-details.json').stat().st_size // 1024, 'KB details')
    (a.out / 'index.json').write_text(json.dumps(index, indent=1) + '\n')


if __name__ == '__main__':
    main()
