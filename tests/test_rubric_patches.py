"""Review only applicable criteria, preserving deterministic skip evidence."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tomllib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation import contract
from validation.rubrics import ROOT, harbor_rubric, implementation_rubric, instruction_suffix_variant
from validation.stages import prepare_review_tasks, runner
from validation.upstream import checkout


@pytest.mark.parametrize('references,skip', [
    (None, False),
    ({'task_count': 3, 'solution_count': 0, 'complete': True}, True),
    ({'task_count': 3, 'solution_count': 1, 'complete': True}, False),
    ({'task_count': 3, 'solution_count': 0, 'complete': False}, False),
    ({'task_count': 0, 'solution_count': 0, 'complete': True}, False),
])
def test_effective_rubric_changes_only_authorized_criteria(references, skip):
    source = ROOT / 'upstream/task-implementation.toml'
    original = tomllib.loads(source.read_text())['criteria']
    text, skipped = implementation_rubric(source, references)
    effective = {c['name']: c for c in tomllib.loads(text)['criteria']}
    assert ('solvable' not in effective) == skip
    assert 'difficult' not in effective
    assert 'novel' not in effective
    assert 'separate_verifier_configured' not in effective
    assert 'environment_hygiene' not in effective
    assert 'difficulty_explanation_quality' not in effective
    assert 'solution_explanation_quality' not in effective
    assert 'verification_explanation_quality' not in effective
    assert 'category_and_tags' not in effective
    assert 'task_name' not in effective
    assert 'task_readme' not in effective
    assert 'expert_time_estimate' not in effective
    assert 'task_toml_schema' not in effective
    assert 'binary_reward' not in effective
    assert set(skipped) == {'difficult', 'novel', 'separate_verifier_configured', 'environment_hygiene',
                            'difficulty_explanation_quality', 'solution_explanation_quality',
                            'verification_explanation_quality', 'category_and_tags', 'task_name', 'task_readme',
                            'expert_time_estimate', 'task_toml_schema', 'binary_reward'} | ({'solvable'} if skip else set())
    assert 'binary rewards are not necessarily required for every RL training task' in skipped['binary_reward']['explanation']
    assert 'Moved to the Stage 1 static check check-task-toml-schema.py' in skipped['task_toml_schema']['explanation']
    assert skipped['expert_time_estimate']['explanation'] == (
        'Automatically skipped: Removed because RL training tasks do not require an expert time estimate.')
    assert 'not required to include a benchmark reviewer README' in skipped['task_readme']['explanation']
    assert 'may preserve source task IDs and their own naming conventions' in skipped['task_name']['explanation']
    assert "not required to follow Terminal-Bench's category, subcategory, and tag taxonomy" in skipped['category_and_tags']['explanation']
    assert 'not required to include a Solution explanation section' in skipped['solution_explanation_quality']['explanation']
    assert 'not required to include a Verification explanation section' in skipped['verification_explanation_quality']['explanation']
    assert 'not required to include a Difficulty explanation section' in skipped['difficulty_explanation_quality']['explanation']
    assert 'benchmark-specific image and dependency management' in skipped['environment_hygiene']['explanation']
    assert 'image reuse and storage costs with fast task-specific setup' in skipped['separate_verifier_configured']['explanation']
    assert 'too restrictive for selecting useful RL training tasks' in skipped['difficult']['explanation']
    assert skipped['novel']['explanation'] == (
        'Automatically skipped: Removed because novelty is mainly an evaluation concern, '
        'ensuring tasks cannot be solved through memorization of training data, '
        'which is not a requirement for RL training tasks.')
    for criterion in original:
        name = criterion['name']
        if name == 'verifiable':
            guidance = criterion['guidance'].replace(
                'etc. inside `test.sh` introduce flakiness',
                'etc. inside `tests/setup.sh` or `tests/test.sh` introduce flakiness')
            assert effective[name] == {**criterion, 'guidance': guidance}
        elif name == 'instruction_concision':
            guidance = criterion['guidance']
            for paragraph in guidance.split('\n\n'):
                if paragraph.startswith(('The canary string (harbor-canary GUID)', 'Every instruction ends',
                                         'Use backticks when referring to file paths')):
                    guidance = guidance.replace(paragraph + '\n\n', '')
            assert effective[name] == {**criterion, 'guidance': guidance}
        elif name not in skipped:
            assert effective[name] == criterion


def test_upstream_drift_fails_before_review(tmp_path):
    source = tmp_path / 'rubric.toml'
    source.write_text((ROOT / 'upstream/task-implementation.toml').read_text() + '\n')
    with pytest.raises(ValueError, match='pinned patch source'):
        implementation_rubric(source)


def test_harbor_default_rubric_only_removes_static_dependency_check():
    source = tomllib.loads((ROOT / 'harbor/upstream/default-rubric.toml').read_text())['criteria']
    effective_text, skipped = harbor_rubric()
    effective = tomllib.loads(effective_text)['criteria']
    removed = {'pinned_dependencies', 'test_deps_in_image'}
    assert len(source) == len(effective) + len(removed)
    assert [item['name'] for item in effective] == [
        item['name'] for item in source if item['name'] not in removed]
    assert skipped['pinned_dependencies']['outcome'] == 'not_applicable'
    assert 'check-pip-pinning.sh' in skipped['pinned_dependencies']['explanation']
    assert skipped['test_deps_in_image']['outcome'] == 'not_applicable'
    assert 'shared task images' in skipped['test_deps_in_image']['explanation']


def test_review_rubric_option_selects_harbor():
    options = runner.parser().parse_args(['--review-rubric', 'harbor'])
    assert options.review_rubric == 'harbor'
    assert runner.parser().parse_args([]).review_rubric == 'terminal-bench'


@pytest.mark.parametrize('tb,local,total,complete,keep', [
    (2, 0, 2, True, True), (0, 2, 2, True, True), (1, 1, 2, True, True),
    (1, 0, 2, True, False), (0, 0, 2, True, False),
    (2, 0, 2, False, False), (0, 0, 0, True, False),
])
def test_concision_timeout_exemption_requires_all_instructions(tb, local, total, complete, keep):
    counts = {'Terminal-Bench tasks': tb, 'this task': local, 'task_count': total, 'complete': complete}
    text, skipped = implementation_rubric(ROOT / 'upstream/task-implementation.toml', suffixes=counts)
    guidance = next(c['guidance'] for c in tomllib.loads(text)['criteria'] if c['name'] == 'instruction_concision')
    assert 'The canary string' not in guidance
    assert 'Use backticks when referring to file paths' not in guidance
    assert ('Every instruction ends with a standard timeout' in guidance) == keep
    assert 'instruction_concision' not in skipped


@pytest.mark.parametrize('ending', ['Terminal-Bench tasks', 'this task'])
def test_suffix_detection_requires_full_trailing_sentence(ending):
    sentence = f'You have 120 seconds to complete this task. Do not cheat by using online solutions or hints specific to {ending}.'
    assert instruction_suffix_variant(('Do the work.\n\n' + sentence + '\n').encode()) == ending
    assert instruction_suffix_variant(sentence + '\nMore instructions.') is None
    assert instruction_suffix_variant(sentence.replace('120', 'X')) is None
    assert instruction_suffix_variant(sentence.split(' Do not cheat')[0]) is None


def test_suffix_counts_include_tasks_outside_limit(tmp_path):
    sentence = 'You have 120 seconds to complete this task. Do not cheat by using online solutions or hints specific to Terminal-Bench tasks.'
    a = make_task(tmp_path, 'a')
    b = make_task(tmp_path, 'b')
    (a / 'instruction.md').write_text(sentence)
    counts = {}
    contract.inventory(tmp_path, limit=1, suffixes=counts)
    assert counts == {'task_count': 2, 'Terminal-Bench tasks': 1, 'this task': 0, 'complete': True}
    (b / 'instruction.md').write_text(sentence.replace('Terminal-Bench tasks', 'this task'))
    contract.inventory(tmp_path, limit=1, suffixes=counts)
    assert counts == {'task_count': 2, 'Terminal-Bench tasks': 1, 'this task': 1, 'complete': True}


def make_task(root, name, solution=False):
    task = root / name
    task.mkdir(parents=True)
    (task / 'task.toml').write_text('schema_version = "1.0"\n')
    (task / 'instruction.md').write_text('Solve the task.\n')
    if solution:
        (task / 'solution').mkdir()
        (task / 'solution/solve.sh').write_text('true\n')
    return task


def test_inventory_counts_whole_datasource_before_selection(tmp_path):
    make_task(tmp_path, 'a')
    make_task(tmp_path, 'b', solution=True)
    refs = {}
    selected, _ = contract.inventory(tmp_path, limit=1, references=refs)
    assert [t['task_id'] for t in selected] == ['a']
    assert refs == {'task_count': 2, 'solution_count': 1, 'complete': True}
    (tmp_path / 'b/solution/solve.sh').unlink()
    contract.inventory(tmp_path, limit=1, references=refs)
    assert refs == {'task_count': 2, 'solution_count': 0, 'complete': True}
    (tmp_path / 'source.json').write_text(json.dumps({'selection': {'limit': 2}}))
    contract.inventory(tmp_path, references=refs)
    assert refs['complete'] is False


def test_single_task_is_not_evidence_of_whole_datasource(tmp_path):
    task = make_task(tmp_path, 'a')
    refs = {}
    contract.inventory(task, references=refs)
    assert refs['complete'] is False


def test_normalized_selection_is_not_full_datasource(tmp_path):
    make_task(tmp_path, 'a')
    (tmp_path / 'source.json').write_text(json.dumps({
        'normalization': {'original_selection': {'limit': 1, 'task_id_range': None}}}))
    refs = {}
    contract.inventory(tmp_path, references=refs)
    assert refs['complete'] is False


def test_parquet_reference_counts_include_unselected_rows(tmp_path):
    import io
    import tarfile
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows = []
    for name, solution in [('a', False), ('b', True)]:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            names = ['task.toml', 'instruction.md'] + (['./solution/solve.sh'] if solution else [])
            for filename in names:
                content = (b'You have 60 seconds to complete this task. Do not cheat by using online solutions or hints specific to Terminal-Bench tasks.'
                           if filename == 'instruction.md' and name == 'a' else b'\n')
                member = tarfile.TarInfo(filename)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        rows.append({'path': name, 'task_binary': buffer.getvalue()})
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / 'tasks.parquet')
    refs = {}
    suffixes = {}
    selected, _ = contract.inventory(tmp_path, limit=1, references=refs, suffixes=suffixes)
    assert [r['task_id'] for r in selected] == ['a']
    assert refs == {'task_count': 2, 'solution_count': 1, 'complete': True}
    assert suffixes == {'task_count': 2, 'Terminal-Bench tasks': 1, 'this task': 0, 'complete': True}


def test_rubric_assets_are_bound_by_contract():
    files = contract.implementation()
    for name in ['UPSTREAM.json', 'patches/verifiable-setup.patch', 'patches/skip-solvable.patch',
                 'patches/skip-difficult.patch', 'patches/skip-novel.patch',
                 'patches/concision-canary.patch', 'patches/concision-timeout.patch',
                 'patches/concision-formatting.patch', 'upstream/task-implementation.toml']:
        assert 'validation/rubrics/' + name in files
    assert 'validation/rubrics/patches/skip-separate_verifier_configured.patch' in files
    assert 'validation/rubrics/patches/skip-environment_hygiene.patch' in files
    assert 'validation/rubrics/patches/skip-difficulty_explanation_quality.patch' in files
    assert 'validation/rubrics/patches/skip-solution_explanation_quality.patch' in files
    assert 'validation/rubrics/patches/skip-verification_explanation_quality.patch' in files
    assert 'validation/rubrics/patches/skip-category_and_tags.patch' in files
    assert 'validation/rubrics/patches/skip-task_name.patch' in files
    assert 'validation/rubrics/patches/skip-task_readme.patch' in files
    assert 'validation/rubrics/patches/skip-expert_time_estimate.patch' in files
    assert 'validation/rubrics/patches/skip-task_toml_schema.patch' in files
    assert 'validation/rubrics/patches/skip-binary_reward.patch' in files
    assert 'validation/rubrics/harbor/upstream/default-rubric.toml' in files
    assert 'validation/rubrics/harbor/patches/remove-training-inapplicable-criteria.patch' in files
    assert 'validation/rubrics/harbor/UPSTREAM.json' in files


@pytest.mark.parametrize('solution_count', [0, 1])
def test_review_prompt_and_result_schema_use_effective_rubric(tmp_path, monkeypatch, solution_count):
    if not (runner.ROOT / 'external/terminal-bench/.git').exists():
        pytest.skip('pinned upstream checkout required')
    upstream = checkout('terminal-bench')
    source = make_task(tmp_path / 'tasks', 'sample')
    refs = {'task_count': 2, 'solution_count': solution_count, 'complete': True}
    options = SimpleNamespace(review_agent='test', review_model='test', review_local=False,
                              backend='apptainer', dry_run=False, attempts=1)
    trial = tmp_path / 'job/trial'
    (trial / 'artifacts').mkdir(parents=True)
    monkeypatch.setattr(runner, 'resolve_review_defaults', lambda *a: None)
    monkeypatch.setattr(runner.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(runner.runtime, 'job_config', lambda *a: {})

    async def execute(config):
        staged = tmp_path / 'item/input/rubric-review'
        text = (staged / 'rubric.toml').read_text()
        assert text in (staged / 'instruction.md').read_text()
        criteria = {c['name'] for c in tomllib.loads(text)['criteria']}
        assert ('solvable' in criteria) == bool(solution_count)
        assert 'difficult' not in criteria
        assert 'novel' not in criteria
        assert 'separate_verifier_configured' not in criteria
        assert 'environment_hygiene' not in criteria
        assert 'difficulty_explanation_quality' not in criteria
        assert 'solution_explanation_quality' not in criteria
        assert 'verification_explanation_quality' not in criteria
        assert 'category_and_tags' not in criteria
        assert 'task_name' not in criteria
        assert 'task_readme' not in criteria
        assert 'expert_time_estimate' not in criteria
        assert 'task_toml_schema' not in criteria
        assert 'binary_reward' not in criteria
        assert 'verifiable' in criteria and 'outcome_verified' in criteria
        assert 'anti_cheat_robustness' in criteria
        verdicts = {'checks': {name: {'outcome': 'pass', 'explanation': 'Reviewed.'} for name in criteria}}
        artifact = trial / 'artifacts/verdicts.json'
        artifact.write_text(json.dumps(verdicts))
        # Skipped criteria must not be requested from or accepted from the judge.
        if not solution_count:
            extra = {'checks': {**verdicts['checks'], 'solvable': {'outcome': 'fail', 'explanation': 'Missing.'}}}
            bad = tmp_path / 'bad.json'
            bad.write_text(json.dumps(extra))
            with pytest.raises(ValueError, match='unexpected rubric criteria'):
                prepare_review_tasks.read_verdicts(bad, staged / 'rubric.toml')
        return trial.parent

    monkeypatch.setattr(runner.runtime, 'execute_job', execute)
    monkeypatch.setattr(runner.runtime, 'trial_results', lambda *a: [(trial, {})])
    monkeypatch.setattr(runner.runtime, 'assess_trials', lambda *a, **kw: {'status': 'completed', 'findings': []})
    result = runner.run_review(2, source, tmp_path / 'item', options, upstream, refs)
    assert result['status'] == 'passed'
    assert result['verdicts'][0]['checks']['solvable']['outcome'] == ('pass' if solution_count else 'not_applicable')
    assert ('solvable' in result['automatic_skips']) == (solution_count == 0)
    assert result['verdicts'][0]['checks']['difficult'] == result['automatic_skips']['difficult']
    assert result['automatic_skips']['difficult']['outcome'] == 'not_applicable'
    assert result['verdicts'][0]['checks']['novel'] == result['automatic_skips']['novel']
    assert result['automatic_skips']['novel']['outcome'] == 'not_applicable'
    assert result['verdicts'][0]['checks']['separate_verifier_configured'] == result['automatic_skips']['separate_verifier_configured']
    assert result['automatic_skips']['separate_verifier_configured']['outcome'] == 'not_applicable'
    assert result['verdicts'][0]['checks']['environment_hygiene'] == result['automatic_skips']['environment_hygiene']
    assert result['automatic_skips']['environment_hygiene']['outcome'] == 'not_applicable'
    assert result['verdicts'][0]['checks']['difficulty_explanation_quality'] == result['automatic_skips']['difficulty_explanation_quality']
    assert result['automatic_skips']['difficulty_explanation_quality']['outcome'] == 'not_applicable'
    for name in ('solution_explanation_quality', 'verification_explanation_quality', 'category_and_tags',
                 'task_name', 'task_readme', 'expert_time_estimate', 'task_toml_schema', 'binary_reward'):
        assert result['verdicts'][0]['checks'][name] == result['automatic_skips'][name]
        assert result['automatic_skips'][name]['outcome'] == 'not_applicable'
