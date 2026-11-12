import pytest

from validation.leak_repair.concurrency import validation_args
from validation.leak_repair.launch import Service, Validation


@pytest.mark.parametrize('arguments, expected', [([], 32), (['--concurrency', '1'], 32),
    (['--concurrency=4'], 32), (['--concurrency', '48'], 48),
    (['--concurrency=64', '--concurrency', '16'], 32)])
def test_runner_concurrency_is_explicit(arguments, expected, tmp_path):
    svc = Service(tmp_path / 'group', tmp_path / 'service', ['--reference-leaks', *arguments])
    run = Validation(tmp_path / 'tasks', tmp_path / 'run', svc.run_args)
    assert run.run_args == ['--reference-leaks', '--concurrency', str(expected)]


@pytest.mark.parametrize('arguments', [['--concurrency'], ['--concurrency=0'], ['--concurrency', '-1']])
def test_invalid_concurrency_fails(arguments):
    with pytest.raises(ValueError):
        validation_args(arguments)


@pytest.mark.parametrize('count, limit, expected', [(4, 32, 4), (20, 32, 20), (66, 32, 32), (66, 64, 64)])
def test_actual_task_count_caps_validation(count, limit, expected, tmp_path):
    tasks = tmp_path / 'tasks'
    tasks.mkdir()
    for i in range(count):
        (tasks / str(i)).mkdir()
    run = Validation(tasks, tmp_path / 'run', ['--concurrency', str(limit)])
    assert run.run_args == ['--concurrency', str(expected)]
