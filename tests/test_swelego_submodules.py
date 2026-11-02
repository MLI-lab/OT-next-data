import json
import os
import shlex
import subprocess
import sys

import pytest

from data.swelego import source_cache, submodule_cache


def test_submodule_command_and_relative_url_review():
    assert submodule_cache.command('git submodule update --init --depth 50')[-1] == '50'
    assert submodule_cache.url_for('https://github.com/org/main.git', '../child.git') == 'https://github.com/org/child.git'
    with pytest.raises(ValueError, match='Unreviewed'):
        submodule_cache.command('git submodule update --remote')
    with pytest.raises(ValueError, match='HTTPS'):
        submodule_cache.url_for('https://github.com/org/main.git', '/etc/passwd')


def test_nested_submodule_setup_uses_pinned_cache_and_preserves_online_origins(tmp_path, monkeypatch):
    def git(path, *args):
        return subprocess.check_output(['git', '-C', str(path), *args], text=True,
                                       stderr=subprocess.STDOUT).strip()

    def commit(path):
        git(path, 'add', *[p.name for p in path.iterdir() if p.is_file()])
        git(path, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'base')
        return git(path, 'rev-parse', 'HEAD')

    repos = {}
    for name in ('main', 'child', 'grandchild'):
        path = tmp_path / name
        path.mkdir()
        git(path, 'init')
        (path / 'value').write_text(name + ' baseline\n')
        repos[name] = path
    grandchild = commit(repos['grandchild'])

    def link(parent, name, oid):
        (repos[parent] / '.gitmodules').write_text(
            '[submodule "nested"]\n path = nested\n url = ../' + name + '.git\n')
        git(repos[parent], 'update-index', '--add', '--cacheinfo', '160000,' + oid + ',nested')

    link('child', 'grandchild', grandchild)
    child = commit(repos['child'])
    link('main', 'child', child)
    base = commit(repos['main'])
    future = {}
    for name, path in repos.items():
        (path / 'value').write_text('future solution\n')
        future[name] = commit(path)
    urls = {'https://github.com/example/' + name + '.git': path.as_uri() for name, path in repos.items()}
    original_run = subprocess.run

    def local_transport(argv, **kwargs):
        argv = list(argv)
        if 'fetch' in argv:
            argv = [urls.get(arg, arg) for arg in argv]
            if 'origin' in argv:
                argv[argv.index('origin')] = repos['main'].as_uri()
            argv = ['protocol.file.allow=always' if arg == 'protocol.file.allow=never' else arg for arg in argv]
        return original_run(argv, **kwargs)

    text = ('#!/bin/bash\nset -euo pipefail\n# OT_OFFLINE_RECIPE:\ngit init\n'
            'git remote add origin https://github.com/example/main.git\n'
            'git fetch --depth 1 origin ' + base + '\n'
            'git checkout --detach --force ' + base + '\n'
            'git submodule update --init --recursive\n')
    recipe = source_cache.recipe(text)
    assert source_cache.validate_recipe(recipe) == recipe
    destination = tmp_path / 'setup_files'
    with monkeypatch.context() as scoped:
        scoped.setattr(subprocess, 'run', local_transport)
        record = source_cache.prepare(recipe, destination, tmp_path / 'cache')
    files = source_cache.prepared_files({'setup_files/setup.sh': text.encode()}, recipe,
        record, (destination / 'source.git.tar.gz').read_bytes())
    for name, data in files.items():
        (destination / name.split('/', 1)[1]).write_bytes(data)
    script = files['setup_files/setup.sh'].decode().replace('/opt/conda/bin/python', shlex.quote(sys.executable))
    script = script.replace('/setup_files', str(destination))
    (destination / 'setup.sh').write_text(script)
    consumer = tmp_path / 'consumer'
    consumer.mkdir()
    result = subprocess.run(['bash', str(destination / 'setup.sh')], cwd=consumer,
        env={**os.environ, 'GIT_ALLOW_PROTOCOL': 'file'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    for name, path in [('main', consumer), ('child', consumer / 'nested'),
                       ('grandchild', consumer / 'nested/nested')]:
        assert (path / 'value').read_text() == name + ' baseline\n'
        assert git(path, 'config', '--get', 'remote.origin.url') == 'https://github.com/example/' + name + '.git'
        with pytest.raises(subprocess.CalledProcessError):
            git(path, 'cat-file', '-e', future[name])
    assert json.loads((destination / 'ot-source.json').read_text())['id'] == record['id']
