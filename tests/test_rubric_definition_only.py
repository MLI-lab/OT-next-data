"""Judge rubrics contain definitions only, including selected/retried subsets."""
import asyncio
import tomllib

import pytest

from validation.rubrics import select_criteria
from validation.rubrics.utils import review


@pytest.mark.parametrize('kind,fmt', [
    ('custom', None), ('custom', 'classification'), ('river', None),
    ('river_selected', None), ('river_selected', 'classification'), ('custom_severity', None),
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


def test_selected_river_uses_eight_identical_definitions_in_both_strategies():
    labeling = tomllib.loads(review.load('river_selected')[0])
    classification_text = review.load('river_selected', 'classification')[0]
    classification = tomllib.loads(classification_text)
    assert {entry['name'] for entry in labeling['criteria']} == review.river_criteria()
    assert len(labeling["criteria"]) == 8
    assert classification['categories'][1:] == labeling['criteria']
    assert classification['categories'][0]['name'] == 'CLEAN'
    assert review.settings(labeling)['prompt'] == 'river_labeling'
    assert review.settings(labeling)['outcomes'] == ['pass', 'fail']
    assert review.settings(classification)['prompt'] == 'classification'
    for mode in ('image', 'files'):
        prompt = review.prompt(classification_text, 'task', mode)
        assert 'Classify the task into exactly one category' in prompt
        assert 'You have 300 minutes' in prompt
        assert 'You have 17 minutes' in review.prompt(classification_text, 'task', mode, minutes=17)
        assert '{{' not in prompt
    with pytest.raises(ValueError, match='cannot restrict'):
        select_criteria(classification_text, {}, ['ANSWER-LEAK'])
    with pytest.raises(ValueError, match='Expected exactly one category'):
        review.validate({'category': 'OTHER', 'explanation': 'Not selected.'}, classification)


@pytest.mark.parametrize('criterion', ['TASK-TRIVIAL', 'ANSWER-LEAK'])
def test_added_river_criteria_keep_binary_policy_in_retry_subsets(criterion):
    text = review.load('river_selected')[0]
    selected, _ = select_criteria(text, {}, [criterion])
    assert review.settings(tomllib.loads(selected))['prompt'] == 'river_labeling'
    with pytest.raises(ValueError, match='Expected pass/fail'):
        review.validate({'checks': {criterion: {'outcome': 'not_applicable', 'explanation': 'x'}}},
                        tomllib.loads(selected))


def test_selected_classification_retries_full_category_set(tmp_path):
    text = review.load('river_selected', 'classification')[0]
    calls = []

    async def invoke(rubric_text, prompt, folder):
        calls.append(rubric_text)
        return {'category': 'OTHER' if len(calls) == 1 else 'CLEAN', 'explanation': 'Checked all six defects.'}, {}

    state = asyncio.run(review.complete_review(text, review.prompt(text, 'task'), tmp_path, invoke))
    assert calls == [text, text]
    assert state['complete']
    assert state['verdict']['checks']['task_clean']['outcome'] == 'pass'


@pytest.mark.parametrize('fmt', ['criteria', 'classification'])
def test_copied_verifier_supports_selected_river_without_repository_imports(tmp_path, fmt):
    import subprocess
    import sys
    text = review.load('river_selected', fmt)[0]
    rubric = tomllib.loads(text)
    review.install_verifier(tmp_path)
    rubric_path = tmp_path / 'setup_files/review-rubric.toml'
    rubric_path.write_text(text)
    verdict = ({'category': 'CLEAN', 'explanation': 'Checked all defects.'} if fmt == 'classification' else
               {'checks': {entry['name']: {'outcome': 'pass', 'explanation': 'Checked.'}
                           for entry in rubric['criteria']}})
    import json
    verdict_path = tmp_path / 'verdict.json'
    verdict_path.write_text(json.dumps(verdict))
    command = [sys.executable, '-I', str(tmp_path / 'tests/run_review.py'),
               '--validate', str(verdict_path), str(rubric_path)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    if fmt == 'classification':
        verdict['category'] = 'OTHER'
    else:
        verdict['checks']['ANSWER-LEAK']['outcome'] = 'not_applicable'
    verdict_path.write_text(json.dumps(verdict))
    assert subprocess.run(command, capture_output=True).returncode != 0


def test_new_experiment_matrix_uses_both_selected_river_strategies():
    from validation.rubrics.utils.evaluation import CONFIGS
    assert {(rubric, fmt) for _, rubric, fmt, _ in CONFIGS} == {
        ('river_selected', 'labeling'), ('river_selected', 'classification')}


@pytest.mark.parametrize('names', [('NEW-DEFECT',), ('NEW-DEFECT', 'SECOND-DEFECT', 'THIRD-DEFECT')])
def test_river_definitions_drive_labeling_classification_and_standalone_verifier(tmp_path, monkeypatch, names):
    import json
    import subprocess
    import sys
    root = tmp_path / 'rubrics'
    selected = root / 'rubric_ot/rubric.toml'
    selected.parent.mkdir(parents=True)
    selected.write_text(''.join(f'[[criteria]]\nname = "{name}"\ndescription = "Definition of {name}."\n\n'
                                for name in names))
    selected.with_name('classification.toml').write_text(
        '[[categories]]\nname = "CLEAN"\ndescription = "No selected defects."\n\n'
        + selected.read_text().replace('[[criteria]]', '[[categories]]'))
    monkeypatch.setattr(review, 'ROOT', root)
    text = review.load('river_selected')[0]
    config = tomllib.loads(text)
    assert review.settings(config)['prompt'] == 'river_labeling'
    assert review.settings(config)['outcomes'] == ['pass', 'fail']
    classification = tomllib.loads(review.load('river_selected', 'classification')[0])
    assert classification['categories'][1:] == config['criteria']
    assert len(classification['categories']) == len(names) + 1
    subset, _ = select_criteria(text, {}, [names[-1]])
    assert review.settings(tomllib.loads(subset))['prompt'] == 'river_labeling'
    destination = tmp_path / 'staged'
    destination.mkdir()
    review.install_verifier(destination)
    # The copied verifier must work after the repository definitions disappear.
    selected.unlink()
    rubric_path = destination / 'setup_files/review-rubric.toml'
    rubric_path.write_text(subset)
    verdict_path = destination / 'verdict.json'
    verdict = {'checks': {names[-1]: {'outcome': 'pass', 'explanation': 'Checked.'}}}
    verdict_path.write_text(json.dumps(verdict))
    command = [sys.executable, '-I', str(destination / 'tests/run_review.py'),
               '--validate', str(verdict_path), str(rubric_path)]
    subprocess.run(command, check=True, capture_output=True)
    verdict['checks'][names[-1]]['outcome'] = 'not_applicable'
    verdict_path.write_text(json.dumps(verdict))
    assert subprocess.run(command, capture_output=True).returncode != 0


@pytest.mark.parametrize('kind', ['custom', 'ot', 'river_selected'])
def test_classification_warns_and_adds_clean_only_if_missing_without_mutating_source(tmp_path, monkeypatch, kind):
    root = tmp_path / 'rubrics'
    monkeypatch.setattr(review, 'ROOT', root)
    folder = review.rubric_dir(kind)
    folder.mkdir(parents=True)
    (folder / 'rubric.toml').write_text('[[criteria]]\nname = "DEFECT"\ndescription = "A defect."\n')
    path = folder / 'classification.toml'
    text = '[[categories]]\nname = "DEFECT"\ndescription = "A defect."\n'
    path.write_text(text)
    monkeypatch.setattr(review, 'ROOT', root)
    with pytest.warns(UserWarning, match='has no CLEAN category; adding the default CLEAN definition'):
        loaded, _ = review.load(kind, 'classification')
    assert path.read_text() == text
    assert loaded == text.rstrip() + '\n\n' + review.DEFAULT_CLEAN_CATEGORY
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        assert review.classification_defaults(loaded) == loaded
    assert not caught
    # CLEAN can appear anywhere. Loading preserves exactly the supplied text.
    text += '\n[[categories]]\nname = "CLEAN"\ndescription = "Our explicit clean definition."\n'
    path.write_text(text)
    with warnings.catch_warnings(record=True) as caught:
        loaded, _ = review.load(kind, 'classification')
    assert not caught
    assert loaded == text
    assert [c['name'] for c in tomllib.loads(loaded)['categories']] == ['DEFECT', 'CLEAN']



def test_labeling_does_not_get_clean_fallback():
    import warnings
    text = review.load('river_selected')[0]
    with warnings.catch_warnings(record=True) as caught:
        assert review.classification_defaults(text) == text
    assert not caught


def test_default_clean_definition_matches_requested_wording():
    category = tomllib.loads(review.DEFAULT_CLEAN_CATEGORY)['categories'][0]
    assert category == {'name': 'CLEAN', 'description':
        'Instruction is clear and self-sufficient, a correct-per-instruction solution passes, '
        'AND the verifier genuinely validates correctness: it recomputes/decodes the true answer '
        'from the input (or exact-compares against a correct hardcoded answer) rather than merely '
        'checking output shape. A hard-but-fair task is still CLEAN.'}


@pytest.mark.parametrize('kind', ['custom', 'river_selected'])
def test_labeling_cli_format_matches_legacy_criteria(kind):
    from validation.stages.runner import parser
    args = parser().parse_args(['--review-rubric', kind, '--review-format', 'labeling'])
    assert review.load(args.review_rubric, args.review_format) == review.load(kind, 'criteria')
    assert review.settings(tomllib.loads(review.load(kind, 'labeling')[0]))['format'] == 'criteria'
