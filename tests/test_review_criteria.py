"""Selected review criteria control the prompt, verdict schema and saved scope."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tomllib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation import contract
from validation.rubrics import ROOT, harbor_rubric, implementation_rubric, select_criteria
from validation.stages import prepare_review_tasks, runner
from validation.upstream import checkout


@pytest.mark.parametrize('kind', ['terminal-bench', 'harbor'])
def test_subset_preserves_guidance_and_drops_unselected_skips(kind):
    text, skipped = (harbor_rubric() if kind == 'harbor' else
                     implementation_rubric(ROOT / 'rubric_terminal_bench/upstream/task-implementation.toml'))
    original = tomllib.loads(text)['criteria']
    names = [c['name'] for c in original[:2]]
    selected, automatic = select_criteria(text, skipped, names)
    assert tomllib.loads(selected)['criteria'] == original[:2]
    assert automatic == {}
    assert select_criteria(text, skipped) == (text, skipped)
    for bad in (['misspelled'], [names[0], names[0]], []):
        with pytest.raises(ValueError):
            select_criteria(text, skipped, bad)
    skip = next(iter(skipped))
    with pytest.raises(ValueError, match='all automatically skipped'):
        select_criteria(text, skipped, [skip])
    _, automatic = select_criteria(text, skipped, [names[0], skip])
    assert automatic == {skip: skipped[skip]}


def test_cli_validates_and_contract_records_selection():
    args = runner.parser().parse_args(['--review-criteria', 'anti_cheat_robustness', 'verifiable'])
    runner.check_args(args)
    profile = contract.profile(args)
    assert profile['review_criteria'] == ['anti_cheat_robustness', 'verifiable']
    assert profile['review_rubric'] == 'terminal-bench'
    assert runner.parser().parse_args([]).review_criteria is None
    args.review_criteria = ['misspelled']
    with pytest.raises(ValueError, match='Unknown review criteria'):
        runner.check_args(args)


def test_selected_prompt_and_verdict_only_contain_requested_criterion(tmp_path, monkeypatch):
    if not (runner.ROOT / 'external/terminal-bench/.git').exists():
        pytest.skip('pinned upstream checkout required')
    source = tmp_path / 'sample'
    source.mkdir()
    (source / 'task.toml').write_text('schema_version = "1.0"\n')
    (source / 'instruction.md').write_text('Solve the task.\n')
    options = SimpleNamespace(out=tmp_path / 'out', review_evidence='files', review_agent='test', review_model='test', review_local=False,
                              review_rubric='terminal-bench', review_criteria=['anti_cheat_robustness'],
                              backend='apptainer', dry_run=False, attempts=1)
    trial = tmp_path / 'job/trial'
    (trial / 'artifacts').mkdir(parents=True)
    monkeypatch.setattr(runner, 'resolve_review_defaults', lambda *a: None)
    monkeypatch.setattr(runner.runtime, 'check_runtime_task', lambda *a: None)
    monkeypatch.setattr(runner.runtime, 'job_config', lambda *a: {})

    async def execute(config):
        staged = tmp_path / 'item/input/rubric-review'
        rubric = staged / 'rubric.toml'
        text = rubric.read_text()
        assert [c['name'] for c in tomllib.loads(text)['criteria']] == ['anti_cheat_robustness']
        prompt = (staged / 'instruction.md').read_text()
        assert text in prompt
        assert 'name = "verifiable"' not in prompt
        checks = {'anti_cheat_robustness': {'outcome': 'fail', 'explanation': 'An accessible file contains the answer.'}}
        target = trial / 'artifacts/verdicts.json'
        target.write_text(json.dumps({'checks': checks}))
        prepare_review_tasks.read_verdicts(target, rubric)
        bad = trial / 'unexpected.json'
        bad.write_text(json.dumps({'checks': {**checks, 'verifiable': {'outcome': 'pass', 'explanation': 'Unrequested.'}}}))
        with pytest.raises(ValueError, match='missing or unexpected'):
            prepare_review_tasks.read_verdicts(bad, rubric)
        return trial.parent

    monkeypatch.setattr(runner.runtime, 'execute_job', execute)
    monkeypatch.setattr(runner.runtime, 'trial_results', lambda *a: [(trial, {})])
    monkeypatch.setattr(runner.runtime, 'assess_trials', lambda *a, **kw: {'status': 'completed', 'findings': []})
    result = runner.run_review(5, source, tmp_path / 'item', options, checkout('terminal-bench'),
                               {'task_count': 1, 'solution_count': 0, 'complete': True})
    assert result['status'] == 'findings'
    assert set(result['verdicts'][0]['checks']) == {'anti_cheat_robustness'}
    assert result['automatic_skips'] == {}
