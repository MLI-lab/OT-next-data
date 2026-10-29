import asyncio
import io
import json
import tarfile
from types import SimpleNamespace

import pytest

pytest.importorskip('bashlex')

from data.terminallego.verifier_runtime import APT_PREFIX, DOWNLOAD, PIP_PREFIX, RUNTIME, UV, patch_entries, rewrite
from harbor_patches.verifier_bundle import (FORMAT, REFERENCE, OFFLINE_FORMAT, OFFLINE_REFERENCE, digest, preflight, spec_id, stage_offline,
                                            validate_offline_archive)

BOOTSTRAP = '''#!/bin/bash
apt-get update
apt-get install -y curl
curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh
source $HOME/.local/bin/env
'''
PYTEST = '''uvx \\
  -p 3.13 \\
  -w pytest==8.4.1 \\
  -w pytest-json-ctrf==0.3.5 \\
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
'''


def script(body):
    return BOOTSTRAP + body + PYTEST + 'echo $? > /logs/verifier/reward.txt\n'


def test_apt_installs_keep_position_and_redirections_but_read_the_staged_repository():
    out, spec, request, unprepared = rewrite(script('apt-get update && apt-get install -y g++ > /dev/null 2>&1 || true\n'))
    assert APT_PREFIX + 'apt-get update && ' + APT_PREFIX + 'apt-get install -y g++ > /dev/null 2>&1 || true' in out
    assert APT_PREFIX + 'apt-get install -y curl' in out
    assert 'astral.sh' not in out and RUNTIME + ' -I -m pytest --ctrf' in out
    assert request == {'apt': ['curl', 'g++'], 'python': [], 'uv': False}
    assert unprepared == []


def test_pip_and_uv_pip_installs_use_the_wheelhouse():
    out, _, request, unprepared = rewrite(script('pip install requests 2>/dev/null\nuv pip install --system flask\n'))
    assert PIP_PREFIX + 'pip install requests 2>/dev/null' in out
    assert UV + ' pip install --system flask' in out
    assert request['python'] == [{'tool': 'pip', 'requirements': ['requests']},
                                 {'tool': 'uv', 'requirements': ['flask']}]
    assert request['uv'] is True and unprepared == []


def test_script_runs_in_fresh_uv_environment_use_prepared_runtime_with_their_packages():
    out, spec, _, _ = rewrite(script('uvx -p 3.13 -w pandas==2.2.3 python solution.py\n'
                                     'uv run -p 3.13 --with numpy plot.py\n'))
    assert RUNTIME + ' solution.py' in out and RUNTIME + ' plot.py' in out
    assert {'pandas==2.2.3', 'numpy'} <= set(spec['requirements'])


def test_unpreparable_commands_stay_unchanged_and_keep_uv_bootstrap():
    body = 'uvx -p 3.13 pip install bcrypt\nnpm install\nwget https://dot.net/v1/dotnet-install.sh\n'
    out, _, request, unprepared = rewrite(script(body))
    assert body in out
    assert 'curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh' in out  # still needed by uvx
    reasons = {u['reason'] for u in unprepared}
    assert DOWNLOAD in reasons and 'runs the tool pip' in reasons
    # apt stays unchanged when the test also downloads: sources may change
    assert APT_PREFIX not in out and request is None


def test_tests_that_change_package_sources_keep_apt_unchanged():
    out, _, request, unprepared = rewrite(script(
        'curl -fsSL https://deb.nodesource.com/setup_18.x | bash -\napt-get install -y nodejs\n'))
    assert APT_PREFIX not in out and 'apt-get install -y nodejs' in out
    assert request is None
    assert any('package sources' in u['reason'] for u in unprepared)


def test_local_service_checks_are_not_downloads():
    _, _, _, unprepared = rewrite(script('curl -s http://localhost:3000/health\n'))
    assert unprepared == []


def test_heredoc_and_substitutions_are_never_prepared():
    body = 'cat > x.sh <<EOF\napt-get install -y vim\nEOF\napt-get install -y $(cat pkgs)\n'
    out, _, request, unprepared = rewrite(script(body))
    assert 'apt-get install -y vim\nEOF' in out
    assert request is None and any(u['reason'].startswith('apt command') for u in unprepared)


