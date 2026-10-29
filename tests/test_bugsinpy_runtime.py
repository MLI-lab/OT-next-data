import json

import pytest

from data.tasktrove_bugsinpy.patch import prepared_verifier_files
from data.utils.verifier_runtime import runtime_spec


def spec():
    return runtime_spec(python='3.8.20', requirements=['pytest==7.4.4'], tools=['pytest'],
                        image_python={'image_sha256': 'a' * 64, 'executable': '/usr/local/bin/python3'})


def files(command='python -m unittest -q tests.test_project.TestCase.test_bug'):
    return {'setup_files/setup.sh': b'original task setup\n',
            'tests/requirements.lock': b'pytest==7.4.4\n',
            'tests/test.sh': ('''#!/bin/bash
set -euo pipefail
bash /setup_files/setup.sh
venv_start=$SECONDS
rm -rf /tests/.venv
python3 -m venv /tests/.venv
env -u PYTHONPATH /tests/.venv/bin/python -m pip install --no-deps -r /tests/requirements.lock
echo "verifier venv ready in $((SECONDS - venv_start))s"
cd /app
export PYTHONNOUSERSITE=1
export PYTHONPATH=/app:/app
export PATH=/tests/.venv/bin:$PATH
set +e
bash /tests/run_test.sh
status=$?
set -e
exit "$status"
''').encode(), 'tests/run_test.sh': ('#!/bin/bash\nset -e\n'+command+'\n').encode()}


def test_preserves_task_setup_and_grading_status_and_uses_trusted_venv():
    source = files()
    result = prepared_verifier_files(source, spec())
    assert result['setup_files/setup.sh'] == source['setup_files/setup.sh']
    assert result['tests/test.sh'].split(b'set +e')[1] == source['tests/test.sh'].split(b'set +e')[1]
    test = result['tests/test.sh'].decode()
    # Prepared runtime when staged, else the task's original online install.
    runtime, online = test.split('if [ -n "${OT_VERIFIER_PYTHON:-}" ]; then\n')[1].split('else\n')
    assert '--without-pip --system-site-packages /tests/.venv' in runtime and 'pip install' not in runtime
    assert 'python3 -m venv /tests/.venv' in online and 'pip install --no-deps -r /tests/requirements.lock' in online
    assert 'bash /setup_files/setup.sh' not in test
    assert b'/tests/.venv/bin/python -I -m unittest -q' in result['tests/run_test.sh']
    assert 'tests/ot_run_tests.py' not in result
    assert b"PATHS = ['/app']" in result['tests/ot_project_paths.py']
    assert b'cp /tests/ot_project_paths.py "$ot_site/ot_project_paths.py"' in result['tests/test.sh']
    assert json.loads(result['tests/ot-verifier-runtime.json'])['mount_mode'] == 'writable'


def test_recorded_unittest_body_is_preserved():
    source = files('python3 /tests/run_unittest.py -q test.test_project.TestCase.test_bug')
    source['tests/run_unittest.py'] = b'original trusted report writer'
    result = prepared_verifier_files(source, spec())
    assert result['tests/run_unittest.py'] == source['tests/run_unittest.py']
    assert b'/tests/.venv/bin/python -I /tests/run_unittest.py -q' in result['tests/run_test.sh']


@pytest.mark.parametrize('command', ['python other.py', 'python -m unittest test.foo; echo hi', 'python -m unittest $TEST'])
def test_unreviewed_commands_are_not_partially_migrated(command):
    with pytest.raises(ValueError, match='Unreviewed'):
        prepared_verifier_files(files(command), spec())


def test_rejects_dependency_drift_and_unreviewed_installer():
    source = files(); source['tests/requirements.lock'] = b'pytest==8.4.1\n'
    with pytest.raises(ValueError, match='original verifier lock'):
        prepared_verifier_files(source, spec())
    source = files(); source['tests/test.sh'] = source['tests/test.sh'].replace(b'--no-deps', b'--force-reinstall')
    with pytest.raises(ValueError, match='Unreviewed verifier installer'):
        prepared_verifier_files(source, spec())


