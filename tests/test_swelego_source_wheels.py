import json

import pytest

from hpc.offline_prep.verifier_offline import python_requests, record_source_wheels
from data.swelego.runtime_inputs import plan, prepared_files
from test_swelego_inputs import fixture


def test_pinned_source_replay_uses_built_wheel_and_preserves_online_requirement(tmp_path):
    requirement = 'custom-project @ git+https://github.com/example/custom-project.git@' + 'a'*40
    files, image = fixture()
    files['setup_files/requirements.txt'] += (requirement+'\n').encode()
    planned = plan(files, image)
    assert requirement in planned['request']['python'][0]['requirements']
    wheels = tmp_path/'wheels'
    wheels.mkdir()
    filename = 'custom_project-1.0-py3-none-any.whl'
    (wheels/filename).touch()
    mapping = record_source_wheels(planned['request'],tmp_path)
    assert mapping == {requirement: filename}
    assert requirement in python_requests(planned['request'])
    replay = python_requests(planned['request'],source_wheels=mapping)
    assert 'git+https://' not in replay
    assert '/ot-check/wheels/'+filename in replay
    assert json.loads((tmp_path/'sources/ot-pip-source-wheels.json').read_text()) == mapping
    adapted = prepared_files(files,planned)
    assert adapted['setup_files/requirements.txt'] == files['setup_files/requirements.txt']
    assert b'ot_requirements=/setup_files/requirements.txt' in adapted['setup_files/setup.sh']
    assert b'ot_local_requirements.py' in adapted['setup_files/setup.sh']


@pytest.mark.parametrize('wheel_names', [[], ['custom_project-1.0-py3-none-any.whl',
                                            'custom_project-2.0-py3-none-any.whl']])
def test_source_wheel_identity_must_be_unambiguous(tmp_path,wheel_names):
    wheels=tmp_path/'wheels';wheels.mkdir()
    for name in wheel_names:(wheels/name).touch()
    spec={'python':[{'tool':'pip','cache_source_wheels':True,
                     'requirements':['custom-project @ https://example.org/source.tar.gz']}]}
    with pytest.raises(ValueError,match='unique prepared wheel'):
        record_source_wheels(spec,tmp_path)


@pytest.mark.parametrize('requirement', ['custom @ https://example.org/main.tar.gz',
                                       '-e git+https://github.com/example/project@main#egg=custom'])
def test_mutable_sources_remain_incomplete(requirement):
    files,image=fixture()
    files['setup_files/requirements.txt']=(requirement+'\n').encode()
    with pytest.raises(ValueError,match='pinned|Editable'):
        plan(files,image)


def test_reviewed_mutable_data_source_is_pinned_only_for_cache_preparation(tmp_path, monkeypatch):
    from data.swelego.runtime_inputs import SKYPY_DATA_ORIGINAL, SKYPY_DATA_PREPARED
    files, image = fixture()
    files['setup_files/requirements.txt'] = (SKYPY_DATA_ORIGINAL + '\n').encode()
    files['setup_files/swelego.json'] = json.dumps({'compiled_checkout': False,
        'repo': 'skypyproject/skypy', 'base_commit': 'c3654084505233a0f5f471d9817c75b63bc7ff4d'}).encode()
    planned = plan(files, image)
    assert planned['request']['python'][0]['requirements'] == [SKYPY_DATA_PREPARED]
    wheels = tmp_path / 'wheels'; wheels.mkdir()
    wheel = wheels / 'skypy_data-0.1.0-py3-none-any.whl'; wheel.touch()
    record_source_wheels(planned['request'], tmp_path)
    adapted = prepared_files(files, planned)
    assert adapted['setup_files/requirements.txt'] == files['setup_files/requirements.txt']
    source = tmp_path / 'requirements.txt'; source.write_bytes(files['setup_files/requirements.txt'])
    target = tmp_path / 'cached-requirements.txt'
    helper = adapted['setup_files/ot_local_requirements.py'].decode().replace(
        '/setup_files/requirements.txt', str(source))
    monkeypatch.setenv('OT_TASK_OFFLINE', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['helper', str(target)])
    exec(compile(helper, 'ot_local_requirements.py', 'exec'), {})
    assert target.read_text().strip() == str(wheel)
