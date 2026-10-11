#!/usr/bin/env python3
"""Fetch full task trees at label-specific commits without running task code."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read_url(url):
    headers = {'User-Agent': 'OT-next-data-labeled-tasks'}
    if url.startswith('https://api.github.com/') and os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    with urlopen(Request(url, headers=headers), timeout=90) as response:
        return response.read()


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args])


def tree_entries(reference, upstream, legacy_checkout=None):
    repo, revision, path = (reference[k] for k in ('repository', 'revision', 'path'))
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('task revision must be a full commit SHA')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise ValueError('invalid repository name')
    if PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
        raise ValueError('invalid task path')
    prefix = path.rstrip('/') + '/'
    checkout = upstream if repo == 'harbor-framework/terminal-bench' else (
        legacy_checkout if repo in ('laude-institute/terminal-bench', 'harbor-framework/terminal-bench-1') else None)
    if checkout is not None and (checkout / '.git').exists():
        entries = []
        for raw in git(checkout, 'ls-tree', '-rz', revision, '--', path).split(b'\0'):
            if not raw:
                continue
            info, name = raw.decode().split('\t', 1)
            mode, kind, sha = info.split()
            entries.append({'path': name, 'mode': mode, 'type': kind, 'sha': sha})
        reader = lambda entry: git(checkout, 'cat-file', 'blob', entry['sha'])
    else:
        data = json.loads(read_url(f'https://api.github.com/repos/{repo}/git/trees/{revision}?recursive=1'))
        if data.get('truncated'):
            raise ValueError('GitHub tree is truncated; refusing an incomplete task')
        entries = data['tree']
        reader = lambda entry: read_url(f'https://raw.githubusercontent.com/{repo}/{revision}/' + quote(entry['path'], safe='/'))
    entries = [e for e in entries if e['path'].startswith(prefix) and e['type'] != 'tree']
    if not entries:
        raise ValueError(f'no task files at {repo}@{revision}:{path}')
    return entries, reader, prefix


def digest_tree(path):
    records = []
    for file in sorted(path.rglob('*')):
        if file.is_symlink():
            data, mode = os.readlink(file).encode(), '120000'
        elif file.is_file():
            data = file.read_bytes()
            mode = '100755' if file.stat().st_mode & 0o111 else '100644'
        else:
            continue
        records.append([file.relative_to(path).as_posix(), mode, hashlib.sha256(data).hexdigest()])
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def materialize(reference, destination, upstream, legacy_checkout=None):
    entries, reader, prefix = tree_entries(reference, upstream, legacy_checkout)
    files, links = [], []
    for entry in entries:
        relative = PurePosixPath(entry['path'][len(prefix):])
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('unsafe path in source tree')
        if entry['type'] != 'blob' or entry['mode'] not in ('100644', '100755', '120000'):
            raise ValueError(f'unsupported Git object: {entry["path"]}')
        payload = reader(entry)
        blob = b'blob ' + str(len(payload)).encode() + b'\0' + payload
        if hashlib.sha1(blob).hexdigest() != entry['sha']:
            raise ValueError(f'Git blob hash mismatch: {entry["path"]}')
        if payload.startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise ValueError(f'Git LFS payload needs separate retrieval: {entry["path"]}')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry['mode'] == '120000':
            links.append((target, payload.decode()))
        else:
            target.write_bytes(payload)
            target.chmod(0o755 if entry['mode'] == '100755' else 0o644)
        files.append(relative.as_posix())
    # Create links last so source paths can never write through them.
    for target, link in links:
        if Path(link).is_absolute() or not (target.parent / link).resolve().is_relative_to(destination.resolve()):
            raise ValueError(f'symlink escapes task: {target.name}')
        target.symlink_to(link)
    if (destination / 'task.toml').is_file():
        task_format = 'harbor'
    elif (destination / 'task.yaml').is_file():
        task_format = 'terminal-bench-legacy'
    else:
        raise ValueError('source tree has neither task.toml nor task.yaml')
    return {'format': task_format, 'files': files, 'sha256': digest_tree(destination)}


def fetch(labels_path, out, upstream, legacy_checkout=None):
    labels = json.loads(labels_path.read_text())
    if isinstance(labels['tasks'], dict):
        selection = json.loads((labels_path.parent / 'task-selection.json').read_text())
        selected = []
        for task_id, reference in selection['tasks'].items():
            task = labels['tasks'][task_id]
            if not set(task['labels']).intersection(selection['criteria']):
                raise ValueError(f'selected task has no retained criterion: {task_id}')
            selected.append({'task_id': task_id, 'task_reference': reference})
        labels = {'tasks': selected}
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out.parent / 'task-manifest.json'
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    previous = {t['task_id']: t for t in old.get('tasks', [])}
    names = [item['task_id'] for item in labels['tasks']]
    if len(set(names)) != len(names):
        raise ValueError('duplicate task IDs in labels')
    if previous.keys() - set(names):
        raise ValueError('previous manifest has tasks removed from labels; use a new output directory')
    next_id = max((int(t['directory'].split('-')[1]) for t in previous.values()), default=0) + 1
    report = {'labels_sha256': hashlib.sha256(labels_path.read_bytes()).hexdigest(),
              'tasks_directory': str(out.resolve()), 'tasks': []}
    for index, item in enumerate(labels['tasks'], 1):
        reference = item['task_reference']
        # Names shown to judges must not reveal a planted criterion.
        task_id = item['task_id']
        prior = previous.get(task_id, {})
        directory = prior.get('directory')
        if not directory:
            directory = f'task-{next_id:04d}'
            next_id += 1
        if not re.fullmatch(r'task-[0-9]{4,}', directory):
            raise ValueError('invalid task directory in previous manifest')
        target = out / directory
        row = {'task_id': task_id, 'directory': directory, 'reference': reference}
        if not reference.get('revision'):
            row.update(status='error' if target.exists() else 'unresolved',
                       reason='Label has no exact task commit; review evidence before fetching.')
        else:
            try:
                if target.exists():
                    if (prior.get('status') != 'fetched' or prior.get('reference') != reference
                            or digest_tree(target) != prior.get('sha256')):
                        raise ValueError('existing task differs from manifest; refusing to overwrite')
                    row = prior
                else:
                    with tempfile.TemporaryDirectory(prefix='.fetch-', dir=out) as temporary:
                        payload = Path(temporary) / 'payload'
                        payload.mkdir()
                        details = materialize(reference, payload, upstream, legacy_checkout)
                        payload.rename(target)
                    row.update(status='fetched', **details)
            except Exception as exc:
                row.update(status='error', reason=f'{type(exc).__name__}: {exc}')
        # Refresh label eligibility even when task files are reused. Keep these
        # findings outside the task directory visible to the judge.
        for key in ('score', 'label_status', 'role', 'exclusion_reason', 'inspection_status'):
            if key in item:
                row[key] = item[key]
            else:
                row.pop(key, None)
        if item.get('score') is False:
            row['exclusion_reason'] = item.get('exclusion_reason', item.get('reason', 'Unverified labels.'))
        report['tasks'].append(row)
        # Retain not-yet-visited records if this resume is interrupted.
        visited = {t['task_id'] for t in report['tasks']}
        checkpoint = {**report, 'tasks': report['tasks'] + [t for k, t in previous.items() if k not in visited]}
        with tempfile.NamedTemporaryFile(mode='w', dir=out.parent, delete=False) as temporary:
            json.dump(checkpoint, temporary, indent=2)
            temporary.write('\n')
        os.replace(temporary.name, manifest_path)
        exclusion = f'; excluded from scoring: {row["exclusion_reason"]}' if row.get('score') is False else ''
        print(f'{task_id}: {row["status"]}{exclusion}', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, default=HERE / 'resolved-examples.json',
                        help='Version-specific examples, or upstream labels with task-selection.json')
    parser.add_argument('--out', type=Path, default=HERE / 'tasks')
    parser.add_argument('--upstream', type=Path, default=ROOT / 'external/terminal-bench')
    parser.add_argument('--legacy-checkout', type=Path,
                        help='Optional Terminal-Bench-1 Git checkout; otherwise use GitHub downloads')
    args = parser.parse_args()
    report = fetch(args.labels, args.out, args.upstream, args.legacy_checkout)
    counts = {status: sum(t['status'] == status for t in report['tasks']) for status in ('fetched', 'unresolved', 'error')}
    counts['excluded_from_scoring'] = sum(t.get('score') is False for t in report['tasks'])
    print(json.dumps(counts))
    raise SystemExit(1 if counts['error'] else 0)


if __name__ == '__main__':
    main()
