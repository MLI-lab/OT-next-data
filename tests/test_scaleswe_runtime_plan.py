import json

import pytest

from data.scaleswe.patch import prepared_verifier_files
from data.scaleswe.runtime import plan
from validation.contract import files_digest


def observed():
    files = {'tests/test.sh': b'#!/bin/bash\ncd /workspace/project || exit 1\npython /tests/score.py /tests/test_ids.json | tee /logs/verifier/score.txt\n',
             'tests/score.py': b'import pytest\npytest.main()\n',
             'task.toml': b'[environment]\nallow_internet = true\n',
             'solution/solve.sh': b'original fix', 'instruction.md': b'Fix the project.'}
    def package(name, version, requires=(), plugins=(), editable=False):
        return dict(name=name, version=version, requires=list(requires),
                    plugins=[dict(name=name, value=p) for p in plugins],
                    location='/workspace/project' if editable else '/opt/conda/lib/python3.10/site-packages',
                    direct_url=None)
    measured = dict(format='scaleswe-image-observation-v1', python='3.10.19',
        base_executable='/opt/conda/bin/python', cwd='/workspace/project',
        architecture='x86_64', libc='gnu', sys_path=['', '/opt/conda/lib/python3.10/site-packages'],
        packages=[package('pytest', '8.4.1', ['pluggy>=1.5', 'exceptiongroup>=1; python_version < "3.11"']),
                  package('pluggy', '1.6.0'), package('exceptiongroup', '1.3.0'),
                  package('pytest-timeout', '2.4.0', ['pytest>=7'], ['pytest_timeout']),
                  package('project', '1.0', plugins=['project.plugin'], editable=True),
                  package('unrelated', '9.0')])
    observation = dict(returncode=0, source_unchanged=True,
        source_content_sha256=files_digest(files.items()), image_sha256='a' * 64,
        stdout=json.dumps(measured))
    return files, observation, measured


def test_original_marker_environment_and_plugin_closure():
    files, observation, _ = observed()
    result = plan(files, observation)
    assert result['spec']['requirements'] == ['exceptiongroup==1.3.0', 'pluggy==1.6.0',
                                            'pytest-timeout==2.4.0', 'pytest==8.4.1']
    assert result['project_distributions'] == ['project']
    updated = prepared_verifier_files(files, result['spec'], None, observation=observation)
    for name in ('task.toml', 'solution/solve.sh', 'instruction.md', 'tests/score.py'):
        assert updated[name] == files[name]
    assert b'PYTEST_DISABLE_PLUGIN_AUTOLOAD= PYTEST_PLUGINS=' in updated['tests/test.sh']
    assert b"'name': 'pytest-timeout', 'value': 'pytest_timeout'" in updated['tests/ot_plugin_allowlist.py']
    assert b'/bin/python -I /tests/score.py' in updated['tests/test.sh']
    assert b'--prepare' in updated['tests/setup.sh']
    assert not any(name.startswith('setup_files/') for name in updated)


@pytest.mark.parametrize('change', ['missing', 'incompatible', 'direct', 'duplicate', 'url', 'extras'])
def test_declines_unreproducible_dependencies(change):
    files, observation, measured = observed()
    if change == 'missing':
        measured['packages'] = [p for p in measured['packages'] if p['name'] != 'pluggy']
    elif change == 'incompatible':
        measured['packages'][1]['version'] = '0.1'
    elif change == 'direct':
        measured['packages'][1]['direct_url'] = {'url': 'file:///unknown.whl'}
    elif change == 'duplicate':
        measured['packages'].append({**measured['packages'][1], 'version': '0.1'})
    elif change == 'url':
        measured['packages'][0]['requires'] = ['pluggy @ https://example.org/pluggy.whl']
    else:
        measured['packages'][0]['requires'] = ['pluggy[extra]>=1.5']
    observation['stdout'] = json.dumps(measured)
    with pytest.raises(ValueError):
        plan(files, observation)


