import shlex

import pytest

from harbor_patches.container_env import environment_arguments


@pytest.mark.parametrize('value', ['a=1, b=2', 'one,two', '"quoted", a=b',
                                  "it's literal $(false), a=b\nnext",
                                  'kkanda__communities and crime unnormalized data set'])
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


@pytest.mark.parametrize('raw', ['', '/opt/bin:$PATH', "it's", 'x y'])
def test_values_harbor_did_not_quote_pass_unchanged(raw):
    cmd = ['apptainer', 'exec', '--env', 'A=' + raw, 'instance://test', 'true']
    assert environment_arguments(cmd, {})[0] == cmd


@pytest.mark.parametrize('value', ['', 'one two', 'one,two', "it's literal $(false), a=b\nnext"])
def test_harbor_prompt_defaults_coexist_with_literal_task_payload(value):
    # Match worker.exec_command: raw prompt defaults, then quoted task values.
    defaults = ['--env', 'PS1=\\$ ', '--env', 'PROMPT_COMMAND=']
    cmd = ['apptainer', 'exec', *defaults, '--env', 'QUESTION=' + shlex.quote(value),
           'instance://test', '/bin/bash', '-lc', 'true']
    actual, _ = environment_arguments(cmd, {})
    assert actual == ['apptainer', 'exec', *defaults, 'instance://test',
                      '/usr/bin/env', '--', 'QUESTION=' + value, '/bin/bash', '-lc', 'true']


def test_ambiguous_comma_encoding_is_not_silently_reinterpreted():
    cmd = ['apptainer', 'exec', '--env', 'QUESTION=one, two', 'instance://test', 'true']
    with pytest.raises(ValueError, match='Unexpected Harbor environment value encoding'):
        environment_arguments(cmd, {})
