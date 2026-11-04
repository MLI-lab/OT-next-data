"""Judge rubrics contain definitions only, including selected/retried subsets."""
import asyncio
import tomllib

import pytest

from validation.rubrics import select_criteria
from validation.rubrics.utils import review


@pytest.mark.parametrize('kind,fmt', [
    ('custom', None), ('custom', 'classification'), ('river', None),
    ('river_selected', None), ('custom_severity', None),
])
def test_loaded_rubrics_contain_only_definitions(kind, fmt):
    text, _ = review.load(kind, fmt)
    config = tomllib.loads(text)
    assert 'review' not in config
    assert not ({'format', 'prompt', 'outcomes'} & config.keys())
    assert '{{' not in review.prompt(text, 'task', minutes=12)
    review.settings(config)


def test_runtime_metadata_is_rejected():
    text, _ = review.load('river_selected')
    with pytest.raises(ValueError, match='non-definition metadata'):
        review.prompt('[review]\nformat = "criteria"\n' + text, 'task')


def test_river_subset_keeps_binary_policy_and_retries_na(tmp_path):
    text, _ = review.load('river_selected')
    text, _ = select_criteria(text, {}, ['INSTR-ENV-MISMATCH', 'INSTR-AMBIGUOUS'])
    assert 'described defect is absent' in review.prompt(text, 'task')
    calls = []

    async def invoke(rubric_text, prompt, folder):
        config = tomllib.loads(rubric_text)
        assert set(config) == {'criteria'}
        names = {c['name'] for c in config['criteria']}
        calls.append(names)
        return {'checks': {name: {
            'outcome': 'not_applicable' if name == 'INSTR-AMBIGUOUS' and len(calls) == 1 else 'pass',
            'explanation': 'Checked the task files.',
        } for name in names}}, {}

    state = asyncio.run(review.complete_review(text, review.prompt(text, 'task'), tmp_path, invoke))
    assert calls == [{'INSTR-ENV-MISMATCH', 'INSTR-AMBIGUOUS'}, {'INSTR-AMBIGUOUS'}]
    assert state['complete']
    config = tomllib.loads(text)
    with pytest.raises(ValueError, match='Expected pass/fail'):
        review.validate_complete({'checks': {name: {'outcome': 'not_applicable', 'explanation': 'x'}
                                             for name in calls[0]}}, config)


@pytest.mark.parametrize('kind', ['custom', 'river', 'river_selected', 'custom_severity'])
def test_introduction_matches_execution_mode(kind):
    text, _ = review.load(kind)
    image = review.prompt(text, 'task', 'image')
    files = review.prompt(text, 'task', 'files')
    assert 'original workspace and setup' in image
    assert 'base review environment' not in image
    assert 'base review environment' in files
    assert 'original workspace and setup' not in files
    assert '{{' not in image and '{{' not in files
    if kind == 'river_selected':
        assert 'interact with the environment as needed' in image
        assert 'interact with the environment as needed' not in files
    with pytest.raises(ValueError, match='Unknown review evidence mode'):
        review.prompt(text, 'task', 'unknown')
