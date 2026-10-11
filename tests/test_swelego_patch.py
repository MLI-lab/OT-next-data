import copy
import re
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from data.swelego.patch import ARCHIVED_TASKS, REVISION, dependencies, patch_files, patch_blob


def fixture():
    commit = 'a' * 40
    row = {'repo': 'example/project', 'base_commit': commit,
           'image_name': 'example/task:latest',
           'install_config': {'install': 'pip install -e .', 'pre_install': ['apt-get update', 'apt-get install -y gcc']},
           'environment': 'dependencies:\\n  - python=3.9.21=build\\n  - attrs=21.4.0=build\\n',
           'requirements': 'attrs @ file:///old/build/attrs\npytest==8.3.5\n-e git+https://github.com/example/project.git@' + commit + '#egg=project\n'}
    lock = {'source_revision': REVISION, 'base': 'example/base@sha256:' + 'b'*64,
            'environments': {'3.9.21': ['attrs=21.4.0=build', 'python=3.9.21=build']}}
    files = {'environment/Dockerfile': b'FROM example/task:latest\nWORKDIR /testbed\n',
             'task.toml': b'schema_version = "1.0"\n[environment]\ncpus = 4\nmemory_mb = 4096\n',
             'tests/test.sh': ('#!/bin/bash\nmkdir -p /logs/verifier\nbase=' + commit + '\n').encode(),
             'tests/test.patch': b'original test patch', 'tests/required.json': b'["test_a"]',
             'solution/solve.sh': b'#!/bin/bash\nset -e\ngit apply /solution/gold.patch\n',
             'solution/gold.patch': b'PRIVATE ANSWER', 'instruction.md': b'Fix the bug.'}
    return files, row, lock


def test_sharing_preserves_grading_and_never_exposes_answer(tmp_path):
    files, row, lock = fixture()
    result = patch_files(files, row, lock)
    other = copy.deepcopy(row)
    other['base_commit'] = 'c'*40
    second = dict(files, **{'tests/test.sh': files['tests/test.sh'].replace(b'a'*40, b'c'*40)})
    changed = patch_files(second, other, lock)
    assert result['environment/Dockerfile'] == changed['environment/Dockerfile']
    assert result['setup_files/setup.sh'] != changed['setup_files/setup.sh']
    for name in ('tests/test.patch', 'tests/required.json', 'solution/gold.patch'):
        assert result[name] == files[name]
    assert all(b'PRIVATE ANSWER' not in v for k,v in result.items() if k.startswith(('environment/', 'setup_files/')))
    assert result['setup_files/requirements.txt'] == b'pytest==8.3.5\n'
    assert b'apt-get update' not in result['setup_files/setup.sh']
    for name, data in result.items():
        if name.endswith('.sh'):
            subprocess.run(['bash', '-n'], input=data, check=True)
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    from validation.stages.task_setup import detect
    assert detect(tmp_path) == 'bash /setup_files/setup.sh'


def test_unknown_local_dependency_is_not_silently_replaced():
    _, row, _ = fixture()
    row['requirements'] = 'private-package @ file:///unavailable/private\n'
    with pytest.raises(ValueError, match='no frozen conda'):
        dependencies(row, ['python=3.9.21=build'])


def test_pymor_runtime_repair_is_scoped_and_requires_original_pin():
    from data.swelego.patch import environment_repairs
    _, row, _ = fixture()
    row['instance_id'] = 'pymor__pymor-1296'
    row['base_commit'] = environment_repairs()[row['instance_id']]['base_commit']
    row['requirements'] = 'numpy==2.0.2\nscipy==1.13.1\n'
    result = dependencies(row, ['python=3.9.21=build'])
    assert 'numpy==1.23.5\n' in result and 'numpy==2.0.2' not in result
    assert 'scipy==1.13.1\n' in result
    row['requirements'] = 'numpy==1.26.4\n'
    with pytest.raises(ValueError, match='original pin'):
        dependencies(row, [])
    row['base_commit'] = '0' * 40
    with pytest.raises(ValueError, match='commit mismatch'):
        dependencies(row, [])


def test_sympy_helper_keeps_exception_assertions_strict(monkeypatch):
    import sys
    import types
    from data.swelego.swelego_sympy_pytest import pytest_configure
    helper = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'sympy', types.ModuleType('sympy'))
    utilities = types.ModuleType('sympy.utilities')
    utilities.pytest = helper
    monkeypatch.setitem(sys.modules, 'sympy.utilities', utilities)
    pytest_configure(None)
    with helper.raises(ValueError):
        raise ValueError('expected')
    with pytest.raises(pytest.fail.Exception):
        with helper.raises(ValueError):
            pass
    with pytest.raises(TypeError):
        with helper.raises(ValueError):
            raise TypeError('wrong exception')
    existing = object()
    helper.raises = existing
    pytest_configure(None)
    assert helper.raises is existing


def test_f5_missing_feature_fails_during_execution_not_collection(tmp_path, monkeypatch):
    import sys
    import types
    from data.swelego.f5_test_imports import repair
    module = types.ModuleType('f5.bigip.tm.asm.tasks')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    original = ('from f5.bigip.tm.asm.tasks import Import_Policy\n'
                'def test_existing():\n    assert 1 == 1\n'
                'def test_feature():\n    assert Import_Policy.value == 42\n')
    paths = []
    for kind in ('functional', 'unit'):
        path = tmp_path / f'f5/bigip/tm/asm/test/{kind}/test_tasks.py'
        path.parent.mkdir(parents=True)
        path.write_text(original)
        paths.append(path)
    repair(tmp_path)
    for path in paths:
        scope = {}
        exec(compile(path.read_text(), str(path), 'exec'), scope)
        scope['test_existing']()
        with pytest.raises(ImportError, match='Import_Policy'):
            scope['test_feature']()
        module.Import_Policy = types.SimpleNamespace(value=42)
        scope['test_feature']()
        del module.Import_Policy


