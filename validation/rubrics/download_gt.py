#!/usr/bin/env python3
"""Download a balanced judge-calibration set, keeping labels outside judge inputs.

Standard library only. Defaults to 20 each: SWE clean/defective and TB
clean/defective. Prefer high confidence, then moderate; never sample Mobius,
incomplete artifacts, or evaluation-ineligible rows. See README.md.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.parse
import urllib.request

REPO = 'FWeindel/task-defect-ground-truth'
CONFIDENCE = {'high': 0, 'moderate': 1}
STRATA = [('swe', 1), ('tb', 1), ('swe', 0), ('tb', 0)]


def family(row):
    return {'swe_bench': 'swe', 'swe_bench_pro': 'swe',
            'terminal_bench': 'tb'}.get(row.get('benchmark'))


def eligible(row):
    return (family(row) is not None and row.get('label') in ('0', '1')
            and row.get('confidence') in CONFIDENCE
            and str(row.get('evaluation_eligible')).lower() == 'true'
            and str(row.get('artifact_complete')).lower() == 'true'
            and row.get('source_id') != 'mobius_task_quality'
            and not row.get('duplicate_of'))


def rank(row, seed):
    # Group first: adding another variant cannot increase a task's sampling odds.
    key = hashlib.sha256(f"{seed}:{row['group_id']}".encode()).hexdigest()
    return CONFIDENCE[row['confidence']], key, row['id']


def select(rows, per_class=20, seed=42, excluded_groups=()):
    """Select one version per group. Reserve defect groups from clean sampling."""
    if per_class < 1:
        raise ValueError('per_class must be positive')
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Manifest contains duplicate IDs')
    excluded_groups = set(excluded_groups)
    pool = [r for r in rows if eligible(r) and r['group_id'] not in excluded_groups]
    defect_groups = {r['group_id'] for r in rows if eligible(r) and r['label'] == '1'}
    selected, used, availability = [], set(excluded_groups), {}
    for fam, label in STRATA:
        candidates = [r for r in pool if family(r) == fam and r['label'] == str(label)
                      and (label == 1 or r['group_id'] not in defect_groups)]
        best = {}
        for row in sorted(candidates, key=lambda r: rank(r, seed)):
            best.setdefault(row['group_id'], row)
        choices = [r for r in best.values() if r['group_id'] not in used]
        key = f"{fam}_{'defective' if label else 'clean'}"
        availability[key] = dict(Counter(r['confidence'] for r in choices))
        if len(choices) < per_class:
            raise ValueError(f'{key}: requested {per_class}, only {len(choices)} eligible '
                             'distinct groups remain. Clean sampling reserves all eligible '
                             'defect groups; lower --per-class or use a different exclusion set.')
        chosen = choices[:per_class]
        selected.extend(dict(r) for r in chosen)
        used.update(r['group_id'] for r in chosen)
    return selected, availability


def read_url(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def get_manifest(revision):
    info = json.loads(read_url(f'https://huggingface.co/api/datasets/{REPO}/revision/'
                              + urllib.parse.quote(revision, safe='')))
    sha = info['sha']
    payload = read_url(f'https://huggingface.co/datasets/{REPO}/resolve/{sha}/manifest.csv')
    return sha, payload, list(csv.DictReader(io.StringIO(payload.decode())))


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_path(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError(f'Unsafe archive path: {name}')
    return Path(*path.parts)


def unpack_bundle(archive, destination, task_id):
    """Accept only files under the one expected task ID; never follow links."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    seen = set()
    with tarfile.open(archive) as tf:
        for member in tf:
            rel = safe_path(member.name)
            if (not member.isfile() or rel.parts[:2] != ('tasks', task_id)
                    or len(rel.parts) < 3 or member.name in seen):
                raise ValueError(f'Unexpected bundle member: {member.name}')
            seen.add(member.name)
            target = destination / Path(*rel.parts[2:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest)
            target.chmod(member.mode & 0o777)


def unpack_repository(archive, destination):
    """Restore a pinned SWE repository; allow only internal relative symlinks."""
    destination.mkdir()
    links, seen, root = [], set(), None
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf:
            raw = safe_path(member.name)
            root = root or raw.parts[0]
            if raw.parts[0] != root:
                raise ValueError('Repository archive has multiple top-level roots')
            if len(raw.parts) == 1:
                if not member.isdir():
                    raise ValueError('Repository root must be a directory')
                continue
            rel = Path(*raw.parts[1:])
            if '.git' in rel.parts or str(rel) in seen:
                raise ValueError(f'Unexpected repository member: {member.name}')
            seen.add(str(rel))
            target = destination / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                target.chmod(member.mode & 0o777)
            elif member.issym():
                if Path(member.linkname).is_absolute():
                    raise ValueError('Absolute repository symlink')
                resolved = (target.parent / member.linkname).resolve()
                if not resolved.is_relative_to(destination.resolve()):
                    raise ValueError('Repository symlink escapes its root')
                links.append((target, member.linkname))
            else:
                raise ValueError(f'Unsupported repository member: {member.name}')
    # Materialize links last so no archive file write can traverse one.
    for target, link in links:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
    for target, _ in links:
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Repository symlink chain escapes its root')


def export_input(task, destination):
    shutil.copytree(task / 'judge_input', destination)
    if (task / 'original/repository.tar.gz').is_file():
        unpack_repository(task / 'original/repository.tar.gz', destination / 'repository')


def download_one(row, revision, output):
    ident = row['id']
    if safe_path(ident).parts != (ident,):
        raise ValueError('Task ID must be one path component')
    rel = safe_path(row['archive_path'])
    if rel.parts != ('tasks', ident, 'task.tar'):
        raise ValueError('Unexpected task archive path')
    archive = output / 'private/bundles' / (ident + '.tar')
    archive.parent.mkdir(parents=True, exist_ok=True)
    def valid():
        return (archive.is_file() and archive.stat().st_size == int(row['archive_bytes'])
                and sha256(archive) == row['archive_sha256'])
    if not valid():
        temporary = archive.with_suffix('.partial')
        url = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{rel.as_posix()}'
        with urllib.request.urlopen(url, timeout=180) as src, temporary.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        if (temporary.stat().st_size != int(row['archive_bytes'])
                or sha256(temporary) != row['archive_sha256']):
            raise ValueError(f'Archive integrity mismatch: {ident}')
        temporary.replace(archive)
    destination = output / 'judge-inputs' / ident
    marker = output / 'private/completed' / (ident + '.json')
    if marker.exists() and destination.is_dir():
        saved = json.loads(marker.read_text())
        if saved == {'revision': revision, 'archive_sha256': row['archive_sha256']}:
            return ident
        raise ValueError(f'{ident}: completion marker belongs to another archive')
    # Work in a fresh directory. Failed extractions are kept for diagnosis, not reused.
    import tempfile
    work = Path(tempfile.mkdtemp(prefix=f'{ident}-', dir=output / 'private/work'))
    task = work / 'task'
    unpack_bundle(archive, task, ident)
    metadata = json.loads((task / 'metadata.json').read_text())
    for key in ('id', 'label', 'group_id', 'confidence'):
        if str(metadata[key]) != row[key]:
            raise ValueError(f'{ident}: metadata disagrees with manifest field {key}')
    if destination.exists():
        raise FileExistsError(f'{destination}: refusing to replace an existing judge input')
    staged = work / 'export'
    export_input(task, staged)
    staged.rename(destination)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'revision': revision, 'archive_sha256': row['archive_sha256']}))
    shutil.rmtree(work)  # Only this invocation's disposable extraction, never source storage.
    return ident


