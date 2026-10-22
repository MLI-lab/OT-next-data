"""Terminus-2 reviews run in the reviewed task's environment; missing criteria get one repair review."""
import asyncio
import json
from types import SimpleNamespace
import tomllib

import pytest

from validation.stages import env_review, prepare_review_tasks, runner
from validation.stages.task_setup import upload_review_files
from validation.upstream import checkout
from harbor_patches.image_context import environment_dir_hash

RUBRIC = '[[criteria]]\nname = "a"\ndescription = "A"\nguidance = "A"\n\n[[criteria]]\nname = "b"\ndescription = "B"\nguidance = "B"\n'


def make_task(root, toml='schema_version = "1.0"\n\n[agent]\nuser = "solver"\ntimeout_sec = 600.0\n'):
    (root / 'environment').mkdir(parents=True)
    (root / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\nWORKDIR /app\n')
    (root / 'setup_files').mkdir()
    (root / 'setup_files/setup.sh').write_text('echo setup\n')
    (root / 'tests').mkdir()
    (root / 'tests/test.sh').write_text('exit 0\n')
    (root / 'task.toml').write_text(toml)
    (root / 'instruction.md').write_text('Solve it.\n')
    return root


def test_stage_keeps_environment_and_adds_evidence(tmp_path):
    source = make_task(tmp_path / 'src/sample')
    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'instruction.md').write_text('Review /app/task-under-review.\n')
    task = env_review.stage(source, staged, tmp_path / 'out/sample', {'task-under-review/sample': source},
                            '/app/verdicts.json')
    assert environment_dir_hash(task / 'environment') == environment_dir_hash(source / 'environment')
    assert (task / 'review_files/task-under-review/sample/instruction.md').read_text() == 'Solve it.\n'
    assert (task / 'setup_files/setup.sh').is_file()
    config = tomllib.loads((task / 'task.toml').read_text())
    assert config['agent'] == {'user': 'solver', 'timeout_sec': env_review.TIMEOUT_SEC}
    assert config['artifacts'] == [{'source': '/app/verdicts.json', 'destination': 'verdicts.json'}]
    assert (task / 'instruction.md').read_text().startswith('Review /app/task-under-review.')
    assert 'own agent environment' in (task / 'instruction.md').read_text()


def test_stage_refuses_tasks_it_cannot_review_faithfully(tmp_path):
    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'instruction.md').write_text('Review.\n')
    steps = make_task(tmp_path / 'steps', 'schema_version = "1.0"\n\n[[steps]]\nname = "one"\n')
    with pytest.raises(ValueError, match='single-step'):
        env_review.stage(steps, staged, tmp_path / 'o1', {}, '/app/verdicts.json')
    artifacts = make_task(tmp_path / 'artifacts', 'artifacts = ["/app/out"]\n')
    with pytest.raises(ValueError, match='already declares artifacts'):
        env_review.stage(artifacts, staged, tmp_path / 'o2', {}, '/app/verdicts.json')


@pytest.mark.parametrize('text', ['schema_version = "1.0"\n', '[agent]\nuser = "x"\n[verifier]\ntimeout_sec = 5.0\n'])
def test_agent_timeout_is_added_without_touching_other_tables(text):
    parsed = tomllib.loads(env_review.agent_timeout(text, 3600.0))
    assert parsed['agent']['timeout_sec'] == 3600.0
    assert parsed.get('verifier', {}).get('timeout_sec', 5.0) == 5.0


def test_review_files_are_uploaded_after_setup(tmp_path):
    (tmp_path / 'review_files/task-under-review').mkdir(parents=True)
    calls = []

    class Environment:
        async def exec(self, command, **kwargs):
            calls.append(('exec', command))

        async def upload_dir(self, source_dir, target_dir):
            calls.append(('upload', target_dir))

    asyncio.run(upload_review_files(Environment(), tmp_path))
    assert calls[0] == ('exec', 'rm -f /app/verdicts.json /app/analysis.json')
    assert ('upload', '/app/task-under-review') in calls
    calls.clear()
    asyncio.run(upload_review_files(Environment(), tmp_path / 'ordinary-task'))
    assert calls == []


def test_collect_verdict_separates_complete_partial_and_invalid(tmp_path):
    rubric = tmp_path / 'rubric.toml'
    rubric.write_text(RUBRIC)
    good = {'outcome': 'pass', 'explanation': 'Fine.'}
    cases = {'complete': {'checks': {'a': good, 'b': good}},
             'partial': {'checks': {'a': good, 'renamed_b': good}},
             'invalid': {'checks': {'a': {'outcome': 'maybe', 'explanation': 'x'}}}}
    result = {'status': 'completed', 'findings': [], 'verdicts': []}
    for name, data in cases.items():
        (tmp_path / name).write_text(json.dumps(data))
        prepare_review_tasks.collect_verdict(result, tmp_path / name, tmp_path / name, rubric, {'c': good})
    assert set(result['verdicts'][0]['checks']) == {'a', 'b', 'c'}
    assert result['partial'] == [{'trial': str(tmp_path / 'partial'), 'checks': {'a': good},
                                  'missing': ['b'], 'unexpected': ['renamed_b']}]
    assert result['status'] == 'failed' and 'invalid' in result['findings'][0]