def test_f5_verifier_repair_preserves_grading_inputs():
    files, row, lock = fixture()
    row['instance_id'] = 'F5Networks__f5-common-python-967'
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    result = patch_files(files, row, lock)
    for name in ('tests/test.patch', 'tests/required.json', 'solution/gold.patch'):
        assert result[name] == files[name]
    assert b'python /tests/f5_test_imports.py || exit $?' in result['tests/test.sh']
    assert not any('f5_test_imports' in k for k in result if k.startswith(('environment/', 'setup_files/')))


@pytest.mark.parametrize('task', ['msgpack__msgpack-python-388', 'sdss__sdss_access-69'])
def test_verifier_fixes_preserve_inputs_and_require_reviewed_commit(task):
    from data.swelego.patch import VERIFIER_FIXES
    files, row, lock = fixture()
    commit = VERIFIER_FIXES[task]['commit']
    row.update(instance_id=task, base_commit=commit)
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, commit.encode())
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    result = patch_files(files, row, lock)
    for name in ('tests/test.patch', 'tests/required.json', 'solution/gold.patch'):
        assert result[name] == files[name]
    if task.startswith('msgpack'):
        assert b'MSGPACK_PUREPYTHON=1 bash /tests/eval.sh' in result['tests/test.sh']
    else:
        assert b'PYTEST_PLUGINS=swelego_sdss_clock${PYTEST_PLUGINS:+,$PYTEST_PLUGINS}' in result['tests/test.sh']
        assert 'tests/swelego_sdss_clock.py' in result
    subprocess.run(['bash', '-n'], input=result['tests/test.sh'], check=True)
    row['base_commit'] = 'f' * 40
    files['tests/test.sh'] = files['tests/test.sh'].replace(commit.encode(), b'f' * 40)
    with pytest.raises(ValueError, match='Verifier fix task commit mismatch'):
        patch_files(files, row, lock)


def test_sdss_clock_is_local_to_grading_module(monkeypatch):
    import datetime
    import sys
    import types
    from data.swelego.sdss_verifier_clock import pytest_collection_modifyitems
    module = types.ModuleType('sdss_access.path.path')
    module.datetime = datetime
    real_datetime = datetime.datetime
    monkeypatch.setitem(sys.modules, module.__name__, module)
    pytest_collection_modifyitems(None, None, [])
    assert module.datetime.datetime.now().date() == datetime.date(2025, 4, 1)
    assert module.datetime.datetime.now(datetime.timezone.utc).tzinfo == datetime.timezone.utc
    assert module.datetime.timedelta is datetime.timedelta
    assert datetime.datetime is real_datetime
    monkeypatch.delitem(sys.modules, module.__name__)
    with pytest.raises(RuntimeError, match='did not import'):
        pytest_collection_modifyitems(None, None, [])


@pytest.mark.parametrize('reverse', [False, True])
def test_specific_image_remains_stable_when_shared_core_is_added(reverse):
    from data.swelego.patch import select_image
    _, row, lock = fixture()
    row['requirements'] = 'pytest==8.3.5\npackaging==24.2\n'
    specific = {'python': '3.9.21', 'requires': ['pytest==8.3.5', 'packaging==24.2'],
                'install': ['pytest==8.3.5', 'packaging==24.2']}
    lock['pip_layers'] = {'specific': specific}
    before = select_image(row, lock)
    core = {'python': '3.9.21', 'requires': ['pytest==8.3.5'], 'install': ['pytest==8.3.5']}
    pairs = [('specific', specific), ('core', core)]
    lock['pip_layers'] = dict(reversed(pairs) if reverse else pairs)
    selected, identity = select_image(row, lock)
    assert identity == before[1]
    assert selected['_pip_layer'] == before[0]['_pip_layer']
    row['requirements'] = 'pytest==8.3.5\n'
    assert select_image(row, lock)[1] == '3.9.21:core'
    lock['pip_layers']['ambiguous'] = dict(core)
    with pytest.raises(ValueError, match='Overlapping'):
        select_image(row, lock)


def test_metadata_mismatch_fails_before_conversion():
    files, row, lock = fixture()
    row['base_commit'] = 'c'*40
    with pytest.raises(ValueError, match='base commit'):
        patch_files(files, row, lock)


def test_repeated_setup_preserves_work_without_running_commands(tmp_path):
    files, row, lock = fixture()
    setup = patch_files(files, row, lock)['setup_files/setup.sh'].decode()
    setup = setup.replace('/setup_files/', str(tmp_path) + '/')
    marker = next(line.split('=',1)[1] for line in setup.splitlines() if line.startswith('marker='))
    Path(marker).touch()
    result = subprocess.run(['bash'], input=setup, text=True, capture_output=True)
    assert result.returncode == 0
    assert result.stdout == ''
    assert not (tmp_path/'setup-timing.json').exists()