def test_task_wheels_keep_original_install_point_and_fallback():
    from data.tasktrove_bugsinpy.patch import prepared_task_setup
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id
    source = files()
    source['setup_files/requirements.lock'] = b'pytest==7.4.4\n'
    source['setup_files/setup.sh'] = (b'#!/bin/bash\nset -euo pipefail\n'
        b'echo restore original source\n'
        b'python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock\n'
        b'echo initialize original project\n')
    package_spec = {'apt': [], 'python': [{'tool': 'pip3', 'requirements': ['pytest==7.4.4']}],
                    'uv': False, 'image_sha256': 'a' * 64}
    record = {'format': OFFLINE_FORMAT, 'spec': package_spec, 'id': spec_id(package_spec),
              'archive_sha256': 'b' * 64, 'status': 'ready'}
    result = prepared_task_setup(source, record)
    setup = result['setup_files/setup.sh']
    assert setup.count(b'# OT_OFFLINE_RECIPE:') == 1
    assert b'--prepare-offline' in setup
    assert setup.index(b'exit $?') < setup.index(b'restore original source')
    assert b'PIP_NO_INDEX=1 PIP_FIND_LINKS="$OT_TASK_OFFLINE/wheels" python3 -m pip install --no-deps --target /app/.deps' in setup
    assert b'else\n  python3 -m pip install --no-deps --target /app/.deps' in setup
    assert setup.index(b'restore original source') < setup.index(b'PIP_NO_INDEX') < setup.index(b'initialize original project')
    ref = json.loads(result['setup_files/ot-task-offline.json'])
    assert 'spec' not in ref and 'request_id' in ref


def test_source_checksum_can_strengthen_but_not_replace_original_url():
    source = files()
    url = 'package @ https://example.org/archive/0123456789abcdef.tar.gz'
    source['tests/requirements.lock'] += (url + '\n').encode()
    runtime = runtime_spec(python='3.8.20', requirements=['pytest==7.4.4', url + '#sha256=' + 'b' * 64],
                           tools=['pytest'], image_python=spec()['image_python'])
    result = prepared_verifier_files(source, runtime)
    assert result['tests/requirements.lock'] == source['tests/requirements.lock']
    changed = dict(runtime)
    changed['requirements'] = [r.replace('0123456789abcdef', 'fedcba9876543210') for r in runtime['requirements']]
    with pytest.raises(ValueError, match='original verifier lock'):
        prepared_verifier_files(source, changed)


def test_export_preserves_history_and_updates_image_binding(tmp_path):
    import hashlib
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.tasktrove_bugsinpy.patch import prepare_runtime_candidates, task_tar
    from harbor_patches.verifier_bundle import FORMAT, spec_id
    from validation.contract import files_digest
    source_files = files(); source_files['instruction.md'] = b'Fix the project regression.\n'
    image = tmp_path / 'image.sif'; image.write_bytes(b'inspected image fixture')
    image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    runtime = spec(); runtime['image_python']['image_sha256'] = image_sha
    identity = spec_id(runtime)
    catalog = {identity: {'format': FORMAT, 'id': identity, 'spec': runtime,
                         'status': 'ready', 'archive_sha256': 'b' * 64}}
    source_hash = files_digest(source_files.items())
    images = {'tasks': {'task': {'content_sha256': source_hash, 'environments': [{'bundle': 'bundle'}]}},
              'bundles': {'bundle': {'files': {'image.sif': {'sha256': image_sha, 'bytes': image.stat().st_size}}}}}
    rows = [{'path': 'task', 'task_binary': task_tar(source_files), 'content_sha256': source_hash,
             'stages_passed': '1,3,4,5', 'run': 'historical', 'validation_labels': ['old'],
             'thinking_successes': 2}]
    table = pa.Table.from_pylist(rows).replace_schema_metadata({b'ot.images.v1': json.dumps(images).encode()})
    source = tmp_path / 'source.parquet'; pq.write_table(table, source)
    recipes = {'task': {'runtime_id': identity, 'source_content_sha256': source_hash,
                       'requirements_sha256': hashlib.sha256(source_files['tests/requirements.lock']).hexdigest(),
                       'image_sha256': image_sha, 'image_path': str(image), 'python': runtime['python']}}
    output = tmp_path / 'output'
    prepare_runtime_candidates(source, output, recipes, catalog, ['task'], 'pinned-fixture')
    actual = pq.read_table(output / 'tasks.parquet'); row = actual.to_pylist()[0]
    assert row['path'] == 'task' and row['thinking_successes'] == 2
    assert row['stages_passed'] == row['run'] == '' and row['validation_labels'] == []
    assert row['content_sha256'] != source_hash
    assert json.loads(actual.schema.metadata[b'ot.images.v1'])['tasks']['task']['content_sha256'] == row['content_sha256']
    evidence = json.loads((output / 'runtime-migration.json').read_text())['tasks']['task']
    assert evidence['historical_row']['run'] == 'historical'
    assert evidence['historical_row']['content_sha256'] == source_hash
    assert evidence['effective_network'] == 'unverified'


