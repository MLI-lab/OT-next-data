import io
import gzip
import hashlib
import json
import subprocess
import sys
import tarfile

import pytest

from data.swelego import source_cache


def test_source_regeneration_rebinds_verified_task_asset_without_overwriting_cache(tmp_path):
    value = {'url': 'https://github.com/example/project.git', 'base_commit': 'a'*40,
             'fetches': [{'commit': 'a'*40, 'depth': 1}]}
    key = source_cache.spec_id(value)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as stream:
        for name in ['git/HEAD', 'git/config', 'git/objects']:
            member = tarfile.TarInfo(name)
            if name == 'git/objects':
                member.type = tarfile.DIRTYPE
            stream.addfile(member, io.BytesIO())
    records = []
    for index in (1, 2):
        cache = tmp_path / str(index)
        cache.mkdir()
        payload = gzip.compress(raw.getvalue(), mtime=index)
        record = dict(id=key, recipe=value, archive_sha256=hashlib.sha256(payload).hexdigest(),
                      archive_bytes=len(payload), kind='task-source-assets')
        (cache/(key+'.tar.gz')).write_bytes(payload)
        (cache/(key+'.json')).write_text(json.dumps(record))
        records.append(record)
    task = tmp_path/'task'
    source_cache.prepare(value, task, tmp_path/'1')
    (task/'ot-source.json').write_text(json.dumps(records[0]))
    assert source_cache.prepare(value, task, tmp_path/'2') == records[1]
    assert source_cache.digest(task/'source.git.tar.gz') == records[1]['archive_sha256']
    assert source_cache.digest(tmp_path/'1'/(key+'.tar.gz')) == records[0]['archive_sha256']
    (task/'source.git.tar.gz').write_bytes(b'unverified bytes')
    with pytest.raises(ValueError, match='unverified'):
        source_cache.prepare(value, task, tmp_path/'2')


def test_source_recipe_preserves_fetch_depth_and_explicit_tag_objects():
    commit, tag = 'a' * 40, 'b' * 40
    text = (f'git remote add origin https://github.com/example/project.git\n'
            f'git fetch --depth 3 origin {commit}\n'
            f'git checkout --detach --force {commit}\n'
            f'git fetch origin {tag}\n')
    value = source_cache.recipe(text)
    assert value['fetches'] == [{'commit': commit, 'depth': 3}, {'commit': tag, 'depth': None}]
    assert source_cache.validate_recipe(value) == value
    with pytest.raises(ValueError, match='fetch'):
        source_cache.recipe(text.replace('origin ' + tag, 'origin main'))


def test_source_cache_has_base_objects_but_no_future_solution(tmp_path, monkeypatch):
    upstream = tmp_path / 'upstream'
    upstream.mkdir()

    def git(directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args], text=True,
                                       stderr=subprocess.STDOUT).strip()

    git(upstream, 'init')
    (upstream / 'project.py').write_text('bug\n')
    git(upstream, 'add', '.')
    git(upstream, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'base')
    base = git(upstream, 'rev-parse', 'HEAD')
    (upstream / 'project.py').write_text('solution\n')
    git(upstream, 'add', '.')
    git(upstream, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'future')
    future = git(upstream, 'rev-parse', 'HEAD')
    original = subprocess.check_output

    def local_transport(argv, **kwargs):
        # Exercise real Git object transfer without public-network access.
        argv = list(argv)
        if 'fetch' in argv and 'origin' in argv:
            argv[argv.index('origin')] = upstream.as_uri()
            argv[argv.index('protocol.file.allow=never')] = 'protocol.file.allow=always'
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, 'check_output', local_transport)
    value = {'url': 'https://github.com/example/project.git', 'base_commit': base,
             'fetches': [{'commit': base, 'depth': 1}]}
    cache, destination = tmp_path / 'cache', tmp_path / 'task'
    record = source_cache.prepare(value, destination, cache)
    extracted = tmp_path / 'restored'
    with tarfile.open(destination / 'source.git.tar.gz') as stream:
        stream.extractall(extracted, filter='data')
    assert git(extracted / 'git', 'show', base + ':project.py') == 'bug'
    with pytest.raises(subprocess.CalledProcessError):
        git(extracted / 'git', 'cat-file', '-e', future)
    assert git(extracted / 'git', 'remote') == ''
    assert not (extracted / 'git/hooks').exists()
    consumer = tmp_path / 'offline-task'
    consumer.mkdir()
    git(consumer, 'init')
    git(consumer, 'fetch', '--depth', '1', str(extracted / 'git'), base)
    git(consumer, 'checkout', '--detach', base)
    assert (consumer / 'project.py').read_text() == 'bug\n'
    assert git(consumer, 'rev-parse', 'HEAD') == base
    (destination / 'ot-source.json').write_text(json.dumps(record))
    restore = tmp_path / 'restore.py'
    restore.write_text(source_cache.RESTORE)
    restored = subprocess.check_output([sys.executable, str(restore), str(destination), base], text=True).strip()
    assert git(restored, 'show', base + ':project.py') == 'bug'
    assert git(restored, 'rev-parse', 'HEAD') == base
    assert git(restored, 'log', '--all', '--not', 'HEAD', '--oneline') == ''
    # Run the generated normal setup with Git network transports prohibited:
    # initial objects come from the cache, but future commands see upstream.
    import os
    import shlex
    script = ('#!/bin/bash\nset -euo pipefail\n# OT_OFFLINE_RECIPE:\ngit init\n'
              'git remote add origin ' + value['url'] + '\n'
              'git fetch --depth 1 origin ' + base + '\n'
              'git checkout --detach --force ' + base + '\n')
    adapted = source_cache.prepared_files({'setup_files/setup.sh': script.encode()}, value)
    script = adapted['setup_files/setup.sh'].decode().replace(
        '/opt/conda/bin/python /setup_files/ot_restore_source.py /setup_files',
        ' '.join(shlex.quote(str(p)) for p in (sys.executable, restore, destination)))
    script_path = tmp_path/'setup.sh'; script_path.write_text(script)
    cached_consumer = tmp_path/'cached-consumer'; cached_consumer.mkdir()
    checked = subprocess.run(['bash', str(script_path)], cwd=cached_consumer,
        env={**os.environ, 'GIT_ALLOW_PROTOCOL': 'file'}, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert git(cached_consumer, 'config', '--get', 'remote.origin.url') == value['url']
    assert (cached_consumer/'project.py').read_text() == 'bug\n'
    (destination / 'source.git.tar.gz').write_bytes(b'corrupt task asset')
    failed = subprocess.run([sys.executable, str(restore), str(destination), base], capture_output=True, text=True)
    assert failed.returncode == 1 and 'checksum mismatch' in failed.stderr
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: pytest.fail('cache hit ran Git'))
    assert source_cache.prepare(value, tmp_path / 'second-task', cache) == record
    archive = cache / (record['id'] + '.tar.gz')
    archive.write_bytes(b'damaged')
    with pytest.raises(ValueError, match='corrupt'):
        source_cache.prepare(value, tmp_path / 'third-task', cache)
    assert archive.read_bytes() == b'damaged'


@pytest.mark.parametrize('name,kind', [('git/../../escape', 'file'), ('git/config', 'symlink')])
def test_source_archive_rejects_unsafe_members(tmp_path, name, kind):
    path = tmp_path / 'source.tar.gz'
    with tarfile.open(path, 'w:gz') as stream:
        member = tarfile.TarInfo(name)
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
        stream.addfile(member, io.BytesIO())
    with pytest.raises(ValueError, match='Unsafe'):
        source_cache.validate_archive(path)