def test_rejects_unsafe_archive():
    _, row, lock = fixture()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        member = tarfile.TarInfo('../escape')
        archive.addfile(member, io.BytesIO(b''))
    with pytest.raises(ValueError, match='Unsafe'):
        patch_blob(data.getvalue(), row, lock)


def test_metadata_requires_pinned_revision(tmp_path):
    from data.swelego.patch import load_recipes
    path = tmp_path/'recipes.json'
    path.write_text(json.dumps({'dataset': 'PrimeIntellect/SWE-Lego-Real-Data-Verified',
                                'revision': 'wrong', 'recipes': {}}))
    with pytest.raises(ValueError, match='pinned dataset'):
        load_recipes(path, tmp_path/'cache')


def test_main_reports_unconverted_without_dropping_tasks(tmp_path, monkeypatch):
    import sys
    import pyarrow as pa
    import pyarrow.parquet as pq
    import data.swelego.patch as patch
    files, recipe, lock = fixture()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    recipe['instance_id'] = 'example__project-1'
    recipe['requirements'] = 'missing @ file:///original-only/missing\n'
    source = tmp_path/'source.parquet'
    pq.write_table(pa.Table.from_pylist([{'path': recipe['instance_id'], 'task_binary': stream.getvalue()}]), source)
    metadata = tmp_path/'recipes.json'
    metadata.write_text(json.dumps({'dataset': patch.DATASET, 'revision': REVISION,
                                   'recipes': {recipe['instance_id']: recipe}}))
    monkeypatch.setattr(sys, 'argv', ['patch.py', '--source', str(source), '--output', str(tmp_path/'out')])
    patch.main()
    result = pq.read_table(tmp_path/'out/source.parquet').to_pylist()
    assert result[0]['task_binary'] == stream.getvalue()
    manifest = json.loads((tmp_path/'out/source.images.json').read_text())
    assert manifest['converted'] == 0
    assert manifest['unique_images'] == 1
    assert recipe['instance_id'] in manifest['unconverted']


def test_shared_superset_does_not_run_conda_solver():
    files, row, lock = fixture()
    lock['environments']['3.9.21'].append('extra=1.0=build')
    result = patch_files(files, row, lock)
    assert b'conda install' not in result['setup_files/setup.sh']
    assert not json.loads(result['setup_files/swelego.json'])['conda_adjustment']
    # Same name with a different build/version is not an acceptable substitute.
    lock['environments']['3.9.21'][0] = 'attrs=22.0.0=build'
    result = patch_files(files, row, lock)
    assert b'conda install' in result['setup_files/setup.sh']
    assert json.loads(result['setup_files/swelego.json'])['conda_adjustment']


def test_shared_image_preserves_extra_package_channels():
    files, row, lock = fixture()
    lock['extra_channels'] = {'3.9.21': ['bioconda']}
    result = patch_files(files, row, lock)
    assert b'--override-channels -c bioconda -c defaults -c conda-forge' in result['environment/Dockerfile']


def test_shared_image_installs_frozen_artifacts_without_solver(monkeypatch):
    import data.swelego.patch as patch
    files, row, lock = fixture()
    explicit = '@EXPLICIT\nhttps://example.org/python.conda#' + 'a' * 32 + '\n'
    monkeypatch.setattr(patch, 'explicit_conda', lambda specs: explicit)
    result = patch_files(files, row, lock)
    assert result['environment/base-conda.txt'] == explicit.encode()
    docker = result['environment/Dockerfile']
    assert b'https://example.org/python.conda#' in docker
    assert b'conda create -y -n testbed --file /opt/base-conda.txt' in docker
    assert b'--solver' not in docker


def test_wheel_cache_preserves_task_specific_dependencies(monkeypatch):
    import data.swelego.patch as patch
    files, row, lock = fixture()
    monkeypatch.setattr(patch, 'wheel_cache', lambda: {'3.9.21': ['pytest==8.3.5', 'pytest==7.0.1']})
    result = patch_files(files, row, lock)
    docker = result['environment/Dockerfile']
    assert docker.count(b' -m pip wheel ') == 2
    assert b' -m pip install ' not in docker
    assert b'--find-links /opt/swelego-wheels --no-index' in result['setup_files/setup.sh']
    row['requirements'] += 'uncached==1.0\n'
    result = patch_files(files, row, lock)
    assert b'--no-index' not in result['setup_files/setup.sh']
    assert result['setup_files/requirements.txt'].endswith(b'uncached==1.0\n')


def test_installed_profile_builds_only_its_required_wheels(monkeypatch):
    import data.swelego.patch as patch
    files, row, lock = fixture()
    monkeypatch.setattr(patch, 'wheel_cache', lambda: {'3.9.21': ['pytest==8.3.5', 'pytest==7.0.1']})
    lock['pip_layers'] = {'frozen': {'python': '3.9.21', 'requires': ['pytest==8.3.5'],
                                   'install': ['pytest==8.3.5'], 'wheel_subset': True}}
    result = patch_files(files, row, lock)
    docker = result['environment/Dockerfile']
    assert b'pytest==7.0.1' not in docker
    assert b'pip install --no-deps --no-index --find-links /opt/swelego-wheels pytest==8.3.5' in docker
    assert result['setup_files/requirements.txt'] == b'pytest==8.3.5\n'