def test_missing_criteria_get_one_restricted_repair(tmp_path, monkeypatch):
    good = {'outcome': 'fail', 'explanation': 'Leaks the answer.'}
    asked = []

    async def review_once(number, source, item_dir, args, *rest):
        asked.append(getattr(args, 'review_criteria', None))
        usage = {'trials': [{'trial': str(item_dir)}], 'expected_trials': 1, 'summary': {}}
        if args.review_criteria is None:
            return {'status': 'completed', 'findings': [], 'verdicts': [], 'usage': usage, 'automatic_skips': {},
                    'partial': [{'trial': 'run/t0', 'checks': {'a': good}, 'missing': ['b', 'c'], 'unexpected': ['x']}]}
        return {'status': 'completed', 'findings': [], 'usage': usage,
                'verdicts': [{'trial': 'repair', 'checks': {'b': good}}]}

    monkeypatch.setattr(runner, 'review_once', review_once)
    args = SimpleNamespace(review_criteria=None, attempts=1)
    result = asyncio.run(runner.run_review_async(5, tmp_path / 'task', tmp_path / 'item', args, None))
    assert asked == [None, ['b', 'c']]
    assert args.review_criteria is None and args.attempts == 1
    assert result['status'] == 'failed' and 'c' in result['findings'][0]
    assert result['criteria_repairs'][0]['still_missing'] == ['c']
    assert result['criteria_repairs'][0]['ignored_criteria'] == ['x']
    assert len(result['usage']['trials']) == 2 and result['usage']['expected_trials'] == 2

    async def complete(number, source, item_dir, args, *rest):
        if args.review_criteria is None:
            return {'status': 'completed', 'findings': [], 'verdicts': [], 'automatic_skips': {'s': good},
                    'partial': [{'trial': 't', 'checks': {'a': good}, 'missing': ['b'], 'unexpected': []}]}
        return {'status': 'completed', 'findings': [], 'verdicts': [{'trial': 'r', 'checks': {'b': good}}]}

    monkeypatch.setattr(runner, 'review_once', complete)
    result = asyncio.run(runner.run_review_async(5, tmp_path / 'task', tmp_path / 'item2', args, None))
    assert result['status'] == 'findings'
    assert result['verdicts'][0]['criterion_origin'] == {'a': 'original', 'b': 'retry'}
    assert set(result['verdicts'][0]['checks']) == {'a', 'b', 's'}


def test_terminus_review_runs_in_task_environment(tmp_path, monkeypatch):
    if not (runner.ROOT / 'external/terminal-bench/.git').exists():
        pytest.skip('pinned upstream checkout required')
    source = make_task(tmp_path / 'sample')
    options = SimpleNamespace(review_evidence='image', review_agent='terminus-2', review_model='openai/glm-5.3-flash', review_local=False,
                              model='openai/glm-5.3-flash', review_rubric='terminal-bench',
                              review_criteria=['anti_cheat_robustness'], backend='apptainer', dry_run=False,
                              attempts=1, review_pricing=None, serve_model='glm-5.3-flash')
    trial = tmp_path / 'job/trial'
    (trial / 'artifacts').mkdir(parents=True)
    seen = {}

    async def run(task, out, args, model):
        seen.update(task=task, model=model)
        checks = {'anti_cheat_robustness': {'outcome': 'pass', 'explanation': 'Tests are hidden.'}}
        (trial / 'artifacts/verdicts.json').write_text(json.dumps({'checks': checks}))
        return trial.parent, [(trial, {})]

    monkeypatch.setattr(runner, 'resolve_review_defaults', lambda *a: None)
    monkeypatch.setattr(env_review, 'run', run)
    result = runner.run_review(5, source, tmp_path / 'item', options, checkout('terminal-bench'),
                               {'task_count': 1, 'solution_count': 0, 'complete': True})
    assert result['status'] == 'passed' and result['evidence_mode'] == 'task-environment'
    assert seen['model'] == 'openai/glm-5.3-flash'
    assert (seen['task'] / 'review_files/task-under-review/sample/task.toml').is_file()
    assert environment_dir_hash(seen['task'] / 'environment') == environment_dir_hash(source / 'environment')


def test_local_inspector_review_uses_stage_five(tmp_path, monkeypatch):
    from validation.stages import image_review
    options = SimpleNamespace(review_agent='claude-code', review_model='remote',
                              review_local=True, model='hosted_vllm/judge',
                              review_evidence='image')
    monkeypatch.setattr(runner, 'resolve_review_defaults', lambda *a: None)
    monkeypatch.setattr(prepare_review_tasks, 'stage_review', lambda *a: tmp_path / 'staged')
    async def review(source, staged, out, args, agent, model):
        assert staged == tmp_path / 'staged'
        assert args.review_local and model == 'hosted_vllm/judge'
        return {'status': 'passed', 'evidence_mode': 'image'}
    monkeypatch.setattr(image_review, 'review', review)
    result = asyncio.run(runner.review_once(5, tmp_path / 'source', tmp_path / 'out', options, tmp_path))
    assert result['evidence_mode'] == 'image'
