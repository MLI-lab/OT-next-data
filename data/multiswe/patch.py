"""Bake a compatible, pinned grader into the pinned Multi-SWE task images."""
from __future__ import annotations

import argparse
import copy
import io
import hashlib
import json
import re
import subprocess
import shlex
from pathlib import Path, PurePosixPath
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


REVISION = '80de95c62ac792c99dcfa8e26569bcd7d036bdc3'
MARKER = '# Multi-SWE pinned grader runtime v3'


def docker_setup():
    # Embed all setup and pins in the exported Dockerfile. No COPY dependency:
    # the Apptainer deferred-build path executes RUN before replaying COPY.
    requirements = Path(__file__).with_name('grader-requirements.txt').read_text().splitlines()
    pins = ' '.join(shlex.quote(line) for line in requirements if line.strip())
    commands = [
        # Complete tmux package installation in single-UID Apptainer builds.
        # Privileged utmp accounting is unnecessary for task terminals.
        'if ! dpkg-statoverride --list /usr/lib/x86_64-linux-gnu/utempter/utempter; then dpkg-statoverride --add root root 0755 /usr/lib/x86_64-linux-gnu/utempter/utempter; fi',
        'apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tmux && tmux -V && test -z "$(dpkg --audit)"',
        "curl --fail --location --retry 3 'https://github.com/astral-sh/python-build-standalone/releases/download/20250818/cpython-3.11.13%2B20250818-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz' -o /tmp/multiswe-python.tar.gz",
        "echo '22232b7e892726fbf898e449cdae9ccfabf080319655575a8c5e54a39b553c96  /tmp/multiswe-python.tar.gz' | sha256sum --check -",
        'mkdir -p /opt/multiswe-python && tar -xzf /tmp/multiswe-python.tar.gz --strip-components=1 -C /opt/multiswe-python && rm /tmp/multiswe-python.tar.gz',
        '/opt/multiswe-python/bin/python3 -m pip install --no-cache-dir --disable-pip-version-check ' + pins,
        "/opt/multiswe-python/bin/python3 -c 'from multi_swe_bench.harness.dataset import Dataset; from multi_swe_bench.harness.instance import Instance'",
    ]
    return '\n' + MARKER + '\nUSER root\n' + ''.join('RUN ' + command + '\n' for command in commands)


def patch_files(files):
    """Keep task/reference semantics; make grader infrastructure failures explicit."""
    files = dict(files)
    docker = files['environment/Dockerfile'].decode()
    if MARKER in docker:
        return files
    test = files['tests/test.sh'].decode()
    install = [line for line in test.splitlines() if line.startswith('python3 -m pip install --target /tmp/multiswe-grader ')]
    if len(install) != 1 or 'python3 /tests/grade.py ' not in test:
        raise ValueError('Unexpected Multi-SWE verifier layout; refusing partial repair')
    grade = files['tests/grade.py'].decode()
    error_line = '        print(f"Multi-SWE grader error: {type(exc).__name__}: {exc}", file=sys.stderr)'
    if grade.count(error_line) != 1:
        raise ValueError('Unexpected grader error handling')
    grade = grade.replace(error_line, error_line + '\n        reward_path.unlink(missing_ok=True)\n        raise')
    test = test.replace('set -uo pipefail', 'set -euo pipefail')
    test = test.replace(install[0] + '\n', '')
    test = test.replace('export PYTHONPATH=/tmp/multiswe-grader:${PYTHONPATH:-}\n', '')
    test = test.replace('mkdir -p /logs/verifier\n', 'mkdir -p /logs/verifier\nrm -f /logs/verifier/reward.json\n')
    test = test.replace('python3 /tests/grade.py ', '/opt/multiswe-python/bin/python3 -I /tests/grade.py ')
    files.update({
        'environment/Dockerfile': (docker.rstrip() + '\n' + docker_setup()).encode(),
        'tests/test.sh': test.encode(), 'tests/grade.py': grade.encode(),
    })
    return files


