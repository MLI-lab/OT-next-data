import subprocess

import pytest

from data.calibforge.build_evidence import bind_step


def inputs():
    command = ['srun', '-w', 'node1', 'python', '-m', 'hpc.offline_prep.image_cache']
    return (dict(command=command, status='error', seconds=12,
                 error=str(subprocess.CalledProcessError(1, command))),
            dict(ExitCode='1:0', State='FAILED', ElapsedRaw='11',
                 Start='2026-11-07T01:00:00', JobID='123_0.0'))


def test_legacy_exit_is_joined_to_measured_step():
    record, step = inputs()
    bound = bind_step(record, step, 'Europe/Berlin')
    assert bound['returncode'] == 1
    assert bound['started_at'] == '2026-11-07T01:00:00+01:00'
    assert bound['node'] == 'node1'


@pytest.mark.parametrize('field,value', [('ExitCode', '0:0'), ('ExitCode', '1:9'),
                                       ('State', 'TIMEOUT'), ('ElapsedRaw', '20')])
def test_conflicting_step_is_rejected(field, value):
    record, step = inputs()
    step[field] = value
    with pytest.raises(ValueError):
        bind_step(record, step, 'Europe/Berlin')


def test_unmeasured_exception_is_not_a_build_exit():
    record, step = inputs()
    record['error'] = 'Missing image'
    with pytest.raises(ValueError):
        bind_step(record, step, 'Europe/Berlin')
