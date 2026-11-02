import json

import pytest

from data.swelego.runtime_inputs import plan, prepared_files, preinstall_python
from harbor_patches.verifier_bundle import OFFLINE_FORMAT, spec_id


def fixture(conda=False):
    conda_command = ('/opt/conda/bin/conda install -y -n testbed --file /setup_files/conda-explicit.txt\n'
                     '/opt/conda/bin/conda clean -afy\n') if conda else ''
    setup = ('#!/bin/bash\nset -euo pipefail\nstarted=$SECONDS\n'
             'finish_phase conda\n' + conda_command +
             'finish_phase preinstall\napt-get update && apt-get install -y cmake\n'
             'finish_phase dependencies\n'
             'python -m pip install --no-deps --find-links /opt/swelego-wheels -r /setup_files/requirements.txt\n'
             'finish_phase install\npip install -e .[dev]\n')
    files = {'setup_files/setup.sh': setup.encode(),
             'setup_files/swelego.json': b'{"compiled_checkout": false}',
             'setup_files/requirements.txt': b'pytest==7.4.4\n',
             'task.toml': b'[environment]\nallow_internet = true\n'}
    bundle = {'name': 'base.sif', 'files': {'base.sif': {'sha256': 'a'*64},
              'base.overlay.img': {'sha256': 'b'*64}, 'base.deferred.json': {'sha256': 'c'*64}}}
    return files, bundle


def test_preinstall_pip_downloads_keep_original_install_order_and_options():
    files, bundle = fixture()
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'finish_phase preinstall\n', b'finish_phase preinstall\n'
        b'python -m pip install --upgrade pip\n'
        b'python -m pip install --no-deps ply==3.4\n')
    planned = plan(files, bundle)
    assert [p['requirements'] for p in planned['request']['python']] == [
        ['pip'], ['ply==3.4'], ['pytest==7.4.4']]
    assert [p['no_deps'] for p in planned['request']['python']] == [False, True, True]
    setup = prepared_files(files, planned)['setup_files/setup.sh']
    assert setup.index(b'install "${ot_pip_links[@]}" --upgrade pip') < setup.index(b'install "${ot_pip_links[@]}" --no-deps ply==3.4')


@pytest.mark.parametrize('command', ['pip install -e .[tests]', 'pip install -r requirements.txt',
                                      'pip install package @ https://example.org/source.zip',
                                      'pip install pytest; touch /tmp/sentinel'])
def test_project_or_unreviewed_preinstall_is_not_treated_as_package_cache(command):
    assert preinstall_python(command) is None


def test_pytorch_preinstall_preserves_cpu_find_links():
    result = preinstall_python('pip install torch torchvision -f https://download.pytorch.org/whl/cpu/torch_stable.html -U')
    assert result['requirements'] == ['torch', 'torchvision']
    assert 'PIP_FIND_LINKS=https://download.pytorch.org/whl/cpu/torch_stable.html' in result['tool']


def test_reviewed_project_requirement_files_keep_their_installation_position():
    import shlex
    from data.swelego.runtime_inputs import CLORM_REQUIREMENTS
    files, bundle = fixture()
    original = b'pip install -r requirements.txt -r requirements-dev.txt'
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake', original)
    files['setup_files/swelego.json'] = json.dumps({
        'compiled_checkout': False, 'repo': 'potassco/clorm',
        'base_commit': '3b3d795e36a6924a77fd5a4d73b71f102cc3e648'}).encode()
    planned = plan(files, bundle)
    assert planned['request']['python'][0]['requirements'] == CLORM_REQUIREMENTS
    assert planned['request']['python'][0]['no_deps'] is False
    setup = prepared_files(files, planned)['setup_files/setup.sh'].decode()
    command = next(line for line in setup.splitlines() if line.startswith('pip install '))
    assert shlex.split(command)[3:] == CLORM_REQUIREMENTS
    assert setup.index(command) < setup.index('finish_phase dependencies')
    files['setup_files/swelego.json'] = files['setup_files/swelego.json'].replace(
        b'3b3d795e36a6924a77fd5a4d73b71f102cc3e648', b'a' * 40)
    with pytest.raises(ValueError, match='preinstall'):
        plan(files, bundle)


