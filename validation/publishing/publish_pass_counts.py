#!/usr/bin/env python3
"""Publish per-task, per-model solver pass counts from completed stage-6 reports."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.contract import read, task_records
from validation.publishing.publish import folder_of, run_name
from validation.checks.reward_metrics import pass_at_k, reward_group

COLUMNS = ('path', 'content_sha256', 'model', 'attempts', 'graded', 'solved', 'group', 'run')
FILE = 'pass_counts.parquet'


def load(submission):
    """One finished job: its contract and the stage-6 items."""
    submission = Path(submission)
    reports = sorted((submission / 'report').glob('stage-6-*.json'))
    if len(reports) != 1:
        raise ValueError(f'{submission}: expected one stage-6 report, found {len(reports)}')
    report = json.loads(reports[0].read_text())
    request = json.loads((submission / 'request.json').read_text())['args']
    contract = read(request['contract'])
    if report.get('contract_sha256') != contract['sha256']:
        raise ValueError(f'{submission}: the report belongs to another contract')
    if not report.get('complete') or report.get('dry_run'):
        raise ValueError(f'{submission}: stage 6 is not complete')
    if not request.get('serve_model') and not request.get('model'):
        raise ValueError(f'{submission}: no model in the request')
    return contract, request, report


def build(submissions, mapping, run_id=None):
    """Rows per data source and the run record."""
    from config.models import MODELS
    loaded = [load(s) for s in submissions]
    name = run_name(folder_of(t['task_id'], mapping) for c, _, _ in loaded for t in task_records(c))
    digest = hashlib.sha256('\n'.join(sorted(c['sha256'] for c, _, _ in loaded)).encode()).hexdigest()
    run_id = run_id or f'{datetime.now(timezone.utc):%Y-%m-%d}-{name}-pass-counts-{digest[:12]}'
    run_file = f'runs/{run_id}.json'
    tables, seen, models, contracts = {}, set(), {}, []
    for contract, request, report in loaded:
        model = request.get('serve_model') or request['model']
        attempts = int(request['attempts'])
        hashes = {t['task_id']: t['sha256'] for t in task_records(contract)}
        spec = MODELS.get(model)
        if spec:
            models[model] = {'hf_repo': spec.hf_repo, 'thinking': spec.thinking, 'context': request.get('serve_context'),
                             'max_output_tokens': spec.max_output_tokens, 'sampling': spec.sampling,
                             'weights': (contract.get('local_model_assets') or {}).get('revision')}
        else:
            models[model] = {}
        items = {Path(item['task']).name: item for item in report['items']}
        contracts.append({'contract_sha256': contract['sha256'], 'model': model, 'attempts': attempts,
                          'tasks': len(hashes), 'reported': len(items), 'job': str(report.get('timing', {}).get('slurm_job_id') or '') or None})
        for task, sha in hashes.items():
            if (task, model) in seen:
                raise ValueError(f'{task} appears in two runs of {model}')
            seen.add((task, model))
            rewards = [r for r in (items.get(task) or {}).get('rewards') or [] if isinstance(r, (int, float))]
            tables.setdefault(folder_of(task, mapping), []).append({
                'path': task, 'content_sha256': sha, 'model': model, 'attempts': max(attempts, len(rewards)),
                'graded': len(rewards), 'solved': sum(r >= 1 for r in rewards), 'group': reward_group(rewards), 'run': run_file})
    sources = {}
    for folder, rows in sorted(tables.items()):
        for model in sorted({r['model'] for r in rows}):
            mine = [r for r in rows if r['model'] == model]
            k = max(r['attempts'] for r in mine)
            sources.setdefault(folder, {})[model] = {
                'tasks': len(mine), 'attempts': sum(r['attempts'] for r in mine),
                'ungraded_attempts': sum(r['attempts'] - r['graded'] for r in mine),
                'pass@1': sum(r['solved'] / r['attempts'] for r in mine) / len(mine),
                f'pass@{k}': sum(pass_at_k(r['attempts'], r['solved'], r['attempts']) for r in mine) / len(mine),
                'groups': dict(sorted(Counter(r['group'] or 'no_reward' for r in mine).items())),
                'tasks_by_solved': {str(n): c for n, c in sorted(Counter(r['solved'] for r in mine).items())}}
    record = {'run': run_id, 'name': name, 'kind': 'pass_counts', 'created_at': datetime.now(timezone.utc).isoformat(),
              'models': models, 'contracts': contracts, 'data_sources': sources,
              'columns': {'solved': 'attempts with reward 1; an attempt without a reward counts as unsolved',
                          'group': 'all_zero, varying, all_solved or constant_partial over the graded attempts'},
              'validation_code': 'https://github.com/MLI-lab/OT-next-data/tree/main/validation'}
    return tables, record, run_file


def previous_rows(repo, folder):
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
    import pyarrow.parquet as pq
    try:
        return pq.read_table(hf_hub_download(repo, f'{folder}/{FILE}', repo_type='dataset')).to_pylist()
    except (EntryNotFoundError, RepositoryNotFoundError):
        return []


def description(record):
    lines = [f"Pass counts `{record['run']}`", '',
             'How many of the agent attempts solved each task, per model. One row per task and model in '
             f'`{FILE}` of each data source.', '',
             '| Data source | Model | Tasks | Attempts | pass@1 | pass@k | Never solved | Sometimes | Always | Ungraded attempts |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for folder, models in record['data_sources'].items():
        for model, c in models.items():
            k = next(key for key in c if key.startswith('pass@') and key != 'pass@1') if len([x for x in c if x.startswith('pass@')]) > 1 else 'pass@1'
            g = c['groups']
            lines.append(f"| {folder} | {model} | {c['tasks']} | {c['attempts'] // c['tasks']} | {c['pass@1']:.3f} | "
                         f"{c[k]:.3f} ({k}) | {g.get('all_zero', 0)} | {g.get('varying', 0)} | {g.get('all_solved', 0)} | {c['ungraded_attempts']} |")
    lines += ['', 'Models:', '']
    for model, m in record['models'].items():
        lines.append(f"- `{model}`: {m.get('hf_repo')}, thinking {'on' if m.get('thinking') else 'off'}, context {m.get('context')}, "
                     f"reply limit {m.get('max_output_tokens')}, sampling {m.get('sampling')}")
    return '\n'.join(lines) + '\n'


def write(tables, record, run_file, out, previous=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = Path(out)
    schema = pa.schema([('path', pa.string()), ('content_sha256', pa.string()), ('model', pa.string()),
                        ('attempts', pa.int64()), ('graded', pa.int64()), ('solved', pa.int64()),
                        ('group', pa.string()), ('run', pa.string())])
    files = []
    for folder, rows in sorted(tables.items()):
        new = {(r['path'], r['model']) for r in rows}
        kept = [r for r in (previous(folder) if previous else []) if (r['path'], r['model']) not in new]
        rows = sorted([*kept, *rows], key=lambda r: (r['path'], r['model']))
        path = out / folder / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({c: [r[c] for r in rows] for c in COLUMNS}, schema=schema), path)
        files.append(f'{folder}/{FILE}')
    (out / run_file).parent.mkdir(parents=True, exist_ok=True)
    (out / run_file).write_text(json.dumps(record, indent=2) + '\n')
    (out / 'pull-request.md').write_text(description(record))
    return [*files, run_file]


def publish(submissions, repo, folders=(), out=None, run_id=None, dry_run=False):
    mapping = dict(item.split('=', 1) for item in folders)
    tables, record, run_file = build(submissions, mapping, run_id)
    out = Path(out) if out else Path(submissions[0]) / 'publish-pass-counts'
    files = write(tables, record, run_file, out, None if dry_run and not repo else lambda folder: previous_rows(repo, folder))
    result = {'run': record['run'], 'files': files, 'out': str(out), 'description': description(record)}
    if dry_run:
        return result
    from huggingface_hub import CommitOperationAdd, HfApi
    commit = HfApi().create_commit(
        repo_id=repo, repo_type='dataset', create_pr=True,
        operations=[CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(out / name)) for name in files],
        commit_message=f"Pass counts {record['run']}", commit_description=description(record))
    result['pull_request'] = commit.pr_url
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('submissions', nargs='+', type=Path, help='finished submission folders (report/ and request.json)')
    ap.add_argument('--repo', default='FWeindel/validated-tasks')
    ap.add_argument('--folder', action='append', default=[], metavar='PREFIX=FOLDER',
                    help='data source folder of a task ID prefix; default: the prefix')
    ap.add_argument('--out', type=Path, help='where the files are written; default in the first submission folder')
    ap.add_argument('--run-id', help='default: date, data source name and a hash of the contracts')
    ap.add_argument('--dry-run', action='store_true', help='write the files and the description, open no pull request')
    a = ap.parse_args()
    result = publish(a.submissions, a.repo, a.folder, a.out, a.run_id, a.dry_run)
    print(result['description'])
    print('files:', ', '.join(result['files']), '\nout:', result['out'])
    if result.get('pull_request'):
        print('pull request:', result['pull_request'])


if __name__ == '__main__':
    main()
