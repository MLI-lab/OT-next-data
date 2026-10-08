import copy
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from data.swelego.patch import REVISION, dependencies, patch_files, patch_blob


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