def test_package_inputs_preserve_install_order_online_fallback_and_network_policy():
    files, bundle = fixture()
    planned = plan(files, bundle)
    assert planned['request']['python'][0]['no_deps'] is True
    assert planned['request']['apt'] == ['cmake']
    result = prepared_files(files, planned)
    setup = result['setup_files/setup.sh'].decode()
    assert setup.index('"--prepare"') < setup.index('started=$SECONDS')
    assert setup.index('apt-get update && apt-get install') < setup.index('python -m pip install') < setup.index('pip install "${ot_pip_links[@]}" -e .')
    assert 'if [ -n "${OT_TASK_OFFLINE:-}" ]; then' in setup
    assert 'export PIP_NO_INDEX=1' in setup
    assert result['task.toml'] == files['task.toml']
    spec = {**planned['request'], **planned['binding']}
    record = {'id': spec_id(spec), 'format': OFFLINE_FORMAT, 'spec': spec,
              'status': 'ready', 'archive_sha256': 'd'*64}
    bound = prepared_files(files, planned, record)
    reference = json.loads(bound['setup_files/ot-task-runtime.json'])
    assert reference['archive_sha256'] == 'd'*64 and 'spec' not in reference
    assert reference['request_id'] == spec_id(planned['request'])


def test_conda_inputs_need_verified_source_bytes_and_keep_original_lock():
    files, bundle = fixture(conda=True)
    url = 'https://example.invalid/pkg-1.0-build.conda'
    files['setup_files/conda-explicit.txt'] = ('@EXPLICIT\n'+url+'#'+'e'*32+'\n').encode()
    with pytest.raises(ValueError, match='verified SHA256'):
        plan(files, bundle)
    planned = plan(files, bundle, source_catalog={url: {'md5': 'e'*32, 'sha256': 'f'*64}})
    result = prepared_files(files, planned)
    assert result['setup_files/conda-explicit.txt'] == files['setup_files/conda-explicit.txt']
    assert b'ot_conda_lock=/setup_files/conda-explicit.txt' in result['setup_files/setup.sh']
    assert b'--file "$ot_conda_lock"' in result['setup_files/setup.sh']
    assert planned['request']['sources'][0]['sha256'] == 'f'*64


def test_conda_mutex_archive_names_are_accepted_by_shared_preparation(tmp_path):
    import hashlib
    import io
    from hpc.offline_prep.offline_sources import prepare, verify_staged
    payload = b'checksum-bound Conda package'
    sources = [{'url': 'https://repo.anaconda.com/pkgs/main/linux-64/' + name,
                'filename': name, 'sha256': hashlib.sha256(payload).hexdigest()}
               for name in ('_libgcc_mutex-0.1-main.conda', '_openmp_mutex-5.1-1_gnu.conda')]
    records = prepare(sources, tmp_path, opener=lambda *args, **kwargs: io.BytesIO(payload))
    verify_staged(records, tmp_path)
    assert len(records) == 2


def test_no_partial_rewrite_of_unreviewed_downloads():
    files, bundle = fixture()
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake', b'curl https://example.invalid/installer | bash')
    with pytest.raises(ValueError, match='preinstall'):
        plan(files, bundle)


def test_reviewed_egg_metadata_bootstrap_stays_in_normal_setup():
    files, bundle = fixture()
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake', b'python setup.py egg_info')
    with pytest.raises(ValueError, match='preinstall'):
        plan(files, bundle)
    files['setup_files/swelego.json'] = json.dumps({
        'compiled_checkout': False, 'repo': 'pypa/setuptools_scm',
        'base_commit': '9f360e6f70b3818275580d3f4ca285a81a5b86ce'}).encode()
    planned = plan(files, bundle)
    assert planned['deferred_initialization'] == ['python setup.py egg_info']
    assert planned['request']['apt'] == []
    setup = prepared_files(files, planned)['setup_files/setup.sh']
    assert b'finish_phase preinstall\npython setup.py egg_info\nfinish_phase dependencies' in setup


def test_reviewed_build_constraint_stays_in_normal_setup():
    files, bundle = fixture()
    original = b"printf '%s\\n' setuptools==75.8.0 > /setup_files/dependency-build-constraints.txt\nexport PIP_CONSTRAINT=/setup_files/dependency-build-constraints.txt"
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake', original)
    files['setup_files/swelego.json'] = json.dumps({
        'compiled_checkout': False, 'repo': 'skypilot-org/skypilot',
        'base_commit': '86e242f811bbab00fc896a3f6a6b3dd591eb6626'}).encode()
    planned = plan(files, bundle)
    assert planned['request']['python'][-1]['constraints'] == ['setuptools==75.8.0']
    assert original in prepared_files(files, planned)['setup_files/setup.sh']