def test_isolated_backend_keeps_constraints_out_of_frozen_runtime(monkeypatch):
    import data.swelego.patch as patch
    files, row, lock = fixture()
    row['instance_id'] = 'example__project-1'
    monkeypatch.setattr(patch, 'build_backends', lambda: {'example__project-1': {
        'base_commit': row['base_commit'], 'install': [],
        'isolated_build_constraints': ['setuptools==67.7.2', 'setuptools-scm==8.2.0']}})
    result = patch_files(files, row, lock)
    assert result['setup_files/requirements.txt'] == b'pytest==8.3.5\n'
    script = result['setup_files/setup.sh']
    assert b'PIP_NO_DEPS=0 PIP_NO_BUILD_ISOLATION=1 pip install --no-deps -e .' in script
    assert b'setuptools==67.7.2 setuptools-scm==8.2.0 > /setup_files/build-constraints.txt' in script
    subprocess.run(['bash', '-n'], input=script, check=True)


def test_baked_system_libraries_are_removed_from_setup():
    files, row, lock = fixture()
    row['install_config']['pre_install'] = ['apt-get install -y gcc libpulse-dev swig']
    lock['system_packages'] = {'3.9.21': ['libpulse-dev', 'swig']}
    result = patch_files(files, row, lock)
    assert b'libpulse-dev' in result['environment/Dockerfile']
    assert b'apt-get' not in result['setup_files/setup.sh']


def test_compiled_checkout_image_uses_only_setup_inputs():
    import base64
    import shlex
    files, row, lock = fixture()
    row['instance_id'] = 'example__project-1'
    lock['compiled_checkouts'] = {row['instance_id']: row['base_commit']}
    result = patch_files(files, row, lock)
    docker = result['environment/Dockerfile'].decode()
    payloads = [base64.b64decode(shlex.split(line)[3]) for line in docker.splitlines()
                if '| base64 -d >' in line]
    assert payloads
    assert all(b'PRIVATE ANSWER' not in data for data in payloads)
    assert b'/opt/swelego-ready/' in result['setup_files/setup.sh']
    assert 'bash /setup_files/setup.sh' in docker
    assert result['solution/gold.patch'] == files['solution/gold.patch']
    subprocess.run(['bash', '-n'], input=result['setup_files/setup.sh'], check=True)
    lock['compiled_checkouts'][row['instance_id']] = 'b' * 40
    with pytest.raises(ValueError, match='Compiled checkout commit mismatch'):
        patch_files(files, row, lock)


def test_conda_profile_promotes_matching_environment_only():
    import hashlib
    from data.swelego.patch import environment
    files, row, lock = fixture()
    _, specs = environment(row)
    key = hashlib.sha256('\n'.join(specs).encode()).hexdigest()
    lock['environments']['3.9.21'] = ['python=3.9.21=build']
    lock['setup_profiles'] = {key: specs}
    result = patch_files(files, row, lock)
    assert b'conda install' not in result['setup_files/setup.sh']
    assert b'attrs=21.4.0=build' in result['environment/Dockerfile']
    assert ':conda-' in json.loads(result['setup_files/swelego.json'])['image_profile']


@pytest.mark.parametrize('name,spec', [
    ('lief', 'py-lief=0.16.2=build'),
    ('openff-interchange', 'openff-interchange-base=0.4.2=build'),
    ('openff-toolkit', 'openff-toolkit-base=0.16.7=build'),
    ('openff-nagl', 'openff-nagl-base=0.5.2=build'),
])
def test_local_conda_distribution_aliases(name, spec):
    _, row, _ = fixture()
    row['requirements'] = name + ' @ file:///old/build/package\n'
    assert dependencies(row, [spec]) == '\n'
    with pytest.raises(ValueError, match='no frozen conda'):
        dependencies(row, [])


def test_local_harness_resolution_is_auditable_and_path_specific():
    files, row, lock = fixture()
    row['requirements'] += 'swebench_matterhorn @ file:///swebench_matterhorn\n'
    result = patch_files(files, row, lock)
    assert b'matterhorn' not in result['setup_files/requirements.txt']
    assert json.loads(result['setup_files/swelego.json'])['local_resolutions'][0]['action'] == 'omit-collection-harness'
    row['requirements'] = 'swebench_matterhorn @ file:///different/source\n'
    with pytest.raises(ValueError):
        dependencies(row, [])


def test_dask_stale_msgpack_requires_explicit_replacement_pin():
    _, row, _ = fixture()
    row['repo'] = 'dask/dask'
    row['requirements'] = 'msgpack @ file:///tmp/build/80754af9/msgpack-python_1612287171716/work\nmsgpack-python==0.5.6\n'
    assert dependencies(row, []) == 'msgpack-python==0.5.6\n'
    row['requirements'] = row['requirements'].splitlines()[0]
    with pytest.raises(ValueError):
        dependencies(row, [])


def test_oemof_source_uses_original_preinstall_commit():
    from data.swelego.patch import OEMOF_PREINSTALL, OEMOF_COMMIT
    files, row, lock = fixture()
    row['instance_id'] = 'rl-institut__smooth-167'
    row['requirements'] = 'oemof @ file:///smooth/oemof-solph\n'
    row['install_config']['pre_install'] = OEMOF_PREINSTALL.copy()
    result = patch_files(files, row, lock)
    assert OEMOF_COMMIT.encode() in result['setup_files/requirements.txt']
    assert b'git clone https://github.com/oemof' not in result['setup_files/setup.sh']
    row['install_config']['pre_install'][4] = 'git checkout main'
    with pytest.raises(ValueError):
        dependencies(row, [])


