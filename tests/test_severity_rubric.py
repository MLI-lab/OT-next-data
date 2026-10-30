"""Severity judgments use the shared stage-5 execution, repair and reporting paths."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import tomllib

import pytest

from validation.rubrics import select_criteria
from validation.rubrics import run_review as review


def rubric():
    return tomllib.loads(review.load('custom_severity')[0])


def entry(level='none', subcategory=None):
    return {'severity': level, 'subcategory': subcategory,
            'finding': 'An essential requirement is not checked.' if level in ('minor', 'major') else None,
            'evidence': ['tests/test_outputs.py:12 only checks the output format.'] if level in ('minor', 'major') else [],
            'not_assessed_reason': 'Verifier environment was unavailable.' if level == 'not_assessed' else None}


def verdict():
    return {'task_interpretation': 'Compute the required output from the supplied data.',
            'verifier_mode': 'shared',
            'results': {r['id']: entry() for r in rubric()['rubrics']}}


def test_loading_selection_prompt_and_cli():
    from validation.stages.runner import parser, check_args
    from validation.contract import profile, implementation
    text = review.load('custom_severity')[0]
    config = tomllib.loads(text)
    assert config['review']['format'] == 'severity'
    assert len(config['rubrics']) == 5
    selected, skipped = select_criteria(text, {}, ['instruction_verifier_alignment'])
    definition = tomllib.loads(selected)['rubrics'][0]
    assert definition == config['rubrics'][1]
    assert len(definition['subcategories']) == 3
    assert skipped == {}
    with pytest.raises(ValueError, match='Unknown review criteria'):
        select_criteria(text, {}, ['misspelled'])
    prompt = review.prompt(text, 'example')
    assert 'Write valid JSON to `/app/verdicts.json`' in prompt
    assert 'Do not modify anything under `/app/task-under-review/`.' in prompt
    assert 'Read the rubric definitions at `/app/rubric.toml`.' in prompt
    assert 'actual task and verifier environments are not running here' in review.prompt(text, 'example', 'files')
    args = parser().parse_args(['--review-rubric', 'custom_severity', '--review-criteria', 'instruction_quality'])
    check_args(args)
    assert args.review_format == 'severity'
    assert profile(args)['review_rubric'] == 'custom_severity'
    assert profile(args)['review_format'] == 'severity'
    hashes = implementation(stages=[5])
    assert 'validation/rubrics/rubric_custom_severity/rubric.toml' in hashes
    assert 'validation/rubrics/prompt_templates/modified/custom_severity.txt' in hashes
    for kind, fmt in [('custom_severity', 'criteria'), ('custom_severity', 'classification'),
                      ('custom', 'severity'), ('river', 'severity')]:
        with pytest.raises(ValueError):
            review.load(kind, fmt)


def test_schema_derives_checks_without_losing_severity():
    raw = verdict()
    raw['results']['instruction_quality'] = entry('minor')
    raw['results']['instruction_verifier_alignment'] = entry('major', 'contradictory_requirement')
    raw['results']['anti_cheating_robustness'] = entry('not_assessed')
    raw['checks'] = {'anti_cheating_robustness': {'outcome': 'pass'}}
    result = review.validate_complete(raw, rubric())
    assert result['results'] == raw['results']
    assert result['checks']['instruction_quality']['outcome'] == 'fail'
    assert result['checks']['instruction_verifier_alignment']['outcome'] == 'fail'
    assert result['checks']['anti_cheating_robustness']['outcome'] == 'incomplete'
    assert result['checks']['other']['outcome'] == 'pass'


@pytest.mark.parametrize('name,field,value', [
    ('anti_cheating_robustness', 'severity', 'minor'),
    ('instruction_quality', 'severity', 'critical'),
    ('instruction_quality', 'subcategory', 'unstated_requirement'),
    ('instruction_quality', 'finding', 'Unexpected finding for none'),
    ('instruction_quality', 'not_assessed_reason', 'Unexpected reason for none'),
    ('instruction_quality', 'evidence', 'not a list'),
    ('instruction_verifier_alignment', 'subcategory', 'unstated_requirement'),
])
def test_invalid_severity_fields_are_rejected(name, field, value):
    raw = verdict()
    raw['results'][name][field] = value
    with pytest.raises(ValueError):
        review.validate_complete(raw, rubric())


def test_schema_requires_evidence_subcategories_and_assessment_reason():
    for name, value in [
        ('instruction_quality', {**entry('minor'), 'evidence': []}),
        ('instruction_quality', {**entry('major'), 'finding': ''}),
        ('instruction_quality', {**entry('not_assessed'), 'not_assessed_reason': ''}),
        ('instruction_verifier_alignment', entry('major')),
        ('instruction_verifier_alignment', entry('major', 'unknown')),
        ('instruction_verifier_alignment', entry('not_assessed', 'unstated_requirement')),
    ]:
        raw = verdict(); raw['results'][name] = value
        with pytest.raises(ValueError):
            review.validate_complete(raw, rubric())
    for mutate in [lambda x: x['results'].pop('other'),
                   lambda x: x['results'].update(unknown=entry()),
                   lambda x: x.update(task_interpretation=''),
                   lambda x: x.update(verifier_mode='invalid')]:
        raw = verdict(); mutate(raw)
        with pytest.raises(ValueError):
            review.validate_complete(raw, rubric())


def test_repair_retains_results_context_and_resume(tmp_path):
    text = review.load('custom_severity')[0]
    calls = []
    async def invoke(selected, prompt, folder):
        ids = [r['id'] for r in tomllib.loads(selected)['rubrics']]
        calls.append(ids)
        if len(calls) == 1:
            return {**verdict(), 'results': {'instruction_quality': entry('minor'),
                'anti_cheating_robustness': entry('minor')}}, {}
        assert 'For this repair round, include only these rubric IDs' in prompt
        return {**verdict(), 'results': {**{name: entry() for name in ids},
                'instruction_quality': entry()}}, {}
    state = asyncio.run(review.complete_review(text, review.prompt(text, 'sample'), tmp_path, invoke))
    assert [len(ids) for ids in calls] == [5, 4]
    assert state['verdict']['results']['instruction_quality']['severity'] == 'minor'
    assert state['criterion_origin']['instruction_quality'] == 0
    assert state['criterion_origin']['anti_cheating_robustness'] == 1
    assert state['verdict']['task_interpretation'] == verdict()['task_interpretation']
    resumed = asyncio.run(review.complete_review(text, 'Review', tmp_path, invoke))
    assert resumed['resumed']
    assert len(calls) == 2


def test_context_repair_after_all_results_are_retained(tmp_path):
    calls = []
    async def invoke(selected, prompt, folder):
        calls.append(folder)
        if len(calls) == 1:
            return {'results': verdict()['results']}, {}
        assert 'return an empty results object' in prompt
        return {**verdict(), 'results': {}}, {}
    state = asyncio.run(review.complete_review(review.load('custom_severity')[0], 'Review', tmp_path, invoke))
    assert state['complete']
    assert len(calls) == 2


def test_not_assessed_is_a_valid_completed_judgment(tmp_path):
    raw = verdict(); raw['results']['anti_cheating_robustness'] = entry('not_assessed')
    calls = []
    async def invoke(*args):
        calls.append(args)
        return raw, {}
    state = asyncio.run(review.complete_review(review.load('custom_severity')[0], 'Review', tmp_path, invoke))
    assert state['complete']
    assert len(calls) == 1
    assert state['verdict']['checks']['anti_cheating_robustness']['outcome'] == 'incomplete'


def test_partial_resume_after_transport_failure(tmp_path):
    text = review.load('custom_severity')[0]
    async def first(selected, prompt, folder):
        if folder.name == 'round-000':
            return {**verdict(), 'results': {'instruction_quality': entry('minor')}}, {}
        raise RuntimeError('Disconnected')
    with pytest.raises(RuntimeError, match='Disconnected'):
        asyncio.run(review.complete_review(text, 'Review', tmp_path, first))
    async def second(selected, prompt, folder):
        assert folder.name == 'round-002'
        assert 'instruction_quality' not in [r['id'] for r in tomllib.loads(selected)['rubrics']]
        return verdict(), {}
    state = asyncio.run(review.complete_review(text, 'Review', tmp_path, second))
    assert state['verdict']['results']['instruction_quality']['severity'] == 'minor'


def test_compact_exports_and_grouping_preserve_severity():
    from validation.publishing.report import review_task, review_finding_reason
    from validation.metrics.review_results import group_reviews
    raw = verdict(); raw['results']['anti_cheating_robustness'] = entry('not_assessed')
    parsed = review.validate_complete(raw, rubric())
    item = {'task': '/tasks/sample', 'status': 'findings', 'verdicts': [parsed]}
    exported = review_task(item)['implementation']['attempts'][0]
    for field in ['results', 'task_interpretation', 'verifier_mode']:
        assert exported[field] == parsed[field]
    assert 'incomplete criteria: anti_cheating_robustness' in review_finding_reason(item)
    grouped = group_reviews([item])
    assert grouped['groups']['has_incomplete'] == ['sample']
    assert grouped['groups']['review_error'] == []
    assert grouped['counts']['all_pass'] == 0
    assert grouped['severity_counts']['anti_cheating_robustness'] == {'not_assessed': 1}
    raw['results']['instruction_verifier_alignment'] = entry('minor', 'unstated_requirement')
    item['verdicts'] = [review.validate_complete(raw, rubric())]
    grouped = group_reviews([item])
    assert grouped['groups']['has_fail'] == ['sample']
    assert grouped['subcategory_counts']['instruction_verifier_alignment'] == {'unstated_requirement': 1}


def test_analysis_compares_severity_and_subcategory(tmp_path):
    from validation.rubrics.analysis import analyze, render
    raw = verdict(); raw['results']['instruction_quality'] = entry('minor')
    first = review.validate_complete(copy.deepcopy(raw), rubric())
    raw['results']['instruction_quality'] = entry('major')
    second = review.validate_complete(copy.deepcopy(raw), rubric())
    raw['results']['instruction_verifier_alignment'] = entry('major', 'unstated_requirement')
    third = review.validate_complete(copy.deepcopy(raw), rubric())
    raw['results']['instruction_verifier_alignment'] = entry('major', 'contradictory_requirement')
    fourth = review.validate_complete(copy.deepcopy(raw), rubric())
    report = {'stage': 5, 'review_rubric': 'custom_severity', 'review_format': 'severity',
              'review_attempts': 4, 'items': [{'task': '/tasks/sample', 'status': 'findings',
              'verdicts': [{'sample_id': f'/cache/sample/{i:03d}', **v}
                           for i, v in enumerate([first, second, third, fourth])]}]}
    path = tmp_path / 'report.json'; path.write_text(json.dumps(report))
    result = analyze([path, path])
    group = result['groups'][0]
    assert result['duplicate_samples_ignored'] == 4
    assert group['completed_reviews'] == 4
    assert group['overall']['pairwise_agreement']['count'] == 0
    assert group['severity_counts']['instruction_quality'] == {'minor': 1, 'major': 3}
    assert group['per_criterion']['instruction_quality']['per_task']['sample']['labels'] == {'minor': 1, 'major': 3}
    assert 'Severity counts:' in render(result)
    json.dumps(result)


def test_standalone_file_verifier_uses_same_schema(tmp_path):
    review.install_verifier(tmp_path)
    definition = tmp_path / 'setup_files/review-rubric.toml'
    definition.write_text(review.load('custom_severity')[0])
    path = tmp_path / 'verdicts.json'; path.write_text(json.dumps(verdict()))
    command = [sys.executable, str(tmp_path / 'tests/run_review.py'), '--validate', str(path), str(definition)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    bad = verdict(); bad['results']['anti_cheating_robustness'] = entry('minor')
    path.write_text(json.dumps(bad))
    assert subprocess.run(command, capture_output=True).returncode != 0


@pytest.mark.parametrize('task_environment', [False, True])
def test_harbor_execution_repairs_and_resumes(tmp_path, monkeypatch, task_environment):
    from validation.stages import harbor as runtime
    source = tmp_path / 'source'
    (source / 'environment').mkdir(parents=True)
    (source / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    (source / 'task.toml').write_text('schema_version = "1.0"\n')
    (source / 'instruction.md').write_text('Compute the output.')
    staged = tmp_path / 'staged'
    (staged / 'environment').mkdir(parents=True)
    (staged / 'setup_files').mkdir()
    (staged / 'environment/Dockerfile').write_text('FROM ubuntu:24.04\n')
    text = review.load('custom_severity')[0]
    (staged / 'rubric.toml').write_text(text)
    (staged / 'rubric-skips.json').write_text('{}')
    (staged / 'instruction.md').write_text(review.prompt(text, source.name, 'image' if task_environment else 'files'))
    selected = []
    async def execute(config):
        task = Path(config['task'])
        definition = task / ('review_files/rubric.toml' if task_environment else 'rubric.toml')
        ids = [r['id'] for r in tomllib.loads(definition.read_text())['rubrics']]
        selected.append(ids)
        if task_environment:
            assert config['verifier']['disable']
            assert (task / 'environment/Dockerfile').read_text() == (source / 'environment/Dockerfile').read_text()
        raw = verdict()
        raw['results'] = {name: entry() for name in (ids[:1] if len(selected) == 1 else ids)}
        trial = task.parent / 'trial'
        (trial / 'artifacts').mkdir(parents=True)
        (trial / 'artifacts/verdicts.json').write_text(json.dumps(raw))
        return trial.parent
    monkeypatch.setattr(runtime, 'check_runtime_task', lambda *args: None)
    monkeypatch.setattr(runtime, 'job_config', lambda task, *args: {'task': str(task)})
    monkeypatch.setattr(runtime, 'execute_job', execute)
    monkeypatch.setattr(runtime, 'trial_results', lambda job: [(job / 'trial', {})])
    monkeypatch.setattr('validation.rubrics.review_pricing.for_stage', lambda *args: {'rates': None, 'source': {}})
    args = SimpleNamespace(out=tmp_path / 'out', attempts=1, review_runs=1, review_resume=True,
                           backend='docker', dry_run=False)
    async def run():
        return await review.review_files(staged, source, tmp_path, args, 'terminus-2', 'test',
                                         task_environment=task_environment)
    result = asyncio.run(run())
    assert result['status'] == 'passed', result
    assert [len(ids) for ids in selected] == [5, 4]
    assert result['verdicts'][0]['results'] == verdict()['results']
    assert result['evidence_mode'] == ('task-environment' if task_environment else 'files')
    again = asyncio.run(run())
    assert again['verdicts'][0]['resumed']
    assert len(selected) == 2


def test_image_inspection_keeps_severity_and_repairs(tmp_path, monkeypatch):
    from validation.stages import image_review
    from validation.stages.image_inspector import exchange
    text = review.load('custom_severity')[0]
    source = tmp_path / 'source'; source.mkdir()
    (source / 'instruction.md').write_text('Compute output.')
    staged = tmp_path / 'staged'; staged.mkdir()
    (staged / 'rubric.toml').write_text(text)
    (staged / 'rubric-skips.json').write_text('{}')
    (staged / 'instruction.md').write_text(review.prompt(text, source.name))
    class Environment:
        raw = {}
        stopped = False
        async def upload_file(self, source_path, target_path):
            self.ids = [r['id'] for r in tomllib.loads(source_path.read_text())['rubrics']]
        async def exec(self, command, **kwargs):
            return SimpleNamespace(return_code=0, stdout='Task evidence', stderr='')
        async def download_file(self, source_path, target_path):
            target_path.write_text(json.dumps(self.raw))
        async def stop(self, **kwargs):
            self.stopped = True
    env = Environment()
    async def create(*args, **kwargs):
        return {'agent': env}
    calls = []
    async def judge(command, prompt, cwd, environ, timeout):
        server = json.loads(command[command.index('--mcp-config') + 1])['mcpServers']['image']
        result = await asyncio.to_thread(exchange, int(server['args'][-1]),
            server['env']['OT_REVIEW_INSPECT_TOKEN'], {'command': 'cat /app/rubric.toml'})
        assert result['exit_code'] == 0
        calls.append(env.ids)
        ids = env.ids[:1] if len(calls) == 1 else env.ids
        env.raw = {**verdict(), 'results': {name: entry() for name in ids}}
        if 'anti_cheating_robustness' in ids:
            env.raw['results']['anti_cheating_robustness'] = entry('not_assessed')
        return 0, json.dumps({'modelUsage': {'test': {'input_tokens': 3, 'output_tokens': 2}}}), ''
    monkeypatch.setattr(image_review, 'create_environments', create)
    monkeypatch.setattr(image_review, 'run_judge', judge)
    monkeypatch.setattr(image_review.shutil, 'which', lambda *args: '/fake/claude')
    monkeypatch.setattr('validation.rubrics.review_pricing.for_stage', lambda *args: {'rates': None, 'source': {}})
    args = SimpleNamespace(out=tmp_path / 'out', attempts=1, review_runs=1, review_resume=True,
        review_retries=2, review_seed=42, review_local=False, dry_run=False, timeout_minutes=1)
    result = asyncio.run(image_review.review(source, staged, tmp_path, args, 'claude-code', 'test'))
    assert result['status'] == 'findings', result
    assert [len(ids) for ids in calls] == [5, 4]
    assert result['verdicts'][0]['results']['anti_cheating_robustness']['severity'] == 'not_assessed'
    assert result['verdicts'][0]['verifier_mode'] == 'shared'
    assert env.stopped


def test_upload_review_evidence_supports_the_rubric_file(tmp_path):
    from validation.stages.task_setup import upload_review_files
    evidence = tmp_path / 'review_files'; evidence.mkdir()
    (evidence / 'rubric.toml').write_text(review.load('custom_severity')[0])
    targets = []
    class Environment:
        async def exec(self, *args, **kwargs):
            pass
        async def upload_file(self, source_path, target_path):
            targets.append(target_path)
    asyncio.run(upload_review_files(Environment(), tmp_path))
    assert targets == ['/app/rubric.toml']