def describe(selected):
    counts = Counter((family(r), r['label'], r['confidence']) for r in selected)
    return {f'{f}_{"defective" if label == "1" else "clean"}_{c}': n
            for (f, label, c), n in sorted(counts.items())}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--per-class', type=int, default=20, help='Per class within each family; total is 4x this (default 20)')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--revision', default='main', help='Resolved to a commit and frozen in selection.json')
    ap.add_argument('--exclude-selection', type=Path, action='append', default=[], help='Exclude all groups in a previous selection.json; repeatable')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--dry-run', action='store_true', help='Inspect balance and size without writing or downloading bundles')
    ap.add_argument('--resume', action='store_true', help='Resume the exact saved selection; sampling arguments are not reapplied')
    args = ap.parse_args()
    if args.workers < 1 or args.per_class < 1:
        ap.error('--workers and --per-class must be positive')
    output = args.output.resolve()
    if args.resume:
        plan = json.loads((output / 'selection.json').read_text())
        if plan['repo_id'] != REPO:
            raise ValueError('Saved selection uses a different dataset')
        selected, revision = plan['examples'], plan['revision']
    else:
        if output.exists() and not args.dry_run:
            raise FileExistsError('Output exists; use --resume for its saved selection or a new path')
        excluded = set()
        for path in args.exclude_selection:
            previous = json.loads(path.read_text())
            excluded.update(r['group_id'] for r in previous['examples'])
        revision, payload, rows = get_manifest(args.revision)
        selected, available = select(rows, args.per_class, args.seed, excluded)
        plan = {'repo_id': REPO, 'revision': revision,
                'manifest_sha256': hashlib.sha256(payload).hexdigest(),
                'per_class': args.per_class, 'seed': args.seed,
                'policy': 'high then moderate; one version per group; reserve all defect groups from clean sampling; no Mobius',
                'excluded_groups': sorted(excluded), 'available_groups': available,
                'examples': selected}
    summary = {'revision': revision, 'tasks': len(selected), 'counts': describe(selected),
               'download_bytes': sum(int(r['archive_bytes']) for r in selected),
               'sources': dict(Counter(r['source_id'] for r in selected))}
    print(json.dumps(summary, indent=2), flush=True)
    if args.dry_run:
        return
    if not args.resume:
        output.mkdir(parents=True)
        (output / 'selection.json').write_text(json.dumps(plan, indent=2) + '\n')
    (output / 'private/work').mkdir(parents=True, exist_ok=True)
    (output / 'judge-inputs').mkdir(exist_ok=True)
    status_path = output / 'download-status.json'
    completed = json.loads(status_path.read_text())['completed'] if status_path.exists() else []
    if any(not (output / 'judge-inputs' / ident).is_dir() for ident in completed):
        raise ValueError('Completed judge inputs are missing; restore them or choose a fresh output')
    pending = [r for r in selected if r['id'] not in completed]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for ident in executor.map(lambda r: download_one(r, revision, output), pending):
            completed.append(ident)
            temporary = status_path.with_suffix('.partial')
            temporary.write_text(json.dumps({'completed': completed, 'total': len(selected)}, indent=2) + '\n')
            temporary.replace(status_path)
            print(f'Prepared {len(completed)}/{len(selected)}: {ident}', flush=True)
    print(f'Judge inputs: {output / "judge-inputs"}')
    print('Mount only an individual judge-inputs/<id> directory. Keep selection.json and private/ inaccessible to judges.')


if __name__ == '__main__':
    main()
