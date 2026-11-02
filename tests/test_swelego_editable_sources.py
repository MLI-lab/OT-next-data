import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

import pytest

from data.swelego import editable_sources
from data.swelego.runtime_inputs import plan, prepared_files
from hpc.offline_prep.git_sources import validate
from test_swelego_inputs import fixture


def test_editable_requirement_is_preserved_and_not_replaced_by_a_wheel():
    files, image = fixture()
    line = '-e git+https://github.com/example/dependency.git@' + 'a'*40 + '#egg=dependency'
    files['setup_files/requirements.txt'] += (line + '\n').encode()
    planned = plan(files, image)
    assert planned['request']['git_sources'] == [editable_sources.recipe(line)]
    assert line not in planned['request']['python'][0]['requirements']
    result = prepared_files(files, planned)
    assert result['setup_files/requirements.txt'] == files['setup_files/requirements.txt']
    assert b'ot_editable_sources.py' in result['setup_files/setup.sh']
    assert 'source_requirements' not in planned or line not in planned['source_requirements']


def test_pinned_jupyter_editable_build_inputs_are_cached_without_changing_install():
    files, image = fixture()
    line = '-e git+https://github.com/jupyter/jupyter_core.git@dc840a3bef34316a511aacb5972b5212c4c0e7af#egg=jupyter_core'
    files['setup_files/requirements.txt'] += (line + '\n').encode()
    planned = plan(files, image)
    assert planned['request']['python'][0] == {
        'tool': '/opt/conda/envs/testbed/bin/pip',
        'requirements': ['hatchling>=1.4', 'editables>=0.3'], 'no_deps': False}
    updated = prepared_files(files, planned)
    assert updated['setup_files/requirements.txt'] == files['setup_files/requirements.txt']
    assert editable_sources.build_requirements({**editable_sources.recipe(line), 'commit': 'a'*40}) == []


def test_cached_git_clone_uses_exact_commit_with_network_protocols_disabled(tmp_path):
    repo = tmp_path / 'repository'; repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
    git('init')
    (repo / 'dependency.py').write_text('value = 1\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'base')
    commit = git('rev-parse', 'HEAD')
    bare = tmp_path / 'bare'
    subprocess.run(['git', 'clone', '--bare', str(repo), str(bare)], check=True, capture_output=True)
    line = '-e git+https://github.com/example/dependency.git@' + commit + '#egg=dependency'
    spec = editable_sources.recipe(line)
    root = tmp_path / 'cache'; sources = root / 'sources'; sources.mkdir(parents=True)
    archive = sources / spec['filename']
    with tarfile.open(archive, 'w:gz') as output:
        output.add(bare, arcname='git')
    record = dict(spec, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    (sources / 'ot-git-sources.json').write_text(json.dumps([record]))
    helper = tmp_path / 'restore.py'; helper.write_text(editable_sources.RESTORE)
    env = dict(os.environ, OT_TASK_OFFLINE=str(root), TMPDIR=str(tmp_path),
               GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='user.name', GIT_CONFIG_VALUE_0='Inherited',
               GIT_ALLOW_PROTOCOL='file')
    restored = subprocess.run([sys.executable, str(helper), json.dumps([spec])],
                              env=env, capture_output=True, text=True)
    assert restored.returncode == 0, restored.stderr
    destination = tmp_path / 'editable-checkout'
    command = restored.stdout + '\ngit clone ' + shlex.quote(spec['url']) + ' ' + shlex.quote(str(destination))
    cloned = subprocess.run(['bash', '-eu', '-c', command], env=env, capture_output=True, text=True)
    assert cloned.returncode == 0, cloned.stderr
    assert subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip() == commit
    assert (destination / 'dependency.py').read_text() == 'value = 1\n'
    assert 'GIT_CONFIG_COUNT=3' in restored.stdout
    archive.write_bytes(b'corrupt')
    rejected = subprocess.run([sys.executable, str(helper), json.dumps([spec])], env=env, capture_output=True)
    assert rejected.returncode != 0 and not rejected.stdout


@pytest.mark.parametrize('field,value', [('commit', 'main'), ('url', 'file:///tmp/repo'),
                                       ('filename', '../escape.tar.gz')])
def test_unpinned_or_unsafe_git_source_is_rejected(field, value):
    spec = editable_sources.recipe('-e git+https://github.com/example/repo@' + 'a'*40 + '#egg=repo')
    spec[field] = value
    with pytest.raises(ValueError): validate([spec])