def test_gdal_constraints_and_links_are_available_without_task_checkout():
    files, bundle = fixture()
    original = b'PIP_CONSTRAINT=/setup_files/requirements.txt pip install --find-links=https://girder.github.io/large_image_wheels --no-cache GDAL\nPIP_CONSTRAINT=/setup_files/requirements.txt pip install localtileserver'
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake', original)
    files['setup_files/swelego.json'] = json.dumps({
        'compiled_checkout': False, 'repo': '12rambau/sepal_ui',
        'base_commit': '27a18eba37bec8ef1cabfa6bcc4022164ebc4c3b'}).encode()
    files['setup_files/requirements.txt'] = b'GDAL==3.11.0\nlocaltileserver==0.10.6\n'
    planned = plan(files, bundle)
    requests = planned['request']['python']
    assert [r['requirements'] for r in requests[:2]] == [['GDAL'], ['localtileserver']]
    assert all(r['constraints'] == ['GDAL==3.11.0', 'localtileserver==0.10.6'] for r in requests[:2])
    assert requests[-1]['find_links'] == ['/opt/swelego-wheels', '/ot-out/wheels']
    setup = prepared_files(files, planned)['setup_files/setup.sh'].decode()
    assert 'ot_gdal_find_links="$OT_TASK_OFFLINE/wheels"' in setup
    assert '--find-links="${ot_gdal_find_links:-https://girder.github.io/large_image_wheels}"' in setup
    assert setup.index('pip install --find-links=') < setup.index('pip install localtileserver')


def test_dvc_preparation_preserves_editable_initialization_and_resolves_only_dependencies():
    files, bundle = fixture()
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'apt-get update && apt-get install -y cmake', b'pip install -e .[tests]')
    files['setup_files/swelego.json'] = json.dumps({
        'compiled_checkout': False, 'repo': 'iterative/dvc',
        'base_commit': 'dd7876e72f0317209144318c0639f8c1e8b00199'}).encode()
    planned = plan(files, bundle)
    requirements = planned['request']['python'][0]['requirements']
    assert 'psutil==5.6.2' in requirements and 'pytest>=4.6.0' in requirements
    assert "black==19.3b0; python_version >= '3.6'" in requirements
    assert not any(r.startswith('-e') for r in requirements)
    setup = prepared_files(files, planned)['setup_files/setup.sh'].decode()
    original = 'pip install "${ot_pip_links[@]}" -e .[tests]'
    assert original in setup
    assert '-e .' not in planned['measurement_replacements'][original]
    # Unreviewed bases remain explicit exceptions.
    metadata = json.loads(files['setup_files/swelego.json']);metadata['base_commit'] = '0'*40
    files['setup_files/swelego.json'] = json.dumps(metadata).encode()
    with pytest.raises(ValueError, match='Unreviewed setup preinstall'):
        plan(files, bundle)


def test_geoip_wheel_preparation_uses_original_preinstall_headers():
    files, bundle = fixture()
    files['setup_files/swelego.json'] = json.dumps({
        'compiled_checkout': False, 'repo': 'meejah/txtorcon',
        'base_commit': '8139ebb8f48544121274aaa2892117f02862dc5f'}).encode()
    files['setup_files/requirements.txt'] = b'GeoIP==1.3.2\n'
    files['setup_files/setup.sh'] = files['setup_files/setup.sh'].replace(
        b'finish_phase preinstall\n',
        b'finish_phase preinstall\napt-get update && apt-get install -y libgeoip-dev\n')
    planned = plan(files, bundle)
    assert planned['request']['python'][-1]['apt_prerequisites'] == ['libgeoip-dev']
    setup = prepared_files(files, planned)['setup_files/setup.sh']
    assert setup.index(b'apt-get install -y libgeoip-dev') < setup.index(b'finish_phase dependencies')
    files['setup_files/requirements.txt'] = b'pytest==7.4.4\n'
    assert 'apt_prerequisites' not in plan(files, bundle)['request']['python'][-1]