def test_task_conda_preserves_nonstandard_source_channel():
    files, row, lock = fixture()
    row['environment'] = 'channels: [openeye, defaults, conda-forge]\n' + row['environment'].replace('\\n', '\n')
    lock['environments']['3.9.21'] = ['python=3.9.21=build']
    setup = patch_files(files, row, lock)['setup_files/setup.sh']
    assert b'-c openeye -c defaults -c conda-forge' in setup


def test_explicit_conda_locks_cover_exact_recorded_packages():
    from data.swelego.patch import explicit_lock, explicit_conda
    from urllib.parse import urlparse
    lock = explicit_lock()
    for profile in lock['profiles'].values():
        urls = explicit_conda(profile['specs']).splitlines()
        assert urls[0] == '@EXPLICIT'
        assert len(urls) == len(profile['specs']) + 1
        for spec, url in zip(profile['specs'], urls[1:]):
            parsed = urlparse(url)
            assert parsed.scheme == 'https'
            assert len(parsed.fragment) == 32
            assert Path(parsed.path).name in {'-'.join(spec.split('=')) + ext
                                             for ext in ('.conda', '.tar.bz2')}
    assert explicit_conda(['python=0.0.0=unknown']) is None


def test_explicit_setup_avoids_solver_and_preserves_grading():
    from data.swelego.patch import explicit_lock
    files, row, lock = fixture()
    profile = next(iter(explicit_lock()['profiles'].values()))
    row['environment'] = 'dependencies:\n' + ''.join('  - ' + spec + '\n' for spec in profile['specs'])
    python = next(s.split('=')[1] for s in profile['specs'] if s.startswith('python='))
    lock['environments'][python] = [next(s for s in profile['specs'] if s.startswith('python='))]
    row['requirements'] = 'pytest==7.0.1\n'
    result = patch_files(files, row, lock)
    assert b'--file /setup_files/conda-explicit.txt' in result['setup_files/setup.sh']
    assert b'--solver' not in result['setup_files/setup.sh']
    assert result['solution/gold.patch'] == files['solution/gold.patch']


def test_toolkit_version_history_is_pinned_and_bounded():
    files, row, lock = fixture()
    row['instance_id'] = 'openforcefield__openff-toolkit-2026'
    result = patch_files(files, row, lock)
    setup = result['setup_files/setup.sh'].decode()
    assert 'git fetch --depth 3 origin ' + row['base_commit'] in setup
    assert 'git merge-base --is-ancestor b7a97ebb8590750e7c5c82f5ce7b1f5ad2ebd6df HEAD' in setup
    assert 'git tag -f 0.16.8 b7a97ebb8590750e7c5c82f5ce7b1f5ad2ebd6df' in setup
    assert 'git fetch --tags' not in setup
    assert 'git remote remove origin\nfinish_phase conda' in setup


def test_holoviews_repairs_only_malformed_tomli_metadata_before_install():
    files, row, lock = fixture()
    row['instance_id'] = 'holoviz__holoviews-6346'
    from data.swelego.patch import environment_repairs
    row['base_commit'] = environment_repairs()[row['instance_id']]['base_commit']
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    setup = patch_files(files, row, lock)['setup_files/setup.sh'].decode()
    cleanup = 'metadata_path="$site_packages/tomli-2.0.1.dist-info/METADATA"'
    assert cleanup in setup
    assert setup.index(cleanup) < setup.index('python -m pip install --no-deps')
    assert 'if [ -d "$metadata_path" ] && [ ! -L "$metadata_path" ]; then rm -rf -- "$metadata_path"; fi' in setup


@pytest.mark.parametrize('task,commit', [
    ('astropy__astropy-16241', '33265f16ebb00d3c6c5812911df0d9b4e1bbb8e0'),
    ('astropy__pyvo-357', '861298fbff5395d61af8192b9b739cff911cb025'),
])
def test_astropy_date_sensitive_repairs_install_verified_leap_table(task, commit):
    files, row, lock = fixture()
    row['instance_id'] = task
    row['base_commit'] = commit
    from data.swelego.patch import environment_repairs
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, commit.encode())
    result = patch_files(files, row, lock)
    setup = result['setup_files/setup.sh'].decode()
    assert 'shutil.copyfile("/setup_files/Leap_Second.dat", astropy_iers_data.IERS_LEAP_SECOND_FILE)' in setup
    assert result['setup_files/Leap_Second.dat']
    assert 'Bulletin 72' in environment_repairs()[task]['reason']


def test_setuptools_scm_restores_full_pinned_history_for_version_test():
    files, row, lock = fixture()
    row['instance_id'] = 'pypa__setuptools_scm-854'
    row['base_commit'] = '8856af656b576f8b8c3612303007bbaa92ec8d50'
    files['tests/test.sh'] = files['tests/test.sh'].replace(
        b'a' * 40, row['base_commit'].encode())
    setup = patch_files(files, row, lock)['setup_files/setup.sh'].decode()
    assert 'git fetch --depth 2147483647 origin 8856af656b576f8b8c3612303007bbaa92ec8d50' in setup


