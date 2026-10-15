import shlex

import pytest

from harbor_patches.container_env import environment_arguments


@pytest.mark.parametrize('value', ['a=1, b=2', 'one,two', '"quoted", a=b',
                                  "it's literal $(false), a=b\nnext"])
def test_environment_values_preserved_and_task_arguments_untouched(value):
    cmd = ['apptainer', 'exec', '--env', 'PATH=/bin', '--env', 'QUESTION=' + shlex.quote(value),
           'instance://test', 'echo', '--env', 'TASK=untouched']
    actual, options = environment_arguments(cmd, {'env': {'HOST': 'kept'}})
    assert actual == ['apptainer', 'exec', '--env', 'PATH=/bin', 'instance://test',
                      '/usr/bin/env', '--', 'QUESTION=' + value, 'echo', '--env', 'TASK=untouched']
    assert options == {'env': {'HOST': 'kept'}}
    assert cmd[4] == '--env'


@pytest.mark.parametrize('cmd', [['other', 'exec', '--env', 'A=B'],
                                ['apptainer', 'exec', '--env', 'A=B', 'instance://test', 'true']])
def test_unrelated_command_unchanged(cmd):
    kwargs = {'env': {}}
    assert environment_arguments(cmd, kwargs) == (cmd, kwargs)