@pytest.mark.parametrize('command', [
    'python -m unittest tests.one\nPYTHONPATH=/app python other.py',
    'pytest test_*.py',
    'python -m unittest tests.one # comment',
])
def test_shell_expansion_or_prefixed_commands_require_review(command):
    with pytest.raises(ValueError, match='Unreviewed'):
        prepared_verifier_files(files(command), spec())


def test_matplotlib_source_input_preserves_build_and_rejects_changed_version():
    import io
    import tarfile
    from data.tasktrove_bugsinpy.patch import prepared_task_setup, task_dependency_request, FREETYPE_SOURCE, FREETYPE_BUILD
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id
    def project(version):
        body = ("LOCAL_FREETYPE_VERSION = " + repr(version) + "\n_freetype_hashes = {'2.6.1': " + repr(FREETYPE_SOURCE['sha256']) + "}\n").encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            member = tarfile.TarInfo('setupext.py'); member.size = len(body); tar.addfile(member, io.BytesIO(body))
        return stream.getvalue()
    source = {'setup_files/requirements.lock': b'pytest==7.4.4\n', 'setup_files/project.tar.gz': project('2.6.1'),
              'setup_files/setup.sh': ('#!/bin/bash\nset -euo pipefail\npython3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock\n' + FREETYPE_BUILD + '\n').encode()}
    request = task_dependency_request(source)
    assert request['sources'] == [FREETYPE_SOURCE]
    package_spec = dict(request, image_sha256='a' * 64)
    record = {'format': OFFLINE_FORMAT, 'spec': package_spec, 'id': spec_id(package_spec), 'archive_sha256': 'b' * 64, 'status': 'ready'}
    result = prepared_task_setup(source, record)
    setup = result['setup_files/setup.sh'].decode()
    assert setup.count(FREETYPE_BUILD) == 1
    assert setup.index('tar -xzf "$freetype_archive"') < setup.index(FREETYPE_BUILD)
    assert 'if [ ! -d /app/build/freetype-2.6.1 ]' in setup
    assert result['setup_files/project.tar.gz'] == source['setup_files/project.tar.gz']
    source['setup_files/project.tar.gz'] = project('2.6.2')
    with pytest.raises(ValueError, match='source dependency changed'):
        task_dependency_request(source)


def test_original_no_deps_task_request_preserves_pins_and_online_fallback():
    from data.tasktrove_bugsinpy.patch import prepared_task_setup, task_dependency_request
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id
    command = 'python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock'
    source = {'setup_files/requirements.lock': b'tensorflow==1.15.0\nkeras-applications==1.0.7\n',
              'setup_files/setup.sh': ('#!/bin/bash\nset -euo pipefail\n' + command + '\n').encode()}
    original = task_dependency_request(source)
    request = task_dependency_request(source, no_deps=True)
    assert request['python'][0]['requirements'] == original['python'][0]['requirements']
    assert 'no_deps' not in original['python'][0]
    package_spec = dict(request, image_sha256='a' * 64)
    record = {'format': OFFLINE_FORMAT, 'spec': package_spec, 'id': spec_id(package_spec),
              'archive_sha256': 'b' * 64, 'status': 'ready'}
    result = prepared_task_setup(source, record)
    assert result['setup_files/requirements.lock'] == source['setup_files/requirements.lock']
    assert ('else\n  ' + command + '\nfi').encode() in result['setup_files/setup.sh']
    source['setup_files/setup.sh'] = source['setup_files/setup.sh'].replace(b'--no-deps ', b'')
    with pytest.raises(ValueError, match='no-deps task installation'):
        task_dependency_request(source, no_deps=True)


