"""Reuse dependency observations only across identical preparation operations.

This establishes dependency equivalence, never task or grading equivalence.
Source checkout, task initialization, tests and audit results are not reused.
"""
import hashlib
import json

from data.swelego import runtime_inputs
from harbor_patches.verifier_bundle import spec_id
from validation.contract import files_digest


def measurement(files, bundle, *, source_catalog=None):
    planned = runtime_inputs.plan(files, bundle, source_catalog=source_catalog)
    measured = dict(planned)
    asset_commands = {'irfs': 'python download_irfs.py', 'nats': 'bash ./scripts/install_nats.sh'}
    assets = planned.get('setup_assets', [])
    measured['setup_assets'] = [asset for asset in assets if asset['kind'] not in asset_commands]
    measured['deferred_initialization'] = [
        *planned.get('deferred_initialization', []),
        *[asset_commands[asset['kind']] for asset in assets if asset['kind'] in asset_commands],
    ]
    setup = runtime_inputs.prepared_files(files, measured)['setup_files/setup.sh'].decode()
    phases = []
    for first, last in [('conda', 'preinstall'), ('preinstall', 'dependencies'), ('dependencies', 'install')]:
        phase = setup.split('finish_phase ' + first + '\n', 1)[1].split('finish_phase ' + last + '\n', 1)[0]
        for command in measured['deferred_initialization']:
            if phase.count(command + '\n') > 1:
                raise ValueError('Repeated deferred initialization')
            phase = phase.replace(command + '\n', '')
        for original, replacement in planned.get('measurement_replacements', {}).items():
            phase = phase.replace(original + '\n', replacement + '\n')
        phases.append(phase)
    input_id = spec_id({**planned['request'], **planned['binding']})
    identity = {'task_runtime_id': input_id, 'dependency_phases': phases}
    return {'id': spec_id(identity), 'inputs': planned, **identity}


def reuse_observation(files, bundle, observation, source_group, *, source_catalog=None):
    """Bind an existing successful dependency-only measurement to a new payload."""
    representative = source_group['representative']
    original_hash = source_group['tasks'][representative]['content_sha256']
    if (observation.get('kind') != 'swelego-dependencies-v1'
            or observation.get('returncode') != 0
            or observation.get('source_bundle_unchanged') is not True
            or observation.get('source_content_sha256') != original_hash
            or 'reuse_evidence' in observation):
        raise ValueError('Missing original successful dependency observation')
    target = measurement(files, bundle, source_catalog=source_catalog)
    source_inputs = source_group['inputs']
    source_id = spec_id({**source_inputs['request'], **source_inputs['binding']})
    if source_id != source_group['task_runtime_id']:
        raise ValueError('Source preparation identity differs')
    if (target['task_runtime_id'] != source_id
            or target['dependency_phases'] != source_group['phases']):
        raise ValueError('Dependency preparation operations differ')
    # Keep the original observation immutable and preserve an independently
    # checkable digest, representative and hash in the derived evidence.
    evidence = {
        'scope': 'Identical dependency phases and pristine image binding; no task execution or grading equivalence',
        'representative': representative,
        'original_source_content_sha256': original_hash,
        'original_observation_sha256': hashlib.sha256(
            json.dumps(observation, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'dependency_group': target['id'],
    }
    return {**observation, 'source_content_sha256': files_digest(files.items()), 'reuse_evidence': evidence}


def reuse_compiled_observation(files, bundle, observation, source_files, source_bundle):
    """Reuse baked-dependency evidence only with identical image and setup bytes."""
    from data.swelego.patch import prepared_compiled_setup

    if observation.get('kind') != 'swelego-dependencies-v1' or 'reuse_evidence' in observation:
        raise ValueError('Missing original compiled dependency observation')
    prepared_compiled_setup(source_files, observation=observation)
    if bundle['files'] != source_bundle['files']:
        raise ValueError('Compiled dependency image differs')
    def dependency_files(payload):
        return {name: data for name, data in payload.items()
                if name.startswith(('setup_files/', 'environment/'))}
    if dependency_files(files) != dependency_files(source_files):
        raise ValueError('Compiled dependency setup or environment differs')
    derived = {**observation, 'source_content_sha256': files_digest(files.items())}
    prepared_compiled_setup(files, observation=derived)
    derived['reuse_evidence'] = {
        'scope': 'Identical compiled image, setup and environment bytes; no task execution or grading equivalence',
        'original_source_content_sha256': observation['source_content_sha256'],
        'original_observation_sha256': hashlib.sha256(
            json.dumps(observation, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'source_image_files': source_bundle['files'],
    }
    return derived