def test_pytrakt_fixes_only_the_verifier_local_calendar_clock():
    files, row, lock = fixture()
    row['instance_id'] = 'moogar0880__PyTrakt-54'
    row['base_commit'] = 'f574c1c1dfc6f65f21296184659aadc2879f2be6'
    files['tests/test.sh'] = files['tests/test.sh'].replace(
        b'a' * 40, row['base_commit'].encode())
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    original_patch = files['solution/gold.patch']
    result = patch_files(files, row, lock)
    assert b'PYTEST_PLUGINS=swelego_pytrakt_clock${PYTEST_PLUGINS:+,$PYTEST_PLUGINS}' in result['tests/test.sh']
    assert 'tests/swelego_pytrakt_clock.py' in result
    assert result['solution/gold.patch'] == original_patch
    import datetime, sys, types
    from data.swelego.swelego_pytrakt_clock import pytest_collection_modifyitems
    module = types.ModuleType('trakt.utils')
    module.datetime = datetime.datetime
    sys.modules[module.__name__] = module
    try:
        pytest_collection_modifyitems(None, None, [])
        assert module.datetime.now().strftime('%Y-%m-%d') == '2026-11-05'
        assert datetime.datetime.now() != module.datetime.now()
    finally:
        del sys.modules[module.__name__]


def test_rpyc_verifier_uses_an_allowed_cpu_without_changing_task_source():
    files, row, lock = fixture()
    row['instance_id'] = 'tomerfiliba-org__rpyc-479'
    from data.swelego.patch import environment_repairs
    row['base_commit'] = environment_repairs()[row['instance_id']]['base_commit']
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    original_solution = files['solution/gold.patch']
    result = patch_files(files, row, lock)
    assert b'min(self._os.sched_getaffinity(0))' in result['tests/swelego_rpyc_affinity.py']
    assert b'python /tests/swelego_rpyc_affinity.py /testbed/tests/test_affinity.py' in result['tests/test.sh']
    assert result['solution/gold.patch'] == original_solution
    from data.swelego.swelego_rpyc_affinity import rewrite
    changed = rewrite('self._os.sched_setaffinity(0, {0, })')
    assert rewrite(changed) == changed


def test_live_service_and_expired_license_archives_have_explicit_reasons():
    for task in ('takeontom__PyPeri-29', 'takeontom__PyPeri-35',
                 'kivy__kivy-6954', 'sphinx-doc__sphinx-5203',
                 'cisagov__check-cve-2019-19781-10'):
        assert ARCHIVED_TASKS[task]['category'] == 'unreliable-live-service-dependency'
        assert 'live' in ARCHIVED_TASKS[task]['reason'].lower()
    assert ARCHIVED_TASKS['PyPSA__linopy-77']['category'] == 'unsupported-expired-proprietary-license'
    assert 'expired on 2026-02-28' in ARCHIVED_TASKS['PyPSA__linopy-77']['reason']
    assert ARCHIVED_TASKS['Yelp__bravado-410']['category'] == 'proxy-sensitive-verification'
    assert 'HTTP 503' in ARCHIVED_TASKS['Yelp__bravado-410']['reason']


def test_dask_1150_gets_measured_8_gib_task_memory_limit():
    files, row, lock = fixture()
    row['instance_id'] = 'dask__dask-1150'
    result = patch_files(files, row, lock)
    assert b'memory_mb = 8192' in result['task.toml']
    from data.swelego.patch import TASK_RESOURCE_FIXES
    fix = TASK_RESOURCE_FIXES[row['instance_id']]
    assert fix['label'] == 'task-memory-8gib'
    assert '7,527,284 KiB' in fix['reason']
    assert '4 GiB' in fix['reason'] and '8 GiB' in fix['reason']


def test_recovered_requirement_sources_are_guarded_by_task_commit():
    from data.swelego.patch import REQUIREMENT_SOURCES
    _, row, _ = fixture()
    task = 'dwavesystems__dwave-system-373'
    commit, original, replacement = REQUIREMENT_SOURCES[task]
    row.update(instance_id=task, base_commit=commit, requirements=original+'\npytest==8.3.5\n')
    assert dependencies(row, ['python=3.9.21=build']).splitlines() == [replacement, 'pytest==8.3.5']
    row['base_commit'] = 'f' * 40
    with pytest.raises(ValueError, match='source task commit mismatch'):
        dependencies(row, ['python=3.9.21=build'])


def test_added_build_tool_cannot_override_recorded_runtime(monkeypatch):
    import data.swelego.patch as patch
    _, row, _ = fixture()
    row.update(instance_id='example__project-1', requirements='tomli==2.2.1\n')
    monkeypatch.setattr(patch, 'build_backends', lambda: {
        row['instance_id']: {'base_commit': row['base_commit'], 'install': ['tomli==2.0.1']}})
    with pytest.raises(ValueError, match='conflicts with frozen runtime'):
        dependencies(row, ['python=3.9.21=build'])


def test_multiple_recovered_sources_preserve_unrelated_pins_and_require_exact_commit():
    from data.swelego.patch import REQUIREMENT_SOURCES, ADDITIONAL_REQUIREMENT_SOURCES
    _, row, _ = fixture()
    task = 'pgmpy__pgmpy-1905'
    first = REQUIREMENT_SOURCES[task]
    second = ADDITIONAL_REQUIREMENT_SOURCES[task]
    row.update(instance_id=task, base_commit=first[0],
               requirements=first[1] + '\n' + second[1] + '\nnumpy==2.0.2\n')
    assert dependencies(row, ['python=3.9.21=build']).splitlines() == [
        first[2], second[2], 'numpy==2.0.2']
    row.update(base_commit='f' * 40, requirements=second[1])
    with pytest.raises(ValueError, match='source task commit mismatch'):
        dependencies(row, ['python=3.9.21=build'])


