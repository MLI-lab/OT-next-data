import copy

import pytest

from data.swelego.runtime_measurements import measurement, reuse_observation
from test_swelego_inputs import fixture
from validation.contract import files_digest


def measured_source():
    files, bundle = fixture()
    value = measurement(files, bundle)
    content = files_digest(files.items())
    group = dict(representative='original', tasks={'original': {'content_sha256': content}},
                 inputs=value['inputs'], task_runtime_id=value['task_runtime_id'],
                 phases=value['dependency_phases'])
    observation = dict(kind='swelego-dependencies-v1', returncode=0,
                       source_bundle_unchanged=True, source_content_sha256=content,
                       stdout='{"python":"3.9.21"}')
    return files, bundle, group, observation


def test_instruction_changes_reuse_only_dependency_observation_with_original_evidence():
    files, bundle, group, observation = measured_source()
    original = copy.deepcopy(observation)
    files['instruction.md'] = b'Different baseline instruction'
    result = reuse_observation(files, bundle, observation, group)
    assert observation == original
    assert result['source_content_sha256'] == files_digest(files.items())
    assert result['reuse_evidence']['original_source_content_sha256'] == original['source_content_sha256']
    assert result['stdout'] == observation['stdout']
    assert 'no task execution or grading equivalence' in result['reuse_evidence']['scope']


@pytest.mark.parametrize('change', ['requirements', 'image', 'phase'])
def test_changed_preparation_cannot_reuse_observation(change):
    files, bundle, group, observation = measured_source()
    if change == 'requirements':
        files['setup_files/requirements.txt'] = b'pytest==8.0.0\n'
    elif change == 'image':
        bundle['files']['base.overlay.img']['sha256'] = 'd' * 64
    else:
        group['phases'][0] += 'echo changed\n'
    with pytest.raises(ValueError, match='operations differ'):
        reuse_observation(files, bundle, observation, group)


@pytest.mark.parametrize('field,value', [('returncode', 1), ('source_bundle_unchanged', False),
                                        ('source_content_sha256', '0' * 64)])
def test_failed_or_unbound_observations_are_not_reused(field, value):
    files, bundle, group, observation = measured_source()
    observation[field] = value
    with pytest.raises(ValueError, match='original successful'):
        reuse_observation(files, bundle, observation, group)


def compiled_source():
    files = {'setup_files/swelego.json': b'{"compiled_checkout":true}',
             'setup_files/setup.sh': ('#!/bin/bash\nset -euo pipefail\n'
                                     'if [ -f /opt/swelego-ready/' + 'a'*64 + ' ]; then\n'
                                     '  echo restore-original-checkout\nfi\n').encode()}
    observation = {'kind': 'swelego-dependencies-v1', 'returncode': 0,
                   'source_bundle_unchanged': True,
                   'source_content_sha256': files_digest(files.items()),
                   'dependency_source': 'already baked in verified compiled task image',
                   'task_preparation': 'no additional dependencies or assets needed; baked checkout archive present'}
    return files, {'files': {'base.sif': {'sha256': 'a'*64}}}, observation


def test_compiled_observation_rebinds_only_dependency_evidence():
    from data.swelego.runtime_measurements import reuse_compiled_observation
    source, bundle, observation = compiled_source()
    target = {**source, 'instruction.md': b'Baseline task instructions'}
    original = copy.deepcopy(observation)
    result = reuse_compiled_observation(target, bundle, observation, source, bundle)
    assert result['source_content_sha256'] == files_digest(target.items())
    assert result['reuse_evidence']['original_source_content_sha256'] == original['source_content_sha256']
    assert observation == original


@pytest.mark.parametrize('change', ['setup', 'image', 'failed', 'unbound'])
def test_compiled_reuse_rejects_changed_or_unproven_dependencies(change):
    from data.swelego.runtime_measurements import reuse_compiled_observation
    source, bundle, observation = compiled_source()
    target, target_bundle = dict(source), copy.deepcopy(bundle)
    if change == 'setup':
        target['setup_files/setup.sh'] += b'pip install new-dependency\n'
    elif change == 'image':
        target_bundle['files']['base.sif']['sha256'] = 'b'*64
    elif change == 'failed':
        observation['returncode'] = 1
    else:
        observation['source_content_sha256'] = 'b'*64
    with pytest.raises(ValueError):
        reuse_compiled_observation(target, target_bundle, observation, source, bundle)