def test_without_package_build_installs_stay_as_upstream_wrote_them():
    source = script('apt-get install -y tree\nuv pip install --system flask\n')
    out, spec, request, unprepared = rewrite(source, prepare_packages=False)
    assert APT_PREFIX not in out and PIP_PREFIX not in out and UV not in out
    assert 'astral.sh/uv/0.9.5/install.sh' in out  # uv pip still needs uv
    assert request is None and spec == rewrite(source)[1]


def entries(test_sh):
    result = []
    for name, data in (('environment/Dockerfile', b'FROM ubuntu:22.04\n'), ('tests/test.sh', test_sh.encode())):
        info = tarfile.TarInfo(name); info.size = len(data)
        result.append((info, data))
    return result


def test_patch_entries_binds_ready_package_build_and_falls_back_otherwise():
    source = script('apt-get install -y tree\n')
    first, _, info = patch_entries(entries(source))
    assert info['status'] == 'requested'
    request = info['request']
    built_spec = {'image_key': 'abc', **request}
    built = {'id': spec_id(built_spec), 'spec': built_spec, 'archive_sha256': 'f' * 64, 'status': 'ready'}
    bound, _, info = patch_entries(entries(source), offline={spec_id(request): built})
    files = {i.name: d for i, d in bound}
    ref = json.loads(files['tests/' + OFFLINE_REFERENCE])
    assert ref == {'format': OFFLINE_FORMAT, 'id': built['id'], 'spec': built_spec, 'archive_sha256': 'f' * 64}
    assert info['status'] == 'prepared' and files['environment/Dockerfile'] == b'FROM ubuntu:22.04\n'
    failed, _, info = patch_entries(entries(source), offline={spec_id(request): {'status': 'failed', 'error': 'boom'}})
    files = {i.name: d for i, d in failed}
    assert 'tests/' + OFFLINE_REFERENCE not in files
    assert b'apt-get install -y tree' in files['tests/test.sh'] and APT_PREFIX.encode() not in files['tests/test.sh']
    assert info['status'] == 'unprepared' and info['unprepared'][-1]['reason'] == 'boom'


def test_shared_runtime_reference_reuses_legacy_archive_without_changing_task_image():
    original = entries(script(''))
    _, spec, _ = patch_entries(original)
    assert 'runtime_layout' not in spec  # Existing cached layout-1 artifact.
    key = spec_id(spec)
    record = dict(format=FORMAT, id=key, spec=spec, status='ready', archive_sha256='a' * 64)
    patched, final_spec, _ = patch_entries(original, catalog={key: record})
    files = {i.name: d for i, d in patched}
    ref = json.loads(files['tests/' + REFERENCE])
    assert final_spec == spec and ref['id'] == key
    assert ref['archive_sha256'] == record['archive_sha256']
    assert ref['mount_mode'] == 'writable'  # shared default (prepared-verifier-runtimes.md)
    assert files['environment/Dockerfile'] == dict((i.name, d) for i, d in original)['environment/Dockerfile']
    assert (RUNTIME + ' -I -m pytest').encode() in files['tests/test.sh']
    with pytest.raises(ValueError, match='matching ready'):
        patch_entries(original, catalog={key: {**record, 'id': 'b' * 64}})


def offline_archive(tmp_path, names=('repo/Packages',)):
    spec = {'image_key': 'abc', 'apt': ['tree'], 'python': [], 'uv': False}
    key = spec_id(spec)
    path = tmp_path / (key + '.tar.gz')
    with tarfile.open(path, 'w:gz') as archive:
        for name in names:
            member = tarfile.TarInfo(name); member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
    ref = {'format': OFFLINE_FORMAT, 'id': key, 'spec': spec, 'status': 'ready', 'archive_sha256': digest(path)}
    (tmp_path / (key + '.json')).write_text(json.dumps(ref))
    return path, ref


def test_offline_archive_requires_index_and_known_roots(tmp_path):
    path, _ = offline_archive(tmp_path)
    validate_offline_archive(path)
    other = tmp_path / 'other'
    other.mkdir()
    bad, _ = offline_archive(other, ('python/bin/python3',))
    with pytest.raises(ValueError):
        validate_offline_archive(bad)