def test_task_image_and_payload_must_match():
    files, observation, _ = observed()
    spec = plan(files, observation)['spec']
    spec['image_python']['image_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='differs'):
        prepared_verifier_files(files, spec, None, observation=observation)
    files['tests/test.sh'] += b'bash /setup_files/setup.sh\n'
    observation['source_content_sha256'] = files_digest(files.items())
    with pytest.raises(ValueError, match='agent setup'):
        plan(files, observation)


def test_native_runtime_can_regenerate_from_published_pristine_image():
    from harbor_patches.verifier_bundle import FORMAT, spec_id
    files, observation, _ = observed()
    spec = plan(files, observation)['spec']
    key = spec_id(spec)
    record = dict(format=FORMAT, id=key, spec=spec, status='ready', archive_sha256='b' * 64)
    url = 'https://huggingface.co/datasets/FWeindel/validated-tasks/resolve/' + 'c' * 40 + '/images/source.sif'
    updated = prepared_verifier_files(files, spec, {key: record}, observation=observation, image_source=url)
    reference = json.loads(updated['tests/ot-verifier-runtime.json'])
    assert reference['image_source'] == url
    assert 'spec' not in reference
    assert ('--image-source ' + url).encode() in updated['tests/setup.sh']
    standalone = plan(files, observation, interpreter='standalone')['spec']
    with pytest.raises(ValueError, match='single checksum-pinned SIF'):
        prepared_verifier_files(files, standalone, None, observation=observation, image_source=url)


@pytest.mark.parametrize("instrumented", [False, True])
def test_project_startup_hook_cannot_run_before_clean_tools(tmp_path, instrumented):
    import os
    import subprocess
    import sys
    from data.scaleswe.patch import project_paths_module
    files, observation, _ = observed()
    updated = prepared_verifier_files(files, plan(files, observation)['spec'], None, observation=observation)
    project = tmp_path / 'project'; project.mkdir()
    (project / 'sitecustomize.py').write_text('raise SystemExit("agent startup hook executed")\n')
    (project / 'project_module.py').write_text('VALUE = 7\n')
    tests = tmp_path / 'tests'; tests.mkdir()
    for name in ('ot_project_paths.py', 'ot_plugin_allowlist.py'):
        (tests / name).write_bytes(updated['tests/' + name])
    (tests / 'ot_project_paths.py').write_text(project_paths_module([str(project)]))
    setup = tests / 'setup.sh'
    setup.write_text(updated['tests/setup.sh'].decode().replace('/tests/', str(tests) + '/'))
    subprocess.run(['bash', str(setup)], env={**os.environ, 'OT_VERIFIER_PYTHON': sys.executable},
                   check=True, capture_output=True, text=True)
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    if instrumented:
        hook = tmp_path / 'instrumentation'; hook.mkdir()
        (hook / 'sitecustomize.py').write_text('import sys; sys.instrumentation_loaded = True\n')
        env['PYTHONPATH'] = str(hook)
    # Both the isolated grader and ordinary Python child processes need task
    # dependencies. Instrumentation must coexist with our deferred bootstrap.
    for flags in (['-I'], []):
        result = subprocess.run([str(tests / '.ot-verifier-venv/bin/python'), *flags, '-c',
            'import project_module, sys; assert project_module.VALUE == 7; '
            'assert bool(getattr(sys, "instrumentation_loaded", False)) == ' +
            repr(instrumented and not flags)], env=env,
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr



def test_editable_duplicate_metadata_and_transitive_extras():
    files, observation, measured = observed()
    project = measured['packages'][4]
    measured['packages'].append({**project, 'location': '/usr/lib/python3.10/site-packages',
        'direct_url': {'url': 'file:///workspace/project', 'dir_info': {'editable': True}}})
    measured['packages'][1].update(extras=['feature'], requires=['unrelated==9; extra == "feature"'])
    measured['packages'][0]['requires'].append('pluggy[feature]>=1.5')
    observation['stdout'] = json.dumps(measured)
    result = plan(files, observation)
    assert 'unrelated==9.0' in result['spec']['requirements']
    assert result['project_distributions'] == ['project']


def test_task_package_metadata_versions_stay_in_original_environment():
    files, observation, measured = observed()
    measured['project_names'] = ['project']
    measured['packages'].append({**measured['packages'][4], 'version': '2.0',
        'requires': ['different-task-dependency'], 'location': '/usr/lib/python3.10/site-packages',
        'direct_url': {'url': 'https://example.org/project.git'}})
    observation['stdout'] = json.dumps(measured)
    reviewed = plan(files, observation)
    assert reviewed['project_metadata_variants']['project'] == [
        dict(version='1.0', location='/workspace/project'),
        dict(version='2.0', location='/usr/lib/python3.10/site-packages')]
    assert not any(r.startswith('project==') for r in reviewed['spec']['requirements'])
    measured['packages'][-1]['plugins'] = []
    observation['stdout'] = json.dumps(measured)
    with pytest.raises(ValueError, match='Ambiguous installed distribution'):
        plan(files, observation)


def test_plugin_registration_preserves_names_and_blocks_new_entrypoints(monkeypatch):
    import importlib.metadata
    import pluggy
    from types import SimpleNamespace
    from data.scaleswe.runtime import plugin_loader_module
    original = pluggy.PluginManager.load_setuptools_entrypoints
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda name: SimpleNamespace(
        metadata={'Name': name}, version='1.0'))
    def unexpected_discovery():
        raise AssertionError('Agent distribution discovery must not run')
    monkeypatch.setattr(importlib.metadata, 'distributions', unexpected_discovery)
    try:
        exec(plugin_loader_module([dict(name='original-plugin-name', value='math', distribution='original-dist')]), {})
        manager = pluggy.PluginManager('pytest')
        assert manager.load_setuptools_entrypoints('pytest11') == 1
        assert manager.get_plugin('original-plugin-name').__name__ == 'math'
        assert manager.load_setuptools_entrypoints('pytest11') == 0
        blocked = pluggy.PluginManager('pytest')
        blocked.set_blocked('original-plugin-name')
        assert blocked.load_setuptools_entrypoints('pytest11') == 0
    finally:
        pluggy.PluginManager.load_setuptools_entrypoints = original


def test_unpublished_image_export_requires_context_and_runtime_compatibility(tmp_path, monkeypatch):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.scaleswe.patch import prepare_runtime_candidates
    from harbor_patches.image_context import environment_dir_hash
    from harbor_patches.verifier_bundle import FORMAT, spec_id
    files, observation, _ = observed()
    files['environment/Dockerfile'] = b'FROM example.invalid/task:original\n'
    observation['source_content_sha256'] = files_digest(files.items())
    spec = plan(files, observation, interpreter='standalone')['spec']
    context = tmp_path / 'environment'
    context.mkdir()
    (context / 'Dockerfile').write_bytes(files['environment/Dockerfile'])
    bundle = dict(name='image.sif', files={'image.sif': dict(sha256='a' * 64, bytes=1)},
                  provenance=dict(pristine=True, environment_sha256=environment_dir_hash(context)))
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode='w') as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    source = tmp_path / 'source.parquet'
    pq.write_table(pa.Table.from_pylist([dict(path='task', task_binary=packed.getvalue(),
        content_sha256=files_digest(files.items()), stages_passed='1,2,3,4', run='historical')]), source)
    record = dict(id=spec_id(spec), format=FORMAT, spec=spec, status='ready', archive_sha256='b' * 64)
    selection = dict(runtime_id=record['id'], observation=observation,
        image_directory=str(tmp_path), image_manifest=bundle)
    monkeypatch.setattr('validation.publishing.image_release.validate_bundle', lambda *args: None)
    monkeypatch.setattr('data.utils.patch_reporting.write_patch_report', lambda *args, **kw: None)
    with pytest.raises(ValueError, match='compatibility'):
        prepare_runtime_candidates(source, tmp_path / 'missing', {'task': selection}, {record['id']: record}, 'revision')
    selection['runtime_compatibility'] = dict(image_sha256='a' * 64, archive_sha256='b' * 64, returncode=0)
    result = prepare_runtime_candidates(source, tmp_path / 'ready', {'task': selection}, {record['id']: record}, 'revision')
    assert result['changed'] == 1
    bundle['provenance']['environment_sha256'] = 'c' * 64
    with pytest.raises(ValueError, match='context differs'):
        prepare_runtime_candidates(source, tmp_path / 'wrong', {'task': selection}, {record['id']: record}, 'revision')


def test_plugin_guard_waits_for_base_runtime_site_packages():
    import subprocess
    import sys
    import sysconfig
    from data.scaleswe.runtime import plugin_loader_module
    code = ('import sys\n' +
            'exec(' + repr(plugin_loader_module([])) + ', {"__name__": "clean_plugins"})\n' +
            'assert "pluggy" not in sys.modules\n' +
            'sys.path.append(' + repr(sysconfig.get_path('purelib')) + ')\n' +
            'import pluggy\n' +
            'assert pluggy.PluginManager.load_setuptools_entrypoints.__module__ == "clean_plugins"\n')
    result = subprocess.run([sys.executable, '-S', '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_noneditable_project_plugin_still_tests_submitted_code():
    files, observation, measured = observed()
    measured['packages'][4]['location'] = '/usr/local/lib/python3.10/site-packages'
    measured['project_names'] = ['project']
    observation['stdout'] = json.dumps(measured)
    result = plan(files, observation)
    assert 'project' in result['project_distributions']
    assert not any(pin.startswith('project==') for pin in result['spec']['requirements'])
    assert any(plugin['value'] == 'project.plugin' for plugin in result['plugins'])