def test_mtgjson_verifier_appends_the_inherited_pythonpath():
    files, row, lock = fixture()
    row['instance_id'] = 'mtgjson__mtgjson-469'
    row['base_commit'] = 'b3d7bc4531bdca514dc1cf9f4ea5f6eac1104f89'
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    files['tests/eval.sh'] = (b'#!/bin/bash\ncd /testbed\nLANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=. pytest --no-header '
                              b'-rA tests/mtgjson4/test_format.py\nstatus=$?\necho "SWELEGO_PYTEST_EXIT=$status"\nexit 0\n')
    result = patch_files(files, row, lock)
    evaluate = result['tests/eval.sh'].decode()
    assert ' PYTHONPATH=.${PYTHONPATH:+:$PYTHONPATH} pytest --no-header -rA tests/mtgjson4/test_format.py' in evaluate
    assert evaluate.count('pytest') == 1 and 'PYTHONPATH=. pytest' not in evaluate
    assert result['tests/required.json'] == files['tests/required.json']
    assert result['tests/test.patch'] == files['tests/test.patch']
    assert result['solution/gold.patch'] == files['solution/gold.patch']
    from data.swelego.patch import VERIFIER_FIXES
    assert VERIFIER_FIXES[row['instance_id']]['label'] == 'verifier-pythonpath-append'
    # A rewritten verifier command is refused rather than silently left broken.
    files['tests/eval.sh'] = files['tests/eval.sh'].replace(b'PYTHONPATH=. pytest', b'pytest')
    with pytest.raises(ValueError, match='mtgjson verifier command'):
        patch_files(files, row, lock)


def test_holoviews_also_restores_full_history_for_its_setuptools_scm_version():
    files, row, lock = fixture()
    row['instance_id'] = 'holoviz__holoviews-6346'
    from data.swelego.patch import environment_repairs
    repair = environment_repairs()[row['instance_id']]
    row['base_commit'] = repair['base_commit']
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    setup = patch_files(files, row, lock)['setup_files/setup.sh'].decode()
    assert 'git fetch --depth 2147483647 origin ' + row['base_commit'] in setup
    assert 'tomli-2.0.1.dist-info/METADATA' in setup
    assert 'restore-git-version-history' in repair['additional_labels']
    assert 'shallow' in repair['reason']


@pytest.mark.parametrize('task, limit, label, evidence', [
    ('dask__dask-4050', 8192, 'task-memory-8gib', '7.17 GiB'),
    ('dask__dask-4181', 8192, 'task-memory-8gib', '6.65 GiB'),
    ('tobymao__sqlglot-1889', 10240, 'task-memory-10gib', '7.82 GiB'),
    ('microsoft__electionguard-python-381', 8192, 'task-memory-8gib', 'four of five'),
])
def test_measured_memory_limits_cite_their_probe(task, limit, label, evidence):
    files, row, lock = fixture()
    row['instance_id'] = task
    from data.swelego.patch import build_backends
    if task in build_backends():
        row['base_commit'] = build_backends()[task]['base_commit']
        files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    result = patch_files(files, row, lock)
    assert ('memory_mb = %d' % limit).encode() in result['task.toml']
    assert result['tests/test.sh'] != b'' and result['solution/gold.patch'] == files['solution/gold.patch']
    from data.swelego.patch import TASK_RESOURCE_FIXES
    fix = TASK_RESOURCE_FIXES[task]
    assert fix['label'] == label and evidence in fix['reason'] and 'job 9' in fix['reason']


def test_modin_5058_is_archived_after_bounded_ray_still_exceeded_12_gib():
    from data.swelego.patch import TASK_RESOURCE_FIXES
    assert 'modin-project__modin-5058' not in TASK_RESOURCE_FIXES
    entry = ARCHIVED_TASKS['modin-project__modin-5058']
    assert entry['category'] == 'verifier-memory-exceeds-limits'
    assert 'MODIN_CPUS=4' in entry['reason'] and '964168' in entry['reason'] and '12 GiB' in entry['reason']



def test_zarr_restores_the_preceding_release_tag_and_gets_a_measured_memory_limit():
    files, row, lock = fixture()
    row['instance_id'] = 'zarr-developers__zarr-python-2784'
    from data.swelego.patch import build_backends, environment_repairs, TASK_RESOURCE_FIXES
    row['base_commit'] = build_backends()[row['instance_id']]['base_commit']
    assert environment_repairs()[row['instance_id']]['base_commit'] == row['base_commit']
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    history = json.loads((Path(__file__).resolve().parents[1] / 'data/swelego/base-environments.json').read_text())['checkout_tags'][row['instance_id']]
    assert history['base_commit'] == row['base_commit'] and history['tag'] == 'v3.0.2'
    result = patch_files(files, row, {**lock, 'checkout_tags': {row['instance_id']: history}})
    setup = result['setup_files/setup.sh'].decode()
    assert 'git fetch --depth 2147483647 origin ' + row['base_commit'] in setup
    assert 'git merge-base --is-ancestor ' + history['tag_commit'] + ' HEAD' in setup
    assert 'git tag -f v3.0.2 ' + history['tag_commit'] in setup
    assert b'memory_mb = 10240' in result['task.toml']
    assert '8.11 GiB' in TASK_RESOURCE_FIXES[row['instance_id']]['reason']
    assert result['solution/gold.patch'] == files['solution/gold.patch']


