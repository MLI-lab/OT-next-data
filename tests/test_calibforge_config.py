import tomllib

import pytest

from data.calibforge import patch
from validation.rules.static.task_toml_schema import check


@pytest.mark.parametrize('task', sorted(patch.REDUNDANT_AGENT_RESOURCE_TASKS))
def test_remove_only_ignored_duplicate_resources(task, tmp_path):
    text = ('version = "1.0"\n[agent]\ntimeout_sec = 900.0\n'
            'cpus = 2\nmemory = "2G"\nstorage = "10G"\n'
            '[environment]\ncpus = 2\nmemory = "2G"\nstorage = "10G"\n')
    (tmp_path / 'task.toml').write_text(text)
    assert len(check(tmp_path)) == 3
    new, removed = patch.remove_redundant_agent_resources(task, text)
    (tmp_path / 'task.toml').write_text(new)
    assert check(tmp_path) == []
    assert set(removed) == {'cpus', 'memory', 'storage'}
    assert tomllib.loads(new)['environment'] == tomllib.loads(text)['environment']
    assert tomllib.loads(new)['agent'] == {'timeout_sec': 900.0}
    assert patch.remove_redundant_agent_resources(task, new) == (new, [])
    assert patch.remove_redundant_agent_resources('other', text) == (text, [])
    with pytest.raises(ValueError, match='differ'):
        patch.remove_redundant_agent_resources(task, text.replace('[environment]\ncpus = 2', '[environment]\ncpus = 1'))


def test_verifier_layer_has_no_test_script_comment():
    script = '#!/bin/bash\nset -e\npip install pytest==8.4.1\npytest /tests/test_outputs.py\n'
    plan = patch.plan_verifier(script)
    assert plan['layer'] is not None
    assert patch.VERIFIER_PREPARATION_COMMENT not in plan['layer']
    assert 'pip install pytest==8.4.1' in plan['layer']
    assert '/tests/test_outputs.py' not in plan['layer']