def export_shared_recipes(rows):
    """Use the pinned upstream builder, including its per-PR recipe selection."""
    from importlib.metadata import version
    from dataclasses import fields
    from multi_swe_bench.harness.pull_request import PullRequest
    from multi_swe_bench.harness.image import Config, Image
    from multi_swe_bench.harness.instance import Instance
    if version('multi-swe-bench') != '1.1.2':
        raise ValueError('Shared-image conversion requires multi-swe-bench==1.1.2')
    keys = {f.name for f in fields(PullRequest)}
    result = {}
    for task_id, row in rows.items():
        try:
            pr = PullRequest.from_dict({k: v for k, v in row.items() if k in keys})
            image = Instance.create(pr, Config(need_clone=True, global_env=None, clear_env=False)).dependency()
            base = image.dependency()
            if not isinstance(base, Image):
                raise ValueError('No upstream shared Image recipe')
            files = image.files()
            names = [f.name for f in files]
            if len(names) != len(set(names)) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', n) for n in names):
                raise ValueError('Unsupported recipe filenames')
            # Deliberately support only the exact COPY + prepare pattern audited
            # in this source. Extra RUN/ENV/USER/etc. need explicit review.
            actual = [l.strip() for l in image.dockerfile().splitlines() if l.strip()]
            expected = ['FROM ' + base.image_full_name()] + ['COPY ' + n + ' /home/' for n in names] + ['RUN bash /home/prepare.sh']
            if actual != expected:
                raise ValueError('Unsupported upstream Dockerfile instructions')
            if not {'prepare.sh', 'test.patch', 'fix-run.sh'}.issubset(names):
                raise ValueError('Incomplete upstream recipe')
            result[task_id] = {
                'original_image': image.image_full_name(), 'base': base.image_full_name(),
                'base_sha': pr.base.sha, 'repo': pr.repo,
                # Never export the reference answer into agent-visible setup.
                'files': {f.name: f.content for f in files if f.name != 'fix.patch'},
            }
        except Exception as exc:
            result[task_id] = {'error': str(exc)}
    return result


def share_image(files, recipe, locks):
    """Move the audited upstream PR layer to idempotent task initialization."""
    if 'error' in recipe:
        raise ValueError(recipe['error'])
    pinned = locks.get(recipe['base'])
    if not pinned or not re.fullmatch(r'[a-z0-9_./-]+@sha256:[a-f0-9]{64}', pinned):
        raise ValueError('Missing digest-pinned shared base: ' + recipe['base'])
    docker = files['environment/Dockerfile'].decode()
    old = 'FROM ' + recipe['original_image']
    if docker.splitlines()[0] != old:
        raise ValueError('Upstream recipe does not match task image')
    if any(n.startswith('setup_files/') for n in files):
        raise ValueError('Existing task setup requires review')
    if not re.fullmatch(r'[a-f0-9]{40}', recipe['base_sha']):
        raise ValueError('Invalid repository commit')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', recipe['repo']):
        raise ValueError('Invalid repository name')
    updated = dict(files)
    updated['environment/Dockerfile'] = docker.replace(old, 'FROM ' + pinned, 1).encode()
    identity = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    marker = '/setup_files/.multiswe-' + identity
    lines = ['#!/bin/bash', 'set -euo pipefail',
             'if [ -f ' + marker + ' ]; then exit 0; fi']
    for name, content in sorted(recipe['files'].items()):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name == 'fix.patch':
            raise ValueError('Invalid or answer-bearing setup filename')
        updated['setup_files/upstream/' + name] = content.encode()
        lines.append('install -m 0644 ' + shlex.quote('/setup_files/upstream/' + name) + ' ' + shlex.quote('/home/' + name))
    lines += ['rm -f /home/fix.patch', 'cd ' + shlex.quote('/home/' + recipe['repo']),
              'bash /home/prepare.sh',
              '[ "$(git rev-parse HEAD)" = ' + recipe['base_sha'] + ' ]',
              'touch ' + marker]
    updated['setup_files/setup.sh'] = ('\n'.join(lines) + '\n').encode()
    solve = updated['solution/solve.sh'].decode()
    updated['solution/solve.sh'] = solve.replace('set -e\n', 'set -e\nbash /setup_files/setup.sh\n', 1).encode()
    if b'bash /setup_files/setup.sh\n' not in updated['solution/solve.sh']:
        raise ValueError('Unknown reference entrypoint')
    updated['instruction.md'] = updated['instruction.md'].rstrip() + b'\n\nBefore working on the task, run `bash /setup_files/setup.sh`. It initializes this task once and safely does nothing on subsequent calls.\n'
    return updated