@pytest.mark.parametrize('task, key, label, host', [
    ('tcgdex__python-sdk-2', 'no_proxy', 'recorded-http', 'api.tcgdex.net'),
    ('contentful__contentful-management.py-117', 'no_proxy', 'recorded-http', 'api.contentful.com'),
    ('h2non__pook-83', 'runtime_unset', 'mocked-http', 'HTTP_PROXY'),
    ('googleapis__google-auth-library-python-424', 'runtime_unset', 'mocked-http', 'http_proxy'),
])
def test_proxy_sensitive_verifiers_keep_recorded_or_mocked_hosts_direct(task, key, label, host):
    files, row, lock = fixture()
    row['instance_id'] = task
    from data.swelego.patch import TASK_RESOURCE_FIXES, select_image, build_backends
    if task in build_backends():
        row['base_commit'] = build_backends()[task]['base_commit']
        files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    fix = TASK_RESOURCE_FIXES[task]
    assert fix['label'] == label and host in fix[key] and re.search(r'jobs? 9\d{5}', fix['reason'])
    selected, identity = select_image(row, lock)
    assert identity.endswith(':' + label)
    dockerfile = patch_files(files, row, lock)['environment/Dockerfile'].decode()
    if key == 'no_proxy':
        assert selected['_no_proxy'] == host and 'NO_PROXY=' + host in dockerfile
    else:
        assert host in selected['_runtime_unset'] and 'unset ' in dockerfile and 'HTTPS_PROXY' not in selected['_runtime_unset']
    assert b'memory_mb = 4096' in patch_files(files, row, lock)['task.toml']


def test_tox_restores_the_preceding_release_tag_for_its_provisioning_check():
    files, row, lock = fixture()
    row['instance_id'] = 'tox-dev__tox-2643'
    from data.swelego.patch import environment_repairs, build_backends
    row['base_commit'] = environment_repairs()[row['instance_id']]['base_commit']
    if row['instance_id'] in build_backends():
        assert build_backends()[row['instance_id']]['base_commit'] == row['base_commit']
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    history = json.loads((Path(__file__).resolve().parents[1] / 'data/swelego/base-environments.json').read_text())['checkout_tags'][row['instance_id']]
    assert history['tag'] == '4.0.2' and history['base_commit'] == row['base_commit']
    setup = patch_files(files, row, {**lock, 'checkout_tags': {row['instance_id']: history}})['setup_files/setup.sh'].decode()
    assert 'git fetch --depth 2147483647 origin ' + row['base_commit'] in setup
    assert 'git tag -f 4.0.2 ' + history['tag_commit'] in setup
    assert '0.1.dev1' in environment_repairs()[row['instance_id']]['reason']


def test_diagnosed_verifier_defects_are_archived_with_their_evidence():
    expected = {'lundberg__respx-13': 'incompatible-verifier-dependency',
                'lundberg__respx-21': 'incompatible-verifier-dependency',
                'mdsol__rwslib-111': 'incompatible-verifier-dependency',
                'getsentry__sentry-python-79': 'incompatible-verifier-dependency',
                '2gis__k8s-handle-120': 'version-sensitive-test-expectation',
                'pytest-dev__pytest-asyncio-1029': 'version-sensitive-test-expectation',
                'ctypesgen__ctypesgen-150': 'unsupported-host-environment',
                'openstates__pyopenstates-15': 'no-op-passes-required-tests'}
    for task, category in expected.items():
        assert ARCHIVED_TASKS[task]['category'] == category, task
        reason = ARCHIVED_TASKS[task]['reason']
        assert re.search(r'jobs? 9\d{5}', reason) and 'original payload preserved' in reason, task


def test_pook_111_verifier_runs_without_any_proxy_but_setup_keeps_them():
    files, row, lock = fixture()
    row['instance_id'] = 'h2non__pook-111'
    row['base_commit'] = 'fac40e9f571152ba09bc16954548ce51d590ccea'
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    result = patch_files(files, row, lock)
    test = result['tests/test.sh'].decode()
    assert 'env -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy -u ALL_PROXY -u all_proxy bash /tests/eval.sh | tee' in test
    assert 'unset' not in result['environment/Dockerfile'].decode()
    assert result['solution/gold.patch'] == files['solution/gold.patch']


@pytest.mark.parametrize('task, command', [
    ('sigmavirus24__github3.py-1167', 'pytest --no-header -rA tests/unit/test_github.py'),
    ('pybamm-team__PyBaMM-4644', 'pytest -m unit --no-header -rA tests/unit/test_solvers/test_processed_variable.py'),
])
def test_xdist_auto_workers_are_bounded_even_with_marker_options(task, command):
    files, row, lock = fixture()
    row['instance_id'] = task
    from data.swelego.patch import environment_repairs, build_backends
    row['base_commit'] = environment_repairs()[task]['base_commit']
    if task in build_backends():
        assert build_backends()[task]['base_commit'] == row['base_commit']
    files['tests/test.sh'] = files['tests/test.sh'].replace(b'a' * 40, row['base_commit'].encode())
    files['tests/eval.sh'] = ('#!/bin/bash\ncd /testbed\nLANG=C.UTF-8 ' + command + '\nstatus=$?\nexit 0\n').encode()
    files['tests/test.sh'] += b'bash /tests/eval.sh | tee /logs/verifier/test-output.txt\n'
    result = patch_files(files, row, lock)
    assert ' pytest -n 2 ' + command.split('pytest ', 1)[1] in result['tests/eval.sh'].decode()
    assert b'OMP_NUM_THREADS=1' in result['tests/test.sh']
    assert '384-CPU host' in environment_repairs()[task]['reason']