def test_pinned_source_lock_is_localized_without_changing_original(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    import sys
    from data.tasktrove_bugsinpy.patch import prepared_task_setup, task_dependency_request, TASK_SOURCE_REQUIREMENTS
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id
    requirement = next(r for r in TASK_SOURCE_REQUIREMENTS if r.startswith('twisted @'))
    command = 'python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock'
    original = ('pytest==5.4.3\n' + requirement + '\n').encode()
    files = {'setup_files/requirements.lock':original,
             'setup_files/setup.sh':('#!/bin/bash\nset -euo pipefail\n'+command+'\n').encode()}
    request = task_dependency_request(files)
    assert request['python'][0] == {'tool':'pip3','requirements':['pytest==5.4.3'],'no_deps':True}
    assert request['sources'] == [TASK_SOURCE_REQUIREMENTS[requirement]]
    assert request['python'][1]['requirements'] == [
        'incremental==24.11.0', 'packaging==26.2', 'setuptools==75.3.4',
        'tomli==2.5.0', 'wheel==0.45.1']
    assert request['python'][1]['no_deps'] is True
    package_spec = dict(request, image_sha256='a'*64)
    record = {'format':OFFLINE_FORMAT,'spec':package_spec,'id':spec_id(package_spec),'archive_sha256':'b'*64,'status':'ready'}
    updated = prepared_task_setup(files, record)
    assert updated['setup_files/requirements.lock'] == original
    shell = updated['setup_files/setup.sh'].decode()
    subprocess.run(['bash','-n'], input=shell, text=True, check=True)
    assert 'else\n  '+command+'\nfi' in shell
    code = shell.split("<<'OT_LOCAL_SOURCE_LOCK'\n",1)[1].split('\nOT_LOCAL_SOURCE_LOCK',1)[0]
    compile(code, '<local-source-lock>', 'exec')
    lock = tmp_path/'original.lock'; lock.write_bytes(original)
    local = tmp_path/'sources'/request['sources'][0]['filename'];local.parent.mkdir();local.write_bytes(b'original pinned source fixture')
    # Exercise the emitted program with a fixture digest, preserving its actual path logic.
    code = code.replace('/setup_files/requirements.lock',str(lock)).replace(request['sources'][0]['sha256'],hashlib.sha256(local.read_bytes()).hexdigest())
    monkeypatch.setenv('OT_TASK_OFFLINE',str(tmp_path));target=tmp_path/'local.lock'
    subprocess.run([sys.executable,'-c',code,str(target)],check=True)
    assert target.read_text() == 'pytest==5.4.3\ntwisted @ '+local.as_uri()+'\n'
    assert lock.read_bytes() == original
    local.write_bytes(b'corrupted source')
    assert subprocess.run([sys.executable,'-c',code,str(target)],capture_output=True).returncode != 0
    files['setup_files/requirements.lock'] = original.replace(b'121c98e006a31750661107d390ec2dc4ffe28e8a',b'0000000000000000000000000000000000000000')
    with pytest.raises(ValueError,match='Unreviewed task source'):
        task_dependency_request(files)


def test_source_requirement_whitespace_and_duplicates_require_review():
    from data.tasktrove_bugsinpy.patch import TASK_SOURCE_REQUIREMENTS, task_dependency_request
    requirement = next(iter(TASK_SOURCE_REQUIREMENTS))
    for lock in ['  '+requirement+'\n', requirement+'\n'+requirement+'\n']:
        with pytest.raises(ValueError, match='Unreviewed task source'):
            task_dependency_request({'setup_files/requirements.lock':lock.encode()})


@pytest.mark.parametrize('force', [' --force', ''])
def test_pandas_extension_rebuild_keeps_original_interpreter_and_submitted_sources(force):
    source = files('pytest pandas/tests/test_project.py::test_bug')
    build = 'python3 setup.py build_ext --inplace -j 2 || python3 setup.py build_ext --inplace -j 1\n'
    source['tests/test.sh'] = source['tests/test.sh'].replace(b'set +e\n',build.encode()+b'set +e\n')
    source['setup_files/setup.sh'] = ('cd /app && python3 setup.py build_ext --inplace' + force + ' -j 2 || ( cd /app && python3 setup.py build_ext --inplace' + force + ' -j 1 )\n').encode()
    result = prepared_verifier_files(source,spec())
    assert result['setup_files/setup.sh'] == source['setup_files/setup.sh']
    assert build.encode() in result['tests/test.sh']
    assert result['tests/test.sh'].split(b'export PATH=')[1] == source['tests/test.sh'].split(b'export PATH=')[1]
    source['tests/test.sh'] = source['tests/test.sh'].replace(b'--inplace -j 2',b'--inplace --force -j 2')
    with pytest.raises(ValueError, match='project build commands'):
        prepared_verifier_files(source,spec())


def test_recorded_tornado_preserves_proxy_reset_and_report_scoring():
    source = files('python3 /tests/run_unittest.py -q tornado.test.websocket_test.Test.test_bug')
    reset = b'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n'
    source['tests/test.sh'] = source['tests/test.sh'].replace(b'set +e\n',reset+b'set +e\n')
    source['tests/run_unittest.py'] = b'import unittest\nunittest.main(module=None)\n'
    original_spec=spec(); original_spec['python']='3.7.17'
    result=prepared_verifier_files(source,original_spec)
    assert result['tests/run_unittest.py']==source['tests/run_unittest.py']
    assert result['tests/test.sh'].split(b'export PATH=')[1]==source['tests/test.sh'].split(b'export PATH=')[1]
    assert b'-I /tests/run_unittest.py -q tornado.test.' in result['tests/run_test.sh']
    source['tests/test.sh']=source['tests/test.sh'].replace(reset,reset+b'python3 unreviewed.py\n')
    with pytest.raises(ValueError,match='project build commands'):
        prepared_verifier_files(source,original_spec)


def test_sanic_preserves_junit_and_original_project_build():
    source=files('pytest tests/test_blueprints.py::test_bp_middleware_order')
    source['setup_files/setup.sh']=b'cd /app && python3 setup.py build\n'
    prefix=(b'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n'
            b'rm -f /logs/verifier/junit.xml\n'
            b'export PYTEST_ADDOPTS=--junitxml=/logs/verifier/junit.xml\n')
    source['tests/test.sh']=source['tests/test.sh'].replace(b'set +e\n',prefix+b'set +e\n')
    result=prepared_verifier_files(source,spec())
    assert result['setup_files/setup.sh']==source['setup_files/setup.sh']
    assert result['tests/test.sh'].split(b'export PATH=')[1]==source['tests/test.sh'].split(b'export PATH=')[1]
    assert b'pytest tests/test_blueprints.py::test_bp_middleware_order' in result['tests/run_test.sh']
    source['setup_files/setup.sh']=b'cd /app && python3 setup.py install\n'
    with pytest.raises(ValueError,match='project build commands'):
        prepared_verifier_files(source,spec())


def test_spacy_keeps_extension_rebuild_and_pytest_alias_selectors():
    source=files('py.test spacy/tests/test_errors.py::test_add_codes')
    source['setup_files/setup.sh']=b'cd /app && python3 setup.py build_ext --inplace\n'
    prefix=(b'python3 setup.py build_ext --inplace > /logs/verifier/build.txt 2>&1\n'
            b'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n'
            b'rm -f /logs/verifier/junit.xml\n'
            b'export PYTEST_ADDOPTS=--junitxml=/logs/verifier/junit.xml\n')
    source['tests/test.sh']=source['tests/test.sh'].replace(b'set +e\n',prefix+b'set +e\n')
    observed=spec();observed['python']='3.7.17'
    result=prepared_verifier_files(source,observed)
    assert result['setup_files/setup.sh']==source['setup_files/setup.sh']
    assert result['tests/test.sh'].split(b'export PATH=')[1]==source['tests/test.sh'].split(b'export PATH=')[1]
    assert b'-I -m pytest spacy/tests/test_errors.py::test_add_codes' in result['tests/run_test.sh']
    source['tests/test.sh']=source['tests/test.sh'].replace(b'build_ext --inplace >',b'build_ext --inplace --force >')
    with pytest.raises(ValueError,match='project build commands'):
        prepared_verifier_files(source,observed)


def test_two_phase_pandas_preserves_install_order_flags_and_build():
    import subprocess
    from data.tasktrove_bugsinpy.patch import (
        PANDAS_BUILD_PINS, PANDAS_TASK_INSTALL, PANDAS_VERIFIER_INSTALL,
        task_dependency_request, prepared_task_setup)
    from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id
    source=files('pytest pandas/tests/test_regression.py::test_bug')
    pins=sorted(PANDAS_BUILD_PINS.split()+['pytest==7.4.4'])
    source['tests/requirements.lock']=source['setup_files/requirements.lock']=('\n'.join(pins)+'\n').encode()
    build='python3 setup.py build_ext --inplace -j 2 || python3 setup.py build_ext --inplace -j 1\n'
    source['setup_files/setup.sh']=('#!/bin/bash\nset -euo pipefail\n'+PANDAS_TASK_INSTALL+'\ncd /app && python3 setup.py build_ext --inplace -j 2 || ( cd /app && python3 setup.py build_ext --inplace -j 1 )\n').encode()
    old='python3 -m venv /tests/.venv\nenv -u PYTHONPATH /tests/.venv/bin/python -m pip install --no-deps -r /tests/requirements.lock\n'
    source['tests/test.sh']=source['tests/test.sh'].replace(old.encode(),PANDAS_VERIFIER_INSTALL.encode()).replace(b'set +e\n',build.encode()+b'set +e\n')
    observed=spec();observed['requirements']=pins
    updated=prepared_verifier_files(source,observed)
    assert updated['setup_files/setup.sh']==source['setup_files/setup.sh']
    assert updated['tests/test.sh'].split(b'export PATH=')[1]==source['tests/test.sh'].split(b'export PATH=')[1]
    request=task_dependency_request(source,no_deps=True)
    assert request['python'][0]['no_deps'] is True
    request=dict(request,image_sha256='a'*64)
    record={'format':OFFLINE_FORMAT,'spec':request,'id':spec_id(request),'archive_sha256':'b'*64,'status':'ready'}
    adapted=prepared_task_setup(updated,record)['setup_files/setup.sh'].decode()
    subprocess.run(['bash','-n'],input=adapted,text=True,check=True)
    assert 'else\n  '+PANDAS_TASK_INSTALL+'\nfi' in adapted
    for line in PANDAS_TASK_INSTALL.splitlines():
        assert 'PIP_NO_INDEX=1 PIP_FIND_LINKS="$OT_TASK_OFFLINE/wheels" '+line in adapted
    assert adapted.index(PANDAS_BUILD_PINS)<adapted.index('--upgrade --no-build-isolation')
    source['setup_files/setup.sh']=source['setup_files/setup.sh'].replace(b'--upgrade ',b'')
    with pytest.raises(ValueError,match='installer'):
        prepared_verifier_files(source,observed)


@pytest.mark.parametrize('fixture', ['bugsinpy-spacy1-requirements.lock', 'bugsinpy-spacy10-requirements.lock', 'bugsinpy-spacy2-requirements.lock',
                                     'bugsinpy-spacy5-requirements.lock', 'bugsinpy-spacy7-requirements.lock'])
def test_observed_spacy_build_wheel_is_scoped_and_original_lock_unchanged(fixture):
    from pathlib import Path
    from data.tasktrove_bugsinpy.patch import verifier_build_requirements, task_dependency_request
    source=files('py.test spacy/tests/test_errors.py::test_add_codes')
    lock=(Path(__file__).parent/'fixtures'/fixture).read_bytes()
    source['tests/requirements.lock']=source['setup_files/requirements.lock']=lock
    source['setup_files/setup.sh']=(b'python3 -m pip install --no-deps --target /app/.deps -r /setup_files/requirements.lock\n'
                                    b'cd /app && python3 setup.py build_ext --inplace\n')
    boundary=(b'python3 setup.py build_ext --inplace > /logs/verifier/build.txt 2>&1\n'
              b'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n'
              b'rm -f /logs/verifier/junit.xml\nexport PYTEST_ADDOPTS=--junitxml=/logs/verifier/junit.xml\n')
    source['tests/test.sh']=source['tests/test.sh'].replace(b'set +e\n',boundary+b'set +e\n')
    observed=runtime_spec(python='3.7.17',requirements=lock.decode().splitlines()+['wheel==0.42.0'],tools=['pytest'],
        image_python={'image_sha256':'aec7ee5b48fa77973fcda22bc798da6c0707e5517a29f1b26b0d7cb180f3bba1','executable':'/usr/local/bin/python3'})
    updated=prepared_verifier_files(source,observed)
    assert updated['tests/requirements.lock']==lock
    assert updated['setup_files/requirements.lock']==lock
    assert verifier_build_requirements(updated)==['wheel==0.42.0']
    request=task_dependency_request(updated,no_deps=True)
    assert request['python'][1]=={'tool':'pip3','requirements':['wheel==0.42.0'],'no_deps':True}
    observed['image_python']['image_sha256']='b'*64
    with pytest.raises(ValueError,match='build-tool interpreter or image'):
        prepared_verifier_files(source,observed)
    source['tests/requirements.lock']+=b'\n'
    assert verifier_build_requirements(source)==[]


def test_project_paths_follow_trusted_paths_in_recorder_visible_venv(tmp_path):
    """The real venv layout: trusted modules win, project modules stay importable."""
    import subprocess, sys, sysconfig
    from data.tasktrove_bugsinpy.patch import project_paths_module
    project = tmp_path / 'app'
    project.mkdir()
    (project / 'json.py').write_text('raise SystemExit("project shadowed stdlib")\n')
    (project / 'projmod.py').write_text('VALUE = 7\n')
    venv = tmp_path / 'venv'
    subprocess.run([sys.executable, '-I', '-m', 'venv', '--without-pip', '--system-site-packages', str(venv)], check=True)
    python = venv / 'bin' / 'python'
    site = subprocess.run([str(python), '-I', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
                          check=True, capture_output=True, text=True).stdout.strip()
    (tmp_path / 'venv-site').write_text(site)
    with open(site + '/ot_project_paths.py', 'w') as stream:
        stream.write(project_paths_module([str(project)]))
    with open(site + '/ot_project_paths.pth', 'w') as stream:
        stream.write('import ot_project_paths\n')
    out = subprocess.run([str(python), '-I', '-c',
                          'import json, sys, projmod; print(json.__file__); print(projmod.VALUE); '
                          'print(sys.path.index(%r) > max(i for i, p in enumerate(sys.path) if p.endswith("site-packages")))'
                          % str(project)],
                         cwd=str(project), check=True, capture_output=True, text=True).stdout.splitlines()
    assert not out[0].startswith(str(project))
    assert out[1:] == ['7', 'True']


def test_grading_never_runs_agent_writable_setup():
    from data.tasktrove_bugsinpy.patch import without_grading_setup_call, test_script
    source = files()
    assert b'bash /setup_files/setup.sh' not in without_grading_setup_call(source)['tests/test.sh']
    assert without_grading_setup_call(without_grading_setup_call(source)) == without_grading_setup_call(source)
    assert 'bash /setup_files/setup.sh' not in test_script('pytest tests/test_x.py', 'a' * 64, 'b' * 64, '/app')