def blob_files(blob):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


def patch_blob(blob, recipe=None, locks=None):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        members = archive.getmembers()
        files = {}
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in files:
                    raise ValueError('Duplicate task archive member')
                files[str(name)] = archive.extractfile(member).read()
    updated = patch_files(files)
    if recipe is not None:
        updated = share_image(updated, recipe, locks or {})
    if updated == files:
        return blob
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for member in members:
            member = copy.copy(member)
            if member.isfile():
                data = updated.pop(str(PurePosixPath(member.name)))
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
        for name, data in sorted(updated.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--share-images', action='store_true', help='experimental: move audited upstream PR preparation into setup_files')
    parser.add_argument('--base-image-lock', type=Path, help='JSON mapping upstream base tags to digest-pinned references')
    parser.add_argument('--recipe-python', default=sys.executable, help='Python with multi-swe-bench==1.1.2 installed')
    args = parser.parse_args()
    if args.share_images and not args.base_image_lock:
        parser.error('--share-images requires --base-image-lock')
    locks = json.loads(args.base_image_lock.read_text()) if args.base_image_lock else {}
    sources = [args.source] if args.source.is_file() else sorted(args.source.glob('*.parquet'))
    if not sources:
        raise ValueError('No source Parquets found')
    if args.output.exists():
        raise ValueError('Use a new output directory')
    args.output.mkdir(parents=True)
    for source in sources:
        table = pq.read_table(source)
        rows = table.to_pylist()
        labels = {}
        recipes, unresolved = {}, {}
        if args.share_images:
            row_data = {r['path']: json.loads(blob_files(r['task_binary'])['tests/row.json']) for r in rows}
            generated = subprocess.run([args.recipe_python, str(Path(__file__).resolve()), '--export-shared-recipes'],
                                       input=json.dumps(row_data), capture_output=True, text=True, check=True)
            recipes = json.loads(generated.stdout)
        for row in rows:
            try:
                patched = patch_blob(row['task_binary'], recipes.get(row['path']), locks)
            except ValueError as exc:
                if not args.share_images:
                    raise
                unresolved[row['path']] = str(exc)
                patched = patch_blob(row['task_binary'])
            if patched != row['task_binary']:
                labels[row['path']] = ['baked-grader-runtime', 'explicit-grader-errors', 'network-check-tooling']
            if row['path'] in recipes and row['path'] not in unresolved:
                labels.setdefault(row['path'], []).append('shared-base-with-task-setup')
            row['task_binary'] = patched
        output = args.output / source.name
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        write_patch_report(source, output, patcher=__file__, source={
            'dataset': 'PrimeIntellect/Multi-SWE-RL-Verified', 'revision': REVISION,
            'url': f'https://huggingface.co/datasets/PrimeIntellect/Multi-SWE-RL-Verified/tree/{REVISION}'},
            dropped={}, change_labels=labels, unresolved=unresolved,
            patches=[{'version': 'multiswe-grader-runtime-v3', 'python': '3.11.13', 'grader': '1.1.2', 'share_images': args.share_images,
                      'base_image_lock': locks if args.share_images else {}}])
        if args.share_images:
            (args.output / (source.stem + '.shared-images.json')).write_text(json.dumps({
                'tasks': len(rows), 'converted': len(recipes) - len(unresolved), 'unresolved': unresolved,
                'unique_dockerfiles': len({hashlib.sha256(blob_files(r['task_binary'])['environment/Dockerfile']).hexdigest() for r in rows}),
            }, indent=2) + '\n')
        print(f'{output}: {len(rows)} tasks, {len(labels)} repaired, none dropped')


if __name__ == '__main__':
    if sys.argv[1:] == ['--export-shared-recipes']:
        print(json.dumps(export_shared_recipes(json.load(sys.stdin))))
    else:
        main()