def test_preflight_checks_offline_package_references(tmp_path):
    path, ref = offline_archive(tmp_path)
    task = tmp_path / 'task'
    (task / 'tests').mkdir(parents=True)
    (task / 'tests' / OFFLINE_REFERENCE).write_text(json.dumps(ref))
    assert preflight([task], tmp_path)[ref['id']]['format'] == OFFLINE_FORMAT
    path.write_bytes(path.read_bytes() + b'tampering')
    with pytest.raises(ValueError, match='corrupt'):
        preflight([task], tmp_path)


def test_stage_offline_extracts_into_fresh_tests_directory(tmp_path, monkeypatch):
    _, ref = offline_archive(tmp_path)
    context = tmp_path / 'tests'
    context.mkdir()
    (context / OFFLINE_REFERENCE).write_text(json.dumps(ref))
    monkeypatch.setenv('OT_VERIFIER_BUNDLE_CACHE', str(tmp_path))
    target = '/tests/.ot-verifier-offline-123456789abc'
    calls = []

    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(command)
            if command.startswith('mktemp '):
                return SimpleNamespace(return_code=0, stdout=target + '\n', stderr='')
            return SimpleNamespace(return_code=0, stdout='', stderr='')

        async def upload_file(self, source_path, target_path):
            assert target_path == target + '/packages.tar.gz'

    result = asyncio.run(stage_offline(Environment(), context, tmp_path / 'evidence'))
    assert result['directory'] == target
    assert 'sha256sum -c' in calls[-1] and 'Dir::Etc::Parts' in calls[-1] and 'apt.conf.d' in calls[-1]


def test_rewritten_members_are_packed_with_their_new_size():
    from data.terminallego.patch import members, pack
    packed = pack(patch_entries(entries(script('apt-get install -y tree\n')))[0])
    files = {i.name: d for i, d in members(packed)}
    assert files['tests/test.sh'].decode().endswith('echo $? > /logs/verifier/reward.txt\n')


def test_pytest_isolation_is_opt_in_and_keeps_the_spec():
    from data.terminallego.verifier_runtime import PYTEST_ISOLATION
    plain, spec, _, _ = rewrite(script(''))
    isolated, spec2, _, _ = rewrite(script(''), isolate_pytest=True)
    assert PYTEST_ISOLATION not in plain and spec2 == spec
    assert RUNTIME + ' -I -m pytest' + PYTEST_ISOLATION + ' --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py' in isolated
    assert isolated.replace(PYTEST_ISOLATION, '') == plain


def test_pytest_isolation_requires_tests_under_slash_tests():
    body = BOOTSTRAP + 'uvx -p 3.13 -w pytest==8.4.1 -w pytest-json-ctrf==0.3.5 pytest --ctrf c.json test_outputs.py\n'
    rewrite(body)
    with pytest.raises(ValueError, match='under /tests'):
        rewrite(body, isolate_pytest=True)


def test_pytest_isolation_flags_ignore_planted_conftest_and_ini(tmp_path):
    import shlex
    import subprocess
    import sys
    from data.terminallego.verifier_runtime import PYTEST_ISOLATION
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_outputs.py').write_text('def test_fail():\n    assert 1 == 2\n')
    hack = ('import pytest\n@pytest.hookimpl(hookwrapper=True)\n'
            'def pytest_runtest_makereport(item, call):\n    yield\n    raise SystemExit(0)\n'
            'def pytest_collection_modifyitems(items):\n    items.clear()\n')
    for planted in (tmp_path / 'conftest.py', tests / 'conftest.py'):
        planted.write_text(hack)
    (tmp_path / 'pytest.ini').write_text('[pytest]\naddopts = -p no:terminal\n')
    run = lambda *flags: subprocess.run([sys.executable, '-I', '-m', 'pytest', '-p', 'no:cacheprovider', *flags,
                                         str(tests / 'test_outputs.py')], cwd=tmp_path, capture_output=True, text=True)
    assert run().returncode == 5  # planted conftest empties the collection
    flags = shlex.split(PYTEST_ISOLATION.replace('/tests', str(tests)))
    hardened = run(*flags)
    assert hardened.returncode == 1 and '1 failed' in hardened.stdout
