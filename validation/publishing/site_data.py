"""Data files for the pipeline website (docs/): one pair of JSON files per datasource with a results.parquet.

Usage: python -m validation.publishing.site_data --dataset NAME=RESULTS.parquet [--dataset ...] --out docs/data
           [--tasks NAME=TASKS.parquet ...] [--judge-usage NAME=USAGE.json ...]

<name>.json holds what the diagram and the tables need: every task's status per check (one letter), the checks
whose automatic repair was applied (rp), wall seconds of the checks that run containers (tm, order in `timed`), the judge
verdicts per criterion and judge, and pass@k of both models. <name>-details.json holds the explanations shown when a
red cell is opened: check details of fails and warnings, judge explanations of failed criteria, and the pass@k
attempts. index.json lists the datasources. Step membership of each check follows the main README.

With --tasks (task Parquets with path and task_binary; the first one listing a task wins), the task files go to
<name>-files/NNN.json in chunks of CHUNK tasks, for the task explorer: text files up to FILE_LIMIT bytes (longer ones
cut), other files by name and size only. Each task in <name>.json records its chunk (f).
With --judge-usage (from validation.publishing.judge_usage), each task gets ju: {judge index: [seconds, turns,
input tokens, cached tokens, output tokens]} of the review behind its verdicts.
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
# Checks without a one-line summary in the rules README.
FALLBACK = {'verifier_paths': 'Absolute paths the verifier needs that are missing and not named in the instruction.',
            'script_execution_coverage': 'The instruction asks for a script to be written, fixed or run, but the verifier never executes it.'}
ROOT = Path(__file__).resolve().parents[2]
REPO = 'https://github.com/MLI-lab/OT-next-data'


def descriptions(criteria_files=('validation/rubrics/rubric_ot/rubric.toml',)):
    """One-line meaning of every check (rules README summaries) and judge criterion (rubric definitions)."""
    import re, tomllib
    out = dict(FALLBACK)
    readme = (ROOT / 'validation/rules/README.md').read_text()
    for name, text in re.findall(r'<summary><code>([^<]+)</code>[^·<]*·\s*([^<]+)</summary>', readme):
        out.setdefault(name, text.strip())
    for path in criteria_files:
        for criterion in tomllib.loads((ROOT / path).read_text()).get('criteria', []):
            # The rubric text exactly as the judge sees it.
            out.setdefault(criterion['name'], criterion['description'].strip())
    return out


CHUNK, FILE_LIMIT, TASK_LIMIT = 64, 50_000, 600_000


def task_files(blob):
    """[{path, size, text?, cut?}] of one task archive: text as UTF-8 up to FILE_LIMIT, binaries by size only."""
    import io, tarfile
    files, total = [], 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        root = members and all('/' in m.name for m in members) and len({m.name.split('/', 1)[0] for m in members}) == 1
        for m in sorted(members, key=lambda m: (m.name.count('/'), m.name)):
            name = m.name.split('/', 1)[1] if root else m.name
            entry = {'path': name, 'size': m.size}
            data = tar.extractfile(m).read(FILE_LIMIT + 1) if total < TASK_LIMIT else b''
            if data and b'\0' not in data[:8192]:
                try:
                    text = data[:FILE_LIMIT].decode('utf-8')
                except UnicodeDecodeError as e:
                    text = data[:e.start].decode('utf-8') if e.start > FILE_LIMIT - 4 else None
                if text is not None:
                    entry['text'] = text
                    entry['cut'] = m.size > len(text.encode())
                    total += len(text)
            files.append(entry)
    return files


def write_task_files(name, ids, sources, out):
    """Chunks of task files for the explorer; returns {task_id: chunk}."""
    import pyarrow.parquet as pq
    want, found = set(ids), {}
    for source in sources:
        table = pq.read_table(source, columns=['path'])
        rows = [i for i, p in enumerate(table.column('path').to_pylist()) if p.rstrip('/').rsplit('/', 1)[-1] in want - set(found)]
        if not rows:
            continue
        for row in pq.read_table(source, columns=['path', 'task_binary']).take(rows).to_pylist():
            found[row['path'].rstrip('/').rsplit('/', 1)[-1]] = task_files(row['task_binary'])
    folder = out / f'{name}-files'
    folder.mkdir(exist_ok=True)
    for old in folder.glob('*.json'):
        old.unlink()
    order = [t for t in ids if t in found]
    chunk = {t: i // CHUNK for i, t in enumerate(order)}
    for c in range(0, len(order), CHUNK):
        (folder / f'{c // CHUNK:03d}.json').write_text(json.dumps({t: found[t] for t in order[c:c + CHUNK]}, separators=(',', ':')))
    print(name, len(order), 'of', len(ids), 'tasks with files in', -(-len(order) // CHUNK), 'chunks,',
          sum(f.stat().st_size for f in folder.glob('*.json')) // 2**20, 'MB')
    return chunk


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
    # Wall time per task of the checks that run containers (build, reference and no-op runs), where measured.
    import pyarrow.parquet as pq
    timed_cols = [c for c in results.TIMED if c in present]
    raw = {r['task_id']: r for r in pq.read_table(path, columns=['task_id', *timed_cols]).to_pylist()}
    wall = lambda r, c: ((raw.get(r['task_id']) or {}).get(c) or {}).get('wall_seconds')
    timed = [c for c in timed_cols if any(wall(r, c) is not None for r in rows)]
    tasks, details = [], {}
    for r in sorted(rows, key=lambda r: r['task_id']):
        task = {'id': r['task_id'], 'c': ''.join(code(r.get(c)) for c in present)}
        try:
            repairs = json.loads(r.get('automatic_repairs') or '{}')
        except ValueError:
            repairs = {}
        repaired = [i for i, c in enumerate(present) if (repairs.get(c) or {}).get('applied')]
        if repaired:
            task['rp'] = repaired  # indices into checks: an automatic repair was applied before the check ran
        times = [wall(r, c) for c in timed]
        if any(x is not None for x in times):
            task['tm'] = times  # wall seconds per check in `timed`
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
    meaning = descriptions()
    data = {'name': name, 'checks': present, 'timed': timed, 'steps': steps, 'criteria': criteria,
            'descriptions': {k: meaning[k] for k in [*present, *criteria] if k in meaning},
            'judges': [{'id': j, 'name': MODEL_NAMES.get(j, j.split('/')[-1])} for j in judges], 'tasks': tasks}
    return data, details


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', action='append', required=True, help='NAME=path/to/results.parquet')
    ap.add_argument('--out', type=Path, default=Path('docs/data'))
    ap.add_argument('--tasks', action='append', default=[], help='NAME=path/to/tasks.parquet (repeatable; earlier wins)')
    ap.add_argument('--judge-usage', action='append', default=[], help='NAME=usage.json from validation.publishing.judge_usage')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    index = []
    for spec in a.dataset:
        name, path = spec.split('=', 1)
        data, details = build(name, path)
        for spec in [u.split('=', 1)[1] for u in a.judge_usage if u.split('=', 1)[0] == name]:
            usage = json.loads(Path(spec).read_text())
            for t in data['tasks']:
                ju = {str(i): usage[j['id']][t['id']] for i, j in enumerate(data['judges']) if t['id'] in usage.get(j['id'], {})}
                if ju:
                    t['ju'] = ju
        sources = [t.split('=', 1)[1] for t in a.tasks if t.split('=', 1)[0] == name]
        if sources:
            chunks = write_task_files(name, [t['id'] for t in data['tasks']], sources, a.out)
            for t in data['tasks']:
                if t['id'] in chunks:
                    t['f'] = chunks[t['id']]
        (a.out / f'{name}.json').write_text(json.dumps(data, separators=(',', ':')))
        (a.out / f'{name}-details.json').write_text(json.dumps(details, separators=(',', ':')))
        entry = {'name': name, 'tasks': len(data['tasks'])}
        if (ROOT / 'data' / name / 'patch.py').exists():  # step 1 is this datasource's patcher
            entry['patch'] = f'{REPO}/blob/main/data/{name}/patch.py'
        index.append(entry)
        print(name, len(data['tasks']), 'tasks', (a.out / f'{name}.json').stat().st_size // 1024, 'KB +',
              (a.out / f'{name}-details.json').stat().st_size // 1024, 'KB details')
    (a.out / 'index.json').write_text(json.dumps(index, indent=1) + '\n')


if __name__ == '__main__':
    main()
