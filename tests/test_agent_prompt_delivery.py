import json

import pytest

from data.patch_repair_loop import agents


@pytest.mark.parametrize('role', sorted(agents.ROLES))
def test_large_evidence_is_referenced_without_losing_policy_or_scope(tmp_path, role):
    context = {
        'outcome': {'failures': ['large evidence, €' * 1000] * 800},
        'user_instructions': 'Never exclude infrastructure failures.',
        'dataset': 'example', 'dataset_dir': '/repo/data/example',
        'evidence_dir': str(tmp_path), 'integration_plan': '/repo/data/example/plan',
    }
    full = agents.prompt_for(role, context)
    actual = agents.delivery_prompt(role, context, tmp_path, full)
    assert len(actual.encode()) < 96_000
    assert str(tmp_path / 'context.json') in actual
    assert 'Never exclude infrastructure failures.' in actual
    assert '/repo/data/example' in actual
    assert json.loads((tmp_path / 'context.json').read_text()) == context
    assert '"json_key": "outcome"' in actual
    # Large-context compaction must retain the shared policy for every role.
    assert 'backend_build_ownership' in actual
    assert 'without first finding a fix' in actual
    assert 'exclusion permissions' in actual


def test_small_prompt_unchanged(tmp_path):
    assert agents.delivery_prompt('fixer', {}, tmp_path, 'small') == 'small'
    assert not (tmp_path / 'context.json').exists()
