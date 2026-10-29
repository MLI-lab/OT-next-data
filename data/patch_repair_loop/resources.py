"""Audited execution resource changes without changing dataset or validation policy."""

import re


FIELDS = {'time', 'cpus', 'memory', 'concurrency', 'shard_size', 'shard_concurrency', 'prebuild_shard_size',
          'prebuild_all_images', 'max_wait_hours', 'validation_args'}
IMAGE_FLAGS = {'--image-build-concurrency', '--image-build-cpus',
               '--image-build-memory-mb', '--image-build-timeout-sec', '--pack-image-cache'}


def validate(changes):
    from data.patch_repair_loop.controller import validation_args
    if not isinstance(changes, dict) or set(changes) - FIELDS:
        raise ValueError('Resource changes may only adjust execution resource settings')
    for key, value in changes.items():
        if key in ('cpus', 'concurrency', 'shard_size', 'shard_concurrency', 'prebuild_shard_size', 'max_wait_hours'):
            if type(value) is not int or value < 1:
                raise ValueError(f'{key} must be a positive integer')
        elif key == 'time':
            if not isinstance(value, str) or not re.fullmatch(r'(?:\d+-)?\d+:\d{2}:\d{2}', value):
                raise ValueError('time must be a finite [days-]hours:minutes:seconds limit')
            hours, minutes, seconds = value.split('-')[-1].split(':')
            if int(minutes) > 59 or int(seconds) > 59 or not any(int(n) for n in re.findall(r'\d+', value)):
                raise ValueError('time must be a positive valid duration')
        elif key == 'memory':
            if not isinstance(value, str) or not re.fullmatch(r'[1-9]\d*[KMGTP]?', value, re.I):
                raise ValueError('memory must be a positive Slurm memory amount')
        elif key == 'prebuild_all_images' and type(value) is not bool:
            raise ValueError('prebuild_all_images must be boolean')
        elif key == 'validation_args':
            validation_args({'validation_args': value})
            if any(arg.startswith('--') and arg not in IMAGE_FLAGS for arg in value):
                raise ValueError('Supervisor may change image resources, not preparation success criteria')
    return changes


def effective(config, state):
    changes = validate(state.get('resource_overrides', {}))
    result = {**config, **changes}
    if 'validation_args' in changes:
        # Resource approval cannot silently remove an existing preparation
        # success threshold by replacing the argument list.
        original = config.get('validation_args', [])
        preserved = []
        index = 0
        while index < len(original):
            flag = original[index]
            length = 1 if flag == '--pack-image-cache' else 2
            if flag not in IMAGE_FLAGS:
                preserved.extend(original[index:index + length])
            index += length
        result['validation_args'] = preserved + changes['validation_args']
    return result


def approve(state, changes, *, decision, evidence):
    """Called by the runner only after supervisor validation and job reconciliation."""
    validate(changes)
    previous = dict(state.get('resource_overrides', {}))
    state['resource_overrides'] = {**previous, **changes}
    state.setdefault('resource_history', []).append({
        'previous_overrides': previous, 'changes': changes,
        'decision': str(decision), 'evidence': evidence})


def reconfigure(config_path, changes):
    """Apply an operator-requested resource update to a stopped, reconciled loop."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from data.patch_repair_loop import controller as core, recovery
    validate(changes)
    config_path = Path(config_path)
    config = core.load_config(config_path)
    work = Path(config['work_root'])
    with core.lock(work / '.supervisor.lock'), recovery.controller_locks(config):
        state_path = work / 'loop-state.json'
        state = json.loads(state_path.read_text())
        raw = json.loads(config_path.read_text())
        if state['config_sha256'] != core.config_digest(raw):
            raise ValueError('Existing config does not match the saved state; reconcile it first')
        if any(not job['finished'] for job in recovery.jobs(work)):
            raise ValueError('Wait for active or uncertain jobs before changing resources')
        if (work / 'publication/job.json').exists():
            raise ValueError('Reconcile publication before changing resources')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        record = work / 'resource-changes' / stamp
        core.save(record / 'config-before.json', raw)
        core.save(record / 'state-before.json', state)
        updated = {**raw, **changes}
        core.save(record / 'config-after.json', updated)
        state['config_sha256'] = core.config_digest(updated)
        state['resource_overrides'] = {k: v for k, v in state.get('resource_overrides', {}).items() if k not in changes}
        state.setdefault('resource_history', []).append({
            'changes': changes, 'decision': 'operator request', 'evidence': [str(record)]})
        core.restart_stage3(state)
        state.update(status='running', shared_infrastructure_revision=recovery.infrastructure_revision())
        state.pop('blocker', None)
        core.save(config_path, updated)
        core.save(state_path, state)
        return {'generation': state['generation'], 'resources': changes, 'audit': str(record)}
